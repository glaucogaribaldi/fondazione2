# Fondazione2 Decision Contract v0

Status: `DRAFT_CONTRACT`

Lo scopo è impedire che QuantDinger, Kronos, Nemotron, Risk Engine e gli executor usino semantiche implicite differenti.

## 1. StrategyIntent

Prodotto da una strategia QuantDinger o da una strategia Fondazione2 compatibile.

Campi minimi:

```json
{
  "intent_id": "uuid",
  "strategy_id": "string",
  "strategy_version": "git-or-content-hash",
  "timestamp": "RFC3339",
  "symbol": "BTC/USDC",
  "timeframe": "1h",
  "action": "NO_TRADE|OPEN|ADD|REDUCE|CLOSE",
  "direction": "LONG|FLAT",
  "requested_risk_fraction": 0.0,
  "requested_position_fraction": 0.0,
  "stop": null,
  "take_profit": null,
  "time_exit": null,
  "signal_features": {},
  "reason_codes": []
}
```

`requested_risk_fraction` e `requested_position_fraction` non sono intercambiabili.

## 2. KronosForecast

```json
{
  "forecast_id": "uuid",
  "model_id": "string",
  "model_version": "string",
  "input_timestamp": "RFC3339",
  "generated_at": "RFC3339",
  "symbol": "BTC/USDC",
  "timeframe": "1h",
  "horizon_steps": 12,
  "trajectory": [],
  "expected_return": 0.0,
  "forecast_volatility": 0.0,
  "uncertainty": 0.0,
  "metadata": {}
}
```

La `uncertainty` deve avere una definizione documentata; non chiamarla probabilità se non è calibrata come probabilità.

## 3. NemotronPolicy

```json
{
  "policy_id": "uuid",
  "model_id": "string",
  "model_version": "string",
  "generated_at": "RFC3339",
  "recommendation": "NO_TRADE|OPEN|ADD|REDUCE|CLOSE",
  "confidence": 0.0,
  "risk_modifier": 1.0,
  "invalidation_conditions": [],
  "reason_codes": [],
  "rationale": "short auditable explanation"
}
```

Nemotron può ridurre o criticare una proposta; non può aumentare una size oltre i limiti della strategia o del Risk Engine.

## 4. DecisionCandidate

Prodotto dal Decision Aggregator.

Deve contenere riferimenti immutabili a:

- market snapshot;
- strategy intent;
- Kronos forecast se usato;
- Nemotron policy se usata;
- portfolio snapshot;
- strategy config/version.

Campi minimi:

```json
{
  "candidate_id": "uuid",
  "mode": "paper|shadow|live",
  "symbol": "BTC/USDC",
  "action": "NO_TRADE|OPEN|ADD|REDUCE|CLOSE",
  "requested_quantity": null,
  "requested_risk_fraction": 0.0,
  "stop": null,
  "take_profit": null,
  "time_exit": null,
  "source_refs": {},
  "reason_codes": []
}
```

## 5. RiskDecision

Il Risk Engine restituisce uno di:

- `APPROVE`;
- `MODIFY_DOWN`;
- `REJECT`;
- `PROTECTIVE_EXIT`.

Non può modificare una proposta aumentando rischio o size.

```json
{
  "risk_decision_id": "uuid",
  "candidate_id": "uuid",
  "result": "APPROVE|MODIFY_DOWN|REJECT|PROTECTIVE_EXIT",
  "approved_action": "NO_TRADE|OPEN|ADD|REDUCE|CLOSE",
  "approved_quantity": 0.0,
  "limits_snapshot": {},
  "reason_codes": [],
  "expires_at": "RFC3339"
}
```

## 6. ExecutionIntent

Unico contratto consumato dagli executor paper/live.

```json
{
  "execution_intent_id": "uuid",
  "risk_decision_id": "uuid",
  "mode": "paper|live",
  "symbol": "BTC/USDC",
  "action": "OPEN|ADD|REDUCE|CLOSE",
  "side": "BUY|SELL",
  "quantity": 0.0,
  "order_type": "MARKET|LIMIT|STOP|STOP_LIMIT",
  "limit_price": null,
  "stop_price": null,
  "take_profit_price": null,
  "time_exit_at": null,
  "client_order_id": "stable-idempotency-key",
  "created_at": "RFC3339",
  "expires_at": "RFC3339"
}
```

## 7. ExecutionResult

Paper e live restituiscono una forma normalizzata:

```json
{
  "execution_intent_id": "uuid",
  "broker_order_id": "string-or-paper-id",
  "status": "PENDING|PARTIAL|FILLED|CANCELLED|REJECTED|FAILED",
  "requested_quantity": 0.0,
  "filled_quantity": 0.0,
  "average_fill_price": null,
  "fee": 0.0,
  "slippage": 0.0,
  "fills": [],
  "updated_at": "RFC3339",
  "reason_codes": []
}
```

## 8. Semantica delle azioni

- `NO_TRADE`: nessuna mutazione.
- `OPEN`: crea esposizione da posizione nulla.
- `ADD`: aumenta una posizione esistente.
- `REDUCE`: vende una frazione esplicita della quantità posseduta.
- `CLOSE`: chiude tutta la posizione residua.

Un `REDUCE 50%` significa esattamente 50% della quantità posseduta, mai 50% dell'equity.

## 9. Protective exits

Stop loss, take profit, time exit, kill switch e liquidazioni risk-driven generano `PROTECTIVE_EXIT` e non dipendono da una nuova decisione LLM.

I cooldown di ingresso non si applicano alle protective exits.

## 10. Atomicità

La verifica finale di capitale, esposizione, stato posizione e riserva deve avvenire in una transazione/critical section coerente immediatamente prima della creazione dell'ExecutionIntent.

## 11. Audit

Ogni oggetto deve essere persistibile e correlabile via ID. Nessun `reasoning` non strutturato è richiesto per autorizzare un ordine: il percorso autorizzativo deve essere riproducibile da campi strutturati e reason codes.
