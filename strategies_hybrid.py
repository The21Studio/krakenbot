"""
strategies_hybrid.py — Sistema IBRIDO multi-strategia

Il bot rileva il tipo di mercato e sceglie automaticamente la strategia
di ingresso più adatta. Ogni strategia ha la sua logica di entrata.

┌─────────────────────┬──────────────────────────────────────────────┐
│ TIPO DI MERCATO      │ STRATEGIA USATA                              │
├─────────────────────┼──────────────────────────────────────────────┤
│ TREND_BULL (ADX>25)  │ TREND FOLLOWING — segue il trend al rialzo   │
│ TREND_BEAR          │ NESSUN ACQUISTO (salvo inversione fortissima)│
│ LATERAL (ADX<20)    │ MEAN REVERSION — compra minimi, vende massimi│
│ HIGH_VOL / squeeze  │ BREAKOUT — entra sull'esplosione del squeeze │
└─────────────────────┴──────────────────────────────────────────────┘

Ogni funzione ritorna (score 0-12, lista motivazioni, nome_strategia).
"""


# ══════════════════════════════════════════════════════════════
# STRATEGIA 1: TREND FOLLOWING
# Per mercati in trend rialzista forte (ADX > 25, EMA allineate)
# Filosofia: "the trend is your friend" — entra e cavalca
# ══════════════════════════════════════════════════════════════

def score_trend_following(p: dict, snap4h: dict = None) -> tuple[float, list[str]]:
    """
    Compra quando il trend rialzista è FORTE e CONFERMATO.
    Non aspetta i minimi — entra sulla forza.
    """
    score   = 0.0
    reasons = []

    # ── Forza del trend (ADX) — IL CUORE di questa strategia (0-3) ──
    adx = p.get("adx", 0)
    if adx > 40 and p.get("adx_bull"):
        score += 3.0; reasons.append(f"ADX fortissimo {adx:.0f} — trend potente")
    elif adx > 30 and p.get("adx_bull"):
        score += 2.5; reasons.append(f"ADX forte {adx:.0f} + DI+ dominante")
    elif adx > 25 and p.get("adx_bull"):
        score += 2.0; reasons.append(f"ADX trend confermato {adx:.0f}")
    elif p.get("adx_cross_up"):
        score += 1.5; reasons.append("ADX: DI+ supera DI- (trend nasce)")

    # ── Allineamento EMA (0-2.5) ──
    if p.get("golden_cross"):
        score += 2.5; reasons.append("Golden Cross EMA50/200 (segnale storico)")
    elif p.get("trend_aligned_bull") and p.get("trend_bull_long"):
        score += 2.0; reasons.append("EMA 9>21>50 allineate + sopra EMA200")
    elif p.get("trend_aligned_bull"):
        score += 1.5; reasons.append("EMA 9>21>50 allineate al rialzo")
    elif p.get("trend_bull_long"):
        score += 0.8; reasons.append("Prezzo sopra EMA200")

    # Three White Soldiers — pattern di continuazione trend forte
    if p.get("three_white"):
        score += 1.0; reasons.append("Three White Soldiers (continuazione)")

    # Forza direzionale DI+ vs DI- (gap ampio = trend pulito)
    di_plus  = p.get("di_plus", 0)
    di_minus = p.get("di_minus", 0)
    if di_plus > di_minus * 1.5 and di_plus > 25:
        score += 0.5; reasons.append(f"DI+ domina nettamente ({di_plus:.0f} vs {di_minus:.0f})")

    # ── MACD conferma il momentum (0-2) ──
    macd_h = p.get("macd_hist", 0)
    if macd_h > 0 and p.get("macd_momentum_up"):
        score += 2.0; reasons.append("MACD positivo e in accelerazione")
    elif macd_h > 0:
        score += 1.2; reasons.append("MACD positivo")
    elif p.get("macd_cross_up"):
        score += 1.5; reasons.append("MACD crossover appena avvenuto")

    # ── Ichimoku: prezzo sopra la cloud (0-1.5) ──
    if p.get("ichi_above_cloud") and p.get("ichi_cloud_bull"):
        score += 1.5; reasons.append("Ichimoku: sopra cloud verde")
    elif p.get("ichi_above_cloud"):
        score += 1.0; reasons.append("Ichimoku: sopra la cloud")
    elif p.get("ichi_tk_cross_up"):
        score += 0.8; reasons.append("Ichimoku: TK crossover bullish")

    # ── Volume a conferma (0-1.5) ──
    vol = p.get("vol_ratio", 1.0)
    if vol >= 2.0 and p.get("obv_rising"):
        score += 1.5; reasons.append(f"Volume {vol:.1f}x + OBV sale")
    elif vol >= 1.5:
        score += 1.0; reasons.append(f"Volume sopra media {vol:.1f}x")
    elif p.get("obv_rising"):
        score += 0.5; reasons.append("OBV in salita")

    # ── RSI non deve essere ipercomprato estremo (0-1) ──
    rsi = p.get("rsi", 50)
    if 45 <= rsi <= 65:
        score += 1.0; reasons.append(f"RSI sano in trend {rsi:.0f}")
    elif 65 < rsi <= 72:
        score += 0.3; reasons.append(f"RSI alto ma ok {rsi:.0f}")
    elif rsi > 78:
        score -= 1.0; reasons.append(f"⚠️ RSI ipercomprato {rsi:.0f} — rischio correzione")

    # ── VWAP: sopra = forza (0-0.5) ──
    if p.get("above_vwap"):
        score += 0.5; reasons.append("Prezzo sopra VWAP")

    # ── Conferma 4H ──
    if snap4h:
        if snap4h.get("adx_bull") and snap4h.get("rsi", 50) > 50:
            score += 1.0; reasons.append("Trend 4H rialzista confermato")

    return score, reasons


# ══════════════════════════════════════════════════════════════
# STRATEGIA 2: MEAN REVERSION
# Per mercati laterali (ADX < 20, bande strette)
# Filosofia: "compra basso, vendi alto" nel range
# ══════════════════════════════════════════════════════════════

def score_mean_reversion(p: dict, snap4h: dict = None) -> tuple[float, list[str]]:
    """
    Compra quando il prezzo è al minimo del range E mostra inversione.
    Questa è la strategia che il bot usava — ottima in mercati laterali.
    """
    score   = 0.0
    reasons = []

    # ── RSI ipervenduto (0-3) ──
    rsi = p.get("rsi", 50)
    if rsi < 25:
        score += 3.0; reasons.append(f"RSI estremo {rsi:.0f}")
    elif rsi < 32:
        score += 2.5; reasons.append(f"RSI molto ipervenduto {rsi:.0f}")
    elif rsi < 38:
        score += 2.0; reasons.append(f"RSI ipervenduto {rsi:.0f}")
    elif rsi < 45:
        score += 1.0; reasons.append(f"RSI zona acquisto {rsi:.0f}")

    # ── Posizione nelle bande (0-3) ──
    bb_pct = p.get("bb_pct", 0.5)
    if p.get("close", 1) < p.get("bb_lower", 0):
        score += 3.0; reasons.append("Prezzo SOTTO banda inferiore")
    elif bb_pct <= 0.10:
        score += 2.5; reasons.append(f"Minimo banda ({bb_pct*100:.0f}%)")
    elif bb_pct <= 0.20:
        score += 2.0; reasons.append(f"Zona bassa banda ({bb_pct*100:.0f}%)")
    elif bb_pct <= 0.35:
        score += 1.0; reasons.append(f"Metà bassa banda ({bb_pct*100:.0f}%)")

    # ── Oscillatori di conferma (0-2) ──
    if p.get("stoch_oversold") and p.get("stoch_cross_up"):
        score += 1.5; reasons.append("StochRSI ipervenduto + crossover")
    elif p.get("stoch_oversold"):
        score += 0.7; reasons.append("StochRSI ipervenduto")
    cci = p.get("cci", 0)
    if p.get("cci_oversold"):
        score += 0.5; reasons.append(f"CCI ipervenduto ({cci:.0f})")
    wr = p.get("williams_r", -50)
    if p.get("wr_oversold"):
        score += 0.5; reasons.append(f"Williams %R ipervenduto ({wr:.0f})")
    mfi = p.get("mfi", 50)
    if p.get("mfi_oversold"):
        score += 0.5; reasons.append(f"MFI ipervenduto ({mfi:.0f}) — denaro pronto")

    # MACD che inizia a girare (istogramma migliora rispetto a prima)
    if p.get("macd_hist", 0) > p.get("macd_hist_prev", 0) and p.get("macd_hist", 0) > -0.0005:
        score += 0.5; reasons.append("MACD inizia a girare al rialzo")

    # ── Divergenza rialzista (0-1.5) ──
    if p.get("bull_divergence"):
        score += 1.5; reasons.append("Divergenza rialzista RSI")

    # ── Vicino supporto (0-1) ──
    if p.get("near_support"):
        dist_s = p.get("dist_from_support", 0)
        score += 1.0; reasons.append(f"Prezzo su supporto ({dist_s*100:.1f}%)")

    # ── Pattern candela rialzista (0-1.5) ──
    if p.get("morning_star"):
        score += 1.5; reasons.append("Morning Star")
    elif p.get("bull_engulf"):
        score += 1.0; reasons.append("Bullish Engulfing")
    elif p.get("hammer"):
        score += 0.7; reasons.append("Hammer")
    elif p.get("piercing"):
        score += 0.7; reasons.append("Piercing Line")
    elif p.get("dragonfly_doji"):
        score += 0.6; reasons.append("Dragonfly Doji (rifiuto del ribasso)")
    elif p.get("inv_hammer"):
        score += 0.5; reasons.append("Inverted Hammer")
    elif p.get("doji") and p.get("near_support"):
        score += 0.4; reasons.append("Doji su supporto (indecisione)")

    # ── Volume (0-0.5) ──
    vol = p.get("vol_ratio", 1.0)
    if vol >= 1.5:
        score += 0.5; reasons.append(f"Volume {vol:.1f}x")

    return score, reasons


# ══════════════════════════════════════════════════════════════
# STRATEGIA 3: BREAKOUT
# Per squeeze di mercato (bande compresse che esplodono)
# Filosofia: la compressione precede l'esplosione
# ══════════════════════════════════════════════════════════════

def score_breakout(p: dict, snap4h: dict = None) -> tuple[float, list[str]]:
    """
    Compra quando una compressione di volatilità si rompe al rialzo.
    I movimenti post-squeeze sono i più forti e direzionali.
    """
    score   = 0.0
    reasons = []

    # ── Squeeze che si rompe — IL SEGNALE PRINCIPALE (0-4) ──
    if p.get("squeeze_off"):
        score += 4.0; reasons.append("🎯 SQUEEZE ROTTO — esplosione in corso")
    elif p.get("bb_squeeze"):
        # Squeeze ancora attivo — prepara ma non entra ancora forte
        score += 1.0; reasons.append("Compressione attiva — breakout imminente")

    # ── Direzione del breakout (deve essere rialzista) (0-2.5) ──
    macd_h = p.get("macd_hist", 0)
    if macd_h > 0 and p.get("macd_momentum_up"):
        score += 2.5; reasons.append("Breakout rialzista (MACD accelera)")
    elif macd_h > 0:
        score += 1.5; reasons.append("MACD positivo conferma direzione")
    elif p.get("macd_cross_up"):
        score += 2.0; reasons.append("MACD crossover sul breakout")

    # ── Volume esplosivo — conferma il breakout (0-3) ──
    vol = p.get("vol_ratio", 1.0)
    if vol >= 3.0:
        score += 3.0; reasons.append(f"Volume esplosivo {vol:.1f}x")
    elif vol >= 2.0:
        score += 2.0; reasons.append(f"Volume forte {vol:.1f}x")
    elif vol >= 1.5:
        score += 1.0; reasons.append(f"Volume in aumento {vol:.1f}x")

    # ── ATR in espansione = volatilità che esplode (0-1.5) ──
    if p.get("atr_expanding"):
        score += 1.5; reasons.append("ATR esplode — movimento ampio")

    # ADX: un breakout con ADX in crescita è più affidabile
    if p.get("adx_strong"):
        score += 0.5; reasons.append("ADX conferma forza del movimento")
    elif p.get("adx_weak"):
        score += 0.3; reasons.append("ADX basso — breakout da compressione")

    # ── Prezzo rompe la resistenza (0-1.5) ──
    if p.get("close", 0) > p.get("resistance", float("inf")) * 0.99:
        score += 1.5; reasons.append("Prezzo rompe la resistenza")

    # ── EMA allineate aiutano (0-1) ──
    if p.get("trend_aligned_bull"):
        score += 1.0; reasons.append("EMA allineate al rialzo")

    # ── RSI con spazio per salire (0-1) ──
    rsi = p.get("rsi", 50)
    if 50 <= rsi <= 68:
        score += 1.0; reasons.append(f"RSI con spazio {rsi:.0f}")
    elif rsi > 75:
        score -= 1.0; reasons.append(f"⚠️ RSI già alto {rsi:.0f}")

    return score, reasons


# ══════════════════════════════════════════════════════════════
# SELETTORE: sceglie la strategia in base al mercato
# ══════════════════════════════════════════════════════════════

def select_strategy(regime: str, p: dict) -> str:
    """
    Decide quale strategia usare in base al regime e agli indicatori.
    ADX è il giudice principale della forza del trend.
    """
    adx = p.get("adx", 0)
    rsi = p.get("rsi", 50)

    # Priorità 1: squeeze in rottura → BREAKOUT
    if p.get("squeeze_off"):
        return "BREAKOUT"

    # Priorità 2: ADX molto basso = range → MEAN REVERSION
    if adx < 18:
        return "MEAN_REVERSION"

    # Priorità 3: RSI estremo → MEAN REVERSION
    if rsi < 32 or rsi > 72:
        return "MEAN_REVERSION"

    # Priorità 4: trend confermato (soglia abbassata a 20 per crypto)
    if adx > 20 and p.get("adx_bull"):
        return "TREND_FOLLOWING"
    if regime == "TREND_BULL":
        return "TREND_FOLLOWING"

    # Priorità 5: alta volatilità senza squeeze
    if regime == "HIGH_VOL":
        return "BREAKOUT"

    return "MEAN_REVERSION"


def compute_hybrid_score(regime: str, p: dict, snap4h: dict = None, market: str = "crypto") -> tuple[float, list[str], str]:
    """
    Calcola lo score usando la strategia adatta al TIPO DI MERCATO.
    
    Ogni mercato ha la sua logica:
      crypto  → selettore adattivo (trend/mean reversion/breakout)
      forex   → principalmente trend following (mercato direzionale)
      stocks  → breakout + trend (orari fissi, news-driven)
    """
    if market == "forex":
        score, reasons, strategy = _score_forex(p, snap4h, regime)
    elif market == "stocks":
        score, reasons, strategy = _score_stocks(p, snap4h, regime)
    else:
        strategy = select_strategy(regime, p)
        if strategy == "TREND_FOLLOWING":
            score, reasons = score_trend_following(p, snap4h)
        elif strategy == "BREAKOUT":
            score, reasons = score_breakout(p, snap4h)
        else:
            score, reasons = score_mean_reversion(p, snap4h)

    return score, reasons, strategy


# ══════════════════════════════════════════════════════════════
# STRATEGIA FOREX — Trend Following direzionale
# Il forex si muove in trend lunghi e lenti. Non fa mean reversion
# come le crypto. ADX + EMA + MACD sono i pilastri.
# Spread piccoli → SL stretto. Trend dura ore/giorni → pazienza.
# ══════════════════════════════════════════════════════════════

def _score_forex(p: dict, snap4h: dict, regime: str) -> tuple[float, list[str], str]:
    """
    Forex: entra SOLO su trend confermato su entrambi i timeframe.
    Non fa mean reversion (il forex non rimbalza come le crypto).
    Soglia più alta: richiede conferme multiple.
    """
    score   = 0.0
    reasons = []

    # 1. ADX — forza trend (fondamentale nel forex) (0-3)
    adx = p.get("adx", 0)
    if adx > 35 and p.get("adx_bull"):
        score += 3.0; reasons.append(f"ADX forte {adx:.0f} — trend forex solido")
    elif adx > 25 and p.get("adx_bull"):
        score += 2.5; reasons.append(f"ADX confermato {adx:.0f}")
    elif adx > 20 and p.get("adx_bull"):
        score += 1.5; reasons.append(f"ADX moderato {adx:.0f}")
    elif p.get("adx_cross_up"):
        score += 1.0; reasons.append("DI+ supera DI- (trend nasce)")
    else:
        # Forex senza trend = non entrare (a differenza delle crypto)
        return 0.0, ["Forex senza trend ADX — aspetto direzione chiara"], "FOREX_NO_TRADE"

    # 2. MACD su 1H confermato (0-2.5)
    macd_h = p.get("macd_hist", 0)
    if p.get("macd_cross_up"):
        score += 2.5; reasons.append("MACD crossover 1H bullish")
    elif macd_h > 0 and p.get("macd_momentum_up"):
        score += 2.0; reasons.append("MACD positivo e in accelerazione")
    elif macd_h > 0:
        score += 1.2; reasons.append("MACD positivo")

    # 3. EMA allineate (0-2)
    if p.get("trend_aligned_bull") and p.get("trend_bull_long"):
        score += 2.0; reasons.append("EMA 9>21>50 + sopra EMA200")
    elif p.get("trend_aligned_bull"):
        score += 1.5; reasons.append("EMA 9>21>50 allineate")
    elif p.get("trend_bull_long"):
        score += 0.8; reasons.append("Sopra EMA200")

    # 4. Conferma 4H — OBBLIGATORIA per forex (0-2)
    if snap4h:
        rsi4h   = snap4h.get("rsi", 50)
        macd4h  = snap4h.get("macd_hist", 0)
        adx4h   = snap4h.get("adx", 0)
        if adx4h > 20 and snap4h.get("adx_bull") and macd4h > 0:
            score += 2.0; reasons.append(f"4H: ADX {adx4h:.0f} + MACD positivo (trend confermato)")
        elif macd4h > 0 and 45 <= rsi4h <= 65:
            score += 1.0; reasons.append(f"4H: MACD ok, RSI sano {rsi4h:.0f}")
        else:
            # 4H non conferma → penalità forte nel forex
            score -= 1.5; reasons.append("⚠️ 4H non conferma il trend")

    # 5. RSI sano — non ipercomprato (0-1)
    rsi = p.get("rsi", 50)
    if 45 <= rsi <= 65:
        score += 1.0; reasons.append(f"RSI sano {rsi:.0f}")
    elif rsi > 72:
        score -= 1.5; reasons.append(f"⚠️ RSI ipercomprato {rsi:.0f}")

    # 6. Volume (forex = meno rilevante ma utile) (0-1)
    if p.get("above_vwap") and p.get("obv_rising"):
        score += 1.0; reasons.append("Sopra VWAP + OBV crescente")

    # 7. Ichimoku sopra cloud (0-1)
    if p.get("ichi_above_cloud") and p.get("ichi_cloud_bull"):
        score += 1.0; reasons.append("Ichimoku: sopra cloud verde")

    return score, reasons, "FOREX_TREND"


# ══════════════════════════════════════════════════════════════
# STRATEGIA STOCKS — Momentum + Breakout
# Le azioni si muovono su earnings, notizie, momentum di settore.
# Trend Following su EMA, Breakout su resistenze chiave.
# Orari fissi (09:30-16:00 ET) → niente trading notturno.
# ══════════════════════════════════════════════════════════════

def _score_stocks(p: dict, snap4h: dict, regime: str) -> tuple[float, list[str], str]:
    """
    Stocks: momentum e breakout.
    Le azioni seguono trend più stabili delle crypto ma con
    rotture violente su notizie/earnings. Strategia mista.
    """
    score   = 0.0
    reasons = []

    # 1. Squeeze breakout (segnale più forte nelle azioni) (0-4)
    if p.get("squeeze_off"):
        score += 4.0; reasons.append("Squeeze rotto — movimento imminente")
        strategy = "STOCKS_BREAKOUT"
    else:
        strategy = "STOCKS_TREND"

    # 2. Momentum EMA (0-2.5)
    if p.get("golden_cross"):
        score += 2.5; reasons.append("Golden Cross (segnale istituzionale)")
    elif p.get("trend_aligned_bull") and p.get("trend_bull_long"):
        score += 2.0; reasons.append("EMA allineate + sopra EMA200")
    elif p.get("trend_aligned_bull"):
        score += 1.5; reasons.append("EMA 9>21>50 allineate")

    # 3. ADX momentum (0-2)
    adx = p.get("adx", 0)
    if adx > 25 and p.get("adx_bull"):
        score += 2.0; reasons.append(f"ADX momentum forte {adx:.0f}")
    elif p.get("adx_cross_up"):
        score += 1.2; reasons.append("ADX: trend in formazione")

    # 4. MACD (0-2)
    if p.get("macd_cross_up"):
        score += 2.0; reasons.append("MACD crossover bullish")
    elif p.get("macd_hist", 0) > 0 and p.get("macd_momentum_up"):
        score += 1.5; reasons.append("MACD positivo e accelera")
    elif p.get("macd_hist", 0) > 0:
        score += 0.8; reasons.append("MACD positivo")

    # 5. Volume istituzionale (fondamentale per stocks) (0-2.5)
    vol = p.get("vol_ratio", 1.0)
    if vol >= 3.0 and p.get("obv_rising"):
        score += 2.5; reasons.append(f"Volume istituzionale {vol:.1f}x + OBV bull")
    elif vol >= 2.0:
        score += 1.5; reasons.append(f"Volume spike {vol:.1f}x")
    elif vol >= 1.5 and p.get("above_vwap"):
        score += 1.2; reasons.append(f"Volume {vol:.1f}x sopra VWAP")

    # 6. Pattern candele (più affidabili nelle azioni) (0-1.5)
    if p.get("three_white"):
        score += 1.5; reasons.append("Three White Soldiers (momentum)")
    elif p.get("bull_engulf") and vol > 1.5:
        score += 1.3; reasons.append("Bullish Engulfing con volume")
    elif p.get("morning_star"):
        score += 1.2; reasons.append("Morning Star")

    # 7. RSI sano con spazio (0-1)
    rsi = p.get("rsi", 50)
    if 50 <= rsi <= 65:
        score += 1.0; reasons.append(f"RSI momentum sano {rsi:.0f}")
    elif rsi > 75:
        score -= 1.5; reasons.append(f"⚠️ RSI ipercomprato {rsi:.0f}")

    # 8. Conferma 4H (0-1)
    if snap4h and snap4h.get("adx_bull") and snap4h.get("rsi", 50) > 50:
        score += 1.0; reasons.append("Trend 4H conferma")

    return score, reasons, strategy
