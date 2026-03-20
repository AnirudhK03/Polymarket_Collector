"""
Interactive Plotly charts for Polymarket BTC binary options analysis.

Mirrors the charts in static.py but with hover tooltips, zoom, pan,
and legend toggling. Outputs self-contained HTML files you can open
in any browser.

Usage:
    from analysis.db import get_window_data
    from analysis.models import add_iv
    from analysis.viz.interactive import plot_window, dashboard

    data = get_window_data(1773963000)
    add_iv(data['trades'], data['price_to_beat'])
    fig = plot_window(data)
    fig.write_html('window.html')

    # Or build a multi-window dashboard:
    all_data = [get_window_data(ts) for ts in window_list]
    fig = dashboard(all_data)
    fig.write_html('dashboard.html')
"""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# -- Colors (matching static.py) -------------------------------------------

COLORS = {
    "up_mid": "#3b82f6",
    "up_band": "rgba(59,130,246,0.12)",
    "btc": "#f59e0b",
    "strike": "rgba(133,79,11,0.5)",
    "iv": "#10b981",
    "iv_band": "rgba(16,185,129,0.10)",
    "buy": "#3b82f6",
    "sell": "#ef4444",
    "model": "#a855f7",
    "mispricing_pos": "#ef4444",
    "mispricing_neg": "#10b981",
}


def _fmt_secs(s: float) -> str:
    """Format seconds as M:SS."""
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _window_label(data: dict) -> str:
    """Short label for dropdown menus."""
    ts = data["window_ts"]
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    outcome = "UP" if data["up_wins"] else "DOWN"
    return f"{dt.strftime('%H:%M')} UTC — {outcome}"


def _window_title(data: dict) -> str:
    """Full descriptive title."""
    ts = data["window_ts"]
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    outcome = "UP wins" if data["up_wins"] else "DOWN wins"
    return (
        f"Window {dt.strftime('%H:%M')} UTC | "
        f"Strike ${data['price_to_beat']:,.0f} | "
        f"Final ${data['final_btc']:,.0f} | "
        f"{outcome}"
    )


def _x_axis(title: bool = False) -> dict:
    """Common x-axis config: 0-300 seconds, M:SS labels."""
    return dict(
        range=[0, 300],
        tickvals=list(range(0, 301, 60)),
        ticktext=[_fmt_secs(s) for s in range(0, 301, 60)],
        title="Time into window" if title else None,
        gridcolor="rgba(0,0,0,0.06)",
    )


def _layout_defaults() -> dict:
    """Shared layout settings for all charts."""
    return dict(
        template="plotly_white",
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(size=12, color="#1a1a1a"),
        hovermode="x unified",
        margin=dict(l=60, r=60, t=50, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=10),
        ),
    )


# -- Individual chart functions --------------------------------------------


def chart_price(data: dict) -> go.Figure:
    """
    Interactive price chart: Up token mid + bid/ask band + BTC price.

    Hover over any point to see exact values. Click legend entries
    to toggle series. Zoom with scroll wheel.
    """
    trades = data["trades"]
    btc = data["btc"]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Bid/ask band as filled area
    fig.add_trace(
        go.Scatter(
            x=trades["secs"], y=trades["up_ask"],
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=trades["secs"], y=trades["up_bid"],
            mode="lines", line=dict(width=0),
            fill="tonexty", fillcolor=COLORS["up_band"],
            name="Bid-ask band", hoverinfo="skip",
        ),
        secondary_y=False,
    )

    # Up mid-price
    fig.add_trace(
        go.Scatter(
            x=trades["secs"], y=trades["mid"],
            mode="lines", line=dict(color=COLORS["up_mid"], width=1.5),
            name="Up mid",
            customdata=np.stack([trades["up_bid"], trades["up_ask"], trades["spread"]], axis=-1),
            hovertemplate=(
                "Mid: %{y:.3f}<br>"
                "Bid: %{customdata[0]:.3f}<br>"
                "Ask: %{customdata[1]:.3f}<br>"
                "Spread: %{customdata[2]:.3f}<extra></extra>"
            ),
        ),
        secondary_y=False,
    )

    # BTC price
    fig.add_trace(
        go.Scatter(
            x=btc["secs"], y=btc["index_price"],
            mode="lines", line=dict(color=COLORS["btc"], width=1.2),
            name="BTC",
            hovertemplate="$%{y:,.2f}<extra></extra>",
        ),
        secondary_y=True,
    )

    # Strike line
    fig.add_hline(
        y=data["price_to_beat"], line_dash="dash",
        line_color=COLORS["strike"], line_width=1,
        secondary_y=True, annotation_text="Strike",
        annotation_position="top right",
        annotation_font_size=10,
    )

    fig.update_layout(
        **_layout_defaults(),
        title=_window_title(data),
        height=350,
    )
    fig.update_xaxes(**_x_axis())
    fig.update_yaxes(title_text="Up token price", range=[-0.02, 1.02], secondary_y=False)
    fig.update_yaxes(title_text="BTC ($)", secondary_y=True)

    return fig


def chart_iv(data: dict) -> go.Figure:
    """
    Interactive IV chart with bid/ask band.

    Hover to see exact IV values. Only shows reliable IV points.
    """
    trades = data["trades"]

    if "iv_mid" not in trades.columns:
        raise ValueError("Call models.add_iv() before plotting IV")

    reliable = trades[trades["reliable_iv"]].copy()
    fig = go.Figure()

    if reliable.empty:
        fig.add_annotation(text="No reliable IV data", x=150, y=0.5,
                           showarrow=False, font=dict(size=16))
        fig.update_layout(**_layout_defaults(), height=280)
        return fig

    # IV band
    band = reliable.dropna(subset=["iv_bid", "iv_ask"])
    if not band.empty:
        fig.add_trace(go.Scatter(
            x=band["secs"], y=band["iv_ask"],
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=band["secs"], y=band["iv_bid"],
            mode="lines", line=dict(width=0),
            fill="tonexty", fillcolor=COLORS["iv_band"],
            name="IV band", hoverinfo="skip",
        ))

    # IV mid as scatter
    fig.add_trace(go.Scatter(
        x=reliable["secs"], y=reliable["iv_mid"],
        mode="markers", marker=dict(color=COLORS["iv"], size=4, opacity=0.6),
        name="IV mid",
        hovertemplate="IV: %{y:.1%}<br>t=%{x:.0f}s<extra></extra>",
    ))

    # Auto-scale y
    iv_vals = reliable["iv_mid"].dropna()
    if not iv_vals.empty:
        p5, p95 = iv_vals.quantile(0.05), iv_vals.quantile(0.95)
        margin = (p95 - p5) * 0.3
        ymin, ymax = max(0, p5 - margin), p95 + margin
    else:
        ymin, ymax = 0, 1

    fig.update_layout(
        **_layout_defaults(),
        title="Implied volatility (annualized)",
        height=280,
        yaxis=dict(
            title="IV",
            range=[ymin, ymax],
            tickformat=".0%",
            gridcolor="rgba(0,0,0,0.06)",
        ),
    )
    fig.update_xaxes(**_x_axis())

    return fig


def chart_volume(data: dict, bucket_secs: int = 10) -> go.Figure:
    """
    Interactive volume chart: buy vs sell aggressor in time buckets.

    Hover to see exact dollar volume per bucket.
    """
    trades = data["trades"]
    n_buckets = 300 // bucket_secs

    buy_vol = np.zeros(n_buckets)
    sell_vol = np.zeros(n_buckets)

    for _, row in trades.iterrows():
        b = min(int(row["secs"] // bucket_secs), n_buckets - 1)
        if row["side"] == "BUY":
            buy_vol[b] += row["size"]
        else:
            sell_vol[b] += row["size"]

    x_labels = [_fmt_secs(i * bucket_secs) for i in range(n_buckets)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x_labels, y=buy_vol,
        name="BUY", marker_color=COLORS["buy"], opacity=0.7,
        hovertemplate="BUY: $%{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=x_labels, y=-sell_vol,
        name="SELL", marker_color=COLORS["sell"], opacity=0.7,
        hovertemplate="SELL: $%{customdata:,.0f}<extra></extra>",
        customdata=sell_vol,
    ))

    fig.update_layout(
        **_layout_defaults(),
        title="Trade volume by aggressor",
        height=250,
        barmode="relative",
        yaxis=dict(title="Volume ($)", gridcolor="rgba(0,0,0,0.06)"),
    )

    return fig


def chart_mispricing(data: dict, sigma: float = None) -> go.Figure:
    """
    Interactive mispricing chart: model fair value vs market mid.

    Red = market is expensive vs model, green = market is cheap.
    """
    trades = data["trades"]

    if "iv_mid" not in trades.columns:
        raise ValueError("Call models.add_iv() before plotting mispricing")

    if sigma is None:
        reliable = trades[trades["reliable_iv"]]
        if reliable.empty:
            fig = go.Figure()
            fig.add_annotation(text="No reliable IV", x=150, y=0, showarrow=False)
            fig.update_layout(**_layout_defaults(), height=280)
            return fig
        sigma = reliable["iv_mid"].median()

    from analysis.models import add_fair_value
    add_fair_value(trades, data["price_to_beat"], sigma)

    mask = trades["secs"] > 5
    t = trades[mask].copy()

    # Color by sign of mispricing
    t["color"] = t["mispricing"].apply(
        lambda v: COLORS["mispricing_pos"] if v > 0 else COLORS["mispricing_neg"]
    )

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t["secs"], y=t["mispricing"],
        mode="markers",
        marker=dict(color=t["color"], size=4, opacity=0.5),
        name="Mispricing",
        customdata=np.stack([t["mid"], t["model_fv"]], axis=-1),
        hovertemplate=(
            "Mispricing: %{y:.3f}<br>"
            "Market mid: %{customdata[0]:.3f}<br>"
            "Model FV: %{customdata[1]:.3f}<extra></extra>"
        ),
    ))

    fig.add_hline(y=0, line_color="rgba(0,0,0,0.15)", line_width=1)

    max_abs = max(
        abs(t["mispricing"].quantile(0.02)),
        abs(t["mispricing"].quantile(0.98)),
        0.01,
    )

    fig.update_layout(
        **_layout_defaults(),
        title=f"Mispricing (model σ = {sigma:.0%})",
        height=280,
        yaxis=dict(
            title="Market − Model",
            range=[-max_abs * 1.3, max_abs * 1.3],
            gridcolor="rgba(0,0,0,0.06)",
        ),
    )
    fig.update_xaxes(**_x_axis(title=True))

    return fig


# -- Composite: single window ---------------------------------------------


def plot_window(data: dict, sigma: float = None) -> go.Figure:
    """
    All four charts for a single window in one Plotly figure.

    Uses subplots with shared x-axis so zooming one zooms all.
    """
    trades = data["trades"]

    # Determine sigma for mispricing panel
    if sigma is None and "iv_mid" in trades.columns:
        reliable = trades[trades["reliable_iv"]]
        if not reliable.empty:
            sigma = reliable["iv_mid"].median()

    # Need fair value for mispricing
    if sigma is not None:
        from analysis.models import add_fair_value
        add_fair_value(trades, data["price_to_beat"], sigma)

    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.35, 0.25, 0.15, 0.25],
        specs=[
            [{"secondary_y": True}],
            [{}],
            [{}],
            [{}],
        ],
        subplot_titles=["Price", "Implied volatility", "Volume", "Mispricing"],
    )

    btc = data["btc"]

    # --- Row 1: Price ---
    fig.add_trace(go.Scatter(
        x=trades["secs"], y=trades["up_ask"],
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ), row=1, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=trades["secs"], y=trades["up_bid"],
        mode="lines", line=dict(width=0),
        fill="tonexty", fillcolor=COLORS["up_band"],
        name="Bid-ask", hoverinfo="skip",
    ), row=1, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=trades["secs"], y=trades["mid"],
        mode="lines", line=dict(color=COLORS["up_mid"], width=1.5),
        name="Up mid",
        hovertemplate="Mid: %{y:.3f}<extra></extra>",
    ), row=1, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=btc["secs"], y=btc["index_price"],
        mode="lines", line=dict(color=COLORS["btc"], width=1.2),
        name="BTC",
        hovertemplate="$%{y:,.2f}<extra></extra>",
    ), row=1, col=1, secondary_y=True)

    # --- Row 2: IV ---
    if "iv_mid" in trades.columns:
        reliable = trades[trades["reliable_iv"]]

        band = reliable.dropna(subset=["iv_bid", "iv_ask"])
        if not band.empty:
            fig.add_trace(go.Scatter(
                x=band["secs"], y=band["iv_ask"],
                mode="lines", line=dict(width=0),
                showlegend=False, hoverinfo="skip",
            ), row=2, col=1)
            fig.add_trace(go.Scatter(
                x=band["secs"], y=band["iv_bid"],
                mode="lines", line=dict(width=0),
                fill="tonexty", fillcolor=COLORS["iv_band"],
                name="IV band", hoverinfo="skip",
            ), row=2, col=1)

        if not reliable.empty:
            fig.add_trace(go.Scatter(
                x=reliable["secs"], y=reliable["iv_mid"],
                mode="markers",
                marker=dict(color=COLORS["iv"], size=3, opacity=0.6),
                name="IV mid",
                hovertemplate="IV: %{y:.1%}<extra></extra>",
            ), row=2, col=1)

    # --- Row 3: Volume ---
    bucket_secs = 10
    n_buckets = 300 // bucket_secs
    buy_vol = np.zeros(n_buckets)
    sell_vol = np.zeros(n_buckets)
    for _, row in trades.iterrows():
        b = min(int(row["secs"] // bucket_secs), n_buckets - 1)
        if row["side"] == "BUY":
            buy_vol[b] += row["size"]
        else:
            sell_vol[b] += row["size"]

    x_centers = np.arange(n_buckets) * bucket_secs + bucket_secs / 2

    fig.add_trace(go.Bar(
        x=x_centers, y=buy_vol, width=bucket_secs * 0.8,
        name="BUY vol", marker_color=COLORS["buy"], opacity=0.7,
        hovertemplate="$%{y:,.0f}<extra></extra>",
    ), row=3, col=1)

    fig.add_trace(go.Bar(
        x=x_centers, y=-sell_vol, width=bucket_secs * 0.8,
        name="SELL vol", marker_color=COLORS["sell"], opacity=0.7,
        hovertemplate="$%{customdata:,.0f}<extra></extra>",
        customdata=sell_vol,
    ), row=3, col=1)

    # --- Row 4: Mispricing ---
    if sigma is not None and "mispricing" in trades.columns:
        mask = trades["secs"] > 5
        t = trades[mask]
        mp_colors = [
            COLORS["mispricing_pos"] if v > 0 else COLORS["mispricing_neg"]
            for v in t["mispricing"]
        ]
        fig.add_trace(go.Scatter(
            x=t["secs"], y=t["mispricing"],
            mode="markers",
            marker=dict(color=mp_colors, size=3, opacity=0.5),
            name=f"Mispricing (σ={sigma:.0%})",
            hovertemplate="Mispricing: %{y:.3f}<extra></extra>",
        ), row=4, col=1)

        fig.add_hline(y=0, line_color="rgba(0,0,0,0.12)",
                      line_width=1, row=4, col=1)

    # --- Layout ---
    fig.update_layout(
        **_layout_defaults(),
        title=_window_title(data),
        height=900,
        barmode="relative",
    )

    # Shared x-axis config
    for i in range(1, 5):
        fig.update_xaxes(**_x_axis(title=(i == 4)), row=i, col=1)

    # Y-axis formatting
    fig.update_yaxes(title_text="Up price", range=[-0.02, 1.02], row=1, col=1,
                     secondary_y=False)
    fig.update_yaxes(title_text="BTC ($)", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="IV", tickformat=".0%", row=2, col=1)
    fig.update_yaxes(title_text="Volume ($)", row=3, col=1)
    fig.update_yaxes(title_text="Market − Model", row=4, col=1)

    return fig


# -- Multi-window dashboard ------------------------------------------------


def dashboard(all_data: list[dict]) -> go.Figure:
    """
    Multi-window dashboard with dropdown to switch between windows.

    Creates a single Plotly figure where each window is a set of traces.
    The dropdown menu toggles visibility so only one window shows at a time.
    This makes a single HTML file you can browse through all your data.
    """
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.40, 0.30, 0.30],
        specs=[
            [{"secondary_y": True}],
            [{}],
            [{}],
        ],
        subplot_titles=["Price + BTC", "Implied volatility", "Volume"],
    )

    buttons = []
    # Each window adds: up_ask, up_bid, mid, btc, iv_ask, iv_bid, iv_mid, buy_bar, sell_bar
    traces_per_window = 9

    bucket_secs = 10
    n_buckets = 300 // bucket_secs

    for i, data in enumerate(all_data):
        trades = data["trades"]
        btc = data["btc"]
        visible = i == 0  # only first window visible initially

        if "iv_mid" not in trades.columns:
            from analysis.models import add_iv
            add_iv(trades, data["price_to_beat"])

        reliable = trades[trades["reliable_iv"]]
        band = reliable.dropna(subset=["iv_bid", "iv_ask"])

        # Row 1: Price
        fig.add_trace(go.Scatter(
            x=trades["secs"], y=trades["up_ask"],
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip", visible=visible,
        ), row=1, col=1, secondary_y=False)

        fig.add_trace(go.Scatter(
            x=trades["secs"], y=trades["up_bid"],
            mode="lines", line=dict(width=0),
            fill="tonexty", fillcolor=COLORS["up_band"],
            name="Bid-ask", hoverinfo="skip", visible=visible,
            showlegend=(i == 0),
        ), row=1, col=1, secondary_y=False)

        fig.add_trace(go.Scatter(
            x=trades["secs"], y=trades["mid"],
            mode="lines", line=dict(color=COLORS["up_mid"], width=1.5),
            name="Up mid", visible=visible,
            hovertemplate="Mid: %{y:.3f}<extra></extra>",
            showlegend=(i == 0),
        ), row=1, col=1, secondary_y=False)

        fig.add_trace(go.Scatter(
            x=btc["secs"], y=btc["index_price"],
            mode="lines", line=dict(color=COLORS["btc"], width=1.2),
            name="BTC", visible=visible,
            hovertemplate="$%{y:,.2f}<extra></extra>",
            showlegend=(i == 0),
        ), row=1, col=1, secondary_y=True)

        # Row 2: IV
        fig.add_trace(go.Scatter(
            x=band["secs"] if not band.empty else [],
            y=band["iv_ask"] if not band.empty else [],
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip", visible=visible,
        ), row=2, col=1)

        fig.add_trace(go.Scatter(
            x=band["secs"] if not band.empty else [],
            y=band["iv_bid"] if not band.empty else [],
            mode="lines", line=dict(width=0),
            fill="tonexty", fillcolor=COLORS["iv_band"],
            name="IV band", hoverinfo="skip", visible=visible,
            showlegend=(i == 0),
        ), row=2, col=1)

        fig.add_trace(go.Scatter(
            x=reliable["secs"] if not reliable.empty else [],
            y=reliable["iv_mid"] if not reliable.empty else [],
            mode="markers",
            marker=dict(color=COLORS["iv"], size=3, opacity=0.6),
            name="IV mid", visible=visible,
            hovertemplate="IV: %{y:.1%}<extra></extra>",
            showlegend=(i == 0),
        ), row=2, col=1)

        # Row 3: Volume
        buy_vol = np.zeros(n_buckets)
        sell_vol = np.zeros(n_buckets)
        for _, row in trades.iterrows():
            b = min(int(row["secs"] // bucket_secs), n_buckets - 1)
            if row["side"] == "BUY":
                buy_vol[b] += row["size"]
            else:
                sell_vol[b] += row["size"]

        x_centers = (np.arange(n_buckets) * bucket_secs + bucket_secs / 2).tolist()

        fig.add_trace(go.Bar(
            x=x_centers, y=buy_vol.tolist(), width=bucket_secs * 0.8,
            name="BUY vol", marker_color=COLORS["buy"], opacity=0.7,
            visible=visible, showlegend=(i == 0),
            hovertemplate="$%{y:,.0f}<extra></extra>",
        ), row=3, col=1)

        fig.add_trace(go.Bar(
            x=x_centers, y=(-sell_vol).tolist(), width=bucket_secs * 0.8,
            name="SELL vol", marker_color=COLORS["sell"], opacity=0.7,
            visible=visible, showlegend=(i == 0),
            hovertemplate="$%{customdata:,.0f}<extra></extra>",
            customdata=sell_vol.tolist(),
        ), row=3, col=1)

        # Button: toggle this window's traces on, all others off
        visibility = [False] * (len(all_data) * traces_per_window)
        for j in range(traces_per_window):
            visibility[i * traces_per_window + j] = True

        buttons.append(dict(
            label=_window_label(data),
            method="update",
            args=[
                {"visible": visibility},
                {"title": _window_title(data)},
            ],
        ))

    layout = _layout_defaults()
    layout["margin"] = dict(l=60, r=60, t=100, b=40)

    fig.update_layout(
        **layout,
        title=dict(
            text=_window_title(all_data[0]),
            y=0.98,
            x=0.35,
            xanchor="left",
        ),
        height=900,
        barmode="relative",
        updatemenus=[dict(
            type="dropdown",
            direction="down",
            x=0.0,
            xanchor="left",
            y=1.08,
            yanchor="top",
            buttons=buttons,
            font=dict(size=11),
            bgcolor="rgba(245,245,245,0.95)",
        )],
    )

    for i in range(1, 4):
        fig.update_xaxes(**_x_axis(title=(i == 3)), row=i, col=1)

    fig.update_yaxes(title_text="Up price", range=[-0.02, 1.02], row=1, col=1,
                     secondary_y=False)
    fig.update_yaxes(title_text="BTC ($)", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="IV", tickformat=".0%", range=[0, 1.0], row=2, col=1)
    fig.update_yaxes(title_text="Volume ($)", row=3, col=1)

    return fig


def iv_overlay(all_data: list[dict]) -> go.Figure:
    """
    Overlay IV curves from all windows on one chart.

    Each window is a separate line. Hover to see which window
    and the exact IV value. Useful for spotting if there's a
    consistent IV pattern across windows.
    """
    fig = go.Figure()

    for data in all_data:
        trades = data["trades"]
        if "iv_mid" not in trades.columns:
            continue

        reliable = trades[trades["reliable_iv"]]
        if reliable.empty:
            continue

        label = _window_label(data)
        fig.add_trace(go.Scatter(
            x=reliable["secs"], y=reliable["iv_mid"],
            mode="lines", line=dict(width=1),
            opacity=0.5, name=label,
            hovertemplate=f"{label}<br>IV: %{{y:.1%}}<br>t=%{{x:.0f}}s<extra></extra>",
        ))

    fig.update_layout(
        **_layout_defaults(),
        title=f"IV across {len(all_data)} windows",
        height=500,
        yaxis=dict(title="IV (annualized)", tickformat=".0%",
                   gridcolor="rgba(0,0,0,0.06)"),
    )
    fig.update_xaxes(**_x_axis(title=True))

    return fig