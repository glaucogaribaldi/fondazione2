#!/usr/bin/env python3
import sys

def main():
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if "/trading/canonical-ledger" in content:
        print("QuantDinger trading_data.py is already patched!")
        return

    print("Patching QuantDinger trading_data.py...")
    patch = """

@agent_v1_bp.route("/trading/canonical-ledger", methods=["GET"])
@agent_required(SCOPE_R)
def get_canonical_ledger():
    \"\"\"
    Blocker M4: Read path inside QuantDinger Flask API process to consume the canonical ledger tables.
    \"\"\"
    with get_db_connection() as db:
        cur = db.cursor()
        
        # 1. Fetch balances
        cur.execute("SELECT lane_id, equity, cash, created_at FROM paper_balances")
        balances = cur.fetchall() or []
        
        # 2. Fetch active positions
        cur.execute("SELECT lane_id, symbol, quantity, entry_price, stop_loss_price, take_profit_price FROM paper_positions")
        positions = cur.fetchall() or []
        
        # 3. Fetch recent decision audits
        cur.execute(
            "SELECT request_id, symbol, proposed_action, final_action, approved, payload_hash, created_at "
            "FROM decision_audit ORDER BY created_at DESC LIMIT 10"
        )
        audits = cur.fetchall() or []
        
        cur.close()
        
    return envelope({
        "balances": [{
            "lane_id": b.get("lane_id"),
            "equity": float(b.get("equity") or 0.0),
            "cash": float(b.get("cash") or 0.0),
            "created_at": b.get("created_at").isoformat() if b.get("created_at") else None
        } for b in balances],
        "positions": [{
            "lane_id": p.get("lane_id"),
            "symbol": p.get("symbol"),
            "quantity": float(p.get("quantity") or 0.0),
            "entry_price": float(p.get("entry_price") or 0.0),
            "stop_loss_price": float(p.get("stop_loss_price")) if p.get("stop_loss_price") else None,
            "take_profit_price": float(p.get("take_profit_price")) if p.get("take_profit_price") else None
        } for p in positions],
        "audits": [{
            "request_id": a.get("request_id"),
            "symbol": a.get("symbol"),
            "proposed_action": a.get("proposed_action"),
            "final_action": a.get("final_action"),
            "approved": bool(a.get("approved")),
            "payload_hash": a.get("payload_hash"),
            "created_at": a.get("created_at").isoformat() if a.get("created_at") else None
        } for a in audits]
    })
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + patch)
    print("Successfully patched!")

if __name__ == "__main__":
    main()
