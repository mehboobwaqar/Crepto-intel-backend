"""
Market Structure Detection
===========================
Chart par Swing Highs / Swing Lows dhundhta hai,
phir Higher Highs (HH), Higher Lows (HL), Lower Highs (LH), Lower Lows (LL) classify karta hai,
aur final verdict deta hai: UPTREND / DOWNTREND / RANGING.

Yeh wohi framework hai jo professional price-action traders use karte hain.
"""

from typing import List, Optional, Tuple
from core.models import Candle, SwingPoint, StructureLabel, MarketRegime


def detect_swing_points(
    candles: List[Candle],
    lookback: int = 5,
) -> List[SwingPoint]:
    """
    Pivot / Swing Point Detection
    -----------------------------
    Ek bar SWING_HIGH hai agar:
        Uski HIGH, uske left ke `lookback` bars AUR right ke `lookback` bars
        ki sab se zyada HIGH se bari ya barabar ho.
    
    Ek bar SWING_LOW hai agar:
        Uski LOW, uske left ke `lookback` bars AUR right ke `lookback` bars
        ki sab se choti LOW se choti ya barabar ho.
    
    Note: Ek bar DONO ho sakta hai (rare lekin mumkin).
    """
    if len(candles) < (2 * lookback + 1):
        return []

    swing_points: List[SwingPoint] = []

    for i in range(lookback, len(candles) - lookback):
        current = candles[i]

        # Left & Right window ki highs aur lows collect karo
        left_highs = [candles[j].high for j in range(i - lookback, i)]
        right_highs = [candles[j].high for j in range(i + 1, i + lookback + 1)]
        left_lows = [candles[j].low for j in range(i - lookback, i)]
        right_lows = [candles[j].low for j in range(i + 1, i + lookback + 1)]

        # Swing High check
        if current.high >= max(left_highs) and current.high >= max(right_highs):
            swing_points.append(
                SwingPoint(
                    symbol=current.symbol,
                    timeframe=current.timeframe,
                    open_time=current.open_time,
                    price=current.high,
                    swing_type="SWING_HIGH",
                    bar_index=i,
                )
            )

        # Swing Low check
        if current.low <= min(left_lows) and current.low <= min(right_lows):
            swing_points.append(
                SwingPoint(
                    symbol=current.symbol,
                    timeframe=current.timeframe,
                    open_time=current.open_time,
                    price=current.low,
                    swing_type="SWING_LOW",
                    bar_index=i,
                )
            )

    return swing_points


def classify_structure(
    swing_points: List[SwingPoint],
) -> List[StructureLabel]:
    """
    HH / HL / LH / LL Classification
    ---------------------------------
    Consecutive swing highs ko compare karta hai:
        Current High > Previous High → HH (Higher High)
        Current High < Previous High → LH (Lower High)
    
    Consecutive swing lows ko compare karta hai:
        Current Low > Previous Low  → HL (Higher Low)
        Current Low < Previous Low  → LL (Lower Low)
    """
    labels: List[StructureLabel] = []

    # Separate highs and lows into their own sequences
    highs = [sp for sp in swing_points if sp.swing_type == "SWING_HIGH"]
    lows = [sp for sp in swing_points if sp.swing_type == "SWING_LOW"]

    # Classify consecutive highs
    for i in range(1, len(highs)):
        prev, curr = highs[i - 1], highs[i]
        if curr.price > prev.price:
            label = "HH"
        elif curr.price < prev.price:
            label = "LH"
        else:
            label = "EH"  # Equal High (rare)
        labels.append(
            StructureLabel(
                open_time=curr.open_time,
                price=curr.price,
                label=label,
                swing_type="SWING_HIGH",
            )
        )

    # Classify consecutive lows
    for i in range(1, len(lows)):
        prev, curr = lows[i - 1], lows[i]
        if curr.price > prev.price:
            label = "HL"
        elif curr.price < prev.price:
            label = "LL"
        else:
            label = "EL"  # Equal Low (rare)
        labels.append(
            StructureLabel(
                open_time=curr.open_time,
                price=curr.price,
                label=label,
                swing_type="SWING_LOW",
            )
        )

    # Sort by time so labels are in chronological order
    labels.sort(key=lambda x: x.open_time)
    return labels


def determine_regime(
    symbol: str,
    timeframe: str,
    swing_points: List[SwingPoint],
    recent_count: int = 6,
) -> MarketRegime:
    """
    Market Regime Detection
    -----------------------
    Pichle `recent_count` structure labels ko dekh ke decide karta hai:
    
        UPTREND:   Majority HH + HL → Market consistently higher move kar raha hai
        DOWNTREND: Majority LH + LL → Market consistently lower move kar raha hai
        RANGING:   Mixed signals → No clear direction
    
    Confidence levels:
        HIGH:   >= 80% labels ek direction mein
        MEDIUM: >= 60% labels ek direction mein
        LOW:    < 60% (confused market)
    """
    labels = classify_structure(swing_points)

    if len(labels) < 3:
        return MarketRegime(
            symbol=symbol,
            timeframe=timeframe,
            trend="INSUFFICIENT_DATA",
            structure_labels=labels,
            confidence="LOW",
        )

    # Sirf recent labels dekho
    recent = labels[-recent_count:] if len(labels) >= recent_count else labels
    total = len(recent)

    bullish_count = sum(1 for l in recent if l.label in ("HH", "HL"))
    bearish_count = sum(1 for l in recent if l.label in ("LH", "LL"))

    bullish_pct = bullish_count / total
    bearish_pct = bearish_count / total

    # Determine trend
    if bullish_pct >= 0.6:
        trend = "UPTREND"
    elif bearish_pct >= 0.6:
        trend = "DOWNTREND"
    else:
        trend = "RANGING"

    # Determine confidence
    dominant_pct = max(bullish_pct, bearish_pct)
    if dominant_pct >= 0.8:
        confidence = "HIGH"
    elif dominant_pct >= 0.6:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Last swing high and low prices
    highs = [sp for sp in swing_points if sp.swing_type == "SWING_HIGH"]
    lows = [sp for sp in swing_points if sp.swing_type == "SWING_LOW"]

    return MarketRegime(
        symbol=symbol,
        timeframe=timeframe,
        trend=trend,
        structure_labels=labels,
        last_swing_high_price=highs[-1].price if highs else None,
        last_swing_low_price=lows[-1].price if lows else None,
        confidence=confidence,
    )
