"""
Risk Manager
=============
Position sizing aur risk calculations.
Fixed fractional risk model: har trade mein account ka sirf 0.5% risk hoga.
AI ya guesswork se nahi — pure math se position size calculate hota hai.
"""

from typing import Optional
import config


def calculate_position_size(
    account_balance: float,
    entry_price: float,
    stop_loss_price: float,
    risk_pct: float = None,
) -> dict:
    """
    Position size calculate karta hai fixed fractional risk model se.
    
    Formula:
        risk_amount = account_balance × risk_pct
        stop_distance = abs(entry_price - stop_loss_price)
        quantity = risk_amount / stop_distance
    
    Returns dict with: quantity, risk_amount, stop_distance, risk_pct
    """
    if risk_pct is None:
        risk_pct = config.RISK_PER_TRADE_PCT

    risk_amount = account_balance * (risk_pct / 100.0)
    stop_distance = abs(entry_price - stop_loss_price)

    if stop_distance == 0:
        return {"quantity": 0, "risk_amount": 0, "stop_distance": 0, "risk_pct": risk_pct}

    quantity = risk_amount / stop_distance

    return {
        "quantity": round(quantity, 6),
        "risk_amount": round(risk_amount, 2),
        "stop_distance": round(stop_distance, 2),
        "risk_pct": risk_pct,
    }


def calculate_take_profits(
    entry_price: float,
    stop_loss_price: float,
    direction: str,
) -> dict:
    """
    Risk-reward based take profit levels calculate karta hai.
    
    TP1 = 1:2 Risk-Reward
    TP2 = 1:3 Risk-Reward
    """
    stop_distance = abs(entry_price - stop_loss_price)

    if direction == "LONG":
        tp1 = entry_price + (stop_distance * config.REWARD_RISK_RATIO_TP1)
        tp2 = entry_price + (stop_distance * config.REWARD_RISK_RATIO_TP2)
    else:  # SHORT
        tp1 = entry_price - (stop_distance * config.REWARD_RISK_RATIO_TP1)
        tp2 = entry_price - (stop_distance * config.REWARD_RISK_RATIO_TP2)

    return {
        "take_profit_1": round(tp1, 2),
        "take_profit_2": round(tp2, 2),
        "risk_reward_1": config.REWARD_RISK_RATIO_TP1,
        "risk_reward_2": config.REWARD_RISK_RATIO_TP2,
    }


def calculate_stop_loss(
    direction: str,
    last_swing_low: Optional[float],
    last_swing_high: Optional[float],
    atr_value: float,
    current_price: float,
) -> Optional[float]:
    """
    Chart-based stop loss calculate karta hai.
    
    LONG:  Stop Loss = Last Swing Low - (ATR × buffer)
    SHORT: Stop Loss = Last Swing High + (ATR × buffer)
    
    Agar swing point nahi mila, to ATR-based fallback use karta hai.
    """
    buffer = atr_value * config.STOP_LOSS_ATR_BUFFER

    if direction == "LONG":
        if last_swing_low is not None:
            return round(last_swing_low - buffer, 2)
        else:
            # Fallback: 2× ATR below current price
            return round(current_price - (atr_value * 2), 2)
    else:  # SHORT
        if last_swing_high is not None:
            return round(last_swing_high + buffer, 2)
        else:
            return round(current_price + (atr_value * 2), 2)


def validate_risk(
    entry_price: float,
    stop_loss: float,
    direction: str,
    atr_value: float,
) -> bool:
    """
    Risk validation checks:
    1. Stop loss entry se galat direction mein nahi hona chahiye
    2. Stop distance bohot chota nahi hona chahiye (< 0.1% = noise)
    3. Stop distance bohot bara nahi hona chahiye (> 5× ATR = too wide)
    """
    stop_distance = abs(entry_price - stop_loss)
    price_pct = (stop_distance / entry_price) * 100

    # Direction check
    if direction == "LONG" and stop_loss >= entry_price:
        return False
    if direction == "SHORT" and stop_loss <= entry_price:
        return False

    # Too tight (noise trade)
    if price_pct < 0.1:
        return False

    # Too wide (excessive risk)
    if atr_value > 0 and stop_distance > (atr_value * 5):
        return False

    return True
