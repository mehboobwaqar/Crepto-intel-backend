"""
Binance Historical Candle Fetcher
Part of: Personal Zero-Cost Crypto Market Intelligence Agent
Downloads 1 year of 1H and 4H historical klines for selected coins and stores them cleanly.
Uses only Python Standard Library (Zero dependencies needed).
"""

import sqlite3
import urllib.request
import urllib.parse
import json
import time
from datetime import datetime, timedelta, timezone

# ================= CONFIGURATION =================
# Aap yahan jitne chahein coins add kar sakte hain!
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"]
TIMEFRAMES = ["1h", "4h"]
DAYS_BACK = 365
DB_PATH = "crypto_market.db"
BINANCE_API_URL = "https://data-api.binance.vision/api/v3/klines"
# =================================================

def init_database(db_path: str):
    """Database aur candles table initialize karta hai with unique constraints (Idempotency)."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'binance',
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            close_time INTEGER NOT NULL,
            quote_volume REAL NOT NULL,
            trades_count INTEGER NOT NULL,
            taker_buy_base_volume REAL NOT NULL,
            taker_buy_quote_volume REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, symbol, timeframe, open_time)
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_candles_lookup 
        ON candles(symbol, timeframe, open_time);
    """)
    conn.commit()
    return conn

def fetch_klines_chunk(symbol: str, interval: str, start_time: int, end_time: int, limit: int = 1000):
    """Binance REST API se 1 chunk (up to 1000 candles) fetch karta hai with retry logic."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": limit
    }
    url = f"{BINANCE_API_URL}?{urllib.parse.urlencode(params)}"
    
    headers = {"User-Agent": "ZeroCostCryptoAgent/1.0"}
    req = urllib.request.Request(url, headers=headers)
    
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode())
        except Exception as e:
            print(f"  [Attempt {attempt}/3] Network error on {symbol} {interval}: {e}. Retrying in 2s...")
            time.sleep(2)
            
    print(f"  [Failed] Could not fetch data for {symbol} {interval} starting at {start_time}")
    return []

def save_candles(conn, symbol: str, timeframe: str, klines: list):
    """Candles ko database mein safely insert karta hai (Duplicate ignore karega)."""
    if not klines:
        return 0
        
    cursor = conn.cursor()
    records = []
    for k in klines:
        # Binance kline format:
        # [0: open_time, 1: open, 2: high, 3: low, 4: close, 5: volume, 
        #  6: close_time, 7: quote_vol, 8: trades, 9: taker_base, 10: taker_quote, 11: ignore]
        records.append((
            'binance',
            symbol,
            timeframe,
            int(k[0]),
            float(k[1]),
            float(k[2]),
            float(k[3]),
            float(k[4]),
            float(k[5]),
            int(k[6]),
            float(k[7]),
            int(k[8]),
            float(k[9]),
            float(k[10])
        ))
        
    cursor.executemany("""
        INSERT OR IGNORE INTO candles (
            source, symbol, timeframe, open_time, open, high, low, close, volume,
            close_time, quote_volume, trades_count, taker_buy_base_volume, taker_buy_quote_volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    return cursor.rowcount

def download_historical_candles(conn, symbol: str, timeframe: str, days_back: int):
    """Pichle N dino ka data chunk by chunk fetch aur store karta hai."""
    now_utc = datetime.now(timezone.utc)
    start_dt = now_utc - timedelta(days=days_back)
    
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(now_utc.timestamp() * 1000)
    
    current_start = start_ms
    total_saved = 0
    print(f"\n>> Fetching [{symbol}] [{timeframe}] from {start_dt.strftime('%Y-%m-%d')} to {now_utc.strftime('%Y-%m-%d')}...")
    
    while current_start < end_ms:
        klines = fetch_klines_chunk(symbol, timeframe, current_start, end_ms, limit=1000)
        if not klines:
            break
            
        saved_count = save_candles(conn, symbol, timeframe, klines)
        total_saved += saved_count
        
        last_candle_open = klines[-1][0]
        last_candle_date = datetime.fromtimestamp(last_candle_open / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        print(f"   Saved batch: {len(klines)} candles (Reached: {last_candle_date} UTC) | Total newly saved: {total_saved}")
        
        # Next batch shuru hoga aakhri candle ke close_time + 1 ms se
        last_close_time = klines[-1][6]
        if last_close_time <= current_start:
            break
        current_start = last_close_time + 1
        
        # Polite rate-limiting sleep (Binance limits ko respect karne ke liye)
        time.sleep(0.1)
        
    print(f"OK: Finished [{symbol}] [{timeframe}] -> Total stored: {total_saved} candles.")

def print_summary(conn):
    """Total stored candles ki summary display karta hai."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, timeframe, COUNT(*), 
               datetime(MIN(open_time)/1000, 'unixepoch'), 
               datetime(MAX(open_time)/1000, 'unixepoch')
        FROM candles
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe;
    """)
    rows = cursor.fetchall()
    print("\n" + "="*70)
    print(f"{'SYMBOL':<10} {'TIMEFRAME':<10} {'TOTAL CANDLES':<15} {'FROM (UTC)':<20} {'TO (UTC)':<20}")
    print("="*70)
    for row in rows:
        print(f"{row[0]:<10} {row[1]:<10} {row[2]:<15} {row[3]:<20} {row[4]:<20}")
    print("="*70 + "\n")

if __name__ == "__main__":
    print(f"=== Zero-Cost Binance Data Ingestion Initialized ===")
    print(f"Target Database: {DB_PATH}")
    print(f"Monitored Universe: {SYMBOLS}")
    print(f"Timeframes: {TIMEFRAMES}")
    print(f"History: {DAYS_BACK} days\n")
    
    conn = init_database(DB_PATH)
    try:
        for sym in SYMBOLS:
            for tf in TIMEFRAMES:
                download_historical_candles(conn, sym, tf, DAYS_BACK)
        print_summary(conn)
    finally:
        conn.close()
        print("Done! Database connection closed.")
