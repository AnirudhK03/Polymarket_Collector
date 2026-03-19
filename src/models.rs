// src/models.rs

use serde::Deserialize;

// ============================================================================
// API Response Models (from Polymarket Gamma API)
// ============================================================================

#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Event {
    pub id: String,
    pub slug: String,
    pub title: String,
    pub active: bool,
    pub closed: bool,
    pub markets: Vec<Market>,
    #[serde(default)]
    pub event_metadata: Option<EventMetadata>,
}

#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Market {
    pub id: String,
    pub slug: String,
    pub outcomes: String,
    pub outcome_prices: String,
    pub clob_token_ids: String,
    pub active: bool,
    pub closed: bool,
    pub end_date: String,
    #[serde(default)]
    pub best_bid: Option<f64>,
    #[serde(default)]
    pub best_ask: Option<f64>,
}

#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct EventMetadata {
    #[serde(default)]
    pub price_to_beat: Option<f64>,
}

// ============================================================================
// Polymarket Websocket Models
// ============================================================================

#[derive(Deserialize, Debug)]
pub struct PolymarketWsMessage {
    pub market: String,
    pub price_changes: Vec<PriceChange>,
    pub timestamp: String,
    pub event_type: String,
}

#[derive(Deserialize, Debug)]
pub struct PriceChange {
    pub asset_id: String,
    pub price: String,
    pub size: String,
    pub side: String,
    pub best_bid: String,
    pub best_ask: String,
}

// ============================================================================
// Binance Websocket Models
// ============================================================================

#[derive(Deserialize, Debug)]
pub struct BinanceWsMessage {
    #[serde(rename = "e")]
    pub event_type: String,
    #[serde(rename = "E")]
    pub event_time: i64,
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "p")]
    pub mark_price: String,
    #[serde(rename = "i")]
    pub index_price: String,
}

// ============================================================================
// Database Row Models
// ============================================================================

#[derive(Debug, Clone)]
pub struct EventRow {
    pub window_ts: i64,
    pub price_to_beat: Option<f64>,
    pub up_token_id: String,
    pub down_token_id: String,
    pub status: WindowStatus,
}

#[derive(Debug, Clone)]
pub struct PriceRow {
    pub window_ts: i64,
    pub server_ts: i64,
    pub normalized_ts: i64,
    pub up_bid: f64,
    pub up_ask: f64,
    pub down_bid: f64,
    pub down_ask: f64,
    pub side: String,
    pub size: f64,
}

#[derive(Debug, Clone)]
pub struct BtcPriceRow {
    pub window_ts: i64,
    pub server_ts: i64,
    pub normalized_ts: i64,
    pub mark_price: f64,
    pub index_price: f64,
}

// ============================================================================
// Runtime State Models
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum WindowStatus {
    Collecting,
    Complete,
    Failed,
}

impl WindowStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            WindowStatus::Collecting => "collecting",
            WindowStatus::Complete => "complete",
            WindowStatus::Failed => "failed",
        }
    }
}

#[derive(Debug)]
pub struct WebsocketState {
    pub clock_offset: i64,
}

impl WebsocketState {
    pub fn new(local_time: i64, server_time: i64) -> Self {
        Self {
            clock_offset: local_time - server_time,
        }
    }

    pub fn normalize(&self, server_ts: i64) -> i64 {
        server_ts + self.clock_offset
    }
}

#[derive(Debug)]
pub struct WindowData {
    pub window_ts: i64,
    pub prices: Vec<PriceRow>,
    pub btc_prices: Vec<BtcPriceRow>,
}