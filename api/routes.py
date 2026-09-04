"""
API Routes
==========
Tamam REST endpoints yahan define hain.
Har endpoint sirf database se data read karta hai — koi heavy computation yahan nahi hoti.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

import config
from core.database import (
    get_connection, init_schema,
    get_available_pairs, get_candle_count,
    fetch_candles, get_active_signals, get_recent_signals,
    get_open_positions, update_position_price, get_all_trades, get_trade_stats,
    get_account_balance,
)
from analytics.indicators import calculate_ema, calculate_rsi, calculate_atr
from analytics.market_structure import detect_swing_points, determine_regime
from trading.paper_trader import get_account_state, update_positions_with_price

router = APIRouter(prefix="/api")


# ── System ─────────────────────────────────────────────────────


@router.get("/status")
def system_status():
    """System health check + database statistics."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    pairs = get_available_pairs(conn)

    pair_stats = []
    for sym, tf in pairs:
        count = get_candle_count(conn, sym, tf)
        pair_stats.append({"symbol": sym, "timeframe": tf, "candles": count})

    conn.close()
    return {
        "status": "healthy",
        "pairs_monitored": len(pairs),
        "symbols": config.SYMBOLS,
        "timeframes": config.TIMEFRAMES,
        "data": pair_stats,
    }


# ── Coins ──────────────────────────────────────────────────────

import json
import time
import urllib.parse
import urllib.request

_LIVE_PRICE_CACHE = {"timestamp": 0.0, "data": {}}
CACHE_TTL = 3.0  # seconds cache


def get_live_ticker_prices() -> dict:
    """Fetch instantaneous real-time ticker prices directly from Binance."""
    now = time.time()
    if now - _LIVE_PRICE_CACHE["timestamp"] < CACHE_TTL and _LIVE_PRICE_CACHE["data"]:
        return _LIVE_PRICE_CACHE["data"]

    try:
        symbols_param = json.dumps(config.SYMBOLS, separators=(',', ':'))
        url = f"{config.BINANCE_REST_BASE}/ticker/price?symbols={urllib.parse.quote(symbols_param)}"
        req = urllib.request.Request(url, headers={"User-Agent": "ZeroCostCryptoAgent/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                prices = {item["symbol"]: float(item["price"]) for item in data}
                _LIVE_PRICE_CACHE["timestamp"] = now
                _LIVE_PRICE_CACHE["data"] = prices
                return prices
    except Exception:
        pass
    return _LIVE_PRICE_CACHE.get("data", {})


@router.get("/coins")
def list_coins():
    """All monitored coins with real-time live price."""
    live_prices = get_live_ticker_prices()
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)

    coins = []
    seen = set()
    for symbol in config.SYMBOLS:
        if symbol in seen:
            continue
        seen.add(symbol)

        price = live_prices.get(symbol)
        if price is None:
            candles = fetch_candles(conn, symbol, "1h", limit=1)
            price = candles[-1].close if candles else 0

        coins.append({
            "symbol": symbol,
            "price": price,
        })

    conn.close()
    return {"coins": coins}


@router.get("/coins/{symbol}/analysis")
def coin_analysis(symbol: str):
    """Full analysis for a specific coin (indicators + market structure + live price)."""
    symbol = symbol.upper()
    live_prices = get_live_ticker_prices()
    live_price = live_prices.get(symbol)

    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)

    result = {"symbol": symbol, "timeframes": {}}

    for tf in config.TIMEFRAMES:
        candles = fetch_candles(conn, symbol, tf)
        if len(candles) < config.EMA_SLOW_PERIOD + 10:
            result["timeframes"][tf] = {"error": "Not enough data"}
            continue

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        display_price = live_price if live_price is not None else candles[-1].close

        ema_fast = calculate_ema(closes, config.EMA_FAST_PERIOD)
        ema_slow = calculate_ema(closes, config.EMA_SLOW_PERIOD)
        rsi = calculate_rsi(closes, config.RSI_PERIOD)
        atr = calculate_atr(highs, lows, closes, config.ATR_PERIOD)

        swings = detect_swing_points(candles, lookback=config.SWING_LOOKBACK)
        regime = determine_regime(symbol, tf, swings)

        result["timeframes"][tf] = {
            "price": display_price,
            "ema_fast": ema_fast[-1],
            "ema_slow": ema_slow[-1],
            "rsi": round(rsi[-1], 1) if rsi[-1] else None,
            "atr": round(atr[-1], 2) if atr[-1] else None,
            "atr_pct": round((atr[-1] / display_price) * 100, 2) if atr[-1] and display_price else None,
            "ema_bias": "BULLISH" if display_price > (ema_slow[-1] or 0) else "BEARISH",
            "trend": regime.trend,
            "trend_confidence": regime.confidence,
            "last_swing_high": regime.last_swing_high_price,
            "last_swing_low": regime.last_swing_low_price,
            "structure": [l.label for l in regime.structure_labels[-8:]],
            "candle_count": len(candles),
        }

    conn.close()
    return result


# ── Signals ────────────────────────────────────────────────────


@router.get("/signals")
def list_signals(
    symbol: Optional[str] = Query(None),
    status: str = Query("all"),
):
    """Active and recent signals."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)

    if status == "active":
        signals = get_active_signals(conn, symbol=symbol.upper() if symbol else None)
    else:
        signals = get_recent_signals(conn, limit=20)

    conn.close()
    return {"signals": signals, "count": len(signals)}


@router.get("/signals/{symbol}")
def signals_for_symbol(symbol: str):
    """Signals for a specific coin."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    signals = get_active_signals(conn, symbol=symbol.upper())
    conn.close()
    return {"symbol": symbol.upper(), "signals": signals}


# ── Trades ─────────────────────────────────────────────────────


@router.get("/trades")
def list_trades(limit: int = Query(50)):
    """Paper trading history."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    trades = get_all_trades(conn, limit=limit)
    conn.close()
    return {"trades": trades, "count": len(trades)}


@router.get("/trades/performance")
def trade_performance():
    """Paper trading performance statistics."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    stats = get_trade_stats(conn)
    conn.close()
    return stats


# ── Account ────────────────────────────────────────────────────


@router.get("/account")
def account_info():
    """Paper trading account state with real-time mark-to-market prices."""
    live_prices = get_live_ticker_prices()
    for sym, p in live_prices.items():
        update_positions_with_price(sym, p)

    state = get_account_state()
    return {
        "balance": state.balance,
        "equity": state.equity,
        "total_trades": state.total_trades,
        "closed_trades": state.total_trades,
        "winning_trades": state.winning_trades,
        "losing_trades": state.losing_trades,
        "win_rate": state.win_rate,
        "total_pnl": state.total_pnl,
        "max_drawdown": state.max_drawdown,
        "avg_r_multiple": state.avg_r_multiple,
        "open_positions": state.open_positions,
    }


# ── Positions ──────────────────────────────────────────────────


@router.get("/positions")
def list_positions():
    """Open paper trading positions with real-time live prices."""
    live_prices = get_live_ticker_prices()
    for sym, p in live_prices.items():
        update_positions_with_price(sym, p)

    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    positions = get_open_positions(conn)
    conn.close()
    return {"positions": positions, "count": len(positions)}
