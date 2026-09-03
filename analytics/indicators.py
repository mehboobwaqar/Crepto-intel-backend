"""
Technical Indicators (Pure Math)
================================
Sirf math - koi database call, koi API call, koi side effect nahi.
Input: List of floats → Output: List of floats.
Har function kisi bhi coin aur kisi bhi timeframe par equally kaam karta hai.
"""

from typing import List, Optional


def calculate_ema(values: List[float], period: int) -> List[Optional[float]]:
    """
    Exponential Moving Average (EMA)
    ---------------------------------
    Pehle `period` candles ka SMA as seed, phir exponential smoothing.
    
    Returns: List same length as input. Pehle (period-1) values None hongi.
    
    Formula:
        multiplier = 2 / (period + 1)
        ema[i] = (value[i] - ema[i-1]) * multiplier + ema[i-1]
    """
    if len(values) < period:
        return [None] * len(values)

    result: List[Optional[float]] = [None] * len(values)
    multiplier = 2.0 / (period + 1)

    # Seed: Simple Moving Average of first `period` values
    sma_seed = sum(values[:period]) / period
    result[period - 1] = sma_seed

    # Exponential smoothing
    for i in range(period, len(values)):
        result[i] = (values[i] - result[i - 1]) * multiplier + result[i - 1]

    return result


def calculate_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Relative Strength Index (RSI)
    -----------------------------
    Wilder's smoothing method (industry standard).
    
    Returns: List same length as input. Pehle `period` values None hongi.
    
    Interpretation:
        RSI > 70 → Overbought (potential reversal down)
        RSI < 30 → Oversold  (potential reversal up)
        40-60    → Neutral zone
    """
    if len(closes) < period + 1:
        return [None] * len(closes)

    result: List[Optional[float]] = [None] * len(closes)

    # Step 1: Calculate price changes
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Step 2: Separate gains and losses
    gains = [max(0.0, c) for c in changes]
    losses = [max(0.0, -c) for c in changes]

    # Step 3: Initial average (SMA of first `period` changes)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # First RSI value
    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    # Step 4: Wilder's smoothing for remaining values
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


def calculate_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> List[Optional[float]]:
    """
    Average True Range (ATR)
    ------------------------
    Volatility measure - stop loss aur position sizing ke liye critical.
    
    True Range = max(
        high - low,
        abs(high - previous_close),
        abs(low  - previous_close)
    )
    
    ATR = Wilder's smoothed average of True Range over `period`.
    
    Returns: List same length as input. Pehle `period` values None hongi.
    """
    n = len(closes)
    if n < period + 1:
        return [None] * n

    result: List[Optional[float]] = [None] * n

    # Step 1: Calculate True Range series
    true_ranges: List[float] = [highs[0] - lows[0]]  # First bar: just H-L
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    # Step 2: Initial ATR = SMA of first `period` true ranges
    initial_atr = sum(true_ranges[1 : period + 1]) / period
    result[period] = initial_atr

    # Step 3: Wilder's smoothing
    for i in range(period + 1, n):
        result[i] = (result[i - 1] * (period - 1) + true_ranges[i]) / period

    return result
