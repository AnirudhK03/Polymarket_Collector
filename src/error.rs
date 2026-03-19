// src/error.rs

use std::fmt;

#[derive(Debug)]
pub enum CollectorError {
    Api(reqwest::Error),
    Websocket(tokio_tungstenite::tungstenite::Error),
    Database(rusqlite::Error),
    Json(serde_json::Error),
    WindowFailed(String),
    ParseError(String),
}

impl fmt::Display for CollectorError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CollectorError::Api(e) => write!(f, "API error: {}", e),
            CollectorError::Websocket(e) => write!(f, "Websocket error: {}", e),
            CollectorError::Database(e) => write!(f, "Database error: {}", e),
            CollectorError::Json(e) => write!(f, "JSON parse error: {}", e),
            CollectorError::WindowFailed(msg) => write!(f, "Window failed: {}", msg),
            CollectorError::ParseError(msg) => write!(f, "Parse error: {}", msg),
        }
    }
}

impl std::error::Error for CollectorError {}

// From implementations for easy ? operator usage

impl From<reqwest::Error> for CollectorError {
    fn from(e: reqwest::Error) -> Self {
        CollectorError::Api(e)
    }
}

impl From<tokio_tungstenite::tungstenite::Error> for CollectorError {
    fn from(e: tokio_tungstenite::tungstenite::Error) -> Self {
        CollectorError::Websocket(e)
    }
}

impl From<rusqlite::Error> for CollectorError {
    fn from(e: rusqlite::Error) -> Self {
        CollectorError::Database(e)
    }
}

impl From<serde_json::Error> for CollectorError {
    fn from(e: serde_json::Error) -> Self {
        CollectorError::Json(e)
    }
}

pub type Result<T> = std::result::Result<T, CollectorError>;