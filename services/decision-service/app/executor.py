import os
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, UTC
from typing import Literal, Any
import sqlite3

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
                self.db.sqlite_conn.commit()
            else:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO paper_balances (lane_id, equity, cash) VALUES (%s, %s, %s) "
                            "ON CONFLICT (lane_id) DO NOTHING",
                            (lane_id, initial_cash, initial_cash)
                        )
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def update_market_mark(self, symbol: str, price: float):
        """
        Maintains fresh per-symbol market marks in the database (Blocker G4).
        """
        conn = self.db.get_cursor()
        try:
            now_str = datetime.now(UTC).isoformat()
            if self.db.use_sqlite:
                conn.execute(
                    "INSERT OR REPLACE INTO market_marks (symbol, price, updated_at) VALUES (?, ?, ?)",
                    (symbol, price, now_str)
                )
                self.db.sqlite_conn.commit()
            else:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO market_marks (symbol, price, updated_at) VALUES (%s, %s, %s) "
                            "ON CONFLICT (symbol) DO UPDATE SET price = EXCLUDED.price, updated_at = EXCLUDED.updated_at",
                            (symbol, price, datetime.now(UTC))
                        )
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def get_market_mark(self, symbol: str) -> dict[str, Any] | None:
        conn = self.db.get_cursor()
        try:
            if self.db.use_sqlite:
                row = conn.execute("SELECT price, updated_at FROM market_marks WHERE symbol = ?", (symbol,)).fetchone()
                if row:
                    # SQLite start as ISO string
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

    def execute_intent(self, lane_id: str, intent: ExecutionIntent, fill_price: float, max_open_positions: int | None = None) -> ExecutionResult:
        """
        Atomically executes an ExecutionIntent against the database state (HST-02 / HST-09 / HST-06).
        Enforces G1 stop-loss wiring, G2 slippage dimensional scaling, and G3 serializable safety.
        """
        # Save fill price immediately as the fresh market mark for this symbol
        self.update_market_mark(intent.symbol, fill_price)

        conn = self.db.get_cursor()
        try:
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
                        "SELECT broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage FROM execution_results WHERE execution_intent_id = ?",
                        (intent_id,)
                    ).fetchone()
                    if res_row:
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
                            reason_codes=["IDEMPOTENT_REPLAY"]
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
                        return self._reject_intent(conn, intent, "OPEN_POSITION_LIMIT")

                # G2 Slippage Dimensional Math: adjusted unit price is independent of quantity (no quadratic cost!)
                slippage_factor = 1.0 + self.slippage_rate if intent.side == "BUY" else 1.0 - self.slippage_rate
                adjusted_fill_price = fill_price * slippage_factor
                
                # Fees & slippage calculations
                quantity = intent.quantity
                fee = quantity * fill_price * self.fee_rate
                slippage_cost = quantity * abs(adjusted_fill_price - fill_price)
                total_cost = (quantity * adjusted_fill_price) + fee

                # Validate
                if intent.side == "BUY" and cash < total_cost:
                    return self._reject_intent(conn, intent, "INSUFFICIENT_CASH")
                if intent.side == "SELL" and current_qty < quantity:
                    return self._reject_intent(conn, intent, "INSUFFICIENT_POSITION")

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

                # G4 Multi-Asset MTM: evaluate each position using its own fresh mark
                all_pos = conn.execute("SELECT symbol, quantity FROM paper_positions WHERE lane_id = ?", (lane_id,)).fetchall()
                mtm_value = 0.0
                for p in all_pos:
                    sym = p["symbol"]
                    qty = p["quantity"]
                    
                    # Fetch fresh mark
                    mark_row = conn.execute("SELECT price, updated_at FROM market_marks WHERE symbol = ?", (sym,)).fetchone()
                    if not mark_row:
                        raise ValueError(f"No fresh market mark found for {sym}")
                    
                    # Check age <= 90s (G4)
                    try:
                        mark_time = datetime.fromisoformat(mark_row["updated_at"])
                    except ValueError:
                        mark_time = datetime.now(UTC)
                    if abs((datetime.now(UTC) - mark_time).total_seconds()) > 90.0:
                        raise ValueError(f"Stale market mark for {sym} (Age: {abs((datetime.now(UTC) - mark_time).total_seconds())}s)")
                        
                    mtm_value += qty * mark_row["price"]

                new_equity = new_cash + mtm_value
                conn.execute("UPDATE paper_balances SET cash = ?, equity = ? WHERE lane_id = ?", (new_cash, new_equity, lane_id))

                # Insert Intent & Result
                broker_id = f"paper-{uuid.uuid4()}"
                conn.execute(
                    "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, str(intent.time_exit_at), intent.client_order_id, str(intent.expires_at))
                )
                conn.execute(
                    "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (intent.execution_intent_id, broker_id, "FILLED", intent.quantity, intent.quantity, adjusted_fill_price, fee, slippage_cost, "[]")
                )
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
                                "SELECT broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage FROM execution_results WHERE execution_intent_id = %s",
                                (intent_id,)
                            )
                            res_row = cur.fetchone()
                            if res_row:
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
                                    reason_codes=["IDEMPOTENT_REPLAY"]
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
                                return self._reject_intent_postgres(cur, intent, "OPEN_POSITION_LIMIT")

                        # G2 Slippage Dimensional Math: adjusted unit price is independent of quantity
                        slippage_factor = 1.0 + self.slippage_rate if intent.side == "BUY" else 1.0 - self.slippage_rate
                        adjusted_fill_price = fill_price * slippage_factor
                        
                        # Fees & slippage calculations
                        quantity = intent.quantity
                        fee = quantity * fill_price * self.fee_rate
                        slippage_cost = quantity * abs(adjusted_fill_price - fill_price)
                        total_cost = (quantity * adjusted_fill_price) + fee

                        # Validate
                        if intent.side == "BUY" and cash < total_cost:
                            return self._reject_intent_postgres(cur, intent, "INSUFFICIENT_CASH")
                        if intent.side == "SELL" and current_qty < quantity:
                            return self._reject_intent_postgres(cur, intent, "INSUFFICIENT_POSITION")

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

                        # G4 Multi-Asset MTM: evaluate each position using its own fresh mark
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
                            
                            # Check age <= 90s (G4)
                            mark_time = mark_row["updated_at"]
                            if abs((datetime.now(UTC) - mark_time).total_seconds()) > 90.0:
                                raise ValueError(f"Stale market mark for {sym} (Age: {abs((datetime.now(UTC) - mark_time).total_seconds())}s)")
                                
                            mtm_value += qty * float(mark_row["price"])

                        new_equity = new_cash + mtm_value
                        cur.execute("UPDATE paper_balances SET cash = %s, equity = %s WHERE lane_id = %s", (new_cash, new_equity, lane_id))

                        # Save Intent & Result
                        broker_id = f"paper-{uuid.uuid4()}"
                        cur.execute(
                            "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, intent.time_exit_at, intent.client_order_id, intent.expires_at)
                        )
                        cur.execute(
                            "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, average_fill_price, fee, slippage, reason_codes) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb)",
                            (intent.execution_intent_id, broker_id, "FILLED", intent.quantity, intent.quantity, adjusted_fill_price, fee, slippage_cost)
                        )

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
                reason_codes=["EXECUTED_PAPER"]
            )
        finally:
            if not self.db.use_sqlite:
                conn.close()

    def check_and_trigger_stops(self, lane_id: str, symbol: str, current_price: float) -> ExecutionResult | None:
        """
        Check stop loss / take profit triggers, executing protective exits instantly (HST-01 / HST-03).
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
            result = self.execute_intent(lane_id, intent, current_price)
            result.reason_codes.append(reason)
            return result
        return None

    def _reject_intent(self, conn, intent: ExecutionIntent, reason: str) -> ExecutionResult:
        broker_id = "rejected-order"
        conn.execute(
            "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, str(intent.time_exit_at), intent.client_order_id, str(intent.expires_at))
        )
        conn.execute(
            "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, fee, slippage, reason_codes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (intent.execution_intent_id, broker_id, "REJECTED", intent.quantity, 0.0, 0.0, 0.0, f'["{reason}"]')
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
            reason_codes=[reason]
        )

    def _reject_intent_postgres(self, cur, intent: ExecutionIntent, reason: str) -> ExecutionResult:
        broker_id = "rejected-order"
        cur.execute(
            "INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, limit_price, stop_price, take_profit_price, time_exit_at, client_order_id, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (intent.execution_intent_id, intent.risk_decision_id, intent.mode, intent.symbol, intent.action, intent.side, intent.quantity, intent.order_type, intent.limit_price, intent.stop_price, intent.take_profit_price, intent.time_exit_at, intent.client_order_id, intent.expires_at)
        )
        cur.execute(
            "INSERT INTO execution_results (execution_intent_id, broker_order_id, status, requested_quantity, filled_quantity, fee, slippage, reason_codes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (intent.execution_intent_id, broker_id, "REJECTED", intent.quantity, 0.0, 0.0, 0.0, f'["{reason}"]')
        )
        return ExecutionResult(
            execution_intent_id=intent.execution_intent_id,
            broker_order_id=broker_id,
            status="REJECTED",
            requested_quantity=intent.quantity,
            filled_quantity=0.0,
            updated_at=datetime.now(UTC),
            reason_codes=[reason]
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
