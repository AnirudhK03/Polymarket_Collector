"""
Within-window time series analysis for Polymarket BTC binary options.

Analyzes BTC price dynamics inside each 5-minute window:
- Autocorrelation of 1-second returns (trending vs mean-reverting)
- Realized vs implied volatility (is the market over/underpricing vol?)
- Volatility clustering across consecutive windows
- Early price movement as outcome predictor
- Intra-window momentum vs mean reversion

These results feed directly into market-making decisions:
  autocorrelation  → how fast to update quotes after a BTC move
  realized/implied → whether to quote tighter (overpriced vol) or wider
  vol clustering   → how to set initial vol when a new window opens
  early move       → whether to bias directional exposure
  momentum/revert  → how to manage risk over the window's life

Usage:
    from analysis.db import get_all_window_data
    from analysis.models import add_iv
    from analysis.timeseries import compute_all_timeseries, print_report

    all_data = get_all_window_data()
    for d in all_data:
        add_iv(d['trades'], d['price_to_beat'])

    ts = compute_all_timeseries(all_data)
    print_report(ts)
"""

import numpy as np
import pandas as pd
from scipy import stats as sp_stats


# ==========================================================================
#  Helpers
# ==========================================================================


def _resample_btc_1s(btc: pd.DataFrame) -> pd.Series:
    """
    Resample BTC index_price to a regular 1-second grid (0..299).

    Raw BTC ticks come at ~1/sec but aren't perfectly regular.
    We resample to exactly 300 points so autocorrelation lags
    have a clean interpretation (lag 1 = 1 second, etc.).

    Uses forward-fill: each second gets the most recent price.
    """
    # Build a regular grid from 0 to 299 seconds
    grid = pd.DataFrame({"secs": np.arange(300.0)})

    # Merge: for each grid second, find the most recent BTC tick
    btc_sorted = btc[["secs", "index_price"]].sort_values("secs")
    merged = pd.merge_asof(
        grid, btc_sorted, on="secs", direction="backward"
    )

    # First few seconds may be NaN if BTC data starts at secs > 0.
    # Forward-fill from first available price.
    merged["index_price"] = merged["index_price"].ffill()

    # If somehow still NaN at the very start, backfill from first tick
    merged["index_price"] = merged["index_price"].bfill()

    return merged.set_index("secs")["index_price"]


def _log_returns(prices: pd.Series) -> pd.Series:
    """Compute log returns: ln(p_t / p_{t-1}). First value is NaN."""
    return np.log(prices / prices.shift(1))


# ==========================================================================
#  1. Autocorrelation of BTC returns
# ==========================================================================


def btc_autocorrelation(all_data: list[dict]) -> dict:
    """
    Compute autocorrelation of 1-second BTC log returns at multiple lags.

    For each window, resample BTC to 1-second, compute log returns,
    then compute autocorrelation at lags 1, 2, 5, 10, 30 seconds.
    Reports the median and distribution across all windows.

    Interpretation:
      positive → BTC trending (moves continue) → update quotes aggressively
      negative → BTC mean-reverting (moves reverse) → fade moves / hold quotes
      ~zero    → random walk → standard Black-Scholes assumptions hold

    Returns dict with per-lag statistics and per-window detail.
    """
    lags = [1, 2, 5, 10, 30]
    # Store per-window autocorrelations: {lag: [ac_window1, ac_window2, ...]}
    per_lag = {lag: [] for lag in lags}

    window_timestamps = []

    for data in all_data:
        btc = data["btc"]
        if len(btc) < 30:
            continue

        prices = _resample_btc_1s(btc)
        rets = _log_returns(prices).dropna()

        window_timestamps.append(data["window_ts"])

        for lag in lags:
            if len(rets) <= lag:
                per_lag[lag].append(np.nan)
                continue
            # Pearson correlation between r(t) and r(t-lag)
            ac = rets.autocorr(lag=lag)
            per_lag[lag].append(ac)

    # Aggregate statistics per lag
    lag_stats = {}
    for lag in lags:
        vals = np.array(per_lag[lag])
        valid = vals[~np.isnan(vals)]
        if len(valid) == 0:
            continue
        lag_stats[lag] = {
            "median": float(np.median(valid)),
            "mean": float(np.mean(valid)),
            "std": float(np.std(valid)),
            "p25": float(np.percentile(valid, 25)),
            "p75": float(np.percentile(valid, 75)),
            "n_windows": len(valid),
            # Is the mean significantly different from zero?
            # t-test: H0 = autocorrelation is zero
            "t_stat": float(sp_stats.ttest_1samp(valid, 0).statistic),
            "p_value": float(sp_stats.ttest_1samp(valid, 0).pvalue),
        }

    return {
        "lags": lags,
        "lag_stats": lag_stats,
        "per_window": {
            "window_ts": window_timestamps,
            **{f"ac_lag{lag}": per_lag[lag] for lag in lags},
        },
    }


# ==========================================================================
#  2. Realized vs Implied Volatility
# ==========================================================================


SECONDS_PER_YEAR = 365.25 * 24 * 3600


def realized_vs_implied(all_data: list[dict]) -> dict:
    """
    Compare realized BTC volatility to the market's implied volatility
    for each window.

    Realized vol: std(1-second log returns) * sqrt(seconds_per_year).
    Implied vol: median IV from reliable trades (requires add_iv to have
    been called on the trades DataFrame).

    Key output: the ratio realized/implied.
      ratio < 1 → market overprices vol → you profit from selling (collecting spread)
      ratio > 1 → market underprices vol → you're undercompensated for risk
      ratio ≈ 1 → market is well-calibrated

    Also reports whether the ratio is stable or varies a lot across windows.
    """
    records = []

    for data in all_data:
        btc = data["btc"]
        trades = data["trades"]

        if len(btc) < 30:
            continue

        # --- Realized vol ---
        prices = _resample_btc_1s(btc)
        rets = _log_returns(prices).dropna()
        # Annualize: std of 1-second returns * sqrt(seconds_in_year / 1)
        realized_vol = float(rets.std() * np.sqrt(SECONDS_PER_YEAR))

        # --- Implied vol (median of reliable IVs) ---
        if "iv_mid" not in trades.columns or "reliable_iv" not in trades.columns:
            # add_iv hasn't been called — skip IV comparison
            implied_vol = np.nan
        else:
            reliable = trades[trades["reliable_iv"]]
            if len(reliable) < 5:
                implied_vol = np.nan
            else:
                implied_vol = float(reliable["iv_mid"].median())

        # --- Ratio ---
        if np.isnan(implied_vol) or implied_vol <= 0:
            ratio = np.nan
        else:
            ratio = realized_vol / implied_vol

        records.append({
            "window_ts": data["window_ts"],
            "realized_vol": realized_vol,
            "implied_vol": implied_vol,
            "ratio": ratio,
            "up_wins": data["up_wins"],
        })

    df = pd.DataFrame(records)

    valid_ratios = df["ratio"].dropna()

    return {
        "n_windows": len(df),
        "realized_vol": {
            "median": float(df["realized_vol"].median()),
            "mean": float(df["realized_vol"].mean()),
            "std": float(df["realized_vol"].std()),
            "min": float(df["realized_vol"].min()),
            "max": float(df["realized_vol"].max()),
        },
        "implied_vol": {
            "median": float(df["implied_vol"].median()),
            "mean": float(df["implied_vol"].mean()),
            "std": float(df["implied_vol"].std()),
        },
        "ratio": {
            "median": float(valid_ratios.median()) if len(valid_ratios) > 0 else np.nan,
            "mean": float(valid_ratios.mean()) if len(valid_ratios) > 0 else np.nan,
            "std": float(valid_ratios.std()) if len(valid_ratios) > 0 else np.nan,
            "pct_below_1": float((valid_ratios < 1.0).mean()) if len(valid_ratios) > 0 else np.nan,
        },
        "per_window": df,
    }


# ==========================================================================
#  3. Volatility Clustering
# ==========================================================================


def vol_clustering(all_data: list[dict]) -> dict:
    """
    Check if realized volatility clusters across consecutive windows.

    If vol at window N predicts vol at window N+1, you can use the
    previous window's realized vol as a better starting estimate than
    a static median.

    Computes autocorrelation of the realized vol series at lags 1-3.
    Also reports the rank correlation (Spearman) as a robustness check.
    """
    # First compute realized vol per window (reuse the logic from above)
    vols = []
    timestamps = []

    for data in all_data:
        btc = data["btc"]
        if len(btc) < 30:
            continue
        prices = _resample_btc_1s(btc)
        rets = _log_returns(prices).dropna()
        rv = float(rets.std() * np.sqrt(SECONDS_PER_YEAR))
        vols.append(rv)
        timestamps.append(data["window_ts"])

    vol_series = pd.Series(vols, index=timestamps).sort_index()

    if len(vol_series) < 5:
        return {
            "n_windows": len(vol_series),
            "clustering": "insufficient data",
        }

    # Autocorrelation of realized vol at lags 1, 2, 3
    lag_ac = {}
    for lag in [1, 2, 3]:
        if len(vol_series) > lag:
            ac = vol_series.autocorr(lag=lag)
            lag_ac[lag] = float(ac) if not np.isnan(ac) else None
        else:
            lag_ac[lag] = None

    # Spearman rank correlation between consecutive windows
    if len(vol_series) > 1:
        spearman_r, spearman_p = sp_stats.spearmanr(
            vol_series.values[:-1], vol_series.values[1:]
        )
    else:
        spearman_r, spearman_p = np.nan, np.nan

    return {
        "n_windows": len(vol_series),
        "lag_autocorrelation": lag_ac,
        "spearman_lag1": {
            "correlation": float(spearman_r),
            "p_value": float(spearman_p),
        },
        "vol_series": vol_series,
    }


# ==========================================================================
#  4. Early Move as Outcome Predictor
# ==========================================================================


def early_move_predictiveness(all_data: list[dict]) -> dict:
    """
    Test whether early BTC price movement predicts window outcome.

    For each cutoff (30s, 60s, 90s):
      - Compute BTC move in basis points from window open to cutoff
      - Record whether Up token won
      - Bucket by move size and compute conditional win rates
      - Compute point-biserial correlation (continuous move vs binary outcome)

    A strong positive correlation means early momentum persists —
    you can bias your quotes directionally based on the early move.
    """
    cutoffs = [30, 60, 90]
    records = []

    for data in all_data:
        btc = data["btc"]
        if len(btc) < 30:
            continue

        prices = _resample_btc_1s(btc)
        open_price = prices.iloc[0]

        row = {
            "window_ts": data["window_ts"],
            "up_wins": int(data["up_wins"]),  # 1 or 0 for correlation
            "open_price": open_price,
        }

        for cutoff in cutoffs:
            if cutoff < len(prices):
                price_at_cutoff = prices.iloc[cutoff]
                move_bps = (price_at_cutoff - open_price) / open_price * 10000
            else:
                move_bps = np.nan
            row[f"move_{cutoff}s_bps"] = move_bps

        records.append(row)

    df = pd.DataFrame(records)

    results = {"n_windows": len(df), "cutoffs": {}}

    for cutoff in cutoffs:
        col = f"move_{cutoff}s_bps"
        valid = df[[col, "up_wins"]].dropna()

        if len(valid) < 5:
            results["cutoffs"][cutoff] = {"status": "insufficient data"}
            continue

        moves = valid[col]
        outcomes = valid["up_wins"]

        # Point-biserial correlation (same as Pearson when one var is binary)
        corr, p_value = sp_stats.pearsonr(moves, outcomes)

        # Bucket analysis: split moves into bins and compute conditional win rate
        bins = []
        # Use quartile-based bins for roughly equal counts
        try:
            valid["bucket"] = pd.qcut(moves, q=4, duplicates="drop")
            for bucket, group in valid.groupby("bucket", observed=True):
                bins.append({
                    "range": str(bucket),
                    "n_windows": len(group),
                    "mean_move_bps": float(group[col].mean()),
                    "up_win_rate": float(group["up_wins"].mean()),
                })
        except ValueError:
            # Not enough unique values for quartile bins — use fixed bins
            edges = [-np.inf, -5, 0, 5, np.inf]
            labels = ["< -5bps", "-5 to 0", "0 to +5", "> +5bps"]
            valid["bucket"] = pd.cut(moves, bins=edges, labels=labels)
            for label in labels:
                group = valid[valid["bucket"] == label]
                if len(group) > 0:
                    bins.append({
                        "range": label,
                        "n_windows": len(group),
                        "mean_move_bps": float(group[col].mean()),
                        "up_win_rate": float(group["up_wins"].mean()),
                    })

        results["cutoffs"][cutoff] = {
            "correlation": float(corr),
            "p_value": float(p_value),
            "mean_move_bps": float(moves.mean()),
            "std_move_bps": float(moves.std()),
            "bins": bins,
        }

    results["per_window"] = df
    return results


# ==========================================================================
#  5. Intra-Window Momentum vs Mean Reversion
# ==========================================================================


def intra_window_momentum(all_data: list[dict]) -> dict:
    """
    Check if BTC direction in the first half of the window persists
    or reverses in the second half.

    Splits each window at 150 seconds:
      first_half_move  = price(150s) - price(0s)   in bps
      second_half_move = price(300s) - price(150s)  in bps

    Then computes correlation between first-half and second-half moves
    across all windows.

    Positive correlation → momentum (first half direction continues)
    Negative correlation → mean reversion (first half direction reverses)
    ~Zero → no relationship (halves are independent)

    Also tests a 100s/200s split (first third / last two thirds)
    since the window isn't symmetric in terms of trading activity.
    """
    splits = [
        {"name": "half", "t_split": 150},
        {"name": "third", "t_split": 100},
    ]

    results = {}

    for split in splits:
        t_split = split["t_split"]
        first_moves = []
        second_moves = []
        outcomes = []

        for data in all_data:
            btc = data["btc"]
            if len(btc) < 30:
                continue

            prices = _resample_btc_1s(btc)
            p_open = prices.iloc[0]
            p_split = prices.iloc[t_split] if t_split < len(prices) else np.nan
            p_close = prices.iloc[-1]

            if np.isnan(p_split):
                continue

            first_bps = (p_split - p_open) / p_open * 10000
            second_bps = (p_close - p_split) / p_split * 10000

            first_moves.append(first_bps)
            second_moves.append(second_bps)
            outcomes.append(int(data["up_wins"]))

        first_moves = np.array(first_moves)
        second_moves = np.array(second_moves)
        outcomes = np.array(outcomes)

        if len(first_moves) < 5:
            results[split["name"]] = {"status": "insufficient data"}
            continue

        # Correlation between first-half and second-half moves
        corr, p_value = sp_stats.pearsonr(first_moves, second_moves)

        # What fraction of windows have same-direction halves?
        same_direction = np.mean(np.sign(first_moves) == np.sign(second_moves))

        # Does the first-half move predict the outcome?
        outcome_corr, outcome_p = sp_stats.pearsonr(first_moves, outcomes)

        results[split["name"]] = {
            "t_split": t_split,
            "n_windows": len(first_moves),
            "halves_correlation": float(corr),
            "halves_p_value": float(p_value),
            "same_direction_pct": float(same_direction),
            "first_move_outcome_corr": float(outcome_corr),
            "first_move_outcome_p": float(outcome_p),
            "first_move_stats": {
                "mean_bps": float(first_moves.mean()),
                "std_bps": float(first_moves.std()),
            },
            "second_move_stats": {
                "mean_bps": float(second_moves.mean()),
                "std_bps": float(second_moves.std()),
            },
        }

    return results


# ==========================================================================
#  Orchestrator
# ==========================================================================


def compute_all_timeseries(all_data: list[dict]) -> dict:
    """
    Run all time series analyses. Mirrors compute_all_stats() pattern.

    Parameters
    ----------
    all_data : list of dicts from get_all_window_data().
               Each dict should have add_iv() already applied to trades
               (for realized_vs_implied to work).

    Returns dict with keys: autocorrelation, realized_implied,
    vol_clustering, early_move, intra_window.
    """
    return {
        "autocorrelation": btc_autocorrelation(all_data),
        "realized_implied": realized_vs_implied(all_data),
        "vol_clustering": vol_clustering(all_data),
        "early_move": early_move_predictiveness(all_data),
        "intra_window": intra_window_momentum(all_data),
    }


# ==========================================================================
#  Report
# ==========================================================================


def print_report(ts: dict):
    """Print a human-readable report of all time series analyses."""

    print("=" * 65)
    print("  POLYMARKET BTC BINARY OPTIONS — TIME SERIES REPORT")
    print("=" * 65)

    # --- 1. Autocorrelation ---
    ac = ts["autocorrelation"]
    print("\n── BTC RETURN AUTOCORRELATION (1-second returns) ────────────────")
    print(f"  {'Lag':>5}  {'Median':>8}  {'Mean':>8}  {'Std':>8}  {'t-stat':>8}  {'p-val':>8}")
    print(f"  {'-' * 50}")
    for lag in ac["lags"]:
        if lag not in ac["lag_stats"]:
            continue
        s = ac["lag_stats"][lag]
        sig = "*" if s["p_value"] < 0.05 else " "
        print(
            f"  {lag:>4}s  {s['median']:>+8.4f}  {s['mean']:>+8.4f}  "
            f"{s['std']:>8.4f}  {s['t_stat']:>+8.2f}  {s['p_value']:>8.4f}{sig}"
        )
    print(f"  (* = statistically significant at 5%)")

    # Interpretation
    lag1 = ac["lag_stats"].get(1)
    if lag1:
        if lag1["mean"] > 0.05 and lag1["p_value"] < 0.05:
            print(f"  → BTC shows short-term MOMENTUM — update quotes aggressively")
        elif lag1["mean"] < -0.05 and lag1["p_value"] < 0.05:
            print(f"  → BTC shows short-term MEAN REVERSION — fade moves")
        else:
            print(f"  → No significant autocorrelation — returns are ~random walk")

    # --- 2. Realized vs Implied ---
    ri = ts["realized_implied"]
    print("\n── REALIZED vs IMPLIED VOLATILITY ──────────────────────────────")
    rv = ri["realized_vol"]
    iv = ri["implied_vol"]
    ratio = ri["ratio"]
    print(f"  Realized vol:   median {rv['median']*100:.1f}%  "
          f"mean {rv['mean']*100:.1f}%  std {rv['std']*100:.1f}%")
    print(f"  Implied vol:    median {iv['median']*100:.1f}%  "
          f"mean {iv['mean']*100:.1f}%  std {iv['std']*100:.1f}%")
    print(f"  Ratio (R/I):    median {ratio['median']:.3f}  "
          f"mean {ratio['mean']:.3f}  std {ratio['std']:.3f}")
    if not np.isnan(ratio.get("pct_below_1", np.nan)):
        print(f"  Windows where realized < implied: {ratio['pct_below_1']:.1%}")

    if ratio["median"] < 0.85:
        print(f"  → Market OVERPRICES vol — collect spread confidently")
    elif ratio["median"] > 1.15:
        print(f"  → Market UNDERPRICES vol — widen quotes or reduce exposure")
    else:
        print(f"  → Market vol pricing is roughly fair")

    # --- 3. Vol Clustering ---
    vc = ts["vol_clustering"]
    print("\n── VOLATILITY CLUSTERING ───────────────────────────────────────")
    if isinstance(vc.get("clustering"), str):
        print(f"  {vc['clustering']}")
    else:
        print(f"  Windows analyzed: {vc['n_windows']}")
        print(f"  Autocorrelation of realized vol across windows:")
        for lag, ac_val in vc["lag_autocorrelation"].items():
            if ac_val is not None:
                print(f"    Lag {lag}: {ac_val:+.3f}")
        sp = vc["spearman_lag1"]
        print(f"  Spearman rank correlation (lag 1): "
              f"{sp['correlation']:+.3f}  (p={sp['p_value']:.4f})")

        if vc["lag_autocorrelation"].get(1) is not None:
            ac1 = vc["lag_autocorrelation"][1]
            if ac1 > 0.3:
                print(f"  → Vol clusters — use previous window's realized vol as prior")
            elif ac1 < -0.2:
                print(f"  → Vol anti-clusters — expect mean reversion in vol")
            else:
                print(f"  → Weak clustering — static vol estimate may be fine")

    # --- 4. Early Move Predictor ---
    em = ts["early_move"]
    print("\n── EARLY MOVE → OUTCOME PREDICTION ────────────────────────────")
    print(f"  Windows analyzed: {em['n_windows']}")

    for cutoff, data in em["cutoffs"].items():
        if "status" in data:
            print(f"\n  {cutoff}s cutoff: {data['status']}")
            continue

        print(f"\n  {cutoff}s cutoff:")
        print(f"    Correlation with outcome: {data['correlation']:+.3f}  "
              f"(p={data['p_value']:.4f})")
        print(f"    Mean early move: {data['mean_move_bps']:+.2f} bps  "
              f"(std: {data['std_move_bps']:.2f})")

        if data["bins"]:
            print(f"    {'Bucket':<18} {'N':>4}  {'Avg move':>10}  {'Up win%':>8}")
            print(f"    {'-' * 44}")
            for b in data["bins"]:
                print(f"    {b['range']:<18} {b['n_windows']:>4}  "
                      f"{b['mean_move_bps']:>+10.1f}bps  "
                      f"{b['up_win_rate']:>8.1%}")

    # Find best cutoff
    best_cutoff = None
    best_corr = 0
    for cutoff, data in em["cutoffs"].items():
        if "correlation" in data and abs(data["correlation"]) > abs(best_corr):
            best_corr = data["correlation"]
            best_cutoff = cutoff

    if best_cutoff and abs(best_corr) > 0.15:
        print(f"\n  → Best signal: {best_cutoff}s move (r={best_corr:+.3f})")
        if best_corr > 0:
            print(f"    Early momentum PERSISTS — use as directional bias")
        else:
            print(f"    Early momentum REVERSES — contrarian signal")
    else:
        print(f"\n  → Early moves are weak predictors of outcome")

    # --- 5. Intra-Window Momentum ---
    iw = ts["intra_window"]
    print("\n── INTRA-WINDOW MOMENTUM vs MEAN REVERSION ────────────────────")

    for name, data in iw.items():
        if "status" in data:
            print(f"  {name} split: {data['status']}")
            continue

        t = data["t_split"]
        print(f"\n  Split at {t}s (first {t}s vs remaining {300-t}s):")
        print(f"    First-half move:   mean {data['first_move_stats']['mean_bps']:+.2f} bps  "
              f"std {data['first_move_stats']['std_bps']:.2f}")
        print(f"    Second-half move:  mean {data['second_move_stats']['mean_bps']:+.2f} bps  "
              f"std {data['second_move_stats']['std_bps']:.2f}")
        print(f"    Halves correlation: {data['halves_correlation']:+.3f}  "
              f"(p={data['halves_p_value']:.4f})")
        print(f"    Same direction:     {data['same_direction_pct']:.1%} of windows")
        print(f"    First-half → outcome: r={data['first_move_outcome_corr']:+.3f}  "
              f"(p={data['first_move_outcome_p']:.4f})")

        corr = data["halves_correlation"]
        if corr > 0.2:
            print(f"    → MOMENTUM within window — early direction tends to persist")
        elif corr < -0.2:
            print(f"    → MEAN REVERSION within window — early direction tends to reverse")
        else:
            print(f"    → Halves are roughly independent")

    print("\n" + "=" * 65)