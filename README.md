# Fondazione2

Fondazione2 è una nuova piattaforma di ricerca quantitativa, decisione AI, paper trading realistico e futura esecuzione controllata su Coinbase Advanced.

Non è una migrazione in-place di `fondazionesemplice`: nasce come progetto nuovo dopo l'audit che ha dichiarato non validi i risultati dell'Arena precedente. La vecchia repository resta solo una fonte storica di lezioni, test di regressione e componenti da riesaminare.

## Baseline storica

- repository precedente: `glaucogaribaldi/fondazionesemplice`
- ultimo commit osservato prima del bootstrap Fondazione2: `a0633cb737e25fb29897b05e6b7cfc1965c5d373`
- commit audit di riferimento: `755e0ba81a4dce4eb86101d4b19821ca45934ad2`
- verdetto audit: `PAPER_INVALID`

## Obiettivo architetturale

```text
Coinbase market data
        |
        v
QuantDinger intelligence / strategy runtime
        |
        +------> Kronos forecast
        |
        +------> Nemotron critic / policy
        |
        v
Fondazione2 Decision Contract
        |
        v
Deterministic Risk Engine
        |
        v
Execution Intent
   +----+----+
   |         |
 PAPER     LIVE
   |         |
Paper      Coinbase Advanced
Executor   Live Executor
   |         |
   +----+----+
        |
        v
PostgreSQL Event Ledger / Audit
```

QuantDinger è una sorgente di intelligence, strategia, backtest e runtime; non ha autorità per bypassare il Risk Engine di Fondazione2. Kronos e Nemotron non possiedono credenziali Coinbase e non inviano ordini.

## OpenClaw

Fondazione2 prevede due ruoli separati:

- **System Agent**: infrastruttura, GPU, servizi, health, deploy, rollback, database, QuantDinger, Kronos, Nemotron e connettività Coinbase.
- **Strategy Agent**: proposta strategia → Git commit → test → backtest → validazione → paper candidate → deploy paper → osservazione → rollback/promozione proposta.

La promozione al live richiede sempre un gate umano esplicito.

## Stato attuale

`ARCHITECTURE_BOOTSTRAP`

In questa fase non è autorizzato alcun wipe o deploy sulla VPS. Prima vengono definiti architettura, contratti, installer, test e procedura di reinstallazione riproducibile.
