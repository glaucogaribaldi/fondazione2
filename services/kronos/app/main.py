import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .helpers import timeframe_delta


class Candle(BaseModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class ForecastRequest(BaseModel):
    symbol: str
    timeframe: str
    candles: list[Candle] = Field(min_length=32, max_length=512)


class ForecastResponse(BaseModel):
    direction: str
    expected_return_pct: float
    confidence: float
    volatility: float
    model: str


FORECAST_CACHE: dict[tuple, ForecastResponse] = {}
FORECAST_LOCKS: dict[tuple, asyncio.Lock] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("KRONOS_BACKEND") == "real":
        await asyncio.to_thread(load_predictor)
    yield


app = FastAPI(title="Fondazione Kronos Service", version="0.2.0", lifespan=lifespan)


def summarize(last_close: float, predicted: np.ndarray, model: str) -> ForecastResponse:
    predicted_return = float((predicted[-1] / last_close - 1) * 100)
    returns = np.diff(predicted) / predicted[:-1] if len(predicted) > 1 else np.array([0.0])
    volatility = float(np.std(returns))
    confidence = float(
        np.clip(abs(predicted_return) / (volatility * 10_000 + 0.25), 0.05, 0.95)
    )
    threshold = 0.05
    if predicted_return > threshold:
        direction = "up"
    elif predicted_return < -threshold:
        direction = "down"
    else:
        direction = "flat"
    return ForecastResponse(
        direction=direction,
        expected_return_pct=round(predicted_return, 6),
        confidence=round(confidence, 6),
        volatility=round(volatility, 8),
        model=model,
    )


def mock_forecast(request: ForecastRequest) -> ForecastResponse:
    closes = np.asarray([candle.close for candle in request.candles], dtype=float)
    short = float(np.mean(closes[-5:]))
    long = float(np.mean(closes[-20:]))
    momentum = (short / long - 1) if long else 0
    predicted = closes[-1] * np.asarray([1 + momentum * step / 12 for step in range(1, 13)])
    return summarize(closes[-1], predicted, "kronos-mock-momentum")


@lru_cache(maxsize=1)
def load_predictor():
    from model import Kronos, KronosPredictor, KronosTokenizer

    tokenizer = KronosTokenizer.from_pretrained(os.getenv("KRONOS_TOKENIZER"))
    model = Kronos.from_pretrained(os.getenv("KRONOS_MODEL"))
    return KronosPredictor(model, tokenizer, max_context=512)


def real_forecast(request: ForecastRequest) -> ForecastResponse:
    frame = pd.DataFrame([candle.model_dump() for candle in request.candles])
    x_timestamp = pd.to_datetime(frame.pop("timestamp"))
    delta = timeframe_delta(request.timeframe)
    horizon = 12
    y_timestamp = pd.Series(
        [x_timestamp.iloc[-1] + delta * step for step in range(1, horizon + 1)]
    )
    prediction = load_predictor().predict(
        df=frame[["open", "high", "low", "close", "volume"]],
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=horizon,
        T=1.0,
        top_p=0.9,
        sample_count=3,
        verbose=False,
    )
    return summarize(
        float(frame.close.iloc[-1]), prediction.close.to_numpy(), os.getenv("KRONOS_MODEL", "")
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "backend": os.getenv("KRONOS_BACKEND", "mock")}


@app.post("/v1/forecast", response_model=ForecastResponse)
async def forecast(request: ForecastRequest) -> ForecastResponse:
    last_candle = request.candles[-1]
    cache_key = (
        request.symbol,
        request.timeframe,
        last_candle.timestamp.isoformat(),
        last_candle.close,
    )
    lock = FORECAST_LOCKS.setdefault(cache_key, asyncio.Lock())
    try:
        async with lock:
            if cached := FORECAST_CACHE.get(cache_key):
                return cached
            result = await asyncio.to_thread(
                real_forecast if os.getenv("KRONOS_BACKEND") == "real" else mock_forecast,
                request,
            )
            FORECAST_CACHE[cache_key] = result
            if len(FORECAST_CACHE) > 32:
                FORECAST_CACHE.pop(next(iter(FORECAST_CACHE)))
            return result
    except Exception as exc:
        detail = f"forecast unavailable: {type(exc).__name__}"
        raise HTTPException(status_code=503, detail=detail) from exc
    finally:
        if not lock.locked():
            FORECAST_LOCKS.pop(cache_key, None)
