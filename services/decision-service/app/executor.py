import os
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, UTC
from typing import Literal, Any
import sqlite3
import json
from .models import ExecutionIntent, ExecutionResult, RiskDecision, Action


class DatabaseConnection:
    """
    Unified Database connection that supports both PostgreSQL (production) and SQLite (tests/fallback).
    """
    def __init__(self, db_url: str | None = None):
        self.db_url = db_url or os.getenv("DATABASE_URL")
        self.use_sqlite = not self.db_url or self.db_url.startswith("sqlite")
        self.sqlite_conn = None

        if self.use_sqlite:
            # For testing and isolated sandboxes
            self.sqlite_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self.sqlite_conn.row_factory = sqlite3.Row
            self._init_sqlite_schema()

    def _init_sqlite_schema(self):
        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS decision_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            lane_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            proposed_action TEXT NOT NULL,
            final_action TEXT NOT NULL,
            approved INTEGER NOT NULL,
            reason_codes TEXT NOT NULL DEFAULT '[]',
            model_versions TEXT NOT NULL DEFAULT '{}',
            payload_hash TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane_id TEXT NOT NULL UNIQUE,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss_price REAL,
            take_profit_price REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(lane_id, symbol)
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_intent_id TEXT NOT NULL UNIQUE,
            risk_decision_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            order_type TEXT NOT NULL,
            limit_price REAL,
            stop_price REAL,
            take_profit_price REAL,
            time_exit_at TEXT,
            client_order_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_intent_id TEXT NOT NULL UNIQUE,
            broker_order_id TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_quantity REAL NOT NULL,
            filled_quantity REAL NOT NULL,
            average_fill_price REAL,
            fee REAL NOT NULL DEFAULT 0,
            slippage REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reason_codes TEXT NOT NULL DEFAULT '[]'
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            price REAL NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS arena_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane_id TEXT NOT NULL,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            realized_pnl REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            fees REAL NOT NULL DEFAULT 0,
            max_drawdown_pct REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        self.sqlite_conn.commit()

    def get_cursor(self):
        if self.use_sqlite:
            return self.sqlite_conn.cursor()
        
        # PostgreSQL with strict SERIALIZABLE isolation session-level configuration (Blocker G3)
        conn = psycopg2.connect(self.db_url)
        conn.set_session(isolation_level='SERIALIZABLE', autocommit=False)
        return conn


class PaperExecutor:
    """
    PostgreSQL/Database-backed PaperExecutor executing normalized ExecutionIntents
    adhering strictly to the Decision Contract v0 and Safety Contract.
    """
    def __init__(self, db_url: str | None = None, fee_rate: float = 0.0060, slippage_rate: float = 0.0005):
        self.db = DatabaseConnection(db_url)
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    def initialize_lane(self, lane_id: str, initial_cash: float):
        """
        Set up the lane balance if not present.
        """
        conn = self.db.get_cursor()
        try:
            if self.db.use_sqlite:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_balances (lane_id, equity, cash) VALUES (?, ?, ?)",
                    (lane_id, initial_cash, initial_cash)
                )
                conn.execute(
                    "INSERT OR IGNORE INTO arena_snapshots (lane_id, equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct) "
                    "SELECT ?, ?, ?, 0.0, 0.0, 0.0, 0.0 WHERE NOT EXISTS (SELECT 1 FROM arena_snapshots WHERE lane_id = ?)",
                    (lane_id, initial_cash, initial_cash, lane_id)
                )
                self.db.sqlite_conn.commit()
            else:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO paper_balances (lane_id, equity, cash) VALUES (%s, %s, %s) "
                            "ON CONFLICT (lane_id) DO NOTHING",
                            (lane_id, initial_cash, initial_cash)
                        )
                        cur.execute(
                            "INSERT INTO arena_snapshots (lane_id, equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct) "
                            "SELECT %s, %s, %s, 0.0, 0.0, 0.0, 0.0 WHERE NOT EXISTS (SELECT 1 FROM arena_snapshots WHERE lane_id = %s)",
                            (lane_id, initial_cash, initial_cash, lane_id)
                        )
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def update_market_mark(self, symbol: str, price: float):
        """
        Maintains fresh per-symbol market marks in the database (Blocker G4).
        Also updates PnL, equity, and drawdown on fresh market marks (O1).
        """
        conn = self.db.get_cursor()
        try:
            now_str = datetime.now(UTC).isoformat()
            if self.db.use_sqlite:
                old_row = conn.execute("SELECT price FROM market_marks WHERE symbol = ?", (symbol,)).fetchone()
                price_changed = (old_row is None) or (abs(old_row[0] - price) > 1e-9)

                conn.execute(
                    "INSERT OR REPLACE INTO market_marks (symbol, price, updated_at) VALUES (?, ?, ?)",
                    (symbol, price, now_str)
                )
                self.db.sqlite_conn.commit()

                if price_changed:
                    # Update equity/drawdown/pnl for any active lane holding this symbol (O1)
                    lanes = conn.execute(
                        "SELECT DISTINCT lane_id FROM paper_positions WHERE symbol = ? AND quantity > 0",
                        (symbol,)
                    ).fetchall()
                    for (lane_id,) in lanes:
                        bal_row = conn.execute("SELECT cash FROM paper_balances WHERE lane_id = ?", (lane_id,)).fetchone()
                        if bal_row:
                            cash = bal_row[0]
                            all_pos = conn.execute("SELECT symbol, quantity FROM paper_positions WHERE lane_id = ?", (lane_id,)).fetchall()
                            mtm_value = 0.0
                            for p_sym, p_qty in all_pos:
                                if p_sym == symbol:
                                    m_price = price
                                else:
                                    m_row = conn.execute("SELECT price FROM market_marks WHERE symbol = ?", (p_sym,)).fetchone()
                                    m_price = m_row[0] if m_row else 0.0
                                mtm_value += p_qty * m_price
                            new_equity = cash + mtm_value
                            conn.execute("UPDATE paper_balances SET equity = ? WHERE lane_id = ?", (new_equity, lane_id))
                            self.db.sqlite_conn.commit()
                            self._write_arena_snapshot(conn, None, lane_id, symbol, price, 0.0, is_sqlite=True)
                            self.db.sqlite_conn.commit()
            else:
                with conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT price FROM market_marks WHERE symbol = %s", (symbol,))
                        old_row = cur.fetchone()
                        price_changed = (old_row is None) or (abs(float(old_row["price"]) - price) > 1e-9)

                        cur.execute(
                            "INSERT INTO market_marks (symbol, price, updated_at) VALUES (%s, %s, %s) "
                            "ON CONFLICT (symbol) DO UPDATE SET price = EXCLUDED.price, updated_at = EXCLUDED.updated_at",
                            (symbol, price, datetime.now(UTC))
                        )
                        
                        if price_changed:
                            # Update equity/drawdown/pnl for any active lane holding this symbol (O1)
                            cur.execute(
                                "SELECT DISTINCT lane_id FROM paper_positions WHERE symbol = %s AND quantity > 0",
                                (symbol,)
                            )
                            lanes = cur.fetchall()
                            for lane in lanes:
                                lane_id = lane["lane_id"]
                                cur.execute("SELECT cash FROM paper_balances WHERE lane_id = %s FOR UPDATE", (lane_id,))
                                bal_row = cur.fetchone()
                                if bal_row:
                                    cash = float(bal_row["cash"])
                                    cur.execute("SELECT symbol, quantity FROM paper_positions WHERE lane_id = %s", (lane_id,))
                                    all_pos = cur.fetchall()
                                    mtm_value = 0.0
                                    for p in all_pos:
                                        p_sym = p["symbol"]
                                        p_qty = float(p["quantity"])
                                        if p_sym == symbol:
                                            m_price = price
                                        else:
                                            cur.execute("SELECT price FROM market_marks WHERE symbol = %s", (p_sym,))
                                            m_row = cur.fetchone()
                                            m_price = float(m_row["price"]) if m_row else 0.0
                                        mtm_value += p_qty * m_price
                                    new_equity = cash + mtm_value
                                    cur.execute("UPDATE paper_balances SET equity = %s WHERE lane_id = %s", (new_equity, lane_id))
                                    self._write_arena_snapshot(None, cur, lane_id, symbol, price, 0.0, is_sqlite=False)
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def get_market_mark(self, symbol: str) -> dict[str, Any] | None:
        conn = self.db.get_cursor()
        try:
            if self.db.use_sqlite:
                row = conn.execute("SELECT price, updated_at FROM market_marks WHERE symbol = ?", (symbol,)).fetchone()
                if row:
                    try:
                        updated_at = datetime.fromisoformat(row["updated_at"])
                    except ValueError:
                        updated_at = datetime.now(UTC)
                    return {"price": row["price"], "updated_at": updated_at}
            else:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT price, updated_at FROM market_marks WHERE symbol = %s", (symbol,))
                    row = cur.fetchone()
                    if row:
                        return {"price": float(row["price"]), "updated_at": row["updated_at"]}
            return None
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def get_balance(self, lane_id: str) -> dict[str, float]:
        conn = self.db.get_cursor()
        try:
            if self.db.use_sqlite:
                row = conn.execute("SELECT equity, cash FROM paper_balances WHERE lane_id = ?", (lane_id,)).fetchone()
                if row:
                    return {"equity": row["equity"], "cash": row["cash"]}
            else:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT equity, cash FROM paper_balances WHERE lane_id = %s", (lane_id,))
                    row = cur.fetchone()
                    if row:
                        return {"equity": float(row["equity"]), "cash": float(row["cash"])}
            return {"equity": 0.0, "cash": 0.0}
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def get_position(self, lane_id: str, symbol: str) -> dict[str, Any] | None:
        conn = self.db.get_cursor()
        try:
            if self.db.use_sqlite:
                row = conn.execute(
                    "SELECT quantity, entry_price, stop_loss_price, take_profit_price FROM paper_positions WHERE lane_id = ? AND symbol = ?",
                    (lane_id, symbol)
                ).fetchone()
                if row:
                    return {
                        "quantity": row["quantity"],
                        "entry_price": row["entry_price"],
                        "stop_loss_price": row["stop_loss_price"],
                        "take_profit_price": row["take_profit_price"]
                    }
            else:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT quantity, entry_price, stop_loss_price, take_profit_price FROM paper_positions WHERE lane_id = %s AND symbol = %s",
                        (lane_id, symbol)
                    )
                    row = cur.fetchone()
                    if row:
                        return {
                            "quantity": float(row["quantity"]),
                            "entry_price": float(row["entry_price"]),
                            "stop_loss_price": float(row["stop_loss_price"]) if row["stop_loss_price"] else None,
                            "take_profit_price": float(row["take_profit_price"]) if row["take_profit_price"] else None
                        }
            return None
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def get_execution_result(self, execution_intent_id: str) -> dict[str, Any] | None:
        """
        Retrieve a persisted ExecutionResult from the database (Blocker H1/H2).
        """
        conn = self.db.get_cursor()
        try:
            if self.db.use_sqlite:
                row = conn.execute(
                    "SELECT broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes "
                    "FROM execution_results WHERE execution_intent_id = ?",
                    (execution_intent_id,)
                ).fetchone()
                if row:
                    return {
                        "broker_order_id": row["broker_order_id"],
                        "status": row["status"],
                        "requested_quantity": float(row["requested_quantity"]),
                        "filled_quantity": float(row["filled_quantity"]),
                        "average_fill_price": float(row["average_fill_price"]) if row["average_fill_price"] else None,
                        "fee": float(row["fee"]),
                        "slippage": float(row["slippage"]),
                        "reason_codes": json.loads(row["reason_codes"])
                    }
            else:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes "
                        "FROM execution_results WHERE execution_intent_id = %s",
                        (execution_intent_id,)
                    )
                    row = cur.fetchone()
                    if row:
                        reasons = row["reason_codes"]
                        if isinstance(reasons, str):
                            reasons = json.loads(reasons)
                        return {
                            "broker_order_id": row["broker_order_id"],
                            "status": row["status"],
                            "requested_quantity": float(row["requested_quantity"]),
                            "filled_quantity": float(row["filled_quantity"]),
                            "average_fill_price": float(row["average_fill_price"]) if row["average_fill_price"] else None,
                            "fee": float(row["fee"]),
                            "slippage": float(row["slippage"]),
                            "reason_codes": reasons
                        }
            return None
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def execute_intent(self, lane_id: str, intent: ExecutionIntent, fill_price: float, max_open_positions: int | None = None, reasons: list[str] | None = None) -> ExecutionResult:
        """
        Atomically executes an ExecutionIntent against the database state (HST-02 / HST-09 / HST-06).
        Enforces G1 stop-loss wiring, G2 slippage dimensional scaling, G3 serializable safety, and H2 atomic reason persistence.
        """
        # Save fill price immediately as the fresh market mark for this symbol
        self.update_market_mark(intent.symbol, fill_price)

        conn = self.db.get_cursor()
        try:
            reasons_list = reasons or []
            # 1. Process Order Intent under database transaction (psycopg2 starts transaction automatically on SERIALIZABLE)
            if self.db.use_sqlite:
                # 1b. Idempotency Check (HST-09)
                existing_intent = conn.execute(
                    "SELECT execution_intent_id FROM execution_intents WHERE client_order_id = ?",
                    (intent.client_order_id,)
                ).fetchone()
                if existing_intent:
                    intent_id = existing_intent[0]
                    res_row = conn.execute(
                        "SELECT broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes FROM execution_results WHERE execution_intent_id = ?",
                        (intent_id,)
                    ).fetchone()
                    if res_row:
                        loaded_reasons = json.loads(res_row["reason_codes"])
                        for r in reasons_list:
                            if r not in loaded_reasons:
                                loaded_reasons.append(r)
                        if "IDEMPOTENT_REPLAY" not in loaded_reasons:
                            loaded_reasons.append("IDEMPOTENT_REPLAY")
                        return ExecutionResult(
                            execution_intent_id=intent_id,
                            broker_order_id=res_row["broker_order_id"],
                            status=res_row["status"],
                            requested_quantity=float(res_row["requested_quantity"]),
                            filled_quantity=float(res_row["filled_quantity"]),
                            average_fill_price=float(res_row["average_fill_price"]) if res_row["average_fill_price"] else None,
                            fee=float(res_row["fee"]),
                            slippage=float(res_row["slippage"]),
                            updated_at=datetime.now(UTC),
                            reason_codes=loaded_reasons
                        )

                # Fetch balance
                bal_row = conn.execute("SELECT cash, equity FROM paper_balances WHERE lane_id = ?", (lane_id,)).fetchone()
                if not bal_row:
                    raise ValueError(f"Balance not initialized for lane: {lane_id}")
                cash = bal_row["cash"]

                # Fetch positions
                pos_row = conn.execute("SELECT quantity, entry_price FROM paper_positions WHERE lane_id = ? AND symbol = ?", (lane_id, intent.symbol)).fetchone()
                current_qty = pos_row["quantity"] if pos_row else 0.0
                current_entry = pos_row["entry_price"] if pos_row else 0.0

                # Recheck portfolio open positions count (HST-02 / G3 portfolio reservation)
                if intent.action == "OPEN" and max_open_positions is not None:
                    active_cnt_row = conn.execute("SELECT COUNT(*) as cnt FROM paper_positions WHERE lane_id = ? AND quantity > 0", (lane_id,)).fetchone()
                    active_cnt = active_cnt_row["cnt"] if active_cnt_row else 0
                    if active_cnt >= max_open_positions:
                        return self._reject_intent(conn, intent, "OPEN_POSITION_LIMIT", extra_reasons=reasons_list)

                # G2 Slippage Dimensional Math
                slippage_factor = 1.0 + self.slippage_rate if intent.side == "BUY" else 1.0 - self.slippage_rate
                adjusted_fill_price = fill_price * slippage_factor
                
                # Fees & slippage calculations
                quantity = intent.quantity
                fee = quantity * fill_price * self.fee_rate
                slippage_cost = quantity * abs(adjusted_fill_price - fill_price)
                total_cost = (quantity * adjusted_fill_price) + fee

                # Validate
                if intent.side == "BUY" and cash < total_cost:
                    return self._reject_intent(conn, intent, "INSUFFICIENT_CASH", extra_reasons=reasons_list)
                if intent.side == "SELL" and current_qty < quantity:
                    return self._reject_intent(conn, intent, "INSUFFICIENT_POSITION", extra_reasons=reasons_list)

                # Perform balance and position state updates
                if intent.side == "BUY":
                    new_cash = cash - total_cost
                    new_qty = current_qty + quantity
                    new_entry = ((current_qty * current_entry) + (quantity * adjusted_fill_price)) / new_qty
                    
                    # G1 Wiring Correction: persist intent.stop_price into stop_loss_price
                    conn.execute(
                        "INSERT OR REPLACE INTO paper_positions (lane_id, symbol, quantity, entry_price, stop_loss_price, take_profit_price) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (lane_id, intent.symbol, new_qty, new_entry, intent.stop_price, intent.take_profit_price)
                    )
                else: # SELL
                    new_cash = cash + (quantity * adjusted_fill_price) - fee
                    new_qty = current_qty - quantity
                    if new_qty <= 0.00001:
                        conn.execute("DELETE FROM paper_positions WHERE lane_id = ? AND symbol = ?", (lane_id, intent.symbol))
                    else:
                        conn.execute(
                            "UPDATE paper_positions SET quantity = ? WHERE lane_id = ? AND symbol = ?",
                            (new_qty, lane_id, intent.symbol)
                        )

                # G4 Multi-Asset MTM
                all_pos = conn.execute("SELECT symbol, quantity FROM paper_positions WHERE lane_id = ?", (lane_id,)).fetchall()
                mtm_value = 0.0
                for p in all_pos:
                    sym = p["symbol"]
                    qty = p["quantity"]
                    
                    # Fetch fresh mark
                    mark_row = conn.execute("SELECT price, updated_at FROM market_marks WHERE symbol = ?", (sym,)).fetchone()
                    if not mark_row:
                        raise ValueError(f"No fresh market mark found for {sym}")
                    
                    # Check age <= 90s
                    try:
                        mark_time = datetime.fromisoformat(mark_row["updated_at"])
                    except ValueError:
                        mark_time = datetime.now(UTC)
                    if abs((datetime.now(UTC) - mark_time).total_seconds()) > 90.0:
                        raise ValueError(f"Stale market mark for {sym}")
                        
                    mtm_value += qty * mark_row["price"]

                new_equity = new_cash + mtm_value
                conn.execute("UPDATE paper_balances SET cash = ?, equity = ? WHERE lane_id = ?", (new_cash, new_equity, lane_id))

                # Insert Intent & Result (atomically saving H2 reason codes)
                broker_id = f"paper-{uuid.uuid4()}"
                conn.execute(
                    "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, str(intent.time_exit_at), intent.client_order_id, str(intent.expires_at))
                )
                final_reasons = reasons_list + ["EXECUTED_PAPER"]
                conn.execute(
                    "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (intent.execution_intent_id, broker_id, "FILLED", intent.quantity, intent.quantity, adjusted_fill_price, fee, slippage_cost, json.dumps(final_reasons))
                )
                self._write_arena_snapshot(conn, None, lane_id, intent.symbol, adjusted_fill_price, fee, is_sqlite=True)
                self.db.sqlite_conn.commit()

            else:
                # PostgreSQL path (G3 serializable concurrency)
                with conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        # 1b. Idempotency Check within SERIALIZABLE transaction (HST-09 / G3)
                        cur.execute(
                            "SELECT execution_intent_id FROM execution_intents WHERE client_order_id = %s",
                            (intent.client_order_id,)
                        )
                        existing_intent = cur.fetchone()
                        if existing_intent:
                            intent_id = existing_intent["execution_intent_id"]
                            cur.execute(
                                "SELECT broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes FROM execution_results WHERE execution_intent_id = %s",
                                (intent_id,)
                            )
                            res_row = cur.fetchone()
                            if res_row:
                                loaded_reasons = res_row["reason_codes"]
                                if isinstance(loaded_reasons, str):
                                    loaded_reasons = json.loads(loaded_reasons)
                                for r in reasons_list:
                                    if r not in loaded_reasons:
                                        loaded_reasons.append(r)
                                if "IDEMPOTENT_REPLAY" not in loaded_reasons:
                                    loaded_reasons.append("IDEMPOTENT_REPLAY")
                                return ExecutionResult(
                                    execution_intent_id=intent_id,
                                    broker_order_id=res_row["broker_order_id"],
                                    status=res_row["status"],
                                    requested_quantity=float(res_row["requested_quantity"]),
                                    filled_quantity=float(res_row["filled_quantity"]),
                                    average_fill_price=float(res_row["average_fill_price"]) if res_row["average_fill_price"] else None,
                                    fee=float(res_row["fee"]),
                                    slippage=float(res_row["slippage"]),
                                    updated_at=datetime.now(UTC),
                                    reason_codes=loaded_reasons
                                )

                        # Fetch balance
                        cur.execute("SELECT cash, equity FROM paper_balances WHERE lane_id = %s FOR UPDATE", (lane_id,))
                        bal_row = cur.fetchone()
                        if not bal_row:
                            raise ValueError(f"Balance not initialized for lane: {lane_id}")
                        cash = float(bal_row["cash"])

                        # Fetch positions
                        cur.execute("SELECT quantity, entry_price FROM paper_positions WHERE lane_id = %s AND symbol = %s FOR UPDATE", (lane_id, intent.symbol))
                        pos_row = cur.fetchone()
                        current_qty = float(pos_row["quantity"]) if pos_row else 0.0
                        current_entry = float(pos_row["entry_price"]) if pos_row else 0.0

                        # Recheck portfolio open positions count (HST-02 / G3 portfolio reservation)
                        if intent.action == "OPEN" and max_open_positions is not None:
                            cur.execute("SELECT COUNT(*) as cnt FROM paper_positions WHERE lane_id = %s AND quantity > 0", (lane_id,))
                            active_cnt_row = cur.fetchone()
                            active_cnt = active_cnt_row["cnt"] if active_cnt_row else 0
                            if active_cnt >= max_open_positions:
                                return self._reject_intent_postgres(cur, intent, "OPEN_POSITION_LIMIT", extra_reasons=reasons_list)

                        # G2 Slippage Dimensional Math
                        slippage_factor = 1.0 + self.slippage_rate if intent.side == "BUY" else 1.0 - self.slippage_rate
                        adjusted_fill_price = fill_price * slippage_factor
                        
                        # Fees & slippage calculations
                        quantity = intent.quantity
                        fee = quantity * fill_price * self.fee_rate
                        slippage_cost = quantity * abs(adjusted_fill_price - fill_price)
                        total_cost = (quantity * adjusted_fill_price) + fee

                        # Validate
                        if intent.side == "BUY" and cash < total_cost:
                            return self._reject_intent_postgres(cur, intent, "INSUFFICIENT_CASH", extra_reasons=reasons_list)
                        if intent.side == "SELL" and current_qty < quantity:
                            return self._reject_intent_postgres(cur, intent, "INSUFFICIENT_POSITION", extra_reasons=reasons_list)

                        # Perform state updates
                        if intent.side == "BUY":
                            new_cash = cash - total_cost
                            new_qty = current_qty + quantity
                            new_entry = ((current_qty * current_entry) + (quantity * adjusted_fill_price)) / new_qty
                            
                            # G1 Wiring Correction: persist intent.stop_price into stop_loss_price
                            cur.execute(
                                "INSERT INTO paper_positions (lane_id, symbol, quantity, entry_price, stop_loss_price, take_profit_price) "
                                "VALUES (%s, %s, %s, %s, %s, %s) "
                                "ON CONFLICT (lane_id, symbol) DO UPDATE SET quantity = EXCLUDED.quantity, entry_price = EXCLUDED.entry_price, stop_loss_price = EXCLUDED.stop_loss_price, take_profit_price = EXCLUDED.take_profit_price",
                                (lane_id, intent.symbol, new_qty, new_entry, intent.stop_price, intent.take_profit_price)
                            )
                        else: # SELL
                            new_cash = cash + (quantity * adjusted_fill_price) - fee
                            new_qty = current_qty - quantity
                            if new_qty <= 0.00001:
                                cur.execute("DELETE FROM paper_positions WHERE lane_id = %s AND symbol = %s", (lane_id, intent.symbol))
                            else:
                                cur.execute(
                                    "UPDATE paper_positions SET quantity = %s WHERE lane_id = %s AND symbol = %s",
                                    (new_qty, lane_id, intent.symbol)
                                )

                        # G4 Multi-Asset MTM
                        cur.execute("SELECT symbol, quantity FROM paper_positions WHERE lane_id = %s", (lane_id,))
                        all_pos = cur.fetchall()
                        mtm_value = 0.0
                        for p in all_pos:
                            sym = p["symbol"]
                            qty = float(p["quantity"])
                            
                            # Fetch fresh mark
                            cur.execute("SELECT price, updated_at FROM market_marks WHERE symbol = %s", (sym,))
                            mark_row = cur.fetchone()
                            if not mark_row:
                                raise ValueError(f"No fresh market mark found for {sym}")
                            
                            # Check age <= 90s
                            mark_time = mark_row["updated_at"]
                            if abs((datetime.now(UTC) - mark_time).total_seconds()) > 90.0:
                                raise ValueError(f"Stale market mark for {sym}")
                                
                            mtm_value += qty * float(mark_row["price"])

                        new_equity = new_cash + mtm_value
                        cur.execute("UPDATE paper_balances SET cash = %s, equity = %s WHERE lane_id = %s", (new_cash, new_equity, lane_id))

                        # Save Intent & Result (atomically saving H2 reason codes)
                        broker_id = f"paper-{uuid.uuid4()}"
                        cur.execute(
                            "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, intent.time_exit_at, intent.client_order_id, intent.expires_at)
                        )
                        final_reasons = reasons_list + ["EXECUTED_PAPER"]
                        cur.execute(
                            "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (intent.execution_intent_id, broker_id, "FILLED", intent.quantity, intent.quantity, adjusted_fill_price, fee, slippage_cost, json.dumps(final_reasons))
                        )
                        self._write_arena_snapshot(None, cur, lane_id, intent.symbol, adjusted_fill_price, fee, is_sqlite=False)

            return ExecutionResult(
                execution_intent_id=intent.execution_intent_id,
                broker_order_id=broker_id,
                status="FILLED",
                requested_quantity=intent.quantity,
                filled_quantity=intent.quantity,
                average_fill_price=adjusted_fill_price,
                fee=fee,
                slippage=slippage_cost,
                updated_at=datetime.now(UTC),
                reason_codes=final_reasons
            )
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def check_and_trigger_stops(self, lane_id: str, symbol: str, current_price: float) -> ExecutionResult | None:
        """
        Check stop loss / take profit triggers, executing protective exits instantly (HST-01 / HST-03 / H2).
        """
        pos = self.get_position(lane_id, symbol)
        if not pos or pos["quantity"] <= 0:
            return None

        trigger_exit = False
        reason = ""
        
        # Stop loss logic (long position stop) - stop_loss_price is G1 stop loss price
        if pos["stop_loss_price"] and current_price <= pos["stop_loss_price"]:
            trigger_exit = True
            reason = "STOP_LOSS_TRIGGERED"
        # Take profit logic
        elif pos["take_profit_price"] and current_price >= pos["take_profit_price"]:
            trigger_exit = True
            reason = "TAKE_PROFIT_TRIGGERED"

        if trigger_exit:
            # Generate instant PROTECTIVE_EXIT ExecutionIntent bypassing cooldowns
            intent_id = str(uuid.uuid4())
            intent = ExecutionIntent(
                execution_intent_id=intent_id,
                risk_decision_id=str(uuid.uuid4()),
                mode="paper",
                symbol=symbol,
                action="CLOSE",
                side="SELL",
                quantity=pos["quantity"],
                order_type="MARKET",
                client_order_id=f"stop-trigger-{intent_id}",
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC)
            )
            # Pass protective reason as an atomic database reason (Blocker H2)
            result = self.execute_intent(lane_id, intent, current_price, reasons=[reason])
            return result
        return None

    def _reject_intent(self, conn, intent: ExecutionIntent, reason: str, extra_reasons: list[str] | None = None) -> ExecutionResult:
        broker_id = "rejected-order"
        conn.execute(
            "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, str(intent.time_exit_at), intent.client_order_id, str(intent.expires_at))
        )
        final_reasons = (extra_reasons or []) + [reason]
        conn.execute(
            "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, fee, slippage, reason_codes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (intent.execution_intent_id, broker_id, "REJECTED", intent.quantity, 0.0, 0.0, 0.0, json.dumps(final_reasons))
        )
        if self.db.use_sqlite:
            self.db.sqlite_conn.commit()
        return ExecutionResult(
            execution_intent_id=intent.execution_intent_id,
            broker_order_id=broker_id,
            status="REJECTED",
            requested_quantity=intent.quantity,
            filled_quantity=0.0,
            updated_at=datetime.now(UTC),
            reason_codes=final_reasons
        )

    def _reject_intent_postgres(self, cur, intent: ExecutionIntent, reason: str, extra_reasons: list[str] | None = None) -> ExecutionResult:
        broker_id = "rejected-order"
        cur.execute(
            "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, intent.time_exit_at, intent.client_order_id, intent.expires_at)
        )
        final_reasons = (extra_reasons or []) + [reason]
        cur.execute(
            "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, fee, slippage, reason_codes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (intent.execution_intent_id, broker_id, "REJECTED", intent.quantity, 0.0, 0.0, 0.0, json.dumps(final_reasons))
        )
        return ExecutionResult(
            execution_intent_id=intent.execution_intent_id,
            broker_order_id=broker_id,
            status="REJECTED",
            requested_quantity=intent.quantity,
            filled_quantity=0.0,
            updated_at=datetime.now(UTC),
            reason_codes=final_reasons
        )

    def _write_arena_snapshot(self, conn, cur, lane_id: str, symbol: str, current_price: float, fee_paid: float, is_sqlite: bool):
        # 1. Fetch current balance
        if is_sqlite:
            bal_row = conn.execute("SELECT cash, equity FROM paper_balances WHERE lane_id = ?", (lane_id,)).fetchone()
            cash = bal_row[0] if bal_row else 0.0
            equity = bal_row[1] if bal_row else 0.0
        else:
            cur.execute("SELECT cash, equity FROM paper_balances WHERE lane_id = %s", (lane_id,))
            bal_row = cur.fetchone()
            cash = float(bal_row["cash"]) if bal_row else 0.0
            equity = float(bal_row["equity"]) if bal_row else 0.0

        # 2. Fetch all current positions
        if is_sqlite:
            all_pos = conn.execute("SELECT symbol, quantity, entry_price FROM paper_positions WHERE lane_id = ?", (lane_id,)).fetchall()
            positions = [{"symbol": p[0], "quantity": p[1], "entry_price": p[2]} for p in all_pos]
        else:
            cur.execute("SELECT symbol, quantity, entry_price FROM paper_positions WHERE lane_id = %s", (lane_id,))
            all_pos = cur.fetchall()
            positions = [{"symbol": p["symbol"], "quantity": float(p["quantity"]), "entry_price": float(p["entry_price"])} for p in all_pos]

        # 3. Calculate unrealized PnL
        unrealized_pnl = 0.0
        for p in positions:
            if p["symbol"] == symbol:
                price = current_price
            else:
                if is_sqlite:
                    m_row = conn.execute("SELECT price FROM market_marks WHERE symbol = ?", (p["symbol"],)).fetchone()
                    price = m_row[0] if m_row else p["entry_price"]
                else:
                    cur.execute("SELECT price FROM market_marks WHERE symbol = %s", (p["symbol"],))
                    m_row = cur.fetchone()
                    price = float(m_row["price"]) if m_row else p["entry_price"]
            unrealized_pnl += p["quantity"] * (price - p["entry_price"])

        # 4. Fetch initial cash/equity, previous fees, and historical peak equity
        if is_sqlite:
            first_row = conn.execute("SELECT equity FROM arena_snapshots WHERE lane_id = ? ORDER BY id ASC LIMIT 1", (lane_id,)).fetchone()
            initial_equity = first_row[0] if first_row else equity
            
            last_row = conn.execute("SELECT fees FROM arena_snapshots WHERE lane_id = ? ORDER BY id DESC LIMIT 1", (lane_id,)).fetchone()
            prev_fees = last_row[0] if last_row else 0.0
            
            max_row = conn.execute("SELECT MAX(equity) FROM arena_snapshots WHERE lane_id = ?", (lane_id,)).fetchone()
            historical_peak = max(max_row[0] or 0.0, equity)
        else:
            cur.execute("SELECT equity FROM arena_snapshots WHERE lane_id = %s ORDER BY id ASC LIMIT 1", (lane_id,))
            first_row = cur.fetchone()
            initial_equity = float(first_row["equity"]) if first_row else equity
            
            cur.execute("SELECT fees FROM arena_snapshots WHERE lane_id = %s ORDER BY id DESC LIMIT 1", (lane_id,))
            last_row = cur.fetchone()
            prev_fees = float(last_row["fees"]) if last_row else 0.0
            
            cur.execute("SELECT MAX(equity) AS max_equity FROM arena_snapshots WHERE lane_id = %s", (lane_id,))
            max_row = cur.fetchone()
            historical_peak = max(float(max_row["max_equity"] or 0.0), equity)

        fees = prev_fees + fee_paid
        realized_pnl = equity - initial_equity - unrealized_pnl
        max_drawdown_pct = ((historical_peak - equity) / historical_peak) * 100.0 if historical_peak > 0.0 else 0.0
        if max_drawdown_pct < 0.0:
            max_drawdown_pct = 0.0

        # 5. Insert snapshot
        if is_sqlite:
            conn.execute(
                "INSERT INTO arena_snapshots (lane_id, equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lane_id, equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct)
            )
        else:
            cur.execute(
                "INSERT INTO arena_snapshots (lane_id, equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (lane_id, equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct)
            )


class CoinbaseLiveExecutor:
    """
    Strictly disarmed Coinbase Live Executor (Blocker D).
    Raises errors or fails gracefully on real order attempts as safety safeguard.
    """
    def __init__(self):
        self.armed = False # Never arm in TASK-0002

    def execute_intent(self, intent: ExecutionIntent) -> ExecutionResult:
        # Strict fail-closed disarmed assertion (Safety Contract)
        raise RuntimeError("CRITICAL_SAFETY_FAILURE: Coinbase Live Executor is strictly disarmed.")
