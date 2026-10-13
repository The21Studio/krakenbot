"""
persistence.py — Salvataggio e caricamento stato su disco + log dei trade
"""

import json
import os
import time
from settings import POSITIONS_FILE, TRADES_LOG_FILE, CRYPTO_CONFIG


# ─── POSIZIONI ────────────────────────────────────────────────

def load_positions() -> dict:
    """Carica le posizioni salvate. Se il file non esiste, inizializza tutto a CACCIA_APERTA."""
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                data = json.load(f)
            print(f"[PERSIST] Caricate {len(data)} posizioni dal disco.")
            return data
        except Exception as e:
            print(f"[PERSIST] Errore lettura posizioni: {e}. Reinizializzo.")
    default = {s: "CACCIA_APERTA" for s in CRYPTO_CONFIG}
    save_positions(default)
    return default


def save_positions(positions: dict):
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
    except Exception as e:
        print(f"[PERSIST] ERRORE salvataggio posizioni: {e}")


# ─── LOG TRADE ────────────────────────────────────────────────

def log_trade(entry: dict):
    """
    Aggiunge una riga al log storico dei trade.
    entry = {symbol, type, price, volume, eur_value, reason, timestamp, pnl_pct, pnl_eur}
    """
    entry.setdefault("timestamp", int(time.time()))
    trades = []
    if os.path.exists(TRADES_LOG_FILE):
        try:
            with open(TRADES_LOG_FILE, "r") as f:
                trades = json.load(f)
        except Exception:
            trades = []
    trades.append(entry)
    try:
        with open(TRADES_LOG_FILE, "w") as f:
            json.dump(trades, f, indent=2)
    except Exception as e:
        print(f"[PERSIST] ERRORE salvataggio log trade: {e}")


def load_trades() -> list:
    if not os.path.exists(TRADES_LOG_FILE):
        return []
    try:
        with open(TRADES_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


# ─── STATISTICHE GIORNALIERE ──────────────────────────────────

def get_daily_stats() -> dict:
    """Calcola P&L del giorno corrente dai trade loggati."""
    trades = load_trades()
    today  = time.strftime("%Y-%m-%d")
    daily  = [t for t in trades if time.strftime("%Y-%m-%d", time.localtime(t.get("timestamp", 0))) == today]

    # Tutti i tipi di USCITA (non solo SELL/STOP_LOSS — quello era il bug
    # che faceva vedere solo le perdite e mai i profitti)
    SELL_TYPES = ("SELL", "STOP_LOSS", "TAKE_PROFIT_1", "TAKE_PROFIT_2",
                  "TRAILING_STOP", "SMART_EXIT", "SIGNAL", "SELL_TP1")
    sells  = [t for t in daily if t.get("type") in SELL_TYPES]

    total_pnl = sum(t.get("pnl_eur", 0) for t in sells)
    wins   = sum(1 for t in sells if t.get("pnl_eur", 0) > 0)
    losses = sum(1 for t in sells if t.get("pnl_eur", 0) <= 0)
    best   = max((t.get("pnl_eur", 0) for t in sells), default=0)
    worst  = min((t.get("pnl_eur", 0) for t in sells), default=0)

    return {
        "date":       today,
        "trades":     len(sells),
        "wins":       wins,
        "losses":     losses,
        "pnl_eur":    round(total_pnl, 2),
        "win_rate":   round(wins / len(sells) * 100, 1) if sells else 0,
        "best_trade": round(best, 2),
        "worst_trade":round(worst, 2),
    }
