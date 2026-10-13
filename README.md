# KrakenBot

Bot di trading algoritmico multi-mercato in Python: analizza il mercato in tempo reale, sceglie la strategia in base al regime di mercato, gestisce il rischio e invia report e notifiche via Telegram. Include un'API REST con autenticazione e una dashboard web mobile per monitorarlo.

> Progetto personale, oggi non più in esecuzione. Il codice è qui come caso studio tecnico.

---

## Cosa fa

- **Dati in tempo reale** da Kraken tramite WebSocket (ccxt.pro), con loop asincrono separato per ogni asset
- **Layer multi-mercato**: un'unica interfaccia (`market_connector.py`) per crypto (Kraken), forex (OANDA) e azioni (Alpaca). Il bot non sa su quale mercato sta operando
- **Screener dinamico**: ogni ora scansiona le oltre 500 coppie EUR di Kraken (e le prime 100 azioni S&P 500 se abilitate) e aggiorna la watchlist con i candidati migliori
- **Rilevamento del regime di mercato** (trend rialzista, trend ribassista, laterale, alta volatilità) che adatta stop loss, take profit e dimensione delle posizioni
- **Sistema ibrido multi-strategia**: trend following, mean reversion o breakout a seconda del regime, più una strategia short dedicata ai mercati ribassisti
- **Scoring multi-indicatore** su RSI, MACD, Bande di Bollinger, ATR, ADX, volumi e pattern di candele, su due timeframe (1h e 4h)
- **Contesto di mercato** da fonti gratuite: Fear & Greed Index, news RSS, CoinGecko, più un modulo di sentiment analysis offline con lessico finanziario pesato
- **Gestione del rischio**: stop loss e trailing stop gestiti nel loop live, take profit su due livelli, limite di perdita giornaliera, numero massimo di posizioni, cooldown tra trade
- **DCA automatico** (core + satellite) attivato dai depositi reali letti dal ledger di Kraken
- **Backtesting** su dati storici Kraken
- **Modalità simulazione** (`DRY_RUN`) e portafoglio virtuale per testare senza denaro reale
- **Notifiche e report giornaliero** via Telegram

---

## Architettura

```
                        ┌──────────────────────┐
  Kraken / OANDA /  ──► │  market_connector.py │  interfaccia unica
  Alpaca                └──────────┬───────────┘
                                   │
  screener.py ──► watchlist        ▼
  context.py  ──► sentiment ──► bot.py (orchestratore asyncio)
  regime.py   ──► regime            │  un loop per asset
                                    │  stato condiviso thread-safe
        indicators.py ◄─────────────┤
        strategies_hybrid.py ◄──────┤  scelta strategia + scoring
        short_strategy.py ◄─────────┤
                                    ▼
             persistence.py   notifier.py   daily_report.py   dca.py
                    │
                    ▼
           api/main.py (FastAPI + JWT) ──► dashboard/index.html
```

| File | Ruolo |
|---|---|
| `bot.py` | Orchestratore asincrono: avvia i loop per asset e coordina tutti i moduli |
| `market_connector.py` | Astrazione su Kraken, OANDA e Alpaca |
| `screener.py` | Selezione dinamica degli asset da monitorare |
| `indicators.py` | Calcolo degli indicatori tecnici |
| `regime.py` | Rilevamento del regime di mercato |
| `strategies_hybrid.py` | Trend following, mean reversion, breakout |
| `strategy.py` | Scoring di acquisto e vendita, controlli di salute del mercato |
| `short_strategy.py` | Strategia short per mercati ribassisti |
| `context.py`, `sentiment.py` | Contesto di mercato e sentiment delle news |
| `dca.py` | Accumulo automatico sui depositi |
| `virtual_portfolio.py` | Portafoglio simulato |
| `backtest.py` | Backtesting su dati storici |
| `persistence.py` | Salvataggio di posizioni e storico trade |
| `notifier.py`, `daily_report.py` | Notifiche e report Telegram |
| `api/main.py` | API REST con autenticazione JWT |
| `dashboard/index.html` | Dashboard web mobile |
| `settings.py` | Configurazione centralizzata (credenziali solo da `.env`) |

**Stack:** Python 3, asyncio, ccxt / ccxt.pro, pandas, numpy, FastAPI, python-telegram-bot, HTML/CSS/JS.

---

## Risultati

<!-- Da completare con i dati del report: periodo, numero di trade, win rate, P&L, drawdown massimo, e 2-3 grafici in docs/ -->

---

## Avvio

```bash
pip install -r requirements.txt
cp .env.example .env        # inserisci le tue chiavi
python bot.py               # parte in DRY_RUN=true (simulazione)

python backtest.py --symbol BTC/EUR --days 365   # backtest
uvicorn api.main:app --port 8000                 # API per la dashboard
```

Le credenziali vengono lette solo da `.env`, che non è incluso nel repository. Le chiavi Kraken vanno create con i soli permessi Query e Trade, mai Withdraw.

---

## Avvertenza

Progetto a scopo di studio. Il trading comporta rischi significativi di perdita del capitale e niente in questo repository è un consiglio di investimento.
