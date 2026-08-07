# Fondazione2 Safety Contract

Questo contratto vale per codice, agenti, deploy e runtime.

## Modalità iniziale obbligatoria

```text
TRADING_MODE=paper
LIVE_CAPABLE=true
LIVE_ARMED=false
```

`LIVE_CAPABLE=true` significa soltanto che l'architettura può contenere un adapter live. Non autorizza l'invio di ordini.

## Regole non negoziabili

1. Kronos non invia ordini.
2. Nemotron non invia ordini.
3. QuantDinger non può bypassare il Risk Engine Fondazione2.
4. OpenClaw Strategy Agent non promuove autonomamente una strategia al live.
5. OpenClaw System Agent non arma il live senza un task esplicito e un gate umano separato.
6. Tutte le decisioni operative passano dal Risk Engine deterministico.
7. Una exit protettiva non può essere bloccata da cooldown pensati per nuove entrate.
8. Snapshot e riserva capitale devono essere atomici rispetto all'esecuzione.
9. Stop loss, take profit e altre protezioni dichiarate devono essere realmente persistite ed eseguite.
10. Il mark-to-market multi-asset non può usare silenziosamente prezzi stale.
11. La semantica di `OPEN`, `ADD`, `REDUCE`, `CLOSE` deve essere univoca e testata.
12. Paper e live devono consumare lo stesso `ExecutionIntent` normalizzato.
13. I segreti non entrano in Git, prompt, report o log.
14. Le credenziali Coinbase live future dovranno escludere trasferimenti/prelievi quando tecnicamente possibile.
15. Ogni ordine live futuro deve avere idempotency key e reconciliation.

## Regressioni derivate dall'audit Fondazione Semplice

Fondazione2 non può dichiarare il paper engine valido finché non esistono test automatici che provino:

- SL/TP realmente eseguiti;
- nessuna TOCTOU su capitale/posizioni;
- SELL/CLOSE/REDUCE protettivi non bloccati dal cooldown entry;
- REDUCE percentuale riferito alla posizione e non all'equity;
- pricing multi-asset fresco o fail-closed;
- fee contabilizzate una sola volta nelle metriche nette;
- configurazioni risk realmente applicate;
- test sintetici isolati dal ledger paper;
- restart idempotente senza ordini duplicati;
- comportamento fail-closed su timeout/model failure.

## Gate distruttivo VPS

La futura reinstallazione senza backup è prevista ma NON autorizzata dal semplice merge di questo documento.

Prerequisiti:

1. installer Fondazione2 presente in Git;
2. installer revisionato e testato su ambiente non produttivo o dry-run;
3. commit immutabile di installazione scelto;
4. inventario target verificato;
5. conferma esplicita dell'operatore nel task di wipe.

Non recuperare database, run o memoria operativa della vecchia Fondazione dopo il wipe, salvo file esplicitamente selezionati come documentazione storica.

## Gate live futuro

Il live richiederà almeno:

- paper engine certificato;
- Coinbase adapter paper/live contract tests;
- backtest + walk-forward + OOS per la strategia candidata;
- paper forward trial;
- reconciliation e kill switch testati;
- limiti live separati e più restrittivi;
- chiavi Coinbase configurate fuori Git;
- autorizzazione umana esplicita e distinta dal deploy.

Fino a quel momento qualsiasi richiesta live deve risultare `LIVE_NOT_ARMED`.
