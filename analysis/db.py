"""
Database loading layer for Polymarket BTC binary options analysis.

Reads from collector.db and returns clean pandas DataFrames.
All timestamp math happens here so downstream code just works
with 'seconds into window' as the time axis.
"""

import sqlite3
from pathlib import Path

import pandas as pd

# Default database path — sits in project root next to src/ and analysis/
DEFAULT_DB = Path(__file__).parent.parent / "collector.db"


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open a read-only connection to the collector database."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    # Read-only via URI so we never accidentally write from analysis code
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    return conn


def get_windows(db_path: Path = DEFAULT_DB) -> pd.DataFrame:
    """
    List all complete windows with metadata.

    Returns DataFrame with columns:
        window_ts      — Unix timestamp of window start
        price_to_beat  — strike price (BTC price at open)
        status         — 'complete', 'collecting', or 'failed'
    """
    conn = connect(db_path)
    df = pd.read_sql(
        """
        SELECT window_ts, price_to_beat, status
        FROM events
        WHERE status = 'complete'
        ORDER BY window_ts
        """,
        conn,
    )
    conn.close()
    return df


def load_trades(window_ts: int, conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Load Polymarket trades for a single window.

    Adds 'secs' column = seconds into the 5-minute window.
    This is the natural time axis for all analysis.
    """
    df = pd.read_sql(
        """
        SELECT normalized_ts, up_bid, up_ask, down_bid, down_ask, side, size
        FROM price_changes
        WHERE window_ts = ?
        ORDER BY normalized_ts
        """,
        conn,
        params=(window_ts,),
    )
    # Convert millisecond timestamps to seconds into window
    # window_ts is in seconds, normalized_ts is in milliseconds
    df["secs"] = (df["normalized_ts"] - window_ts * 1000) / 1000.0

    # Derived columns that every analysis will need
    df["mid"] = (df["up_bid"] + df["up_ask"]) / 2  # mid-price of Up token
    df["spread"] = df["up_ask"] - df["up_bid"]  # bid-ask spread

    return df


def load_btc(window_ts: int, conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Load Binance BTC prices for a single window.

    Adds 'secs' column matching the trade time axis.
    """
    df = pd.read_sql(
        """
        SELECT normalized_ts, mark_price, index_price
        FROM btc_prices
        WHERE window_ts = ?
        ORDER BY normalized_ts
        """,
        conn,
        params=(window_ts,),
    )
    df["secs"] = (df["normalized_ts"] - window_ts * 1000) / 1000.0
    return df


def get_window_data(window_ts: int, db_path: Path = DEFAULT_DB) -> dict:
    """
    Load and join all data for a single window.

    This is the main function downstream code should call.
    It returns a dict with everything needed for analysis:

        {
            'window_ts':       int,
            'price_to_beat':   float,    # strike price
            'trades':          DataFrame, # Polymarket trades with nearest BTC price
            'btc':             DataFrame, # raw BTC price series
            'final_btc':       float,    # BTC price at window end
            'up_wins':         bool,     # did Up token pay out?
        }

    The 'trades' DataFrame has these columns:
        secs, up_bid, up_ask, down_bid, down_ask, side, size,
        mid, spread, btc_price, time_remaining

    The key join: each trade gets the most recent BTC price via merge_asof.
    This lets you compute IV, fair value, etc. for every single trade.
    """
    conn = connect(db_path)

    # Get strike price
    event = pd.read_sql(
        "SELECT price_to_beat FROM events WHERE window_ts = ?",
        conn,
        params=(window_ts,),
    )
    if event.empty:
        conn.close()
        raise ValueError(f"Window {window_ts} not found in events table")

    price_to_beat = event.iloc[0]["price_to_beat"]

    # Load both data sources
    trades = load_trades(window_ts, conn)
    btc = load_btc(window_ts, conn)
    conn.close()

    if trades.empty or btc.empty:
        raise ValueError(f"Window {window_ts} has no trade or BTC data")

    # --- The important join ---
    # merge_asof: for each trade, find the most recent BTC price.
    # Both DataFrames must be sorted by the join key (normalized_ts).
    # direction='backward' means: find the BTC tick at or just before the trade.
    trades = pd.merge_asof(
        trades.sort_values("normalized_ts"),
        btc[["normalized_ts", "index_price"]].sort_values("normalized_ts"),
        on="normalized_ts",
        direction="backward",
    ).rename(columns={"index_price": "btc_price"})

    # Early trades may arrive before the first BTC tick (merge_asof returns NaN
    # for rows before the first match). Fill those with the earliest BTC price
    # we do have — it's only off by 1-2 seconds.
    if trades["btc_price"].isna().any():
        first_btc = btc.iloc[0]["index_price"]
        trades["btc_price"] = trades["btc_price"].fillna(first_btc)

    # Time remaining in the window (seconds). Needed for Black-Scholes.
    # Window is 300 seconds total.
    trades["time_remaining"] = 300.0 - trades["secs"]

    # Determine outcome
    final_btc = btc.iloc[-1]["index_price"]
    up_wins = final_btc >= price_to_beat

    return {
        "window_ts": window_ts,
        "price_to_beat": price_to_beat,
        "trades": trades,
        "btc": btc,
        "final_btc": final_btc,
        "up_wins": up_wins,
    }


def get_all_window_data(db_path: Path = DEFAULT_DB) -> list[dict]:
    """Load data for all complete windows. Returns list of get_window_data dicts."""
    windows = get_windows(db_path)
    results = []
    for _, row in windows.iterrows():
        try:
            data = get_window_data(int(row["window_ts"]), db_path)
            results.append(data)
        except ValueError as e:
            print(f"Skipping window {row['window_ts']}: {e}")
    return results