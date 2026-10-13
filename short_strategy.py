"""
short_strategy.py — Strategia SHORT per mercati ribassisti

Attivata quando BTC è sotto EMA200 (BEAR_MACRO) e i segnali
confermano una continuazione del ribasso.

SICUREZZA:
  - Size massima 15% del budget (mai leva > 1x in DRY RUN)
  - Stop loss strettissimo (1.5% max)
  - Richiede 4/5 segnali ribassisti simultanei
  - Disabilitata su stocks (rischio short squeeze)
  - Solo su crypto con alta liquidità (BTC, ETH, SOL)
"""

# Asset abilitati allo short (solo i più liquidi)
SHORT_ELIGIBLE = {"BTC/EUR", "ETH/EUR", "SOL/EUR", "XRP/EUR"}

# Budget massimo per posizione short (% del budget normale)
SHORT_SIZE_PCT = 0.15


def score_short(snap: dict, cfg: dict, snap4h: dict = None) -> tuple[int, list[str]]:
    """
    Score 0-10 per aprire una posizione SHORT.
    Soglia: 5/10 — più alta del long per maggiore cautela.

    Richiede segnali ribassisti forti e simultanei.
    """
    score   = 0.0
    reasons = []
    p       = snap

    # ── 1. RSI ipercomprato (0-2.5) ──
    rsi = p.get("rsi", 50)
    if rsi > 80:
        score += 2.5; reasons.append(f"RSI ipercomprato estremo {rsi:.0f}")
    elif rsi > 72:
        score += 2.0; reasons.append(f"RSI ipercomprato {rsi:.0f}")
    elif rsi > 65:
        score += 1.2; reasons.append(f"RSI zona overbought {rsi:.0f}")
    elif rsi > 58:
        score += 0.5; reasons.append(f"RSI alto {rsi:.0f}")

    # ── 2. Posizione alta nelle bande (0-2.5) ──
    bb_pct = p.get("bb_pct", 0.5)
    if bb_pct >= 1.0:
        score += 2.5; reasons.append("Prezzo SOPRA banda superiore BB")
    elif bb_pct >= 0.90:
        score += 2.0; reasons.append(f"Massimo assoluto BB ({bb_pct*100:.0f}%)")
    elif bb_pct >= 0.78:
        score += 1.5; reasons.append(f"Zona alta BB ({bb_pct*100:.0f}%)")
    elif bb_pct >= 0.65:
        score += 0.8; reasons.append(f"BB alto ({bb_pct*100:.0f}%)")

    # ── 3. MACD gira al ribasso (0-2) ──
    if p.get("macd_cross_down"):
        score += 2.0; reasons.append("MACD crossover ribassista")
    elif p.get("macd_momentum_down") and p.get("macd_hist", 0) < 0:
        score += 1.5; reasons.append("MACD gira negativo")
    elif p.get("macd_momentum_down"):
        score += 0.8; reasons.append("MACD in decelerazione")

    # ── 4. ADX ribassista (0-1.5) ──
    adx = p.get("adx", 0)
    if p.get("adx_strong") and p.get("adx_bear"):
        score += 1.5; reasons.append(f"ADX forte ({adx:.0f}) trend ribassista")
    elif p.get("adx_cross_down"):
        score += 1.0; reasons.append("ADX: DI- supera DI+")
    elif p.get("adx_bear"):
        score += 0.5; reasons.append("DI- > DI+")

    # ── 5. Divergenza ribassista RSI (0-1.5) ──
    if p.get("bear_divergence"):
        score += 1.5; reasons.append("Divergenza ribassista RSI")

    # ── 6. Pattern ribassisti (0-1.5) ──
    if p.get("evening_star"):
        score += 1.5; reasons.append("Evening Star")
    elif p.get("three_black"):
        score += 1.3; reasons.append("Three Black Crows")
    elif p.get("bear_engulf"):
        score += 1.0; reasons.append("Bearish Engulfing")
    elif p.get("shooting_star") or p.get("hanging_man"):
        score += 0.7; reasons.append("Pattern ribassista")
    elif p.get("gravestone_doji"):
        score += 0.7; reasons.append("Gravestone Doji")

    # ── 7. Volume su ribasso (0-1) ──
    vol = p.get("vol_ratio", 1.0)
    if vol >= 2.0 and not p.get("obv_rising"):
        score += 1.0; reasons.append(f"Volume ribassista {vol:.1f}x")
    elif vol >= 1.5:
        score += 0.5; reasons.append(f"Volume elevato {vol:.1f}x")

    # ── 8. Ichimoku bearish (0-1) ──
    if p.get("ichi_below_cloud"):
        score += 1.0; reasons.append("Ichimoku: sotto la cloud")
    elif p.get("ichi_tk_cross_dn"):
        score += 0.7; reasons.append("Ichimoku: TK cross ribassista")

    # ── 9. StochRSI overbought (0-0.5) ──
    if p.get("stoch_overbought") and p.get("stoch_cross_down"):
        score += 0.5; reasons.append("StochRSI overbought + crossover down")

    # ── 10. Vicino resistenza (0-0.5) ──
    if p.get("near_resistance"):
        score += 0.5; reasons.append("Prezzo su resistenza dinamica")

    # Conferma 4H
    if snap4h:
        rsi4h = snap4h.get("rsi", 50)
        if rsi4h > 65 and snap4h.get("macd_hist", 0) < 0:
            score += 0.5; reasons.append(f"4H conforme ribassista RSI={rsi4h:.0f}")

    return int(score), reasons


def can_short(symbol: str, shared_state: dict) -> tuple[bool, str]:
    """
    Controlla se è possibile aprire uno short su questo asset.
    Più restrittivo del long per sicurezza.
    """
    if symbol not in SHORT_ELIGIBLE:
        return False, f"{symbol} non abilitato allo short"

    # Verifica macro regime: solo in BEAR_MACRO
    from strategy import _get_macro_regime
    macro, reason = _get_macro_regime(shared_state)
    if macro != "BEAR_MACRO":
        return False, f"Short solo in BEAR_MACRO (ora: {macro})"

    # Controlla posizioni short aperte
    positions = shared_state.get("positions", {})
    open_shorts = sum(
        1 for v in positions.values()
        if isinstance(v, dict) and v.get("status") == "IN_POSITION"
        and v.get("direction") == "short"
    )
    if open_shorts >= 2:  # max 2 short contemporanei
        return False, f"Max short aperti ({open_shorts}/2)"

    return True, ""


def score_short_exit(snap: dict, pos: dict, live_price: float, cfg: dict) -> tuple[str | None, str | None]:
    """
    Uscita da posizione short.
    Il profitto su short = entry - live (inverso del long).
    """
    entry    = pos["entry_price"]
    sl_price = pos.get("sl_price", entry * 1.015)   # SL = 1.5% sopra entry
    tp1      = pos.get("tp1_price", entry * 0.97)   # TP1 = -3%
    tp2      = pos.get("tp2_price", entry * 0.94)   # TP2 = -6%

    pnl_pct = (entry - live_price) / entry * 100    # positivo se prezzo scende

    # Stop Loss: prezzo sale troppo
    if live_price >= sl_price:
        return f"Short SL {pnl_pct:.2f}%", "STOP_LOSS_SHORT"

    # Take Profit 2
    if live_price <= tp2:
        return f"Short TP2 +{pnl_pct:.2f}%", "TAKE_PROFIT_SHORT_2"

    # Take Profit 1
    if not pos.get("tp1_hit") and live_price <= tp1:
        return f"Short TP1 50% +{pnl_pct:.2f}%", "TAKE_PROFIT_SHORT_1"

    # Smart Exit short: chiudi se il mercato gira rialzista
    if pnl_pct > 0.5:
        reversal = sum([
            snap.get("macd_cross_up", False),
            snap.get("bull_divergence", False),
            snap.get("rsi_oversold", False),
            snap.get("bull_engulf", False) or snap.get("hammer", False),
            snap.get("stoch_cross_up", False) and snap.get("stoch_oversold", False),
        ])
        if reversal >= 3:
            return f"Smart Exit Short +{pnl_pct:.2f}% (mercato gira)", "SMART_EXIT_SHORT"

    return None, None
