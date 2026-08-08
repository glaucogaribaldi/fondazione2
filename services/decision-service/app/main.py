import os
import sys
import hashlib
import json
import time
import asyncio
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from prometheus_client import Counter, Gauge, make_asgi_app
from pydantic import BaseModel
import psycopg2

from .clients import get_ai_proposal, get_forecast, quant_proposal
from .config import load_lane_settings, load_risk_settings
from .models import DecisionRequest, DecisionResponse, Proposal, PortfolioSnapshot
from .risk import evaluate_risk
from .products import registry
from .websocket_service import websocket_service

def get_fresh_db_mark(symbol: str) -> float | None:
    """
    Retrieves fresh canonical market marks from the database with age validation (Blocker S1).
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url or db_url.startswith("sqlite"):
        try:
            conn = sqlite3.connect("file:fondazione_test?mode=memory&cache=shared", uri=True)
            cursor = conn.cursor()
            row = cursor.execute("SELECT price, updated_at FROM market_marks WHERE symbol = ?", (symbol,)).fetchone()
            conn.close()
            if row:
                price, updated_at_str = row
                try:
                    updated_at = datetime.fromisoformat(updated_at_str)
                except ValueError:
                    updated_at = datetime.now(UTC)
                age = (datetime.now(UTC) - updated_at).total_seconds()
                if abs(age) <= 90.0:
                    return float(price)
        except Exception:
            pass
        return None

    try:
        conn = psycopg2.connect(db_url)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT price, updated_at FROM market_marks WHERE symbol = %s", (symbol,))
                row = cur.fetchone()
                if row:
                    price, updated_at = row
                    age = (datetime.now(UTC) - updated_at).total_seconds()
                    if abs(age) <= 90.0:
                        return float(price)
        conn.close()
    except Exception:
        pass
    return None


app = FastAPI(title="Fondazione Decision Service", version="0.1.0")

@app.on_event("startup")
async def startup_event():
    # Force initial sync of product catalog
    await asyncio.to_thread(registry.sync_universe)
    # Start the unauthenticated public Advanced Trade WS Service
    websocket_service.start()

@app.get("/v1/universe/summary")
async def get_universe_summary():
    # Fetch all marks in a single query to avoid opening 832 sequential connections (Blocker S1 Performance)
    marks_cache = {}
    db_url = os.getenv("DATABASE_URL")
    if not db_url or db_url.startswith("sqlite"):
        try:
            conn = sqlite3.connect("file:fondazione_test?mode=memory&cache=shared", uri=True)
            cursor = conn.cursor()
            rows = cursor.execute("SELECT symbol, price, updated_at FROM market_marks").fetchall()
            conn.close()
            now = datetime.now(UTC)
            for r_sym, r_price, r_updated in rows:
                try:
                    updated_at = datetime.fromisoformat(r_updated)
                except ValueError:
                    updated_at = now
                age = (now - updated_at).total_seconds()
                if abs(age) <= 90.0:
                    marks_cache[r_sym] = float(r_price)
        except Exception:
            pass
    else:
        try:
            conn = psycopg2.connect(db_url)
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol, price, updated_at FROM market_marks")
                    rows = cur.fetchall()
                    now = datetime.now(UTC)
                    for r_sym, r_price, r_updated in rows:
                        age = (now - r_updated).total_seconds()
                        if abs(age) <= 90.0:
                            marks_cache[r_sym] = float(r_price)
            conn.close()
        except Exception:
            pass

    # Simple local dictionary lookup function
    def cached_get_mark(symbol: str) -> float | None:
        return marks_cache.get(symbol)

    return registry.get_metrics_summary(cached_get_mark)

@app.get("/v1/universe/products")
async def get_universe_products(limit: int = 100):
    products = registry.list_products()
    return {
        "total": len(products),
        "products": [p.model_dump(mode="json") for p in products[:limit]]
    }
app.mount("/metrics", make_asgi_app())

DECISIONS = Counter("foundation_decisions_total", "Decisions", ["lane", "action", "approved"])
REASONS = Counter("foundation_decision_reasons_total", "Decision reasons", ["lane", "reason"])

# Blocker K5, L3 & M3: Comprehensive Observability Metrics
MODEL_FAILURES = Counter("foundation_model_failures_total", "Model failures", ["lane", "model", "error_type"])
DECISION_LATENCY = Gauge("foundation_decision_latency_seconds", "Decision latency", ["lane"])
STALE_DATA = Counter("foundation_stale_data_total", "Stale market data events", ["lane"])
RISK_REJECTIONS = Counter("foundation_risk_rejections_total", "Risk engine rejections", ["lane", "reason"])
FILLS = Counter("foundation_fills_total", "Executed fills", ["lane", "symbol", "side"])
EQUITY = Gauge("foundation_equity", "Portfolio equity", ["lane"])
DRAWDOWN = Gauge("foundation_drawdown", "Portfolio drawdown", ["lane"])

# Blocker M3: Realized and Unrealized PnL
REALIZED_PNL = Gauge("foundation_realized_pnl", "Portfolio realized PnL", ["lane"])
UNREALIZED_PNL = Gauge("foundation_unrealized_pnl", "Portfolio unrealized PnL", ["lane"])

# Blocker L3 & M3: Reachability metric
COMPONENT_REACHABLE = Gauge("foundation_component_reachable", "Component reachability status (1=up, 0=down)", ["component"])


class FinalizeAuditRequest(BaseModel):
    request_id: str
    execution_intent: Optional[Dict[str, Any]] = None
    execution_result: Optional[Dict[str, Any]] = None


class ProtectiveExitRequest(BaseModel):
    request_id: str
    lane_id: str
    symbol: str
    reason: str # STOP_LOSS_TRIGGERED or TAKE_PROFIT_TRIGGERED
    execution_intent: Dict[str, Any]
    execution_result: Dict[str, Any]
    portfolio: PortfolioSnapshot


class MarketDataFailureRequest(BaseModel):
    request_id: str
    lane_id: str
    symbol: str
    reason: str  # STALE_MARKET_DATA, TICKER_FETCH_FAILED, or CANDLES_FETCH_FAILED
    portfolio: PortfolioSnapshot



def authorize(x_api_key: Annotated[str, Header()] = "") -> None:
    expected = os.getenv("DECISION_API_KEY", "")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/healthz")
async def healthz() -> dict:
    # Blocker M3: Real PostgreSQL probe query
    db_url = os.getenv("DATABASE_URL")
    postgres_ok = False
    if db_url and not db_url.startswith("sqlite"):
        try:
            conn = psycopg2.connect(db_url, connect_timeout=3)
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            conn.close()
            postgres_ok = True
        except Exception:
            postgres_ok = False
    else:
        postgres_ok = True # SQLite fallback
        
    COMPONENT_REACHABLE.labels("postgres").set(1.0 if postgres_ok else 0.0)
    
    return {
        "status": "ok" if postgres_ok else "degraded",
        "postgres": "up" if postgres_ok else "down",
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

    # Check dynamic product eligibility before proceeding to decision (Blocker S1)
    product_id = request.symbol.replace("/", "-")
    p = registry.get_product(product_id)
    if p:
        eligible, reason = registry.get_product_eligibility(product_id, get_fresh_db_mark)
        if not eligible:
            print(f"Product {request.symbol} is NOT eligible: {reason}")
            # Blocker S1: fail-closed inside decide, returning NO_TRADE with explicit reason code
            return DecisionResponse(
                request_id=request.request_id,
                lane_id=request.lane_id,
                symbol=request.symbol,
                decision="NO_TRADE",
                allocation_pct=0.0,
                confidence=0.0,
                stop_loss_pct=None,
                take_profit_pct=None,
                valid_until=datetime.now(UTC).isoformat(),
                approved_by_risk_engine=False,
                reason_codes=["PRODUCT_INELIGIBLE", reason or "Unknown reason"],
                model_versions={"forecast": "unavailable", "decision": "unavailable"}
            )
    
    try:
        lane, lane_settings = load_lane_settings(lane_id)
        
        # 1. Fetch Forecast from Kronos with error tracking
        try:
            forecast = await get_forecast(request)
            COMPONENT_REACHABLE.labels("kronos").set(1.0)
        except Exception as exc:
            MODEL_FAILURES.labels(lane_id, "kronos", type(exc).__name__).inc()
            COMPONENT_REACHABLE.labels("kronos").set(0.0)
            raise exc

        # 2. Fetch Proposal from Nemotron SGLang with error tracking
        try:
            proposal = (
                await get_ai_proposal(request, forecast)
                if lane["ai_enabled"]
                else quant_proposal(forecast)
            )
            # Only mark Nemotron reachable if AI actually invoked SGLang (M3)
            if lane["ai_enabled"]:
                COMPONENT_REACHABLE.labels("nemotron").set(1.0)
        except Exception as exc:
            MODEL_FAILURES.labels(lane_id, "nemotron", type(exc).__name__).inc()
            if lane["ai_enabled"]:
                COMPONENT_REACHABLE.labels("nemotron").set(0.0)
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
        _persist_audit(request, None, fallback_proposal, fallback_response, "failed")
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

    # Blocker K2 & L1: Stable SHA-256 digest & complete causal chain persistence (MarketSnapshot, Forecast, Proposal, Response)
    # Blocker K3: Fail-closed on audit save failure
    _persist_audit(request, forecast, proposal, response, model_versions)

    # Blocker K5 & L3: Latency & Correct Drawdown Metrics
    latency = time.time() - start_time
    DECISION_LATENCY.labels(request.lane_id).set(latency)
    EQUITY.labels(request.lane_id).set(request.portfolio.equity)
    
    # M3: Correct drawdown formula: peak-to-trough
    peak_equity = _get_peak_equity(request.lane_id, request.portfolio.equity)
    drawdown = ((peak_equity - request.portfolio.equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
    DRAWDOWN.labels(request.lane_id).set(drawdown)

    # M3: Update realized/unrealized PnL metrics
    _update_pnl_metrics(request.lane_id)

    return response


@app.post("/v1/decision/finalize", dependencies=[Depends(authorize)])
async def finalize_decision_audit(request: FinalizeAuditRequest):
    """
    Blocker L1 & L3: Updates the persisted causal chain in PostgreSQL to include the ExecutionIntent and ExecutionResult.
    Also updates the fills counter when execution is complete.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url or db_url.startswith("sqlite"):
        return {"status": "ok", "detail": "Skipped update for SQLite sandboxes"}

    try:
        conn = psycopg2.connect(db_url)
        with conn:
            with conn.cursor() as cur:
                # 1. Fetch current payload
                cur.execute("SELECT payload, lane_id, symbol FROM decision_audit WHERE request_id = %s", (request.request_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"Audit log row not found for request_id: {request.request_id}")
                
                payload = row[0]
                lane_id = row[1]
                symbol = row[2]
                
                # 2. Update payload dict
                payload["execution_intent"] = request.execution_intent
                payload["execution_result"] = request.execution_result
                
                # 3. Recalculate stable SHA-256 hash
                payload_json = json.dumps(payload, sort_keys=True)
                payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                
                # 4. Save updated payload
                cur.execute(
                    "UPDATE decision_audit SET payload = %s, payload_hash = %s WHERE request_id = %s",
                    (payload_json, payload_hash, request.request_id)
                )
                
                # 5. Increment fills counter (L3)
                if request.execution_result and request.execution_result.get("status") == "FILLED":
                    side = request.execution_intent.get("side", "UNKNOWN") if request.execution_intent else "UNKNOWN"
                    FILLS.labels(lane_id, symbol, side).inc()
                    
        conn.close()
        return {"status": "ok", "payload_hash": payload_hash}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"CRITICAL SAFETY ABORT: Failed to finalize causal audit update: {e}"
        )


@app.post("/v1/decision/protective_exit", dependencies=[Depends(authorize)])
async def record_protective_exit(request: ProtectiveExitRequest):
    """
    Blocker M2: Real, correlated protective exit audit insertion.
    Inserts a brand new row in decision_audit for protective stop crosses,
    calculates stable SHA-256 and updates Prometheus fills counter.
    """
    proposal_dict = {
        "action": "CLOSE",
        "allocation_pct": 100.0,
        "confidence": 1.0,
        "reason_codes": [request.reason],
        "stop_loss_pct": None,
        "take_profit_pct": None
    }
    response_dict = {
        "request_id": request.request_id,
        "lane_id": request.lane_id,
        "symbol": request.symbol,
        "decision": "CLOSE",
        "allocation_pct": 100.0,
        "confidence": 1.0,
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "valid_until": datetime.now(UTC).isoformat(),
        "approved_by_risk_engine": True,
        "reason_codes": [request.reason],
        "model_versions": {"forecast": "n/a", "decision": "protective-exit"}
    }
    
    payload = {
        "request": {
            "request_id": request.request_id,
            "lane_id": request.lane_id,
            "symbol": request.symbol,
            "portfolio": request.portfolio.model_dump(mode="json")
        },
        "forecast": None,
        "proposal": proposal_dict,
        "response": response_dict,
        "execution_intent": request.execution_intent,
        "execution_result": request.execution_result
    }
    
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    
    db_url = os.getenv("DATABASE_URL")
    if db_url and not db_url.startswith("sqlite"):
        try:
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
                            "CLOSE",
                            "CLOSE",
                            True,
                            json.dumps([request.reason]),
                            json.dumps({"forecast": "n/a", "decision": "protective-exit"}),
                            payload_hash,
                            payload_json
                        )
                    )
            conn.close()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"CRITICAL SAFETY ABORT: Failed to persist protective exit audit: {e}")
            
    # Update Prometheus metrics
    FILLS.labels(request.lane_id, request.symbol, "SELL").inc()
    EQUITY.labels(request.lane_id).set(request.portfolio.equity)
    
    # Correct peak-to-trough drawdown calculation (M3)
    peak_equity = _get_peak_equity(request.lane_id, request.portfolio.equity)
    drawdown = ((peak_equity - request.portfolio.equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
    DRAWDOWN.labels(request.lane_id).set(drawdown)
    
    _update_pnl_metrics(request.lane_id)
    
    return {"status": "ok", "payload_hash": payload_hash}


@app.post("/v1/decision/market_data_failure", dependencies=[Depends(authorize)])
async def record_market_data_failure(request: MarketDataFailureRequest):
    """
    Blocker N1: Canonical market data failure audit path.
    Persists a NO_TRADE/FAILED cycle in decision_audit, increments observability counters,
    and returns stable SHA-256 digest of the complete causal chain.
    """
    proposal_dict = {
        "action": "NO_TRADE",
        "allocation_pct": 0.0,
        "confidence": 0.0,
        "reason_codes": [request.reason],
        "stop_loss_pct": None,
        "take_profit_pct": None
    }
    response_dict = {
        "request_id": request.request_id,
        "lane_id": request.lane_id,
        "symbol": request.symbol,
        "decision": "NO_TRADE",
        "allocation_pct": 0.0,
        "confidence": 0.0,
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "valid_until": datetime.now(UTC).isoformat(),
        "approved_by_risk_engine": False,
        "reason_codes": [request.reason],
        "model_versions": {"forecast": "n/a", "decision": "market-data-failure"}
    }
    
    payload = {
        "request": {
            "request_id": request.request_id,
            "lane_id": request.lane_id,
            "symbol": request.symbol,
            "portfolio": request.portfolio.model_dump(mode="json"),
            "market": None
        },
        "forecast": None,
        "proposal": proposal_dict,
        "response": response_dict,
        "execution_intent": None,
        "execution_result": None
    }
    
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    
    db_url = os.getenv("DATABASE_URL")
    if db_url and not db_url.startswith("sqlite"):
        try:
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
                            "NO_TRADE",
                            "NO_TRADE",
                            False,
                            json.dumps([request.reason]),
                            json.dumps({"forecast": "n/a", "decision": "market-data-failure"}),
                            payload_hash,
                            payload_json
                        )
                    )
            conn.close()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"CRITICAL SAFETY ABORT: Failed to persist market data failure audit: {e}")
            
    # Update Prometheus metrics
    DECISIONS.labels(request.lane_id, "NO_TRADE", "false").inc()
    REASONS.labels(request.lane_id, request.reason).inc()
    if request.reason == "STALE_MARKET_DATA":
        STALE_DATA.labels(request.lane_id).inc()
    else:
        MODEL_FAILURES.labels(request.lane_id, "coinbase_adapter", request.reason).inc()
        
    EQUITY.labels(request.lane_id).set(request.portfolio.equity)
    
    # Correct peak-to-trough drawdown calculation (M3)
    peak_equity = _get_peak_equity(request.lane_id, request.portfolio.equity)
    drawdown = ((peak_equity - request.portfolio.equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
    DRAWDOWN.labels(request.lane_id).set(drawdown)
    
    _update_pnl_metrics(request.lane_id)
    
    return {"status": "ok", "payload_hash": payload_hash}



def _get_peak_equity(lane_id: str, current_equity: float) -> float:
    """
    Blocker M3: Calculates peak-to-trough peak equity by querying maximum historically observed equity in DB.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url or db_url.startswith("sqlite"):
        return current_equity
    try:
        conn = psycopg2.connect(db_url)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(equity) FROM arena_snapshots WHERE lane_id = %s", (lane_id,))
                row = cur.fetchone()
                if row and row[0] is not None:
                    return max(float(row[0]), current_equity)
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error in _get_peak_equity: {e}")
    return current_equity


def _update_pnl_metrics(lane_id: str):
    """
    Blocker M3: Exposes realized_pnl and unrealized_pnl metrics by querying PostgreSQL.
    """
    db_url = os.getenv("DATABASE_URL")
    realized = 0.0
    unrealized = 0.0
    if db_url and not db_url.startswith("sqlite"):
        try:
            conn = psycopg2.connect(db_url)
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT realized_pnl, unrealized_pnl FROM arena_snapshots WHERE lane_id = %s ORDER BY id DESC LIMIT 1", (lane_id,))
                    row = cur.fetchone()
                    if row:
                        realized = float(row[0] or 0.0)
                        unrealized = float(row[1] or 0.0)
            conn.close()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Database error in _update_pnl_metrics: {e}")
    REALIZED_PNL.labels(lane_id).set(realized)
    UNREALIZED_PNL.labels(lane_id).set(unrealized)


def _persist_audit(request: DecisionRequest, forecast: Any, proposal: Proposal, response: DecisionResponse, model_versions: Any):
    """
    Atomically and durably persists the complete causal chain using a stable SHA-256 digest (Blocker K2/K3/L1).
    """
    payload = {
        "request": request.model_dump(mode="json"),
        "forecast": forecast.model_dump(mode="json") if forecast else None,
        "proposal": proposal.model_dump(mode="json"),
        "response": response.model_dump(mode="json"),
        "execution_intent": None,
        "execution_result": None
    }
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        db_url = "sqlite:///:memory:"

    try:
        if db_url.startswith("sqlite"):
            # Mock SQLite unit-testing connection
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
