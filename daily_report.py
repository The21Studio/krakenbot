"""
daily_report.py — Report giornaliero automatico via Telegram

Ogni mattina alle 08:00 UTC invia un riepilogo con:
- P&L del giorno precedente
- Win rate aggiornato
- Posizioni aperte
- Stato portafoglio virtuale
- Drawdown e regime mercato
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta


async def send_daily_report(shared_state: dict, telegram_send):
    """Genera e invia il report giornaliero."""
    try:
        import json, os

        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        date_str = yesterday.strftime("%d/%m/%Y")

        # Intervallo Unix di "ieri" (UTC)
        y_start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        y_end   = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        def _ts(t):
            """Estrae il timestamp come float, qualunque sia il formato salvato."""
            v = t.get("timestamp", 0)
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        # ── P&L e trade di ieri ──────────────────────────────
        trades = []
        if os.path.exists("trades_log.json"):
            all_trades = json.load(open("trades_log.json"))
            for t in all_trades:
                ts = _ts(t)
                if y_start <= ts < y_end and t.get("type") not in ("BUY",):
                    trades.append(t)

        pnl_ieri = sum(t.get("pnl_eur", 0) or 0 for t in trades)
        wins     = [t for t in trades if (t.get("pnl_eur") or 0) > 0]
        losses   = [t for t in trades if (t.get("pnl_eur") or 0) < 0]
        wr       = len(wins) / len(trades) * 100 if trades else 0

        # ── P&L totale storico ───────────────────────────────
        pnl_totale = 0.0
        if os.path.exists("trades_log.json"):
            all_trades = json.load(open("trades_log.json"))
            pnl_totale = sum(t.get("pnl_eur", 0) or 0 for t in all_trades
                            if t.get("type") not in ("BUY",))

        # ── Posizioni aperte ─────────────────────────────────
        positions = shared_state.get("positions", {})
        open_pos  = {k: v for k, v in positions.items()
                    if isinstance(v, dict) and v.get("status") == "IN_POSITION"}
        prices    = shared_state.get("live_prices", {})

        open_pnl = 0.0
        pos_lines = []
        for sym, pos in open_pos.items():
            entry  = pos.get("entry_price", 0)
            live   = prices.get(sym, entry)
            vol    = pos.get("volume", 0)
            pnl_op = (live - entry) * vol if vol else 0
            open_pnl += pnl_op
            sign = "+" if pnl_op >= 0 else ""
            pos_lines.append(f"  • {sym}: {sign}{pnl_op:.2f}€")

        # ── Portafoglio virtuale ──────────────────────────────
        vp_saldo = 8500.0
        vp_deposited = 8500.0
        try:
            vp_data = json.load(open("virtual_portfolio.json"))
            vp_deposited = vp_data.get("total_deposited", 8500.0)
            vp_saldo = vp_deposited + pnl_totale
        except Exception:
            pass

        rendimento = (pnl_totale / vp_deposited * 100) if vp_deposited > 0 else 0

        # ── Regime macro ─────────────────────────────────────
        ctx = shared_state.get("context", {})
        fg  = ctx.get("fear_greed", {}).get("value", "—")
        fg_label = ctx.get("fear_greed", {}).get("label", "")

        # Emoji P&L
        pnl_emoji = "📈" if pnl_ieri >= 0 else "📉"
        tot_emoji  = "📈" if pnl_totale >= 0 else "📉"

        # ── Circuit breaker ───────────────────────────────────
        cb_until = shared_state.get("circuit_breaker_until", 0)
        cb_str   = ""
        if cb_until > time.time():
            rem = int((cb_until - time.time()) / 60)
            cb_str = f"\n⚡ Circuit breaker attivo ancora {rem} min"

        # ── Composizione messaggio ────────────────────────────
        pos_section = "\n".join(pos_lines) if pos_lines else "  Nessuna"

        msg = (
            f"📊 <b>Report Giornaliero — {date_str}</b>\n\n"
            f"{pnl_emoji} <b>P&L ieri:</b> {'+' if pnl_ieri >= 0 else ''}{pnl_ieri:.2f}€\n"
            f"   {len(trades)} operazioni · {len(wins)}W {len(losses)}L · Win rate {wr:.0f}%\n\n"
            f"{tot_emoji} <b>P&L totale:</b> {'+' if pnl_totale >= 0 else ''}{pnl_totale:.2f}€\n"
            f"   Rendimento: {'+' if rendimento >= 0 else ''}{rendimento:.2f}%\n"
            f"   Saldo virtuale: {vp_saldo:.2f}€ (depositato {vp_deposited:.0f}€)\n\n"
            f"💼 <b>Posizioni aperte ({len(open_pos)}):</b>\n{pos_section}\n"
            f"   P&L non realizzato: {'+' if open_pnl >= 0 else ''}{open_pnl:.2f}€\n\n"
            f"😱 <b>Fear & Greed:</b> {fg}/100 {fg_label}"
            f"{cb_str}"
        )

        await telegram_send(msg)
        print(f"[REPORT] Report giornaliero inviato ({date_str})")

    except Exception as e:
        print(f"[REPORT] Errore: {e}")





async def send_weekly_report(shared_state: dict, telegram_send):
    """Report settimanale ogni lunedì alle 08:00 UTC."""
    try:
        import json, os
        from datetime import datetime, timezone, timedelta

        now  = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=7)).timestamp()

        def _ts(t):
            v = t.get("timestamp", 0)
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        trades = []
        if os.path.exists("trades_log.json"):
            all_trades = json.load(open("trades_log.json"))
            for t in all_trades:
                if _ts(t) >= week_start and t.get("type") not in ("BUY",):
                    trades.append(t)

        pnl_week = sum(t.get("pnl_eur", 0) or 0 for t in trades)
        wins     = [t for t in trades if (t.get("pnl_eur") or 0) > 0]
        losses   = [t for t in trades if (t.get("pnl_eur") or 0) < 0]
        wr       = len(wins) / len(trades) * 100 if trades else 0

        # Top 3 migliori e peggiori trade
        sorted_t = sorted(trades, key=lambda x: x.get("pnl_eur", 0), reverse=True)
        top3     = sorted_t[:3]
        bot3     = sorted_t[-3:]

        top_str = "\n".join(f"  🟢 {t['symbol']}: +{t['pnl_eur']:.2f}€" for t in top3 if t.get("pnl_eur", 0) > 0)
        bot_str = "\n".join(f"  🔴 {t['symbol']}: {t['pnl_eur']:.2f}€" for t in bot3 if t.get("pnl_eur", 0) < 0)

        # Portafoglio
        pnl_totale = 0.0
        if os.path.exists("trades_log.json"):
            all_t = json.load(open("trades_log.json"))
            pnl_totale = sum(t.get("pnl_eur", 0) or 0 for t in all_t if t.get("type") not in ("BUY",))
        deposited = 8500.0
        try:
            vp = json.load(open("virtual_portfolio.json"))
            deposited = vp.get("total_deposited", 8500.0)
        except Exception:
            pass

        rendimento = (pnl_totale / deposited * 100) if deposited > 0 else 0
        emoji = "📈" if pnl_week >= 0 else "📉"

        msg = (
            f"📅 <b>Report Settimanale</b>\n\n"
            f"{emoji} <b>P&L settimana:</b> {'+' if pnl_week >= 0 else ''}{pnl_week:.2f}€\n"
            f"   {len(trades)} operazioni · {len(wins)}W {len(losses)}L · WR {wr:.0f}%\n\n"
            f"📊 <b>Performance totale:</b>\n"
            f"   P&L: {'+' if pnl_totale >= 0 else ''}{pnl_totale:.2f}€\n"
            f"   Rendimento: {'+' if rendimento >= 0 else ''}{rendimento:.2f}%\n"
            f"   Saldo: {deposited + pnl_totale:.2f}€\n\n"
            + (f"🏆 <b>Migliori trade:</b>\n{top_str}\n\n" if top_str else "")
            + (f"💀 <b>Peggiori trade:</b>\n{bot_str}" if bot_str else "")
        )

        await telegram_send(msg)
        print("[REPORT] Report settimanale inviato")
    except Exception as e:
        print(f"[REPORT] Errore settimanale: {e}")


async def daily_report_loop(shared_state: dict, telegram_send):
    """Loop che invia il report ogni mattina alle 08:00 UTC e il report settimanale il lunedì."""
    print("[REPORT] Loop report giornaliero avviato")
    while True:
        try:
            now    = datetime.now(timezone.utc)
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            print(f"[REPORT] Prossimo report alle 08:00 UTC (tra {wait_seconds/3600:.1f}h)")
            await asyncio.sleep(wait_seconds)

            # Report giornaliero
            await send_daily_report(shared_state, telegram_send)

            # Report settimanale ogni lunedì
            if datetime.now(timezone.utc).weekday() == 0:
                await send_weekly_report(shared_state, telegram_send)

        except Exception as e:
            print(f"[REPORT] Errore loop: {e}")
            await asyncio.sleep(3600)
