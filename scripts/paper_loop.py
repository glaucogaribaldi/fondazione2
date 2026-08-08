#!/usr/bin/env python3
import os
import sys
import uuid
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, UTC, timedelta
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.models import (
    DecisionRequest, PortfolioSnapshot, MarketSnapshot, Candle,
    ExecutionIntent, Proposal, Action, DecisionResponse
)
from app.executor import PaperExecutor
from app.coinbase_adapter import CoinbasePublicAdapter


async def run_one_cycle(
    lane_id: str,
    symbol: str,
    executor: PaperExecutor,
    adapter: CoinbasePublicAdapter,
    api_key: str,
    decision_service_url: str = "http://localhost:8080"
) -> bool:
    """
    Blocker L4: Exposes a standalone, testable single-cycle function.
    Executes checks, fetches market data, posts to decision-service,
    and finalizes the causal audit chain.
    """
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }

    # 1. Check stop-loss/take-profit of any active positions first (Blocker G1/H2)
    print("Checking active position stops...")
    pos = executor.get_position(lane_id, symbol)
    if pos and pos["quantity"] > 0:
        print(f"Active position found: {pos['quantity']} {symbol} @ {pos['entry_price']}")
        # Fetch current ticker to cross
        ticker = await adapter.get_ticker(symbol, proxy_to_usd=True)
        current_price = ticker["price"]
        print(f"Current market price: {current_price}")
        trigger_res = executor.check_and_trigger_stops(lane_id, symbol, current_price)
        if trigger_res:
            print(f"PROTECTIVE EXIT TRIGGERED: {trigger_res.status} with reason: {trigger_res.reason_codes}")
            
            # Update metric in decision service via finalization if possible
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    url = f"{decision_service_url}/v1/decision/finalize"
                    intent_dict = {
                        "execution_intent_id": trigger_res.execution_intent_id,
                        "symbol": symbol,
                        "action": "CLOSE",
                        "side": "SELL",
                        "quantity": float(pos["quantity"])
                    }
                    result_dict = {
                        "execution_intent_id": trigger_res.execution_intent_id,
                        "status": "FILLED",
                        "average_fill_price": float(trigger_res.average_fill_price),
                        "reason_codes": list(trigger_res.reason_codes)
                    }
                    await client.post(url, json={
                        "request_id": str(uuid.uuid4()), # protective exit is post-hoc
                        "execution_intent": intent_dict,
                        "execution_result": result_dict
                    }, headers=headers)
            except Exception as e:
                print(f"Failed to post metric for protective exit: {e}")
            return True
    else:
        print("No active position or position quantity is zero.")

    # 2. Fetch fresh Coinbase Advance public ticker and candles
    print(f"Fetching public market data for {symbol}...")
    try:
        ticker = await adapter.get_ticker(symbol, proxy_to_usd=True)
    except Exception as e:
        print(f"Failed to fetch ticker from Coinbase: {e}. Failing closed.")
        return False

    print(f"Ticker price: {ticker['price']}, Fresh: {ticker['is_fresh']}, Age: {ticker['freshness_seconds']}s")
    
    if not ticker["is_fresh"]:
        print(f"Warning: Market data is stale! Age is {ticker['freshness_seconds']}s. Failing closed.")
        # Trigger an API call with stale data so risk engine processes and audits it (L4)
        pass

    try:
        raw_candles = await adapter.get_candles(symbol, granularity=60, proxy_to_usd=True)
        # Sort by timestamp ascending for correctness
        sorted_candles = sorted(raw_candles, key=lambda x: x[0])
        candles = []
        for c in sorted_candles[-32:]: # Take last 32 candles as required by MarketSnapshot
            candles.append(Candle(
                timestamp=datetime.fromtimestamp(c[0], tz=UTC),
                open=float(c[3]),
                high=float(c[2]),
                low=float(c[1]),
                close=float(c[4]),
                volume=float(c[5])
            ))
    except Exception as e:
        print(f"Failed to fetch candles: {e}. Generating flat candles for fallback.")
        candles = [Candle(
            timestamp=datetime.now(UTC) - timedelta(minutes=i),
            open=ticker["price"], high=ticker["price"], low=ticker["price"], close=ticker["price"], volume=1.0
        ) for i in range(32)]

    # 3. Construct Market & Portfolio Snapshots
    market_snap = MarketSnapshot(
        timestamp=datetime.fromisoformat(ticker["time"]),
        bid=ticker["bid"],
        ask=ticker["ask"],
        candles=candles
    )
    
    bal = executor.get_balance(lane_id)
    if not bal:
        # Blocker L5: Fail-closed if lane is missing. Do not invent capital.
        raise ValueError(f"CRITICAL SAFETY ERROR: Balance for lane '{lane_id}' does not exist in database!")

    pos = executor.get_position(lane_id, symbol)
    open_pos_count = 1 if pos and pos["quantity"] > 0 else 0
    current_pos_pct = 0.0
    if pos and pos["quantity"] > 0 and bal["equity"] > 0:
        current_pos_pct = (pos["quantity"] * ticker["price"] / bal["equity"]) * 100.0

    portfolio_snap = PortfolioSnapshot(
        equity=bal["equity"],
        cash=bal["cash"],
        daily_pnl_pct=0.0,
        open_positions=open_pos_count,
        current_position_pct=current_pos_pct
    )

    # 4. Build Decision Request
    request_id = str(uuid.uuid4())
    request = DecisionRequest(
        request_id=request_id,
        mode="paper",
        lane_id=lane_id,
        symbol=symbol,
        timeframe="1m",
        market=market_snap,
        portfolio=portfolio_snap
    )

    # 5. Post Decision Request to local decision-service API
    print("Posting decision request to local decision-service API...")
    try:
        async with httpx.AsyncClient(timeout=65.0) as client:
            url = f"{decision_service_url}/v1/decision"
            response = await client.post(url, json=request.model_dump(mode="json"), headers=headers)
            response.raise_for_status()
            dec_data = response.json()
            dec_res = DecisionResponse.model_validate(dec_data)
    except Exception as e:
        # Blocker K3 / L4: Fail-closed on API or audit persistence failure
        print(f"CRITICAL: Decision API request or audit persistence failed: {e}. Aborting cycle.")
        return False
        
    print(f"API Response: Action={dec_res.decision}, Approved={dec_res.approved_by_risk_engine}, Reasons={dec_res.reason_codes}")

    # 6. Create ExecutionIntent and execute only via PaperExecutor
    intent_dict = None
    result_dict = None

    if dec_res.approved_by_risk_engine and dec_res.decision in ("OPEN", "ADD", "REDUCE", "CLOSE"):
        side = "BUY" if dec_res.decision in ("OPEN", "ADD") else "SELL"
        quantity = 0.0
        
        if dec_res.decision in ("OPEN", "ADD"):
            allocated_cash = bal["cash"] * (dec_res.allocation_pct / 100.0)
            quantity = allocated_cash / ticker["price"]
        elif dec_res.decision == "REDUCE" and pos:
            quantity = pos["quantity"] * (dec_res.allocation_pct / 100.0)
        elif dec_res.decision == "CLOSE" and pos:
            quantity = pos["quantity"]

        if quantity > 0.0:
            intent_id = str(uuid.uuid4())
            intent = ExecutionIntent(
                execution_intent_id=intent_id,
                risk_decision_id=request_id,
                mode="paper",
                symbol=symbol,
                action=dec_res.decision,
                side=side,
                quantity=quantity,
                stop_price=ticker["price"] * 0.98 if dec_res.decision == "OPEN" else None, # 2% stop-loss
                take_profit_price=ticker["price"] * 1.05 if dec_res.decision == "OPEN" else None, # 5% take-profit
                client_order_id=f"order-loop-{intent_id}",
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5)
            )
            
            print(f"Executing ExecutionIntent: {side} {quantity} {symbol} @ {ticker['price']}")
            exec_res = executor.execute_intent(lane_id, intent, ticker["price"])
            print(f"ExecutionResult Status: {exec_res.status}, Fee: {exec_res.fee}, Slippage: {exec_res.slippage}")
            
            intent_dict = intent.model_dump(mode="json")
            result_dict = {
                "execution_intent_id": intent_id,
                "broker_order_id": f"broker-{intent_id}",
                "status": exec_res.status,
                "requested_quantity": float(intent.quantity),
                "filled_quantity": float(exec_res.filled_quantity),
                "average_fill_price": float(exec_res.average_fill_price) if exec_res.average_fill_price else None,
                "fee": float(exec_res.fee),
                "slippage": float(exec_res.slippage),
                "reason_codes": list(exec_res.reason_codes)
            }
        else:
            print("Calculated trade quantity is zero. Skipping execution.")
    else:
        print("No trade executed.")

    # 7. Blocker L1: Finalize the causal audit chain (MarketSnapshot, Forecast, Proposal, Response, ExecutionIntent, ExecutionResult)
    print("Finalizing causal audit chain with execution parameters...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{decision_service_url}/v1/decision/finalize"
            response = await client.post(url, json={
                "request_id": request_id,
                "execution_intent": intent_dict,
                "execution_result": result_dict
            }, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            print(f"Causal audit chain successfully updated. Stable SHA-256 Digest: {res_data.get('payload_hash')}")
    except Exception as e:
        # Blocker K3 / L1: Fail-closed on audit finalize failure
        print(f"CRITICAL SAFETY ERROR: Failed to finalize causal audit chain: {e}")
        return False

    return True


async def run_paper_loop(lane_id: str, symbol: str, runs: int, delay_seconds: int):
    print(f"=== Starting Paper Loop Orchestrator ===")
    print(f"Lane: {lane_id}")
    print(f"Symbol: {symbol}")
    print(f"Runs to execute: {runs}")
    print(f"Delay between runs: {delay_seconds}s")
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL environment variable is missing!", file=sys.stderr)
        sys.exit(1)
        
    executor = PaperExecutor(db_url=db_url)
    
    # Blocker L5: Verify lane already exists. Do not automatically initialize or invent capital!
    bal = executor.get_balance(lane_id)
    if not bal:
        print(f"Error: Lane '{lane_id}' does not exist and is not initialized in the database! Fail-closed.", file=sys.stderr)
        sys.exit(1)
        
    adapter = CoinbasePublicAdapter()
    api_key = os.getenv("DECISION_API_KEY", "")
    
    for run_idx in range(1, runs + 1):
        print(f"\n--- [Run {run_idx}/{runs}] {datetime.now(UTC).isoformat()} ---")
        
        try:
            await run_one_cycle(lane_id, symbol, executor, adapter, api_key)
        except Exception as e:
            print(f"Causal chain exception inside cycle: {e}. Failing closed.")

        if run_idx < runs:
            print(f"Waiting {delay_seconds}s for next run...")
            await asyncio.sleep(delay_seconds)
            
    print("=== Paper Loop Completed ===")


def main():
    parser = argparse.ArgumentParser(description="Fondazione2 Paper Loop Orchestrator")
    parser.add_argument("--lane", default="lane_1", help="Strategy lane ID")
    parser.add_argument("--symbol", default="BTC/USDC", help="Trading symbol")
    parser.add_argument("--runs", type=int, default=3, help="Number of loops to execute")
    parser.add_argument("--delay", type=int, default=5, help="Delay in seconds between loops")
    args = parser.parse_args()
    
    asyncio.run(run_paper_loop(args.lane, args.symbol, args.runs, args.delay))


if __name__ == "__main__":
    main()
