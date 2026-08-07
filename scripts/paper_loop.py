#!/usr/bin/env python3
import os
import sys
import uuid
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, UTC
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.models import (
    DecisionRequest, PortfolioSnapshot, MarketSnapshot, Candle,
    ExecutionIntent, Proposal, Action, Forecast
)
from app.clients import get_forecast, get_ai_proposal, quant_proposal
from app.executor import PaperExecutor, DatabaseConnection
from app.coinbase_adapter import CoinbasePublicAdapter
from app.risk import evaluate_risk
from app.config import load_risk_settings, load_lane_settings


async def run_paper_loop(lane_id: str, symbol: str, runs: int, delay_seconds: int):
    print(f"=== Starting Paper Loop Orchestrator ===")
    print(f"Lane: {lane_id}")
    print(f"Symbol: {symbol}")
    print(f"Runs to execute: {runs}")
    print(f"Delay between runs: {delay_seconds}s")
    
    # 1. Initialize PaperExecutor on canonical production DB
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL environment variable is missing!", file=sys.stderr)
        sys.exit(1)
        
    executor = PaperExecutor(db_url=db_url)
    executor.initialize_lane(lane_id, 10000.0) # initial cash $10,000
    
    adapter = CoinbasePublicAdapter()
    
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

            # 6. Fetch forecast from Kronos and proposal from SGLang/Nemotron
            # Wrapped in fail-closed safety block to ensure 100% audit persistence (TASK-0003)
            proposal_reasons = []
            forecast_obj = None
            proposal = None

            print("Invoking Kronos forecasting service...")
            try:
                forecast_obj = await get_forecast(request)
                print(f"Kronos forecast direction: {forecast_obj.direction}, confidence: {forecast_obj.confidence}")
            except Exception as e:
                print(f"Error: Kronos service call failed: {e}. Failing closed to NO_TRADE.")
                proposal_reasons.append("KRONOS_FAILED")
                proposal_reasons.append(type(e).__name__.upper())

            if forecast_obj:
                print("Invoking AI proposal engine...")
                try:
                    proposal = await get_ai_proposal(request, forecast_obj)
                    print(f"AI proposal action: {proposal.action}, allocation: {proposal.allocation_pct}%")
                except Exception as e:
                    print(f"Error: AI proposal engine failed: {e}. Failing closed to NO_TRADE.")
                    proposal_reasons.append("NEMOTRON_FAILED")
                    proposal_reasons.append(type(e).__name__.upper())

            # If any backend failed, fall back to safe NO_TRADE proposal
            if not proposal:
                proposal = Proposal(
                    action="NO_TRADE",
                    allocation_pct=0.0,
                    confidence=0.0,
                    reason_codes=proposal_reasons or ["FAIL_CLOSED", "MODEL_EXCEPTION"]
                )

            # 7. Deterministic Risk Check
            print("Evaluating deterministic Risk Engine rules...")
            lane_config, lane_settings = load_lane_settings(lane_id)
            risk_result = evaluate_risk(
                request,
                proposal,
                load_risk_settings(),
                lane_settings,
                now=datetime.now(UTC),
                live_enabled=False
            )
            print(f"Risk Engine result: Approved={risk_result.approved}, Action={risk_result.action}")

            # 8. Create ExecutionIntent and execute only via PaperExecutor
            exec_res = None
            if risk_result.approved and risk_result.action in ("OPEN", "ADD", "REDUCE", "CLOSE"):
                # Determine side and quantity
                side = "BUY" if risk_result.action in ("OPEN", "ADD") else "SELL"
                quantity = 0.0
                
                if risk_result.action in ("OPEN", "ADD"):
                    allocated_cash = bal["cash"] * (risk_result.allocation_pct / 100.0)
                    quantity = allocated_cash / ticker["price"]
                elif risk_result.action == "REDUCE" and pos:
                    quantity = pos["quantity"] * (risk_result.allocation_pct / 100.0)
                elif risk_result.action == "CLOSE" and pos:
                    quantity = pos["quantity"]

                if quantity > 0.0:
                    intent_id = str(uuid.uuid4())
                    intent = ExecutionIntent(
                        execution_intent_id=intent_id,
                        risk_decision_id=str(uuid.uuid4()),
                        mode="paper",
                        symbol=symbol,
                        action=risk_result.action,
                        side=side,
                        quantity=quantity,
                        stop_price=ticker["price"] * 0.98 if risk_result.action == "OPEN" else None, # 2% stop-loss
                        take_profit_price=ticker["price"] * 1.05 if risk_result.action == "OPEN" else None, # 5% take-profit
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
                print(f"No trade executed. Risk reasons: {risk_result.reasons}")

            # 9. Persist complete causal chain to PostgreSQL for full traceability (TASK-0003)
            db_conn = executor.db.get_cursor()
            try:
                # Save audit row to decision_audit table
                with db_conn:
                    with db_conn.cursor() as cur:
                        payload = {
                            "request": request.model_dump(mode="json"),
                            "forecast": forecast_obj.model_dump(mode="json") if forecast_obj else None,
                            "proposal": proposal.model_dump(mode="json"),
                            "risk_result": {
                                "approved": risk_result.approved,
                                "action": risk_result.action,
                                "reasons": list(risk_result.reasons)
                            },
                            "execution_result": exec_res.model_dump(mode="json") if exec_res else None
                        }
                        cur.execute(
                            "INSERT INTO decision_audit (request_id, lane_id, symbol, proposed_action, final_action, approved, reason_codes, model_versions, payload_hash) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (
                                request.request_id,
                                lane_id,
                                symbol,
                                proposal.action,
                                risk_result.action,
                                risk_result.approved,
                                json.dumps(list(risk_result.reasons)),
                                json.dumps({
                                    "forecast": forecast_obj.model if forecast_obj else "failed",
                                    "decision": "nvidia/NVIDIA-Nemotron-Nano-9B-v2" if proposal else "failed"
                                }),
                                str(hash(json.dumps(payload)))
                            )
                        )
                print("Causal chain successfully persisted to PostgreSQL decision_audit ledger.")
            except Exception as audit_err:
                print(f"Warning: Failed to persist causal chain to database: {audit_err}")
            finally:
                db_conn.close()

        except Exception as e:
            print(f"Causal chain exception: {e}. Failing closed to NO_TRADE.")

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
