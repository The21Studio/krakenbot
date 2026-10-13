"""
config.py — Configurazione centralizzata v6.0 MULTI-MERCATO
Crypto (Kraken) + Forex (OANDA) + Stock (Alpaca)
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ─── CREDENZIALI ──────────────────────────────────────────────
KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_SECRET     = os.getenv("KRAKEN_SECRET", "")
OANDA_API_KEY     = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID  = os.getenv("OANDA_ACCOUNT_ID", "")
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET     = os.getenv("ALPACA_SECRET", "")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID  = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
DRY_RUN           = os.getenv("DRY_RUN", "true").lower() == "true"

# ─── MERCATI ABILITATI ────────────────────────────────────────
# Imposta true/false per attivare/disattivare ogni mercato
MARKET_CRYPTO_ENABLED = os.getenv("MARKET_CRYPTO",  "true").lower()  == "true"
MARKET_FOREX_ENABLED  = os.getenv("MARKET_FOREX",   "false").lower() == "true"
MARKET_STOCKS_ENABLED = os.getenv("MARKET_STOCKS",  "false").lower() == "true"

# ─── CRYPTO CONFIG (Kraken EUR) ───────────────────────────────
CRYPTO_CONFIG = {
    "BTC/EUR":   {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.045, "tp_pct": 0.06,  "market": "crypto"},
    "ETH/EUR":   {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.050, "tp_pct": 0.07,  "market": "crypto"},
    "SOL/EUR":   {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.055, "tp_pct": 0.08,  "market": "crypto"},
    "XRP/EUR":   {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.060, "tp_pct": 0.09,  "market": "crypto"},
    "ADA/EUR":   {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.060, "tp_pct": 0.09,  "market": "crypto"},
    "DOT/EUR":   {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.055, "tp_pct": 0.08,  "market": "crypto"},
    "LTC/EUR":   {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.050, "tp_pct": 0.07,  "market": "crypto"},
    "TRX/EUR":   {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.045, "tp_pct": 0.07,  "market": "crypto"},
    "XLM/EUR":   {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.060, "tp_pct": 0.09,  "market": "crypto"},
    "NEAR/EUR":  {"budget_eur": 600, "bb_std": 1.6, "sl_pct": 0.060, "tp_pct": 0.10,  "market": "crypto"},
    "ICP/EUR":   {"budget_eur": 600, "bb_std": 1.6, "sl_pct": 0.065, "tp_pct": 0.10,  "market": "crypto"},
    "ONDO/EUR":  {"budget_eur": 600, "bb_std": 1.6, "sl_pct": 0.065, "tp_pct": 0.10,  "market": "crypto"},
    "TAO/EUR":   {"budget_eur": 600, "bb_std": 1.5, "sl_pct": 0.065, "tp_pct": 0.11,  "market": "crypto"},
    "ZEC/EUR":   {"budget_eur": 600, "bb_std": 1.5, "sl_pct": 0.070, "tp_pct": 0.11,  "market": "crypto"},
}

# ─── FOREX CONFIG (OANDA) ─────────────────────────────────────
# Forex: spread piccoli, trend lunghi, ADX più stabile
# Strategia principale: Trend Following su 1H con filtro 4H
# SL più stretto (pips-based), TP generoso per seguire i trend
FOREX_CONFIG = {
    "EUR_USD": {"budget_eur": 30, "bb_std": 1.8, "sl_pct": 0.003, "tp_pct": 0.008, "market": "forex"},
    "GBP_USD": {"budget_eur": 25, "bb_std": 1.8, "sl_pct": 0.004, "tp_pct": 0.010, "market": "forex"},
    "USD_JPY": {"budget_eur": 25, "bb_std": 1.8, "sl_pct": 0.003, "tp_pct": 0.008, "market": "forex"},
    "EUR_GBP": {"budget_eur": 20, "bb_std": 1.6, "sl_pct": 0.003, "tp_pct": 0.007, "market": "forex"},
    "AUD_USD": {"budget_eur": 20, "bb_std": 1.8, "sl_pct": 0.004, "tp_pct": 0.009, "market": "forex"},
    "USD_CAD": {"budget_eur": 20, "bb_std": 1.8, "sl_pct": 0.004, "tp_pct": 0.009, "market": "forex"},
    "EUR_JPY": {"budget_eur": 20, "bb_std": 1.8, "sl_pct": 0.004, "tp_pct": 0.010, "market": "forex"},
    "GBP_JPY": {"budget_eur": 20, "bb_std": 1.8, "sl_pct": 0.005, "tp_pct": 0.012, "market": "forex"},
}

# ─── STOCKS CONFIG (Alpaca — azioni USA) ─────────────────────
# Stocks: trend più lenti, meno volatili delle crypto
# Strategia: Trend Following + Breakout su open/close giornalieri
# Solo azioni ad alta liquidità (S&P500)
STOCKS_CONFIG = {
    "AAPL":  {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.025, "tp_pct": 0.05, "market": "stocks"},
    "MSFT":  {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.025, "tp_pct": 0.05, "market": "stocks"},
    "GOOGL": {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.030, "tp_pct": 0.06, "market": "stocks"},
    "AMZN":  {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.030, "tp_pct": 0.06, "market": "stocks"},
    "NVDA":  {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.035, "tp_pct": 0.07, "market": "stocks"},
    "TSLA":  {"budget_eur": 600, "bb_std": 1.8, "sl_pct": 0.040, "tp_pct": 0.08, "market": "stocks"},
    "META":  {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.030, "tp_pct": 0.06, "market": "stocks"},
    "SPY":   {"budget_eur": 600, "bb_std": 2.0, "sl_pct": 0.020, "tp_pct": 0.04, "market": "stocks"},
}

# Config unificata: tutti i mercati in un unico dict
ALL_ASSETS = {}
if MARKET_CRYPTO_ENABLED:
    ALL_ASSETS.update(CRYPTO_CONFIG)
if MARKET_FOREX_ENABLED:
    ALL_ASSETS.update(FOREX_CONFIG)
if MARKET_STOCKS_ENABLED:
    ALL_ASSETS.update(STOCKS_CONFIG)

# Retrocompatibilità
def get_all_config() -> dict:
    return ALL_ASSETS

# ─── TIMEFRAME ────────────────────────────────────────────────
TIMEFRAME       = "1h"
TIMEFRAME_TREND = "4h"

# Stocks: mercato chiuso la notte — usa timeframe diversi
STOCKS_TIMEFRAME       = "1h"
STOCKS_TIMEFRAME_TREND = "4h"

# ─── INDICATORI ───────────────────────────────────────────────
BB_PERIOD    = 20
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIGNAL_P= 9
RSI_PERIOD   = 14
RSI_OVERSOLD = 38
RSI_OVERBOUGHT=68
ATR_PERIOD   = 14

# ─── SCORING ──────────────────────────────────────────────────
MIN_SCORE_BUY  = 5
VOLUME_MULT    = 1.4
BB_ENTRY_MARGIN= 0.002

# ─── TIMING ───────────────────────────────────────────────────
TRADE_COOLDOWN = 3600
# Forex non ha cooldown fisso (mercato 24/5)
FOREX_COOLDOWN = 1800
# Stocks: cooldown più lungo (mercato lento)
STOCKS_COOLDOWN= 7200

# ─── GESTIONE RISCHIO ─────────────────────────────────────────
MAX_OPEN_POSITIONS     = 20   # aumentato per multi-mercato
MAX_OPEN_CRYPTO        = 14
MAX_OPEN_FOREX         = 4
MAX_OPEN_STOCKS        = 4
MAX_DAILY_LOSS_EUR     = 25.0
TRAILING_STOP_ENABLED  = True
TRAILING_STOP_PCT      = 0.015
TP1_SELL_FRACTION      = 0.5

# ─── AGGIORNAMENTO ────────────────────────────────────────────
BANDS_UPDATE_INTERVAL  = 900
BANDS_HISTORY_CANDLES  = 200
MIN_SCORE_SELL         = 3

# ─── DCA ──────────────────────────────────────────────────────
MONTHLY_BUDGET_EUR  = 50.0
CORE_PCT            = 0.60
SATELLITE_PCT       = 0.40
DCA_EXECUTION_DAY   = 1
DCA_EXECUTION_HOUR  = 10

# ─── FILE DI PERSISTENZA ──────────────────────────────────────
POSITIONS_FILE  = "posizioni.json"
TRADES_LOG_FILE = "trades_log.json"

# ─── TELEGRAM ─────────────────────────────────────────────────
NOTIFICATION_INTERVAL = 3600

# ─── ORARI MERCATO (per stocks) ───────────────────────────────
# NYSE: 14:30-21:00 UTC (09:30-16:00 ET)
STOCKS_MARKET_OPEN_UTC  = 14  # ora apertura UTC
STOCKS_MARKET_CLOSE_UTC = 21  # ora chiusura UTC
STOCKS_TRADING_DAYS     = [0, 1, 2, 3, 4]  # Lun-Ven
