# Crypto Market Intelligence — Autonomous Trading Agent & Backend

Zero-cost, institutional-grade autonomous crypto trading engine and REST API.

## Features
- **8-Rule Strategy Engine:** Trend pullback detection, Bitcoin Master Trend alignment, institutional volume confirmation, candlestick rejection wicks, and dynamic ATR stop management.
- **FastAPI REST API:** Full endpoints for coins, real-time live prices, open positions, closed trade journals, signals, and account equity.
- **24/7 Binance WebSocket Streamer:** Real-time second-by-second trade execution and candle close processing.
- **Cloud-Ready:** Preconfigured Dockerfile and `render.yaml` for 1-click zero-cost deployment.

## Running Locally
```bash
pip install -r requirements.txt
python3 main.py serve
```
API Documentation: `http://localhost:8000/docs`
