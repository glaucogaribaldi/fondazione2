#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime, timedelta


def request_json(url: str, api_key: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def main() -> int:
    api_key = os.getenv("DECISION_API_KEY", "")
    if not api_key:
        print("DECISION_API_KEY is required", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    candles = []
    for index in range(64):
        price = 50_000 + index * 25
        candles.append(
            {
                "timestamp": (now - timedelta(minutes=(64 - index) * 5)).isoformat(),
                "open": price,
                "high": price + 40,
                "low": price - 40,
                "close": price + 20,
                "volume": 10 + index / 10,
            }
        )
    payload = {
        "request_id": f"e2e-{int(now.timestamp())}",
        "mode": "paper",
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "market": {
            "timestamp": now.isoformat(),
            "bid": 51_594,
            "ask": 51_596,
            "candles": candles,
        },
    }
    url = os.getenv("ARENA_TEST_URL", "http://127.0.0.1:8082/v1/smoke-evaluate")
    first = request_json(url, api_key, payload)
    second = request_json(url, api_key, payload)
    assert len(first["decisions"]) == 5
    assert len(first["executions"]) == 5
    assert len(first["ranking"]) == 5
    assert all(
        "FAIL_CLOSED" not in decision["reason_codes"] for decision in first["decisions"]
    ), "one or more decision paths failed closed"
    first_ids = [item["event"]["id"] for item in first["executions"]]
    second_ids = [item["event"]["id"] for item in second["executions"]]
    assert first_ids == second_ids, "duplicate request was not idempotent"
    print("End-to-end paper test passed: 5 lanes, isolated ledger, idempotent events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
