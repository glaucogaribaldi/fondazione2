#!/usr/bin/env python3
import os
import sys
import argparse
import asyncio
from pathlib import Path
from datetime import datetime, UTC

# Add decision-service to PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.backfill import CoinbaseBackfillEngine
from app.products import registry

async def main():
    parser = argparse.ArgumentParser(description="Fondazione2 Historical Backfill Engine CLI")
    parser.add_argument("--all-active-products", action="store_true", help="Backfill all active SPOT products")
    parser.add_argument("--symbol", help="Canonical symbol, e.g. BTC/USDC")
    parser.add_argument("--start", required=True, help="Start ISO timestamp (YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--end", required=True, help="End ISO timestamp (YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--granularity", type=int, default=300, help="Granularity in seconds (60, 300, 3600, etc.)")
    parser.add_argument("--resume", action="store_true", default=True, help="Resume from previous checkpoints")
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run: do not execute downloads")
    args = parser.parse_args()

    # Parse timestamps
    try:
        start_dt = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
        end_dt = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    except Exception as e:
        print(f"Error: Invalid start/end ISO format: {e}")
        sys.exit(1)

    # Initialize registry dynamically
    print("Backfill CLI: Synchronizing Coinbase products catalog...")
    await asyncio.to_thread(registry.sync_universe)

    symbols_to_backfill = []
    if args.all_active_products:
        active_products = [p for p in registry.list_products() if p.market_data_eligible]
        symbols_to_backfill = [p.canonical_symbol for p in active_products]
    elif args.symbol:
        symbols_to_backfill = [args.symbol]
    else:
        print("Error: Either --all-active-products or --symbol is mandatory!")
        sys.exit(1)

    print(f"Backfill CLI: Selected {len(symbols_to_backfill)} products to backfill.")

    engine = CoinbaseBackfillEngine()
    
    success_count = 0
    for sym in symbols_to_backfill:
        try:
            res = await engine.backfill_product(
                symbol=sym,
                start_time=start_dt,
                end_time=end_dt,
                granularity=args.granularity,
                resume=args.resume,
                dry_run=args.dry_run
            )
            if res:
                success_count += 1
        except Exception as e:
            print(f"Backfill CLI: Error processing {sym}: {e}")

    print(f"Backfill CLI: Processed {len(symbols_to_backfill)} products. Success count: {success_count}/{len(symbols_to_backfill)}.")

if __name__ == "__main__":
    asyncio.run(main())
