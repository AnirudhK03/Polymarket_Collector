"""
Cross-window statistics for Polymarket BTC binary options.

Aggregates data across multiple windows to find patterns:
- Opening price bias and market calibration
- IV regime and term structure
- Spread dynamics and capture opportunity
- Trading activity patterns

Usage:
    from analysis.db import get_all_window_data
    from analysis.models import add_iv
    from analysis.stats import compute_all_stats

    all_data = get_all_window_data()
    for d in all_data:
        add_iv(d['trades'], d['price_to_beat'])

    stats = compute_all_stats(all_data)
    print_report(stats)
"""

import numpy as np
import pandas as pd


# -- 1. Opening price analysis --------------------------------------------


def opening_price_stats(all_data: list[dict]) -> dict:
    """
    Analyze the opening price of the Up token across windows.

    Answers: "Does the market start at 0.50 (fair coin flip) or is
    there a directional bias?" and "Is that bias justified by outcomes?"

    We define 'opening price' as the median mid-price in the first
    10 seconds — more robust than the very first trade which can be noisy.
    """
    opening_prices = []
    outcomes = []  # True = Up won

    for data in all_data:
        trades = data["trades"]
        # First 10 seconds of trading
        early = trades[trades["secs"] <= 10]
        if early.empty:
            continue

        opening_mid = early["mid"].median()
        opening_prices.append(opening_mid)
        outcomes.append(data["up_wins"])

    opening_prices = np.array(opening_prices)
    outcomes = np.array(outcomes)

    n = len(opening_prices)
    actual_up_rate = outcomes.mean()
    implied_up_rate = opening_prices.mean()

    return {
        "n_windows": n,
        "opening_mid_mean": opening_prices.mean(),
        "opening_mid_median": np.median(opening_prices),
        "opening_mid_std": opening_prices.std(),
        "opening_mid_min": opening_prices.min(),
        "opening_mid_max": opening_prices.max(),
        # Key comparison: does the market's implied probability match reality?
        "implied_up_probability": implied_up_rate,
        "actual_up_win_rate": actual_up_rate,
        "calibration_gap": actual_up_rate - implied_up_rate,
        # Per-window detail
        "per_window": list(zip(
            [d["window_ts"] for d in all_data[:n]],
            opening_prices.tolist(),
            outcomes.tolist(),
        )),
    }


# -- 2. Market calibration ------------------------------------------------


def calibration_stats(all_data: list[dict]) -> dict:
    """
    Check if market prices match realized probabilities.

    Bins all trades by their mid-price and checks what fraction of
    those windows actually resolved Up. In a perfect market:
      - trades priced at 0.30 should be in windows that resolve Up 30% of the time
      - trades priced at 0.70 should resolve Up 70% of the time

    Deviations reveal systematic mispricing.
    """
    bins = [(0.0, 0.15), (0.15, 0.30), (0.30, 0.45), (0.45, 0.55),
            (0.55, 0.70), (0.70, 0.85), (0.85, 1.0)]

    results = []
    for lo, hi in bins:
        implied_probs = []
        actuals = []

        for data in all_data:
            trades = data["trades"]
            # Only use trades after the chaotic opening
            mask = (trades["mid"] >= lo) & (trades["mid"] < hi) & (trades["secs"] > 15)
            matching = trades[mask]

            if not matching.empty:
                implied_probs.extend(matching["mid"].tolist())
                actuals.extend([float(data["up_wins"])] * len(matching))

        if implied_probs:
            implied = np.mean(implied_probs)
            actual = np.mean(actuals)
            results.append({
                "bin": f"{lo:.2f}-{hi:.2f}",
                "n_trades": len(implied_probs),
                "avg_implied_prob": implied,
                "actual_up_rate": actual,
                "gap": actual - implied,
            })

    return {"bins": results}


# -- 3. IV regime stats ----------------------------------------------------


def iv_stats(all_data: list[dict]) -> dict:
    """
    Analyze implied volatility patterns across all windows.

    Answers:
    - What's the typical IV level?
    - Does IV systematically rise or fall over the 5-minute window?
    - How much does IV vary across windows?
    """
    # Collect all reliable IV points with their time and window
    all_iv_points = []

    per_window_median_iv = []

    for data in all_data:
        trades = data["trades"]
        if "iv_mid" not in trades.columns:
            continue

        reliable = trades[trades["reliable_iv"]].copy()
        if reliable.empty:
            continue

        median_iv = reliable["iv_mid"].median()
        per_window_median_iv.append(median_iv)

        for _, row in reliable.iterrows():
            all_iv_points.append({
                "secs": row["secs"],
                "iv_mid": row["iv_mid"],
                "window_ts": data["window_ts"],
            })

    if not all_iv_points:
        return {"error": "No reliable IV data"}

    iv_df = pd.DataFrame(all_iv_points)
    per_window_median_iv = np.array(per_window_median_iv)

    # IV by 30-second time buckets — shows the term structure
    iv_df["bucket"] = (iv_df["secs"] // 30).astype(int) * 30
    term_structure = (
        iv_df.groupby("bucket")["iv_mid"]
        .agg(["median", "mean", "std", "count"])
        .reset_index()
    )

    return {
        "overall_median": float(iv_df["iv_mid"].median()),
        "overall_mean": float(iv_df["iv_mid"].mean()),
        "overall_std": float(iv_df["iv_mid"].std()),
        "overall_p25": float(iv_df["iv_mid"].quantile(0.25)),
        "overall_p75": float(iv_df["iv_mid"].quantile(0.75)),
        # Cross-window variation
        "per_window_median_mean": float(per_window_median_iv.mean()),
        "per_window_median_std": float(per_window_median_iv.std()),
        "per_window_median_min": float(per_window_median_iv.min()),
        "per_window_median_max": float(per_window_median_iv.max()),
        # Term structure: how IV evolves over the 5-minute window
        "term_structure": term_structure.to_dict("records"),
    }


# -- 4. Spread analysis ----------------------------------------------------


def spread_stats(all_data: list[dict]) -> dict:
    """
    Analyze bid-ask spread patterns.

    The spread is the market-making opportunity. Wider spread = more
    potential profit per trade, but also more risk and less volume.

    Answers:
    - How wide is the typical spread?
    - Does it widen or tighten over the window?
    - What's the theoretical max revenue from capturing spread?
    """
    all_spread_points = []
    per_window_stats = []

    for data in all_data:
        trades = data["trades"]
        t = trades[trades["secs"] > 5].copy()  # skip chaotic opening

        if t.empty:
            continue

        # Time-bucketed spreads
        for _, row in t.iterrows():
            all_spread_points.append({
                "secs": row["secs"],
                "spread": row["spread"],
                "size": row["size"],
            })

        # Per-window summary
        # Theoretical spread capture: if you could buy at bid and sell at ask
        # on every trade, your revenue would be sum(spread * size) / 2.
        # The /2 is because you'd capture half the spread on each side.
        spread_capture = (t["spread"] * t["size"]).sum() / 2

        per_window_stats.append({
            "window_ts": data["window_ts"],
            "median_spread": t["spread"].median(),
            "mean_spread": t["spread"].mean(),
            "total_volume": t["size"].sum(),
            "theoretical_spread_capture": spread_capture,
            "n_trades": len(t),
        })

    spread_df = pd.DataFrame(all_spread_points)
    window_df = pd.DataFrame(per_window_stats)

    # Spread by time bucket
    spread_df["bucket"] = (spread_df["secs"] // 30).astype(int) * 30
    spread_by_time = (
        spread_df.groupby("bucket")["spread"]
        .agg(["median", "mean", "std"])
        .reset_index()
    )

    return {
        "overall_median_spread": float(spread_df["spread"].median()),
        "overall_mean_spread": float(spread_df["spread"].mean()),
        "overall_median_spread_cents": float(spread_df["spread"].median() * 100),
        # Per-window aggregates
        "avg_volume_per_window": float(window_df["total_volume"].mean()),
        "avg_trades_per_window": float(window_df["n_trades"].mean()),
        "avg_spread_capture_per_window": float(window_df["theoretical_spread_capture"].mean()),
        "total_spread_capture_all_windows": float(window_df["theoretical_spread_capture"].sum()),
        # Spread term structure
        "spread_by_time": spread_by_time.to_dict("records"),
        # Per-window detail
        "per_window": window_df.to_dict("records"),
    }


# -- 5. Trading activity patterns ------------------------------------------


def activity_stats(all_data: list[dict]) -> dict:
    """
    Analyze when and how trading happens.

    Answers:
    - When does trading start and stop within the window?
    - What does the trade size distribution look like?
    - Is there a consistent volume pattern over time?
    """
    all_trades = []

    for data in all_data:
        trades = data["trades"]
        for _, row in trades.iterrows():
            all_trades.append({
                "secs": row["secs"],
                "size": row["size"],
                "side": row["side"],
            })

    df = pd.DataFrame(all_trades)

    # Trade timing
    per_window_last_trade = []
    for data in all_data:
        trades = data["trades"]
        if not trades.empty:
            per_window_last_trade.append(trades["secs"].max())

    # Size distribution
    nonzero = df[df["size"] > 0]["size"]

    # Volume by 30-second bucket
    df["bucket"] = (df["secs"] // 30).astype(int) * 30
    vol_by_time = (
        df.groupby("bucket")
        .agg(
            total_volume=("size", "sum"),
            n_trades=("size", "count"),
            buy_volume=("size", lambda x: x[df.loc[x.index, "side"] == "BUY"].sum()),
        )
        .reset_index()
    )
    vol_by_time["sell_volume"] = vol_by_time["total_volume"] - vol_by_time["buy_volume"]
    # Average per window
    n_windows = len(all_data)
    vol_by_time["avg_volume"] = vol_by_time["total_volume"] / n_windows
    vol_by_time["avg_trades"] = vol_by_time["n_trades"] / n_windows

    return {
        "total_trades": len(df),
        "total_volume": float(df["size"].sum()),
        "avg_last_trade_secs": float(np.mean(per_window_last_trade)),
        "median_last_trade_secs": float(np.median(per_window_last_trade)),
        # Size distribution
        "size_median": float(nonzero.median()) if not nonzero.empty else 0,
        "size_mean": float(nonzero.mean()) if not nonzero.empty else 0,
        "size_p75": float(nonzero.quantile(0.75)) if not nonzero.empty else 0,
        "size_p95": float(nonzero.quantile(0.95)) if not nonzero.empty else 0,
        "size_max": float(nonzero.max()) if not nonzero.empty else 0,
        # Side balance
        "buy_fraction": float((df["side"] == "BUY").mean()),
        # Volume by time
        "volume_by_time": vol_by_time.to_dict("records"),
    }


# -- Aggregate all stats ---------------------------------------------------


def compute_all_stats(all_data: list[dict]) -> dict:
    """Run all stat functions and return a combined dict."""
    return {
        "opening": opening_price_stats(all_data),
        "calibration": calibration_stats(all_data),
        "iv": iv_stats(all_data),
        "spread": spread_stats(all_data),
        "activity": activity_stats(all_data),
    }


# -- Pretty print ----------------------------------------------------------


def print_report(stats: dict):
    """Print a human-readable report of all statistics."""
    o = stats["opening"]
    iv = stats["iv"]
    sp = stats["spread"]
    act = stats["activity"]
    cal = stats["calibration"]

    print("=" * 65)
    print("  POLYMARKET BTC BINARY OPTIONS — STATISTICAL REPORT")
    print(f"  {o['n_windows']} complete windows analyzed")
    print("=" * 65)

    print("\n── OPENING PRICE BIAS ──────────────────────────────────────────")
    print(f"  Median opening Up price:   {o['opening_mid_median']:.3f}")
    print(f"  Mean opening Up price:     {o['opening_mid_mean']:.3f}")
    print(f"  Range:                     {o['opening_mid_min']:.3f} – {o['opening_mid_max']:.3f}")
    print(f"  Std dev:                   {o['opening_mid_std']:.3f}")
    print(f"")
    print(f"  Implied Up probability:    {o['implied_up_probability']:.1%}")
    print(f"  Actual Up win rate:        {o['actual_up_win_rate']:.1%}")
    print(f"  Calibration gap:           {o['calibration_gap']:+.1%}")
    if abs(o["calibration_gap"]) > 0.05:
        direction = "underpriced" if o["calibration_gap"] > 0 else "overpriced"
        print(f"  → Up token appears {direction} at open")
    else:
        print(f"  → Market appears well-calibrated at open")

    print("\n── MARKET CALIBRATION ──────────────────────────────────────────")
    print(f"  {'Price bin':<12} {'Trades':>8} {'Implied':>10} {'Actual':>10} {'Gap':>8}")
    print(f"  {'-'*50}")
    for b in cal["bins"]:
        print(f"  {b['bin']:<12} {b['n_trades']:>8} {b['avg_implied_prob']:>10.1%} "
              f"{b['actual_up_rate']:>10.1%} {b['gap']:>+8.1%}")

    print("\n── IMPLIED VOLATILITY ──────────────────────────────────────────")
    print(f"  Overall median IV:         {iv['overall_median']:.1%}")
    print(f"  Overall mean IV:           {iv['overall_mean']:.1%}")
    print(f"  IQR:                       {iv['overall_p25']:.1%} – {iv['overall_p75']:.1%}")
    print(f"  Cross-window median range: {iv['per_window_median_min']:.1%} – {iv['per_window_median_max']:.1%}")
    print(f"  Cross-window std:          {iv['per_window_median_std']:.1%}")
    print(f"")
    print(f"  IV term structure (by 30s bucket):")
    print(f"  {'Bucket':<10} {'Median':>10} {'Mean':>10} {'Std':>10} {'N':>8}")
    print(f"  {'-'*50}")
    for row in iv["term_structure"]:
        t = f"{int(row['bucket']//60)}:{int(row['bucket']%60):02d}"
        print(f"  {t:<10} {row['median']:>10.1%} {row['mean']:>10.1%} "
              f"{row['std']:>10.1%} {row['count']:>8}")

    print("\n── SPREAD DYNAMICS ─────────────────────────────────────────────")
    print(f"  Median spread:             {sp['overall_median_spread_cents']:.1f} cents")
    print(f"  Mean spread:               {sp['overall_mean_spread']*100:.1f} cents")
    print(f"  Avg volume per window:     ${sp['avg_volume_per_window']:,.0f}")
    print(f"  Avg trades per window:     {sp['avg_trades_per_window']:.0f}")
    print(f"  Avg spread capture/window: ${sp['avg_spread_capture_per_window']:,.2f}")
    print(f"  Total spread capture:      ${sp['total_spread_capture_all_windows']:,.2f}")
    print(f"")
    print(f"  Spread by time bucket:")
    print(f"  {'Bucket':<10} {'Median':>10} {'Mean':>10}")
    print(f"  {'-'*32}")
    for row in sp["spread_by_time"]:
        t = f"{int(row['bucket']//60)}:{int(row['bucket']%60):02d}"
        print(f"  {t:<10} {row['median']*100:>9.1f}c {row['mean']*100:>9.1f}c")

    print("\n── TRADING ACTIVITY ────────────────────────────────────────────")
    print(f"  Total trades:              {act['total_trades']:,}")
    print(f"  Total volume:              ${act['total_volume']:,.0f}")
    print(f"  Avg last trade at:         {act['avg_last_trade_secs']:.0f}s "
          f"({act['avg_last_trade_secs']/60:.1f} min)")
    print(f"  Buy/sell balance:          {act['buy_fraction']:.1%} buy")
    print(f"")
    print(f"  Trade size distribution:")
    print(f"    Median:  ${act['size_median']:,.2f}")
    print(f"    Mean:    ${act['size_mean']:,.2f}")
    print(f"    75th:    ${act['size_p75']:,.2f}")
    print(f"    95th:    ${act['size_p95']:,.2f}")
    print(f"    Max:     ${act['size_max']:,.2f}")

    print("\n" + "=" * 65)