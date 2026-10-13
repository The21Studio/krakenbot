"""
virtual_portfolio.py — Portafoglio virtuale unificato

Simula un conto unico (Kraken crypto + Alpaca stocks) con:
- Saldo iniziale configurabile
- Deposito mensile automatico (+500€ il 1° del mese)
- P&L reale calcolato dallo storico trade
- Budget per trade adattivo al saldo corrente
- Split visivo Kraken / Alpaca
"""

import json
import os
import time
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────────
PORTFOLIO_FILE   = "virtual_portfolio.json"
INITIAL_BALANCE  = 8500.0   # saldo iniziale virtuale
MONTHLY_DEPOSIT  = 500.0    # deposito mensile automatico
CRYPTO_SPLIT_PCT = 0.60     # 60% allocato a crypto
STOCKS_SPLIT_PCT = 0.40     # 40% allocato a stocks
MAX_RISK_PER_TRADE_PCT = 0.07  # max 7% del saldo per singolo trade

# ─── PERSISTENZA ──────────────────────────────────────────────
def _load() -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return _init_portfolio()


def _save(data: dict):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _init_portfolio() -> dict:
    """Inizializza il portafoglio virtuale con i valori di partenza."""
    now = int(time.time())
    data = {
        "initial_balance":  INITIAL_BALANCE,
        "monthly_deposit":  MONTHLY_DEPOSIT,
        "created_at":       now,
        "last_deposit_month": datetime.now(timezone.utc).month,
        "last_deposit_year":  datetime.now(timezone.utc).year,
        "deposits_log": [
            {"ts": now, "amount": INITIAL_BALANCE, "note": "Deposito iniziale"}
        ],
        "total_deposited": INITIAL_BALANCE,
    }
    _save(data)
    return data


# ─── LOGICA PRINCIPALE ────────────────────────────────────────
def get_portfolio_state(trades_log: list, positions: dict, live_prices: dict) -> dict:
    """
    Calcola lo stato completo del portafoglio virtuale.
    
    Returns:
        dict con saldo, P&L, split crypto/stocks, budget per trade
    """
    data = _load()

    # ── Controlla se fare deposito mensile ───────────────────
    now_utc = datetime.now(timezone.utc)
    last_month = data.get("last_deposit_month", now_utc.month)
    last_year  = data.get("last_deposit_year",  now_utc.year)

    if (now_utc.month != last_month or now_utc.year != last_year) and now_utc.day >= 1:
        deposit_amount = data.get("monthly_deposit", MONTHLY_DEPOSIT)
        data["deposits_log"].append({
            "ts": int(time.time()),
            "amount": deposit_amount,
            "note": f"Deposito mensile {now_utc.strftime('%B %Y')}"
        })
        data["total_deposited"] = data.get("total_deposited", INITIAL_BALANCE) + deposit_amount
        data["last_deposit_month"] = now_utc.month
        data["last_deposit_year"]  = now_utc.year
        _save(data)

    # ── Calcola P&L da storico trade ─────────────────────────
    pnl_crypto = 0.0
    pnl_stocks = 0.0
    n_trades_crypto = 0
    n_trades_stocks = 0

    for t in trades_log:
        pnl = t.get("pnl_eur", 0) or 0
        symbol = t.get("symbol", "")
        if pnl == 0:
            continue
        if "/" in symbol:  # crypto (es. BTC/EUR)
            pnl_crypto += pnl
            n_trades_crypto += 1
        else:  # stocks (es. AAPL)
            pnl_stocks += pnl
            n_trades_stocks += 1

    pnl_totale = pnl_crypto + pnl_stocks

    # ── Calcola valore posizioni aperte ──────────────────────
    open_value_crypto = 0.0
    open_value_stocks = 0.0
    open_pnl_crypto   = 0.0
    open_pnl_stocks   = 0.0

    for symbol, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        if pos.get("status") != "IN_POSITION":
            continue
        entry  = pos.get("entry_price", 0)
        volume = pos.get("volume", 0)
        price  = live_prices.get(symbol, entry)
        value  = price * volume
        cost   = entry * volume
        pnl_op = value - cost

        if "/" in symbol:
            open_value_crypto += value
            open_pnl_crypto   += pnl_op
        else:
            open_value_stocks += value
            open_pnl_stocks   += pnl_op

    # ── Saldo virtuale totale ────────────────────────────────
    total_deposited = data.get("total_deposited", INITIAL_BALANCE)
    saldo_totale    = total_deposited + pnl_totale

    # Saldo disponibile (non investito in posizioni aperte)
    investito = open_value_crypto + open_value_stocks
    saldo_libero = max(0, saldo_totale - investito)

    # ── Split crypto / stocks ────────────────────────────────
    saldo_crypto = saldo_totale * CRYPTO_SPLIT_PCT
    saldo_stocks = saldo_totale * STOCKS_SPLIT_PCT

    # ── Budget adattivo per singolo trade ────────────────────
    # Si adatta al saldo totale: max 7% per trade, min 50€
    budget_per_trade = max(50.0, round(saldo_totale * MAX_RISK_PER_TRADE_PCT, 0))

    # Arrotonda a multipli di 50 per semplicità
    budget_per_trade = round(budget_per_trade / 50) * 50

    return {
        # Saldo
        "saldo_totale":      round(saldo_totale, 2),
        "saldo_libero":      round(saldo_libero, 2),
        "saldo_investito":   round(investito, 2),

        # Split conti
        "saldo_crypto":      round(saldo_crypto, 2),
        "saldo_stocks":      round(saldo_stocks, 2),

        # P&L realizzato
        "pnl_totale":        round(pnl_totale, 2),
        "pnl_crypto":        round(pnl_crypto, 2),
        "pnl_stocks":        round(pnl_stocks, 2),

        # P&L non realizzato (posizioni aperte)
        "open_pnl_crypto":   round(open_pnl_crypto, 2),
        "open_pnl_stocks":   round(open_pnl_stocks, 2),
        "open_pnl_totale":   round(open_pnl_crypto + open_pnl_stocks, 2),

        # Statistiche
        "n_trades_crypto":   n_trades_crypto,
        "n_trades_stocks":   n_trades_stocks,
        "total_deposited":   round(total_deposited, 2),

        # Budget adattivo
        "budget_per_trade":  budget_per_trade,

        # Prossimo deposito
        "prossimo_deposito": MONTHLY_DEPOSIT,
        "prossimo_deposito_data": f"1° {_next_month_name()}",

        # Rendimento
        "rendimento_pct":    round((pnl_totale / total_deposited) * 100, 2) if total_deposited > 0 else 0,

        # Log depositi
        "deposits_log":      data.get("deposits_log", [])[-6:],  # ultimi 6
    }


def get_budget_for_trade(symbol: str) -> float:
    """
    Ritorna il budget adattivo per un singolo trade basato sul saldo corrente.
    Se il portafoglio è in drawdown > 10% dal massimo, dimezza il budget.
    """
    data = _load()
    total_deposited = data.get("total_deposited", INITIAL_BALANCE)

    pnl = 0.0
    if os.path.exists("trades_log.json"):
        try:
            trades = json.load(open("trades_log.json"))
            pnl = sum(t.get("pnl_eur", 0) or 0 for t in trades)
        except Exception:
            pass

    saldo = total_deposited + pnl

    # Calcola drawdown dal massimo storico
    max_balance = data.get("max_balance", saldo)
    if saldo > max_balance:
        data["max_balance"] = saldo
        _save(data)
        max_balance = saldo

    drawdown_pct = (max_balance - saldo) / max_balance * 100 if max_balance > 0 else 0

    budget = max(50.0, round(saldo * MAX_RISK_PER_TRADE_PCT / 50) * 50)

    # Punto 5: se drawdown > 10% dimezza il budget
    if drawdown_pct > 10:
        budget = max(50.0, budget * 0.5)
        print(f"[PORTFOLIO] Drawdown {drawdown_pct:.1f}% — budget dimezzato a {budget:.0f}€")

    return budget


def _next_month_name() -> str:
    months = ["Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
              "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
    now = datetime.now(timezone.utc)
    next_month = (now.month % 12)  # 0-indexed for next month
    return months[next_month]


def init_if_needed():
    """Inizializza il portafoglio se non esiste ancora."""
    if not os.path.exists(PORTFOLIO_FILE):
        _init_portfolio()
        print(f"[PORTFOLIO] Portafoglio virtuale inizializzato: {INITIAL_BALANCE}€")
