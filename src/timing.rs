// src/timing.rs

use crate::config::WINDOW_DURATION_SECS;
use std::time::{SystemTime, UNIX_EPOCH};

/// Get current Unix timestamp in seconds
pub fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

/// Get current Unix timestamp in milliseconds
pub fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

/// Get the window_ts for the currently active window
pub fn current_window_ts() -> i64 {
    let now = now_secs();
    (now / WINDOW_DURATION_SECS as i64) * WINDOW_DURATION_SECS as i64
}

/// Get the window_ts for the next window
pub fn next_window_ts() -> i64 {
    current_window_ts() + WINDOW_DURATION_SECS as i64
}

/// Get the window_ts for the previous window
pub fn previous_window_ts() -> i64 {
    current_window_ts() - WINDOW_DURATION_SECS as i64
}

/// Seconds elapsed since current window started
pub fn seconds_into_window() -> i64 {
    now_secs() - current_window_ts()
}

/// Seconds remaining in the current window
pub fn seconds_remaining_in_window() -> i64 {
    WINDOW_DURATION_SECS as i64 - seconds_into_window()
}

/// Seconds until a specific window starts
pub fn seconds_until_window(window_ts: i64) -> i64 {
    window_ts - now_secs()
}

/// Check if we're in the middle of a window (not on exact boundary)
pub fn is_mid_window() -> bool {
    seconds_into_window() > 0
}