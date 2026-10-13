"""
regime.py — Regime detection DEFINITIVO v5.0
Nessun regime blocca gli acquisti.
Il regime adatta SOLO SL, TP e budget.
"""

from dataclasses import dataclass
from typing import Literal

Regime = Literal["TREND_BULL", "TREND_BEAR", "LATERAL", "HIGH_VOL"]


@dataclass
class RegimeConfig:
    regime:          Regime
    label:           str
    emoji:           str
    sl_multiplier:   float
    tp_multiplier:   float
    size_multiplier: float


REGIME_CONFIGS: dict[str, RegimeConfig] = {
    "TREND_BULL": RegimeConfig(
        regime="TREND_BULL", label="Trend rialzista", emoji="🚀",
        sl_multiplier=2.5,  # SL largo: lascia respirare il trend
        tp_multiplier=1.4,  # TP ambizioso
        size_multiplier=1.0,
    ),
    "TREND_BEAR": RegimeConfig(
        regime="TREND_BEAR", label="Trend ribassista", emoji="🔴",
        sl_multiplier=1.2,  # SL stretto: esce subito se sbaglia
        tp_multiplier=0.8,  # TP conservativo
        size_multiplier=0.5, # budget dimezzato
    ),
    "LATERAL": RegimeConfig(
        regime="LATERAL", label="Laterale", emoji="↔",
        sl_multiplier=1.5,
        tp_multiplier=1.0,
        size_multiplier=0.8,
    ),
    "HIGH_VOL": RegimeConfig(
        regime="HIGH_VOL", label="Volatile", emoji="⚡",
        sl_multiplier=1.0,  # SL strettissimo
        tp_multiplier=1.2,  # TP un po' più alto per compensare
        size_multiplier=0.3, # budget al 30%
    ),
}


def detect_regime(snap1h: dict, snap4h: dict | None) -> tuple[Regime, str, dict]:
    signals  = {}
    bb_width = snap1h.get("bb_width", 0.03)
    atr_pct  = snap1h.get("atr_pct",  0.02)
    rsi_1h   = snap1h.get("rsi",      50)
    macd_h   = snap1h.get("macd_hist", 0)
    rsi_4h   = snap4h["rsi"]       if snap4h else rsi_1h
    macd_4h  = snap4h["macd_hist"] if snap4h else macd_h
    price_4h = snap4h["close"]     if snap4h else snap1h.get("close", 0)
    mid_4h   = snap4h["bb_mid"]    if snap4h else snap1h.get("bb_mid", price_4h)

    signals.update({"bb_width": bb_width, "atr_pct": atr_pct,
                    "rsi_1h": rsi_1h, "rsi_4h": rsi_4h})

    # HIGH_VOL: entrambe le condizioni devono essere estreme
    if atr_pct > 0.07 and bb_width > 0.15:
        return "HIGH_VOL", f"Volatilità estrema ATR={atr_pct*100:.1f}%", signals

    # TREND_BEAR: almeno 3/4 conferme ribassiste su 4H
    bear = sum([
        rsi_4h < 35,
        macd_4h < -0.001,
        price_4h < mid_4h * 0.98,
        bool(snap4h and snap4h.get("macd_cross_down")),
    ])
    signals["bear_count"] = bear
    if bear >= 3:
        return "TREND_BEAR", f"Trend ribassista {bear}/4", signals

    # LATERAL: bande strette + RSI centrale + MACD piatto
    lat = sum([
        bb_width < 0.03,
        40 <= rsi_1h <= 60,
        abs(macd_h) < snap1h.get("atr", 1) * 0.001,
    ])
    signals["lateral_score"] = lat
    if lat >= 2:
        return "LATERAL", f"Laterale {lat}/3", signals

    # Default: trend rialzista
    return "TREND_BULL", f"Trend rialzista RSI4H={rsi_4h:.1f}", signals


def get_adapted_sl(entry: float, atr: float, cfg: dict, regime: Regime) -> float:
    rc = REGIME_CONFIGS[regime]
    sl_atr   = entry - (rc.sl_multiplier * atr)
    sl_fixed = entry * (1 - cfg.get("sl_pct", 0.05))
    return max(sl_atr, sl_fixed)


def get_adapted_tp(entry: float, cfg: dict, regime: Regime) -> tuple[float, float]:
    rc  = REGIME_CONFIGS[regime]
    tp1 = entry * (1 + cfg.get("tp_pct", 0.07) * rc.tp_multiplier)
    tp2 = entry * (1 + cfg.get("tp_pct", 0.07) * rc.tp_multiplier * 2.5)
    return tp1, tp2


def get_adapted_budget(budget_eur: float, regime: Regime) -> float:
    return budget_eur * REGIME_CONFIGS[regime].size_multiplier
