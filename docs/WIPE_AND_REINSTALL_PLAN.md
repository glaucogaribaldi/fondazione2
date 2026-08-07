# Fondazione2 Clean VPS Reinstall Plan

Status: `PLANNED_NOT_AUTHORIZED`

## Intento

La VPS Fondazione esistente verrà ricostruita come host Fondazione2 pulito senza backup applicativo della vecchia Fondazione.

Non verranno migrati:

- vecchio ledger;
- vecchie run paper;
- vecchi database applicativi;
- vecchi container/volumi Fondazione;
- vecchia memoria operativa OpenClaw Fondazione;
- vecchi workaround/bootstrap state.

GitHub e i documenti selezionati restano lo storico tecnico.

## Perché il wipe non avviene subito

Prima di distruggere l'ambiente esistente Fondazione2 deve essere installabile da un commit immutabile e deve avere almeno un preflight verificabile. In caso contrario il wipe trasformerebbe il progetto in una configurazione manuale non riproducibile.

## Prerequisiti

### Gate A - repository

- architecture baseline presente;
- safety contract presente;
- installer/compose presente;
- `.env.example` senza segreti;
- dependency pinning definito;
- QuantDinger upstream ref fissato;
- Kronos source/model ref fissati;
- Nemotron model/runtime ref fissati;
- Coinbase public adapter installabile;
- smoke test non finanziario presente.

### Gate B - install validation

- installer dry-run/preflight PASS;
- `docker compose config` PASS;
- secret generation locale definita;
- GPU preflight definito;
- database migration bootstrap definito;
- health/readiness definiti;
- live risulta disarmato per default.

### Gate C - target identity

Prima del wipe il System Agent deve produrre inventario sanitizzato di:

- hostname;
- cloud project/zone/instance quando disponibili;
- machine type;
- GPU;
- RAM/storage;
- network/Tailscale identity;
- boot/data disks;
- current Fondazione processes/containers da eliminare.

Non deve preservare il loro stato applicativo.

### Gate D - operator authorization

Il task distruttivo futuro deve richiedere conferma esplicita:

`ERASE_OLD_FOUNDATION_AND_INSTALL_FONDAZIONE2_WITHOUT_BACKUP`

La frase non va considerata già fornita perché compare in questo documento.

## Obiettivo del nuovo host

Directory autorevole:

`/opt/fondazione2`

Runtime atteso iniziale:

- Ubuntu supportato e documentato;
- Docker + Compose;
- NVIDIA drivers/container toolkit;
- PostgreSQL;
- Redis se richiesto da QuantDinger;
- QuantDinger pinned runtime;
- Kronos service;
- Nemotron + SGLang service;
- Fondazione2 decision/risk services;
- Coinbase public market adapter;
- Paper Executor;
- observability;
- OpenClaw System/Strategy Agent integration.

Initial state:

```text
TRADING_MODE=paper
LIVE_ARMED=false
Coinbase private credentials=not required
```

## Install sequence

1. stop old Fondazione workload;
2. erase old Fondazione application/container state according to the installer;
3. install fresh runtime prerequisites;
4. clone Fondazione2 at immutable ref;
5. generate local secrets;
6. install/start PostgreSQL/Redis;
7. install/start QuantDinger pinned ref;
8. install/start Kronos;
9. install/start Nemotron/SGLang;
10. start Fondazione2 services;
11. configure Coinbase public data only;
12. run migrations;
13. run preflight/smoke/health;
14. prove live is disarmed;
15. create a clean install evidence report.

## Failure policy

Because the operator explicitly intends no backup of the old application state, rollback means repairing/reinstalling Fondazione2 from Git, not restoring Fondazione Semplice.

Do not silently fall back to old binaries, old containers or old memory if the new installation fails.
