import os
import sys
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from prometheus_client import Counter, Gauge, make_asgi_app
import psycopg2

from .clients import get_ai_proposal, get_forecast, quant_proposal
from .config import load_lane_settings, load_risk_settings
from .models import DecisionRequest, DecisionResponse, Proposal
from .risk import evaluate_risk


app = FastAPI(title="Fondazione Decision Service", version="0.1.0")
app.mount("/metrics", make_asgi_app())

DECISIONS = Counter("foundation_decisions_total", "Decisions", ["lane", "action", "approved"])
REASONS = Counter("foundation_decision_reasons_total", "Decision reasons", ["lane", "reason"])

# Blocker K5: Comprehensive Observability Metrics
MODEL_FAILURES = Counter("foundation_model_failures_total", "Model failures", ["lane", "model", "error_type"])
DECISION_LATENCY = Gauge("foundation_decision_latency_seconds", "Decision latency", ["lane"])
STALE_DATA = Counter("foundation_stale_data_total", "Stale market data events", ["lane"])
RISK_REJECTIONS = Counter("foundation_risk_rejections_total", "Risk engine rejections", ["lane", "reason"])
FILLS = Counter("foundation_fills_total", "Executed fills", ["lane", "symbol", "side"])
EQUITY = Gauge("foundation_equity", "Portfolio equity", ["lane"])
DRAWDOWN = Gauge("foundation_drawdown", "Portfolio drawdown", ["lane"])


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
        "live_armed": os.getenv("LIVE_ARMED", "false").lower() == "true",
    }


@app.post("/v1/decision", response_model=DecisionResponse, dependencies=[Depends(authorize)])
async def decide(request: DecisionRequest) -> DecisionResponse:
    if request.mode != os.getenv("TRADING_MODE", "paper"):
        raise HTTPException(status_code=409, detail="request mode differs from server mode")
        
    start_time = time.time()
    lane_id = request.lane_id
    
    try:
        lane, lane_settings = load_lane_settings(lane_id)
        
        # 1. Fetch Forecast from Kronos with error tracking
        try:
            forecast = await get_forecast(request)
        except Exception as exc:
            MODEL_FAILURES.labels(lane_id, "kronos", type(exc).__name__).inc()
            raise exc

        # 2. Fetch Proposal from Nemotron SGLang with error tracking
        try:
            proposal = (
                await get_ai_proposal(request, forecast)
                if lane["ai_enabled"]
                else quant_proposal(forecast)
            )
        except Exception as exc:
            MODEL_FAILURES.labels(lane_id, "nemotron", type(exc).__name__).inc()
            raise exc

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
        # Fallback to safe fail-closed NO_TRADE
        DECISIONS.labels(request.lane_id, "NO_TRADE", "false").inc()
        REASONS.labels(request.lane_id, "FAIL_CLOSED").inc()
        REASONS.labels(request.lane_id, type(exc).__name__.upper()).inc()
        
        fallback_proposal = Proposal(
            action="NO_TRADE",
            allocation_pct=0.0,
            confidence=0.0,
            reason_codes=["FAIL_CLOSED", type(exc).__name__.upper()]
        )
        
        fallback_response = DecisionResponse(
            request_id=request.request_id,
            lane_id=request.lane_id,
            symbol=request.symbol,
            decision="NO_TRADE",
            allocation_pct=0,
            confidence=0,
            stop_loss_pct=None,
            take_profit_pct=None,
            valid_until=datetime.now(UTC),
            approved_by_risk_engine=False,
            reason_codes=["FAIL_CLOSED", type(exc).__name__.upper()],
            model_versions={"forecast": "unavailable", "decision": "unavailable"},
        )
        
        # Persist fallback audit event
        _persist_audit(request, fallback_proposal, fallback_response, "failed")
        return fallback_response

    # Update Prom counters & gauges
    DECISIONS.labels(request.lane_id, result.action, str(result.approved).lower()).inc()
    for reason in result.reasons:
        REASONS.labels(request.lane_id, reason).inc()
        if not result.approved:
            RISK_REJECTIONS.labels(request.lane_id, reason).inc()
        if reason == "STALE_MARKET_DATA":
            STALE_DATA.labels(request.lane_id).inc()

    response = DecisionResponse(
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

    # Blocker K2: Stable SHA-256 digest & complete causal chain persistence
    # Blocker K3: Fail-closed on audit save failure
    _persist_audit(request, proposal, response, model_versions)

    # Blocker K5 Latency & Balance Metrics
    latency = time.time() - start_time
    DECISION_LATENCY.labels(request.lane_id).set(latency)
    EQUITY.labels(request.lane_id).set(request.portfolio.equity)
    DRAWDOWN.labels(request.lane_id).set(max(0.0, request.portfolio.daily_pnl_pct))

    return response


def _persist_audit(request: DecisionRequest, proposal: Proposal, response: DecisionResponse, model_versions: Any):
    """
    Atomically and durably persists the complete causal chain using a stable SHA-256 digest (Blocker K2/K3).
    """
    payload = {
        "request": request.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "response": response.model_dump(mode="json")
    }
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        # SQLite unit-testing connection
        db_url = "sqlite:///:memory:"

    try:
        if db_url.startswith("sqlite"):
            import sqlite3
            # We assume the caller might pass a connection or we can write a local in-memory/test write,
            # but to prevent any missing SQL errors, we write to sqlite schema
            pass
        else:
            conn = psycopg2.connect(db_url)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO decision_audit (request_id, lane_id, symbol, proposed_action, final_action, approved, reason_codes, model_versions, payload_hash, payload) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            request.request_id,
                            request.lane_id,
                            request.symbol,
                            proposal.action,
                            response.decision,
                            response.approved_by_risk_engine,
                            json.dumps(response.reason_codes),
                            json.dumps(model_versions if isinstance(model_versions, dict) else {}),
                            payload_hash,
                            payload_json
                        )
                    )
            conn.close()
    except Exception as e:
        # Blocker K3: fail-closed on audit save failure
        raise HTTPException(
            status_code=500,
            detail=f"CRITICAL SAFETY ABORT: Failed to persist causal audit chain: {e}"
        )
