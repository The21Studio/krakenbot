"""
dca.py — DCA intelligente basato su rilevamento depositi reali

Il bot controlla i movimenti del ledger Kraken ogni ora.
Quando rileva un deposito EUR (tipo "deposit"), esegue
automaticamente la redistribuzione Core + Satellite.

Questo approccio è sicuro perché:
- Legge solo movimenti di tipo "deposit" dal ledger Kraken
- Ignora completamente guadagni da trade, commissioni, ecc.
- Non si attiva mai per variazioni di saldo dovute al trading
"""

import json
import os
import time
import asyncio

from settings import (
    DRY_RUN, CORE_PCT, SATELLITE_PCT,
)
from persistence import log_trade

# ─── CONFIGURAZIONE ───────────────────────────────────────────

# Importo minimo per considerarlo un deposito intenzionale
# (filtra micro-depositi o errori)
MIN_DEPOSIT_EUR    = 10.0

# Intervallo controllo ledger (secondi)
CHECK_INTERVAL     = 3600   # ogni ora

CORE_ASSETS = {
    "BTC/EUR": 0.65,
    "ETH/EUR": 0.35,
}

DCA_FILE           = "dca_state.json"
DCA_PORTFOLIO_FILE = "dca_portfolio.json"


# ─── PERSISTENZA ──────────────────────────────────────────────

def load_dca_state() -> dict:
    if os.path.exists(DCA_FILE):
        try:
            with open(DCA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_ledger_id":    None,   # ultimo ID ledger processato
        "last_check":        None,
        "total_invested":    0.0,
        "total_executions":  0,
        "satellite_balance": 0.0,
        "deposits_detected": [],
    }


def save_dca_state(state: dict):
    try:
        with open(DCA_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[DCA] Errore salvataggio: {e}")


def load_dca_portfolio() -> dict:
    if os.path.exists(DCA_PORTFOLIO_FILE):
        try:
            with open(DCA_PORTFOLIO_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "BTC/EUR": {"units": 0.0, "total_eur_spent": 0.0, "avg_price": 0.0, "purchases": []},
        "ETH/EUR": {"units": 0.0, "total_eur_spent": 0.0, "avg_price": 0.0, "purchases": []},
    }


def save_dca_portfolio(portfolio: dict):
    try:
        with open(DCA_PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=2)
    except Exception as e:
        print(f"[DCA] Errore salvataggio portfolio: {e}")


# ─── STATISTICHE ──────────────────────────────────────────────

def get_portfolio_stats(portfolio: dict, live_prices: dict) -> dict:
    stats = {}
    total_invested = 0.0
    total_value    = 0.0

    for asset, data in portfolio.items():
        if data["units"] <= 0:
            continue
        live_price  = live_prices.get(asset, data["avg_price"])
        current_val = data["units"] * live_price
        invested    = data["total_eur_spent"]
        pnl_eur     = current_val - invested
        pnl_pct     = (pnl_eur / invested * 100) if invested > 0 else 0.0

        stats[asset] = {
            "units":        round(data["units"], 8),
            "avg_price":    round(data["avg_price"], 2),
            "live_price":   round(live_price, 2),
            "invested_eur": round(invested, 2),
            "current_val":  round(current_val, 2),
            "pnl_eur":      round(pnl_eur, 2),
            "pnl_pct":      round(pnl_pct, 2),
            "purchases":    len(data["purchases"]),
        }
        total_invested += invested
        total_value    += current_val

    stats["_totals"] = {
        "invested": round(total_invested, 2),
        "value":    round(total_value, 2),
        "pnl_eur":  round(total_value - total_invested, 2),
        "pnl_pct":  round((total_value - total_invested) / total_invested * 100, 2)
                    if total_invested > 0 else 0.0,
    }
    return stats


# ─── LETTURA DEPOSITI DAL LEDGER ──────────────────────────────

async def _get_new_eur_deposits(exchange, since_id: str | None) -> list[dict]:
    """
    Legge il ledger Kraken e ritorna solo i depositi EUR
    più recenti dell'ultimo ID processato.
    """
    if DRY_RUN:
        return []   # in dry run non ci sono depositi reali

    try:
        # Fetch ledger degli ultimi movimenti
        result = await exchange.fetch_ledger(code="EUR", limit=50)
        deposits = []

        for entry in result:
            # Salta tutto ciò che non è un deposito esterno
            if entry.get("type") not in ("deposit",):
                continue

            amount = float(entry.get("amount", 0))
            if amount < MIN_DEPOSIT_EUR:
                continue

            entry_id = entry.get("id", "")

            # Se abbiamo già processato questo o precedenti, fermati
            if since_id and entry_id == since_id:
                break

            deposits.append({
                "id":     entry_id,
                "amount": amount,
                "date":   entry.get("datetime", time.strftime("%Y-%m-%d %H:%M")),
            })

        return deposits

    except Exception as e:
        print(f"[DCA] Errore lettura ledger: {e}")
        return []


# ─── ESECUZIONE DCA ───────────────────────────────────────────

async def execute_dca(
    exchange,
    deposit_amount: float,
    deposit_date:   str,
    live_prices:    dict,
    send_telegram:  callable,
) -> dict:
    """Redistribuisce il deposito in Core + Satellite."""
    state     = load_dca_state()
    portfolio = load_dca_portfolio()

    core_eur = deposit_amount * CORE_PCT
    sat_eur  = deposit_amount * SATELLITE_PCT

    print(f"[DCA] Deposito {deposit_amount:.2f}€ → Core: {core_eur:.2f}€ | Satellite: {sat_eur:.2f}€")

    results = {}

    for asset, alloc_pct in CORE_ASSETS.items():
        eur_amount = core_eur * alloc_pct
        live_price = live_prices.get(asset)

        if not live_price or live_price <= 0:
            print(f"[DCA] Prezzo non disponibile per {asset}")
            continue

        units = eur_amount / live_price

        if DRY_RUN:
            exec_price = live_price
        else:
            try:
                precise    = float(exchange.amount_to_precision(asset, units))
                order      = await exchange.create_market_order(asset, "buy", precise)
                exec_price = float(order.get("price", live_price) or live_price)
            except Exception as e:
                print(f"[DCA] Errore acquisto {asset}: {e}")
                continue

        p = portfolio[asset]
        p["units"]           += units
        p["total_eur_spent"] += eur_amount
        p["avg_price"]        = p["total_eur_spent"] / p["units"]
        p["purchases"].append({
            "date":       deposit_date,
            "price":      exec_price,
            "units":      units,
            "eur_amount": eur_amount,
        })

        results[asset] = {
            "units":      round(units, 8),
            "price":      exec_price,
            "eur_amount": round(eur_amount, 2),
            "avg_price":  round(p["avg_price"], 2),
        }

        log_trade({
            "symbol":    asset,
            "type":      "DCA_BUY",
            "price":     exec_price,
            "volume":    units,
            "eur_value": eur_amount,
            "reason":    f"DCA deposito {deposit_amount:.2f}€ del {deposit_date}",
        })

    state["satellite_balance"] += sat_eur
    state["total_invested"]    += deposit_amount
    state["total_executions"]  += 1
    state["deposits_detected"].append({
        "date":   deposit_date,
        "amount": round(deposit_amount, 2),
    })

    save_dca_portfolio(portfolio)
    save_dca_state(state)

    await _notify_dca(send_telegram, results, state, sat_eur, deposit_amount)
    return results


async def _notify_dca(send_telegram, results, state, sat_eur, deposit_amount):
    dry   = " <i>[DRY RUN]</i>" if DRY_RUN else ""
    lines = [
        f"💰 <b>DEPOSITO RILEVATO — DCA ESEGUITO{dry}</b>\n",
        f"Deposito: <b>{deposit_amount:.2f}€</b>\n",
    ]
    for asset, r in results.items():
        lines.append(
            f"<b>{asset.split('/')[0]}</b> — {r['units']:.6f} unità "
            f"@ {r['price']:.2f}€ = {r['eur_amount']:.2f}€\n"
        )
    lines.append(f"<b>Satellite:</b> +{sat_eur:.2f}€ per trading")
    lines.append(f"<b>Totale investito:</b> {state['total_invested']:.2f}€")
    try:
        await send_telegram("\n".join(lines))
    except Exception as e:
        print(f"[DCA] Errore notifica: {e}")


# ─── LOOP PRINCIPALE ──────────────────────────────────────────

async def dca_loop(exchange, shared_state: dict, send_telegram: callable):
    """
    Controlla ogni ora il ledger Kraken.
    Se trova nuovi depositi EUR esegue il DCA.
    """
    print(f"[DCA] Loop avviato — rilevamento depositi attivo (controllo ogni {CHECK_INTERVAL//3600}h)")
    await asyncio.sleep(30)

    while True:
        try:
            state    = load_dca_state()
            last_id  = state.get("last_ledger_id")
            deposits = await _get_new_eur_deposits(exchange, last_id)

            if deposits:
                print(f"[DCA] {len(deposits)} nuovo/i deposito/i rilevato/i")
                for dep in deposits:
                    await execute_dca(
                        exchange,
                        dep["amount"],
                        dep["date"],
                        shared_state.get("live_prices", {}),
                        send_telegram,
                    )
                # Salva l'ID più recente processato
                state = load_dca_state()
                state["last_ledger_id"] = deposits[0]["id"]
                state["last_check"]     = time.strftime("%Y-%m-%d %H:%M")
                save_dca_state(state)
            else:
                state["last_check"] = time.strftime("%Y-%m-%d %H:%M")
                save_dca_state(state)
                print(f"[DCA] Nessun nuovo deposito EUR rilevato")

        except Exception as e:
            print(f"[DCA] Errore loop: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# ─── REPORT ───────────────────────────────────────────────────

def format_portfolio_report(live_prices: dict) -> str:
    portfolio = load_dca_portfolio()
    state     = load_dca_state()
    stats     = get_portfolio_stats(portfolio, live_prices)

    if not stats.get("_totals") or stats["_totals"]["invested"] == 0:
        return (
            "📊 <b>Portfolio CORE</b>\n\n"
            "In attesa del primo deposito EUR su Kraken.\n"
            "Quando depositi, il bot rileva automaticamente\n"
            "il movimento e redistribuisce: 60% BTC+ETH, 40% trading."
        )

    t    = stats["_totals"]
    dry  = " [DRY RUN]" if DRY_RUN else ""
    deps = state.get("deposits_detected", [])
    lines = [f"📊 <b>Portfolio CORE{dry}</b>\n"]

    for asset in ["BTC/EUR", "ETH/EUR"]:
        s = stats.get(asset)
        if not s:
            continue
        emoji = "📈" if s["pnl_pct"] >= 0 else "📉"
        lines.append(
            f"<b>{asset}</b> ({s['purchases']} acquisti)\n"
            f"  Unità: {s['units']:.6f} · Prezzo medio: {s['avg_price']:.2f}€\n"
            f"  Valore: {s['current_val']:.2f}€ · P&L: {emoji} {s['pnl_pct']:+.2f}%\n"
        )

    emoji_tot = "📈" if t["pnl_eur"] >= 0 else "📉"
    lines.append(
        f"<b>Totale investito:</b> {t['invested']:.2f}€\n"
        f"<b>Valore attuale:</b> {t['value']:.2f}€\n"
        f"<b>P&L:</b> {emoji_tot} {t['pnl_pct']:+.2f}% ({t['pnl_eur']:+.2f}€)\n"
        f"<b>Depositi processati:</b> {len(deps)}"
    )
    return "\n".join(lines)

import json
import os
import time
import asyncio

from settings import (
    DRY_RUN, KRAKEN_API_KEY, KRAKEN_SECRET,
    MONTHLY_BUDGET_EUR, CORE_PCT, SATELLITE_PCT,
)
from persistence import log_trade

# ─── CONFIGURAZIONE ───────────────────────────────────────────

# Importo minimo per considerarlo un deposito (evita falsi positivi
# dovuti a piccole variazioni di saldo da commissioni o trade)
MIN_DEPOSIT_EUR = 20.0

# Quante ore aspettare tra un controllo e l'altro
CHECK_INTERVAL_HOURS = 1

# Allocazione del CORE
CORE_ASSETS = {
    "BTC/EUR": 0.65,   # 65% del core
    "ETH/EUR": 0.35,   # 35% del core
}

DCA_FILE           = "dca_state.json"
DCA_PORTFOLIO_FILE = "dca_portfolio.json"


# ─── PERSISTENZA ──────────────────────────────────────────────

def load_dca_state() -> dict:
    if os.path.exists(DCA_FILE):
        try:
            with open(DCA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_eur_balance":  None,   # ultimo saldo EUR rilevato
        "last_check":        None,   # timestamp ultimo controllo
        "total_invested":    0.0,
        "total_executions":  0,
        "satellite_balance": 0.0,
        "deposits_detected": [],     # storico depositi rilevati
    }


def save_dca_state(state: dict):
    try:
        with open(DCA_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[DCA] Errore salvataggio stato: {e}")


def load_dca_portfolio() -> dict:
    if os.path.exists(DCA_PORTFOLIO_FILE):
        try:
            with open(DCA_PORTFOLIO_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "BTC/EUR": {"units": 0.0, "total_eur_spent": 0.0, "avg_price": 0.0, "purchases": []},
        "ETH/EUR": {"units": 0.0, "total_eur_spent": 0.0, "avg_price": 0.0, "purchases": []},
    }


def save_dca_portfolio(portfolio: dict):
    try:
        with open(DCA_PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=2)
    except Exception as e:
        print(f"[DCA] Errore salvataggio portfolio: {e}")


# ─── STATISTICHE PORTFOLIO ────────────────────────────────────

def get_portfolio_stats(portfolio: dict, live_prices: dict) -> dict:
    stats = {}
    total_invested = 0.0
    total_value    = 0.0

    for asset, data in portfolio.items():
        if data["units"] <= 0:
            continue
        live_price  = live_prices.get(asset, data["avg_price"])
        current_val = data["units"] * live_price
        invested    = data["total_eur_spent"]
        pnl_eur     = current_val - invested
        pnl_pct     = (pnl_eur / invested * 100) if invested > 0 else 0.0

        stats[asset] = {
            "units":        round(data["units"], 8),
            "avg_price":    round(data["avg_price"], 2),
            "live_price":   round(live_price, 2),
            "invested_eur": round(invested, 2),
            "current_val":  round(current_val, 2),
            "pnl_eur":      round(pnl_eur, 2),
            "pnl_pct":      round(pnl_pct, 2),
            "purchases":    len(data["purchases"]),
        }
        total_invested += invested
        total_value    += current_val

    stats["_totals"] = {
        "invested": round(total_invested, 2),
        "value":    round(total_value, 2),
        "pnl_eur":  round(total_value - total_invested, 2),
        "pnl_pct":  round((total_value - total_invested) / total_invested * 100, 2)
                    if total_invested > 0 else 0.0,
    }
    return stats


# ─── LETTURA SALDO EUR ────────────────────────────────────────

async def _get_eur_balance(exchange) -> float:
    """Legge il saldo EUR reale da Kraken."""
    if DRY_RUN:
        return 999.0
    try:
        balances = await exchange.fetch_balance()
        return float(balances.get("EUR", {}).get("free", 0))
    except Exception as e:
        print(f"[DCA] Errore lettura saldo EUR: {e}")
        return 0.0


# ─── ESECUZIONE DCA ───────────────────────────────────────────

async def execute_dca(
    exchange,
    deposit_amount: float,
    live_prices:    dict,
    send_telegram:  callable,
) -> dict:
    """
    Esegue la redistribuzione dopo un deposito rilevato.
    deposit_amount = EUR aggiuntivi rilevati sul conto.
    """
    state     = load_dca_state()
    portfolio = load_dca_portfolio()

    core_eur = deposit_amount * CORE_PCT       # es. 30€
    sat_eur  = deposit_amount * SATELLITE_PCT  # es. 20€

    print(f"[DCA] Deposito rilevato: {deposit_amount:.2f}€ → Core: {core_eur:.2f}€ | Satellite: {sat_eur:.2f}€")

    results = {}

    for asset, alloc_pct in CORE_ASSETS.items():
        eur_amount = core_eur * alloc_pct
        live_price = live_prices.get(asset)

        if not live_price or live_price <= 0:
            print(f"[DCA] Prezzo non disponibile per {asset}")
            continue

        units = eur_amount / live_price

        if DRY_RUN:
            exec_price = live_price
            print(f"[DCA][DRY] Compro {units:.8f} {asset} @ {exec_price:.2f} = {eur_amount:.2f}€")
        else:
            try:
                precise = float(exchange.amount_to_precision(asset, units))
                order   = await exchange.create_market_order(asset, "buy", precise)
                exec_price = float(order.get("price", live_price) or live_price)
                print(f"[DCA] Acquistato {precise} {asset} @ {exec_price:.2f}")
            except Exception as e:
                print(f"[DCA] Errore acquisto {asset}: {e}")
                continue

        # Aggiorna portfolio
        p = portfolio[asset]
        p["units"]           += units
        p["total_eur_spent"] += eur_amount
        p["avg_price"]        = p["total_eur_spent"] / p["units"]
        p["purchases"].append({
            "date":       time.strftime("%Y-%m-%d %H:%M"),
            "price":      exec_price,
            "units":      units,
            "eur_amount": eur_amount,
        })

        results[asset] = {
            "units":      round(units, 8),
            "price":      exec_price,
            "eur_amount": round(eur_amount, 2),
            "avg_price":  round(p["avg_price"], 2),
        }

        log_trade({
            "symbol":    asset,
            "type":      "DCA_BUY",
            "price":     exec_price,
            "volume":    units,
            "eur_value": eur_amount,
            "reason":    f"DCA da deposito #{state['total_executions'] + 1} ({deposit_amount:.2f}€)",
        })

    # Aggiorna stato
    state["satellite_balance"] += sat_eur
    state["total_invested"]    += deposit_amount
    state["total_executions"]  += 1
    state["deposits_detected"].append({
        "date":   time.strftime("%Y-%m-%d %H:%M"),
        "amount": round(deposit_amount, 2),
    })

    save_dca_portfolio(portfolio)
    save_dca_state(state)

    # Notifica Telegram
    await _notify_dca(send_telegram, results, state, sat_eur, deposit_amount)

    return results


async def _notify_dca(send_telegram, results, state, sat_eur, deposit_amount):
    dry = " <i>[DRY RUN]</i>" if DRY_RUN else ""
    lines = [
        f"💰 <b>DEPOSITO RILEVATO{dry}</b>\n",
        f"Importo: <b>{deposit_amount:.2f}€</b>\n",
        f"<b>Esecuzione #{state['total_executions']}</b>\n",
    ]
    for asset, r in results.items():
        lines.append(
            f"<b>{asset.split('/')[0]}</b> — {r['units']:.6f} unità "
            f"@ {r['price']:.2f}€ = {r['eur_amount']:.2f}€ "
            f"(media: {r['avg_price']:.2f}€)\n"
        )
    lines.append(f"\n<b>Satellite:</b> +{sat_eur:.2f}€ disponibili per trading")
    lines.append(f"<b>Totale investito:</b> {state['total_invested']:.2f}€")

    try:
        await send_telegram("\n".join(lines))
    except Exception as e:
        print(f"[DCA] Errore notifica: {e}")


# ─── LOOP PRINCIPALE ──────────────────────────────────────────

async def dca_loop(exchange, shared_state: dict, send_telegram: callable):
    """
    Controlla ogni ora il saldo EUR su Kraken.
    Se rileva un aumento >= MIN_DEPOSIT_EUR esegue il DCA.
    """
    print(f"[DCA] Loop avviato — controllo saldo ogni {CHECK_INTERVAL_HOURS}h")
    await asyncio.sleep(30)  # aspetta avvio bot

    while True:
        try:
            state       = load_dca_state()
            eur_balance = await _get_eur_balance(exchange)
            last_bal    = state.get("last_eur_balance")

            if eur_balance > 0:
                if last_bal is not None:
                    increase = eur_balance - last_bal

                    if increase >= MIN_DEPOSIT_EUR:
                        print(f"[DCA] ✅ Deposito rilevato: +{increase:.2f}€ "
                              f"(da {last_bal:.2f}€ a {eur_balance:.2f}€)")
                        await execute_dca(
                            exchange,
                            increase,
                            shared_state.get("live_prices", {}),
                            send_telegram,
                        )
                        # Aggiorna il saldo di riferimento dopo il DCA
                        state = load_dca_state()
                        state["last_eur_balance"] = await _get_eur_balance(exchange)
                        state["last_check"]       = time.strftime("%Y-%m-%d %H:%M")
                        save_dca_state(state)
                    else:
                        # Aggiorna il saldo di riferimento normalmente
                        state["last_eur_balance"] = eur_balance
                        state["last_check"]       = time.strftime("%Y-%m-%d %H:%M")
                        save_dca_state(state)
                        print(f"[DCA] Saldo EUR: {eur_balance:.2f}€ "
                              f"(variazione: {increase:+.2f}€ — nessun deposito)")
                else:
                    # Prima lettura — salva il saldo iniziale
                    print(f"[DCA] Prima lettura saldo: {eur_balance:.2f}€")
                    state["last_eur_balance"] = eur_balance
                    state["last_check"]       = time.strftime("%Y-%m-%d %H:%M")
                    save_dca_state(state)

        except Exception as e:
            print(f"[DCA] Errore loop: {e}")

        await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)


# ─── REPORT PORTFOLIO ─────────────────────────────────────────

def format_portfolio_report(live_prices: dict) -> str:
    portfolio = load_dca_portfolio()
    state     = load_dca_state()
    stats     = get_portfolio_stats(portfolio, live_prices)

    if not stats.get("_totals") or stats["_totals"]["invested"] == 0:
        return (
            "📊 <b>Portfolio CORE</b>\n\n"
            "Nessun deposito rilevato ancora.\n"
            f"Il bot monitora il saldo EUR su Kraken ogni {CHECK_INTERVAL_HOURS}h.\n"
            "Quando depositi, esegue automaticamente la redistribuzione."
        )

    t    = stats["_totals"]
    dry  = " [DRY RUN]" if DRY_RUN else ""
    deps = state.get("deposits_detected", [])
    lines = [f"📊 <b>Portfolio CORE{dry}</b>\n"]

    for asset in ["BTC/EUR", "ETH/EUR"]:
        s = stats.get(asset)
        if not s:
            continue
        emoji = "📈" if s["pnl_pct"] >= 0 else "📉"
        lines.append(
            f"<b>{asset}</b> ({s['purchases']} acquisti)\n"
            f"  Unità: {s['units']:.6f}\n"
            f"  Prezzo medio: {s['avg_price']:.2f}€\n"
            f"  Valore: {s['current_val']:.2f}€\n"
            f"  P&L: {emoji} {s['pnl_pct']:+.2f}% ({s['pnl_eur']:+.2f}€)\n"
        )

    emoji_tot = "📈" if t["pnl_eur"] >= 0 else "📉"
    lines.append(
        f"<b>Totale investito:</b> {t['invested']:.2f}€\n"
        f"<b>Valore attuale:</b> {t['value']:.2f}€\n"
        f"<b>P&L:</b> {emoji_tot} {t['pnl_pct']:+.2f}% ({t['pnl_eur']:+.2f}€)\n"
        f"<b>Depositi rilevati:</b> {len(deps)}\n"
        f"<b>Satellite disponibile:</b> {state['satellite_balance']:.2f}€"
    )
    return "\n".join(lines)
