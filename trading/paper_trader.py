"""
Paper Trading Simulator
========================
Virtual trading engine — real logic, fake money.
Har signal ko virtual fill karta hai, stop loss / take profit monitor karta hai,
aur complete trade journal maintain karta hai.
"""

from datetime import datetime, timezone
from typing import List, Optional

import config
from core.models import Signal, Position, Trade, AccountState
from core.database import (
    get_connection, init_schema,
    save_position, get_open_positions, update_position_price,
    update_position_stop_loss, close_position, save_trade,
    get_all_trades, get_trade_stats, get_account_balance,
    record_balance_event, update_signal_status, fetch_candles,
)


def execute_signal(signal: Signal) -> Optional[Position]:
    """
    Signal ko virtual position mein convert karta hai.
    Paper fill at signal's entry price.
    """
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)

    # Check max positions
    open_pos = get_open_positions(conn)
    if len(open_pos) >= config.MAX_OPEN_POSITIONS:
        print(f"  [SKIP] Max positions ({config.MAX_OPEN_POSITIONS}) reached. Signal {signal.signal_id} not filled.")
        conn.close()
        return None

    now = datetime.now(timezone.utc).isoformat()
    position_id = f"POS-{signal.symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    position = Position(
        position_id=position_id,
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        direction=signal.direction,
        entry_price=signal.entry_price,
        current_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit_1=signal.take_profit_1,
        take_profit_2=signal.take_profit_2,
        quantity=signal.position_size,
        unrealized_pnl=0.0,
        status="OPEN",
        opened_at=now,
    )

    save_position(conn, position)
    update_signal_status(conn, signal.signal_id, "FILLED")

    print(f"  ✓ Position opened: {position_id}")
    print(f"    {signal.direction} {signal.symbol} @ ${signal.entry_price:,.2f}")
    print(f"    SL: ${signal.stop_loss:,.2f} | TP1: ${signal.take_profit_1:,.2f} | TP2: ${signal.take_profit_2:,.2f}")
    print(f"    Size: {signal.position_size:.6f} | Risk: ${signal.risk_amount:.2f}")

    conn.close()
    return position


def update_positions_with_price(symbol: str, current_price: float) -> List[Trade]:
    """
    Ek symbol ki open positions ko current price se update karta hai.
    Agar stop loss ya take profit hit ho gaya, to position close kar ke trade record karta hai.
    Returns: List of closed trades (if any).
    """
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    closed_trades = []

    positions = get_open_positions(conn, symbol=symbol)

    for pos_dict in positions:
        entry = pos_dict["entry_price"]
        qty = pos_dict["quantity"]
        direction = pos_dict["direction"]
        sl = pos_dict["stop_loss"]
        tp1 = pos_dict["take_profit_1"]
        tp2 = pos_dict["take_profit_2"]

        # Calculate unrealized P&L
        if direction == "LONG":
            unrealized_pnl = (current_price - entry) * qty
        else:
            unrealized_pnl = (entry - current_price) * qty

        # Check stop loss
        exit_reason = None
        exit_price = None

        if direction == "LONG":
            if current_price <= sl:
                exit_reason = "STOP_LOSS"
                exit_price = sl
            elif current_price >= tp2:
                exit_reason = "TAKE_PROFIT_2"
                exit_price = tp2
            elif current_price >= tp1:
                exit_reason = "TAKE_PROFIT_1"
                exit_price = tp1
        else:  # SHORT
            if current_price >= sl:
                exit_reason = "STOP_LOSS"
                exit_price = sl
            elif current_price <= tp2:
                exit_reason = "TAKE_PROFIT_2"
                exit_price = tp2
            elif current_price <= tp1:
                exit_reason = "TAKE_PROFIT_1"
                exit_price = tp1

        if exit_reason:
            # Close position
            if direction == "LONG":
                realized_pnl = (exit_price - entry) * qty
            else:
                realized_pnl = (entry - exit_price) * qty

            # R-multiple calculation
            risk_per_unit = abs(entry - sl)
            r_multiple = (exit_price - entry) / risk_per_unit if direction == "LONG" else (entry - exit_price) / risk_per_unit
            r_multiple = round(r_multiple, 2) if risk_per_unit > 0 else 0

            now = datetime.now(timezone.utc).isoformat()
            trade_id = f"TRD-{pos_dict['symbol']}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

            trade = Trade(
                trade_id=trade_id,
                signal_id=pos_dict["signal_id"],
                symbol=pos_dict["symbol"],
                direction=direction,
                entry_price=entry,
                exit_price=exit_price,
                quantity=qty,
                pnl=round(realized_pnl, 2),
                r_multiple=r_multiple,
                exit_reason=exit_reason,
                entry_reason=f"Trend Pullback {direction}",
                opened_at=pos_dict["opened_at"],
                closed_at=now,
            )

            save_trade(conn, trade)
            close_position(conn, pos_dict["position_id"])
            if pos_dict.get("signal_id"):
                update_signal_status(conn, pos_dict["signal_id"], exit_reason)

            # Update account balance
            balance = get_account_balance(conn, config.PAPER_INITIAL_BALANCE)
            new_balance = balance + realized_pnl
            record_balance_event(conn, new_balance, "TRADE_CLOSE",
                                 f"{trade_id}: {exit_reason} PnL=${realized_pnl:+.2f}")

            pnl_emoji = "💚" if realized_pnl > 0 else "🔴"
            print(f"  {pnl_emoji} Trade closed: {trade_id}")
            print(f"    {exit_reason} | PnL: ${realized_pnl:+,.2f} ({r_multiple:+.1f}R)")
            print(f"    Balance: ${new_balance:,.2f}")

            closed_trades.append(trade)
        else:
            # ── Trailing Stop-Loss / Break-Even Lock ──────────────────
            # Jaise hi trade 30% towards TP1 move kare, SL ko Entry price par lock kar do!
            be_threshold = getattr(config, "BREAK_EVEN_TRIGGER_RATIO", 0.30)
            if direction == "LONG":
                target_dist = tp1 - entry
                if target_dist > 0 and current_price >= entry + (target_dist * be_threshold) and sl < entry:
                    update_position_stop_loss(conn, pos_dict["position_id"], entry)
                    print(f"  🛡️ [BREAK-EVEN LOCKED] {pos_dict['symbol']} {direction}: SL moved to Entry ${entry:,.2f} — Zero Risk!")
            else:  # SHORT
                target_dist = entry - tp1
                if target_dist > 0 and current_price <= entry - (target_dist * be_threshold) and sl > entry:
                    update_position_stop_loss(conn, pos_dict["position_id"], entry)
                    print(f"  🛡️ [BREAK-EVEN LOCKED] {pos_dict['symbol']} {direction}: SL moved to Entry ${entry:,.2f} — Zero Risk!")

            # Update unrealized P&L
            update_position_price(conn, pos_dict["position_id"], current_price, round(unrealized_pnl, 2))

    conn.close()
    return closed_trades


def get_account_state() -> AccountState:
    """Current paper trading account snapshot return karta hai."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)

    balance = get_account_balance(conn, config.PAPER_INITIAL_BALANCE)
    stats = get_trade_stats(conn)
    open_pos = get_open_positions(conn)

    # Calculate equity (balance + unrealized P&L)
    unrealized_total = sum(p.get("unrealized_pnl", 0) for p in open_pos)
    equity = balance + unrealized_total

    # Max drawdown (simplified: from initial balance)
    max_drawdown = 0
    if balance < config.PAPER_INITIAL_BALANCE:
        max_drawdown = round(
            (config.PAPER_INITIAL_BALANCE - balance) / config.PAPER_INITIAL_BALANCE * 100, 2
        )

    conn.close()

    return AccountState(
        balance=round(balance, 2),
        equity=round(equity, 2),
        total_trades=stats["total_trades"],
        winning_trades=stats["winning_trades"],
        losing_trades=stats["losing_trades"],
        win_rate=round(stats["win_rate"], 1),
        total_pnl=round(stats["total_pnl"], 2),
        max_drawdown=max_drawdown,
        avg_r_multiple=round(stats["avg_r_multiple"], 2),
        open_positions=len(open_pos),
    )


def print_account_summary() -> None:
    """Paper trading account ka summary display karta hai."""
    state = get_account_state()

    print(f"\n{'='*60}")
    print("  PAPER TRADING ACCOUNT")
    print(f"{'='*60}")
    print(f"  Balance:        ${state.balance:,.2f}")
    print(f"  Equity:         ${state.equity:,.2f}")
    print(f"  Open Positions: {state.open_positions}")
    print(f"{'─'*60}")
    print(f"  Total Trades:   {state.total_trades}")
    print(f"  Win Rate:       {state.win_rate}%")
    print(f"  Total P&L:      ${state.total_pnl:+,.2f}")
    print(f"  Avg R-Multiple: {state.avg_r_multiple:+.2f}R")
    print(f"  Max Drawdown:   {state.max_drawdown}%")
    print(f"{'='*60}\n")
