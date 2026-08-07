import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, HTTPException

from .helpers import normalize_candles, product_to_symbol, timeframe_label


EXCHANGE_URL = "https://api.exchange.coinbase.com"
ARENA_URL = os.getenv("ARENA_URL", "http://arena:8082")
API_KEY = os.getenv("DECISION_API_KEY", "")
PRODUCTS = [item.strip() for item in os.getenv("COINBASE_PRODUCTS", "BTC-USDT").split(",")]
GRANULARITY = int(os.getenv("MARKET_TIMEFRAME_SECONDS", "300"))
POLL_SECONDS = int(os.getenv("MARKET_POLL_SECONDS", "30"))
CANDLE_LIMIT = max(32, min(int(os.getenv("MARKET_CANDLE_LIMIT", "96")), 300))
STATE: dict = {
    "status": "starting",
    "products": {},
    "last_success_at": None,
    "last_error": None,
}


async def process_product(client: httpx.AsyncClient, product: str) -> None:
    candles_response, ticker_response = await asyncio.gather(
        client.get(
            f"{EXCHANGE_URL}/products/{product}/candles",
            params={"granularity": GRANULARITY},
        ),
        client.get(f"{EXCHANGE_URL}/products/{product}/ticker"),
    )
    candles_response.raise_for_status()
    ticker_response.raise_for_status()
    now = int(datetime.now(UTC).timestamp())
    candles = normalize_candles(candles_response.json(), now, GRANULARITY, CANDLE_LIMIT)
    if len(candles) < 32:
        raise ValueError(f"only {len(candles)} closed candles for {product}")

    ticker = ticker_response.json()
    latest_timestamp = candles[-1]["timestamp"]
    request_id = f"coinbase-{product}-{latest_timestamp}"
    if STATE["products"].get(product, {}).get("request_id") == request_id:
        return
    payload = {
        "request_id": request_id,
        "mode": "paper",
        "symbol": product_to_symbol(product),
        "timeframe": timeframe_label(GRANULARITY),
        "market": {
            "timestamp": datetime.now(UTC).isoformat(),
            "bid": float(ticker["bid"]),
            "ask": float(ticker["ask"]),
            "candles": candles,
        },
    }
    response = await client.post(
        f"{ARENA_URL}/v1/evaluate",
        json=payload,
        headers={"X-API-Key": API_KEY},
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()
    STATE["products"][product] = {
        "request_id": request_id,
        "last_candle": latest_timestamp,
        "decisions": len(result["decisions"]),
    }


async def market_loop() -> None:
    headers = {"User-Agent": "fondazionesemplice/0.2"}
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        while True:
            try:
                results = await asyncio.gather(
                    *(process_product(client, product) for product in PRODUCTS),
                    return_exceptions=True,
                )
                errors = [str(result) for result in results if isinstance(result, Exception)]
                if len(errors) == len(PRODUCTS):
                    raise RuntimeError("; ".join(errors))
                STATE["status"] = "ok" if not errors else "degraded"
                STATE["last_success_at"] = datetime.now(UTC).isoformat()
                STATE["last_error"] = "; ".join(errors) or None
            except Exception as exc:
                STATE["status"] = "degraded"
                STATE["last_error"] = str(exc)
            await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(market_loop())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="Fondazione Coinbase Market Feed", version="0.2.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    if STATE["last_success_at"] is None:
        raise HTTPException(status_code=503, detail=STATE)
    return STATE
