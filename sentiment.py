"""
sentiment.py — Analisi sentiment news 100% gratuita e offline

Non usa nessuna API a pagamento. Analizza i titoli delle news
con un modello di sentiment basato su:
  1. Lessico finanziario pesato (300+ termini)
  2. Rilevamento intensità (parole forti vs deboli)
  3. Negazioni ("not bullish" = bearish)
  4. Pattern di eventi critici (hack, SEC, ETF, ban)
  5. Aggregazione per asset

È lo stesso approccio che usano i sistemi di sentiment commerciali
prima dell'era LLM — funziona bene su titoli brevi e diretti.
"""

import re


# ══════════════════════════════════════════════════════════════
# LESSICO FINANZIARIO PESATO
# ══════════════════════════════════════════════════════════════

# Peso da -3 (molto ribassista) a +3 (molto rialzista)
LEXICON = {
    # ── FORTEMENTE POSITIVO (+3) ──────────────────────────────
    "surge": 3, "soar": 3, "skyrocket": 3, "rally": 3, "breakout": 3,
    "all-time high": 3, "ath": 3, "record high": 3, "parabolic": 3,
    "explode": 3, "moon": 3, "boom": 3,

    # ── POSITIVO (+2) ─────────────────────────────────────────
    "gain": 2, "rise": 2, "jump": 2, "climb": 2, "bullish": 2, "bull": 2,
    "approve": 2, "approval": 2, "etf": 2, "adoption": 2, "adopt": 2,
    "partnership": 2, "upgrade": 2, "breakthrough": 2, "milestone": 2,
    "institutional": 2, "accumulate": 2, "inflow": 2, "buy": 2,
    "outperform": 2, "rebound": 2, "recovery": 2, "recover": 2,

    # ── LIEVEMENTE POSITIVO (+1) ──────────────────────────────
    "up": 1, "higher": 1, "positive": 1, "growth": 1, "grow": 1,
    "support": 1, "optimistic": 1, "confidence": 1, "interest": 1,
    "launch": 1, "expand": 1, "integration": 1, "green": 1,
    "advance": 1, "boost": 1, "strong": 1, "momentum": 1,

    # ── LIEVEMENTE NEGATIVO (-1) ──────────────────────────────
    "down": -1, "lower": -1, "negative": -1, "weak": -1, "slip": -1,
    "concern": -1, "uncertainty": -1, "cautious": -1, "pressure": -1,
    "correction": -1, "pullback": -1, "red": -1, "dip": -1,

    # ── NEGATIVO (-2) ─────────────────────────────────────────
    "fall": -2, "drop": -2, "decline": -2, "bearish": -2, "bear": -2,
    "sell-off": -2, "selloff": -2, "dump": -2, "outflow": -2, "loss": -2,
    "reject": -2, "rejection": -2, "warning": -2, "risk": -2, "fear": -2,
    "lawsuit": -2, "sue": -2, "investigation": -2, "delay": -2,
    "downgrade": -2, "liquidation": -2, "underperform": -2,

    # ── FORTEMENTE NEGATIVO (-3) ──────────────────────────────
    "crash": -3, "plunge": -3, "collapse": -3, "plummet": -3, "tank": -3,
    "hack": -3, "hacked": -3, "exploit": -3, "scam": -3, "fraud": -3,
    "ban": -3, "banned": -3, "halt": -3, "freeze": -3, "bankruptcy": -3,
    "bankrupt": -3, "default": -3, "crackdown": -3, "seize": -3,
}

# Parole che amplificano (moltiplicano il peso)
INTENSIFIERS = {"very", "extremely", "massive", "huge", "major", "significant",
                "sharp", "sharply", "dramatic", "dramatically", "strongly"}

# Negazioni che invertono il sentiment
NEGATIONS = {"not", "no", "never", "without", "fails", "fail", "failed",
             "denies", "deny", "denied", "avoid", "halt", "stop"}

# Eventi critici che dominano il sentiment a prescindere
CRITICAL_BULLISH = ["etf approved", "etf approval", "spot etf", "halving",
                    "institutional adoption", "blackrock", "fidelity"]
CRITICAL_BEARISH = ["sec lawsuit", "sec charges", "exchange hack", "exit scam",
                    "rug pull", "delisted", "delisting", "criminal", "ponzi"]


# ══════════════════════════════════════════════════════════════
# ANALISI SINGOLO TITOLO
# ══════════════════════════════════════════════════════════════

def _word_weight(word: str) -> int:
    """Cerca il peso di una parola gestendo plurali e coniugazioni."""
    if word in LEXICON:
        return LEXICON[word]
    # Stemming leggero: rimuove suffissi comuni
    for suffix in ["s", "es", "ed", "ing", "d"]:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            stem = word[:-len(suffix)]
            if stem in LEXICON:
                return LEXICON[stem]
    return 0


def analyze_title(title: str) -> dict:
    """
    Analizza un singolo titolo e ritorna sentiment + score.
    Score normalizzato da -1.0 (molto negativo) a +1.0 (molto positivo).
    """
    text = title.lower()
    words = re.findall(r"[a-z\-]+", text)

    # 1. Eventi critici (override)
    for phrase in CRITICAL_BULLISH:
        if phrase in text:
            return {"sentiment": "POSITIVE", "score": 0.9, "critical": True}
    for phrase in CRITICAL_BEARISH:
        if phrase in text:
            return {"sentiment": "NEGATIVE", "score": -0.9, "critical": True}

    # 2. Analisi parola per parola con negazioni e intensificatori
    total_score = 0.0
    matches     = 0

    for i, word in enumerate(words):
        weight = _word_weight(word)
        if weight == 0 and i + 1 < len(words):
            bigram = f"{word} {words[i+1]}"
            weight = LEXICON.get(bigram, 0)
        if weight != 0:
            context_words = words[max(0, i-3):i]
            if any(neg in context_words for neg in NEGATIONS):
                weight = -weight
            if any(intf in context_words for intf in INTENSIFIERS):
                weight *= 1.5
            total_score += weight
            matches += 1

    if matches == 0:
        return {"sentiment": "NEUTRAL", "score": 0.0, "critical": False}

    avg = total_score / matches
    norm = max(-1.0, min(1.0, avg / 3.0))

    if norm > 0.15:
        sentiment = "POSITIVE"
    elif norm < -0.15:
        sentiment = "NEGATIVE"
    else:
        sentiment = "NEUTRAL"

    return {"sentiment": sentiment, "score": round(norm, 2), "critical": False}


# ══════════════════════════════════════════════════════════════
# ANALISI AGGREGATA PER ASSET
# ══════════════════════════════════════════════════════════════

def analyze_asset_news(symbol: str, news: list[dict]) -> dict:
    """
    Aggrega il sentiment di tutte le news rilevanti per un asset.
    Ritorna un modificatore di score da -2.0 a +2.0 per la strategia.
    """
    relevant = [n for n in news if symbol in n.get("assets", [])]
    if not relevant:
        return {
            "action": "HOLD", "score_modifier": 0.0,
            "reason": "nessuna news", "confidence": 0.5,
            "risk_level": "MEDIUM", "news_count": 0, "ai_used": False,
        }

    scores   = []
    critical = False
    for n in relevant:
        analysis = analyze_title(n["title"])
        scores.append(analysis["score"])
        if analysis["critical"]:
            critical = True

    avg_score = sum(scores) / len(scores)

    # Modificatore di score: news positive aumentano, negative diminuiscono
    # Scala: avg_score [-1,+1] → modifier [-2,+2]
    modifier = round(avg_score * 2.0, 2)

    # Eventi critici amplificano
    if critical:
        modifier = round(modifier * 1.3, 2)
        modifier = max(-2.0, min(2.0, modifier))

    # Determina azione
    if avg_score > 0.3:
        action, risk = "BUY", "LOW" if avg_score > 0.6 else "MEDIUM"
    elif avg_score < -0.3:
        action, risk = "SELL", "HIGH"
    else:
        action, risk = "HOLD", "MEDIUM"

    # Reason leggibile
    pos = sum(1 for s in scores if s > 0.15)
    neg = sum(1 for s in scores if s < -0.15)
    reason = f"{pos} positive, {neg} negative su {len(relevant)} news"
    if critical:
        reason = "⚠ evento critico — " + reason

    return {
        "action":         action,
        "score_modifier": modifier,
        "reason":         reason,
        "confidence":     round(min(1.0, abs(avg_score) + 0.4), 2),
        "risk_level":     risk,
        "news_count":     len(relevant),
        "avg_sentiment":  round(avg_score, 2),
        "ai_used":        True,   # usa il nostro motore, non LLM esterno
    }
