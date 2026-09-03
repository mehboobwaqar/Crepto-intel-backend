"""
Signal Generator — Trend Pullback Strategy
=============================================
Deterministic signal generation engine.
AI nahi — pure rules aur math se trade signals generate karta hai.

Strategy Logic:
1. 4H timeframe se TREND check karo (UPTREND → look for LONG, DOWNTREND → SHORT)
2. 1H timeframe par pullback zone check karo (price near EMA20)
3. RSI confirmation (not overbought for LONG, not oversold for SHORT)
4. ATR filter (market alive hai, dead nahi)
5. Stop Loss = last swing low/high + ATR buffer
6. Position size = fixed 0.5% account risk
"""

from datetime import datetime, timezone
from typing import List, Optional
import uuid

import config
from core.models import Signal, Candle, IndicatorSnapshot, MarketRegime
from core.database import (
    get_connection, init_schema, fetch_candles, get_available_pairs,
    save_signal, get_active_signals, get_open_positions, get_account_balance,
    save_indicator_batch,
)
from analytics.indicators import calculate_ema, calculate_rsi, calculate_atr
from analytics.market_structure import detect_swing_points, determine_regime
from strategy.risk_manager import (
    calculate_position_size, calculate_take_profits,
    calculate_stop_loss, validate_risk,
)


def _compute_latest_indicators(candles: List[Candle]) -> dict:
    """Candle list se latest indicator values extract karta hai."""
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    ema_fast = calculate_ema(closes, config.EMA_FAST_PERIOD)
    ema_slow = calculate_ema(closes, config.EMA_SLOW_PERIOD)
    rsi = calculate_rsi(closes, config.RSI_PERIOD)
    atr = calculate_atr(highs, lows, closes, config.ATR_PERIOD)

    return {
        "close": closes[-1] if closes else None,
        "ema_fast": ema_fast[-1] if ema_fast else None,
        "ema_slow": ema_slow[-1] if ema_slow else None,
        "rsi": rsi[-1] if rsi else None,
        "atr": atr[-1] if atr else None,
    }


def _check_pullback_zone(close: float, ema_fast: float, direction: str) -> bool:
    """
    Price EMA20 ke paas hai ya nahi (pullback zone).
    Tolerance: config.EMA_PULLBACK_TOLERANCE_PCT (default 0.5%)
    """
    if ema_fast is None or ema_fast == 0:
        return False

    distance_pct = abs(close - ema_fast) / ema_fast * 100

    if direction == "LONG":
        # Price EMA20 ke thoda upar ya thoda neeche (pulling back)
        return distance_pct <= config.EMA_PULLBACK_TOLERANCE_PCT and close >= ema_fast * 0.995
    else:
        # SHORT: Price EMA20 ke thoda neeche ya thoda upar
        return distance_pct <= config.EMA_PULLBACK_TOLERANCE_PCT and close <= ema_fast * 1.005


def _check_rsi_zone(rsi: float, direction: str) -> bool:
    """RSI pullback zone check karta hai."""
    if rsi is None:
        return False

    if direction == "LONG":
        return config.RSI_LONG_PULLBACK_LOW <= rsi <= config.RSI_LONG_PULLBACK_HIGH
    else:
        return config.RSI_SHORT_PULLBACK_LOW <= rsi <= config.RSI_SHORT_PULLBACK_HIGH


def _check_atr_filter(atr: float, close: float) -> bool:
    """ATR minimum filter — dead/choppy market filter out."""
    if atr is None or close == 0:
        return False
    atr_pct = (atr / close) * 100
    return atr_pct >= config.ATR_MIN_PCT


def _check_candlestick_rejection(candles: List[Candle], direction: str) -> bool:
    """
    Price Action Candlestick Rejection Filter:
    Check karta hai ke EMA20 par rejection wick bani hai ya nahi,
    taake falling knife ya pump breakout ke aage galat entry na ho.
    """
    if not candles:
        return False
    last = candles[-1]
    candle_range = last.high - last.low
    if candle_range <= 0:
        return True

    if direction == "LONG":
        # Buyers defended EMA20: Lower wick >= 15% of range OR candle closed green
        lower_wick = min(last.open, last.close) - last.low
        has_wick = (lower_wick / candle_range) >= 0.15
        is_green = last.close >= last.open
        return has_wick or is_green
    else:  # SHORT
        # Sellers rejected EMA20: Upper wick >= 15% of range OR candle closed red
        upper_wick = last.high - max(last.open, last.close)
        has_wick = (upper_wick / candle_range) >= 0.15
        is_red = last.close <= last.open
        return has_wick or is_red


def _check_btc_alignment(conn, direction: str, symbol: str) -> tuple:
    """
    Bitcoin Trend Alignment Filter (The King Filter):
    Altcoins (ETH, SOL, LINK) Bitcoin ke sath highly correlated hain.
    Agar altcoin SHORT signal de raha ho lekin Bitcoin UPTREND mein ho,
    toh signal REJECT — kyunki Bitcoin ka pump altcoin ko zabardasti
    upar kheench lega (jaisa LINK ke sath hua).

    Returns: (is_aligned: bool, btc_trend: str, btc_confidence: str)
    """
    if symbol == "BTCUSDT":
        return True, "SELF", "HIGH"

    candles_btc_4h = fetch_candles(conn, "BTCUSDT", config.TREND_TIMEFRAME)
    if len(candles_btc_4h) < config.EMA_SLOW_PERIOD + 20:
        return False, "INSUFFICIENT_DATA", "LOW"

    swings_btc = detect_swing_points(candles_btc_4h, lookback=config.SWING_LOOKBACK)
    regime_btc = determine_regime("BTCUSDT", config.TREND_TIMEFRAME, swings_btc)

    btc_trend = regime_btc.trend
    btc_conf = regime_btc.confidence

    # Rule: Altcoin direction MUST match Bitcoin's trend
    if direction == "LONG" and btc_trend == "DOWNTREND":
        return False, btc_trend, btc_conf
    if direction == "SHORT" and btc_trend == "UPTREND":
        return False, btc_trend, btc_conf

    return True, btc_trend, btc_conf


def _check_volume_confirmation(candles: List[Candle], lookback: int = 20) -> tuple:
    """
    Volume Confirmation Filter (Institutional Money Check):
    Pullback candle ka volume pichli 20 candles ke average volume ke
    barabar ya usse zyada hona chahiye taake dead/fakeout entry na ho.

    Returns: (is_confirmed: bool, vol_ratio: float)
    """
    if len(candles) < lookback + 1:
        return True, 1.0

    recent_volumes = [c.volume for c in candles[-(lookback + 1):-1]]
    avg_vol = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0

    if avg_vol <= 0:
        return True, 1.0

    current_vol = candles[-1].volume
    vol_ratio = current_vol / avg_vol

    # Volume at least 75% of average to confirm active market interest
    return vol_ratio >= 0.75, round(vol_ratio, 2)


def generate_signals_for_symbol(symbol: str) -> List[Signal]:
    """
    Ek symbol ke liye complete signal generation pipeline:
    1. 4H trend check
    2. 1H entry conditions check
    3. Risk management calculations
    4. Signal generate (ya NO_SIGNAL)
    """
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    signals = []

    # ── Step 1: Get 4H trend (higher timeframe filter) ────────
    candles_4h = fetch_candles(conn, symbol, config.TREND_TIMEFRAME)
    if len(candles_4h) < config.EMA_SLOW_PERIOD + 20:
        conn.close()
        return signals

    swings_4h = detect_swing_points(candles_4h, lookback=config.SWING_LOOKBACK)
    regime_4h = determine_regime(symbol, config.TREND_TIMEFRAME, swings_4h)

    # Determine allowed direction from 4H trend
    if regime_4h.trend == "UPTREND":
        allowed_direction = "LONG"
    elif regime_4h.trend == "DOWNTREND":
        allowed_direction = "SHORT"
    else:
        # RANGING or INSUFFICIENT_DATA → no signal
        conn.close()
        return signals

    # ── Step 1.5: Bitcoin Trend Alignment (The King Filter) ───
    # Altcoins cannot trade against Bitcoin's higher timeframe trend!
    btc_aligned, btc_trend, btc_conf = _check_btc_alignment(conn, allowed_direction, symbol)
    if not btc_aligned:
        print(f"  ⛔ [{symbol}] Signal BLOCKED by BTC filter: {allowed_direction} vs BTC {btc_trend} ({btc_conf})")
        conn.close()
        return signals

    # ── Step 2: Get 1H entry data ─────────────────────────────
    candles_1h = fetch_candles(conn, symbol, config.ENTRY_TIMEFRAME)
    if len(candles_1h) < config.EMA_SLOW_PERIOD + 20:
        conn.close()
        return signals

    indicators = _compute_latest_indicators(candles_1h)
    close = indicators["close"]
    ema_fast = indicators["ema_fast"]
    ema_slow = indicators["ema_slow"]
    rsi = indicators["rsi"]
    atr = indicators["atr"]

    # ── Step 3: Check all entry conditions ────────────────────

    # Guard: all indicators must be present
    if any(v is None for v in [close, ema_fast, ema_slow, rsi, atr]):
        conn.close()
        return signals

    # Condition 1: EMA alignment
    if allowed_direction == "LONG" and close < ema_slow:
        conn.close()
        return signals
    if allowed_direction == "SHORT" and close > ema_slow:
        conn.close()
        return signals

    # Condition 2: Pullback zone
    if not _check_pullback_zone(close, ema_fast, allowed_direction):
        conn.close()
        return signals

    # Condition 3: RSI zone
    if not _check_rsi_zone(rsi, allowed_direction):
        conn.close()
        return signals

    # Condition 4: ATR filter (market not dead)
    if not _check_atr_filter(atr, close):
        conn.close()
        return signals

    # Condition 5: Price Action Rejection (no falling knives / fakeout bars)
    if not _check_candlestick_rejection(candles_1h, allowed_direction):
        conn.close()
        return signals

    # Condition 6: Volume Confirmation (Institutional participation)
    vol_confirmed, vol_ratio = _check_volume_confirmation(candles_1h)
    if not vol_confirmed:
        print(f"  ⛔ [{symbol}] Signal BLOCKED: Volume too low ({vol_ratio}x avg)")
        conn.close()
        return signals

    # Condition 7: No duplicate — check if active signal already exists
    existing = get_active_signals(conn, symbol=symbol)
    if existing:
        conn.close()
        return signals

    # Condition 8: Max positions & duplicate position check
    open_positions = get_open_positions(conn)
    if len(open_positions) >= config.MAX_OPEN_POSITIONS:
        conn.close()
        return signals

    if any(p["symbol"] == symbol for p in open_positions):
        conn.close()
        return signals

    # ── Step 4: Calculate risk management ─────────────────────

    # Swing points from 1H for stop loss
    swings_1h = detect_swing_points(candles_1h, lookback=config.SWING_LOOKBACK)
    swing_highs = [s for s in swings_1h if s.swing_type == "SWING_HIGH"]
    swing_lows = [s for s in swings_1h if s.swing_type == "SWING_LOW"]

    last_swing_high = swing_highs[-1].price if swing_highs else None
    last_swing_low = swing_lows[-1].price if swing_lows else None

    stop_loss = calculate_stop_loss(
        direction=allowed_direction,
        last_swing_low=last_swing_low,
        last_swing_high=last_swing_high,
        atr_value=atr,
        current_price=close,
    )

    if stop_loss is None:
        conn.close()
        return signals

    # Validate risk
    if not validate_risk(close, stop_loss, allowed_direction, atr):
        conn.close()
        return signals

    # Take profit levels
    tp = calculate_take_profits(close, stop_loss, allowed_direction)

    # Position sizing
    balance = get_account_balance(conn, config.PAPER_INITIAL_BALANCE)
    sizing = calculate_position_size(balance, close, stop_loss)

    if sizing["quantity"] <= 0:
        conn.close()
        return signals

    # ── Step 5: Build signal ──────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    signal_id = f"SIG-{symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # Build reason string
    btc_tag = f"BTC: {btc_trend}" if symbol != "BTCUSDT" else "BTC Leader"
    reason = (
        f"{allowed_direction} Trend Pullback | "
        f"4H: {regime_4h.trend} ({regime_4h.confidence}) | {btc_tag} | "
        f"Price ${close:,.2f} near EMA20 ${ema_fast:,.2f} | "
        f"RSI: {rsi:.1f} | ATR: ${atr:,.2f} | Vol: {vol_ratio}x avg"
    )

    # Determine confidence (boosted when coin + BTC are both aligned)
    base_conf = regime_4h.confidence
    if base_conf == "HIGH" and btc_conf in ("HIGH", "MEDIUM"):
        sig_confidence = "HIGH"
    elif base_conf in ("HIGH", "MEDIUM"):
        sig_confidence = "MEDIUM"
    else:
        sig_confidence = "LOW"

    # Downgrade confidence if BTC is RANGING
    if btc_trend == "RANGING" and sig_confidence == "HIGH":
        sig_confidence = "MEDIUM"

    signal = Signal(
        signal_id=signal_id,
        symbol=symbol,
        direction=allowed_direction,
        entry_price=close,
        stop_loss=stop_loss,
        take_profit_1=tp["take_profit_1"],
        take_profit_2=tp["take_profit_2"],
        position_size=sizing["quantity"],
        risk_amount=sizing["risk_amount"],
        risk_reward_ratio=tp["risk_reward_1"],
        confidence=sig_confidence,
        reason=reason,
        status="ACTIVE",
        created_at=now,
        timeframe=config.ENTRY_TIMEFRAME,
        trend_tf=config.TREND_TIMEFRAME,
    )

    save_signal(conn, signal)
    signals.append(signal)
    conn.close()
    return signals


def scan_all_symbols() -> List[Signal]:
    """Config mein defined tamam symbols par signal scan karta hai."""
    all_signals = []
    for symbol in config.SYMBOLS:
        signals = generate_signals_for_symbol(symbol)
        all_signals.extend(signals)
    return all_signals
