"""
screener.py — Screening dinamico multi-mercato

Ogni ora scansiona:
  - Tutte le coppie EUR disponibili su Kraken (~530+ coppie)
  - Top 100 ticker S&P500 su Alpaca

Seleziona i migliori candidati per score e aggiorna la watchlist.
Salva lo stato su disco (screener_state.json) per l'API e la dashboard.
"""

import asyncio
import time
import json
import os
import pandas as pd

from settings import (
    CRYPTO_CONFIG, STOCKS_CONFIG,
    MARKET_CRYPTO_ENABLED, MARKET_STOCKS_ENABLED,
)

# ─── CONFIG ───────────────────────────────────────────────────
MAX_ACTIVE_CRYPTO  = 30
MAX_ACTIVE_STOCKS  = 20
SCAN_INTERVAL      = 3600
SCREENER_FILE      = "screener_state.json"

DEFAULT_CRYPTO_CFG = {"bb_std":1.8,"sl_pct":0.06,"tp_pct":0.10,"budget_eur":600,"market":"crypto"}
DEFAULT_STOCKS_CFG = {"bb_std":2.0,"sl_pct":0.03,"tp_pct":0.06,"budget_eur":600,"market":"stocks"}

ALWAYS_INCLUDE_CRYPTO = set(CRYPTO_CONFIG.keys())  # i 14 default
ALWAYS_INCLUDE_STOCKS = {
    "AAPL","MSFT","GOOGL","AMZN","NVDA","TSLA","META","SPY",
}

SP500_TOP = [
    # Mega cap tech
    "AAPL","MSFT","GOOGL","GOOG","AMZN","NVDA","META","TSLA","AVGO","ORCL",
    "AMD","INTC","QCOM","TXN","MU","AMAT","LRCX","KLAC","MRVL","NXPI",
    # Finanza
    "BRK.B","JPM","V","MA","BAC","WFC","GS","MS","C","AXP",
    "BLK","SCHW","CB","PGR","AON","MMC","TRV","ALL","MET","PRU",
    "ICE","CME","SPGI","MCO","FIS","FISV","PYPL","SQ","COIN","HOOD",
    # Healthcare
    "UNH","JNJ","LLY","ABBV","MRK","PFE","TMO","ABT","DHR","BMY",
    "AMGN","GILD","ISRG","SYK","BSX","MDT","ZTS","REGN","VRTX","BIIB",
    "MRNA","BNTX","ILMN","IQV","CRL","WAT","A","DXCM","HOLX","IDXX",
    # Consumer
    "WMT","PG","KO","PEP","COST","MCD","SBUX","NKE","TGT","HD",
    "LOW","TJX","AMZN","BKNG","MAR","HLT","YUM","CMG","DPZ","DKNG",
    "NFLX","DIS","CMCSA","WBD","PARA","FOX","NYT","SNAP","PINS","RBLX",
    # Energia
    "XOM","CVX","COP","EOG","SLB","PSX","VLO","MPC","PXD","DVN",
    "HAL","BKR","OXY","HES","FANG","APA","MRO","RRC","EQT","CNX",
    # Industriali
    "GE","BA","CAT","HON","RTX","LMT","NOC","GD","HII","L",
    "UPS","FDX","CSX","UNP","NSC","CP","CNI","WM","RSG","CTAS",
    "MMM","EMR","ETN","PH","ROK","DOV","ITW","SWK","XYL","GNRC",
    # Real Estate & Utilities
    "PLD","AMT","EQIX","CCI","DLR","PSA","EXR","AVB","EQR","MAA",
    "NEE","DUK","SO","AEP","EXC","SRE","D","PCG","ED","FE",
    # Materials
    "LIN","APD","ECL","SHW","PPG","NEM","FCX","NUE","STLD","RS",
    "CF","MOS","FMC","ALB","EMN","CE","OLN","HUN","CC","KRO",
    # ETF principali
    "SPY","QQQ","IWM","DIA","VTI","VOO","GLD","SLV","USO","TLT",
    "HYG","LQD","EEM","EFA","VNQ","XLF","XLK","XLV","XLE","XLI",
    # Growth / Tech puri
    "CRM","NOW","SNOW","PLTR","UBER","LYFT","ABNB","DASH","SOFI","RIVN",
    "LCID","NIO","XPEV","LI","BABA","JD","PDD","MELI","SE","GRAB",
    "ZM","DOCU","OKTA","CRWD","ZS","DDOG","NET","GTLB","MDB","ESTC",
    "U","RBLX","HOOD","COIN","MSTR","ARKK","ARKQ","ARKG","ARKF","ARKX",
]

# ─── STATO IN MEMORIA ─────────────────────────────────────────
WATCHLIST: dict[str, dict] = {}
_STATE = {
    "last_scan":          0,
    "scan_in_progress":   False,
    "total_crypto_scanned": 0,
    "total_stocks_scanned": 0,
    "crypto_candidates":  [],  # tutti i candidati ordinati per score
    "stocks_candidates":  [],
    "watchlist_symbols":  [],
    "always_crypto":      list(ALWAYS_INCLUDE_CRYPTO),
    "always_stocks":      list(ALWAYS_INCLUDE_STOCKS),
}


def get_watchlist() -> dict:
    return WATCHLIST


def auto_blacklist_add(symbol: str, days: int = 7):
    """
    Aggiunge un asset alla blacklist automatica per N giorni.
    Chiamato dal bot quando un asset prende 3+ SL consecutivi.
    """
    expiry = time.time() + (days * 86400)
    if "auto_blacklist" not in _STATE:
        _STATE["auto_blacklist"] = {}
    _STATE["auto_blacklist"][symbol] = expiry
    # Rimuovi dalla watchlist
    global WATCHLIST
    if symbol in WATCHLIST:
        del WATCHLIST[symbol]
    _save()
    print(f"[SCREENER] Auto-blacklist: {symbol} per {days} giorni")


def load_state() -> dict:
    """Legge lo stato da disco. Usato dall'API (processo separato)."""
    if os.path.exists(SCREENER_FILE):
        try:
            with open(SCREENER_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return _STATE


def _save():
    """Salva lo stato su disco."""
    try:
        _STATE["watchlist_symbols"] = list(WATCHLIST.keys())
        with open(SCREENER_FILE, "w") as f:
            json.dump(_STATE, f, indent=2)
    except Exception as e:
        print(f"[SCREENER] Errore salvataggio: {e}")


# ─── QUICK SCORE ──────────────────────────────────────────────
def quick_score(df: pd.DataFrame) -> float:
    """Score rapido 0-10 basato su RSI, Volume, BB, MACD, Momentum."""
    if len(df) < 30:
        return 0.0
    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)
    score  = 0.0

    # RSI
    d = close.diff()
    gain = d.clip(lower=0).rolling(14).mean()
    loss = (-d.clip(upper=0)).rolling(14).mean()
    rsi = 100 - (100 / (1 + gain / (loss + 1e-10)))
    r = float(rsi.iloc[-1])
    if r < 25:   score += 3.0
    elif r < 35: score += 2.0
    elif r < 42: score += 1.5
    elif r < 50: score += 1.0
    elif r > 75: score -= 1.0

    # Volume spike
    vma = volume.rolling(20).mean().iloc[-1]
    vr  = float(volume.iloc[-1]) / (vma + 1e-10)
    if vr >= 2.5: score += 2.5
    elif vr >= 1.8: score += 1.8
    elif vr >= 1.3: score += 1.0

    # BB position
    sma = close.rolling(20).mean()
    std = close.rolling(20).std()
    rng = float((sma + 2*std - (sma - 2*std)).iloc[-1])
    if rng > 0:
        bp = float((close.iloc[-1] - (sma - 2*std).iloc[-1]) / rng)
        if bp <= 0.05:   score += 2.5
        elif bp <= 0.15: score += 2.0
        elif bp <= 0.25: score += 1.5
        elif bp <= 0.35: score += 1.0

    # MACD
    m = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    s = m.ewm(span=9).mean()
    h = m - s
    if float(h.iloc[-1]) > 0 and float(h.iloc[-1]) > float(h.iloc[-2]):
        score += 1.5
    elif float(h.iloc[-1]) > 0:
        score += 0.8

    # Momentum
    mom = (float(close.iloc[-1]) - float(close.iloc[-6])) / (float(close.iloc[-6]) + 1e-10)
    if mom > 0.02:    score += 1.0
    elif mom > 0:     score += 0.5
    elif mom < -0.05: score -= 1.0

    return round(min(10.0, score), 2)


# ─── SCAN CRYPTO ──────────────────────────────────────────────
async def scan_crypto(exchange) -> list[dict]:
    candidates = []
    try:
        markets = await exchange.load_markets(True)  # True = forza reload, ignora cache
        pairs = sorted([
            s for s, m in markets.items()
            if s.endswith("/EUR") and m.get("active") and m.get("spot")
            and not s.startswith("EUR/")
        ])
        print(f"[SCREENER] Crypto: scansiono {len(pairs)} coppie EUR...")
        _STATE["total_crypto_scanned"] = len(pairs)

        # Stablecoin da escludere
        STABLECOIN = {"USDC/EUR","USDT/EUR","EURC/EUR","DAI/EUR","BUSD/EUR",
                      "PAXG/EUR","XAUT/EUR","EURT/EUR","EURS/EUR"}

        # ── BLACKLIST DEFINITIVA ──────────────────────────────────
        # Criteri di esclusione:
        # 1. Token meme senza utilità (PEPE, DOGE-like, SHIB, ecc.)
        # 2. Token già causa di stop loss nei test (VVV, NIL, HIPPO, ecc.)
        # 3. Token micro-cap con spread elevati e liquidità insufficiente
        # 4. Token politici/celebrity senza fondamentali (TRUMP, MELANIA, ecc.)
        # 5. Token con nome esplicitamente speculativo
        # 6. Wrapped token e derivati (WBTC, MSOL, LSETH, ecc.)
        # 7. Token con storico di exploit o crolli strutturali
        BLACKLIST = {
            # ── Già causa di stop loss nei test ──
            "VVV/EUR","NIL/EUR","HIPPO/EUR","ANON/EUR","SOON/EUR",
            "EAT/EUR","VINE/EUR","MSOL/EUR","ASRR/EUR","UP/EUR",
            "KIN/EUR","HYPE/EUR","WLD/EUR","GHIBLI/EUR","COOKIE/EUR",
            "BLUAI/EUR","ZBCN/EUR","RUJI/EUR","B2/EUR","BANANAS31/EUR",

            # ── Meme token ──
            "DOGE/EUR","SHIB/EUR","PEPE/EUR","BONK/EUR","WIF/EUR",
            "FLOKI/EUR","MEME/EUR","TURBO/EUR","GIGA/EUR","MEW/EUR",
            "DOGS/EUR","POPCAT/EUR","PONKE/EUR","SNEK/EUR","LOFI/EUR",
            "MOODENG/EUR","FARTCOIN/EUR","RETARDIO/EUR","TITCOIN/EUR",
            "USELESS/EUR","CHEEMS/EUR","BILL/EUR","TOSHI/EUR","COQ/EUR",
            "FWOG/EUR","CHILLHOUSE/EUR","JAILSTOOL/EUR","PUPS/EUR",
            "BILLY/EUR","APU/EUR","GRIFFAIN/EUR","BERT/EUR","BOB/EUR",
            "BASED/EUR","LOFI/EUR","ROLL/EUR","TOKEN/EUR","TREE/EUR",

            # ── Token politici/celebrity ──
            "TRUMP/EUR","MELANIA/EUR","MAGA/EUR","BODEN/EUR",

            # ── Wrapped/derivati (già coperti dalla versione originale) ──
            "WBTC/EUR","MSOL/EUR","LSETH/EUR","LSSOL/EUR","LUNC/EUR",
            "WAXL/EUR","PYUSD/EUR","USDD/EUR","USDQ/EUR","USDS/EUR",
            "STBL/EUR","USDE/EUR","PYUSD/EUR","RLUSD/EUR",

            # ── Micro-cap con storico problematico ──
            "TITCOIN/EUR","CHILLHOUSE/EUR","FLOCK/EUR","GENIUS/EUR",
            "SWARMS/EUR","AI3/EUR","DAG/EUR","JST/EUR","REN/EUR",
            "ST/EUR","SUKU/EUR","XMN/EUR","US/EUR","ROLL/EUR",
            "CLANKER/EUR","CLOUD/EUR","DOLO/EUR","EDGE/EUR",
            "GOMINING/EUR","KEEP/EUR","KGEN/EUR","LCAP/EUR",
            "LOCKIN/EUR","OMNI/EUR","POLS/EUR","POND/EUR",
            "SCA/EUR","SCOR/EUR","TANSSI/EUR","ZEX/EUR","OKB/EUR",
            "XION/EUR","SIGMA/EUR","ZORA/EUR","MULTI/EUR",
            "HPOS10I/EUR","AUDIO/EUR","WBTC/EUR",

            # ── Token senza liquidità sufficiente (storicamente) ──
            "REPPO/EUR","SIDEKICK/EUR","TREMP/EUR","WMTX/EUR",
            "UNITE/EUR","UXLINK/EUR","VELODROME/EUR","WEN/EUR",
            "EPT/EUR","BDXN/EUR","BKS/EUR","BNC/EUR","KEY/EUR",
            "METIS/EUR","ORDER/EUR","PIPE/EUR","QI/EUR","TBTC/EUR",
            "SUNDOG/EUR","SUP/EUR","SUNDOG/EUR","SOMI/EUR","SOSO/EUR",
            "XPL/EUR","WARD/EUR","SENT/EUR","OPEN/EUR","ADI/EUR",
            "MXC/EUR","ARC/EUR","PEP/EUR","ORCA/EUR","NIL/EUR",
            "NOS/EUR","PARTI/EUR","PROMPT/EUR","REKT/EUR","SAHARA/EUR",
            "SCRT/EUR","SGB/EUR","SPACE/EUR","SPELL/EUR","ZEUS/EUR",
            "ZBT/EUR","XTER/EUR","WAL/EUR","PUMP/EUR","PTB/EUR",
            "RAVE/EUR","SKR/EUR","NIGHT/EUR","MIRA/EUR","LION/EUR",
            "KNTQ/EUR","IN/EUR","HFT/EUR","HDX/EUR","GFI/EUR",
            "ELIZAOS/EUR","DYM/EUR","CELR/EUR","CARV/EUR","BTT/EUR",
            "BLUAI/EUR","AZTEC/EUR","AVNT/EUR","AIXBT/EUR",
            "ACU/EUR","A/EUR","0G/EUR","2Z/EUR","AIOZ/EUR",
            "COOKIE/EUR","DUCK/EUR","NOBODY/EUR","PORTAL/EUR",
            "SBR/EUR","SPK/EUR","TX/EUR","ZAMA/EUR","ZRO/EUR",
            "ALMANAK/EUR","ALTHEA/EUR","ASTER/EUR","AVA/EUR",
            "B3/EUR","BADGER/EUR","BIGTIME/EUR","BIO/EUR","CAT/EUR",
            "EV/EUR","FXS/EUR","KOBAN/EUR","L3/EUR","MF/EUR",
            "OBOL/EUR","PLAY/EUR","QUAI/EUR","SAMO/EUR","SYND/EUR",
            "TOKE/EUR","UFD/EUR","VANRY/EUR","XDC/EUR","XNY/EUR",
            "XRT/EUR","ZKP/EUR","ZBCN/EUR","RSC/EUR","SOGNI/EUR",
            "RUJI/EUR","B2/EUR","BANANAS31/EUR","GAIA/EUR","LAVA/EUR",
            "MANTRA/EUR","MIM/EUR","MLN/EUR","WAXL/EUR","ZK/EUR",
            "CYBER/EUR","ANIME/EUR","BABY/EUR","CAMP/EUR","MERL/EUR",
            "MYX/EUR","SBR/EUR","STX/EUR","ZAMA/EUR",
        }

        for sym in pairs:
            if sym in STABLECOIN or sym in BLACKLIST:
                continue
            # Punto 5: salta asset in auto-blacklist temporanea
            auto_bl = _STATE.get("auto_blacklist", {})
            if sym in auto_bl:
                continue
            try:
                ohlcv = await exchange.fetch_ohlcv(sym, "1h", limit=50)
                if len(ohlcv) < 30:
                    continue
                df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
                df = df.astype({"close": float, "volume": float})

                avg_vol_eur = float(df["volume"].mean()) * float(df["close"].iloc[-1])
                sc = quick_score(df)
                candidates.append({
                    "symbol": sym,
                    "score":  sc,
                    "price":  round(float(df["close"].iloc[-1]), 6),
                    "in_watchlist": sym in WATCHLIST,
                    "vol_ok": avg_vol_eur >= 50000,
                })
                await asyncio.sleep(0.3)
            except Exception:
                continue

        candidates.sort(key=lambda x: x["score"], reverse=True)
        print(f"[SCREENER] Crypto: {len(candidates)} valide su {len(pairs)} totali. Top: {candidates[0]['symbol']} {candidates[0]['score']}/10" if candidates else "[SCREENER] Crypto: nessun candidato")
    except Exception as e:
        print(f"[SCREENER] Errore scan crypto: {e}")
    return candidates


# ─── SCAN STOCKS ──────────────────────────────────────────────
async def scan_stocks(connector) -> list[dict]:
    candidates = []
    print(f"[SCREENER] Stocks: scansiono {len(SP500_TOP)} ticker...")
    _STATE["total_stocks_scanned"] = len(SP500_TOP)

    for ticker in SP500_TOP:
        try:
            df = await connector.fetch_ohlcv(ticker, "1h", 50)
            if df is None or len(df) < 30:
                continue
            sc = quick_score(df)
            candidates.append({
                "symbol": ticker,
                "score":  sc,
                "price":  round(float(df["close"].iloc[-1]), 2),
                "in_watchlist": ticker in WATCHLIST,
            })
            await asyncio.sleep(0.15)
        except Exception:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    print(f"[SCREENER] Stocks: {len(candidates)} scansionate. Top: {candidates[0]['symbol']} {candidates[0]['score']}/10" if candidates else "[SCREENER] Stocks: nessun candidato")
    return candidates


# ─── AGGIORNA WATCHLIST ───────────────────────────────────────
def update_watchlist(crypto_cands: list, stocks_cands: list, positions: dict):
    global WATCHLIST

    new_wl = {}

    # ── CRYPTO ────────────────────────────────────────────────
    if MARKET_CRYPTO_ENABLED:
        # 1. Sempre inclusi
        for sym in ALWAYS_INCLUDE_CRYPTO:
            cfg = dict(CRYPTO_CONFIG.get(sym, DEFAULT_CRYPTO_CFG))
            cfg["market"] = "crypto"
            cfg["_always"] = True
            new_wl[sym] = cfg

        # 2. In posizione (non rimuovere mai)
        for sym, pos in positions.items():
            if isinstance(pos, dict) and pos.get("status") == "IN_POSITION":
                if sym.endswith("/EUR") and sym not in new_wl:
                    cfg = dict(CRYPTO_CONFIG.get(sym, DEFAULT_CRYPTO_CFG))
                    cfg["market"] = "crypto"
                    cfg["_position"] = True
                    new_wl[sym] = cfg

        # 3. Migliori candidati dallo screening (solo quelli con volume ok)
        crypto_in_wl = len([k for k in new_wl if new_wl[k].get("market") == "crypto"])
        for c in crypto_cands:
            if crypto_in_wl >= MAX_ACTIVE_CRYPTO:
                break
            sym = c["symbol"]
            if sym in new_wl:
                continue
            # Punto 3: soglia alzata a 4.0
            if c["score"] >= 4.0 and c.get("vol_ok", True):
                cfg = dict(DEFAULT_CRYPTO_CFG)
                cfg["_discovered"] = True
                cfg["_score"] = c["score"]
                new_wl[sym] = cfg
                crypto_in_wl += 1

    # ── STOCKS ────────────────────────────────────────────────
    if MARKET_STOCKS_ENABLED:
        for sym in ALWAYS_INCLUDE_STOCKS:
            cfg = dict(STOCKS_CONFIG.get(sym, DEFAULT_STOCKS_CFG))
            cfg["market"] = "stocks"
            cfg["_always"] = True
            new_wl[sym] = cfg

        for sym, pos in positions.items():
            if isinstance(pos, dict) and pos.get("status") == "IN_POSITION":
                if not sym.endswith("/EUR") and sym not in new_wl:
                    cfg = dict(STOCKS_CONFIG.get(sym, DEFAULT_STOCKS_CFG))
                    cfg["market"] = "stocks"
                    cfg["_position"] = True
                    new_wl[sym] = cfg

        stocks_in_wl = len([k for k in new_wl if new_wl[k].get("market") == "stocks"])
        for c in stocks_cands:
            if stocks_in_wl >= MAX_ACTIVE_STOCKS:
                break
            sym = c["symbol"]
            if sym in new_wl:
                continue
            if c["score"] >= 3.5:
                cfg = dict(DEFAULT_STOCKS_CFG)
                cfg["_discovered"] = True
                cfg["_score"] = c["score"]
                new_wl[sym] = cfg
                stocks_in_wl += 1

    WATCHLIST = new_wl

    # Aggiorna stato con candidati completi (per la dashboard)
    # NON sovrascrivere se la lista è vuota (avvio con default)
    if crypto_cands:
        _STATE["crypto_candidates"] = crypto_cands
        _STATE["total_crypto_scanned"] = len(crypto_cands)
    if stocks_cands:
        _STATE["stocks_candidates"] = stocks_cands
        _STATE["total_stocks_scanned"] = len(stocks_cands)

    n_crypto = len([k for k in WATCHLIST if WATCHLIST[k].get("market") == "crypto"])
    n_stocks = len([k for k in WATCHLIST if WATCHLIST[k].get("market") == "stocks"])
    _STATE["active_crypto"] = n_crypto
    _STATE["active_stocks"] = n_stocks
    _STATE["last_scan"]     = int(time.time())
    _STATE["scan_in_progress"] = False

    print(f"[SCREENER] Watchlist: {n_crypto} crypto + {n_stocks} stocks = {len(WATCHLIST)} totale")
    _save()
    return WATCHLIST


# ─── LOOP PRINCIPALE ──────────────────────────────────────────
async def screener_loop(exchange, shared_state: dict):
    from market_connector import get_connector

    # Inizializzazione immediata con default
    # Carica stato precedente dal disco per preservare i candidati dello scan precedente
    prev_state = load_state()
    if prev_state.get("crypto_candidates"):
        _STATE["crypto_candidates"] = prev_state["crypto_candidates"]
        _STATE["total_crypto_scanned"] = prev_state.get("total_crypto_scanned", 0)
    if prev_state.get("stocks_candidates"):
        _STATE["stocks_candidates"] = prev_state["stocks_candidates"]
        _STATE["total_stocks_scanned"] = prev_state.get("total_stocks_scanned", 0)

    # Punto 5: carica blacklist automatica da disco
    auto_bl = prev_state.get("auto_blacklist", {})
    now_ts   = time.time()
    # Rimuovi quelli scaduti (dopo 7 giorni)
    auto_bl  = {k: v for k, v in auto_bl.items() if v > now_ts}
    _STATE["auto_blacklist"] = auto_bl
    if auto_bl:
        print(f"[SCREENER] Auto-blacklist attiva: {list(auto_bl.keys())}")

    positions = shared_state.get("positions", {})
    update_watchlist(
        _STATE.get("crypto_candidates", []),
        _STATE.get("stocks_candidates", []),
        positions
    )
    print(f"[SCREENER] Watchlist iniziale: {len(WATCHLIST)} asset")

    await asyncio.sleep(30)  # breve attesa poi parte il primo scan

    while True:
        try:
            _STATE["scan_in_progress"] = True
            _save()
            print(f"[SCREENER] === SCAN COMPLETO ===")
            positions = shared_state.get("positions", {})

            crypto_cands = []
            stocks_cands = []

            if MARKET_CRYPTO_ENABLED:
                crypto_cands = await scan_crypto(exchange)

            if MARKET_STOCKS_ENABLED:
                connector = get_connector("AAPL", exchange)
                if connector and connector.is_market_open("AAPL"):
                    stocks_cands = await scan_stocks(connector)
                else:
                    print("[SCREENER] NYSE chiuso — skip scan stocks")

            update_watchlist(crypto_cands, stocks_cands, positions)

            # Notifica Telegram top candidati
            top = sorted(
                (crypto_cands[:5] if crypto_cands else []) +
                (stocks_cands[:5] if stocks_cands else []),
                key=lambda x: x["score"], reverse=True
            )[:6]
            if top:
                lines = "\n".join(f"  • {c['symbol']} — {c['score']}/10" for c in top)
                msg = (f"🔍 <b>Screener completato</b>\n\n"
                       f"Scansionati: {_STATE['total_crypto_scanned']} crypto + {_STATE['total_stocks_scanned']} stocks\n"
                       f"In watchlist: {len(WATCHLIST)} asset\n\n"
                       f"<b>Top candidati:</b>\n{lines}")
                try:
                    from notifier import _send
                    await _send(msg)
                except Exception:
                    pass

        except Exception as e:
            print(f"[SCREENER] Errore loop: {e}")
            _STATE["scan_in_progress"] = False
            _save()

        await asyncio.sleep(SCAN_INTERVAL)
