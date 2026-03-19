// src/ws/polymarket.rs

use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tokio_util::sync::CancellationToken;
use std::time::Duration;

use crate::config::{POLYMARKET_WS_URL, PING_INTERVAL_SECS};
use crate::error::{CollectorError, Result};
use crate::models::{PolymarketWsMessage, PriceRow, WebsocketState};
use crate::timing::now_millis;

/// Connect to Polymarket and collect price changes until cancelled or window ends
pub async fn collect(
    window_ts: i64,
    window_end_ms: i64,
    up_token_id: String,
    down_token_id: String,
    cancel_token: CancellationToken,
) -> Result<Vec<PriceRow>> {
    // Connect to websocket
    let (ws_stream, _) = connect_async(POLYMARKET_WS_URL).await?;
    let (mut write, mut read) = ws_stream.split();

    // Subscribe to the token IDs
    let subscribe_msg = serde_json::json!({
        "assets_ids": [&up_token_id, &down_token_id],
        "type": "market"
    });
    write.send(Message::Text(subscribe_msg.to_string().into())).await?;
    println!("[Polymarket] Subscribed to tokens");

    // Initialize buffer and state
    let mut buffer: Vec<PriceRow> = Vec::new();
    let mut state: Option<WebsocketState> = None;

    // Track last prices for deduplication
    let mut last_up_bid: Option<String> = None;
    let mut last_up_ask: Option<String> = None;

    // Ping interval
    let mut ping_interval = tokio::time::interval(Duration::from_secs(PING_INTERVAL_SECS));

    // Loop until cancelled or window ends
    loop {
        tokio::select! {
            // Check for cancellation
            _ = cancel_token.cancelled() => {
                println!("[Polymarket] Cancelled");
                break;
            }

            // Send ping to keep connection alive
            _ = ping_interval.tick() => {
                let ping_msg = serde_json::json!({"type": "ping"});
                if let Err(e) = write.send(Message::Text(ping_msg.to_string().into())).await {
                    return Err(CollectorError::Websocket(e));
                }
            }

            // Receive next message
            msg = read.next() => {
                match msg {
                    None => {
                        return Err(CollectorError::WindowFailed(
                            "Polymarket websocket closed unexpectedly".to_string()
                        ));
                    }

                    Some(Ok(Message::Text(text))) => {
                        // Skip non-price messages (pong, subscription confirmations)
                        if !text.contains("price_change") {
                            continue;
                        }

                        // Parse JSON
                        let parsed: PolymarketWsMessage = match serde_json::from_str(&text) {
                            Ok(p) => p,
                            Err(_) => continue,  // Skip malformed messages
                        };

                        // Parse timestamp
                        let server_ts: i64 = match parsed.timestamp.parse() {
                            Ok(ts) => ts,
                            Err(_) => continue,
                        };

                        // Initialize clock offset on first valid message
                        let ws_state = state.get_or_insert_with(|| {
                            let offset = WebsocketState::new(now_millis(), server_ts);
                            println!("[Polymarket] Connected, clock offset: {}ms", offset.clock_offset);
                            offset
                        });

                        // Check if window ended
                        let normalized_ts = ws_state.normalize(server_ts);
                        if normalized_ts >= window_end_ms {
                            println!("[Polymarket] Window ended");
                            break;
                        }

                        // Extract prices for up and down tokens
                        let mut up_bid: Option<String> = None;
                        let mut up_ask: Option<String> = None;
                        let mut down_bid: Option<String> = None;
                        let mut down_ask: Option<String> = None;
                        let mut side = String::new();
                        let mut size = String::new();

                        for change in &parsed.price_changes {
                            if change.asset_id == up_token_id {
                                up_bid = Some(change.best_bid.clone());
                                up_ask = Some(change.best_ask.clone());
                                side = change.side.clone();
                                size = change.size.clone();
                            } else if change.asset_id == down_token_id {
                                down_bid = Some(change.best_bid.clone());
                                down_ask = Some(change.best_ask.clone());
                            }
                        }

                        // Need at least up token prices
                        let (current_up_bid, current_up_ask) = match (up_bid, up_ask) {
                            (Some(b), Some(a)) => (b, a),
                            _ => continue,
                        };

                        // Deduplicate: skip if prices haven't changed
                        if Some(&current_up_bid) == last_up_bid.as_ref()
                            && Some(&current_up_ask) == last_up_ask.as_ref()
                        {
                            continue;
                        }

                        // Update last prices
                        last_up_bid = Some(current_up_bid.clone());
                        last_up_ask = Some(current_up_ask.clone());

                        // Parse to f64
                        let up_bid_f: f64 = current_up_bid.parse().unwrap_or(0.0);
                        let up_ask_f: f64 = current_up_ask.parse().unwrap_or(0.0);
                        let down_bid_f: f64 = down_bid.as_ref().and_then(|s| s.parse().ok()).unwrap_or(0.0);
                        let down_ask_f: f64 = down_ask.as_ref().and_then(|s| s.parse().ok()).unwrap_or(0.0);
                        let size_f: f64 = size.parse().unwrap_or(0.0);

                        // Create row and add to buffer
                        let row = PriceRow {
                            window_ts,
                            server_ts,
                            normalized_ts,
                            up_bid: up_bid_f,
                            up_ask: up_ask_f,
                            down_bid: down_bid_f,
                            down_ask: down_ask_f,
                            side,
                            size: size_f,
                        };
                        buffer.push(row);
                    }

                    Some(Ok(_)) => {}

                    Some(Err(e)) => {
                        return Err(CollectorError::Websocket(e));
                    }
                }
            }
        }
    }

    println!("[Polymarket] Collected {} rows", buffer.len());
    Ok(buffer)
}