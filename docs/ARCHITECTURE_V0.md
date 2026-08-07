# Fondazione2 Architecture v0

Status: `DESIGN_BASELINE`

## Missione

Costruire da zero una piattaforma paper-first per Coinbase che permetta di progettare, validare, versionare e osservare strategie quantitative/AI usando QuantDinger, Kronos e Nemotron, mantenendo rischio ed esecuzione sotto controllo deterministico.

La piattaforma deve essere predisposta per il live, ma il live non è autorizzato nella fase bootstrap.

## Principio principale

Paper e live devono condividere lo stesso contratto decisionale e gli stessi controlli di rischio. Cambia solo l'adapter di esecuzione.

```text
                         Operator
                            |
                     OpenClaw control
                            |
                            v
Coinbase ---> Market Data / Universe / Features
                            |
                 +----------+----------+
                 |          |          |
                 v          v          v
            QuantDinger   Kronos    Nemotron
              Strategy    Forecast    Critic
                 |          |          |
                 +----------+----------+
                            |
                            v
                    Decision Aggregator
                            |
                            v
                  Deterministic Risk Engine
                            |
                            v
                    Portfolio Allocator
                            |
                            v
                     ExecutionIntent
                      /             \
                     v               v
              Paper Executor   Coinbase Live Adapter
                      \             /
                       v           v
                    PostgreSQL Event Ledger
                            |
                            v
                 Metrics / Dashboard / Audit
```

## Componenti

### 1. Coinbase Market Adapter

Responsabilità:

- public market data REST/WebSocket;
- ticker, candles, trades e order book quando necessario;
- normalizzazione simboli e precisioni;
- product metadata;
- dynamic universe inputs;
- in futuro, account/order/fill API attraverso un adapter live separato.

Le credenziali private non devono essere necessarie per il bootstrap paper.

### 2. QuantDinger

QuantDinger viene usato come piattaforma quantitativa e runtime estendibile per:

- Strategy API;
- indicatori e feature;
- backtest;
- experiment workflow;
- paper runtime;
- strategy scheduling;
- Agent Gateway/MCP dove utile;
- monitoring e audit integrabili.

QuantDinger non è l'autorità finale di rischio di Fondazione2.

### 3. Kronos Service

Input:

- serie temporali normalizzate;
- timeframe e horizon espliciti;
- feature opzionali consentite dal contratto.

Output strutturato minimo:

- model/version hash;
- horizon;
- trajectory/distribution;
- expected return;
- volatility forecast;
- uncertainty/confidence con semantica documentata;
- timestamp input/output.

Kronos non invia ordini.

### 4. Nemotron Service

Ruolo iniziale: critic/policy model, non trader discrezionale libero.

Riceve:

- strategy definition/version;
- market/regime state;
- QuantDinger strategy intent;
- Kronos forecast;
- portfolio state;
- capital state;
- risk envelope informativo.

Restituisce output tipizzato, per esempio:

- `NO_TRADE`;
- `OPEN`;
- `ADD`;
- `REDUCE`;
- `CLOSE`;
- confidence;
- rationale sintetica/auditabile;
- invalidation conditions.

Nemotron non possiede credenziali Coinbase.

### 5. Decision Aggregator

Unifica QuantDinger, Kronos e Nemotron in un singolo `DecisionCandidate`. Non applica mutazioni al portafoglio.

Deve rendere esplicito quale componente ha contribuito a ogni campo.

### 6. Deterministic Risk Engine

Ultimo gate prima dell'esecuzione.

Deve controllare almeno:

- modalità paper/live;
- allowlist dinamica dei prodotti;
- freshness dei dati;
- spread/liquidità;
- precision/min notional;
- max position e max exposure;
- capital reservations;
- correlation/concentration limits;
- daily loss e drawdown;
- stop/take/trailing/time exits;
- uscita protettiva sempre possibile;
- idempotenza;
- stato capital state machine;
- live authorization gates.

Nessun modello AI può modificarne l'esito durante il ciclo decisionale.

### 7. Portfolio Allocator

Trasforma una decisione approvata in quantità e prezzi compatibili con il broker/exchange, preservando i limiti del Risk Engine.

### 8. Paper Executor

Il simulatore deve modellare almeno:

- bid/ask;
- spread;
- fee;
- slippage;
- min notional;
- partial fill quando modellato;
- stop loss;
- take profit;
- trailing stop;
- time exit;
- OPEN/ADD/REDUCE/CLOSE;
- ordini e stati;
- atomicità;
- mark-to-market multi-asset con freshness.

### 9. Coinbase Live Executor

Implementa la stessa semantica di `ExecutionIntent` del paper executor.

Deve essere predisposto ma disarmato. L'abilitazione live richiederà una modifica separata, revisione e gate umano.

### 10. PostgreSQL Event Ledger

Fonte canonica per:

- market snapshot references;
- model outputs;
- strategy candidates;
- risk decisions;
- orders/fills;
- portfolio state;
- strategy/version hashes;
- agent/deploy events;
- metriche riproducibili.

SQLite non è previsto come ledger canonico Fondazione2.

## OpenClaw control plane

### System Agent

Gestisce infrastruttura e runtime.

### Strategy Agent

Gestisce lifecycle di ricerca e rilascio delle strategie.

Gli agenti usano GitHub come fonte di task e versione. Il runtime non deve evolvere attraverso modifiche non versionate.

## Fasi

1. architecture/contracts;
2. reproducible installer;
3. clean VPS rebuild;
4. QuantDinger baseline;
5. Coinbase market adapter;
6. Kronos + Nemotron services;
7. decision/risk contracts;
8. realistic paper execution;
9. Strategy Agent workflow;
10. candidate strategies;
11. backtest/OOS/walk-forward;
12. paper forward trial;
13. eventuale live gate separato.
