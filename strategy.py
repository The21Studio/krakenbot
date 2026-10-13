"""
strategy.py — Strategia v9.0 IBRIDA ADATTIVA

Il bot rileva il tipo di mercato e sceglie automaticamente la strategia:
  • TREND_BULL  → Trend Following (segue il trend al rialzo)
  • LATERAL     → Mean Reversion (compra minimi, vende massimi)
  • SQUEEZE     → Breakout (entra sull'esplosione di volatilità)
  • TREND_BEAR  → Nessun acquisto (salvo inversione fortissima)

Tutti gli indicatori sono collegati. Soglia 5/12.
Uscita intelligente protegge il profitto su ogni strategia.
"""

import time
from settings import VOLUME_MULT, TRADE_COOLDOWN, MAX_OPEN_POSITIONS, MAX_DAILY_LOSS_EUR
from regime import (
    detect_regime, get_adapted_sl, get_adapted_tp,
    get_adapted_budget, REGIME_CONFIGS, Regime
)
from strategies_hybrid import compute_hybrid_score, select_strategy


def _get_context_modifier(symbol: str, shared_state: dict) -> tuple[float, str]:
    try:
        ctx       = shared_state.get("context", {})
        asset_ctx = ctx.get("assets", {}).get(symbol, {})
        modifier  = float(asset_ctx.get("total_modifier", 0.0))
        fg        = ctx.get("fear_greed", {})
        ai        = asset_ctx.get("ai", {})
        label     = f"F&G:{fg.get('value',50)} {ai.get('action','?')}"
        return modifier, label
    except Exception:
        return 0.0, ""


# ══════════════════════════════════════════════════════════════
# SCORE BUY IBRIDO — sceglie la strategia in base al mercato
# ══════════════════════════════════════════════════════════════

def score_buy(snap: dict, cfg: dict, snap4h: dict = None, shared_state: dict = None) -> tuple[int, list[str]]:
    if not snap:
        return 0, ["Dati non disponibili"]

    regime, _, _ = detect_regime(snap, snap4h)
    rc = REGIME_CONFIGS[regime]
    p  = snap
    market = cfg.get("market", "crypto")

    # ── MACRO REGIME BTC (solo per CRYPTO) ────────────────────
    # Il filtro BTC EMA200 vale solo per le crypto: forex e stocks
    # non sono correlati al ciclo di Bitcoin.
    macro_regime = "NEUTRAL"
    if market == "crypto":
        if shared_state:
            macro_regime, macro_reason = _get_macro_regime(shared_state)
        elif snap4h and snap4h.get("close") and snap4h.get("ema200"):
            close  = snap.get("close", 0)
            ema200 = snap.get("ema200", 0)
            if ema200 > 0:
                if close > ema200 * 1.02:
                    macro_regime = "BULL_MACRO"
                elif close < ema200 * 0.98:
                    macro_regime = "BEAR_MACRO"

    # ── BLOCCO TREND_BEAR di regime (4H) ─────────────────────
    if regime == "TREND_BEAR":
        reversal = sum([
            p.get("bull_divergence", False),
            p.get("macd_cross_up", False),
            p.get("vol_ratio", 1) >= 2.5,
            p.get("stoch_cross_up", False) and p.get("stoch_oversold", False),
        ])
        if reversal < 3:
            return 0, [f"🔴 {rc.label} — blocco acquisti (inversione {reversal}/4)"]

    # ── SELEZIONE STRATEGIA ADATTIVA ──────────────────────────
    # Ogni mercato usa la sua logica specifica:
    # crypto → selettore adattivo (trend/mean reversion/breakout)
    # forex  → trend following direzionale
    # stocks → momentum + breakout istituzionale
    base_score, strat_reasons, strategy_name = compute_hybrid_score(regime, p, snap4h, market)

    # In BEAR_MACRO blocca Mean Reversion su crypto
    if macro_regime == "BEAR_MACRO" and strategy_name in ("MEAN_REVERSION",):
        return 0, [
            f"🐻 BEAR MACRO — Mean Reversion vietata (BTC sotto EMA200)",
            f"Attendere mercato rialzista strutturale"
        ]

    # ── FILTRO ANTI-TREND per la MEAN REVERSION ───────────────
    # Non comprare l'ipervenduto se il trend DI FONDO dell'asset (4H) è ribassista:
    # in un downtrend l'ipervenduto diventa più ipervenduto (compri il calo e
    # continua a scendere). È la causa principale delle perdite nel backtest
    # (LTC/DOT/ADA in trend ribassista). Trend Following e Breakout non sono
    # toccati: richiedono già struttura rialzista per definizione.
    if strategy_name == "MEAN_REVERSION" and snap4h:
        c4    = snap4h.get("close", 0)
        e50   = snap4h.get("ema50", 0)
        macd4 = snap4h.get("macd_hist", 0)
        rsi4  = snap4h.get("rsi", 50)
        bearish_4h = (
            (e50 and c4 < e50 * 0.985)              # prezzo nettamente sotto EMA50 4H
            or (macd4 < 0 and rsi4 < 45)            # momentum 4H negativo e debole
            or snap4h.get("trend_aligned_bear")     # EMA 4H allineate al ribasso
        )
        if bearish_4h:
            return 0, [
                "🚫 Mean reversion bloccata — trend 4H ribassista",
                "Niente acquisti contro il trend di fondo",
            ]

    # Forex senza trend → non entrare mai
    if strategy_name == "FOREX_NO_TRADE":
        return 0, strat_reasons

    strat_labels = {
        "TREND_FOLLOWING":  "📈 Trend Following",
        "MEAN_REVERSION":   "🔄 Mean Reversion",
        "BREAKOUT":         "💥 Breakout",
        "FOREX_TREND":      "💱 Forex Trend",
        "STOCKS_TREND":     "📈 Stocks Trend",
        "STOCKS_BREAKOUT":  "💥 Stocks Breakout",
    }
    reasons = [
        f"{rc.emoji} {rc.label}",
        f"Strategia: {strat_labels.get(strategy_name, strategy_name)}",
    ]
    if macro_regime != "NEUTRAL":
        reasons.append(f"Macro: {'🐂 BULL' if macro_regime == 'BULL_MACRO' else '🐻 BEAR'} (BTC EMA200)")
    reasons.extend(strat_reasons)

    score = base_score

    # Penalità se compriamo in TREND_BEAR di regime
    if regime == "TREND_BEAR":
        score -= 2.0
        reasons.append("⚠️ Acquisto in TREND_BEAR — rischio elevato")

    # In BEAR_MACRO anche Trend Following richiede segnali più forti
    if macro_regime == "BEAR_MACRO":
        score -= 1.5
        reasons.append("⚠️ Bear macro — soglia alzata")

    # ── CONTESTO GLOBALE ──────────────────────────────────────
    if shared_state:
        symbol  = cfg.get("_symbol", "")
        ctx_mod, ctx_label = _get_context_modifier(symbol, shared_state)
        if ctx_mod != 0:
            score += ctx_mod
            tag = "+" if ctx_mod > 0 else ""
            reasons.append(f"Contesto: {tag}{ctx_mod:.1f} ({ctx_label})")

    # ── PUNTO 4: Orario preferenziale crypto ─────────────────
    # Fuori dalla finestra 14-20 UTC (mercati USA chiusi) soglia più alta
    if market == "crypto":
        hour_utc = __import__("datetime").datetime.utcnow().hour
        if not (14 <= hour_utc < 20):
            if score < 7:
                reasons.append(f"⏰ Fuori orario preferenziale (14-20 UTC) — score min 7")
                return int(max(0, score)), reasons

    # ── PUNTO 4: soglia minima in BEAR_MACRO alzata a 8 ──────
    if macro_regime == "BEAR_MACRO" and score < 8:
        return int(max(0, score)), reasons + ["🐻 Bear macro — score insufficiente (min 8)"]

    return int(max(0, score)), reasons


# ══════════════════════════════════════════════════════════════
# USCITA INTELLIGENTE
# ══════════════════════════════════════════════════════════════

def score_sell(snap: dict, pos: dict, live_price: float, cfg: dict) -> tuple[str | None, str | None]:
    if not snap:
        return None, None

    entry  = pos["entry_price"]
    # Usa i livelli SALVATI al momento del buy (coerenza totale con le notifiche)
    # Se mancano (vecchie posizioni), li ricalcola come fallback
    regime = pos.get("regime_at_entry") or detect_regime(snap, pos.get("snap4h_at_entry"))[0]
    rc     = REGIME_CONFIGS[regime]
    atr    = pos.get("atr_at_entry", snap.get("atr", entry * cfg.get("sl_pct", 0.05)))

    sl_price  = pos.get("sl_price")  or get_adapted_sl(entry, atr, cfg, regime)
    tp1_price = pos.get("tp1_price") or get_adapted_tp(entry, cfg, regime)[0]
    tp2       = pos.get("tp2_price") or get_adapted_tp(entry, cfg, regime)[1]

    if live_price > pos.get("max_price", entry):
        pos["max_price"] = live_price
    max_price    = pos.get("max_price", entry)
    pnl_pct      = (live_price - entry)     / entry     * 100
    pnl_from_max = (live_price - max_price) / max_price * 100

    if pnl_pct >= 10:  trail_pct = 0.012
    elif pnl_pct >= 6: trail_pct = 0.018
    elif pnl_pct >= 3: trail_pct = 0.022
    else:              trail_pct = 0.028
    trailing_sl = max_price * (1 - trail_pct)

    # 1. Stop Loss
    if live_price <= sl_price:
        return f"Stop Loss {rc.emoji} {pnl_pct:.2f}%", "STOP_LOSS"

    # 1b. Uscita intelligente NEGATIVA — esce prima dello SL SOLO se il ribasso
    # è una rottura di momentum CONFERMATA (non un semplice ipervenduto).
    # Attiva solo se in perdita > 1.5% e non ha ancora toccato TP1.
    if pnl_pct < -1.5 and not pos.get("tp1_hit"):
        rsi = snap.get("rsi", 50)

        # Se siamo in zona ipervenduto / setup di rimbalzo, NON uscire in difensiva:
        # un rimbalzo è probabile e lo stop loss fisso fa già da rete di sicurezza.
        # (Uscire qui significherebbe vendere proprio sul fondo — il bug storico.)
        oversold_bounce = (
            rsi < 38
            or snap.get("rsi_oversold")
            or snap.get("bull_divergence")
            or snap.get("hammer") or snap.get("morning_star") or snap.get("piercing")
            or (snap.get("stoch_oversold") and snap.get("stoch_cross_up"))
            or snap.get("rsi_rising")
        )

        if not oversold_bounce:
            neg = 0
            notes = []

            # MACD: rottura ribassista (momentum, non ipervenduto)
            if snap.get("macd_cross_down"):
                neg += 3; notes.append("MACD cross ribassista")
            elif snap.get("macd_hist", 0) < 0 and snap.get("macd_momentum_down"):
                neg += 2; notes.append("MACD negativo e in peggioramento")

            # Pattern ribassisti forti
            if snap.get("three_black") or snap.get("bear_engulf"):
                neg += 3; notes.append("Pattern ribassista forte")
            elif snap.get("shooting_star") or snap.get("gravestone_doji"):
                neg += 1; notes.append("Candela ribassista")

            # Divergenza ribassista
            if snap.get("bear_divergence"):
                neg += 2; notes.append("Divergenza ribassista")

            # ADX: trend ribassista forte in corso
            if snap.get("adx_strong") and snap.get("adx_bear"):
                neg += 2; notes.append("ADX forte ribassista")

            # Ichimoku sotto la cloud
            if snap.get("ichi_below_cloud"):
                neg += 1; notes.append("Sotto cloud Ichimoku")

            # Volume in espansione che CONFERMA il ribasso (con MACD negativo)
            if snap.get("vol_spike") and snap.get("macd_hist", 0) < 0:
                neg += 1; notes.append("Volume in espansione al ribasso")

            # Richiede ALMENO un segnale forte di rottura, non solo accumulo di deboli
            strong = (
                snap.get("macd_cross_down")
                or snap.get("three_black") or snap.get("bear_engulf")
                or (snap.get("adx_strong") and snap.get("adx_bear"))
            )

            # Soglia: 5+ punti E un segnale forte → esci prima dello SL
            if neg >= 5 and strong:
                return (
                    f"Uscita difensiva {pnl_pct:.2f}% ({', '.join(notes[:3])})",
                    "SMART_EXIT"
                )

    # 2. Trailing stop (dopo TP1)
    if pos.get("tp1_hit") and live_price <= trailing_sl:
        return f"Trailing stop ({pnl_pct:+.2f}%)", "TRAILING_STOP"

    # 3. Uscita intelligente in PROFITTO — protegge il guadagno SOLO su
    # inversione forte e confermata. Obiettivo: lasciare respirare i vincenti
    # fino al TP1 invece di incassare +2% su un singolo segnale debole.
    if pnl_pct > 0.5:
        rev = 0
        notes = []

        if snap.get("macd_cross_down"):
            rev += 3; notes.append("MACD cross ribassista")
        elif snap.get("macd_momentum_down") and snap.get("macd_hist", 0) < 0:
            rev += 2; notes.append("MACD gira negativo")

        if p_get(snap, "rsi_overbought") or snap.get("rsi", 50) > 70:
            rev += 2; notes.append(f"RSI ipercomprato")
        elif snap.get("rsi_falling") and snap.get("rsi", 50) > 60:
            rev += 1; notes.append("RSI in calo da zona alta")

        if snap.get("bear_engulf") or snap.get("evening_star") or snap.get("three_black"):
            rev += 2; notes.append("Pattern ribassista")
        elif snap.get("shooting_star") or snap.get("hanging_man") or snap.get("gravestone_doji"):
            rev += 1; notes.append("Candela ribassista")

        if snap.get("bear_divergence"):
            rev += 2; notes.append("Divergenza ribassista")

        if snap.get("near_resistance"):
            dist_r = snap.get("dist_from_resistance", 1)
            rev += 1; notes.append(f"Vicino resistenza ({dist_r*100:.1f}%)")

        if snap.get("adx_bear") and snap.get("adx_cross_down"):
            rev += 1; notes.append("ADX gira ribassista")

        if snap.get("ichi_below_cloud") or snap.get("ichi_tk_cross_dn"):
            rev += 1; notes.append("Ichimoku bearish")

        if snap.get("stoch_overbought") and snap.get("stoch_cross_down"):
            rev += 1; notes.append("StochRSI ipercomprato e in calo")

        if snap.get("death_cross"):
            rev += 2; notes.append("Death Cross EMA50/200")

        if snap.get("cci_overbought"):
            rev += 1; notes.append("CCI ipercomprato")
        if snap.get("wr_overbought"):
            rev += 1; notes.append("Williams %R ipercomprato")
        if snap.get("mfi_overbought"):
            rev += 1; notes.append("MFI ipercomprato")

        # Forte ritracciamento dal massimo con volume = i compratori mollano
        give_back = pnl_from_max < -(trail_pct * 100) and snap.get("vol_spike")
        if give_back:
            rev += 2; notes.append("Calo dal massimo con volume")

        # Serve un segnale FORTE, non solo un accumulo di oscillatori ipercomprati
        strong = (
            snap.get("macd_cross_down")
            or snap.get("bear_engulf") or snap.get("evening_star") or snap.get("three_black")
            or snap.get("death_cross")
            or give_back
        )

        if not pos.get("tp1_hit"):
            # PRIMA del TP1: dai spazio al trade per arrivare al target.
            # Esci in anticipo solo se c'è un guadagno reale da proteggere (≥3%)
            # E un'inversione forte e netta (soglia alta). Altrimenti lo lasci correre:
            # ci pensano il TP1 a incassare e lo stop loss a proteggere.
            if pnl_pct >= 3 and rev >= 6 and strong:
                return (
                    f"Uscita intelligente +{pnl_pct:.2f}% ({', '.join(notes[:3])})",
                    "SMART_EXIT"
                )
        else:
            # DOPO il TP1: il rischio è già stato tolto (venduto il 50%).
            # Lascia correre il resto — il trailing stop fa il suo lavoro.
            # Esci prima solo su un'inversione forte e netta.
            if rev >= 5 and strong:
                return (
                    f"Uscita intelligente +{pnl_pct:.2f}% ({', '.join(notes[:3])})",
                    "SMART_EXIT"
                )

    # 4. Take Profit 2
    if live_price >= tp2:
        return f"TP2 {rc.emoji} +{pnl_pct:.2f}%", "TAKE_PROFIT_2"

    # 5. Take Profit 1 (50%)
    if not pos.get("tp1_hit") and live_price >= tp1_price:
        return f"TP1 {rc.emoji} 50% +{pnl_pct:.2f}%", "TAKE_PROFIT_1"

    return None, None


def p_get(snap, key):
    return snap.get(key, False)


# ══════════════════════════════════════════════════════════════
# GUARDIE
# ══════════════════════════════════════════════════════════════

def _get_macro_regime(shared_state: dict) -> tuple[str, str]:
    """
    Determina il macro-regime del mercato basandosi su BTC.
    Questo è il filtro più importante — se BTC è in bear market
    strutturale, Mean Reversion non funziona su nessun asset.

    BULL_MACRO:  BTC sopra EMA200 — mercato rialzista strutturale
    BEAR_MACRO:  BTC sotto EMA200 — bear market, solo Trend Following
    NEUTRAL:     Dati non disponibili
    """
    btc = shared_state.get("snapshots", {}).get("BTC/EUR") if shared_state else None
    if not btc:
        return "NEUTRAL", "BTC dati non disponibili"

    close  = btc.get("close", 0)
    ema200 = btc.get("ema200", 0)

    if close <= 0 or ema200 <= 0:
        return "NEUTRAL", "EMA200 non calcolata"

    dist_pct = (close - ema200) / ema200 * 100

    if close > ema200 * 1.02:   # BTC >2% sopra EMA200 = bull confermato
        return "BULL_MACRO", f"BTC sopra EMA200 (+{dist_pct:.1f}%)"
    elif close < ema200 * 0.98: # BTC >2% sotto EMA200 = bear confermato
        return "BEAR_MACRO", f"BTC sotto EMA200 ({dist_pct:.1f}%)"
    else:
        return "NEUTRAL", f"BTC vicino EMA200 ({dist_pct:+.1f}%)"


def market_is_healthy(shared_state: dict) -> tuple[bool, str]:
    """
    Blocca acquisti in condizioni di mercato sfavorevoli.
    Usa il macro-regime BTC come filtro principale.
    """
    btc = shared_state.get("snapshots", {}).get("BTC/EUR") if shared_state else None
    if not btc:
        return True, "ok"

    rsi    = btc.get("rsi", 50)
    atr    = btc.get("atr_pct", 0)
    macd_h = btc.get("macd_hist", 0)
    macd_d = btc.get("macd_cross_down", False)

    # Flash crash violento — stop totale
    if rsi < 20:
        return False, f"Flash crash BTC RSI={rsi:.0f}"

    # Volatilità estrema
    if atr > 0.08:
        return False, f"BTC volatilità estrema ATR={atr*100:.0f}%"

    # Discesa attiva
    if rsi < 35 and macd_h < 0:
        return False, f"BTC in discesa RSI={rsi:.0f} MACD negativo"

    # MACD appena girato al ribasso
    if macd_d and rsi < 45:
        return False, f"BTC MACD cross ribassista RSI={rsi:.0f}"

    return True, "ok"


def can_buy(symbol: str, shared_state: dict, cfg: dict) -> tuple[bool, str]:
    positions  = shared_state["positions"]
    daily_loss = shared_state.get("daily_loss_eur", 0.0)
    last_t     = shared_state.get("last_trade_time", {}).get(symbol, 0)
    sl_count   = shared_state.get("stop_loss_count", {}).get(symbol, 0)
    market     = cfg.get("market", "crypto")

    # ── CIRCUIT BREAKER GLOBALE ───────────────────────────────
    # Se nelle ultime 2 ore ci sono stati 4+ stop loss su qualsiasi asset → pausa
    circuit_breaker_until = shared_state.get("circuit_breaker_until", 0)
    if time.time() < circuit_breaker_until:
        rem = int((circuit_breaker_until - time.time()) / 60)
        return False, f"⚡ Circuit breaker attivo — pausa {rem} min (troppi SL)"

    # Conta SL recenti (ultima ora) su tutti gli asset
    recent_sl = shared_state.get("recent_stop_losses", [])
    now = time.time()
    recent_sl = [t for t in recent_sl if now - t < 3600]  # solo ultima ora
    shared_state["recent_stop_losses"] = recent_sl
    if len(recent_sl) >= 4:
        # Attiva circuit breaker per 2 ore
        shared_state["circuit_breaker_until"] = now + 7200
        # Notifica Telegram
        try:
            asyncio.create_task(_telegram_send(
                f"⚡ <b>Circuit Breaker Attivato</b>\n\n"
                f"{len(recent_sl)} stop loss nell'ultima ora.\n"
                f"Bot in pausa per 2 ore fino alle "
                f"{__import__('datetime').datetime.utcfromtimestamp(now+7200).strftime('%H:%M')} UTC.\n"
                f"Ripresa automatica al termine."
            ))
        except Exception:
            pass
        return False, f"⚡ Circuit breaker — {len(recent_sl)} SL in 1h → pausa 2 ore"

    # Import cooldown e limiti specifici per mercato
    from settings import (FOREX_COOLDOWN, STOCKS_COOLDOWN,
                          MAX_OPEN_CRYPTO, MAX_OPEN_FOREX, MAX_OPEN_STOCKS)

    # Conta posizioni aperte TOTALI e PER MERCATO
    open_total = 0
    open_by_market = {"crypto": 0, "forex": 0, "stocks": 0}
    for v in positions.values():
        if isinstance(v, dict) and v.get("status") == "IN_POSITION":
            open_total += 1
            m = v.get("market", "crypto")
            open_by_market[m] = open_by_market.get(m, 0) + 1

    if open_total >= MAX_OPEN_POSITIONS:
        return False, f"Max posizioni totali ({open_total}/{MAX_OPEN_POSITIONS})"

    # Limite per mercato
    market_limits = {"crypto": MAX_OPEN_CRYPTO, "forex": MAX_OPEN_FOREX, "stocks": MAX_OPEN_STOCKS}
    mlimit = market_limits.get(market, 99)
    if open_by_market.get(market, 0) >= mlimit:
        return False, f"Max posizioni {market} ({open_by_market[market]}/{mlimit})"

    if daily_loss >= MAX_DAILY_LOSS_EUR:
        return False, f"Perdita giornaliera limite"

    # Cooldown specifico per mercato
    cooldowns = {"crypto": TRADE_COOLDOWN, "forex": FOREX_COOLDOWN, "stocks": STOCKS_COOLDOWN}
    base_cd = cooldowns.get(market, TRADE_COOLDOWN)
    if time.time() - last_t < base_cd:
        rem = int(base_cd - (time.time() - last_t))
        return False, f"Cooldown {rem}s ({market})"

    # 3 stop loss consecutivi in TREND_BEAR = aspetta inversione
    if sl_count >= 3:
        snap1h = shared_state.get("snapshots", {}).get(symbol, {})
        snap4h = shared_state.get("snapshots4h", {}).get(symbol)
        if snap1h:
            from regime import detect_regime
            regime, _, _ = detect_regime(snap1h, snap4h)
            if regime == "TREND_BEAR":
                return False, f"{sl_count} SL consecutivi in TREND_BEAR — aspetto inversione"
            else:
                shared_state.get("stop_loss_count", {})[symbol] = 0

    # ── PUNTO 3: Limite correlazione asset ───────────────────
    CORRELATED_GROUP = {"BTC/EUR","ETH/EUR","SOL/EUR","XRP/EUR","ADA/EUR","DOT/EUR"}
    if symbol in CORRELATED_GROUP:
        corr_open = sum(1 for s, v in positions.items()
                       if isinstance(v, dict) and v.get("status") == "IN_POSITION"
                       and s in CORRELATED_GROUP)
        if corr_open >= 3:
            return False, f"Max 3 posizioni su asset correlati ({corr_open}/3 aperte)"

    # ── PUNTO 6: Diversificazione settoriale crypto ───────────
    SECTORS = {
        "layer1":  {"BTC/EUR","ETH/EUR","SOL/EUR","ADA/EUR","DOT/EUR","NEAR/EUR","AVAX/EUR","SUI/EUR"},
        "layer2":  {"ONDO/EUR","ARB/EUR","OP/EUR","STRK/EUR","ZK/EUR","MANTA/EUR"},
        "defi":    {"UNI/EUR","AAVE/EUR","CRV/EUR","LDO/EUR","PENDLE/EUR","DYDX/EUR"},
        "exchange":{"BNB/EUR","OKB/EUR","CRO/EUR"},
        "privacy": {"XMR/EUR","ZEC/EUR","DASH/EUR"},
        "oracle":  {"LINK/EUR","BAND/EUR","API3/EUR"},
        "ai":      {"FET/EUR","OCEAN/EUR","RNDR/EUR","RENDER/EUR","TAO/EUR"},
    }
    for sector, members in SECTORS.items():
        if symbol in members:
            sector_open = sum(1 for s, v in positions.items()
                             if isinstance(v, dict) and v.get("status") == "IN_POSITION"
                             and s in members)
            if sector_open >= 2:
                return False, f"Max 2 posizioni per settore {sector} ({sector_open}/2)"
            break

    # Filtro BTC SOLO per crypto (forex e stocks non sono correlati a BTC)
    if market == "crypto":
        ok, reason = market_is_healthy(shared_state)
        if not ok:
            return False, reason

    return True, ""


def get_asset_regime(symbol: str, shared_state: dict) -> tuple[Regime, str]:
    snap1h = shared_state.get("snapshots",   {}).get(symbol)
    snap4h = shared_state.get("snapshots4h", {}).get(symbol)
    if not snap1h:
        return "LATERAL", "no data"
    regime, reason, _ = detect_regime(snap1h, snap4h)
    return regime, reason
