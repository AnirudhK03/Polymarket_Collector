// src/ws/binance.rs

use futures_util::StreamExt;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tokio_util::sync::CancellationToken;

use crate::config::BINANCE_WS_URL;
use crate::error::{CollectorError, Result};
use crate::models::{BinanceWsMessage, BtcPriceRow, WebsocketState};
use crate::timing::now_millis;

/// Connect to Binance and collect BTC prices until cancelled or window ends
pub async fn collect(
    window_ts: i64,
    window_end_ms: i64,
    cancel_token: CancellationToken,
) -> Result<Vec<BtcPriceRow>> {
    // Connect to websocket
    let (ws_stream, _) = connect_async(BINANCE_WS_URL).await?;
    let (_, mut read) = ws_stream.split();

    // Initialize buffer
    let mut buffer: Vec<BtcPriceRow> = Vec::new();

    // Will be set on first message
    let mut state: Option<WebsocketState> = None;

    // Loop until cancelled or window ends
    loop {
        tokio::select! {
            // Check for cancellation (other websocket failed)
            _ = cancel_token.cancelled() => {
                println!("[Binance] Cancelled");
                break;
            }

            // Receive next message
            msg = read.next() => {
                match msg {
                    // Stream ended
                    None => {
                        return Err(CollectorError::WindowFailed(
                            "Binance websocket closed unexpectedly".to_string()
                        ));
                    }

                    // Got a message
                    Some(Ok(Message::Text(text))) => {
                        // Parse JSON
                        let parsed: BinanceWsMessage = serde_json::from_str(&text)?;

                        // Initialize clock offset on first message
                        let ws_state = state.get_or_insert_with(|| {
                            let offset = WebsocketState::new(now_millis(), parsed.event_time);
                            println!("[Binance] Connected, clock offset: {}ms", offset.clock_offset);
                            offset
                        });

                        // Check if window ended
                        let normalized_ts = ws_state.normalize(parsed.event_time);
                        if normalized_ts >= window_end_ms {
                            println!("[Binance] Window ended");
                            break;
                        }

                        // Parse prices (they come as strings)
                        let mark_price: f64 = parsed.mark_price.parse()
                            .map_err(|_| CollectorError::ParseError(
                                format!("Invalid mark_price: {}", parsed.mark_price)
                            ))?;

                        let index_price: f64 = parsed.index_price.parse()
                            .map_err(|_| CollectorError::ParseError(
                                format!("Invalid index_price: {}", parsed.index_price)
                            ))?;

                        // Create row and add to buffer
                        let row = BtcPriceRow {
                            window_ts,
                            server_ts: parsed.event_time,
                            normalized_ts,
                            mark_price,
                            index_price,
                        };
                        buffer.push(row);
                    }

                    // Ping/pong handled automatically by tungstenite
                    Some(Ok(_)) => {}

                    // Websocket error
                    Some(Err(e)) => {
                        return Err(CollectorError::Websocket(e));
                    }
                }
            }
        }
    }

    println!("[Binance] Collected {} rows", buffer.len());
    Ok(buffer)
}