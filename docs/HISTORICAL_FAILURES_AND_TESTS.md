# Historical Failures -> Fondazione2 Acceptance Tests

Questo documento non propone di riparare `fondazionesemplice`. Converte i suoi failure mode noti in test obbligatori per la nuova architettura.

## Fonte storica

- audit code reference: `glaucogaribaldi/fondazionesemplice@755e0ba81a4dce4eb86101d4b19821ca45934ad2`
- ultimo commit storico osservato al bootstrap Fondazione2: `a0633cb737e25fb29897b05e6b7cfc1965c5d373`
- verdetto storico: `PAPER_INVALID`

I risultati economici prodotti dalla vecchia Arena non sono baseline scientifiche Fondazione2.

## HST-01 - Protection orders must execute

Derivato da FND-01.

Acceptance:

- OPEN persiste stop/take/time-exit;
- market update che attraversa stop genera protective exit;
- market update che attraversa take-profit genera protective exit;
- protective exit aggiorna fill, cash, position, realized PnL, fees e audit;
- restart non perde le protezioni attive.

## HST-02 - No portfolio TOCTOU

Derivato da FND-02.

Acceptance:

- due segnali concorrenti sulla stessa strategy/portfolio non possono superare max positions, max exposure o cash disponibile;
- reservations e final risk check sono atomici;
- retry identico non duplica l'ordine.

## HST-03 - Exit is never blocked by entry cooldown

Derivato da FND-03.

Acceptance:

- `CLOSE`, `REDUCE` e protective exits possono essere eseguiti immediatamente dopo `OPEN`;
- cooldown può impedire solo nuova/incrementale esposizione secondo policy.

## HST-04 - Position sizing semantics

Derivato da FND-04.

Acceptance:

- `REDUCE 50%` lascia esattamente il 50% della quantità precedente salvo precision rounding documentato;
- `CLOSE` porta la quantità a zero;
- sizing buy/open e sizing reduce non condividono una semantica ambigua `allocation_pct`.

## HST-05 - Fresh multi-asset mark-to-market

Derivato da FND-05.

Acceptance:

- equity usa un price registry coerente per tutte le posizioni;
- ogni mark include timestamp;
- prezzo oltre freshness limit produce stato STALE e policy fail-closed/risk-off esplicita;
- nessuna equity viene dichiarata corrente usando silenziosamente un mark scaduto.

## HST-06 - Net metrics do not double count fees

Derivato da FND-06.

Acceptance:

- realized/unrealized/net equity includono fee una sola volta;
- eventuale turnover penalty è una metrica distinta, non nascosta nel net return;
- metriche possono essere ricostruite dagli eventi del ledger.

## HST-07 - Configuration is executable truth

Derivato da FND-07.

Acceptance:

- ogni config risk dichiarata viene validata e consumata oppure rifiutata come campo sconosciuto;
- nessun campo safety può essere documentato ma ignorato silenziosamente;
- test cambiano una policy e provano che il runtime cambia coerentemente.

## HST-08 - Test isolation

Le probe/smoke sintetiche non devono mai mutare il ledger paper-forward.

Acceptance:

- database/namespace/test account separato;
- una smoke execution sintetica non modifica equity, drawdown, fills o ranking paper;
- CI non usa credenziali live.

## HST-09 - Restart and reconciliation

Acceptance:

- worker restart non duplica intent/orders;
- ordini pending vengono riconciliati;
- fencing/lease impedisce due owner simultanei dello stesso runtime;
- un client_order_id stabile impedisce replay duplicati.

## HST-10 - Model failure is fail-safe

Acceptance:

- timeout Kronos non produce nuova esposizione non autorizzata;
- output Nemotron invalido non produce nuova esposizione;
- QuantDinger strategy exception non produce ordine;
- protective exits già persistiti restano operative anche se i modelli AI sono indisponibili.

## HST-11 - Paper/live semantic parity

Acceptance:

Lo stesso `ExecutionIntent` deve poter essere inviato a PaperExecutor e CoinbaseLiveExecutor ottenendo differenze solo legate al broker/fill model, non alla semantica dell'azione.

## HST-12 - Paper certification gate

Il Paper Engine può assumere stato `CERTIFIED_FOR_FORWARD_TEST` soltanto dopo il passaggio di HST-01..HST-11 più i test Coinbase adapter applicabili.
