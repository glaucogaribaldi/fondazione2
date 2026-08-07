import json
import os

import httpx

from .models import DecisionRequest, Forecast, Proposal


TIMEOUT = float(os.getenv("DECISION_TIMEOUT_SECONDS", "20"))


async def get_forecast(request: DecisionRequest) -> Forecast:
    payload = {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "candles": [candle.model_dump(mode="json") for candle in request.market.candles],
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(f"{os.environ['KRONOS_URL']}/v1/forecast", json=payload)
        response.raise_for_status()
    return Forecast.model_validate(response.json())


def quant_proposal(forecast: Forecast) -> Proposal:
    if forecast.confidence < 0.65 or abs(forecast.expected_return_pct) < 0.10:
        return Proposal(action="HOLD", allocation_pct=0, confidence=forecast.confidence)
    if forecast.direction == "up":
        return Proposal(
            action="BUY",
            allocation_pct=8,
            confidence=forecast.confidence,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
            reason_codes=["KRONOS_UP"],
        )
    if forecast.direction == "down":
        return Proposal(
            action="SELL",
            allocation_pct=8,
            confidence=forecast.confidence,
            reason_codes=["KRONOS_DOWN"],
        )
    return Proposal(action="HOLD", allocation_pct=0, confidence=forecast.confidence)


async def get_ai_proposal(request: DecisionRequest, forecast: Forecast) -> Proposal:
    if os.getenv("AI_BACKEND", "mock") == "mock":
        return quant_proposal(forecast)

    system_prompt = (
        "You are a constrained trading proposal engine. Return JSON only. "
        "Allowed actions: BUY, SELL, HOLD. Never override risk limits."
    )
    user_payload = {
        "symbol": request.symbol,
        "mode": request.mode,
        "forecast": forecast.model_dump(mode="json"),
        "portfolio": request.portfolio.model_dump(mode="json"),
        "required_schema": {
            "action": "BUY|SELL|HOLD",
            "allocation_pct": "number 0..20",
            "confidence": "number 0..1",
            "stop_loss_pct": "number|null",
            "take_profit_pct": "number|null",
            "reason_codes": ["UPPER_SNAKE_CASE"],
        },
    }
    body = {
        "model": os.environ["NEMOTRON_MODEL"],
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, separators=(",", ":"))},
        ],
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{os.environ['NEMOTRON_URL']}/chat/completions", json=body
        )
        response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return Proposal.model_validate_json(content)
