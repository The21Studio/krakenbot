"""
bot.py — Orchestratore principale del bot v6.0 MULTI-MERCATO

Architettura:
  • Loop asincrono per ogni asset (crypto, forex, stocks)
  • Layer di astrazione multi-mercato (market_connector.py)
  • Strategia short in BEAR_MACRO (short_strategy.py)
  • SHARED_STATE thread-safe tra tutte le coroutine
"""

import asyncio
import time
import sys
import os
import json

try:
    import ccxt.pro as ccxtpro
except ImportError:
    print("ERRORE: ccxt non installato. Esegui: pip install ccxt")
    sys.exit(1)

import pandas as pd

from settings import (
    KRAKEN_API_KEY, KRAKEN_SECRET, CRYPTO_CONFIG, STOCKS_CONFIG, ALL_ASSETS,
    TIMEFRAME, TIMEFRAME_TREND, BANDS_UPDATE_INTERVAL, BANDS_HISTORY_CANDLES,
    NOTIFICATION_INTERVAL, DRY_RUN, TP1_SELL_FRACTION,
    TRAILING_STOP_ENABLED, TRADE_COOLDOWN,
    MARKET_CRYPTO_ENABLED, MARKET_FOREX_ENABLED, MARKET_STOCKS_ENABLED,
    MAX_OPEN_CRYPTO, MAX_OPEN_FOREX, MAX_OPEN_STOCKS,
)
from indicators      import add_all, get_snapshot
from strategy        import score_buy, score_sell, can_buy, market_is_healthy, get_asset_regime
from regime          import detect_regime, REGIME_CONFIGS
from context         import context_loop, load_context
from persistence     import load_positions, save_positions, log_trade, get_daily_stats
from notifier        import (notify_start, notify_buy, notify_sell,
                              notify_daily_report, notify_daily_loss_limit, notify_error)
from dca             import dca_loop, format_portfolio_report, load_dca_portfolio, get_portfolio_stats
from market_connector import init_connectors, get_connector
from short_strategy  import score_short, can_short, score_short_exit, SHORT_SIZE_PCT
from screener        import screener_loop, get_watchlist, update_watchlist, ALWAYS_INCLUDE_CRYPTO, ALWAYS_INCLUDE_STOCKS

# ─── PERSISTENZA COOLDOWN (sopravvive ai riavvii) ─────────────
def _load_cooldowns() -> dict:
    """Carica i cooldown salvati, scartando quelli scaduti."""
    try:
        if os.path.exists("cooldowns.json"):
            d = json.load(open("cooldowns.json"))
            now = time.time()
            return {k: v for k, v in d.items() if v > now}
    except Exception:
        pass
    return {}


def _save_cooldowns():
    """Salva i cooldown su disco così non si azzerano al riavvio."""
    try:
        json.dump(SHARED_STATE.get("last_trade_time", {}),
                  open("cooldowns.json", "w"))
    except Exception:
        pass


# ─── STATO CONDIVISO ──────────────────────────────────────────
SHARED_STATE = {
    "positions":        {},
    "snapshots":        {},
    "snapshots4h":      {},
    "live_prices":      {},
    "last_trade_time":  _load_cooldowns(),
    "daily_loss_eur":   0.0,
    "weekly_loss_eur":  0.0,
    "last_daily_reset": time.strftime("%Y-%m-%d"),
    "context":          {},
    "stop_loss_count":  {},
    "last_exit_type":   {},
    "recent_stop_losses": [],
    "circuit_breaker_until": 0,
    "server_health":    {},
    # Multi-mercato
    "market_status":    {
        "crypto": {"active": MARKET_CRYPTO_ENABLED, "positions": 0},
        "forex":  {"active": MARKET_FOREX_ENABLED,  "positions": 0},
        "stocks": {"active": MARKET_STOCKS_ENABLED, "positions": 0},
    },
}

# ─── EXCHANGE ─────────────────────────────────────────────────
exchange = ccxtpro.kraken({
    "apiKey":          KRAKEN_API_KEY,
    "secret":          KRAKEN_SECRET,
    "enableRateLimit": True,
    "options":         {"nonce": "milliseconds"},
})


# ─── TELEGRAM (livello di modulo, usabile da ogni coroutine) ──
async def _telegram_send(text, parse_mode="HTML"):
    """Invia un messaggio Telegram. Sicuro: non solleva mai eccezioni."""
    try:
        from notifier import get_bot
        from settings import TELEGRAM_CHAT_ID
        bot = get_bot()
        if bot:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID,
                                   text=text, parse_mode=parse_mode)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# HELPER: CALCOLO BANDE PER UN ASSET
# ══════════════════════════════════════════════════════════════

async def _fetch_and_compute(symbol: str, cfg: dict):
    """Scarica le candele 1H e 4H e calcola tutti gli indicatori.
    Usa il connettore giusto per ogni mercato (crypto/forex/stocks)."""
    market    = cfg.get("market", "crypto")
    connector = get_connector(symbol, exchange)
    tf        = TIMEFRAME
    tf4h      = TIMEFRAME_TREND

    if not connector:
        print(f"[BANDS] Nessun connettore per {symbol}")
        return

    # ── Timeframe principale (1H) ─────────────────────────────
    try:
        if market == "crypto":
            df = await asyncio.wait_for(
                _kraken_ohlcv(symbol, tf, BANDS_HISTORY_CANDLES), timeout=15
            )
        else:
            df = await asyncio.wait_for(
                connector.fetch_ohlcv(symbol, tf, BANDS_HISTORY_CANDLES), timeout=15
            )
        if df is not None and not df.empty:
            df = add_all(df)
            snap = get_snapshot(df, cfg["bb_std"])
            if len(df) >= 3:
                snap["atr_expanding"] = float(df["atr"].iloc[-2]) > float(df["atr"].iloc[-4])
            SHARED_STATE["snapshots"][symbol] = snap
    except asyncio.TimeoutError:
        print(f"[BANDS] Timeout 1H {symbol}")
    except Exception as e:
        print(f"[BANDS] Errore 1H {symbol}: {type(e).__name__}: {e}")

    # ── Timeframe trend (4H) ──────────────────────────────────
    try:
        if market == "crypto":
            df4h = await asyncio.wait_for(
                _kraken_ohlcv(symbol, tf4h, 100), timeout=15
            )
        else:
            df4h = await asyncio.wait_for(
                connector.fetch_ohlcv(symbol, tf4h, 100), timeout=15
            )
        if df4h is not None and not df4h.empty:
            df4h = add_all(df4h)
            snap4h = get_snapshot(df4h, cfg["bb_std"])
            SHARED_STATE["snapshots4h"][symbol] = snap4h
    except asyncio.TimeoutError:
        print(f"[BANDS] Timeout 4H {symbol}")
    except Exception as e:
        print(f"[BANDS] Errore 4H {symbol}: {type(e).__name__}")


async def _kraken_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Helper per fetch OHLCV da Kraken via ccxt.pro."""
    ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
    return df


# ══════════════════════════════════════════════════════════════
# HELPER: ESECUZIONE ORDINI
# ══════════════════════════════════════════════════════════════

async def _place_order(symbol: str, side: str, volume: float, connector=None) -> dict:
    """
    Piazza un ordine market sul mercato corretto.
    Crypto → Kraken, Stocks → Alpaca, In DRY_RUN simula.
    """
    if DRY_RUN:
        price = SHARED_STATE["live_prices"].get(symbol, 0)
        print(f"[DRY RUN] {side.upper()} {volume:.6f} {symbol} @ {price:.4f}")
        return {"dry_run": True, "price": price, "volume": volume}

    # Stocks: usa Alpaca
    if connector and hasattr(connector, 'place_order') and connector.market == "stocks":
        order = await connector.place_order(symbol, side, volume)
        return order

    # Crypto: usa Kraken
    precise = float(exchange.amount_to_precision(symbol, volume))
    if precise <= 0:
        raise ValueError(f"Volume non valido: {precise}")
    order = await exchange.create_market_order(symbol, side, precise)
    return order


async def _get_asset_balance(symbol: str) -> float:
    """Ritorna il saldo libero della valuta base (es. BTC da BTC/EUR)."""
    base = symbol.split("/")[0]
    if DRY_RUN:
        pos = SHARED_STATE["positions"].get(symbol, {})
        return pos.get("volume", 0.0) if isinstance(pos, dict) else 0.0
    balances = await exchange.fetch_balance()
    return float(balances.get(base, {}).get("free", 0))


async def _get_eur_balance() -> float:
    if DRY_RUN:
        return 999.0   # in dry run non limitiamo per budget
    balances = await exchange.fetch_balance()
    return float(balances.get("EUR", {}).get("free", 0))


# ══════════════════════════════════════════════════════════════
# LOOP: RESET GIORNALIERO
# ══════════════════════════════════════════════════════════════

async def _daily_reset_loop():
    """Azzera le perdite giornaliere a mezzanotte e settimanali il lunedì."""
    while True:
        today    = time.strftime("%Y-%m-%d")
        weekday  = int(time.strftime("%w"))  # 0=domenica, 1=lunedì

        if today != SHARED_STATE["last_daily_reset"]:
            SHARED_STATE["daily_loss_eur"]  = 0.0
            SHARED_STATE["last_daily_reset"] = today
            print(f"[SYSTEM] Reset giornaliero — {today}")

            # Reset settimanale ogni lunedì
            if weekday == 1:
                SHARED_STATE["weekly_loss_eur"] = 0.0
                print(f"[SYSTEM] Reset settimanale — {today}")

            # Backup automatico giornaliero
            try:
                import shutil, os
                backup_dir = "/root/bot/backups"
                os.makedirs(backup_dir, exist_ok=True)
                for fname in ["posizioni.json","trades_log.json","virtual_portfolio.json","screener_state.json"]:
                    if os.path.exists(f"/root/bot/{fname}"):
                        shutil.copy(f"/root/bot/{fname}",
                                   f"{backup_dir}/{today}_{fname}")
                # Tieni solo gli ultimi 7 backup
                backups = sorted(os.listdir(backup_dir))
                dates   = sorted(set(b[:10] for b in backups))
                for old_date in dates[:-7]:
                    for b in backups:
                        if b.startswith(old_date):
                            os.remove(f"{backup_dir}/{b}")
                print(f"[SYSTEM] Backup completato in {backup_dir}")
            except Exception as e:
                print(f"[SYSTEM] Errore backup: {e}")

        # Monitoring salute server ogni 5 minuti
        if int(time.time()) % 300 < 60:
            try:
                import psutil
                cpu  = psutil.cpu_percent(interval=1)
                mem  = psutil.virtual_memory().percent
                disk = psutil.disk_usage("/").percent
                if cpu > 90 or mem > 90 or disk > 90:
                    print(f"[SYSTEM] ⚠️ Risorse alte: CPU {cpu}% MEM {mem}% DISK {disk}%")
                    SHARED_STATE["server_health"] = {"cpu": cpu, "mem": mem, "disk": disk, "ts": int(time.time())}
            except ImportError:
                pass  # psutil non installato, skip

        await asyncio.sleep(60)


# ══════════════════════════════════════════════════════════════
# LOOP: AGGIORNAMENTO BANDE (ogni 10 minuti)
# ══════════════════════════════════════════════════════════════

async def _bands_updater_loop():
    """Ricalcola gli indicatori per tutti gli asset in watchlist ogni 15 min."""
    await asyncio.sleep(2)
    while True:
        try:
            watchlist = get_watchlist()
            print(f"[BANDS] Aggiornamento indicatori per {len(watchlist)} asset...")
            for symbol, cfg in watchlist.items():
                market = cfg.get("market", "crypto")
                # Per stocks: aggiorna solo se NYSE è aperto
                if market == "stocks":
                    connector = get_connector(symbol, exchange)
                    if not connector or not connector.is_market_open(symbol):
                        continue  # skip: mercato chiuso
                await _fetch_and_compute(symbol, cfg)
                await asyncio.sleep(0.3)
            print("[BANDS] Aggiornamento completato.")
        except Exception as e:
            print(f"[BANDS] Errore: {e}")
        await asyncio.sleep(BANDS_UPDATE_INTERVAL)


async def _stocks_market_open_watcher():
    """
    Watcher che rileva l'apertura di NYSE e carica subito gli indicatori stocks.
    Così al momento dell'apertura i dati sono già pronti per il trading.
    """
    was_open = False
    while True:
        try:
            from market_connector import AlpacaConnector
            from settings import MARKET_STOCKS_ENABLED
            if not MARKET_STOCKS_ENABLED:
                await asyncio.sleep(300)
                continue

            connector = AlpacaConnector()
            is_open = connector.is_market_open("AAPL")

            # Transizione chiuso → aperto: carica subito gli indicatori
            if is_open and not was_open:
                print("[STOCKS] NYSE aperto — caricamento indicatori stocks...")
                watchlist = get_watchlist()
                for symbol, cfg in watchlist.items():
                    if cfg.get("market") == "stocks":
                        await _fetch_and_compute(symbol, cfg)
                        await asyncio.sleep(0.5)
                print("[STOCKS] Indicatori stocks caricati — trading attivo")

            was_open = is_open
        except Exception as e:
            print(f"[STOCKS WATCHER] Errore: {e}")
        await asyncio.sleep(60)  # controlla ogni minuto


# ══════════════════════════════════════════════════════════════
# LOOP: REPORT GIORNALIERO TELEGRAM (una volta al giorno a mezzanotte)
# ══════════════════════════════════════════════════════════════

async def _report_loop():
    """Invia il report una volta al giorno alle 00:00."""
    import datetime
    while True:
        try:
            now  = datetime.datetime.now()
            # Calcola quanti secondi mancano alla mezzanotte
            midnight = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=5, microsecond=0
            )
            wait = (midnight - now).total_seconds()
            await asyncio.sleep(wait)
            await notify_daily_report(SHARED_STATE)
        except Exception as e:
            print(f"[REPORT] Errore: {e}")
            await asyncio.sleep(60)


# ══════════════════════════════════════════════════════════════
# LOOP: STREAM PREZZI + LOGICA TRADING (per asset)
# ══════════════════════════════════════════════════════════════

async def _trading_loop(symbol: str, cfg: dict):
    """
    Loop principale per ogni asset.
    Crypto: WebSocket Kraken via ccxt.pro
    Forex/Stocks: polling ogni 10 secondi (no WebSocket)
    Supporta posizioni LONG e SHORT.
    """
    market    = cfg.get("market", "crypto")
    connector = get_connector(symbol, exchange)

    while True:
        try:
            # ── Prezzo live: WebSocket per crypto, polling per altri ──
            if market == "crypto":
                ticker     = await exchange.watch_ticker(symbol)
                live_price = float(ticker["last"] or ticker["close"] or 0)
            else:
                if connector and connector.is_market_open(symbol):
                    live_price = await connector.fetch_price(symbol)
                else:
                    await asyncio.sleep(60)
                    continue

            if live_price <= 0:
                continue

            SHARED_STATE["live_prices"][symbol] = live_price
            snap     = SHARED_STATE["snapshots"].get(symbol)
            pos_data = SHARED_STATE["positions"].get(symbol, "CACCIA_APERTA")

            if snap is None:
                if market != "crypto":
                    await asyncio.sleep(10)
                continue

            is_in_position = isinstance(pos_data, dict) and pos_data.get("status") == "IN_POSITION"
            is_short = is_in_position and pos_data.get("direction") == "short"

            # ─────────────────────────────────────────────────
            # A. LOGICA DI USCITA
            # ─────────────────────────────────────────────────
            if is_in_position:

                # ── Uscita SHORT ──────────────────────────────
                if is_short:
                    reason, exit_type = score_short_exit(snap, pos_data, live_price, cfg)
                    if exit_type:
                        try:
                            volume  = pos_data.get("volume", 0)
                            pnl_eur = (pos_data["entry_price"] - live_price) * volume
                            if pnl_eur < 0:
                                SHARED_STATE["daily_loss_eur"]  += abs(pnl_eur)
                                SHARED_STATE["weekly_loss_eur"] += abs(pnl_eur)
                            SHARED_STATE["stop_loss_count"][symbol] = (
                                SHARED_STATE["stop_loss_count"].get(symbol, 0) + 1
                                if "STOP" in exit_type else 0
                            )
                            SHARED_STATE["last_trade_time"][symbol] = time.time()
                            _save_cooldowns()
                            SHARED_STATE["positions"][symbol] = "CACCIA_APERTA"
                            save_positions(SHARED_STATE["positions"])
                            log_trade({"symbol": symbol, "type": exit_type,
                                       "price": live_price, "volume": volume,
                                       "eur_value": volume*live_price, "pnl_eur": pnl_eur,
                                       "reason": reason, "direction": "short"})
                            await notify_sell(symbol, live_price, volume,
                                              volume*live_price, reason, exit_type,
                                              pos_data["entry_price"], pnl_eur)
                            print(f"[{exit_type}] SHORT {symbol} @ {live_price:.4f} | P&L: {pnl_eur:+.2f}€")
                        except Exception as e:
                            print(f"[ERROR] Short exit {symbol}: {e}")
                    if market != "crypto":
                        await asyncio.sleep(10)
                    continue

                # ── Uscita LONG ───────────────────────────────
                reason, exit_type = score_sell(snap, pos_data, live_price, cfg)

                # Punto 4: timeout posizioni stagnanti (48h senza TP1)
                if not exit_type and not pos_data.get("tp1_hit"):
                    entry_ts = pos_data.get("timestamp", time.time())
                    age_h    = (time.time() - entry_ts) / 3600
                    if age_h > 48:
                        pnl_pct = (live_price - pos_data["entry_price"]) / pos_data["entry_price"] * 100
                        # Esci solo se in perdita o guadagno minimo
                        if pnl_pct < 1.0:
                            reason    = f"Posizione stagnante da {age_h:.0f}h (P&L {pnl_pct:+.1f}%)"
                            exit_type = "SMART_EXIT"

                if exit_type == "TAKE_PROFIT_1":
                    try:
                        asset_bal   = await _get_asset_balance(symbol)
                        sell_volume = asset_bal * TP1_SELL_FRACTION
                        order       = await _place_order(symbol, "sell", sell_volume, connector)
                        eur_val     = sell_volume * live_price
                        pnl_eur     = (live_price - pos_data["entry_price"]) * sell_volume
                        pos_data["tp1_hit"]   = True
                        pos_data["max_price"] = live_price
                        SHARED_STATE["positions"][symbol] = pos_data
                        save_positions(SHARED_STATE["positions"])
                        log_trade({"symbol": symbol, "type": "SELL_TP1",
                                   "price": live_price, "volume": sell_volume,
                                   "eur_value": eur_val, "pnl_eur": pnl_eur,
                                   "reason": reason, "market": market})
                        await notify_sell(symbol, live_price, sell_volume, eur_val,
                                          reason, exit_type, pos_data["entry_price"], pnl_eur)
                        print(f"[TP1] {symbol} — venduto 50% @ {live_price:.4f}")
                    except Exception as e:
                        print(f"[ERROR] TP1 {symbol}: {e}")

                elif exit_type in ("STOP_LOSS","TAKE_PROFIT_2","TRAILING_STOP","SIGNAL","SMART_EXIT"):
                    try:
                        asset_bal = await _get_asset_balance(symbol)
                        order     = await _place_order(symbol, "sell", asset_bal, connector)
                        eur_val   = asset_bal * live_price
                        pnl_eur   = (live_price - pos_data["entry_price"]) * asset_bal
                        if pnl_eur < 0:
                            SHARED_STATE["daily_loss_eur"] += abs(pnl_eur)
                        SHARED_STATE["last_exit_type"][symbol] = exit_type
                        if exit_type == "STOP_LOSS":
                            sl_count = SHARED_STATE["stop_loss_count"].get(symbol, 0) + 1
                            SHARED_STATE["stop_loss_count"][symbol] = sl_count
                            adaptive_cooldown = min(TRADE_COOLDOWN * (2 ** sl_count), 28800)
                            SHARED_STATE["last_trade_time"][symbol] = time.time() + adaptive_cooldown - TRADE_COOLDOWN
                            _save_cooldowns()
                            print(f"[STOP] {symbol} — SL #{sl_count}, cooldown {adaptive_cooldown//3600:.0f}h")
                            # Registra per circuit breaker globale
                            if "recent_stop_losses" not in SHARED_STATE:
                                SHARED_STATE["recent_stop_losses"] = []
                            SHARED_STATE["recent_stop_losses"].append(time.time())
                            # Punto 3: salva stop_loss_count su disco
                            try:
                                sl_file = json.load(open("sl_count.json")) if os.path.exists("sl_count.json") else {"counts":{}, "timestamps":{}}
                                sl_file["counts"][symbol] = sl_count
                                sl_file["timestamps"][symbol] = time.time()
                                json.dump(sl_file, open("sl_count.json","w"))
                            except Exception:
                                pass
                            # Punto 5: auto-blacklist dopo 3 SL consecutivi (7 giorni)
                            if sl_count >= 3 and "/" in symbol:  # solo crypto
                                from screener import auto_blacklist_add, ALWAYS_INCLUDE_CRYPTO
                                if symbol not in ALWAYS_INCLUDE_CRYPTO:
                                    auto_blacklist_add(symbol, days=7)
                                    await _telegram_send(
                                        f"🚫 <b>Auto-blacklist</b>\n\n"
                                        f"{symbol} rimosso dalla watchlist per 7 giorni\n"
                                        f"Motivo: {sl_count} stop loss consecutivi"
                                    )
                        else:
                            SHARED_STATE["stop_loss_count"][symbol] = 0
                            SHARED_STATE["last_trade_time"][symbol] = time.time()
                            _save_cooldowns()
                        SHARED_STATE["positions"][symbol] = "CACCIA_APERTA"
                        save_positions(SHARED_STATE["positions"])
                        log_trade({"symbol": symbol, "type": exit_type,
                                   "price": live_price, "volume": asset_bal,
                                   "eur_value": eur_val, "pnl_eur": pnl_eur,
                                   "reason": reason, "market": market})
                        await notify_sell(symbol, live_price, asset_bal, eur_val,
                                          reason, exit_type, pos_data["entry_price"], pnl_eur)
                        print(f"[{exit_type}] {symbol} @ {live_price:.4f} | P&L: {pnl_eur:+.2f}€")
                    except Exception as e:
                        print(f"[ERROR] Sell {exit_type} {symbol}: {e}")
                        await notify_error(f"Errore vendita {symbol}: {e}")

            # ─────────────────────────────────────────────────
            # B. LOGICA DI INGRESSO
            # ─────────────────────────────────────────────────
            elif pos_data == "CACCIA_APERTA":
                ok, block_reason = can_buy(symbol, SHARED_STATE, cfg)
                if not ok:
                    if market != "crypto":
                        await asyncio.sleep(10)
                    continue

                snap4h       = SHARED_STATE["snapshots4h"].get(symbol)
                cfg_with_sym = {**cfg, "_symbol": symbol}
                buy_score, buy_reasons = score_buy(snap, cfg_with_sym, snap4h, SHARED_STATE)

                print(f"[SCORE] {symbol} {buy_score}/12 — {' | '.join(buy_reasons[:3])}")

                # ── Controlla possibilità SHORT ───────────────
                short_ok, short_block = can_short(symbol, SHARED_STATE)
                if short_ok and buy_score == 0 and "BEAR MACRO" in str(buy_reasons):
                    short_score, short_reasons = score_short(snap, cfg_with_sym, snap4h)
                    if short_score >= 5:
                        # Apri SHORT
                        try:
                            eur_balance  = await _get_eur_balance()
                            short_budget = min(cfg["budget_eur"] * SHORT_SIZE_PCT, eur_balance * 0.10)
                            if short_budget < 2:
                                pass
                            else:
                                short_volume = short_budget / live_price
                                sl_short = live_price * 1.015   # SL 1.5% sopra
                                tp1_short= live_price * 0.970   # TP1 3% sotto
                                tp2_short= live_price * 0.940   # TP2 6% sotto
                                SHARED_STATE["positions"][symbol] = {
                                    "status":      "IN_POSITION",
                                    "direction":   "short",
                                    "entry_price": live_price,
                                    "volume":      short_volume,
                                    "sl_price":    sl_short,
                                    "tp1_price":   tp1_short,
                                    "tp2_price":   tp2_short,
                                    "tp1_hit":     False,
                                    "max_price":   live_price,
                                    "buy_score":   short_score,
                                    "regime_at_entry": "TREND_BEAR",
                                    "timestamp":   int(time.time()),
                                    "market":      market,
                                }
                                SHARED_STATE["last_trade_time"][symbol] = time.time()
                                _save_cooldowns()
                                save_positions(SHARED_STATE["positions"])
                                log_trade({"symbol": symbol, "type": "SHORT",
                                           "price": live_price, "volume": short_volume,
                                           "eur_value": short_budget,
                                           "reason": ", ".join(short_reasons[:3]),
                                           "direction": "short", "market": market})
                                await notify_buy(symbol, live_price, short_volume, short_budget,
                                                 short_score, ["📉 SHORT"] + short_reasons[:4],
                                                 sl_short, tp1_short, tp2_short)
                                print(f"[SHORT] {symbol} @ {live_price:.4f} | score {short_score}/10")
                        except Exception as e:
                            print(f"[ERROR] Short {symbol}: {e}")

                # ── LONG normale ──────────────────────────────
                elif buy_score >= 5:
                    # Punto 7: conferma obbligatoria su 4H prima di comprare
                    snap4h_check = SHARED_STATE.get("snapshots4h", {}).get(symbol, {})
                    if snap4h_check and market == "crypto":
                        rsi4h   = snap4h_check.get("rsi", 50)
                        macd4h  = snap4h_check.get("macd_hist", 0)
                        ema_ok  = snap4h_check.get("close", 0) > snap4h_check.get("ema50", 0)
                        # Blocca se 4H fortemente ribassista
                        if rsi4h > 75 and macd4h < 0:
                            print(f"[4H] {symbol} — 4H ipercomprato (RSI {rsi4h:.0f}) con MACD negativo, skip")
                            if market != "crypto": await asyncio.sleep(10)
                            continue
                        if rsi4h < 25 and not snap4h_check.get("macd_cross_up"):
                            pass  # ipervenduto su 4H è ok — segnale forte

                    try:
                        eur_balance  = await _get_eur_balance()
                        from regime import get_adapted_sl, get_adapted_tp
                        asset_regime, _, _ = detect_regime(snap, snap4h)

                        # ── SIZING ADATTIVO INTELLIGENTE ─────────────────────
                        # Il bot decide quanto investire in base a:
                        # 1. Score del segnale (più alto = investe di più)
                        # 2. Volatilità ATR (alta volatilità = investe di meno)
                        # 3. Regime di mercato (bull = più aggressivo, bear = più cauto)
                        # 4. Saldo disponibile (non supera mai il saldo reale)
                        # 5. Numero posizioni aperte (più posizioni = meno per singola)

                        base_budget = cfg.get("budget_eur", 600)

                        # Fattore score: score 5=70%, score 8=90%, score 12=110%
                        score_factor = 0.5 + (buy_score / 12) * 0.6  # 0.7 a 1.1

                        # Fattore volatilità: ATR alto = riduce budget
                        atr_val  = snap.get("atr", 0)
                        atr_pct  = (atr_val / live_price) if live_price > 0 else 0.05
                        vol_factor = max(0.5, 1.0 - (atr_pct * 10))  # ATR 3% → factor 0.7

                        # Fattore regime
                        regime_factors = {
                            "TREND_BULL":   1.0,
                            "LATERAL":      0.8,
                            "TREND_BEAR":   0.6,
                            "COMPRESSION":  0.7,
                        }
                        regime_factor = regime_factors.get(asset_regime, 0.8)

                        # Fattore posizioni aperte: più posizioni → meno per trade
                        n_open = sum(1 for v in SHARED_STATE["positions"].values()
                                    if isinstance(v, dict) and v.get("status") == "IN_POSITION")
                        pos_factor = max(0.5, 1.0 - (n_open * 0.05))  # 10 pos → factor 0.5

                        # Budget finale
                        budget = base_budget * score_factor * vol_factor * regime_factor * pos_factor
                        budget = min(budget, eur_balance * 0.95)  # mai più del saldo
                        budget = max(budget, 20.0)  # minimo 20€

                        if budget < 20:
                            print(f"[BUY] {symbol} — budget insufficiente ({budget:.2f}€)")
                            if market != "crypto": await asyncio.sleep(10)
                            continue

                        print(f"[BUY] {symbol} — budget adattivo: {budget:.0f}€ "
                              f"(score×{score_factor:.2f} vol×{vol_factor:.2f} "
                              f"regime×{regime_factor:.2f} pos×{pos_factor:.2f})")
                        volume       = budget / live_price
                        order        = await _place_order(symbol, "buy", volume, connector)
                        exec_price   = float(order.get("price", live_price) or live_price)
                        atr_val      = snap.get("atr", exec_price * cfg["sl_pct"])
                        sl_price     = get_adapted_sl(exec_price, atr_val, cfg, asset_regime)
                        tp1_price, tp2_price = get_adapted_tp(exec_price, cfg, asset_regime)
                        SHARED_STATE["positions"][symbol] = {
                            "status":          "IN_POSITION",
                            "direction":       "long",
                            "entry_price":     exec_price,
                            "volume":          volume,
                            "sl_price":        sl_price,
                            "tp1_price":       tp1_price,
                            "tp2_price":       tp2_price,
                            "tp1_hit":         False,
                            "max_price":       exec_price,
                            "buy_score":       buy_score,
                            "regime_at_entry": asset_regime,
                            "snap4h_at_entry": snap4h,
                            "atr_at_entry":    atr_val,
                            "timestamp":       int(time.time()),
                            "market":          market,
                        }
                        SHARED_STATE["last_trade_time"][symbol] = time.time()
                        _save_cooldowns()
                        save_positions(SHARED_STATE["positions"])
                        log_trade({"symbol": symbol, "type": "BUY",
                                   "price": exec_price, "volume": volume,
                                   "eur_value": budget, "reason": ", ".join(buy_reasons),
                                   "market": market})
                        await notify_buy(symbol, exec_price, volume, budget,
                                         buy_score, buy_reasons,
                                         sl_price, tp1_price, tp2_price)
                        print(f"[BUY] {symbol} @ {exec_price:.4f} | score {buy_score}/12 | {market}")
                    except Exception as e:
                        print(f"[ERROR] Buy {symbol}: {e}")
                        await notify_error(f"Errore acquisto {symbol}: {e}")

            if market != "crypto":
                await asyncio.sleep(10)   # polling ogni 10s per forex/stocks

        except Exception as e:
            print(f"[ERROR] Loop {symbol}: {type(e).__name__}: {e}")
            await asyncio.sleep(5 if market == "crypto" else 30)


# ══════════════════════════════════════════════════════════════
# DASHBOARD TERMINALE (si aggiorna in place ogni secondo)
# ══════════════════════════════════════════════════════════════

# Codici ANSI colori
_G  = "\033[92m"   # verde
_R  = "\033[91m"   # rosso
_Y  = "\033[93m"   # giallo
_C  = "\033[96m"   # ciano
_W  = "\033[97m"   # bianco brillante
_DIM= "\033[2m"    # tenue
_B  = "\033[1m"    # grassetto
_RST= "\033[0m"    # reset


def _rsi_color(rsi: float) -> str:
    if rsi <= 35:  return _G
    if rsi >= 65:  return _R
    return _Y


def _dist_color(dist_pct: float) -> str:
    """Verde = vicino al pavimento (opportunità), rosso = lontano"""
    if dist_pct <= 1.0:  return _G
    if dist_pct <= 5.0:  return _Y
    return _R


def _score_bar(score: int, total: int = 6) -> str:
    filled = "█" * score
    empty  = "░" * (total - score)
    if score >= 5:   color = _G
    elif score >= 3: color = _Y
    else:            color = _DIM
    return f"{color}{filled}{empty}{_RST}"


def _pnl_color(pnl: float) -> str:
    return _G if pnl >= 0 else _R


def _render_dashboard():
    """Ridisegna la dashboard completa nel terminale."""
    lines = []
    ts    = time.strftime("%H:%M:%S")
    mode  = f"{_Y}⚠  DRY RUN{_RST}" if DRY_RUN else f"{_R}● LIVE{_RST}"
    stats = get_daily_stats()

    # ── Intestazione ──────────────────────────────────────────
    lines.append(f"{_B}{'─'*72}{_RST}")
    lines.append(
        f"  {_B}KRAKEN BOT v5.0{_RST}  {mode}  "
        f"{_DIM}aggiornato: {ts}{_RST}  │  "
        f"P&L oggi: {_pnl_color(stats['pnl_eur'])}{stats['pnl_eur']:+.2f}€{_RST}  "
        f"Trade: {stats['trades']}  "
        f"Win: {_G}{stats['wins']}{_RST}/{_R}{stats['losses']}{_RST}"
    )
    lines.append(f"{_B}{'─'*72}{_RST}")

    # ── Portfolio CORE (DCA) ──────────────────────────────────
    lines.append(f"{_B}{'─'*72}{_RST}")
    lines.append(f"  {_B}PORTFOLIO CORE — Buy & Hold (macera){_RST}")
    lines.append(f"  {'─'*68}")

    portfolio = load_dca_portfolio()
    port_stats = get_portfolio_stats(portfolio, SHARED_STATE.get("live_prices", {}))

    for asset in ["BTC/EUR", "ETH/EUR"]:
        s = port_stats.get(asset)
        if not s:
            lines.append(f"  {_DIM}{asset:<10} nessun acquisto DCA ancora{_RST}")
            continue
        pnl_c = _G if s["pnl_pct"] >= 0 else _R
        lines.append(
            f"  {_W}{asset:<10}{_RST} "
            f"{s['units']:.6f} unità  │  "
            f"carico medio: {s['avg_price']:.2f}€  │  "
            f"ora vale: {_B}{s['current_val']:.2f}€{_RST}  │  "
            f"P&L: {pnl_c}{s['pnl_pct']:+.2f}% ({s['pnl_eur']:+.2f}€){_RST}"
        )

    t = port_stats.get("_totals", {})
    if t.get("invested", 0) > 0:
        tc = _G if t["pnl_eur"] >= 0 else _R
        lines.append(
            f"  {_DIM}Totale versato: {t['invested']:.2f}€  │  "
            f"Valore: {t['value']:.2f}€  │  "
            f"Guadagno: {tc}{t['pnl_pct']:+.2f}% ({t['pnl_eur']:+.2f}€){_RST}"
        )
    else:
        lines.append(f"  {_DIM}Primo DCA il giorno 1 del mese alle 10:00{_RST}")

    lines.append(f"{_B}{'─'*72}{_RST}")

    # ── Separatore trading attivo ─────────────────────────────
    lines.append(f"  {_B}SATELLITE — Trading attivo{_RST}")
    lines.append(f"  {'─'*68}")

    # ── Intestazione colonne ──────────────────────────────────
    lines.append(
        f"  {'ASSET':<10} {'PREZZO':>11}  {'REGIME':<12}  {'STATO':<11} "
        f"{'DIST.PAV.':>12}  {'RSI':>5}  {'SCORE':>8}  {'PnL':>14}"
    )

    # ── Una riga per asset ────────────────────────────────────
    # Gruppi visivi per categoria
    GROUPS = {
        "── Large cap ──":  ["BTC/EUR", "ETH/EUR"],
        "── Mid cap ─────": ["SOL/EUR", "XRP/EUR", "ADA/EUR", "DOT/EUR",
                             "LTC/EUR", "TRX/EUR", "XLM/EUR"],
        "── Alt cap ─────": ["NEAR/EUR", "ICP/EUR", "ONDO/EUR"],
        "── High vol ────": ["TAO/EUR", "ZEC/EUR"],
    }

    # Colori regime
    _REGIME_COLOR = {
        "TREND_BULL": _G,
        "TREND_BEAR": _R,
        "LATERAL":    _Y,
        "HIGH_VOL":   _R,
    }

    for group_label, symbols in GROUPS.items():
        group_assets = [s for s in symbols if s in CRYPTO_CONFIG]
        if not group_assets:
            continue
        lines.append(f"  {_DIM}{group_label}{'─'*40}{_RST}")

        for symbol in group_assets:
            cfg      = CRYPTO_CONFIG[symbol]
            price    = SHARED_STATE["live_prices"].get(symbol)
            snap     = SHARED_STATE["snapshots"].get(symbol)
            snap4h   = SHARED_STATE["snapshots4h"].get(symbol)
            pos_data = SHARED_STATE["positions"].get(symbol, "CACCIA_APERTA")

            if price is None or snap is None:
                lines.append(f"  {_DIM}{symbol:<10} {'⏳ caricamento...'}{_RST}")
                continue

            is_in = isinstance(pos_data, dict) and pos_data.get("status") == "IN_POSITION"

            # Regime corrente
            regime, _ = get_asset_regime(symbol, SHARED_STATE)
            rc        = REGIME_CONFIGS[regime]
            reg_color = _REGIME_COLOR.get(regime, _DIM)
            reg_str   = f"{reg_color}{rc.emoji} {rc.label[:9]:<9}{_RST}"

            bb_lower = snap["bb_lower"]
            dist_pct = (price - bb_lower) / bb_lower * 100
            dist_str = f"{dist_pct:+.2f}%"
            fill     = max(0, min(8, int((1 - min(dist_pct, 10) / 10) * 8)))
            dist_bar = f"[{'█'*fill}{'░'*(8-fill)}]"
            rsi      = snap["rsi"]
            score, _ = score_buy(snap, cfg, snap4h)

            if is_in:
                entry    = pos_data["entry_price"]
                pnl_pct  = (price - entry) / entry * 100
                pnl_eur  = pnl_pct / 100 * cfg["budget_eur"]
                sl_price = entry * (1 - cfg["sl_pct"])
                tp1      = entry * (1 + cfg["tp_pct"])

                if pnl_pct >= cfg["tp_pct"] * 100:
                    stato = f"{_G}● GAIN   {_RST}"
                elif price <= sl_price * 1.008:
                    stato = f"{_R}⚠ VIC.SL {_RST}"
                else:
                    stato = f"{_C}● POSIZ. {_RST}"

                pnl_str = f"{_pnl_color(pnl_pct)}{pnl_pct:+5.2f}% {pnl_eur:+5.2f}€{_RST}"
                lines.append(
                    f"  {_W}{symbol:<10}{_RST}{_B}{price:>11.4f}{_RST}  "
                    f"{reg_str}  {stato}  "
                    f"{_dist_color(dist_pct)}{dist_bar}{dist_str:>7}{_RST}  "
                    f"{_rsi_color(rsi)}{rsi:>5.1f}{_RST}  "
                    f"{_score_bar(score)}  {pnl_str}  "
                    f"{_DIM}SL:{sl_price:.3f}{_RST}"
                )
            else:
                if dist_pct <= 0.5:
                    stato = f"{_G}★ PRONTO {_RST}"
                elif dist_pct <= 3.0:
                    stato = f"{_Y}◎ VICINO {_RST}"
                else:
                    stato = f"{_DIM}○ ATTESA {_RST}"

                lines.append(
                    f"  {symbol:<10}{price:>11.4f}  "
                    f"{reg_str}  {stato}  "
                    f"{_dist_color(dist_pct)}{dist_bar}{dist_str:>7}{_RST}  "
                    f"{_rsi_color(rsi)}{rsi:>5.1f}{_RST}  "
                    f"{_score_bar(score)}  {'─':>14}"
                )

    # ── Legenda ───────────────────────────────────────────────
    lines.append(f"  {'─'*68}")
    lines.append(
        f"  {_DIM}Distanza pavimento: "
        f"{_G}verde{_RST}{_DIM}=vicino  "
        f"{_Y}giallo{_RST}{_DIM}=medio  "
        f"{_R}rosso{_RST}{_DIM}=lontano  │  "
        f"Score: {_G}████{_RST}{_DIM}=pronto a comprare  "
        f"░░░░=troppo presto{_RST}"
    )
    lines.append(f"{_B}{'─'*72}{_RST}")

    # ── Disegna in place (sovrascrive le righe precedenti) ────
    n = len(lines)
    # Torna su di n righe se non è la prima volta
    if hasattr(_render_dashboard, "_last_lines") and _render_dashboard._last_lines > 0:
        sys.stdout.write(f"\033[{_render_dashboard._last_lines}A\033[J")
    _render_dashboard._last_lines = n
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()

_render_dashboard._last_lines = 0


async def _dashboard_loop():
    """Ridisegna la dashboard ogni secondo."""
    await asyncio.sleep(5)
    while True:
        try:
            _render_dashboard()
        except Exception:
            pass
        await asyncio.sleep(1)


async def _state_writer_loop():
    """Scrive lo stato condiviso su disco ogni 5s per l'API REST."""
    while True:
        try:
            def clean(d):
                out = {}
                for k, v in d.items():
                    if isinstance(v, dict):
                        out[k] = {ik: (bool(iv) if isinstance(iv, (bool,)) else
                                       float(iv) if hasattr(iv, '__float__') else
                                       str(iv) if not isinstance(iv, (int, float, str, bool, type(None))) else iv)
                                  for ik, iv in v.items()}
                    else:
                        out[k] = v
                return out

            snaps_clean   = clean(SHARED_STATE.get("snapshots", {}))
            snaps4h_clean = clean(SHARED_STATE.get("snapshots4h", {}))

            data = {
                "live_prices":  SHARED_STATE.get("live_prices", {}),
                "snapshots":    snaps_clean,
                "snapshots4h":  snaps4h_clean,
                "positions":    {k: v for k, v in SHARED_STATE.get("positions", {}).items()
                                 if isinstance(v, dict) and v.get("status") == "IN_POSITION"},
                "daily_loss":   SHARED_STATE.get("daily_loss_eur", 0),
                "market_status":SHARED_STATE.get("market_status", {}),
                "ts":           int(time.time()),
            }
            with open("shared_state.json", "w") as f:
                json.dump(data, f)

            # Punto 1: salva snapshot su disco separato per sopravvivere ai riavvii
            if snaps_clean:
                with open("snapshots_cache.json", "w") as f:
                    json.dump({"snapshots": snaps_clean, "snapshots4h": snaps4h_clean,
                               "ts": int(time.time())}, f)

        except Exception as e:
            print(f"[STATE] Errore writer: {e}")
        await asyncio.sleep(5)


    """Ridisegna la dashboard ogni secondo."""
    await asyncio.sleep(5)   # aspetta il primo caricamento dati
    while True:
        try:
            _render_dashboard()
        except Exception:
            pass
        await asyncio.sleep(1)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("  KRAKEN TRADING BOT v5.0")
    print(f"  Modalità: {'⚠️  DRY RUN' if DRY_RUN else '🔴 LIVE'}")
    print(f"  Asset crypto: {', '.join(CRYPTO_CONFIG.keys())}")
    if MARKET_STOCKS_ENABLED:
        print(f"  Asset stocks: {', '.join(STOCKS_CONFIG.keys())}")
    print("=" * 60)

    # Carica posizioni salvate
    SHARED_STATE["positions"] = load_positions()

    # Punto 1: carica snapshot da cache disco (sopravvivono ai riavvii)
    try:
        if os.path.exists("snapshots_cache.json"):
            cache = json.load(open("snapshots_cache.json"))
            age   = time.time() - cache.get("ts", 0)
            if age < 3600:
                SHARED_STATE["snapshots"]   = cache.get("snapshots", {})
                SHARED_STATE["snapshots4h"] = cache.get("snapshots4h", {})
                print(f"[INIT] Snapshot cache caricata: {len(SHARED_STATE['snapshots'])} asset ({age/60:.0f} min fa)")
    except Exception as e:
        print(f"[INIT] Cache snapshot non disponibile: {e}")

    # Punto 3: carica stop_loss_count dal disco (sopravvive ai riavvii)
    try:
        if os.path.exists("sl_count.json"):
            sl_data = json.load(open("sl_count.json"))
            # Pulisci contatori vecchi (> 24h)
            now_ts = time.time()
            SHARED_STATE["stop_loss_count"] = {
                k: v for k, v in sl_data.get("counts", {}).items()
                if now_ts - sl_data.get("timestamps", {}).get(k, 0) < 86400
            }
            print(f"[INIT] SL count caricato: {len(SHARED_STATE['stop_loss_count'])} asset")
    except Exception as e:
        print(f"[INIT] SL count non disponibile: {e}")

    # Gestione posizioni orfane — asset in posizione ma non in watchlist
    # Vengono aggiunti automaticamente alla watchlist
    for sym, pos in SHARED_STATE["positions"].items():
        if isinstance(pos, dict) and pos.get("status") == "IN_POSITION":
            mkt = pos.get("market", "crypto")
            if mkt == "crypto" and sym not in ALWAYS_INCLUDE_CRYPTO:
                cfg_orphan = CRYPTO_CONFIG.get(sym, {
                    "budget_eur": 600, "bb_std": 1.8,
                    "sl_pct": 0.06, "tp_pct": 0.10, "market": "crypto"
                })
                cfg_orphan["_orphan"] = True
                ALWAYS_INCLUDE_CRYPTO.add(sym)
                print(f"[INIT] Posizione orfana aggiunta alla watchlist: {sym}")

    # Inizializza connettori multi-mercato
    print("[INIT] Inizializzazione connettori di mercato...")
    init_connectors(exchange)

    # Carica mercati Kraken
    print("[INIT] Caricamento mercati Kraken...")
    await exchange.load_markets()

    # Inizializza watchlist con asset di default
    from screener import WATCHLIST, update_watchlist
    update_watchlist(
        [{"symbol": s, "score": 5.0, "price": 0, "market": "crypto"} for s in ALWAYS_INCLUDE_CRYPTO],
        [{"symbol": s, "score": 5.0, "price": 0, "market": "stocks"} for s in ALWAYS_INCLUDE_STOCKS],
        SHARED_STATE["positions"],
    )

    # Calcolo iniziale indicatori per tutti gli asset in watchlist
    print("[INIT] Calcolo iniziale indicatori...")
    for symbol, cfg in get_watchlist().items():
        await _fetch_and_compute(symbol, cfg)
        await asyncio.sleep(0.3)
    print(f"[INIT] Pronti. {len(get_watchlist())} asset monitorati. Avvio loop trading...")

    await notify_start(len(get_watchlist()))

    # Loop che aggiunge nuovi task quando la watchlist si espande
    async def _watchlist_manager():
        """Avvia loop trading per i nuovi asset scoperti dallo screener."""
        active_tasks = {}
        while True:
            try:
                wl = get_watchlist()
                for symbol, cfg in wl.items():
                    if symbol not in active_tasks or active_tasks[symbol].done():
                        t = asyncio.create_task(_trading_loop(symbol, cfg))
                        active_tasks[symbol] = t
                        if cfg.get("_discovered"):
                            print(f"[SCREENER] Aggiunto {symbol} alla watchlist (score {cfg.get('_score',0)})")
                            await _fetch_and_compute(symbol, cfg)
            except Exception as e:
                print(f"[WATCHLIST] Errore: {e}")
            await asyncio.sleep(60)  # controlla ogni minuto

    # Avvia tutti i task
    tasks = [
        asyncio.create_task(_daily_reset_loop()),
        asyncio.create_task(_bands_updater_loop()),
        asyncio.create_task(_stocks_market_open_watcher()),
        asyncio.create_task(
            __import__("daily_report").daily_report_loop(SHARED_STATE, _telegram_send)
        ),
        asyncio.create_task(_state_writer_loop()),
        asyncio.create_task(dca_loop(exchange, SHARED_STATE, _telegram_send)),
        asyncio.create_task(context_loop(SHARED_STATE)),
        asyncio.create_task(screener_loop(exchange, SHARED_STATE)),
        asyncio.create_task(_watchlist_manager()),
    ]

    # Task iniziali per asset di default
    for symbol, cfg in get_watchlist().items():
        tasks.append(asyncio.create_task(_trading_loop(symbol, cfg)))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Bot fermato dall'utente.")
    except Exception as e:
        print(f"\n[FATAL] {e}")
        raise
