// src/api.rs
use crate::error::{CollectorError, Result};
use crate::models::Event;

// ============================================================================
// Slug Helpers
// ============================================================================

/// Build slug from window timestamp
/// Example: 1773117600 -> "btc-updown-5m-1773117600"
pub fn window_to_slug(window_ts: i64) -> String {
    format!("{}{}", crate::config::MARKET_SLUG_PREFIX, window_ts)
}

//Extract window timestamp from slug
/// Example: "btc-updown-5m-1773117600" -> 1773117600
pub fn slug_to_window(slug: &str) -> Option<i64> {
    slug.strip_prefix(crate::config::MARKET_SLUG_PREFIX)
        .and_then(|ts_str| ts_str.parse::<i64>().ok())
}

// ============================================================================
// API Calls
// ============================================================================

/// Fetch event by window timestamp
/// GET {GAMMA_API_URL}/events?slug=btc-updown-5m-{window_ts}
pub async fn fetch_event(window_ts: i64) -> Result<Event> {
    // TODO: Build URL using window_to_slug()
    let url = format!(
        "{}/events?slug={}",
        crate::config::GAMMA_API_URL,
        window_to_slug(window_ts)
    );
    // TODO: Make GET request with reqwest
    let response = reqwest::get(&url).await?;
    // TODO: Parse response as Vec<Event>
    let events: Vec<Event> = response.json().await?;
    // TODO: Return first event or error if empty
    events
        .into_iter()
        .next()
        .ok_or_else(|| CollectorError::ParseError(format!("Event not found for window: {}", window_ts)))
}

/// Fetch price_to_beat for a completed window
/// Returns None if not yet populated
pub async fn fetch_price_to_beat(window_ts: i64) -> Result<Option<f64>> {
    // TODO: Call fetch_event()
    let event = fetch_event(window_ts).await?;
    // TODO: Extract price_to_beat from event_metadata
    let price = event
        .event_metadata
        .and_then(|meta| meta.price_to_beat);
    Ok(price)
    // TODO: Return None if not present
}

/// Extract token IDs from event
/// Returns (up_token_id, down_token_id)
pub fn extract_token_ids(event: &Event) -> Result<(String, String)> {
    // TODO: Get first market from event.markets
    let market = event
        .markets
        .first()
        .ok_or_else(|| CollectorError::ParseError(format!("No markets found in event: {}", event.id)))?;
    // TODO: Parse market.clob_token_ids as JSON array
    let token_ids: Vec<String> = serde_json::from_str(&market.clob_token_ids)
        .map_err(|_| CollectorError::ParseError(format!("Failed to parse clob_token_ids: {}", market.clob_token_ids)))?;
    // TODO: Return first two IDs
    if token_ids.len() < 2 {
        return Err(CollectorError::ParseError(format!("Not enough tokens in market: {}", market.id)));
    }
    Ok((token_ids[0].clone(), token_ids[1].clone()))
}
