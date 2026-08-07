# Fondazione2 Agent Governance

Questo file definisce i confini operativi degli agenti che lavorano su Fondazione2.

## Autorità

Ordine di autorità:

1. decisione esplicita dell'operatore umano;
2. `docs/SAFETY_CONTRACT.md`;
3. specifiche e task versionati in questa repository;
4. codice e test del commit in esecuzione;
5. report runtime con evidenze verificabili;
6. memoria dell'agente.

La memoria di un agente non può sovrascrivere Git, i test o lo stato runtime osservabile.

## Ruoli

### OpenClaw System Agent

Responsabile di:

- inventario host e VPS;
- installazione e aggiornamento servizi;
- Docker, GPU, rete privata e process supervision;
- PostgreSQL, Redis e storage;
- QuantDinger runtime;
- Kronos runtime;
- Nemotron/SGLang runtime;
- Coinbase connectivity e adapter;
- health, observability, deploy e rollback;
- raccolta di evidenze sanitizzate.

Non modifica autonomamente strategie o limiti economici.

### OpenClaw Strategy Agent

Responsabile del ciclo:

```text
strategy proposal
    -> branch/commit
    -> tests
    -> backtest
    -> validation
    -> paper candidate
    -> OpenClaw deploy paper
    -> forward observation
    -> recommendation: promote / revise / rollback
```

Può creare e modificare strategie, configurazioni e test entro il task assegnato. Non può promuovere autonomamente una strategia al live.

## Separazione dei privilegi

Kronos, Nemotron e QuantDinger non possiedono autorità finale sull'esecuzione.

- Kronos produce forecast strutturati.
- Nemotron produce critica/policy/proposte strutturate.
- QuantDinger produce strategy intents, backtest, paper runtime e intelligence.
- Fondazione2 normalizza tutto in un `DecisionCandidate`.
- Il Risk Engine deterministico produce un `ExecutionIntent` approvato o un rifiuto.
- Soltanto l'execution layer può simulare o inviare ordini.

## Git come control plane

Ogni modifica strutturale o strategica deve essere associata a:

- task o issue;
- branch;
- commit identificabile;
- test eseguiti;
- report di risultato.

Non modificare il checkout runtime manualmente senza riportare la modifica in Git.

## Stop conditions

Un agente deve fermarsi e dichiarare `BLOCKED` quando:

- il target host o repository è ambiguo;
- il commit richiesto non esiste;
- un test di sicurezza fallisce;
- sono richiesti segreti non disponibili;
- una modifica richiede di indebolire un controllo deterministico;
- lo stato live è ambiguo;
- una procedura distruttiva non ha il gate umano richiesto.
