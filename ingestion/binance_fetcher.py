"""
Binance Data Fetcher
====================
Historical candle data Binance Public REST API se download karta hai.
Koi API key nahi chahiye. Automatic pagination aur retry logic built-in hai.
"""

import urllib.request
import urllib.parse
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List

import config
from core.database import get_connection, init_schema, upsert_candles, get_candle_count


def fetch_klines_chunk(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    limit: int = 1000,
) -> list:
    """Binance se 1 chunk (up to 1000 candles) download karta hai with retry."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": limit,
    }
    url = f"{config.BINANCE_KLINES_URL}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": "ZeroCostCryptoAgent/1.0"}
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(1, config.API_RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=config.API_REQUEST_TIMEOUT) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode())
        except Exception as e:
            print(f"  [Attempt {attempt}/{config.API_RETRY_ATTEMPTS}] {symbol} {interval}: {e}")
            time.sleep(config.API_RETRY_DELAY)

    print(f"  [FAILED] Could not fetch {symbol} {interval} from {start_time}")
    return []


def download_historical(
    symbol: str,
    timeframe: str,
    days_back: int = None,
) -> int:
    """
    Ek symbol+timeframe ka pura historical data download aur store karta hai.
    Returns: Total newly saved candle count.
    """
    if days_back is None:
        days_back = config.DAYS_BACK

    conn = get_connection(str(config.DB_PATH))
    init_schema(conn)

    now_utc = datetime.now(timezone.utc)
    start_dt = now_utc - timedelta(days=days_back)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(now_utc.timestamp() * 1000)

    current_start = start_ms
    total_saved = 0

    print(f"\n>> Downloading [{symbol}] [{timeframe}] "
          f"from {start_dt.strftime('%Y-%m-%d')} to {now_utc.strftime('%Y-%m-%d')}...")

    while current_start < end_ms:
        klines = fetch_klines_chunk(symbol, timeframe, current_start, end_ms)
        if not klines:
            break

        saved = upsert_candles(conn, symbol, timeframe, klines)
        total_saved += saved

        last_open = klines[-1][0]
        last_date = datetime.fromtimestamp(
            last_open / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")
        print(f"   Batch: {len(klines)} candles (→ {last_date} UTC) | New: {total_saved}")

        last_close = klines[-1][6]
        if last_close <= current_start:
            break
        current_start = last_close + 1
        time.sleep(config.API_RATE_LIMIT_SLEEP)

    total_in_db = get_candle_count(conn, symbol, timeframe)
    print(f"   Done [{symbol}] [{timeframe}]: {total_saved} new, {total_in_db} total in DB.")
    conn.close()
    return total_saved


def download_all() -> None:
    """Config mein defined tamam symbols aur timeframes ka data download karta hai."""
    print("=" * 60)
    print("  BINANCE HISTORICAL DATA DOWNLOAD")
    print(f"  Universe: {config.SYMBOLS}")
    print(f"  Timeframes: {config.TIMEFRAMES}")
    print(f"  History: {config.DAYS_BACK} days")
    print("=" * 60)

    for symbol in config.SYMBOLS:
        for tf in config.TIMEFRAMES:
            download_historical(symbol, tf)

    # Final summary
    conn = get_connection(str(config.DB_PATH))
    from core.database import get_available_pairs
    pairs = get_available_pairs(conn)

    print("\n" + "=" * 75)
    print(f"{'SYMBOL':<12} {'TIMEFRAME':<10} {'CANDLES':<12}")
    print("=" * 75)
    for sym, tf in pairs:
        count = get_candle_count(conn, sym, tf)
        print(f"{sym:<12} {tf:<10} {count:<12}")
    print("=" * 75)
    conn.close()
