"""
notifier.py — Messaggi Telegram formattati per ogni evento del bot
"""

import asyncio
import time
from settings import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DRY_RUN
from persistence import get_daily_stats

try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False
    print("[NOTIFIER] python-telegram-bot non installato. Notifiche disabilitate.")


_bot = None

def get_bot():
    global _bot
    if TELEGRAM_OK and _bot is None and TELEGRAM_TOKEN:
        _bot = Bot(token=TELEGRAM_TOKEN)
    return _bot


async def _send(text: str):
    """Invia un messaggio HTML a Telegram con retry automatico."""
    bot = get_bot()
    if not bot:
        print(f"[NOTIFY] {text[:120]}")
        return
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                print(f"[NOTIFY] Errore Telegram dopo 3 tentativi: {e}")


def _dry(tag: str) -> str:
    return " <i>[DRY RUN]</i>" if DRY_RUN else ""


# ─── MESSAGGI ─────────────────────────────────────────────────

async def notify_start(n_assets: int):
    await _send(
        f"🤖 <b>Bot v5.0 avviato{_dry('start')}</b>\n\n"
        f"Monitoraggio attivo su <b>{n_assets} asset</b>.\n"
        f"Strategia: Bollinger + MACD + RSI + Volume + Pattern\n"
        f"Soglia BUY: 4/6 condizioni · SELL: 3/5 condizioni\n"
        f"Modalità: {'⚠️ DRY RUN (simulazione)' if DRY_RUN else '🔴 LIVE — ORDINI REALI'}"
    )


async def notify_buy(symbol: str, price: float, volume: float,
                     eur_amt: float, score: int, reasons: list[str],
                     sl_price: float, tp1_price: float, tp2_price: float):
    # Prima riga è il regime — separala dalle motivazioni tecniche
    regime_line = reasons[0] if reasons else ""
    tech_reasons = reasons[1:]
    reasons_txt = "\n".join(f"  ✅ {r}" for r in tech_reasons[:8])
    sl_pct  = ((sl_price  / price) - 1) * 100
    tp1_pct = ((tp1_price / price) - 1) * 100
    tp2_pct = ((tp2_price / price) - 1) * 100
    await _send(
        f"🚀 <b>ACQUISTO ESEGUITO{_dry('buy')}</b>\n\n"
        f"<b>Asset:</b> {symbol}\n"
        f"<b>Prezzo:</b> {price:.4f} EUR\n"
        f"<b>Volume:</b> {volume:.6f}\n"
        f"<b>Importo:</b> ~{eur_amt:.2f}€\n\n"
        f"<b>Regime:</b> {regime_line}\n"
        f"<b>Score:</b> {score}/12 (soglia 5)\n"
        f"<b>Motivazioni:</b>\n{reasons_txt}\n\n"
        f"🛑 <b>Stop Loss:</b> {sl_price:.4f} ({sl_pct:.1f}%)\n"
        f"🎯 <b>TP1 (50%):</b> {tp1_price:.4f} (+{tp1_pct:.1f}%)\n"
        f"🎯 <b>TP2 (50%):</b> {tp2_price:.4f} (+{tp2_pct:.1f}%)"
    )


async def notify_sell(symbol: str, price: float, volume: float,
                      eur_amt: float, reason: str, exit_type: str,
                      entry_price: float, pnl_eur: float):
    pnl_pct  = (price - entry_price) / entry_price * 100
    emoji    = "📈" if pnl_eur >= 0 else "📉"
    type_map = {
        "STOP_LOSS":     "🛑 Stop Loss",
        "SELL_TP1":      "🎯 Take Profit 1 (50%)",
        "TAKE_PROFIT_1": "🎯 Take Profit 1 (50%)",
        "TAKE_PROFIT_2": "🎯 Take Profit 2 (finale)",
        "TRAILING_STOP": "📌 Trailing Stop",
        "SIGNAL":        "📊 Segnale tecnico",
        "SMART_EXIT":    "🧠 Uscita intelligente",
    }
    type_label = type_map.get(exit_type, exit_type)
    await _send(
        f"{emoji} <b>VENDITA{_dry('sell')}</b> — {type_label}\n\n"
        f"<b>Asset:</b> {symbol}\n"
        f"<b>Motivo:</b> {reason}\n"
        f"<b>Carico:</b> {entry_price:.4f} EUR\n"
        f"<b>Uscita:</b> {price:.4f} EUR\n"
        f"<b>P&amp;L:</b> {emoji} {pnl_pct:+.2f}% ({pnl_eur:+.2f}€)"
    )


async def notify_daily_report(shared_state: dict):
    stats  = get_daily_stats()
    prices = shared_state.get("live_prices", {})
    open_pos = {
        s: v for s, v in shared_state["positions"].items()
        if isinstance(v, dict) and v.get("status") == "IN_POSITION"
    }

    pos_lines = ""
    for sym, pos in open_pos.items():
        live  = prices.get(sym, pos["entry_price"])
        pnl   = (live - pos["entry_price"]) / pos["entry_price"] * 100
        emoji = "📈" if pnl >= 0 else "📉"
        pos_lines += f"\n  {emoji} {sym}: {pnl:+.2f}%"

    wr        = stats["win_rate"]
    wr_emoji  = "✅" if wr >= 55 else "⚠️" if wr >= 40 else "🔴"
    pnl_emoji = "📈" if stats["pnl_eur"] >= 0 else "📉"

    # Mostra best e worst trade del giorno
    best  = stats.get("best_trade", 0)
    worst = stats.get("worst_trade", 0)
    extra = ""
    if stats["trades"] > 0:
        extra = (f"\n  🟢 Miglior trade: +{best:.2f}€"
                 f"\n  🔴 Peggior trade: {worst:.2f}€")

    await _send(
        f"📊 <b>Report giornaliero</b> — {stats['date']}\n\n"
        f"Trade chiusi: <b>{stats['trades']}</b> "
        f"(<b>{stats['wins']}</b> vinti / <b>{stats['losses']}</b> persi)\n"
        f"{wr_emoji} Win rate: <b>{wr:.0f}%</b>\n"
        f"{pnl_emoji} P&amp;L oggi: <b>{stats['pnl_eur']:+.2f}€</b>"
        f"{extra}\n"
        f"\n<b>Posizioni aperte:</b> "
        f"{'Nessuna' if not open_pos else pos_lines}"
    )


async def notify_daily_loss_limit(loss: float):
    await _send(
        f"🚨 <b>LIMITE PERDITA GIORNALIERA RAGGIUNTO</b>\n\n"
        f"Perdita accumulata oggi: <b>−{loss:.2f}€</b>\n"
        f"Il bot ha <b>sospeso i nuovi acquisti</b> per oggi.\n"
        f"Le posizioni aperte continuano ad essere monitorate."
    )


async def notify_error(msg: str):
    await _send(f"⚠️ <b>Errore bot</b>\n<code>{msg[:400]}</code>")
