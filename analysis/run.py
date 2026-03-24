"""
CLI entry point for Polymarket BTC binary options analysis.

Usage:
    python -m analysis.run plot --window 1773963000
    python -m analysis.run plot --all
    python -m analysis.run dashboard
    python -m analysis.run iv-overlay
    python -m analysis.run list
"""

import argparse
import sys
import time
from pathlib import Path

from analysis.db import get_windows, get_window_data, get_all_window_data
from analysis.models import add_iv


# Default paths
DEFAULT_DB = Path("collector.db")
OUTPUT_DIR = Path("output")


def ensure_output_dir():
    OUTPUT_DIR.mkdir(exist_ok=True)


def load_and_compute(window_ts: int, db_path: Path) -> dict:
    """Load a window and compute IV. Returns the data dict."""
    data = get_window_data(window_ts, db_path)
    add_iv(data["trades"], data["price_to_beat"])
    return data


def load_all(db_path: Path) -> list[dict]:
    """Load all complete windows with IV computed."""
    windows = get_windows(db_path)
    all_data = []
    for _, row in windows.iterrows():
        ts = int(row["window_ts"])
        try:
            data = load_and_compute(ts, db_path)
            all_data.append(data)
        except ValueError as e:
            print(f"  Skipping {ts}: {e}")
    return all_data


# -- Commands --------------------------------------------------------------


def cmd_list(args):
    """List all complete windows."""
    windows = get_windows(args.db)
    if windows.empty:
        print("No complete windows found.")
        return

    print(f"{'window_ts':<15} {'price_to_beat':>15} {'status':<10}")
    print("-" * 42)
    for _, row in windows.iterrows():
        print(f"{int(row['window_ts']):<15} {row['price_to_beat']:>15,.2f} {row['status']:<10}")
    print(f"\n{len(windows)} complete windows")


def cmd_plot(args):
    """Generate static matplotlib charts."""
    import matplotlib
    matplotlib.use("Agg")
    from analysis.viz.static import plot_window

    ensure_output_dir()

    if args.window:
        # Single window
        print(f"Loading window {args.window}...")
        data = load_and_compute(args.window, args.db)
        fig = plot_window(data)
        out = OUTPUT_DIR / f"window_{args.window}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        print(f"Saved: {out}")
    else:
        # All windows
        windows = get_windows(args.db)
        print(f"Plotting {len(windows)} windows...")
        t0 = time.time()
        for i, (_, row) in enumerate(windows.iterrows()):
            ts = int(row["window_ts"])
            try:
                data = load_and_compute(ts, args.db)
                fig = plot_window(data)
                out = OUTPUT_DIR / f"window_{ts}.png"
                fig.savefig(out, dpi=150, bbox_inches="tight")
                import matplotlib.pyplot as plt
                plt.close(fig)
                print(f"  [{i+1}/{len(windows)}] {out}")
            except ValueError as e:
                print(f"  [{i+1}/{len(windows)}] Skipped {ts}: {e}")
        print(f"Done in {time.time() - t0:.1f}s")


def cmd_dashboard(args):
    """Generate interactive Plotly dashboard."""
    from analysis.viz.interactive import dashboard

    ensure_output_dir()

    print("Loading all windows...")
    t0 = time.time()
    all_data = load_all(args.db)
    print(f"Loaded {len(all_data)} windows in {time.time() - t0:.1f}s")

    print("Building dashboard...")
    fig = dashboard(all_data)
    out = OUTPUT_DIR / "dashboard.html"
    fig.write_html(str(out))
    print(f"Saved: {out}")


def cmd_interactive(args):
    """Generate interactive Plotly chart for a single window."""
    from analysis.viz.interactive import plot_window

    ensure_output_dir()

    print(f"Loading window {args.window}...")
    data = load_and_compute(args.window, args.db)
    fig = plot_window(data)
    out = OUTPUT_DIR / f"window_{args.window}.html"
    fig.write_html(str(out))
    print(f"Saved: {out}")


def cmd_iv_overlay(args):
    """Generate IV overlay chart across all windows."""
    from analysis.viz.interactive import iv_overlay

    ensure_output_dir()

    print("Loading all windows...")
    t0 = time.time()
    all_data = load_all(args.db)
    print(f"Loaded {len(all_data)} windows in {time.time() - t0:.1f}s")

    print("Building IV overlay...")
    fig = iv_overlay(all_data)
    out = OUTPUT_DIR / "iv_overlay.html"
    fig.write_html(str(out))
    print(f"Saved: {out}")


def cmd_iv_summary(args):
    """Generate static IV summary box plot across all windows."""
    import matplotlib
    matplotlib.use("Agg")
    from analysis.viz.static import chart_iv_summary

    ensure_output_dir()

    print("Loading all windows...")
    all_data = load_all(args.db)

    fig = chart_iv_summary(all_data)
    out = OUTPUT_DIR / "iv_summary.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved: {out}")


def cmd_stats(args):
    """Print statistical report across all windows."""
    from analysis.stats import compute_all_stats, print_report

    print("Loading all windows...")
    t0 = time.time()
    all_data = load_all(args.db)
    print(f"Loaded {len(all_data)} windows in {time.time() - t0:.1f}s\n")

    stats = compute_all_stats(all_data)
    print_report(stats)

def cmd_timeseries(args):
    """Print time series analysis report."""
    from analysis.timeseries import compute_all_timeseries, print_report as ts_report
 
    print("Loading all windows...")
    t0 = time.time()
    all_data = load_all(args.db)
    print(f"Loaded {len(all_data)} windows in {time.time() - t0:.1f}s\n")
 
    ts = compute_all_timeseries(all_data)
    ts_report(ts)

# -- Argument parser -------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket BTC binary options analysis",
        prog="python -m analysis.run",
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help="Path to collector.db (default: ./collector.db)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", help="List all complete windows")

    # plot
    p_plot = subparsers.add_parser("plot", help="Generate matplotlib PNGs")
    p_plot.add_argument("-w", "--window", type=int, help="Single window_ts (omit for all)")
    p_plot.add_argument("--all", action="store_true", help="Plot all windows")

    # interactive
    p_int = subparsers.add_parser("interactive", help="Generate Plotly HTML for one window")
    p_int.add_argument("-w", "--window", type=int, required=True, help="window_ts")

    # dashboard
    subparsers.add_parser("dashboard", help="Generate multi-window Plotly dashboard")

    # iv-overlay
    subparsers.add_parser("iv-overlay", help="IV curves overlaid across all windows")

    # iv-summary
    subparsers.add_parser("iv-summary", help="IV box plot across all windows")

    # stats
    subparsers.add_parser("stats", help="Print statistical report across all windows")

    #timeseries
    subparsers.add_parser("timeseries", help="Time series analysis of BTC dynamics")

    args = parser.parse_args()

    commands = {
        "list": cmd_list,
        "plot": cmd_plot,
        "interactive": cmd_interactive,
        "dashboard": cmd_dashboard,
        "iv-overlay": cmd_iv_overlay,
        "iv-summary": cmd_iv_summary,
        "stats": cmd_stats,
        "timeseries": cmd_timeseries,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()