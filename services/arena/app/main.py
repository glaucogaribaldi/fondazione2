import asyncio
import os
from pathlib import Path
from typing import Annotated

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from prometheus_client import Gauge, make_asgi_app

from .ledger import ExecutionSettings, PaperLedger


app = FastAPI(title="Fondazione Arena", version="0.2.0")
app.mount("/metrics", make_asgi_app())
EQUITY = Gauge("foundation_lane_equity", "Paper equity", ["lane"])
CASH = Gauge("foundation_lane_cash", "Paper cash", ["lane"])
RETURN = Gauge("foundation_lane_return_pct", "Paper return percent", ["lane"])
DRAWDOWN = Gauge(
    "foundation_lane_max_drawdown_pct", "Maximum paper drawdown percent", ["lane"]
)
FEES = Gauge("foundation_lane_fees", "Accumulated paper fees", ["lane"])
OPEN_POSITIONS = Gauge("foundation_lane_open_positions", "Open paper positions", ["lane"])
STRATEGY_INFO = Gauge(
    "foundation_lane_strategy_info",
    "Active strategy release",
    ["lane", "strategy", "ai_enabled", "release_id"],
)
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "310"))
DECISION_TIMEOUT = float(os.getenv("ARENA_DECISION_TIMEOUT_SECONDS", "90"))


def lane_ids() -> list[str]:
    with Path("/app/config/strategies.yml").open(encoding="utf-8") as handle:
        return list(yaml.safe_load(handle)["lanes"])


def strategy_metadata() -> tuple[dict, str]:
    with Path("/app/config/strategies.yml").open(encoding="utf-8") as handle:
        strategies = yaml.safe_load(handle)["lanes"]
    with Path("/app/config/release.yml").open(encoding="utf-8") as handle:
        release_id = yaml.safe_load(handle)["release_id"]
    return strategies, release_id


LANES = lane_ids()
LEDGER = PaperLedger(os.getenv("ARENA_DB_PATH", "/data/arena.db"), LANES, INITIAL_CAPITAL)
SMOKE_LEDGER = PaperLedger("/tmp/fondazione-smoke/arena.db", LANES, INITIAL_CAPITAL)
EXECUTION = ExecutionSettings(
    fee_bps=float(os.getenv("PAPER_FEE_BPS", "60")),
    slippage_bps=float(os.getenv("PAPER_SLIPPAGE_BPS", "5")),
)
STRATEGIES, RELEASE_ID = strategy_metadata()
for active_lane, metadata in STRATEGIES.items():
    STRATEGY_INFO.labels(
        active_lane,
        metadata["name"],
        str(metadata["ai_enabled"]).lower(),
        RELEASE_ID,
    ).set(1)


def record_metrics(rows: list[dict]) -> None:
    for row in rows:
        lane = row["lane_id"]
        EQUITY.labels(lane).set(row["equity"])
        CASH.labels(lane).set(row["cash"])
        RETURN.labels(lane).set(row["return_pct"])
        DRAWDOWN.labels(lane).set(row["max_drawdown_pct"])
        FEES.labels(lane).set(row["fees"])
        OPEN_POSITIONS.labels(lane).set(len(row["positions"]))


record_metrics(LEDGER.ranking())


def authorize(x_api_key: Annotated[str, Header()] = "") -> None:
    expected = os.getenv("DECISION_API_KEY", "")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "lanes": len(LANES), "persistent_ledger": True}


@app.get("/v1/ranking", dependencies=[Depends(authorize)])
async def ranking() -> list[dict]:
    result = LEDGER.ranking()
    record_metrics(result)
    return result


@app.get("/v1/events", dependencies=[Depends(authorize)])
async def events(limit: int = 100) -> list[dict]:
    return LEDGER.events(min(max(limit, 1), 1000))


async def evaluate_with_ledger(
    snapshot: dict, ledger: PaperLedger, *, publish_metrics: bool
) -> dict:
    required = {"request_id", "mode", "symbol", "timeframe", "market"}
    if missing := required - snapshot.keys():
        raise HTTPException(status_code=422, detail=f"missing fields: {sorted(missing)}")
    if snapshot["mode"] != "paper":
        raise HTTPException(status_code=409, detail="arena accepts paper mode only")

    bid = float(snapshot["market"]["bid"])
    ask = float(snapshot["market"]["ask"])
    mid = (bid + ask) / 2

    async def call_lane(client: httpx.AsyncClient, lane_id: str) -> dict:
        payload = dict(snapshot)
        payload["request_id"] = f"{snapshot['request_id']}-{lane_id}"
        payload["lane_id"] = lane_id
        payload["portfolio"] = ledger.snapshot(lane_id, snapshot["symbol"], mid)
        response = await client.post(
            f"{os.environ['DECISION_URL']}/v1/decision",
            json=payload,
            headers={"X-API-Key": os.environ["DECISION_API_KEY"]},
        )
        response.raise_for_status()
        return response.json()

    try:
        async with httpx.AsyncClient(timeout=DECISION_TIMEOUT) as client:
            decisions = await asyncio.gather(*(call_lane(client, lane) for lane in LANES))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="decision service unavailable") from exc

    executions = []
    for decision in decisions:
        execution = ledger.execute(
            snapshot["request_id"],
            decision["lane_id"],
            snapshot["symbol"],
            decision,
            bid,
            ask,
            EXECUTION,
        )
        executions.append(execution)
    result = ledger.ranking()
    if publish_metrics:
        record_metrics(result)
    return {
        "request_id": snapshot["request_id"],
        "decisions": decisions,
        "executions": executions,
        "ranking": result,
    }


@app.post("/v1/evaluate", dependencies=[Depends(authorize)])
async def evaluate(snapshot: dict) -> dict:
    return await evaluate_with_ledger(snapshot, LEDGER, publish_metrics=True)


@app.post("/v1/smoke-evaluate", dependencies=[Depends(authorize)])
async def smoke_evaluate(snapshot: dict) -> dict:
    return await evaluate_with_ledger(snapshot, SMOKE_LEDGER, publish_metrics=False)
