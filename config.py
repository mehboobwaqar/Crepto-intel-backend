"""
Centralized Configuration
=========================
Tamam project-wide settings ek jagah. Koi hardcoded value kisi aur file mein nahi hogi.
Naya coin add karna ho? Sirf SYMBOLS list mein naam daal do, baaki sab automatic hai.
"""

from pathlib import Path

# ── Project Paths ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
DB_PATH = PROJECT_ROOT / "crypto_market.db"

# ── Universe (Monitored Coins) ────────────────────────────────
# Yahan jitne chahein Binance Spot pairs add kar sakte hain.
# Engine har coin par equally kaam karega - koi code change nahi chahiye.
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "LINKUSDT",
]

# ── Timeframes ─────────────────────────────────────────────────
TIMEFRAMES = ["1h", "4h"]

# ── History Depth ──────────────────────────────────────────────
DAYS_BACK = 365

# ── Binance API ────────────────────────────────────────────────
BINANCE_REST_BASE = "https://data-api.binance.vision/api/v3"
BINANCE_KLINES_URL = f"{BINANCE_REST_BASE}/klines"
API_REQUEST_TIMEOUT = 10          # seconds
API_RETRY_ATTEMPTS = 3
API_RETRY_DELAY = 2               # seconds
API_RATE_LIMIT_SLEEP = 0.1        # seconds between batch requests

# ── Indicator Parameters ──────────────────────────────────────
EMA_FAST_PERIOD = 20
EMA_SLOW_PERIOD = 50
RSI_PERIOD = 14
ATR_PERIOD = 14

# ── Market Structure ──────────────────────────────────────────
SWING_LOOKBACK = 5     # Pivot detection: kitne bars left & right check karne hain

# ── Strategy Parameters ───────────────────────────────────────
STRATEGY_NAME = "trend_pullback"
# Trend filter: 4H timeframe se trend liya jayega
TREND_TIMEFRAME = "4h"
# Entry timeframe: 1H par entry signals generate honge
ENTRY_TIMEFRAME = "1h"
# RSI pullback zones
RSI_LONG_PULLBACK_LOW = 40
RSI_LONG_PULLBACK_HIGH = 55
RSI_SHORT_PULLBACK_LOW = 45
RSI_SHORT_PULLBACK_HIGH = 60
# ATR minimum filter (% of price) - dead market filter
ATR_MIN_PCT = 0.3
# EMA proximity for pullback detection (% distance from EMA20)
EMA_PULLBACK_TOLERANCE_PCT = 0.5

# ── Risk Management ──────────────────────────────────────────
RISK_PER_TRADE_PCT = 0.5          # 0.5% of account per trade
MAX_OPEN_POSITIONS = 3
REWARD_RISK_RATIO_TP1 = 2.0      # First target: 1:2 R:R
REWARD_RISK_RATIO_TP2 = 3.0      # Second target: 1:3 R:R
STOP_LOSS_ATR_BUFFER = 0.5       # Extra buffer beyond swing point (in ATR units)

# ── Paper Trading ─────────────────────────────────────────────
PAPER_INITIAL_BALANCE = 10000.0   # $10,000 virtual starting balance
PAPER_CURRENCY = "USDT"

# ── FastAPI Server ────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 8000

# ── WebSocket Streamer ────────────────────────────────────────
BINANCE_WS_BASE = "wss://data-stream.binance.vision/ws"
WS_RECONNECT_DELAY = 5           # seconds before reconnect attempt
WS_MAX_RECONNECT_DELAY = 60      # max backoff delay
