// src/collector.rs

use tokio_util::sync::CancellationToken;
use std::time::Duration;

use crate::api;
use crate::db;
use crate::error::Result;
use crate::models::{EventRow, WindowStatus};
use crate::timing;
use crate::ws::{binance, polymarket};

/// Main collection loop - runs forever
pub async fn run() -> Result<()> {
    println!("[Collector] Starting...");

    // Open database connection
    let mut conn = db::open()?;
    println!("[Collector] Database opened");

    let mut first_run = true;

    loop {
        let window_ts = timing::current_window_ts();
        let seconds_into = timing::seconds_into_window();

        // If we're more than 5 seconds into a window, skip to next
        if seconds_into > 5 {
            let next_window = timing::next_window_ts();
            let wait_secs = timing::seconds_until_window(next_window);
            println!(
                "[Collector] Mid-window ({}s in), waiting {}s for window {}",
                seconds_into, wait_secs, next_window
            );
            tokio::time::sleep(Duration::from_secs(wait_secs as u64)).await;
            tokio::time::sleep(Duration::from_millis(500)).await;
            continue;
        }

        // We're at the start of a window, collect it
        println!("\n[Collector] === Starting window {} ===", window_ts);

        // Spawn backfill task for previous window
        if first_run {
            println!("[Collector] First run, skipping backfill");
            first_run = false;
        } else {
            let previous_window = window_ts - 300;
            tokio::spawn(backfill_price_to_beat(previous_window));
        }

        // Collect the window
        match collect_window(&mut conn, window_ts).await {
            Ok((price_count, btc_count)) => {
                println!(
                    "[Collector] Window {} complete: {} prices, {} btc rows",
                    window_ts, price_count, btc_count
                );
            }
            Err(e) => {
                println!("[Collector] Window {} failed: {}", window_ts, e);
                let _ = db::mark_failed(&conn, window_ts);
            }
        }

        // Small delay before checking next window
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

/// Collect a single window
async fn collect_window(conn: &mut rusqlite::Connection, window_ts: i64) -> Result<(usize, usize)> {
    // Fetch event from API
    let event = api::fetch_event(window_ts).await?;
    let (up_token_id, down_token_id) = api::extract_token_ids(&event)?;

    println!("[Collector] Up token: {}", up_token_id);
    println!("[Collector] Down token: {}", down_token_id);

    // Insert event row
    let event_row = EventRow {
        window_ts,
        price_to_beat: None,
        up_token_id: up_token_id.clone(),
        down_token_id: down_token_id.clone(),
        status: WindowStatus::Collecting,
    };

    // Check if already exists (in case of restart)
    if !db::window_exists(conn, window_ts)? {
        db::insert_event(conn, &event_row)?;
        println!("[Collector] Event row inserted");
    }

    // Calculate window end time in milliseconds
    let window_end_ms = (window_ts + 300) * 1000;

    // Create cancellation token (if one fails, stop the other)
    let cancel_token = CancellationToken::new();

    // Run both websockets concurrently
    let binance_token = cancel_token.clone();
    let polymarket_token = cancel_token.clone();

    let binance_handle = tokio::spawn(async move {
        binance::collect(window_ts, window_end_ms, binance_token).await
    });

    let polymarket_handle = tokio::spawn(async move {
        polymarket::collect(
            window_ts,
            window_end_ms,
            up_token_id,
            down_token_id,
            polymarket_token,
        ).await
    });

    // Wait for both to complete
    let (binance_result, polymarket_result) = tokio::join!(binance_handle, polymarket_handle);

    // Unwrap the JoinHandle results
    let btc_prices = binance_result
        .map_err(|e| crate::error::CollectorError::WindowFailed(format!("Binance task panicked: {}", e)))?
        .map_err(|e| {
            cancel_token.cancel();
            e
        })?;

    let prices = polymarket_result
        .map_err(|e| crate::error::CollectorError::WindowFailed(format!("Polymarket task panicked: {}", e)))?
        .map_err(|e| {
            cancel_token.cancel();
            e
        })?;

    // Write data to database
    let price_count = prices.len();
    let btc_count = btc_prices.len();

    db::write_window_data(conn, &prices, &btc_prices)?;
    db::mark_complete(conn, window_ts)?;

    Ok((price_count, btc_count))
}

/// Backfill price_to_beat for a previous window
async fn backfill_price_to_beat(window_ts: i64) {
    println!("[Backfill] Waiting {}s for window {}...", crate::config::BACKFILL_DELAY_SECS, window_ts);
    tokio::time::sleep(Duration::from_secs(crate::config::BACKFILL_DELAY_SECS)).await;

    match api::fetch_price_to_beat(window_ts).await {
        Ok(Some(price)) => {
            if let Ok(conn) = db::open() {
                if let Err(e) = db::update_price_to_beat(&conn, window_ts, price) {
                    println!("[Backfill] Failed to update DB: {}", e);
                } else {
                    println!("[Backfill] Window {} price_to_beat: {}", window_ts, price);
                }
            }
        }
        Ok(None) => {
            println!("[Backfill] Window {} price_to_beat not available", window_ts);
        }
        Err(e) => {
            println!("[Backfill] Failed to fetch price_to_beat: {}", e);
        }
    }
}