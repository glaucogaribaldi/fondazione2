import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from prometheus_client import Counter, make_asgi_app

from .bootstrap import bootstrap_probe_proposal, should_run_bootstrap_probe
from .clients import get_ai_proposal, get_forecast, quant_proposal
from .config import load_bootstrap_probe, load_lane_settings, load_risk_settings
from .models import DecisionRequest, DecisionResponse
from .risk import evaluate_risk


app = FastAPI(title="Fondazione Decision Service", version="0.1.0")
app.mount("/metrics", make_asgi_app())
DECISIONS = Counter("foundation_decisions_total", "Decisions", ["lane", "action", "approved"])
REASONS = Counter("foundation_decision_reasons_total", "Decision reasons", ["lane", "reason"])


def authorize(x_api_key: Annotated[str, Header()] = "") -> None:
    expected = os.getenv("DECISION_API_KEY", "")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "trading_mode": os.getenv("TRADING_MODE", "paper"),
        "live_enabled": os.getenv("LIVE_ENABLED", "false").lower() == "true",
    }


@app.post("/v1/decision", response_model=DecisionResponse, dependencies=[Depends(authorize)])
async def decide(request: DecisionRequest) -> DecisionResponse:
    if request.mode != os.getenv("TRADING_MODE", "paper"):
        raise HTTPException(status_code=409, detail="request mode differs from server mode")
    try:
        lane, lane_settings = load_lane_settings(request.lane_id)
        bootstrap_probe = load_bootstrap_probe()
        if should_run_bootstrap_probe(request, bootstrap_probe):
            proposal = bootstrap_probe_proposal(bootstrap_probe)
            model_versions = {
                "forecast": "not-used",
                "decision": "deterministic-bootstrap-probe",
            }
        else:
            forecast = await get_forecast(request)
            proposal = (
                await get_ai_proposal(request, forecast)
                if lane["ai_enabled"]
                else quant_proposal(forecast)
            )
            model_versions = {
                "forecast": forecast.model,
                "decision": os.getenv("NEMOTRON_MODEL", "deterministic-quant"),
            }
        result = evaluate_risk(
            request,
            proposal,
            load_risk_settings(),
            lane_settings,
            now=datetime.now(UTC),
            live_enabled=os.getenv("LIVE_ENABLED", "false").lower() == "true",
            live_confirmation=os.getenv("LIVE_CONFIRMATION", ""),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown lane: {request.lane_id}") from exc
    except Exception as exc:
        DECISIONS.labels(request.lane_id, "HOLD", "false").inc()
        REASONS.labels(request.lane_id, "FAIL_CLOSED").inc()
        REASONS.labels(request.lane_id, type(exc).__name__.upper()).inc()
        return DecisionResponse(
            request_id=request.request_id,
            lane_id=request.lane_id,
            symbol=request.symbol,
            decision="HOLD",
            allocation_pct=0,
            confidence=0,
            stop_loss_pct=None,
            take_profit_pct=None,
            valid_until=datetime.now(UTC),
            approved_by_risk_engine=False,
            reason_codes=["FAIL_CLOSED", type(exc).__name__.upper()],
            model_versions={"forecast": "unavailable", "decision": "unavailable"},
        )

    DECISIONS.labels(request.lane_id, result.action, str(result.approved).lower()).inc()
    for reason in result.reasons:
        REASONS.labels(request.lane_id, reason).inc()
    return DecisionResponse(
        request_id=request.request_id,
        lane_id=request.lane_id,
        symbol=request.symbol,
        decision=result.action,
        allocation_pct=result.allocation_pct,
        confidence=proposal.confidence,
        stop_loss_pct=proposal.stop_loss_pct if result.approved else None,
        take_profit_pct=proposal.take_profit_pct if result.approved else None,
        valid_until=result.valid_until,
        approved_by_risk_engine=result.approved,
        reason_codes=list(result.reasons),
        model_versions=model_versions,
    )
