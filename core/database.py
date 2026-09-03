"""
Database Layer
==============
SQLite connection management, schema creation, and all data queries.
Baaki koi bhi module seedha SQL nahi likhega - sab kuch yahan se guzrega.
"""

import sqlite3
from typing import List, Optional
from core.models import Candle, IndicatorSnapshot, Signal, Position, Trade, AccountState


def get_connection(db_path: str) -> sqlite3.Connection:
    """Thread-safe database connection with WAL mode for concurrent reads."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Database schema banata hai (idempotent - baar baar chala sakte hain)."""
    cursor = conn.cursor()

    # ── Raw Candles Table ──────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT    NOT NULL DEFAULT 'binance',
            symbol          TEXT    NOT NULL,
            timeframe       TEXT    NOT NULL,
            open_time       INTEGER NOT NULL,
            open            REAL    NOT NULL,
            high            REAL    NOT NULL,
            low             REAL    NOT NULL,
            close           REAL    NOT NULL,
            volume          REAL    NOT NULL,
            close_time      INTEGER NOT NULL,
            quote_volume    REAL    NOT NULL,
            trades_count    INTEGER NOT NULL,
            taker_buy_base_volume  REAL NOT NULL,
            taker_buy_quote_volume REAL NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, symbol, timeframe, open_time)
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_candles_lookup
        ON candles(symbol, timeframe, open_time);
    """)

    # ── Computed Indicators Table ──────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS indicators (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            timeframe   TEXT    NOT NULL,
            open_time   INTEGER NOT NULL,
            close       REAL    NOT NULL,
            ema_fast    REAL,
            ema_slow    REAL,
            rsi         REAL,
            atr         REAL,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timeframe, open_time)
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_indicators_lookup
        ON indicators(symbol, timeframe, open_time);
    """)

    # ── Swing Points Table ─────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS swing_points (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            timeframe   TEXT    NOT NULL,
            open_time   INTEGER NOT NULL,
            price       REAL    NOT NULL,
            swing_type  TEXT    NOT NULL,
            label       TEXT,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timeframe, open_time, swing_type)
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_swing_lookup
        ON swing_points(symbol, timeframe, open_time);
    """)

    # ── Signals Table ──────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            signal_id       TEXT PRIMARY KEY,
            symbol          TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            stop_loss       REAL    NOT NULL,
            take_profit_1   REAL    NOT NULL,
            take_profit_2   REAL    NOT NULL,
            position_size   REAL    NOT NULL,
            risk_amount     REAL    NOT NULL,
            risk_reward_ratio REAL  NOT NULL,
            confidence      TEXT    NOT NULL,
            reason          TEXT    NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'ACTIVE',
            timeframe       TEXT    NOT NULL DEFAULT '1h',
            trend_tf        TEXT    NOT NULL DEFAULT '4h',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Positions Table ────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            position_id     TEXT PRIMARY KEY,
            signal_id       TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            current_price   REAL    NOT NULL,
            stop_loss       REAL    NOT NULL,
            take_profit_1   REAL    NOT NULL,
            take_profit_2   REAL    NOT NULL,
            quantity        REAL    NOT NULL,
            unrealized_pnl  REAL    DEFAULT 0.0,
            status          TEXT    NOT NULL DEFAULT 'OPEN',
            opened_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Trades Table (Closed positions) ────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id        TEXT PRIMARY KEY,
            signal_id       TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            exit_price      REAL    NOT NULL,
            quantity        REAL    NOT NULL,
            pnl             REAL    NOT NULL,
            r_multiple      REAL    NOT NULL,
            exit_reason     TEXT    NOT NULL,
            entry_reason    TEXT    NOT NULL,
            opened_at       TIMESTAMP,
            closed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Account Ledger ─────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            balance         REAL    NOT NULL,
            event_type      TEXT    NOT NULL,
            event_detail    TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()


# ── Query Functions ────────────────────────────────────────────


def get_available_pairs(conn: sqlite3.Connection) -> List[tuple]:
    """Database mein mojood tamam unique (symbol, timeframe) pairs return karta hai."""
    cursor = conn.execute("""
        SELECT DISTINCT symbol, timeframe FROM candles ORDER BY symbol, timeframe;
    """)
    return [(row["symbol"], row["timeframe"]) for row in cursor.fetchall()]


def fetch_candles(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    limit: Optional[int] = None,
) -> List[Candle]:
    """Database se candles uthata hai (sorted by open_time ascending)."""
    if limit:
        query = """
            SELECT symbol, timeframe, open_time, open, high, low, close, volume,
                   close_time, quote_volume, trades_count,
                   taker_buy_base_volume, taker_buy_quote_volume
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY open_time DESC
            LIMIT ?
        """
        cursor = conn.execute(query, [symbol, timeframe, limit])
        rows = cursor.fetchall()
        rows.reverse()
    else:
        query = """
            SELECT symbol, timeframe, open_time, open, high, low, close, volume,
                   close_time, quote_volume, trades_count,
                   taker_buy_base_volume, taker_buy_quote_volume
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY open_time ASC
        """
        cursor = conn.execute(query, [symbol, timeframe])
        rows = cursor.fetchall()

    return [
        Candle(
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            open_time=row["open_time"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            close_time=row["close_time"],
            quote_volume=row["quote_volume"],
            trades_count=row["trades_count"],
            taker_buy_base_volume=row["taker_buy_base_volume"],
            taker_buy_quote_volume=row["taker_buy_quote_volume"],
        )
        for row in rows
    ]


def save_indicator_batch(
    conn: sqlite3.Connection, snapshots: List[IndicatorSnapshot]
) -> int:
    """Computed indicators ko database mein bulk save karta hai."""
    if not snapshots:
        return 0
    cursor = conn.cursor()
    records = [
        (s.symbol, s.timeframe, s.open_time, s.close,
         s.ema_fast, s.ema_slow, s.rsi, s.atr)
        for s in snapshots
    ]
    cursor.executemany("""
        INSERT OR REPLACE INTO indicators
            (symbol, timeframe, open_time, close, ema_fast, ema_slow, rsi, atr)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    return len(records)


def save_swing_points(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    points: list,
) -> int:
    """Detected swing points ko database mein save karta hai."""
    if not points:
        return 0
    cursor = conn.cursor()
    # Pehle purane swing points clear karo is pair ke liye (fresh recalculation)
    cursor.execute(
        "DELETE FROM swing_points WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    )
    records = [
        (symbol, timeframe, sp.open_time, sp.price, sp.swing_type,
         getattr(sp, "label", None) if hasattr(sp, "label") else None)
        for sp in points
    ]
    cursor.executemany("""
        INSERT OR REPLACE INTO swing_points
            (symbol, timeframe, open_time, price, swing_type, label)
        VALUES (?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    return len(records)


def upsert_candles(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    raw_klines: list,
) -> int:
    """Raw Binance klines ko database mein insert karta hai (duplicates ignore)."""
    if not raw_klines:
        return 0
    cursor = conn.cursor()
    records = [
        (
            "binance", symbol, timeframe,
            int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]),
            float(k[5]), int(k[6]), float(k[7]), int(k[8]),
            float(k[9]), float(k[10]),
        )
        for k in raw_klines
    ]
    cursor.executemany("""
        INSERT OR IGNORE INTO candles (
            source, symbol, timeframe, open_time, open, high, low, close,
            volume, close_time, quote_volume, trades_count,
            taker_buy_base_volume, taker_buy_quote_volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    return cursor.rowcount


def get_candle_count(conn: sqlite3.Connection, symbol: str, timeframe: str) -> int:
    """Kisi pair ki total candle count return karta hai."""
    cursor = conn.execute(
        "SELECT COUNT(*) as cnt FROM candles WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    )
    return cursor.fetchone()["cnt"]


# ── Signal Functions ───────────────────────────────────────────


def save_signal(conn: sqlite3.Connection, signal: Signal) -> None:
    """Naya signal database mein save karta hai."""
    conn.execute("""
        INSERT OR REPLACE INTO signals
            (signal_id, symbol, direction, entry_price, stop_loss,
             take_profit_1, take_profit_2, position_size, risk_amount,
             risk_reward_ratio, confidence, reason, status, timeframe, trend_tf, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal.signal_id, signal.symbol, signal.direction,
        signal.entry_price, signal.stop_loss,
        signal.take_profit_1, signal.take_profit_2,
        signal.position_size, signal.risk_amount,
        signal.risk_reward_ratio, signal.confidence,
        signal.reason, signal.status, signal.timeframe,
        signal.trend_tf, signal.created_at,
    ))
    conn.commit()


def get_active_signals(conn: sqlite3.Connection, symbol: str = None) -> List[dict]:
    """Active signals return karta hai (optionally filtered by symbol)."""
    query = "SELECT * FROM signals WHERE status = 'ACTIVE'"
    params = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    query += " ORDER BY created_at DESC"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_recent_signals(conn: sqlite3.Connection, limit: int = 20) -> List[dict]:
    """Recent signals (all statuses) return karta hai."""
    return [dict(row) for row in conn.execute(
        "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()]


def update_signal_status(conn: sqlite3.Connection, signal_id: str, status: str) -> None:
    """Signal ka status update karta hai."""
    conn.execute("UPDATE signals SET status = ? WHERE signal_id = ?", (status, signal_id))
    conn.commit()


# ── Position Functions ─────────────────────────────────────────


def save_position(conn: sqlite3.Connection, pos: Position) -> None:
    """Nayi position save karta hai."""
    conn.execute("""
        INSERT OR REPLACE INTO positions
            (position_id, signal_id, symbol, direction, entry_price, current_price,
             stop_loss, take_profit_1, take_profit_2, quantity, unrealized_pnl,
             status, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pos.position_id, pos.signal_id, pos.symbol, pos.direction,
        pos.entry_price, pos.current_price, pos.stop_loss,
        pos.take_profit_1, pos.take_profit_2, pos.quantity,
        pos.unrealized_pnl, pos.status, pos.opened_at,
    ))
    conn.commit()


def get_open_positions(conn: sqlite3.Connection, symbol: str = None) -> List[dict]:
    """Open positions return karta hai."""
    query = "SELECT * FROM positions WHERE status = 'OPEN'"
    params = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def update_position_price(conn: sqlite3.Connection, position_id: str,
                          current_price: float, unrealized_pnl: float) -> None:
    """Position ki current price aur unrealized P&L update karta hai."""
    conn.execute("""
        UPDATE positions SET current_price = ?, unrealized_pnl = ?
        WHERE position_id = ?
    """, (current_price, unrealized_pnl, position_id))
    conn.commit()


def update_position_stop_loss(conn: sqlite3.Connection, position_id: str,
                              new_stop_loss: float) -> None:
    """Trailing Stop Loss update karta hai (Break-Even lock)."""
    conn.execute("""
        UPDATE positions SET stop_loss = ?
        WHERE position_id = ?
    """, (new_stop_loss, position_id))
    conn.commit()


def close_position(conn: sqlite3.Connection, position_id: str) -> None:
    """Position ko close mark karta hai."""
    conn.execute("UPDATE positions SET status = 'CLOSED' WHERE position_id = ?", (position_id,))
    conn.commit()


# ── Trade Functions ────────────────────────────────────────────


def save_trade(conn: sqlite3.Connection, trade: Trade) -> None:
    """Completed trade save karta hai."""
    conn.execute("""
        INSERT INTO trades
            (trade_id, signal_id, symbol, direction, entry_price, exit_price,
             quantity, pnl, r_multiple, exit_reason, entry_reason, opened_at, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade.trade_id, trade.signal_id, trade.symbol, trade.direction,
        trade.entry_price, trade.exit_price, trade.quantity,
        trade.pnl, trade.r_multiple, trade.exit_reason,
        trade.entry_reason, trade.opened_at, trade.closed_at,
    ))
    conn.commit()


def get_all_trades(conn: sqlite3.Connection, limit: int = 50) -> List[dict]:
    """Trade history return karta hai."""
    return [dict(row) for row in conn.execute(
        "SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (limit,)
    ).fetchall()]


def get_trade_stats(conn: sqlite3.Connection) -> dict:
    """Overall trading performance stats return karta hai."""
    cursor = conn.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losing_trades,
            COALESCE(SUM(pnl), 0) as total_pnl,
            COALESCE(AVG(r_multiple), 0) as avg_r_multiple,
            COALESCE(MAX(pnl), 0) as best_trade,
            COALESCE(MIN(pnl), 0) as worst_trade
        FROM trades
    """)
    row = cursor.fetchone()
    total = row["total_trades"] or 0
    return {
        "total_trades": total,
        "winning_trades": row["winning_trades"] or 0,
        "losing_trades": row["losing_trades"] or 0,
        "win_rate": (row["winning_trades"] or 0) / total * 100 if total > 0 else 0,
        "total_pnl": row["total_pnl"],
        "avg_r_multiple": row["avg_r_multiple"],
        "best_trade": row["best_trade"],
        "worst_trade": row["worst_trade"],
    }


# ── Account Ledger Functions ──────────────────────────────────


def get_account_balance(conn: sqlite3.Connection, initial_balance: float) -> float:
    """Latest account balance return karta hai (ya initial balance agar koi entry nahi)."""
    cursor = conn.execute(
        "SELECT balance FROM account_ledger ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return row["balance"] if row else initial_balance


def record_balance_event(conn: sqlite3.Connection, balance: float,
                         event_type: str, detail: str = "") -> None:
    """Account balance change event log karta hai."""
    conn.execute(
        "INSERT INTO account_ledger (balance, event_type, event_detail) VALUES (?, ?, ?)",
        (balance, event_type, detail),
    )
    conn.commit()

