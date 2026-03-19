// src/config.rs

/// Polymarket Gamma API base URL
pub const GAMMA_API_URL: &str = "https://gamma-api.polymarket.com";

/// Polymarket websocket URL
pub const POLYMARKET_WS_URL: &str = "wss://ws-subscriptions-clob.polymarket.com/ws/market";

/// Binance futures websocket URL (mark price stream)
pub const BINANCE_WS_URL: &str = "wss://fstream.binance.com/ws/btcusdt@markPrice@1s";

/// Market slug prefix
pub const MARKET_SLUG_PREFIX: &str = "btc-updown-5m-";

/// Window duration in seconds
pub const WINDOW_DURATION_SECS: u64 = 300;

/// Delay before fetching price_to_beat after window ends
pub const BACKFILL_DELAY_SECS: u64 = 5;

/// Ping interval for Polymarket websocket
pub const PING_INTERVAL_SECS: u64 = 10;

/// Default database path
pub const DEFAULT_DB_PATH: &str = "collector.db";

/// Get database path from environment or use default
pub fn db_path() -> String {
    std::env::var("DB_PATH").unwrap_or_else(|_| DEFAULT_DB_PATH.to_string())
}