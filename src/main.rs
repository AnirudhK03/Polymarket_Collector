// src/main.rs

mod config;
mod models;
mod error;
mod timing;
mod api;
mod ws;
mod db;
mod collector;

#[tokio::main]
async fn main() {
    println!("=== Polymarket Collector ===\n");

    if let Err(e) = collector::run().await {
        println!("[Error] Collector stopped: {}", e);
    }
}