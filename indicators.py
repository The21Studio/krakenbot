"""
indicators.py — Suite completa di indicatori tecnici professionali

Tutto ciò che usa un broker professionista:
  Trend:      EMA9/21/50/200, MACD, ADX+DI, Ichimoku
  Momentum:   RSI, Stochastic RSI, CCI, Williams %R, MFI
  Volatilità: ATR, Bollinger Bands, Keltner Channels
  Volume:     OBV, VWAP, Volume Ratio, CMF
  Pattern:    14 pattern candele giapponesi
  Extra:      Divergenze RSI, Supporti/Resistenze dinamici
"""

import pandas as pd
import numpy as np
from settings import BB_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL_P, RSI_PERIOD, ATR_PERIOD


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df[["open","high","low","close","volume"]] = \
        df[["open","high","low","close","volume"]].astype(float)

    df = _ema_suite(df)
    df = _bollinger(df)
    df = _keltner(df)
    df = _macd(df)
    df = _rsi(df)
    df = _stoch_rsi(df)
    df = _cci(df)
    df = _williams_r(df)
    df = _atr(df)
    df = _adx(df)
    df = _ichimoku(df)
    df = _volume_suite(df)
    df = _mfi(df)
    df = _candle_patterns(df)
    df = _divergences(df)
    df = _support_resistance(df)
    return df


# ══════════════════════════════════════════════════════════════
# TREND
# ══════════════════════════════════════════════════════════════

def _ema_suite(df):
    """EMA 9, 21, 50, 200 — la spina dorsale di ogni broker."""
    for p in [9, 21, 50, 200]:
        df[f"ema{p}"] = df["close"].ewm(span=p, adjust=False).mean()

    # Trend di lungo periodo
    df["trend_bull_long"] = df["close"] > df["ema200"]
    df["trend_bull_mid"]  = df["ema21"] > df["ema50"]
    df["trend_bull_short"]= df["ema9"]  > df["ema21"]

    # Golden/Death cross EMA50/200
    df["golden_cross"] = (df["ema50"] > df["ema200"]) & (df["ema50"].shift(1) <= df["ema200"].shift(1))
    df["death_cross"]  = (df["ema50"] < df["ema200"]) & (df["ema50"].shift(1) >= df["ema200"].shift(1))

    # Allineamento trend: tutte le EMA in ordine
    df["trend_aligned_bull"] = (df["ema9"] > df["ema21"]) & (df["ema21"] > df["ema50"])
    df["trend_aligned_bear"] = (df["ema9"] < df["ema21"]) & (df["ema21"] < df["ema50"])
    return df


def _bollinger(df):
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (df["bb_mid"] + 1e-10)
    df["bb_pct"]   = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-10)

    # Squeeze: bande molto strette = esplosione imminente
    df["bb_squeeze"] = df["bb_width"] < df["bb_width"].rolling(50).mean() * 0.75
    return df


def _keltner(df):
    """Keltner Channels — combinati con BB identificano squeeze di mercato."""
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    atr   = _calc_atr_series(df, 10)
    df["kc_upper"] = ema20 + 1.5 * atr
    df["kc_lower"] = ema20 - 1.5 * atr
    # Squeeze momentum: BB dentro Keltner = compressione
    df["squeeze_on"]  = (df["bb_upper"] < df["kc_upper"]) & (df["bb_lower"] > df["kc_lower"])
    df["squeeze_off"] = (~df["squeeze_on"]) & (df["squeeze_on"].shift(1))
    return df


def _macd(df):
    ema_f = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_s = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd"]        = ema_f - ema_s
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL_P, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]
    df["macd_hist_prev"]   = df["macd_hist"].shift(1)
    df["macd_hist_prev2"]  = df["macd_hist"].shift(2)
    df["macd_cross_up"]    = (df["macd_hist"] > 0) & (df["macd_hist"].shift(1) <= 0)
    df["macd_cross_down"]  = (df["macd_hist"] < 0) & (df["macd_hist"].shift(1) >= 0)
    # Momentum MACD: istogramma in crescita/calo
    df["macd_momentum_up"]   = df["macd_hist"] > df["macd_hist_prev"]
    df["macd_momentum_down"] = df["macd_hist"] < df["macd_hist_prev"]
    return df


# ══════════════════════════════════════════════════════════════
# MOMENTUM
# ══════════════════════════════════════════════════════════════

def _rsi(df):
    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=RSI_PERIOD-1, adjust=False).mean()
    avg_l = loss.ewm(com=RSI_PERIOD-1, adjust=False).mean()
    rs    = avg_g / (avg_l + 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    # RSI momentum: sta salendo o scendendo?
    df["rsi_slope"]    = df["rsi"].diff(3)   # variazione su 3 candele
    df["rsi_rising"]   = df["rsi_slope"] > 0
    df["rsi_falling"]  = df["rsi_slope"] < 0

    # Zone RSI
    df["rsi_oversold"]    = df["rsi"] < 30
    df["rsi_overbought"]  = df["rsi"] > 70
    df["rsi_neutral"]     = (df["rsi"] >= 40) & (df["rsi"] <= 60)
    return df


def _stoch_rsi(df):
    """Stochastic RSI — più reattivo del RSI, ottimo per timing preciso."""
    period = 14
    rsi    = df["rsi"]
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10)
    df["stoch_rsi_k"] = stoch_rsi.rolling(3).mean() * 100
    df["stoch_rsi_d"] = df["stoch_rsi_k"].rolling(3).mean()
    df["stoch_cross_up"]   = (df["stoch_rsi_k"] > df["stoch_rsi_d"]) & \
                              (df["stoch_rsi_k"].shift(1) <= df["stoch_rsi_d"].shift(1))
    df["stoch_cross_down"] = (df["stoch_rsi_k"] < df["stoch_rsi_d"]) & \
                              (df["stoch_rsi_k"].shift(1) >= df["stoch_rsi_d"].shift(1))
    df["stoch_oversold"]   = df["stoch_rsi_k"] < 20
    df["stoch_overbought"] = df["stoch_rsi_k"] > 80
    return df


def _cci(df):
    """CCI — Commodity Channel Index. Misura deviazione dal prezzo medio."""
    period = 20
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean())
    df["cci"]            = (tp - sma) / (0.015 * mad + 1e-10)
    df["cci_oversold"]   = df["cci"] < -100
    df["cci_overbought"] = df["cci"] > 100
    return df


def _williams_r(df):
    """Williams %R — misura la posizione del close nel range H-L."""
    period = 14
    hh = df["high"].rolling(period).max()
    ll  = df["low"].rolling(period).min()
    df["williams_r"]    = -100 * (hh - df["close"]) / (hh - ll + 1e-10)
    df["wr_oversold"]   = df["williams_r"] < -80
    df["wr_overbought"] = df["williams_r"] > -20
    return df


# ══════════════════════════════════════════════════════════════
# VOLATILITÀ
# ══════════════════════════════════════════════════════════════

def _calc_atr_series(df, period=14):
    h_l  = df["high"] - df["low"]
    h_cp = (df["high"] - df["close"].shift()).abs()
    l_cp = (df["low"]  - df["close"].shift()).abs()
    tr   = pd.concat([h_l, h_cp, l_cp], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _atr(df):
    df["atr"]     = _calc_atr_series(df, ATR_PERIOD)
    df["atr_pct"] = df["atr"] / (df["close"] + 1e-10)
    # ATR normalizzato su 20 periodi — capisce se volatilità è alta o bassa
    df["atr_norm"] = df["atr"] / df["atr"].rolling(20).mean()
    df["atr_expanding"] = df["atr"] > df["atr"].shift(1)
    return df


# ══════════════════════════════════════════════════════════════
# ADX — FORZA DEL TREND
# ══════════════════════════════════════════════════════════════

def _adx(df):
    """
    ADX + DI+ DI- — il broker più importante per capire se è in trend o laterale.
    ADX > 25 = trend forte (compra/vendi aggressivamente)
    ADX < 20 = laterale (mean reversion)
    DI+ > DI- = trend rialzista
    DI- > DI+ = trend ribassista
    """
    period = 14
    high, low, close = df["high"], df["low"], df["close"]

    tr     = _calc_atr_series(df, period)
    up_m   = high.diff()
    down_m = -low.diff()

    plus_dm  = up_m.where((up_m > down_m) & (up_m > 0), 0.0)
    minus_dm = down_m.where((down_m > up_m) & (down_m > 0), 0.0)

    atr14    = _calc_atr_series(df, period)
    plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean()  / (atr14 + 1e-10)
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / (atr14 + 1e-10)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)

    df["adx"]      = dx.ewm(span=period, adjust=False).mean()
    df["di_plus"]  = plus_di
    df["di_minus"] = minus_di

    df["adx_strong"]    = df["adx"] > 25     # trend forte
    df["adx_weak"]      = df["adx"] < 20     # mercato laterale
    df["adx_bull"]      = df["di_plus"] > df["di_minus"]
    df["adx_bear"]      = df["di_minus"] > df["di_plus"]
    df["adx_cross_up"]  = (df["di_plus"] > df["di_minus"]) & (df["di_plus"].shift(1) <= df["di_minus"].shift(1))
    df["adx_cross_down"]= (df["di_minus"] > df["di_plus"]) & (df["di_minus"].shift(1) <= df["di_plus"].shift(1))
    return df


# ══════════════════════════════════════════════════════════════
# ICHIMOKU CLOUD
# ══════════════════════════════════════════════════════════════

def _ichimoku(df):
    """
    Ichimoku — sistema completo di trend, supporto e resistenza.
    Usato da broker professionisti per decisioni rapide.
    """
    h, l = df["high"], df["low"]

    tenkan  = (h.rolling(9).max()  + l.rolling(9).min())  / 2   # conversion line
    kijun   = (h.rolling(26).max() + l.rolling(26).min()) / 2   # base line
    span_a  = ((tenkan + kijun) / 2).shift(26)                   # leading span A
    span_b  = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)  # leading span B
    chikou  = df["close"].shift(-26)                              # lagging span

    df["ichi_tenkan"] = tenkan
    df["ichi_kijun"]  = kijun
    df["ichi_span_a"] = span_a
    df["ichi_span_b"] = span_b

    # Segnali Ichimoku
    df["ichi_above_cloud"] = df["close"] > df[["ichi_span_a","ichi_span_b"]].max(axis=1)
    df["ichi_below_cloud"] = df["close"] < df[["ichi_span_a","ichi_span_b"]].min(axis=1)
    df["ichi_tk_cross_up"] = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))
    df["ichi_tk_cross_dn"] = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))
    df["ichi_cloud_bull"]  = span_a > span_b  # cloud verde = bullish
    return df


# ══════════════════════════════════════════════════════════════
# VOLUME
# ══════════════════════════════════════════════════════════════

def _volume_suite(df):
    """OBV, VWAP, Volume Ratio — il volume non mente mai."""
    # Volume Ratio
    avg_vol = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / (avg_vol + 1e-10)

    # OBV — On Balance Volume: conferma trend con volume
    obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    df["obv"]          = obv
    df["obv_ema"]      = obv.ewm(span=20, adjust=False).mean()
    df["obv_rising"]   = df["obv"] > df["obv_ema"]    # volume supporta il rialzo

    # VWAP — Volume Weighted Average Price
    # Reset giornaliero (approssimato su 24 periodi)
    tp   = (df["high"] + df["low"] + df["close"]) / 3
    cum_tpv = (tp * df["volume"]).rolling(24).sum()
    cum_vol = df["volume"].rolling(24).sum()
    df["vwap"]         = cum_tpv / (cum_vol + 1e-10)
    df["above_vwap"]   = df["close"] > df["vwap"]    # sopra VWAP = bullish

    # Volume trend: sta aumentando?
    df["vol_expanding"] = df["volume"] > df["volume"].shift(1)
    df["vol_spike"]     = df["vol_ratio"] > 2.0
    return df


def _mfi(df):
    """Money Flow Index — RSI del volume. Rileva accumulazione/distribuzione."""
    period = 14
    tp     = (df["high"] + df["low"] + df["close"]) / 3
    mf     = tp * df["volume"]
    pos_mf = mf.where(tp > tp.shift(1), 0.0)
    neg_mf = mf.where(tp < tp.shift(1), 0.0)
    mfr    = pos_mf.rolling(period).sum() / (neg_mf.rolling(period).sum() + 1e-10)
    df["mfi"]           = 100 - (100 / (1 + mfr))
    df["mfi_oversold"]  = df["mfi"] < 20
    df["mfi_overbought"]= df["mfi"] > 80
    return df


# ══════════════════════════════════════════════════════════════
# PATTERN CANDELE GIAPPONESI (14 pattern)
# ══════════════════════════════════════════════════════════════

def _candle_patterns(df):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body       = (c - o).abs()
    total      = (h - l).replace(0, 1e-10)
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l

    # ── RIALZISTI ────────────────────────────────────────────
    df["hammer"]       = (lower_wick >= body*2) & (body/total < 0.35) & (c > o)
    df["inv_hammer"]   = (upper_wick >= body*2) & (body/total < 0.35) & (c > o)
    df["bull_engulf"]  = (c>o) & (c.shift(1)<o.shift(1)) & (c>o.shift(1)) & (o<c.shift(1))
    df["morning_star"] = (
        (c.shift(2) < o.shift(2)) &                    # prima rossa
        (body.shift(1) < body.shift(2) * 0.3) &        # doji in mezzo
        (c > (o.shift(2) + c.shift(2)) / 2)            # terza chiude a metà prima
    )
    df["three_white"]  = (
        (c > o) & (c.shift(1) > o.shift(1)) & (c.shift(2) > o.shift(2)) &
        (o > o.shift(1)) & (o.shift(1) > o.shift(2))   # 3 candele verdi consecutive
    )
    df["dragonfly_doji"] = (body/total < 0.05) & (lower_wick > total*0.6)
    df["piercing"]     = (
        (c.shift(1) < o.shift(1)) &   # rossa
        (c > o) &                      # verde
        (o < c.shift(1)) &             # apre sotto chiusura precedente
        (c > (o.shift(1) + c.shift(1)) / 2)  # chiude sopra metà rossa
    )

    # ── RIBASSISTI ────────────────────────────────────────────
    df["shooting_star"]= (upper_wick >= body*2) & (body/total < 0.35) & (c < o)
    df["bear_engulf"]  = (c<o) & (c.shift(1)>o.shift(1)) & (c<o.shift(1)) & (o>c.shift(1))
    df["evening_star"] = (
        (c.shift(2) > o.shift(2)) &
        (body.shift(1) < body.shift(2) * 0.3) &
        (c < (o.shift(2) + c.shift(2)) / 2)
    )
    df["three_black"]  = (
        (c < o) & (c.shift(1) < o.shift(1)) & (c.shift(2) < o.shift(2)) &
        (o < o.shift(1)) & (o.shift(1) < o.shift(2))
    )
    df["hanging_man"]  = (lower_wick >= body*2) & (body/total < 0.35) & (c < o)
    df["gravestone_doji"] = (body/total < 0.05) & (upper_wick > total*0.6)

    # ── NEUTRI ────────────────────────────────────────────────
    df["doji"] = body/total < 0.10
    return df


# ══════════════════════════════════════════════════════════════
# DIVERGENZE RSI (segnale professionale avanzato)
# ══════════════════════════════════════════════════════════════

def _divergences(df):
    """
    Divergenza rialzista: prezzo fa nuovi minimi, RSI no → inversione imminente.
    Divergenza ribassista: prezzo fa nuovi massimi, RSI no → calo imminente.
    """
    period = 5
    price_lower_low = df["close"] < df["close"].rolling(period).min().shift(1)
    rsi_higher_low  = df["rsi"]   > df["rsi"].rolling(period).min().shift(1)
    df["bull_divergence"] = price_lower_low & rsi_higher_low

    price_higher_high = df["close"] > df["close"].rolling(period).max().shift(1)
    rsi_lower_high    = df["rsi"]   < df["rsi"].rolling(period).max().shift(1)
    df["bear_divergence"] = price_higher_high & rsi_lower_high
    return df


# ══════════════════════════════════════════════════════════════
# SUPPORTI E RESISTENZE DINAMICI
# ══════════════════════════════════════════════════════════════

def _support_resistance(df):
    """Identifica supporti e resistenze chiave negli ultimi 20 periodi."""
    period = 20
    df["resistance"] = df["high"].rolling(period).max()
    df["support"]    = df["low"].rolling(period).min()

    # Distanza % da supporto e resistenza
    df["dist_from_support"]    = (df["close"] - df["support"])    / (df["support"] + 1e-10)
    df["dist_from_resistance"] = (df["resistance"] - df["close"]) / (df["close"] + 1e-10)

    # Near support/resistance (entro 1%)
    df["near_support"]    = df["dist_from_support"]    < 0.01
    df["near_resistance"] = df["dist_from_resistance"] < 0.01
    return df


# ══════════════════════════════════════════════════════════════
# SNAPSHOT COMPLETO
# ══════════════════════════════════════════════════════════════

def get_snapshot(df: pd.DataFrame, bb_std: float) -> dict:
    """
    Estrae snapshot completo dalla candela chiusa (iloc[-2]).
    Tutti gli indicatori in un dict per strategy.py.
    """
    if len(df) < 55:
        return {}

    row  = df.iloc[-2]   # candela chiusa, non quella live
    row2 = df.iloc[-3]   # candela precedente per confronti

    mid   = float(row["bb_mid"])
    std   = float(row["bb_std"])
    lower = mid - bb_std * std
    upper = mid + bb_std * std

    def safe(val):
        try:
            v = float(val)
            return 0.0 if (v != v or abs(v) == float("inf")) else v  # nan/inf check
        except:
            return 0.0

    def safeb(val):
        try:
            return bool(val)
        except:
            return False

    return {
        # ── Prezzo ──────────────────────────────────────────
        "close":       safe(row["close"]),
        "open":        safe(row["open"]),
        "high":        safe(row["high"]),
        "low":         safe(row["low"]),

        # ── Bollinger ───────────────────────────────────────
        "bb_lower":    lower,
        "bb_mid":      mid,
        "bb_upper":    upper,
        "bb_width":    safe(row["bb_width"]),
        "bb_pct":      safe((row["close"] - lower) / (upper - lower + 1e-10)),
        "bb_squeeze":  safeb(row["bb_squeeze"]),
        "squeeze_on":  safeb(row["squeeze_on"]),
        "squeeze_off": safeb(row["squeeze_off"]),

        # ── EMA ─────────────────────────────────────────────
        "ema9":        safe(row["ema9"]),
        "ema21":       safe(row["ema21"]),
        "ema50":       safe(row["ema50"]),
        "ema200":      safe(row["ema200"]),
        "trend_aligned_bull": safeb(row["trend_aligned_bull"]),
        "trend_aligned_bear": safeb(row["trend_aligned_bear"]),
        "trend_bull_long":    safeb(row["trend_bull_long"]),
        "golden_cross":       safeb(row["golden_cross"]),
        "death_cross":        safeb(row["death_cross"]),

        # ── MACD ────────────────────────────────────────────
        "macd":             safe(row["macd"]),
        "macd_signal":      safe(row["macd_signal"]),
        "macd_hist":        safe(row["macd_hist"]),
        "macd_hist_prev":   safe(row["macd_hist_prev"]),
        "macd_hist_prev2":  safe(row["macd_hist_prev2"]),
        "macd_cross_up":    safeb(row["macd_cross_up"]),
        "macd_cross_down":  safeb(row["macd_cross_down"]),
        "macd_momentum_up": safeb(row["macd_momentum_up"]),
        "macd_momentum_down": safeb(row["macd_momentum_down"]),

        # ── RSI ─────────────────────────────────────────────
        "rsi":          safe(row["rsi"]),
        "rsi_slope":    safe(row["rsi_slope"]),
        "rsi_rising":   safeb(row["rsi_rising"]),
        "rsi_falling":  safeb(row["rsi_falling"]),
        "rsi_oversold": safeb(row["rsi_oversold"]),
        "rsi_overbought": safeb(row["rsi_overbought"]),

        # ── Stochastic RSI ──────────────────────────────────
        "stoch_k":          safe(row["stoch_rsi_k"]),
        "stoch_d":          safe(row["stoch_rsi_d"]),
        "stoch_cross_up":   safeb(row["stoch_cross_up"]),
        "stoch_cross_down": safeb(row["stoch_cross_down"]),
        "stoch_oversold":   safeb(row["stoch_oversold"]),
        "stoch_overbought": safeb(row["stoch_overbought"]),

        # ── CCI ─────────────────────────────────────────────
        "cci":           safe(row["cci"]),
        "cci_oversold":  safeb(row["cci_oversold"]),
        "cci_overbought":safeb(row["cci_overbought"]),

        # ── Williams %R ─────────────────────────────────────
        "williams_r":    safe(row["williams_r"]),
        "wr_oversold":   safeb(row["wr_oversold"]),
        "wr_overbought": safeb(row["wr_overbought"]),

        # ── ATR ─────────────────────────────────────────────
        "atr":          safe(row["atr"]),
        "atr_pct":      safe(row["atr_pct"]),
        "atr_norm":     safe(row["atr_norm"]),
        "atr_expanding":safeb(row["atr_expanding"]),

        # ── ADX ─────────────────────────────────────────────
        "adx":          safe(row["adx"]),
        "di_plus":      safe(row["di_plus"]),
        "di_minus":     safe(row["di_minus"]),
        "adx_strong":   safeb(row["adx_strong"]),
        "adx_weak":     safeb(row["adx_weak"]),
        "adx_bull":     safeb(row["adx_bull"]),
        "adx_bear":     safeb(row["adx_bear"]),
        "adx_cross_up": safeb(row["adx_cross_up"]),
        "adx_cross_down":safeb(row["adx_cross_down"]),

        # ── Ichimoku ────────────────────────────────────────
        "ichi_above_cloud": safeb(row["ichi_above_cloud"]),
        "ichi_below_cloud": safeb(row["ichi_below_cloud"]),
        "ichi_tk_cross_up": safeb(row["ichi_tk_cross_up"]),
        "ichi_tk_cross_dn": safeb(row["ichi_tk_cross_dn"]),
        "ichi_cloud_bull":  safeb(row["ichi_cloud_bull"]),

        # ── Volume ──────────────────────────────────────────
        "vol_ratio":    safe(row["vol_ratio"]),
        "vol_spike":    safeb(row["vol_spike"]),
        "vol_expanding":safeb(row["vol_expanding"]),
        "obv_rising":   safeb(row["obv_rising"]),
        "above_vwap":   safeb(row["above_vwap"]),
        "mfi":          safe(row["mfi"]),
        "mfi_oversold": safeb(row["mfi_oversold"]),
        "mfi_overbought":safeb(row["mfi_overbought"]),

        # ── Pattern candele ─────────────────────────────────
        "hammer":        safeb(row["hammer"]),
        "inv_hammer":    safeb(row["inv_hammer"]),
        "bull_engulf":   safeb(row["bull_engulf"]),
        "morning_star":  safeb(row["morning_star"]),
        "three_white":   safeb(row["three_white"]),
        "dragonfly_doji":safeb(row["dragonfly_doji"]),
        "piercing":      safeb(row["piercing"]),
        "shooting_star": safeb(row["shooting_star"]),
        "bear_engulf":   safeb(row["bear_engulf"]),
        "evening_star":  safeb(row["evening_star"]),
        "three_black":   safeb(row["three_black"]),
        "hanging_man":   safeb(row["hanging_man"]),
        "doji":          safeb(row["doji"]),

        # ── Divergenze ──────────────────────────────────────
        "bull_divergence": safeb(row["bull_divergence"]),
        "bear_divergence": safeb(row["bear_divergence"]),

        # ── Supporti/Resistenze ─────────────────────────────
        "support":            safe(row["support"]),
        "resistance":         safe(row["resistance"]),
        "near_support":       safeb(row["near_support"]),
        "near_resistance":    safeb(row["near_resistance"]),
        "dist_from_support":  safe(row["dist_from_support"]),
        "dist_from_resistance":safe(row["dist_from_resistance"]),
    }
