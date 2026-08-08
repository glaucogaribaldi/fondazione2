#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime, UTC

# Add decision-service to PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.backtest import CoinbaseReplayEngine
from app.products import registry

def main():
    parser = argparse.ArgumentParser(description="Fondazione2 Backtest Replay CLI")
    parser.add_argument("--symbols", required=True, help="Comma-separated canonical symbols, e.g. BTC/USDC,ETH/USDC")
    parser.add_argument("--start", required=True, help="Start ISO timestamp (YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--end", required=True, help="End ISO timestamp (YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--granularity", type=int, default=300, help="Timeframe granularity in seconds")
    parser.add_argument("--cash", type=float, default=10000.0, help="Initial simulation cash")
    parser.add_argument("--fee", type=float, default=0.0060, help="Fee rate")
    parser.add_argument("--slippage", type=float, default=0.0005, help="Slippage rate")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic simulation seed")
    args = parser.parse_args()

    symbols_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
    
    try:
        start_dt = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
        end_dt = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    except Exception as e:
        print(f"Error: Invalid start/end ISO format: {e}")
        sys.exit(1)

    print("Backtest CLI: Loading dataset and running replay simulation...")
    
    engine = CoinbaseReplayEngine()
    
    try:
        # Load dataset
        dataset = engine.load_dataset_from_db(
            symbols=symbols_list,
            granularity=args.granularity,
            start_time=start_dt,
            end_time=end_dt,
            as_of=end_dt
        )
        
        # Run simulation
        result = engine.run_backtest(
            dataset=dataset,
            initial_cash=args.cash,
            fee_rate=args.fee,
            slippage_rate=args.slippage,
            seed=args.seed
        )
        
        print("\n=== BACKTEST REPLAY RESULTS ===")
        print(f"Run ID: {result['run_id']}")
        print(f"Dataset Hash: {result['dataset_hash']}")
        print(f"Config Hash: {result['config_hash']}")
        print(f"Total Trades Executed: {result['trades_count']}")
        print(f"Realized PnL: {result['realized_pnl']:.4f} USDC")
        print(f"Total Fees Paid: {result['fees_paid']:.4f} USDC")
        print(f"Final Portfolio Equity: {result['final_equity']:.4f} USDC")
        print(f"Maximum Drawdown: {result['max_drawdown']:.4f}%")
        print(f"Deterministic Result Digest: {result['result_digest']}")
        print("===============================\n")
        
    except Exception as e:
        print(f"Error executing backtest replay: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
