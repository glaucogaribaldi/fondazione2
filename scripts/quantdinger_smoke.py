#!/usr/bin/env python3
import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor

def main():
    print("=== QuantDinger Read-Only Integration Smoke Check ===")
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL is missing!", file=sys.stderr)
        sys.exit(1)
        
    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print(f"Error: Failed to connect to canonical PostgreSQL: {e}", file=sys.stderr)
        sys.exit(1)
        
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Fetch current balances
                print("\n[1/3] Retrieving balances from paper_balances...")
                cur.execute("SELECT lane_id, equity, cash, created_at FROM paper_balances")
                balances = cur.fetchall()
                print(f"{'Lane ID':<25} | {'Equity':<15} | {'Cash':<15} | {'Initialized At'}")
                print("-" * 80)
                for b in balances:
                    print(f"{b['lane_id']:<25} | {float(b['equity']):<15.2f} | {float(b['cash']):<15.2f} | {b['created_at']}")
                
                # 2. Fetch current active positions
                print("\n[2/3] Retrieving positions from paper_positions...")
                cur.execute("SELECT lane_id, symbol, quantity, entry_price, stop_loss_price, take_profit_price FROM paper_positions")
                positions = cur.fetchall()
                if not positions:
                    print("No active paper positions currently registered.")
                else:
                    print(f"{'Lane ID':<15} | {'Symbol':<10} | {'Quantity':<10} | {'Entry Price':<12} | {'Stop Loss':<12} | {'Take Profit':<12}")
                    print("-" * 90)
                    for p in positions:
                        sl = f"{float(p['stop_loss_price']):.2f}" if p['stop_loss_price'] else "None"
                        tp = f"{float(p['take_profit_price']):.2f}" if p['take_profit_price'] else "None"
                        print(f"{p['lane_id']:<15} | {p['symbol']:<10} | {float(p['quantity']):<10.4f} | {float(p['entry_price']):<12.2f} | {sl:<12} | {tp:<12}")
                
                # 3. Fetch recent decision audits with stable payload digests
                print("\n[3/3] Retrieving recent causal chains from decision_audit...")
                cur.execute(
                    "SELECT request_id, symbol, proposed_action, final_action, approved, payload_hash, created_at "
                    "FROM decision_audit ORDER BY created_at DESC LIMIT 5"
                )
                audits = cur.fetchall()
                if not audits:
                    print("No decision audit events found.")
                else:
                    print(f"{'Request ID':<38} | {'Symbol':<10} | {'Proposed':<10} | {'Final':<10} | {'Approved':<8} | {'Stable SHA-256 Digest':<15}")
                    print("-" * 115)
                    for a in audits:
                        app_str = "True" if a['approved'] else "False"
                        # Limit hash to first 12 chars for pretty printing
                        print(f"{a['request_id']:<38} | {a['symbol']:<10} | {a['proposed_action']:<10} | {a['final_action']:<10} | {app_str:<8} | {a['payload_hash'][:12]}...")
                        
    except Exception as e:
        print(f"Error: Database read-only integration query failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
        
    print("\nQuantDinger integration check completed successfully!")
    print("All reads executed directly on the canonical PostgreSQL schema without alternate execution/accounting truth.")

if __name__ == "__main__":
    main()
