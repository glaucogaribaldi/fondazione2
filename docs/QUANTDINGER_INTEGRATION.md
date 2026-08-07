# QuantDinger Integration Plan

## Scopo

Usare QuantDinger come piattaforma quantitativa e runtime di strategia all'interno di Fondazione2, senza delegargli l'autorità finale di rischio o permettergli di bypassare il control plane Fondazione2.

## Upstream scelto

Repository upstream di riferimento:

`https://github.com/OpenByteInc/QuantDinger`

Durante l'implementazione deve essere fissato a un tag/commit immutabile dopo audit della versione scelta.

## Funzioni che vogliamo riusare

QuantDinger v5 espone superfici utili per:

- Strategy API V2;
- indicatori;
- backtest ed experiment workflow;
- paper/live strategy runtime;
- PostgreSQL state;
- durable workers;
- scheduling;
- Agent Gateway / MCP;
- observability opzionale;
- adapter extension model.

Fondazione2 non deve duplicare queste funzioni se l'upstream soddisfa i nostri test e confini di sicurezza.

## Funzioni che restano autorità Fondazione2

- Decision Contract;
- normalizzazione dei contributi QuantDinger/Kronos/Nemotron;
- Deterministic Risk Engine;
- Capital State Machine;
- portfolio-level exposure controls;
- live arming gate;
- Coinbase execution authorization;
- audit di promozione strategia.

## Pattern di integrazione

```text
QuantDinger Strategy API V2
          |
          | StrategyIntent
          v
Fondazione2 Decision Aggregator
          |
          +---- KronosForecast
          |
          +---- NemotronPolicy
          |
          v
DecisionCandidate
          |
          v
Deterministic Risk Engine
          |
          v
ExecutionIntent
```

QuantDinger può essere usato anche per backtest e paper runtime, ma qualsiasi percorso di esecuzione integrato deve rispettare il nostro `ExecutionIntent` o un adapter formalmente equivalente.

## Coinbase

L'elenco upstream osservato degli exchange crypto integrati non include Coinbase. Fondazione2 deve quindi implementare o verificare un adapter Coinbase Advanced dedicato usando l'extension boundary ufficiale di QuantDinger.

Il lavoro deve essere diviso in due superfici:

### Coinbase Public Market Data Adapter

Nessuna credenziale privata richiesta.

Deve normalizzare almeno:

- products/master data;
- candles;
- ticker;
- bid/ask;
- order book quando richiesto;
- precision/base increment/quote increment;
- min notional e stato prodotto;
- timestamps/freshness.

### Coinbase Account/Execution Adapter

Creato tecnicamente ma disarmato nella fase paper.

Contratto futuro:

- accounts/balances;
- positions where applicable;
- create order;
- cancel order;
- order status;
- fills;
- idempotency;
- retry classification;
- reconciliation;
- precision and sizing;
- no transfer/withdrawal operations in Fondazione2.

## Strategy API V2

La stessa strategia deve poter attraversare:

```text
source strategy
 -> backtest
 -> validated paper candidate
 -> paper runtime
 -> future live runtime
```

senza riscrivere la logica strategica per il passaggio paper/live.

Le assunzioni di execution del backtest devono essere esplicite e compatibili con quelle del Paper Executor Fondazione2.

## Agent Gateway / MCP

OpenClaw Strategy Agent può usare le superfici agentiche QuantDinger per ricerca, backtest, report e gestione strategy candidate se:

- i token sono scoped;
- live trading resta disabilitato;
- nessuna credenziale broker viene esposta all'agente;
- tutte le write action sono auditabili;
- le modifiche di strategia restano versionate in Git.

## Decisione iniziale

QuantDinger è un sottosistema di Fondazione2, non il proprietario del portafoglio e non il broker finale.

Prima del deploy reale devono essere prodotti:

1. upstream audit report;
2. commit/tag pin;
3. dependency/license inventory;
4. Coinbase extension design;
5. Strategy API V2 proof-of-concept;
6. paper/live semantic compatibility tests.
