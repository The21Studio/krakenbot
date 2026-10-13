"""
market_connector.py — Layer di astrazione multi-mercato

Nasconde le differenze tra Kraken (crypto), OANDA (forex), Alpaca (stocks).
Il bot usa sempre le stesse funzioni indipendentemente dal mercato.

Interface unificata:
  fetch_ohlcv(symbol, timeframe, limit) → DataFrame
  fetch_price(symbol) → float
  place_order(symbol, side, amount) → dict
  get_balance() → float
  is_market_open(symbol) → bool
"""

import asyncio
import time
import json
import os
from datetime import datetime, timezone
import urllib.request
import urllib.parse

import pandas as pd

from settings import (
    KRAKEN_API_KEY, KRAKEN_SECRET,
    OANDA_API_KEY, OANDA_ACCOUNT_ID,
    ALPACA_API_KEY, ALPACA_SECRET,
    DRY_RUN,
    STOCKS_MARKET_OPEN_UTC, STOCKS_MARKET_CLOSE_UTC, STOCKS_TRADING_DAYS,
)


# ══════════════════════════════════════════════════════════════
# KRAKEN CONNECTOR (crypto)
# ══════════════════════════════════════════════════════════════

class KrakenConnector:
    """Wrapper per ccxt.pro Kraken — già esistente nel bot."""

    def __init__(self, exchange):
        self.exchange = exchange
        self.market   = "crypto"

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        candles = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(candles, columns=["timestamp","open","high","low","close","volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})

    async def fetch_price(self, symbol: str) -> float:
        ticker = await self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    async def place_order(self, symbol: str, side: str, amount: float) -> dict:
        if DRY_RUN:
            price = await self.fetch_price(symbol)
            return {"symbol": symbol, "side": side, "amount": amount,
                    "price": price, "status": "closed", "dry_run": True}
        order = await self.exchange.create_market_order(symbol, side, amount)
        return order

    async def get_balance(self) -> float:
        bal = await self.exchange.fetch_balance()
        return float(bal.get("EUR", {}).get("free", 0))

    def is_market_open(self, symbol: str) -> bool:
        return True  # crypto 24/7


# ══════════════════════════════════════════════════════════════
# OANDA CONNECTOR (forex)
# ══════════════════════════════════════════════════════════════

class OandaConnector:
    """Connettore OANDA REST API v20 per forex."""

    BASE_URL = "https://api-fxtrade.oanda.com/v3"  # live
    DEMO_URL = "https://api-fxpractice.oanda.com/v3"  # demo

    # Mappa timeframe comune → OANDA granularity
    TF_MAP = {
        "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
        "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
    }

    def __init__(self):
        self.market  = "forex"
        self.api_key = OANDA_API_KEY
        self.account = OANDA_ACCOUNT_ID
        self.base    = self.DEMO_URL if DRY_RUN else self.BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

    def _get(self, path: str) -> dict:
        url = f"{self.base}{path}"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def _post(self, path: str, data: dict) -> dict:
        url     = f"{self.base}{path}"
        payload = json.dumps(data).encode()
        req     = urllib.request.Request(url, data=payload,
                                          headers=self.headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        gran = self.TF_MAP.get(timeframe, "H1")
        # OANDA usa _ invece di / (EUR/USD → EUR_USD)
        instrument = symbol.replace("/", "_")
        data = self._get(f"/instruments/{instrument}/candles?granularity={gran}&count={limit}")
        candles = data.get("candles", [])
        rows = []
        for c in candles:
            if c.get("complete"):
                mid = c["mid"]
                rows.append({
                    "timestamp": int(pd.Timestamp(c["time"]).timestamp() * 1000),
                    "open":   float(mid["o"]),
                    "high":   float(mid["h"]),
                    "low":    float(mid["l"]),
                    "close":  float(mid["c"]),
                    "volume": float(c.get("volume", 0)),
                })
        df = pd.DataFrame(rows)
        if not df.empty:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    async def fetch_price(self, symbol: str) -> float:
        instrument = symbol.replace("/", "_")
        data = self._get(f"/accounts/{self.account}/pricing?instruments={instrument}")
        price = data["prices"][0]
        return (float(price["asks"][0]["price"]) + float(price["bids"][0]["price"])) / 2

    async def place_order(self, symbol: str, side: str, amount: float) -> dict:
        if DRY_RUN:
            price = await self.fetch_price(symbol)
            return {"symbol": symbol, "side": side, "amount": amount,
                    "price": price, "status": "closed", "dry_run": True}
        instrument = symbol.replace("/", "_")
        units = str(int(amount)) if side == "buy" else str(-int(amount))
        data = self._post(
            f"/accounts/{self.account}/orders",
            {"order": {"type": "MARKET", "instrument": instrument, "units": units}}
        )
        return data

    async def get_balance(self) -> float:
        data = self._get(f"/accounts/{self.account}/summary")
        return float(data["account"]["NAV"])

    def is_market_open(self, symbol: str) -> bool:
        """Forex aperto Lun 00:00 UTC - Ven 22:00 UTC."""
        now = datetime.now(timezone.utc)
        dow = now.weekday()  # 0=Lun, 6=Dom
        if dow == 5:  # Sabato chiuso
            return False
        if dow == 6:  # Domenica: apre alle 22:00
            return now.hour >= 22
        if dow == 4 and now.hour >= 22:  # Venerdì dopo 22 chiuso
            return False
        return True


# ══════════════════════════════════════════════════════════════
# ALPACA CONNECTOR (stocks USA)
# ══════════════════════════════════════════════════════════════

class AlpacaConnector:
    """Connettore Alpaca Markets API v2 per azioni USA."""

    DATA_URL  = "https://data.alpaca.markets/v2"
    TRADE_URL = "https://paper-api.alpaca.markets/v2" if DRY_RUN else "https://api.alpaca.markets/v2"

    TF_MAP = {
        "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
        "1h": "1Hour", "4h": "4Hour", "1d": "1Day",
    }

    def __init__(self):
        self.market  = "stocks"
        self.headers = {
            "APCA-API-KEY-ID":     ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET,
        }

    def _get(self, base: str, path: str, params: dict = None) -> dict:
        """GET con retry automatico e backoff esponenziale."""
        import time as _time
        url = f"{base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self.headers)

        last_err = None
        for attempt in range(3):  # max 3 tentativi
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 401:
                    print(f"[ALPACA] 401 Unauthorized — chiavi API non valide")
                    raise  # Non ritentare su 401
                elif e.code == 429:
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s
                    print(f"[ALPACA] Rate limit (429) — attendo {wait}s")
                    _time.sleep(wait)
                else:
                    print(f"[ALPACA] HTTP {e.code} — tentativo {attempt+1}/3")
                    _time.sleep(2 ** attempt)
            except Exception as e:
                last_err = e
                print(f"[ALPACA] Errore rete — tentativo {attempt+1}/3: {e}")
                _time.sleep(2 ** attempt)

        raise last_err or Exception("Alpaca _get fallito dopo 3 tentativi")

    def _post(self, path: str, data: dict) -> dict:
        url     = f"{self.TRADE_URL}{path}"
        payload = json.dumps(data).encode()
        req     = urllib.request.Request(url, data=payload,
                                          headers={**self.headers,"Content-Type":"application/json"},
                                          method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        from datetime import datetime, timedelta, timezone as tz
        tf    = self.TF_MAP.get(timeframe, "1Hour")
        end   = datetime.now(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        start = (datetime.now(tz.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data  = self._get(self.DATA_URL, f"/stocks/{symbol}/bars",
                          {"timeframe": tf, "start": start, "end": end,
                           "feed": "iex", "sort": "asc", "limit": 1000})
        all_bars = data.get("bars", [])
        bars = all_bars[-limit:] if len(all_bars) > limit else all_bars
        rows = []
        for b in bars:
            rows.append({
                "timestamp": int(pd.Timestamp(b["t"]).timestamp() * 1000),
                "open":   float(b["o"]),
                "high":   float(b["h"]),
                "low":    float(b["l"]),
                "close":  float(b["c"]),
                "volume": float(b["v"]),
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    async def fetch_price(self, symbol: str) -> float:
        data = self._get(self.DATA_URL, f"/stocks/{symbol}/trades/latest")
        return float(data["trade"]["p"])

    async def place_order(self, symbol: str, side: str, amount: float) -> dict:
        if DRY_RUN:
            price = await self.fetch_price(symbol)
            return {"symbol": symbol, "side": side, "amount": amount,
                    "price": price, "status": "accepted", "dry_run": True}
        data = self._post("/orders", {
            "symbol": symbol, "qty": str(amount),
            "side": side, "type": "market", "time_in_force": "day",
        })
        return data

    async def get_balance(self) -> float:
        data = self._get(self.TRADE_URL, "/account")
        return float(data.get("cash", 0))

    def is_market_open(self, symbol: str) -> bool:
        """NYSE aperto Lun-Ven 14:30-21:00 UTC."""
        now = datetime.now(timezone.utc)
        if now.weekday() not in STOCKS_TRADING_DAYS:
            return False
        h = now.hour + now.minute / 60
        return STOCKS_MARKET_OPEN_UTC <= h < STOCKS_MARKET_CLOSE_UTC


# ══════════════════════════════════════════════════════════════
# FACTORY: crea il connettore giusto per ogni asset
# ══════════════════════════════════════════════════════════════

_connectors = {}  # cache connettori

def get_connector(symbol: str, kraken_exchange=None):
    """Ritorna il connettore corretto in base al mercato dell'asset."""
    from settings import ALL_ASSETS
    cfg    = ALL_ASSETS.get(symbol, {})
    market = cfg.get("market", "crypto")

    if market == "crypto":
        key = "kraken"
        if key not in _connectors and kraken_exchange:
            _connectors[key] = KrakenConnector(kraken_exchange)
        return _connectors.get(key)

    elif market == "forex":
        key = "oanda"
        if key not in _connectors:
            _connectors[key] = OandaConnector()
        return _connectors[key]

    elif market == "stocks":
        key = "alpaca"
        if key not in _connectors:
            _connectors[key] = AlpacaConnector()
        return _connectors[key]

    return None


def init_connectors(kraken_exchange):
    """Inizializza tutti i connettori abilitati."""
    from settings import MARKET_CRYPTO_ENABLED, MARKET_FOREX_ENABLED, MARKET_STOCKS_ENABLED
    if MARKET_CRYPTO_ENABLED:
        _connectors["kraken"] = KrakenConnector(kraken_exchange)
    if MARKET_FOREX_ENABLED and OANDA_API_KEY:
        _connectors["oanda"]  = OandaConnector()
    if MARKET_STOCKS_ENABLED and ALPACA_API_KEY:
        _connectors["alpaca"] = AlpacaConnector()
    return _connectors
