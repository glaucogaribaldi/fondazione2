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


async def run_paper_loop(lane_id: str, symbol: str, runs: int, delay_seconds: int):
    print(f"=== Starting Paper Loop Orchestrator ===")
    print(f"Lane: {lane_id}")
    print(f"Symbol: {symbol}")
    print(f"Runs to execute: {runs}")
    print(f"Delay between runs: {delay_seconds}s")
    
    # 1. Initialize PaperExecutor on canonical production DB (Blocker K7)
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL environment variable is missing!", file=sys.stderr)
        sys.exit(1)
        
    executor = PaperExecutor(db_url=db_url)
    
    # Blocker K7: Do not hardcode capital. Verify configured environment variable or fail-closed.
    initial_cash_str = os.getenv("PAPER_INITIAL_CASH")
    if not initial_cash_str:
        print("Error: PAPER_INITIAL_CASH is mandatory and must be configured for paper lane initialization!", file=sys.stderr)
        sys.exit(1)
    try:
        initial_cash = float(initial_cash_str)
    except ValueError:
        print(f"Error: Invalid capital value configured: {initial_cash_str}", file=sys.stderr)
        sys.exit(1)
        
    executor.initialize_lane(lane_id, initial_cash)
    
    adapter = CoinbasePublicAdapter()
    
    # Fetch decision API key
    api_key = os.getenv("DECISION_API_KEY", "")
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    for run_idx in range(1, runs + 1):
        print(f"\n--- [Run {run_idx}/{runs}] {datetime.now(UTC).isoformat()} ---")
        
        try:
            # 2. Check stop-loss/take-profit of any active positions first (Blocker G1/H2)
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
                    continue
            else:
                print("No active position or position quantity is zero.")

            # 3. Fetch fresh Coinbase Advance public ticker and candles
            print(f"Fetching public market data for {symbol}...")
            ticker = await adapter.get_ticker(symbol, proxy_to_usd=True)
            print(f"Ticker price: {ticker['price']}, Fresh: {ticker['is_fresh']}, Age: {ticker['freshness_seconds']}s")
            
            if not ticker["is_fresh"]:
                print(f"Warning: Market data is stale! Age is {ticker['freshness_seconds']}s. Failing closed to NO_TRADE.")
                continue

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

            # 4. Construct Market & Portfolio Snapshots
            market_snap = MarketSnapshot(
                timestamp=datetime.fromisoformat(ticker["time"]),
                bid=ticker["bid"],
                ask=ticker["ask"],
                candles=candles
            )
            
            bal = executor.get_balance(lane_id)
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

            # 5. Build Decision Request
            request = DecisionRequest(
                request_id=str(uuid.uuid4()),
                mode="paper",
                lane_id=lane_id,
                symbol=symbol,
                timeframe="1m",
                market=market_snap,
                portfolio=portfolio_snap
            )

            # 6. Post Decision Request to local decision-service API
            # This triggers Kronos, SGLang, Risk Check, Prometheus Metrics, and PostgreSQL Causal Auditing
            print("Posting decision request to local decision-service API...")
            async with httpx.AsyncClient(timeout=65.0) as client:
                url = f"http://localhost:8080/v1/decision"
                response = await client.post(url, json=request.model_dump(mode="json"), headers=headers)
                response.raise_for_status()
                dec_data = response.json()
                dec_res = DecisionResponse.model_validate(dec_data)
                
            print(f"API Response: Action={dec_res.decision}, Approved={dec_res.approved_by_risk_engine}, Reasons={dec_res.reason_codes}")

            # 7. Create ExecutionIntent and execute only via PaperExecutor
            if dec_res.approved_by_risk_engine and dec_res.decision in ("OPEN", "ADD", "REDUCE", "CLOSE"):
                # Determine side and quantity
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
                        risk_decision_id=str(uuid.uuid4()),
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
                else:
                    print("Calculated trade quantity is zero. Skipping execution.")
            else:
                print("No trade executed.")

        except Exception as e:
            print(f"Causal chain exception: {e}. Failing closed.")

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
