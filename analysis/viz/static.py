"""
Static matplotlib charts for Polymarket BTC binary options analysis.

Each function takes data from db.py / models.py and returns a matplotlib
Figure. Call fig.savefig('path.png', dpi=150) to save, or plt.show() to
display interactively.

Usage:
    from analysis.db import get_window_data
    from analysis.models import add_iv, add_fair_value
    from analysis.viz.static import plot_window

    data = get_window_data(1773963000)
    add_iv(data['trades'], data['price_to_beat'])
    fig = plot_window(data)
    fig.savefig('window_1773963000.png', dpi=150)
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from datetime import datetime, timezone


# -- Style defaults --------------------------------------------------------
# Consistent look across all charts. Dark background is easier on the eyes
# for trading analysis and makes colored lines pop.

COLORS = {
    "up_mid": "#3b82f6",  # blue — Up token mid-price
    "up_band": "#3b82f620",  # blue transparent — bid/ask fill
    "btc": "#f59e0b",  # amber — BTC price
    "strike": "#854f0b80",  # dark amber dashed — strike line
    "iv": "#10b981",  # green — implied vol
    "iv_band": "#10b98118",  # green transparent — IV bid/ask fill
    "buy": "#3b82f6",  # blue — buy aggressor
    "sell": "#ef4444",  # red — sell aggressor
    "model": "#a855f7",  # purple — model fair value
    "mispricing_pos": "#ef4444",  # red — market rich
    "mispricing_neg": "#10b981",  # green — market cheap
}


def _fmt_secs(s, _pos=None):
    """Format seconds as M:SS for x-axis labels."""
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _window_title(data: dict) -> str:
    """Generate a descriptive title from window data."""
    ts = data["window_ts"]
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    outcome = "UP wins" if data["up_wins"] else "DOWN wins"
    return (
        f"Window {dt.strftime('%H:%M')} UTC — "
        f"Strike ${data['price_to_beat']:,.0f} — "
        f"Final ${data['final_btc']:,.0f} — "
        f"{outcome}"
    )


def _setup_ax(ax, ylabel: str, show_xlabel: bool = True):
    """Apply common formatting to an axis."""
    ax.set_xlim(0, 300)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_fmt_secs))
    ax.xaxis.set_major_locator(ticker.MultipleLocator(60))
    ax.set_ylabel(ylabel)
    if show_xlabel:
        ax.set_xlabel("Time into window")
    ax.grid(True, alpha=0.15)


# -- Individual chart functions --------------------------------------------


def chart_price(data: dict, ax: plt.Axes = None) -> plt.Axes:
    """
    Plot Up token mid-price and BTC price on dual y-axes.

    Shows:
    - Blue line: Up token mid-price (left axis, 0-1)
    - Blue shading: bid-ask spread
    - Amber line: BTC price (right axis)
    - Dashed line: strike price (price_to_beat)
    """
    trades = data["trades"]
    btc = data["btc"]

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 4))

    # Up token mid + bid/ask band
    ax.fill_between(
        trades["secs"], trades["up_bid"], trades["up_ask"],
        color=COLORS["up_band"], label="_nolegend_",
    )
    ax.plot(trades["secs"], trades["mid"], color=COLORS["up_mid"],
            linewidth=1.2, label="Up mid")

    _setup_ax(ax, "Up token price", show_xlabel=False)
    ax.set_ylim(-0.02, 1.02)

    # BTC price on right axis
    ax2 = ax.twinx()
    ax2.plot(btc["secs"], btc["index_price"], color=COLORS["btc"],
             linewidth=1, alpha=0.8, label="BTC")
    ax2.axhline(data["price_to_beat"], color=COLORS["strike"],
                linestyle="--", linewidth=1, label="Strike")
    ax2.set_ylabel("BTC price ($)")

    # Combined legend
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)

    return ax


def chart_iv(data: dict, ax: plt.Axes = None) -> plt.Axes:
    """
    Plot implied volatility over time with bid/ask band.

    Only shows 'reliable' IV points (after the chaotic opening,
    solver converged, reasonable range). Requires add_iv() to have
    been called on the trades DataFrame first.
    """
    trades = data["trades"]

    if "iv_mid" not in trades.columns:
        raise ValueError("Call models.add_iv() before plotting IV")

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 3))

    reliable = trades[trades["reliable_iv"]].copy()

    if reliable.empty:
        ax.text(150, 0.5, "No reliable IV data", ha="center", va="center")
        _setup_ax(ax, "Implied vol (annualized)")
        return ax

    # IV band (bid to ask)
    band = reliable.dropna(subset=["iv_bid", "iv_ask"])
    if not band.empty:
        ax.fill_between(
            band["secs"], band["iv_bid"], band["iv_ask"],
            color=COLORS["iv_band"], label="IV band",
        )

    # IV mid as scatter (points, not line — IV can jump around)
    ax.scatter(
        reliable["secs"], reliable["iv_mid"],
        color=COLORS["iv"], s=4, alpha=0.6, label="IV mid", zorder=3,
    )

    _setup_ax(ax, "Implied vol (annualized)", show_xlabel=False)

    # Set y-limits based on data, but keep it reasonable
    iv_vals = reliable["iv_mid"].dropna()
    if not iv_vals.empty:
        p5, p95 = iv_vals.quantile(0.05), iv_vals.quantile(0.95)
        margin = (p95 - p5) * 0.3
        ax.set_ylim(max(0, p5 - margin), p95 + margin)

    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.legend(loc="upper right", fontsize=8)

    return ax


def chart_volume(data: dict, ax: plt.Axes = None, bucket_secs: int = 10) -> plt.Axes:
    """
    Plot trade volume by aggressor side in time buckets.

    Blue bars above zero = BUY aggressor volume.
    Red bars below zero = SELL aggressor volume.
    Shows where the trading activity concentrates.
    """
    trades = data["trades"]

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 2.5))

    # Bucket trades into time intervals
    n_buckets = 300 // bucket_secs
    buy_vol = np.zeros(n_buckets)
    sell_vol = np.zeros(n_buckets)

    for _, row in trades.iterrows():
        b = min(int(row["secs"] // bucket_secs), n_buckets - 1)
        if row["side"] == "BUY":
            buy_vol[b] += row["size"]
        else:
            sell_vol[b] += row["size"]

    x = np.arange(n_buckets) * bucket_secs + bucket_secs / 2  # bar centers

    ax.bar(x, buy_vol, width=bucket_secs * 0.8, color=COLORS["buy"],
           alpha=0.7, label="BUY")
    ax.bar(x, -sell_vol, width=bucket_secs * 0.8, color=COLORS["sell"],
           alpha=0.7, label="SELL")

    _setup_ax(ax, "Volume ($)", show_xlabel=False)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.3)
    ax.yaxis.set_major_formatter(
        ticker.FuncFormatter(lambda v, _: f"${abs(v):,.0f}")
    )
    ax.legend(loc="upper right", fontsize=8)

    return ax


def chart_mispricing(data: dict, sigma: float = None, ax: plt.Axes = None) -> plt.Axes:
    """
    Plot model fair value vs market mid, and the mispricing.

    If sigma is not provided, uses the median reliable IV from the window.
    Requires add_iv() to have been called first.
    """
    trades = data["trades"]

    if "iv_mid" not in trades.columns:
        raise ValueError("Call models.add_iv() before plotting mispricing")

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 3))

    # Determine sigma to use
    if sigma is None:
        reliable = trades[trades["reliable_iv"]]
        if reliable.empty:
            ax.text(150, 0, "No reliable IV — can't compute fair value",
                    ha="center", va="center")
            _setup_ax(ax, "Mispricing")
            return ax
        sigma = reliable["iv_mid"].median()

    # Import here to avoid circular dependency
    from analysis.models import add_fair_value

    add_fair_value(trades, data["price_to_beat"], sigma)

    # Only plot where we have valid data (after first ~5 seconds)
    mask = trades["secs"] > 5
    t = trades[mask]

    # Mispricing as colored scatter: red = market rich, green = market cheap
    colors = [
        COLORS["mispricing_pos"] if v > 0 else COLORS["mispricing_neg"]
        for v in t["mispricing"]
    ]
    ax.scatter(t["secs"], t["mispricing"], c=colors, s=4, alpha=0.5, zorder=3)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.3)

    _setup_ax(ax, f"Mispricing (σ={sigma:.0%})")
    ax.set_ylabel(f"Market − Model (σ={sigma:.0%})")

    # Symmetric y-limits
    max_abs = max(abs(t["mispricing"].quantile(0.02)),
                  abs(t["mispricing"].quantile(0.98)))
    ax.set_ylim(-max_abs * 1.3, max_abs * 1.3)

    return ax


# -- Composite chart -------------------------------------------------------


def plot_window(data: dict, sigma: float = None) -> plt.Figure:
    """
    Generate all four charts for a single window as one figure.

    This is the main entry point for quick visual analysis.
    Returns a matplotlib Figure with 4 stacked subplots.
    """
    fig, axes = plt.subplots(4, 1, figsize=(14, 14), height_ratios=[3, 2, 1.5, 2])
    fig.suptitle(_window_title(data), fontsize=13, fontweight="bold", y=0.98)

    chart_price(data, ax=axes[0])
    chart_iv(data, ax=axes[1])
    chart_volume(data, ax=axes[2])
    chart_mispricing(data, sigma=sigma, ax=axes[3])

    # Only show x-axis label on the bottom chart
    axes[3].set_xlabel("Time into window")

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    return fig


# -- Cross-window charts ---------------------------------------------------


def chart_iv_across_windows(all_data: list[dict]) -> plt.Figure:
    """
    Overlay IV curves from multiple windows on one chart.

    Useful for seeing if there's a consistent IV pattern across windows
    (e.g. IV always rises as time decays, or clusters around a level).
    """
    fig, ax = plt.subplots(figsize=(14, 5))

    for data in all_data:
        trades = data["trades"]
        if "iv_mid" not in trades.columns:
            continue

        reliable = trades[trades["reliable_iv"]]
        if reliable.empty:
            continue

        dt = datetime.fromtimestamp(data["window_ts"], tz=timezone.utc)
        label = dt.strftime("%H:%M")
        ax.plot(
            reliable["secs"], reliable["iv_mid"],
            linewidth=0.8, alpha=0.5, label=label,
        )

    _setup_ax(ax, "Implied vol (annualized)")
    ax.set_xlabel("Time into window")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.set_title("Implied volatility across windows", fontweight="bold")
    ax.legend(loc="upper left", fontsize=7, ncol=4)

    fig.tight_layout()
    return fig


def chart_iv_summary(all_data: list[dict]) -> plt.Figure:
    """
    Box plot of IV by time bucket across all windows.

    Groups all reliable IV points into 30-second buckets and shows
    the distribution. Reveals whether IV systematically changes
    over the 5-minute window.
    """
    import pandas as pd

    all_points = []
    for data in all_data:
        trades = data["trades"]
        if "iv_mid" not in trades.columns:
            continue
        reliable = trades[trades["reliable_iv"]].copy()
        if reliable.empty:
            continue
        reliable["bucket"] = (reliable["secs"] // 30).astype(int) * 30
        all_points.append(reliable[["bucket", "iv_mid"]])

    if not all_points:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No IV data", ha="center", va="center",
                transform=ax.transAxes)
        return fig

    combined = pd.concat(all_points)
    buckets = sorted(combined["bucket"].unique())

    fig, ax = plt.subplots(figsize=(14, 5))

    box_data = [combined[combined["bucket"] == b]["iv_mid"].values for b in buckets]
    bp = ax.boxplot(
        box_data,
        positions=buckets,
        widths=20,
        patch_artist=True,
        showfliers=False,
    )

    for patch in bp["boxes"]:
        patch.set_facecolor(COLORS["iv_band"].replace("18", "40"))
        patch.set_edgecolor(COLORS["iv"])
    for element in ["whiskers", "caps", "medians"]:
        for line in bp[element]:
            line.set_color(COLORS["iv"])

    _setup_ax(ax, "Implied vol (annualized)")
    ax.set_xlabel("Time into window")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.set_title(
        f"IV distribution by time bucket ({len(all_data)} windows)",
        fontweight="bold",
    )

    fig.tight_layout()
    return fig