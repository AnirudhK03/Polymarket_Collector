# Polymarket Collector

A Rust-based data collection pipeline for Polymarket's BTC 5-minute Up/Down prediction markets.

## What It Does

Collects real-time data from two sources simultaneously:
- **Polymarket websocket** — orderbook price changes for BTC Up/Down binary markets
- **Binance websocket** — BTC mark price and index price (1 update per second)

All data is stored in a local SQLite database with synchronized timestamps for analysis.

## Architecture
```
┌─────────────────────────────────────────────────────────────────┐
│                          MAIN LOOP                               │
│                                                                  │
│  1. Wait for window boundary (every 300 seconds)                │
│  2. Fetch token IDs from Polymarket API                         │
│  3. Spawn backfill task for previous window's price_to_beat     │
│  4. Connect both websockets                                      │
│  5. Collect until window ends (or failure)                      │
│  6. Write data atomically to SQLite                             │
│  7. Loop                                                         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure
```
src/
├── main.rs           # Entry point
├── config.rs         # Constants (URLs, timing, etc.)
├── models.rs         # Data structures
├── error.rs          # Error types
├── timing.rs         # Window timing helpers
├── api.rs            # Polymarket REST API calls
├── db.rs             # SQLite operations
├── collector.rs      # Main collection loop
└── ws/
    ├── mod.rs        # Module exports
    ├── binance.rs    # Binance websocket handler
    └── polymarket.rs # Polymarket websocket handler
```

## Database Schema
```sql
-- Event metadata (one row per 5-minute window)
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_ts INTEGER NOT NULL UNIQUE,  -- Unix timestamp of window start
    price_to_beat REAL,                  -- BTC price at window start
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    status TEXT NOT NULL                 -- 'collecting', 'complete', 'failed'
);

-- Polymarket orderbook data
CREATE TABLE price_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_ts INTEGER NOT NULL,
    server_ts INTEGER NOT NULL,          -- Polymarket timestamp
    normalized_ts INTEGER NOT NULL,      -- Adjusted to local clock
    up_bid REAL NOT NULL,
    up_ask REAL NOT NULL,
    down_bid REAL NOT NULL,
    down_ask REAL NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL
);

-- Binance BTC price data
CREATE TABLE btc_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_ts INTEGER NOT NULL,
    server_ts INTEGER NOT NULL,          -- Binance timestamp
    normalized_ts INTEGER NOT NULL,      -- Adjusted to local clock
    mark_price REAL NOT NULL,
    index_price REAL NOT NULL
);
```

## Usage

### Build
```bash
cargo build --release
```

### Run
```bash
cargo run
```

Or for release build:
```bash
./target/release/polymarket_collector
```

### Keep Mac Awake
```bash
caffeinate -i cargo run
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `collector.db` | Path to SQLite database |

### Query Data
```bash
# Check collected windows
sqlite3 collector.db "SELECT window_ts, status, price_to_beat FROM events;"

# Count rows per window
sqlite3 collector.db "SELECT window_ts, COUNT(*) FROM price_changes GROUP BY window_ts;"
sqlite3 collector.db "SELECT window_ts, COUNT(*) FROM btc_prices GROUP BY window_ts;"

# View sample data
sqlite3 collector.db "SELECT * FROM price_changes LIMIT 10;"
```

## Key Features

### Timestamp Synchronization

Both websockets have different server clocks. We normalize timestamps to a common reference:
```
normalized_ts = server_ts + clock_offset
clock_offset = local_time - server_time (calculated on first message)
```

This ensures Polymarket and Binance data can be accurately joined for analysis.

### Deduplication

Polymarket sends ~100 messages/second, but most are duplicates. We only store rows when prices actually change, reducing ~30,000 raw messages to ~600-1000 meaningful data points per window.

### Atomic Writes

All data for a window is buffered in memory, then written in a single SQLite transaction when the window completes. If anything fails mid-window, no partial data is written.

### Backfill

The `price_to_beat` (BTC price at window start) isn't available from the API until ~2 minutes after the window ends. A background task fetches and updates this 3 minutes after each window completes.

## Error Handling

- If either websocket fails mid-window, both are cancelled and the window is marked as `failed`
- No partial data is stored
- The collector immediately moves to the next window
- `Ctrl+C` exits without cleanup (window stays as `collecting` - run manual cleanup)

### Manual Cleanup
```bash
sqlite3 collector.db "UPDATE events SET status = 'failed' WHERE status = 'collecting';"
```

## Dependencies

- `tokio` — async runtime
- `tokio-tungstenite` — websocket client
- `reqwest` — HTTP client
- `rusqlite` — SQLite
- `serde` / `serde_json` — JSON parsing
- `futures-util` — stream utilities
- `tokio-util` — cancellation tokens

## License

MIT