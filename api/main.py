"""
api/main.py — API REST del bot (FastAPI)

Espone i dati del bot via HTTP con autenticazione JWT.
La dashboard web e l'app mobile si connettono qui.
Gira sullo stesso server del bot, porta 8000.
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json, os, time, hashlib, hmac, base64
from datetime import datetime, timedelta

# ─── IMPORTA MODULI BOT ───────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from persistence import load_positions, load_trades, get_daily_stats
from dca import load_dca_portfolio, load_dca_state, get_portfolio_stats
from settings import DRY_RUN, CRYPTO_CONFIG, ALL_ASSETS

# ─── CONFIGURAZIONE ───────────────────────────────────────────
API_USERNAME = os.getenv("API_USERNAME", "admin")
API_PASSWORD = os.getenv("API_PASSWORD", "cambia_questa_password")
JWT_SECRET   = os.getenv("JWT_SECRET",   "chiave_segreta_molto_lunga_cambiarla")
JWT_EXPIRE_HOURS = 24

app = FastAPI(
    title="Kraken Bot API",
    docs_url=None,    # disabilita swagger pubblico
    redoc_url=None,
)

# CORS — permette solo il tuo dominio
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # in produzione metti il tuo dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# ─── AUTH ─────────────────────────────────────────────────────

def _make_token(username: str) -> str:
    expire = int(time.time()) + JWT_EXPIRE_HOURS * 3600
    payload = f"{username}:{expire}"
    sig = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}:{sig}"
    return base64.b64encode(raw.encode()).decode()


def _verify_token(token: str) -> str:
    try:
        raw     = base64.b64decode(token.encode()).decode()
        parts   = raw.rsplit(":", 2)
        username, expire, sig = parts[0], parts[1], parts[2]
        payload  = f"{username}:{expire}"
        expected = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if sig != expected:
            raise ValueError("firma invalida")
        if int(expire) < int(time.time()):
            raise ValueError("token scaduto")
        return username
    except Exception:
        raise HTTPException(status_code=401, detail="Token non valido o scaduto")


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    return _verify_token(token)


# ─── SHARED STATE (viene aggiornato dal bot) ──────────────────
# Il bot scrive in questi file JSON, l'API li legge
STATE_FILE = "shared_state.json"

def _read_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"live_prices": {}, "snapshots": {}}


# ─── ENDPOINTS ────────────────────────────────────────────────

@app.post("/token")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    if form.username != API_USERNAME or form.password != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Credenziali errate")
    return {
        "access_token": _make_token(form.username),
        "token_type":   "bearer",
    }


@app.get("/status")
async def bot_status(user: str = Depends(get_current_user)):
    """Stato generale del bot."""
    state = _read_state()
    stats = get_daily_stats()
    return {
        "running":    True,
        "dry_run":    DRY_RUN,
        "mode":       "DRY RUN" if DRY_RUN else "LIVE",
        "assets":     list(ALL_ASSETS.keys()),
        "n_assets":   len(ALL_ASSETS),
        "daily":      stats,
        "timestamp":  int(time.time()),
    }


@app.get("/prices")
async def live_prices(user: str = Depends(get_current_user)):
    """Prezzi live di tutti gli asset monitorati."""
    state = _read_state()
    return {
        "prices":    state.get("live_prices", {}),
        "timestamp": int(time.time()),
    }


@app.get("/positions")
async def open_positions(user: str = Depends(get_current_user)):
    """Posizioni aperte del bot con regime e indicatori."""
    positions = load_positions()
    state     = _read_state()
    prices    = state.get("live_prices", {})
    snaps     = state.get("snapshots", {})

    # Merge con le posizioni da shared_state (più aggiornate)
    state_positions = state.get("positions", {})
    for sym, pos in state_positions.items():
        if isinstance(pos, dict) and pos.get("status") == "IN_POSITION":
            if sym not in positions or positions[sym].get("status") != "IN_POSITION":
                positions[sym] = pos

    result = {}
    for symbol, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        status = pos.get("status", "")
        # Accetta sia IN_POSITION che CACCIA_APERTA con entry_price
        if status != "IN_POSITION" and not (status == "CACCIA_APERTA" and pos.get("entry_price")):
            continue
        if status != "IN_POSITION":
            continue
        entry   = pos.get("entry_price", 0)
        if not entry:
            continue
        live    = prices.get(symbol, entry)
        pnl_pct = (live - entry) / entry * 100 if entry else 0
        cfg     = ALL_ASSETS.get(symbol, {})
        sl      = pos.get("sl_price")  or entry * (1 - cfg.get("sl_pct", 0.05))
        tp1     = pos.get("tp1_price") or entry * (1 + cfg.get("tp_pct", 0.07))
        tp2     = pos.get("tp2_price") or entry * (1 + cfg.get("tp_pct", 0.07) * 2.5)
        snap    = snaps.get(symbol, {})
        volume  = pos.get("volume", 0)
        pnl_eur = (live - entry) * volume if volume else pnl_pct / 100 * cfg.get("budget_eur", 20)

        result[symbol] = {
            "status":      "IN_POSITION",
            "entry_price": round(entry, 6),
            "live_price":  round(live, 6),
            "pnl_pct":     round(pnl_pct, 2),
            "pnl_eur":     round(pnl_eur, 2),
            "sl_price":    round(sl, 6),
            "tp1_price":   round(tp1, 6),
            "tp2_price":   round(tp2, 6),
            "stop_loss":   round(sl, 6),
            "take_profit": round(tp1, 6),
            "take_profit2":round(tp2, 6),
            "tp1_hit":     pos.get("tp1_hit", False),
            "direction":   pos.get("direction", "long"),
            "market":      pos.get("market", cfg.get("market", "crypto")),
            "volume":      round(volume, 6),
            "since":       pos.get("timestamp", 0),
            "regime":      pos.get("regime_at_entry", "—"),
            "rsi":         round(snap.get("rsi") or 0, 1),
            "macd_hist":   round(snap.get("macd_hist") or 0, 6),
            "vol_ratio":   round(snap.get("vol_ratio") or 0, 2),
            "bb_pct":      round(snap.get("bb_pct") or 0, 3),
        }
    return {"positions": result, "count": len(result)}


@app.get("/portfolio")
async def core_portfolio(user: str = Depends(get_current_user)):
    """Portfolio CORE (DCA — BTC + ETH che macerano)."""
    state     = _read_state()
    prices    = state.get("live_prices", {})
    portfolio = load_dca_portfolio()
    dca_state = load_dca_state()
    stats     = get_portfolio_stats(portfolio, prices)

    return {
        "portfolio":    stats,
        "dca_state":    dca_state,
        "next_dca":     f"Giorno 1 del mese alle 10:00",
    }


@app.get("/trades")
async def trade_history(
    limit: int = 50,
    user:  str = Depends(get_current_user)
):
    """Storico trade (ultimi N)."""
    trades = load_trades()
    trades_sorted = sorted(trades, key=lambda x: x.get("timestamp", 0), reverse=True)
    return {
        "trades": trades_sorted[:limit],
        "total":  len(trades),
    }


@app.get("/indicators/{symbol}")
async def indicators(
    symbol: str,
    user:   str = Depends(get_current_user)
):
    """Indicatori tecnici per un asset specifico."""
    state = _read_state()
    snap  = state.get("snapshots", {}).get(symbol)
    if not snap:
        raise HTTPException(status_code=404, detail=f"Dati non disponibili per {symbol}")
    return {"symbol": symbol, "indicators": snap}


@app.get("/market")
async def market_data(user: str = Depends(get_current_user)):
    """Dati completi di mercato per tutti gli asset abilitati."""
    state   = _read_state()
    prices  = state.get("live_prices", {})
    snaps   = state.get("snapshots", {})
    snaps4h = state.get("snapshots4h", {})

    result = {}
    for symbol, cfg in ALL_ASSETS.items():
        price = prices.get(symbol)
        snap  = snaps.get(symbol, {})
        s4h   = snaps4h.get(symbol, {})
        if not price or not snap:
            continue

        market = cfg.get("market", "crypto")
        rsi4h  = s4h.get("rsi", snap.get("rsi", 50))
        macd4h = s4h.get("macd_hist", snap.get("macd_hist", 0))
        atr_pct= snap.get("atr_pct", 0.02)
        bb_w   = snap.get("bb_width", 0.03)

        regime = "LATERAL"
        if atr_pct > 0.06 or bb_w > 0.12:
            regime = "HIGH_VOL"
        elif rsi4h < 35 and macd4h < -0.001:
            regime = "TREND_BEAR"
        elif rsi4h > 55 and macd4h > 0:
            regime = "TREND_BULL"

        bb_lower = snap.get("bb_lower", price)
        dist_pct = (price - bb_lower) / bb_lower * 100 if bb_lower else 0

        # Determina quale strategia userebbe il bot per questo asset
        adx = snap.get("adx", 0)
        rsi = snap.get("rsi", 50)
        if market == "forex":
            strategy = "Forex Trend" if (adx > 20) else "Attesa trend"
        elif market == "stocks":
            strategy = "Stocks Breakout" if snap.get("squeeze_off") else "Stocks Trend"
        else:  # crypto
            if snap.get("squeeze_off"):
                strategy = "Breakout"
            elif adx < 18 or rsi < 32 or rsi > 72:
                strategy = "Mean Reversion"
            elif adx > 20:
                strategy = "Trend Following"
            else:
                strategy = "Mean Reversion"

        result[symbol] = {
            "price":     round(price, 6),
            "regime":    regime,
            "market":    market,
            "strategy":  strategy,
            "rsi":       round(snap.get("rsi", 0), 1),
            "macd_hist": round(snap.get("macd_hist", 0), 6),
            "vol_ratio": round(snap.get("vol_ratio", 1), 2),
            "bb_lower":  round(bb_lower, 6),
            "bb_upper":  round(snap.get("bb_upper", price), 6),
            "bb_pct":    round(snap.get("bb_pct", 0.5), 3),
            "dist_pct":  round(dist_pct, 2),
            "atr_pct":   round(atr_pct * 100, 2),
        }
    return {"market": result, "ts": int(time.time())}


@app.get("/context")
async def market_context(user: str = Depends(get_current_user)):
    """Contesto globale: Fear & Greed, news, analisi AI."""
    try:
        from context import load_context
        ctx = load_context()
        return ctx
    except Exception:
        return {"fear_greed": {"value": 50, "label": "Neutral"}, "assets": {}}


@app.get("/screener")
async def screener_status_endpoint(user: str = Depends(get_current_user)):
    """Stato screener: candidati completi, watchlist attiva, statistiche scan."""
    try:
        from screener import load_state, ALWAYS_INCLUDE_CRYPTO, ALWAYS_INCLUDE_STOCKS
        st = load_state()
        return {
            "last_scan":             st.get("last_scan", 0),
            "scan_in_progress":      st.get("scan_in_progress", False),
            "total_crypto_scanned":  st.get("total_crypto_scanned", 0),
            "total_stocks_scanned":  st.get("total_stocks_scanned", 0),
            "active_crypto":         st.get("active_crypto", 0),
            "active_stocks":         st.get("active_stocks", 0),
            "watchlist":             st.get("watchlist_symbols", []),
            "always_crypto":         list(ALWAYS_INCLUDE_CRYPTO),
            "always_stocks":         list(ALWAYS_INCLUDE_STOCKS),
            "crypto_candidates":     st.get("crypto_candidates", []),
            "stocks_candidates":     st.get("stocks_candidates", []),
        }
    except Exception as e:
        return {"error": str(e), "last_scan": 0, "watchlist": [],
                "crypto_candidates": [], "stocks_candidates": []}


@app.get("/virtual_portfolio")
async def virtual_portfolio_endpoint(user: str = Depends(get_current_user)):
    """Portafoglio virtuale unificato: saldo, P&L, split crypto/stocks, budget adattivo."""
    try:
        # Fix path — l'API gira in /root/bot/api/ ma i file sono in /root/bot/
        import sys, os
        bot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if bot_dir not in sys.path:
            sys.path.insert(0, bot_dir)
        os.chdir(bot_dir)  # assicura che virtual_portfolio.json sia trovato

        from virtual_portfolio import get_portfolio_state
        state     = _read_state()
        trades    = load_trades()
        positions = load_positions()
        prices    = state.get("live_prices", {})
        pos_dict  = {sym: pos for sym, pos in positions.items()
                    if isinstance(pos, dict) and pos.get("status") == "IN_POSITION"}
        result = get_portfolio_state(trades, pos_dict, prices)
        return result
    except Exception as e:
        import traceback
        print(f"[API] virtual_portfolio error: {e}\n{traceback.format_exc()}")
        return {"error": str(e), "saldo_totale": 8500.0, "pnl_totale": 0,
                "saldo_crypto": 5100.0, "saldo_stocks": 3400.0,
                "budget_per_trade": 600, "prossimo_deposito": 500,
                "prossimo_deposito_data": "1° Luglio", "rendimento_pct": 0,
                "total_deposited": 8500.0, "n_trades_crypto": 0, "n_trades_stocks": 0,
                "pnl_crypto": 0, "pnl_stocks": 0, "open_pnl_totale": 0,
                "saldo_libero": 8500.0, "saldo_investito": 0}


@app.get("/trades_full")
async def trades_full(user: str = Depends(get_current_user)):
    """Tutti i trade chiusi con P&L per grafico e performance per asset."""
    trades = load_trades()
    closed = [t for t in trades if t.get("type") not in ("BUY",) and t.get("pnl_eur") is not None]
    closed_sorted = sorted(closed, key=lambda x: x.get("timestamp", ""))
    return {"trades": closed_sorted, "total": len(closed_sorted)}


@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}
