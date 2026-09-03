#!/usr/bin/env python3
"""
Zero-Cost Crypto Market Intelligence Agent
==========================================
Main entry point - CLI interface for all operations.

Usage:
    python3 main.py download              # Download/update historical candle data
    python3 main.py analyze               # Run analytics on all pairs
    python3 main.py analyze BTCUSDT       # Run analytics on specific coin
    python3 main.py signals               # Scan for new trade signals
    python3 main.py paper-status          # Show paper trading account
    python3 main.py trades                # Show trade history
    python3 main.py stream                # Start live WebSocket streamer
    python3 main.py serve                 # Start FastAPI REST server
    python3 main.py status                # Show database summary
"""

import sys
from datetime import datetime, timezone

import config
from core.database import (
    get_connection, init_schema, fetch_candles,
    get_available_pairs, get_candle_count,
    save_indicator_batch, save_swing_points,
    get_recent_signals, get_all_trades,
)
from core.models import IndicatorSnapshot
from analytics.indicators import calculate_ema, calculate_rsi, calculate_atr
from analytics.market_structure import detect_swing_points, classify_structure, determine_regime
from ingestion.binance_fetcher import download_all


# ── Analytics Pipeline ─────────────────────────────────────────


def run_analytics(symbol: str, timeframe: str) -> None:
    """
    Complete analytics pipeline for one symbol+timeframe:
    1. Database se candles uthao
    2. EMA, RSI, ATR calculate karo
    3. Swing Points detect karo
    4. Market Structure (HH/HL/LH/LL) classify karo
    5. Trend regime determine karo
    6. Results database mein save aur console par display karo
    """
    conn = get_connection(str(config.DB_PATH))
    candles = fetch_candles(conn, symbol, timeframe)

    if len(candles) < config.EMA_SLOW_PERIOD + 10:
        print(f"  [SKIP] {symbol} {timeframe}: Not enough candles ({len(candles)})")
        conn.close()
        return

    print(f"\n{'─'*60}")
    print(f"  ANALYZING: {symbol} | {timeframe} | {len(candles)} candles")
    print(f"{'─'*60}")

    # ── Step 1: Extract price arrays ──────────────────────────
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    # ── Step 2: Calculate Indicators ──────────────────────────
    ema_fast = calculate_ema(closes, config.EMA_FAST_PERIOD)
    ema_slow = calculate_ema(closes, config.EMA_SLOW_PERIOD)
    rsi = calculate_rsi(closes, config.RSI_PERIOD)
    atr = calculate_atr(highs, lows, closes, config.ATR_PERIOD)

    # Build snapshots for database storage
    snapshots = []
    for i, candle in enumerate(candles):
        snapshots.append(
            IndicatorSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                open_time=candle.open_time,
                close=candle.close,
                ema_fast=ema_fast[i],
                ema_slow=ema_slow[i],
                rsi=rsi[i],
                atr=atr[i],
            )
        )

    saved = save_indicator_batch(conn, snapshots)
    print(f"  ✓ Indicators computed & saved: {saved} rows (EMA {config.EMA_FAST_PERIOD}/{config.EMA_SLOW_PERIOD}, RSI {config.RSI_PERIOD}, ATR {config.ATR_PERIOD})")

    # ── Step 3: Detect Swing Points ───────────────────────────
    swings = detect_swing_points(candles, lookback=config.SWING_LOOKBACK)
    swing_highs = [s for s in swings if s.swing_type == "SWING_HIGH"]
    swing_lows = [s for s in swings if s.swing_type == "SWING_LOW"]

    saved_swings = save_swing_points(conn, symbol, timeframe, swings)
    print(f"  ✓ Swing Points detected: {len(swing_highs)} Highs, {len(swing_lows)} Lows ({saved_swings} saved)")

    # ── Step 4: Market Structure ──────────────────────────────
    labels = classify_structure(swings)
    regime = determine_regime(symbol, timeframe, swings)

    # ── Step 5: Display Results ───────────────────────────────
    latest = candles[-1]
    latest_date = datetime.fromtimestamp(
        latest.open_time / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n  ┌─ LATEST SNAPSHOT ({latest_date}) ─────────────────")
    print(f"  │  Price:    ${latest.close:,.2f}")

    latest_ema_f = ema_fast[-1]
    latest_ema_s = ema_slow[-1]
    latest_rsi = rsi[-1]
    latest_atr = atr[-1]

    if latest_ema_f and latest_ema_s:
        ema_alignment = "BULLISH (Price > EMA50)" if latest.close > latest_ema_s else "BEARISH (Price < EMA50)"
        print(f"  │  EMA {config.EMA_FAST_PERIOD}:   ${latest_ema_f:,.2f}")
        print(f"  │  EMA {config.EMA_SLOW_PERIOD}:   ${latest_ema_s:,.2f}")
        print(f"  │  EMA Bias: {ema_alignment}")

    if latest_rsi:
        rsi_label = "OVERBOUGHT ⚠" if latest_rsi > 70 else ("OVERSOLD ⚠" if latest_rsi < 30 else "NEUTRAL")
        print(f"  │  RSI {config.RSI_PERIOD}:   {latest_rsi:.1f} ({rsi_label})")

    if latest_atr:
        atr_pct = (latest_atr / latest.close) * 100
        print(f"  │  ATR {config.ATR_PERIOD}:   ${latest_atr:,.2f} ({atr_pct:.2f}% of price)")

    trend_emoji = {"UPTREND": "📈", "DOWNTREND": "📉", "RANGING": "↔️", "INSUFFICIENT_DATA": "❓"}
    print(f"  │")
    print(f"  │  TREND:      {trend_emoji.get(regime.trend, '')}  {regime.trend}")
    print(f"  │  CONFIDENCE: {regime.confidence}")

    if regime.last_swing_high_price:
        print(f"  │  Last Swing High: ${regime.last_swing_high_price:,.2f}")
    if regime.last_swing_low_price:
        print(f"  │  Last Swing Low:  ${regime.last_swing_low_price:,.2f}")

    recent_labels = labels[-8:] if labels else []
    if recent_labels:
        label_str = " → ".join(l.label for l in recent_labels)
        print(f"  │  Structure:  {label_str}")

    print(f"  └{'─'*50}")
    conn.close()


# ── CLI Commands ───────────────────────────────────────────────


def cmd_download():
    """Download/update historical candle data from Binance."""
    download_all()


def cmd_analyze(filter_symbol: str = None):
    """Run analytics on all (or filtered) pairs in database."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    pairs = get_available_pairs(conn)
    conn.close()

    if not pairs:
        print("Database is empty! Run 'python3 main.py download' first.")
        return

    if filter_symbol:
        pairs = [(s, t) for s, t in pairs if s == filter_symbol.upper()]
        if not pairs:
            print(f"No data found for '{filter_symbol}'.")
            return

    print("\n" + "=" * 60)
    print("  CRYPTO MARKET INTELLIGENCE — ANALYTICS ENGINE")
    print(f"  Pairs to analyze: {len(pairs)}")
    print("=" * 60)

    for symbol, timeframe in pairs:
        run_analytics(symbol, timeframe)

    print(f"\n{'='*60}")
    print("  ANALYTICS COMPLETE ✓")
    print(f"{'='*60}\n")


def cmd_signals():
    """Scan for new trade signals across all symbols."""
    from strategy.signal_generator import scan_all_symbols

    print("\n" + "=" * 60)
    print("  SIGNAL SCANNER — Trend Pullback Strategy")
    print(f"  Scanning: {config.SYMBOLS}")
    print("=" * 60)

    signals = scan_all_symbols()

    if signals:
        for sig in signals:
            print(f"\n  🔔 SIGNAL: {sig.direction} {sig.symbol}")
            print(f"     Entry:  ${sig.entry_price:,.2f}")
            print(f"     SL:     ${sig.stop_loss:,.2f}")
            print(f"     TP1:    ${sig.take_profit_1:,.2f} (1:{sig.risk_reward_ratio})")
            print(f"     TP2:    ${sig.take_profit_2:,.2f}")
            print(f"     Size:   {sig.position_size:.6f} | Risk: ${sig.risk_amount:.2f}")
            print(f"     Reason: {sig.reason}")
    else:
        print("\n  No signals generated. Market conditions not met for any symbol.")

    # Also show recent signals from DB
    conn = get_connection(str(config.DB_PATH))
    recent = get_recent_signals(conn, limit=5)
    conn.close()

    if recent:
        print(f"\n  ── Recent Signals ────────────────────────────")
        for s in recent:
            print(f"  {s['status']:<10} {s['direction']:<6} {s['symbol']:<10} @ ${s['entry_price']:,.2f}  ({s['created_at'][:16]})")

    print(f"\n{'='*60}\n")


def cmd_paper_status():
    """Show paper trading account summary."""
    from trading.paper_trader import print_account_summary
    print_account_summary()


def cmd_trades():
    """Show trade history."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    trades = get_all_trades(conn, limit=20)
    conn.close()

    if not trades:
        print("\n  No trades yet. Signals need to be generated and filled first.\n")
        return

    print(f"\n{'='*75}")
    print(f"  {'DATE':<18} {'SYMBOL':<10} {'DIR':<6} {'ENTRY':>10} {'EXIT':>10} {'P&L':>10} {'R':>6} {'REASON':<15}")
    print(f"{'='*75}")
    for t in trades:
        pnl_str = f"${t['pnl']:+,.2f}"
        r_str = f"{t['r_multiple']:+.1f}R"
        print(f"  {t['closed_at'][:16]:<18} {t['symbol']:<10} {t['direction']:<6} ${t['entry_price']:>9,.2f} ${t['exit_price']:>9,.2f} {pnl_str:>10} {r_str:>6} {t['exit_reason']:<15}")
    print(f"{'='*75}\n")


def cmd_stream():
    """Start live WebSocket streamer."""
    from ingestion.websocket_streamer import start_streaming
    start_streaming()


def cmd_serve():
    """Start FastAPI REST server."""
    from api.app import start_server
    start_server()


def cmd_status():
    """Show database summary."""
    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)
    pairs = get_available_pairs(conn)

    if not pairs:
        print("Database is empty.")
        conn.close()
        return

    print("\n" + "=" * 75)
    print(f"  {'SYMBOL':<12} {'TIMEFRAME':<10} {'CANDLES':<12} {'INDICATORS':<12} {'SWINGS':<10}")
    print("=" * 75)

    for sym, tf in pairs:
        candle_count = get_candle_count(conn, sym, tf)
        ind_count = conn.execute(
            "SELECT COUNT(*) as c FROM indicators WHERE symbol=? AND timeframe=? AND ema_fast IS NOT NULL",
            (sym, tf),
        ).fetchone()["c"]
        swing_count = conn.execute(
            "SELECT COUNT(*) as c FROM swing_points WHERE symbol=? AND timeframe=?",
            (sym, tf),
        ).fetchone()["c"]
        print(f"  {sym:<12} {tf:<10} {candle_count:<12} {ind_count:<12} {swing_count:<10}")

    # Signals & trades summary
    sig_count = conn.execute("SELECT COUNT(*) as c FROM signals").fetchone()["c"]
    trade_count = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()["c"]
    pos_count = conn.execute("SELECT COUNT(*) as c FROM positions WHERE status='OPEN'").fetchone()["c"]

    print(f"{'─'*75}")
    print(f"  Signals: {sig_count} | Trades: {trade_count} | Open Positions: {pos_count}")
    print("=" * 75)
    conn.close()


# ── Main Entry Point ──────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()
    commands = {
        "download": cmd_download,
        "analyze": lambda: cmd_analyze(sys.argv[2] if len(sys.argv) > 2 else None),
        "signals": cmd_signals,
        "paper-status": cmd_paper_status,
        "trades": cmd_trades,
        "stream": cmd_stream,
        "serve": cmd_serve,
        "status": cmd_status,
    }

    handler = commands.get(command)
    if handler:
        handler()
    else:
        print(f"Unknown command: '{command}'")
        print(__doc__)


if __name__ == "__main__":
    main()
