# Kraken Trading Bot v5.0

Bot di trading automatico per Kraken con strategia multi-indicatore,
gestione rischio avanzata e notifiche Telegram.

---

## Installazione

```bash
# 1. Clona o copia la cartella del bot
cd kraken_bot_v2

# 2. Installa le dipendenze
pip install -r requirements.txt

# 3. Crea il file .env con le tue credenziali
cp .env.example .env
# Apri .env e inserisci le chiavi reali

# 4. Avvia in modalità simulazione (DRY_RUN=true nel .env)
python bot.py

# 5. Quando sei soddisfatto del comportamento, metti DRY_RUN=false
```

---

## Struttura file

```
kraken_bot_v2/
├── bot.py          ← Orchestratore principale, avvia qui
├── config.py       ← Tutti i parametri (budget, soglie, ecc.)
├── indicators.py   ← Calcolo RSI, MACD, Bollinger, ATR, pattern
├── strategy.py     ← Logica BUY/SELL con scoring multi-condizione
├── notifier.py     ← Messaggi Telegram
├── persistence.py  ← Salvataggio posizioni e log trade
├── requirements.txt
├── .env.example    ← Template credenziali
└── README.md
```

---

## Cosa è stato risolto rispetto alla v4.0

### Problema principale: compra/vende su ogni minima variazione
**Causa**: la v4.0 controllava UNA condizione per volta (prezzo sotto banda = compra).
**Fix**: sistema di scoring 6 condizioni per BUY, 5 per SELL.
Servono almeno 4/6 per comprare. Risultato: segnali rari ma affidabili.

### Condizioni BUY (servono 4/6)
1. Prezzo sotto banda inferiore Bollinger (+ margine 0.2%)
2. RSI ≤ 38 (ipervenduto)
3. MACD bullish crossover o istogramma positivo
4. Volume spike ≥ 1.4× la media 20 periodi
5. Pattern candela rialzista (Hammer o Bullish Engulfing)
6. Bande Bollinger larghe ≥ 4% (mercato in movimento, non piatto)

### Condizioni SELL (priorità decrescente)
1. Stop Loss blindato (% configurabile per asset)
2. Trailing Stop (attivo dopo TP1, segue il prezzo al rialzo)
3. Take Profit 2 (target finale, vende tutto)
4. Take Profit 1 (vende 50%, attiva trailing stop sul restante)
5. Segnale tecnico ribassista (3/5 condizioni)

### Altri fix
- `df.iloc[-2]` invece di `[-1]`: usa candela chiusa, non quella live
- Stop loss nativo: il controllo è nel loop live, non su ordine Kraken
  (Kraken non supporta stop-loss nativi sulle coppie spot standard)
- WebSocket singolo per asset (non 16 connessioni multiple)
- Credenziali in .env, mai nel codice
- Max 3 posizioni aperte contemporaneamente
- Limite perdita giornaliera (default 15€): blocca nuovi acquisti
- Cooldown 1 ora tra trade sullo stesso asset
- Budget realistico per 50€ totali (4 asset × ~12€)
- Take profit a due livelli: TP1 vende 50%, TP2 vende il resto
- Trailing stop al 2.5% dal massimo dopo TP1

---

## Configurazione (config.py)

### Budget consigliato con 50€

```python
CRYPTO_CONFIG = {
    "BTC/EUR":  {"budget_eur": 15, "bb_std": 2.0, "sl_pct": 0.045, "tp_pct": 0.06},
    "ETH/EUR":  {"budget_eur": 12, "bb_std": 2.0, "sl_pct": 0.050, "tp_pct": 0.07},
    "SOL/EUR":  {"budget_eur": 12, "bb_std": 1.8, "sl_pct": 0.055, "tp_pct": 0.08},
    "XRP/EUR":  {"budget_eur": 11, "bb_std": 1.8, "sl_pct": 0.060, "tp_pct": 0.09},
}
```

### Parametri chiave

| Parametro | Default | Significato |
|-----------|---------|-------------|
| `MIN_SCORE_BUY` | 4 | Condizioni minime su 6 per comprare |
| `MIN_SCORE_SELL` | 3 | Condizioni minime su 5 per vendita tecnica |
| `TRADE_COOLDOWN` | 3600 | Secondi tra trade sullo stesso asset |
| `MAX_OPEN_POSITIONS` | 3 | Posizioni aperte massime |
| `MAX_DAILY_LOSS_EUR` | 15.0 | Stop bot se perde più di X€/giorno |
| `BB_ENTRY_MARGIN` | 0.002 | Il prezzo deve essere 0.2% SOTTO la banda |
| `TRAILING_STOP_PCT` | 0.025 | Trailing stop 2.5% dal massimo |

---

## Sicurezza

- Le chiavi API Kraken devono avere solo i permessi **Trade** e **Query**
- NON abilitare il permesso "Withdraw" sulle chiavi del bot
- Testa sempre con `DRY_RUN=true` prima di andare live
- Il file `.env` non va mai condiviso o committato su git

---

## Avvertimento

Questo bot opera su mercati reali con denaro reale.
Il trading di criptovalute comporta rischi significativi di perdita del capitale.
Non investire più di quanto puoi permetterti di perdere.
