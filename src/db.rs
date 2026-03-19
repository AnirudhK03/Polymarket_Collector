// src/db.rs

use rusqlite::{Connection, params};
use crate::error::Result;
use crate::models::{BtcPriceRow, EventRow, PriceRow, WindowStatus};
use crate::config::DEFAULT_DB_PATH;

/// Open database connection and create tables if needed
pub fn open() -> Result<Connection> {
    let path = std::env::var("DB_PATH").unwrap_or_else(|_| DEFAULT_DB_PATH.to_string());
    let conn = Connection::open(&path)?;
    create_tables(&conn)?;
    Ok(conn)
}

/// Create all tables if they don't exist
fn create_tables(conn: &Connection) -> Result<()> {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_ts INTEGER NOT NULL UNIQUE,
            price_to_beat REAL,
            up_token_id TEXT NOT NULL,
            down_token_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'collecting'
        )",
        [],
    )?;

    conn.execute(
        "CREATE TABLE IF NOT EXISTS price_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_ts INTEGER NOT NULL,
            server_ts INTEGER NOT NULL,
            normalized_ts INTEGER NOT NULL,
            up_bid REAL NOT NULL,
            up_ask REAL NOT NULL,
            down_bid REAL NOT NULL,
            down_ask REAL NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL
        )",
        [],
    )?;

    conn.execute(
        "CREATE TABLE IF NOT EXISTS btc_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_ts INTEGER NOT NULL,
            server_ts INTEGER NOT NULL,
            normalized_ts INTEGER NOT NULL,
            mark_price REAL NOT NULL,
            index_price REAL NOT NULL
        )",
        [],
    )?;

    // Create indexes
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_window ON price_changes(window_ts)",
        [],
    )?;
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_btc_window ON btc_prices(window_ts)",
        [],
    )?;

    Ok(())
}

/// Insert a new event row when window collection starts
pub fn insert_event(conn: &Connection, event: &EventRow) -> Result<()> {
    conn.execute(
        "INSERT INTO events (window_ts, price_to_beat, up_token_id, down_token_id, status)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![
            event.window_ts,
            event.price_to_beat,
            event.up_token_id,
            event.down_token_id,
            event.status.as_str(),
        ],
    )?;
    Ok(())
}

/// Write all collected data for a window in a single transaction
pub fn write_window_data(
    conn: &mut Connection,
    prices: &[PriceRow],
    btc_prices: &[BtcPriceRow],
) -> Result<()> {
    let tx = conn.transaction()?;

    // Insert price changes
    for row in prices {
        tx.execute(
            "INSERT INTO price_changes 
             (window_ts, server_ts, normalized_ts, up_bid, up_ask, down_bid, down_ask, side, size)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                row.window_ts,
                row.server_ts,
                row.normalized_ts,
                row.up_bid,
                row.up_ask,
                row.down_bid,
                row.down_ask,
                row.side,
                row.size,
            ],
        )?;
    }

    // Insert BTC prices
    for row in btc_prices {
        tx.execute(
            "INSERT INTO btc_prices 
             (window_ts, server_ts, normalized_ts, mark_price, index_price)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                row.window_ts,
                row.server_ts,
                row.normalized_ts,
                row.mark_price,
                row.index_price,
            ],
        )?;
    }

    tx.commit()?;
    Ok(())
}

/// Update price_to_beat for a window (called by backfill task)
pub fn update_price_to_beat(conn: &Connection, window_ts: i64, price: f64) -> Result<()> {
    conn.execute(
        "UPDATE events SET price_to_beat = ?1 WHERE window_ts = ?2",
        params![price, window_ts],
    )?;
    Ok(())
}

/// Mark window as complete
pub fn mark_complete(conn: &Connection, window_ts: i64) -> Result<()> {
    conn.execute(
        "UPDATE events SET status = ?1 WHERE window_ts = ?2",
        params![WindowStatus::Complete.as_str(), window_ts],
    )?;
    Ok(())
}

/// Mark window as failed
pub fn mark_failed(conn: &Connection, window_ts: i64) -> Result<()> {
    conn.execute(
        "UPDATE events SET status = ?1 WHERE window_ts = ?2",
        params![WindowStatus::Failed.as_str(), window_ts],
    )?;
    Ok(())
}

/// Check if a window already exists in the database
pub fn window_exists(conn: &Connection, window_ts: i64) -> Result<bool> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM events WHERE window_ts = ?1",
        params![window_ts],
        |row| row.get(0),
    )?;
    Ok(count > 0)
}