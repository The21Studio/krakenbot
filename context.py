"""
context.py — Contesto di mercato globale (fonti 100% gratuite)

Fonti:
  1. Fear & Greed Index (Alternative.me) — gratis, no key
  2. News RSS (CoinDesk, CoinTelegraph, Decrypt) — gratis, no key
  3. CoinGecko trending + market — gratis, no key
  4. Analisi AI (Claude API) — interpreta news e contesto

Aggiornamento ogni 15 minuti.
"""

import asyncio
import time
import json
import os
import re
import urllib.request
import urllib.parse
from settings import DRY_RUN
from sentiment import analyze_asset_news

# ─── CONFIGURAZIONE ───────────────────────────────────────────

CONTEXT_UPDATE_MIN = 15
CONTEXT_FILE       = "market_context.json"

# Feed RSS gratuiti (nessuna API key)
RSS_FEEDS = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

ASSET_KEYWORDS = {
    "BTC/EUR": ["bitcoin", "btc"],
    "ETH/EUR": ["ethereum", "eth", "ether"],
    "SOL/EUR": ["solana", "sol"],
    "XRP/EUR": ["xrp", "ripple"],
    "ADA/EUR": ["cardano", "ada"],
    "DOT/EUR": ["polkadot", "dot"],
    "LTC/EUR": ["litecoin", "ltc"],
    "TRX/EUR": ["tron", "trx"],
    "XLM/EUR": ["stellar", "xlm"],
    "NEAR/EUR":["near protocol", "near"],
    "ICP/EUR": ["internet computer", "icp"],
    "ONDO/EUR":["ondo"],
    "TAO/EUR": ["bittensor", "tao"],
    "ZEC/EUR": ["zcash", "zec"],
}


# ══════════════════════════════════════════════════════════════
# FEAR & GREED
# ══════════════════════════════════════════════════════════════

async def fetch_fear_greed() -> dict:
    try:
        url = "https://api.alternative.me/fng/?limit=2&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "KrakenBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        current    = data["data"][0]
        prev       = data["data"][1] if len(data["data"]) > 1 else current
        value      = int(current["value"])
        prev_value = int(prev["value"])
        label      = current["value_classification"]

        if value <= 24:
            context, signal, modifier = "EXTREME_FEAR", "BUY_STRONG", +0.5
        elif value <= 44:
            context, signal, modifier = "FEAR", "BUY_CAUTIOUS", +0.2
        elif value <= 55:
            context, signal, modifier = "NEUTRAL", "NEUTRAL", 0.0
        elif value <= 74:
            context, signal, modifier = "GREED", "SELL_CAUTIOUS", -0.3
        else:
            context, signal, modifier = "EXTREME_GREED", "SELL_STRONG", -0.8

        trend = "in salita" if value > prev_value else "in calo" if value < prev_value else "stabile"
        return {"value": value, "label": label, "context": context, "signal": signal,
                "modifier": modifier, "trend": trend, "prev": prev_value, "ts": int(time.time())}
    except Exception as e:
        print(f"[CONTEXT] Errore Fear&Greed: {e}")
        return {"value": 50, "label": "Neutral", "context": "NEUTRAL",
                "signal": "NEUTRAL", "modifier": 0.0, "trend": "—", "ts": 0}


# ══════════════════════════════════════════════════════════════
# NEWS DA RSS (gratis)
# ══════════════════════════════════════════════════════════════

def _parse_rss(xml: str, source: str, limit: int = 10) -> list[dict]:
    """Parser RSS minimale senza librerie esterne."""
    items = []
    blocks = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)[:limit]
    for m in blocks:
        title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", m, re.DOTALL)
        date_m  = re.search(r"<pubDate>(.*?)</pubDate>", m, re.DOTALL)
        link_m  = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", m, re.DOTALL)
        if title_m:
            title = re.sub(r"<.*?>", "", title_m.group(1)).strip()
            items.append({
                "title":     title,
                "source":    source,
                "published": date_m.group(1).strip() if date_m else "",
                "link":      link_m.group(1).strip() if link_m else "",
            })
    return items


async def fetch_news() -> list[dict]:
    """Scarica news da tutti i feed RSS gratuiti."""
    all_news = []
    for source, url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 KrakenBot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                xml = r.read().decode("utf-8", errors="ignore")
            news = _parse_rss(xml, source, limit=8)
            all_news.extend(news)
        except Exception as e:
            print(f"[CONTEXT] Errore RSS {source}: {e}")

    # Assegna sentiment semplice basato su parole chiave
    positive_words = ["surge", "rally", "soar", "gain", "bull", "record", "high",
                      "adopt", "approve", "breakthrough", "rise", "jump", "boom"]
    negative_words = ["crash", "plunge", "drop", "fall", "bear", "hack", "ban",
                      "lawsuit", "decline", "dump", "sink", "fear", "warning", "risk"]

    for n in all_news:
        title_low = n["title"].lower()
        pos = sum(1 for w in positive_words if w in title_low)
        neg = sum(1 for w in negative_words if w in title_low)
        if pos > neg:
            n["sentiment"] = "POSITIVE"
        elif neg > pos:
            n["sentiment"] = "NEGATIVE"
        else:
            n["sentiment"] = "NEUTRAL"
        # Tag asset rilevanti
        n["assets"] = [sym for sym, kws in ASSET_KEYWORDS.items()
                       if any(kw in title_low for kw in kws)]

    return all_news[:20]


# ══════════════════════════════════════════════════════════════
# ANALISI SENTIMENT (gratuita, motore offline)
# ══════════════════════════════════════════════════════════════

async def analyze_asset(symbol: str, news: list[dict], fg: dict, snap: dict) -> dict:
    """
    Analizza il contesto di un asset usando il motore sentiment gratuito.
    Combina sentiment news + Fear&Greed, nessuna API esterna.
    """
    # Sentiment news (motore offline gratuito)
    sentiment = analyze_asset_news(symbol, news)

    # Combina con segnale Fear&Greed per decisione finale
    fg_signal = fg.get("signal", "NEUTRAL")
    base_action = sentiment["action"]

    # Se F&G e news concordano, aumenta la confidenza
    if fg_signal in ("BUY_STRONG", "BUY_CAUTIOUS") and base_action == "BUY":
        sentiment["confidence"] = min(1.0, sentiment["confidence"] + 0.2)
        sentiment["reason"] = "F&G + news concordi BUY · " + sentiment["reason"]
    elif fg_signal in ("SELL_STRONG", "SELL_CAUTIOUS") and base_action == "SELL":
        sentiment["confidence"] = min(1.0, sentiment["confidence"] + 0.2)

    sentiment["ts"] = int(time.time())
    return sentiment


# ══════════════════════════════════════════════════════════════
# CONTESTO COMPLETO
# ══════════════════════════════════════════════════════════════

async def get_full_context(symbols: list[str], snaps: dict) -> dict:
    print("[CONTEXT] Aggiornamento contesto...")
    fg = await fetch_fear_greed()
    print(f"[CONTEXT] Fear&Greed: {fg['value']}/100 ({fg['label']})")
    news = await fetch_news()
    print(f"[CONTEXT] News scaricate: {len(news)}")

    context = {"fear_greed": fg, "news": news, "assets": {}, "ts": int(time.time())}

    # Analisi sentiment gratuita per ogni asset
    for symbol in symbols:
        snap = snaps.get(symbol, {})
        if not snap:
            continue
        analysis = await analyze_asset(symbol, news, fg, snap)
        context["assets"][symbol] = {
            "fear_greed_modifier": fg["modifier"],
            "ai": analysis,
            "total_modifier": round(fg["modifier"] + analysis["score_modifier"], 2),
        }

    try:
        with open(CONTEXT_FILE, "w") as f:
            json.dump(context, f, indent=2)
    except Exception as e:
        print(f"[CONTEXT] Errore salvataggio: {e}")
    return context


def load_context() -> dict:
    if os.path.exists(CONTEXT_FILE):
        try:
            with open(CONTEXT_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"fear_greed": {"value": 50, "label": "Neutral", "modifier": 0}, "news": [], "assets": {}}


async def context_loop(shared_state: dict):
    print(f"[CONTEXT] Loop avviato — ogni {CONTEXT_UPDATE_MIN} min")
    await asyncio.sleep(15)
    while True:
        try:
            snaps   = shared_state.get("snapshots", {})
            symbols = list(snaps.keys())
            if symbols:
                ctx = await get_full_context(symbols, snaps)
                shared_state["context"] = ctx
                print(f"[CONTEXT] ✅ F&G {ctx['fear_greed']['value']}/100, {len(ctx['news'])} news")
        except Exception as e:
            print(f"[CONTEXT] Errore loop: {e}")
        await asyncio.sleep(CONTEXT_UPDATE_MIN * 60)
