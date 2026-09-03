"""
Binance WebSocket Live Streamer
================================
Real-time candle stream from Binance Public WebSocket.
Har candle close hone par:
  1. Database mein upsert karega
  2. Analytics re-compute karega
  3. Signal scan trigger karega
  4. Open positions update karega

Auto-reconnect with exponential backoff on disconnect.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import List

import websockets

import config
from core.database import get_connection, init_schema, upsert_candles, fetch_candles
from core.models import IndicatorSnapshot
from analytics.indicators import calculate_ema, calculate_rsi, calculate_atr
from analytics.market_structure import detect_swing_points, determine_regime
from core.database import save_indicator_batch, save_swing_points
from strategy.signal_generator import generate_signals_for_symbol
from trading.paper_trader import execute_signal, update_positions_with_price


def _build_stream_url(symbols: List[str], timeframes: List[str]) -> str:
    """
    Binance combined stream URL banata hai.
    Format: wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1h/ethusdt@kline_1h/...
    """
    streams = []
    for sym in symbols:
        for tf in timeframes:
            stream_name = f"{sym.lower()}@kline_{tf}"
            streams.append(stream_name)
    return f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"


def _on_candle_close(symbol: str, timeframe: str, kline_data: dict) -> None:
    """
    Jab candle close ho jaye tab yeh function chalta hai.
    1. Database mein save
    2. Indicators re-compute (last 200 candles)
    3. Signal scan
    4. Position update
    """
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)

    # Save the closed candle
    raw_kline = [[
        kline_data["t"],   # open_time
        kline_data["o"],   # open
        kline_data["h"],   # high
        kline_data["l"],   # low
        kline_data["c"],   # close
        kline_data["v"],   # volume
        kline_data["T"],   # close_time
        kline_data["q"],   # quote_volume
        kline_data["n"],   # trades
        kline_data["V"],   # taker_buy_base_volume
        kline_data["Q"],   # taker_buy_quote_volume
    ]]
    upsert_candles(conn, symbol, timeframe, raw_kline)

    close_price = float(kline_data["c"])
    close_time = datetime.fromtimestamp(
        int(kline_data["T"]) / 1000, tz=timezone.utc
    ).strftime("%H:%M UTC")

    print(f"  📊 [{symbol}] [{timeframe}] Candle closed @ ${close_price:,.2f} ({close_time})")

    # Re-compute indicators (last 200 candles is enough for EMA50 warmup)
    candles = fetch_candles(conn, symbol, timeframe, limit=200)
    if len(candles) >= config.EMA_SLOW_PERIOD + 10:
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        ema_fast = calculate_ema(closes, config.EMA_FAST_PERIOD)
        ema_slow = calculate_ema(closes, config.EMA_SLOW_PERIOD)
        rsi_vals = calculate_rsi(closes, config.RSI_PERIOD)
        atr_vals = calculate_atr(highs, lows, closes, config.ATR_PERIOD)

        # Save latest indicators
        snapshots = []
        for i, candle in enumerate(candles):
            snapshots.append(IndicatorSnapshot(
                symbol=symbol, timeframe=timeframe,
                open_time=candle.open_time, close=candle.close,
                ema_fast=ema_fast[i], ema_slow=ema_slow[i],
                rsi=rsi_vals[i], atr=atr_vals[i],
            ))
        save_indicator_batch(conn, snapshots)

        # Update swing points
        swings = detect_swing_points(candles, lookback=config.SWING_LOOKBACK)
        save_swing_points(conn, symbol, timeframe, swings)

    conn.close()

    # Signal scan (only on entry timeframe candle close)
    if timeframe == config.ENTRY_TIMEFRAME:
        signals = generate_signals_for_symbol(symbol)
        for sig in signals:
            print(f"  🔔 NEW SIGNAL: {sig.direction} {sig.symbol} @ ${sig.entry_price:,.2f}")
            print(f"     SL: ${sig.stop_loss:,.2f} | TP1: ${sig.take_profit_1:,.2f} | TP2: ${sig.take_profit_2:,.2f}")
            print(f"     Reason: {sig.reason}")
            # Auto-execute in paper trading
            execute_signal(sig)

    # Update open positions with latest price
    update_positions_with_price(symbol, close_price)


async def _stream_loop():
    """Main WebSocket connection loop with auto-reconnect."""
    url = _build_stream_url(config.SYMBOLS, config.TIMEFRAMES)
    reconnect_delay = config.WS_RECONNECT_DELAY

    print(f"\n{'='*60}")
    print("  LIVE WEBSOCKET STREAMER")
    print(f"  Symbols: {config.SYMBOLS}")
    print(f"  Timeframes: {config.TIMEFRAMES}")
    print(f"  Binance WS: Connected")
    print(f"{'='*60}\n")

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                reconnect_delay = config.WS_RECONNECT_DELAY  # Reset on success
                print(f"  ✓ WebSocket connected. Listening for candle closes...")

                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                        data = msg.get("data", {})

                        if "e" in data and data["e"] == "kline":
                            kline = data["k"]
                            symbol = data["s"]           # e.g. "BTCUSDT"
                            timeframe = kline["i"]       # e.g. "1h"
                            is_closed = kline["x"]       # True if candle is closed
                            current_tick_price = float(kline["c"])

                            # Real-time SL / TP / Break-Even check on every incoming tick
                            update_positions_with_price(symbol, current_tick_price)

                            if is_closed:
                                _on_candle_close(symbol, timeframe, kline)

                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"  [WARN] Parse error: {e}")
                        continue

        except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
            print(f"\n  [DISCONNECT] WebSocket lost: {e}")
            print(f"  Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, config.WS_MAX_RECONNECT_DELAY)

        except Exception as e:
            print(f"\n  [ERROR] Unexpected: {e}")
            print(f"  Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, config.WS_MAX_RECONNECT_DELAY)


def start_streaming():
    """WebSocket streamer start karta hai (blocking)."""
    try:
        asyncio.run(_stream_loop())
    except KeyboardInterrupt:
        print("\n\n  WebSocket streamer stopped by user (Ctrl+C).\n")
