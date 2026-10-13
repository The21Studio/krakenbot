"""
config.py — DEPRECATO. Mantenuto solo per retrocompatibilità.
Tutta la configurazione vera è in settings.py. Questo file la ri-esporta
così nessun vecchio import si rompe e i due file non possono divergere.
"""
from settings import *          # noqa: F401,F403
from settings import (          # re-export espliciti più usati
    KRAKEN_API_KEY, KRAKEN_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    DRY_RUN, CRYPTO_CONFIG, POSITIONS_FILE, TRADES_LOG_FILE,
    TRADE_COOLDOWN, MAX_OPEN_POSITIONS, MAX_DAILY_LOSS_EUR,
    TIMEFRAME, TIMEFRAME_TREND,
)
