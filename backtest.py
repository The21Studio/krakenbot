"""
backtest.py — Backtesting storico su dati Kraken

Usa i dati OHLCV storici di Kraken per simulare la strategia
su 12 mesi di dati reali e calcola le performance.

Utilizzo:
    python3 backtest.py --symbol BTC/EUR --days 365
    python3 backtest.py --all --days 180
"""

import asyncio
import argparse
import json
import time
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")  # silenzia i PerformanceWarning cosmetici di pandas


def run_backtest(df: pd.DataFrame, cfg: dict, symbol: str) -> dict:
    from indicators import add_all
    from regime import detect_regime, get_adapted_sl, get_adapted_tp

    if len(df) < 60:
        return {"error": "Dati insufficienti"}

    df = add_all(df.copy())
    from strategy import score_buy, score_sell
    budget   = cfg.get("budget_eur", 600)
    TRADE_COOLDOWN = 3600

    deals    = []   # un "deal" = ciclo completo di una posizione (entry → chiusura totale)
    position = None
    cooldown = 0

    def snap_clean(row):
        return {k:(float(v) if isinstance(v,(int,float,np.integer,np.floating)) else v) for k,v in row.items()}

    for i in range(50, len(df)):
        snap   = snap_clean(df.iloc[i].to_dict())
        snap4h = snap_clean(df.iloc[max(0, i-4)].to_dict())
        price  = float(df.iloc[i]["close"])
        ts     = int(df.iloc[i]["timestamp"])

        if position:
            # usa la VERA logica di uscita del bot
            reason, exit_type = score_sell(snap, position, price, cfg)

            if exit_type == "TAKE_PROFIT_1" and not position["tp1_hit"]:
                # vende il 50%, incassa, tiene il resto
                half = position["remaining"] * 0.5
                position["realized"] += (price - position["entry_price"]) * half
                position["remaining"] -= half
                position["tp1_hit"]   = True
                position["sl_price"]  = position["entry_price"]  # SL a pareggio (come il bot live)
                continue

            if exit_type in ("STOP_LOSS","TAKE_PROFIT_2","TRAILING_STOP","SMART_EXIT"):
                position["realized"] += (price - position["entry_price"]) * position["remaining"]
                position["bars"]      = i - position["bar_in"]
                position["exit"]      = exit_type
                deals.append(position)
                position = None
                cooldown = ts + TRADE_COOLDOWN
                continue
        else:
            if ts < cooldown:
                continue
            try:
                score, _ = score_buy(snap, cfg, snap4h)
                if score >= 5:
                    regime, _, _ = detect_regime(snap, snap4h)
                    atr = snap.get("atr", price * cfg.get("sl_pct", 0.05))
                    sl  = get_adapted_sl(price, atr, cfg, regime)
                    tp1, tp2 = get_adapted_tp(price, cfg, regime)
                    vol = budget / price
                    position = {
                        "entry_price": price, "volume": vol, "remaining": vol,
                        "sl_price": sl, "tp1_price": tp1, "tp2_price": tp2,
                        "tp1_hit": False, "max_price": price, "realized": 0.0,
                        "regime_at_entry": regime, "atr_at_entry": atr,
                        "snap4h_at_entry": snap4h, "bar_in": i, "score": score,
                    }
            except Exception:
                continue

    # posizione ancora aperta a fine dati → mark-to-market
    if position:
        last = float(df.iloc[-1]["close"])
        position["realized"] += (last - position["entry_price"]) * position["remaining"]
        position["bars"] = len(df)-1 - position["bar_in"]
        position["exit"] = "OPEN_MTM"
        deals.append(position)

    if not deals:
        return {"symbol":symbol,"trades":0,"pnl_eur":0,"win_rate":0,"max_drawdown":0,"profit_factor":0}

    # un trade = un deal completo; vince se il P&L NETTO del ciclo è positivo
    nets   = [d["realized"] for d in deals]
    wins   = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    pnl    = sum(nets)

    running=peak=max_dd=0
    for n in nets:
        running += n
        if running > peak: peak = running
        if peak - running > max_dd: max_dd = peak - running

    by_exit = {}
    for d in deals:
        by_exit[d["exit"]] = by_exit.get(d["exit"], 0) + 1

    return {
        "symbol":        symbol,
        "trades":        len(deals),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins)/len(deals)*100,1),
        "pnl_eur":       round(pnl,2),
        "pnl_per_trade": round(pnl/len(deals),2),
        "avg_win":       round(sum(wins)/len(wins),2) if wins else 0,
        "avg_loss":      round(sum(losses)/len(losses),2) if losses else 0,
        "max_drawdown":  round(max_dd,2),
        "avg_bars_held": round(float(np.mean([d["bars"] for d in deals])),1),
        "profit_factor": round(sum(wins)/abs(sum(losses)),2) if losses and sum(losses)!=0 else 999,
        "exits":         by_exit,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/EUR")
    parser.add_argument("--all",    action="store_true")
    parser.add_argument("--days",   type=int, default=180)
    args = parser.parse_args()

    sys.path.insert(0, ".")
    import ccxt.pro as ccxtpro
    from settings import KRAKEN_API_KEY, KRAKEN_SECRET, CRYPTO_CONFIG

    exchange = ccxtpro.kraken({"apiKey":KRAKEN_API_KEY,"secret":KRAKEN_SECRET,"enableRateLimit":True})
    symbols  = list(CRYPTO_CONFIG.keys()) if args.all else [args.symbol]

    print(f"\n{'='*60}\nBACKTESTING — {args.days} giorni\nAsset: {', '.join(symbols)}\n{'='*60}\n")

    results = []
    for sym in symbols:
        try:
            cfg   = CRYPTO_CONFIG.get(sym, {"budget_eur":600,"sl_pct":0.05,"tp_pct":0.08})
            ohlcv = await exchange.fetch_ohlcv(sym, "1h", limit=args.days*24)
            df    = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
            df    = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
            res   = run_backtest(df, cfg, sym)
            results.append(res)
            print(f"  {sym:15} | {res.get('trades',0):3d} trade | WR {res.get('win_rate',0):5.1f}% | "
                  f"PF {res.get('profit_factor',0):5.2f} | P&L {res.get('pnl_eur',0):+8.2f}€ | "
                  f"avgW {res.get('avg_win',0):+6.2f} avgL {res.get('avg_loss',0):+6.2f} | DD -{res.get('max_drawdown',0):.2f}€")
        except Exception as e:
            print(f"  {sym}: ERRORE — {e}")

    await exchange.close()

    if results:
        tot_t = sum(r.get("trades",0) for r in results)
        tot_p = sum(r.get("pnl_eur",0) for r in results)
        tot_w = sum(r.get("wins",0) for r in results)
        tot_l = sum(r.get("losses",0) for r in results)
        wr    = tot_w/tot_t*100 if tot_t else 0
        # profit factor aggregato
        gross_w = sum((r.get("avg_win",0)*r.get("wins",0)) for r in results)
        gross_l = abs(sum((r.get("avg_loss",0)*r.get("losses",0)) for r in results))
        pf = round(gross_w/gross_l,2) if gross_l else 999
        winners = [r for r in results if r.get("pnl_eur",0)>0]
        print(f"\n{'='*60}")
        print(f"TOTALE: {tot_t} trade | WR {wr:.1f}% | Profit Factor {pf} | P&L {tot_p:+.2f}€")
        print(f"Asset in profitto: {len(winners)}/{len(results)} | vittorie {tot_w} · perdite {tot_l}")
        print(f"{'='*60}\n")
        json.dump({"date":datetime.now(timezone.utc).isoformat(),"days":args.days,"results":results,
                   "totals":{"trades":tot_t,"win_rate":round(wr,1),"profit_factor":pf,"pnl_eur":round(tot_p,2)}},
                  open("backtest_results.json","w"), indent=2)
        print("Risultati salvati in backtest_results.json")

if __name__ == "__main__":
    asyncio.run(main())
