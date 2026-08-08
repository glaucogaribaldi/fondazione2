import os
import sys
import uuid
import json
import math
import hashlib
import sqlite3
import psycopg2
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

from .products import registry, get_conversion_rate_to_usdc, get_product_mapping
from .config import load_risk_settings, load_lane_settings

class PortfolioSnapshot(BaseModel):
    equity: float
    cash: Dict[str, float]  # Currency -> cash
    reserved: Dict[str, float]  # Currency -> reserved cash
    positions: Dict[str, Dict[str, Any]]  # Symbol -> position details
    gross_exposure: float
    net_exposure: float
    concentration: float
    drawdown: float
    peak_equity: float
    version: int
    digest: str
    valuation_valid: bool = True
    valuation_reason_codes: List[str] = Field(default_factory=list)
    stale_missing_marks: List[str] = Field(default_factory=list)
    stale_missing_conversion_paths: List[str] = Field(default_factory=list)
    symbol_exposures: Dict[str, float] = Field(default_factory=dict)
    risk_budget_consumed: float = 0.0

class AllocationProposal(BaseModel):
    proposal_id: str
    symbol: str
    action: str  # OPEN, ADD, REDUCE, CLOSE
    requested_risk_fraction: float  # fraction of equity, e.g. 0.10
    requested_notional: float
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

class AllocationResult(BaseModel):
    allocation_id: str
    proposal_id: str
    symbol: str
    decision: Literal["APPROVE", "MODIFY_DOWN", "REJECT"]
    approved_notional: float
    approved_quantity: float
    reserved_capital: float
    reason_codes: List[str]
    portfolio_version: int
    portfolio_digest: str


class PortfolioEngine:
    """
    PostgreSQL/SQLite-backed unified shared-capital Portfolio Engine (TASK-0006).
    Enforces risk budgets, exposure limits, concentration limits, and atomic reservations.
    """
    def __init__(self, db_url: str | None = None, base_currency: str = "USDC"):
        from .executor import DatabaseConnection
        self.db = DatabaseConnection(db_url)
        self.base_currency = base_currency.upper()

    def _get_db_cursor_context(self, cur=None):
        """Unified cursor context manager for SQLite/PostgreSQL."""
        if cur is not None:
            class PassedCursorContext:
                def __init__(self, c):
                    self.c = c
                def __enter__(self):
                    return self.c
                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass
            return PassedCursorContext(cur)

        if self.db.use_sqlite:
            class SQLiteContext:
                def __init__(self, conn):
                    self.conn = conn
                    self.cur = None
                def __enter__(self):
                    self.cur = self.conn.cursor()
                    return self.cur
                def __exit__(self, exc_type, exc_val, exc_tb):
                    try:
                        if exc_type is None:
                            self.conn.commit()
                    finally:
                        if self.cur:
                            self.cur.close()
            return SQLiteContext(self.db.sqlite_conn)
        else:
            class PostgresContext:
                def __init__(self, db_url):
                    self.db_url = db_url
                    self.conn = None
                    self.cur = None
                def __enter__(self):
                    import psycopg2
                    from psycopg2.extras import RealDictCursor
                    self.conn = psycopg2.connect(self.db_url)
                    self.conn.set_session(isolation_level='SERIALIZABLE', autocommit=False)
                    self.cur = self.conn.cursor(cursor_factory=RealDictCursor)
                    return self.cur
                def __exit__(self, exc_type, exc_val, exc_tb):
                    try:
                        if exc_type is not None:
                            self.conn.rollback()
                        else:
                            self.conn.commit()
                    finally:
                        if self.cur:
                            self.cur.close()
                        if self.conn:
                            self.conn.close()
            return PostgresContext(self.db.db_url)

    # ────────────────────────── Core Portfolio Operations ──────────────────────────

    def initialize_portfolio(self, initial_cash_usdc: float):
        """
        Initializes the shared capital portfolio with a base USDC cash balance only if not already present (Requirement D1).
        """
        with self._get_db_cursor_context() as cur:
            # 1. Initialize cash
            if self.db.use_sqlite:
                cur.execute("""
                    INSERT INTO portfolio_cash (currency, cash, reserved) 
                    VALUES (?, ?, 0.0)
                    ON CONFLICT(currency) DO NOTHING
                """, (self.base_currency, initial_cash_usdc))
                cur.execute(
                    "INSERT OR IGNORE INTO portfolio_metadata (key, value) VALUES ('peak_equity', ?)",
                    (str(initial_cash_usdc),)
                )
                cur.execute(
                    "INSERT OR IGNORE INTO portfolio_metadata (key, value) VALUES ('version', '0')"
                )
                cur.execute(
                    "INSERT OR IGNORE INTO portfolio_metadata (key, value) VALUES ('digest', '')"
                )
            else:
                cur.execute("""
                    INSERT INTO portfolio_cash (currency, cash, reserved) 
                    VALUES (%s, %s, 0)
                    ON CONFLICT (currency) DO NOTHING
                """, (self.base_currency, initial_cash_usdc))
                
                cur.execute("""
                    INSERT INTO portfolio_metadata (key, value) 
                    VALUES ('peak_equity', %s)
                    ON CONFLICT (key) DO NOTHING
                """, (str(initial_cash_usdc),))
                
                cur.execute("""
                    INSERT INTO portfolio_metadata (key, value) 
                    VALUES ('version', '0')
                    ON CONFLICT (key) DO NOTHING
                """)
                
                cur.execute("""
                    INSERT INTO portfolio_metadata (key, value) 
                    VALUES ('digest', '')
                    ON CONFLICT (key) DO NOTHING
                """)

    def reset_portfolio_explicit(self, initial_cash_usdc: float):
        """
        Explicitly resets the portfolio to its initial cash and clears all positions (Requirement D1).
        """
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute("DELETE FROM portfolio_cash")
                cur.execute("DELETE FROM portfolio_positions")
                cur.execute("DELETE FROM portfolio_allocations")
                cur.execute("DELETE FROM portfolio_metadata")
            else:
                cur.execute("TRUNCATE TABLE portfolio_cash CASCADE")
                cur.execute("TRUNCATE TABLE portfolio_positions CASCADE")
                cur.execute("TRUNCATE TABLE portfolio_allocations CASCADE")
                cur.execute("TRUNCATE TABLE portfolio_metadata CASCADE")
        self.initialize_portfolio(initial_cash_usdc)

    def load_portfolio_snapshot(self, get_mark_func, cur=None) -> PortfolioSnapshot:
        """
        Loads the PostgreSQL-backed versioned portfolio state and computes full MTM valuation (Requirement #1 / #2 / #10 / D2).
        """
        if cur is None:
            ctx = self._get_db_cursor_context()
        else:
            class DummyContext:
                def __init__(self, c):
                    self.c = c
                def __enter__(self):
                    return self.c
                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass
            ctx = DummyContext(cur)

        with ctx as active_cur:
            # 1. Load Cash Balances
            cash_balances = {}
            reserved_balances = {}
            if self.db.use_sqlite:
                rows = active_cur.execute("SELECT currency, cash, reserved FROM portfolio_cash").fetchall()
                for r in rows:
                    cash_balances[r[0]] = float(r[1])
                    reserved_balances[r[0]] = float(r[2])
            else:
                active_cur.execute("SELECT currency, cash, reserved FROM portfolio_cash")
                rows = active_cur.fetchall()
                for r in rows:
                    cash_balances[r["currency"]] = float(r["cash"])
                    reserved_balances[r["currency"]] = float(r["reserved"])

            # 2. Load Positions
            positions = {}
            if self.db.use_sqlite:
                rows = active_cur.execute("SELECT symbol, quantity, entry_price, realized_pnl, unrealized_pnl, stop_loss_price, take_profit_price FROM portfolio_positions").fetchall()
                for r in rows:
                    positions[r[0]] = {
                        "symbol": r[0],
                        "quantity": float(r[1]),
                        "entry_price": float(r[2]),
                        "realized_pnl": float(r[3]),
                        "unrealized_pnl": float(r[4]),
                        "stop_loss_price": float(r[5]) if r[5] is not None else None,
                        "take_profit_price": float(r[6]) if r[6] is not None else None
                    }
            else:
                active_cur.execute("SELECT symbol, quantity, entry_price, realized_pnl, unrealized_pnl, stop_loss_price, take_profit_price FROM portfolio_positions")
                rows = active_cur.fetchall()
                for r in rows:
                    positions[r["symbol"]] = {
                        "symbol": r["symbol"],
                        "quantity": float(r["quantity"]),
                        "entry_price": float(r["entry_price"]),
                        "realized_pnl": float(r["realized_pnl"]),
                        "unrealized_pnl": float(r["unrealized_pnl"]),
                        "stop_loss_price": float(r["stop_loss_price"]) if r["stop_loss_price"] is not None else None,
                        "take_profit_price": float(r["take_profit_price"]) if r["take_profit_price"] is not None else None
                    }

            # 3. Load Metadata
            metadata = {}
            if self.db.use_sqlite:
                rows = active_cur.execute("SELECT key, value FROM portfolio_metadata").fetchall()
                for r in rows:
                    metadata[r[0]] = r[1]
            else:
                active_cur.execute("SELECT key, value FROM portfolio_metadata")
                rows = active_cur.fetchall()
                for r in rows:
                    metadata[r["key"]] = r["value"]

            version = int(metadata.get("version", 0))
            peak_equity = float(metadata.get("peak_equity", 10000.0))

            # 3b. Load Pending Allocations (Requirement E2)
            pending_allocs = []
            if self.db.use_sqlite:
                rows = active_cur.execute(
                    "SELECT symbol, approved_notional, approved_risk_fraction, action, allocation_id FROM portfolio_allocations WHERE status = 'PENDING'"
                ).fetchall()
                for r in rows:
                    pending_allocs.append({
                        "symbol": r[0],
                        "approved_notional": float(r[1]),
                        "approved_risk_fraction": float(r[2]),
                        "action": r[3],
                        "allocation_id": r[4]
                    })
            else:
                active_cur.execute(
                    "SELECT symbol, approved_notional, approved_risk_fraction, action, allocation_id FROM portfolio_allocations WHERE status = 'PENDING'"
                )
                rows = active_cur.fetchall()
                for r in rows:
                    pending_allocs.append({
                        "symbol": r["symbol"],
                        "approved_notional": float(r["approved_notional"]),
                        "approved_risk_fraction": float(r["approved_risk_fraction"]),
                        "action": r["action"],
                        "allocation_id": r["allocation_id"]
                    })

        # 4. Perform Multi-Quote MTM Valuation & Exposure Calculation
        base_currency_cash = cash_balances.get(self.base_currency, 0.0)
        converted_other_cash = 0.0
        
        stale_missing_marks = []
        stale_missing_conversion_paths = []
        valuation_reason_codes = []

        for currency, val in cash_balances.items():
            if currency == self.base_currency:
                continue
            rate = get_conversion_rate_to_usdc(currency, get_mark_func)
            if rate is None:
                stale_missing_conversion_paths.append(currency)
                valuation_reason_codes.append(f"MISSING_CONVERSION_PATH_{currency}")
            else:
                converted_other_cash += val * rate

        # Calculate position market values and unrealized PnL in base currency
        marked_position_values = 0.0
        active_position_exposures = {}

        for sym, pos in positions.items():
            qty = pos["quantity"]
            if qty <= 0.0:
                continue

            p_mapping = get_product_mapping(sym)
            quote = p_mapping.canonical_symbol.split("/")[-1].upper()

            # Retrieve fresh market mark
            mark_price = get_mark_func(sym)
            if mark_price is None or mark_price <= 0.0:
                stale_missing_marks.append(sym)
                valuation_reason_codes.append(f"STALE_OR_MISSING_MARK_{sym}")
                mark_price = pos["entry_price"]  # Safe fallback for exits / protective runs

            pos_val_quote = qty * mark_price
            rate = get_conversion_rate_to_usdc(quote, get_mark_func)
            
            pos_val_base = pos_val_quote
            if rate is None:
                stale_missing_conversion_paths.append(quote)
                if f"MISSING_CONVERSION_PATH_{quote}" not in valuation_reason_codes:
                    valuation_reason_codes.append(f"MISSING_CONVERSION_PATH_{quote}")
            else:
                pos_val_base = pos_val_quote * rate

            # Update position unrealized PnL in-memory
            pos["unrealized_pnl"] = qty * (mark_price - pos["entry_price"])

            marked_position_values += pos_val_base
            active_position_exposures[sym] = pos_val_base

        portfolio_equity = base_currency_cash + converted_other_cash + marked_position_values

        # Build symbol exposures mapping including PENDING allocations (Requirement E2)
        symbol_exposures = {}
        for sym, val in active_position_exposures.items():
            symbol_exposures[sym] = val

        total_pending_risk_fraction = 0.0
        for alloc in pending_allocs:
            sym = alloc["symbol"]
            approved_notional = alloc["approved_notional"]
            action = alloc["action"]
            
            if action in ["OPEN", "ADD"]:
                p_mapping = get_product_mapping(sym)
                quote = p_mapping.canonical_symbol.split("/")[-1].upper()
                rate = get_conversion_rate_to_usdc(quote, get_mark_func) or 1.0
                pending_val_base = approved_notional * rate
                symbol_exposures[sym] = symbol_exposures.get(sym, 0.0) + pending_val_base
                total_pending_risk_fraction += alloc["approved_risk_fraction"]

        # Calculate final exposures incorporating PENDING allocations (Requirement E2)
        gross_exposure = sum(abs(v) for v in symbol_exposures.values())
        net_exposure = sum(v for v in symbol_exposures.values())
        max_pos_val_base = max(symbol_exposures.values()) if symbol_exposures else 0.0

        valuation_valid = (len(stale_missing_marks) == 0 and len(stale_missing_conversion_paths) == 0)

        # Track peak_equity and drawdown (D2 Update: only if valuation is valid!)
        if valuation_valid:
            peak_equity = max(peak_equity, portfolio_equity)
            drawdown = ((peak_equity - portfolio_equity) / peak_equity) * 100.0 if peak_equity > 0.0 else 0.0
            # Save peak_equity if it updated
            if peak_equity > float(metadata.get("peak_equity", 0.0)):
                self._save_metadata("peak_equity", str(peak_equity))
        else:
            # Valuation is invalid: do not update or save peak_equity!
            stored_peak_equity = float(metadata.get("peak_equity", 10000.0))
            drawdown = ((stored_peak_equity - portfolio_equity) / stored_peak_equity) * 100.0 if stored_peak_equity > 0.0 else 0.0

        concentration = (max_pos_val_base / portfolio_equity) * 100.0 if portfolio_equity > 0.0 else 0.0

        # Calculate risk budget consumed (Requirement E2)
        active_risk_fraction = sum(
            active_position_exposures[sym] / portfolio_equity
            for sym in active_position_exposures
        ) if portfolio_equity > 0.0 else 0.0
        risk_budget_consumed = active_risk_fraction + total_pending_risk_fraction

        # Build stable, deterministic state version digest
        digest_payload = {
            "version": version,
            "equity": round(portfolio_equity, 4),
            "cash": {c: round(v, 4) for c, v in sorted(cash_balances.items())},
            "positions": {s: {"qty": round(p["quantity"], 4), "entry": round(p["entry_price"], 4)} for s, p in sorted(positions.items())}
        }
        state_digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode("utf-8")).hexdigest()

        return PortfolioSnapshot(
            equity=portfolio_equity,
            cash=cash_balances,
            reserved=reserved_balances,
            positions=positions,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            concentration=concentration,
            drawdown=drawdown,
            peak_equity=peak_equity,
            version=version,
            digest=state_digest,
            valuation_valid=valuation_valid,
            valuation_reason_codes=valuation_reason_codes,
            stale_missing_marks=stale_missing_marks,
            stale_missing_conversion_paths=stale_missing_conversion_paths,
            symbol_exposures=symbol_exposures,
            risk_budget_consumed=risk_budget_consumed
        )

    def _save_metadata(self, key: str, value: str):
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute("INSERT OR REPLACE INTO portfolio_metadata (key, value) VALUES (?, ?)", (key, value))
            else:
                cur.execute("""
                    INSERT INTO portfolio_metadata (key, value) 
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key, value))

    def _increment_version(self) -> int:
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                row = cur.execute("SELECT value FROM portfolio_metadata WHERE key = 'version'").fetchone()
                new_v = int(row[0]) + 1 if row else 1
                cur.execute("INSERT OR REPLACE INTO portfolio_metadata (key, value) VALUES ('version', ?)", (str(new_v),))
            else:
                cur.execute("SELECT value FROM portfolio_metadata WHERE key = 'version' FOR UPDATE")
                row = cur.fetchone()
                new_v = int(row["value"]) + 1 if row else 1
                cur.execute("""
                    INSERT INTO portfolio_metadata (key, value) 
                    VALUES ('version', %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (str(new_v),))
            return new_v

    # ────────────────────────── Capital Reservation & Commits ──────────────────────────

    def reserve_capital(self, allocation_id: str, currency: str, amount: float) -> bool:
        """
        Atomically reserves the requested capital amount in the specified quote currency (Requirement #4 / #6).
        """
        currency = currency.upper()
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                # SQLite locks the database on write transactions. We load and update
                row = cur.execute("SELECT cash, reserved FROM portfolio_cash WHERE currency = ?", (currency,)).fetchone()
                if not row:
                    return False
                cash, reserved = float(row[0]), float(row[1])
                if cash - reserved >= amount:
                    cur.execute(
                        "UPDATE portfolio_cash SET reserved = reserved + ? WHERE currency = ?",
                        (amount, currency)
                    )
                    return True
            else:
                cur.execute("SELECT cash, reserved FROM portfolio_cash WHERE currency = %s FOR UPDATE", (currency,))
                row = cur.fetchone()
                if not row:
                    return False
                cash, reserved = float(row["cash"]), float(row["reserved"])
                if cash - reserved >= amount:
                    cur.execute(
                        "UPDATE portfolio_cash SET reserved = reserved + %s WHERE currency = %s",
                        (amount, currency)
                    )
                    return True
        return False

    def release_reservation(self, allocation_id: str, currency: str, amount: float, cur=None):
        """
        Releases reserved capital back to available cash (Requirement #4).
        """
        currency = currency.upper()
        with self._get_db_cursor_context(cur) as active_cur:
            if self.db.use_sqlite:
                active_cur.execute(
                    "UPDATE portfolio_cash SET reserved = MAX(0.0, reserved - ?) WHERE currency = ?",
                    (amount, currency)
                )
            else:
                active_cur.execute(
                    "UPDATE portfolio_cash SET reserved = GREATEST(0.0, reserved - %s) WHERE currency = %s",
                    (amount, currency)
                )

    def commit_allocation(self, allocation_id: str, currency: str, reserved_amount: float, actual_spent: float, cur=None):
        """
        Commits reserved capital, deducting spent amount from cash and releasing any leftover reservation (Requirement #4 / #6).
        """
        currency = currency.upper()
        with self._get_db_cursor_context(cur) as active_cur:
            if self.db.use_sqlite:
                # Deduct from cash, release reservation
                active_cur.execute(
                    "UPDATE portfolio_cash SET cash = cash - ?, reserved = MAX(0.0, reserved - ?) WHERE currency = ?",
                    (actual_spent, reserved_amount, currency)
                )
            else:
                active_cur.execute(
                    "UPDATE portfolio_cash SET cash = cash - %s, reserved = GREATEST(0.0, reserved - %s) WHERE currency = %s",
                    (actual_spent, reserved_amount, currency)
                )

    def deposit_cash(self, currency: str, amount: float, cur=None):
        """
        Utility function to deposit or credit cash back to the portfolio (e.g. on sale).
        """
        currency = currency.upper()
        with self._get_db_cursor_context(cur) as active_cur:
            if self.db.use_sqlite:
                active_cur.execute(
                    "INSERT INTO portfolio_cash (currency, cash, reserved) VALUES (?, ?, 0.0) "
                    "ON CONFLICT(currency) DO UPDATE SET cash = cash + ?",
                    (currency, amount, amount)
                )
            else:
                active_cur.execute("""
                    INSERT INTO portfolio_cash (currency, cash, reserved) 
                    VALUES (%s, %s, 0)
                    ON CONFLICT(currency) DO UPDATE SET cash = portfolio_cash.cash + EXCLUDED.cash
                """, (currency, amount))

    # ────────────────────────── PortfolioAllocator Implementation ──────────────────────────

    # ────────────────────────── PortfolioAllocator Implementation ──────────────────────────

    def allocate(
        self,
        proposal: AllocationProposal,
        get_mark_func,
        risk_settings,
        lane_settings,
        code_sha: str,
        config_hash: str = "v1"
    ) -> AllocationResult:
        """
        Entry point for allocation with bounded retries for serialization conflicts (Requirement D3).
        """
        max_retries = 10
        backoff = 0.05
        import time
        for attempt in range(max_retries):
            try:
                return self._allocate_transactional(
                    proposal=proposal,
                    get_mark_func=get_mark_func,
                    risk_settings=risk_settings,
                    lane_settings=lane_settings,
                    code_sha=code_sha,
                    config_hash=config_hash
                )
            except (psycopg2.errors.SerializationFailure, sqlite3.OperationalError) as e:
                if attempt == max_retries - 1:
                    print(f"PortfolioEngine: Concurrency retry limit exceeded: {e}")
                    raise e
                print(f"PortfolioEngine: Concurrency conflict. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff *= 2.0

    def _allocate_transactional(
        self,
        proposal: AllocationProposal,
        get_mark_func,
        risk_settings,
        lane_settings,
        code_sha: str,
        config_hash: str = "v1"
    ) -> AllocationResult:
        """
        Determines the safe allocation matching limits and reservations in a SINGLE SQL transaction (Requirement D3).
        """
        allocation_id = f"alloc-{uuid.uuid4()}"
        reason_codes = []

        with self._get_db_cursor_context() as cur:
            # 1. Load fresh snapshot inside the active transaction (cur passed)
            snapshot = self.load_portfolio_snapshot(get_mark_func, cur=cur)

            # Protective exits bypass limits
            is_exit = proposal.action in ["REDUCE", "CLOSE"]
            
            if is_exit:
                new_v = self._increment_version_tx(cur)
                self._persist_allocation_audit_tx(
                    cur,
                    allocation_id=allocation_id,
                    proposal_id=proposal.proposal_id,
                    symbol=proposal.symbol,
                    action=proposal.action,
                    req_risk=proposal.requested_risk_fraction,
                    app_risk=proposal.requested_risk_fraction,
                    req_notional=proposal.requested_notional,
                    app_notional=proposal.requested_notional,
                    reserved=0.0,
                    status="APPROVED",
                    reason_codes=["PROTECTIVE_EXIT_BYPASS"],
                    port_version=new_v,
                    port_digest=snapshot.digest,
                    marks_provenance={},
                    config_hash=config_hash,
                    code_sha=code_sha
                )
                return AllocationResult(
                    allocation_id=allocation_id,
                    proposal_id=proposal.proposal_id,
                    symbol=proposal.symbol,
                    decision="APPROVE",
                    approved_notional=proposal.requested_notional,
                    approved_quantity=0.0,
                    reserved_capital=0.0,
                    reason_codes=["PROTECTIVE_EXIT_BYPASS"],
                    portfolio_version=new_v,
                    portfolio_digest=snapshot.digest
                )

            # 2. Check Valuation Validity (D2)
            if not snapshot.valuation_valid:
                reason_codes.append("PORTFOLIO_VALUATION_INVALID")
                reason_codes.extend(snapshot.valuation_reason_codes)
                new_v = self._increment_version_tx(cur)
                self._persist_allocation_audit_tx(
                    cur,
                    allocation_id=allocation_id,
                    proposal_id=proposal.proposal_id,
                    symbol=proposal.symbol,
                    action=proposal.action,
                    req_risk=proposal.requested_risk_fraction,
                    app_risk=0.0,
                    req_notional=proposal.requested_notional,
                    app_notional=0.0,
                    reserved=0.0,
                    status="REJECTED",
                    reason_codes=reason_codes,
                    port_version=new_v,
                    port_digest=snapshot.digest,
                    marks_provenance={},
                    config_hash=config_hash,
                    code_sha=code_sha
                )
                return AllocationResult(
                    allocation_id=allocation_id,
                    proposal_id=proposal.proposal_id,
                    symbol=proposal.symbol,
                    decision="REJECT",
                    approved_notional=0.0,
                    approved_quantity=0.0,
                    reserved_capital=0.0,
                    reason_codes=reason_codes,
                    portfolio_version=new_v,
                    portfolio_digest=snapshot.digest
                )

            # 3. New entries: check strict portfolio-level controls
            p_mapping = get_product_mapping(proposal.symbol)
            quote = p_mapping.canonical_symbol.split("/")[-1].upper()
            rate = get_conversion_rate_to_usdc(quote, get_mark_func)
            price = get_mark_func(proposal.symbol)

            if rate is None or rate <= 0.0:
                reason_codes.append("STALE_CONVERSION_PATH")
                new_v = self._increment_version_tx(cur)
                self._persist_allocation_audit_tx(
                    cur,
                    allocation_id=allocation_id,
                    proposal_id=proposal.proposal_id,
                    symbol=proposal.symbol,
                    action=proposal.action,
                    req_risk=proposal.requested_risk_fraction,
                    app_risk=0.0,
                    req_notional=proposal.requested_notional,
                    app_notional=0.0,
                    reserved=0.0,
                    status="REJECTED",
                    reason_codes=reason_codes,
                    port_version=new_v,
                    port_digest=snapshot.digest,
                    marks_provenance={},
                    config_hash=config_hash,
                    code_sha=code_sha
                )
                return AllocationResult(
                    allocation_id=allocation_id,
                    proposal_id=proposal.proposal_id,
                    symbol=proposal.symbol,
                    decision="REJECT",
                    approved_notional=0.0,
                    approved_quantity=0.0,
                    reserved_capital=0.0,
                    reason_codes=reason_codes,
                    portfolio_version=new_v,
                    portfolio_digest=snapshot.digest
                )

            # Drawdown check
            if snapshot.drawdown > 10.0:
                reason_codes.append("RISK_BUDGET_EXCEEDED")
                decision = "REJECT"
                approved_notional = 0.0
            else:
                # Open positions limit
                active_positions = [s for s, p in snapshot.positions.items() if p["quantity"] > 0.0]
                if len(active_positions) >= lane_settings.max_open_positions and proposal.symbol not in active_positions:
                    reason_codes.append("MAX_POSITIONS_LIMIT_REACHED")
                    decision = "REJECT"
                    approved_notional = 0.0
                else:
                    # Limits calculations
                    max_notional_base = snapshot.equity * (lane_settings.max_position_pct / 100.0)
                    max_notional_quote = max_notional_base / rate

                    max_gross_limit = 50000.0
                    remaining_gross_quota_base = max(0.0, max_gross_limit - snapshot.gross_exposure)
                    remaining_gross_quota_quote = remaining_gross_quota_base / rate

                    max_concentration_limit_base = snapshot.equity * 0.30
                    max_concentration_quota_quote = max_concentration_limit_base / rate

                    # E2: Use combined (filled + pending) pre-existing exposure on this symbol
                    current_exposure_base = snapshot.symbol_exposures.get(proposal.symbol, 0.0)
                    current_exposure_quote = current_exposure_base / rate

                    remaining_position_capacity = max(0.0, max_notional_quote - current_exposure_quote)
                    remaining_concentration_capacity = max(0.0, max_concentration_quota_quote - current_exposure_quote)
                    
                    safe_max_notional_quote = min(remaining_position_capacity, remaining_concentration_capacity, remaining_gross_quota_quote)

                    # Limit 4: D7 and E2 Portfolio Risk Fraction constraints
                    max_portfolio_risk_budget = 1.0  # 100% of equity
                    remaining_portfolio_risk = max(0.0, max_portfolio_risk_budget - snapshot.risk_budget_consumed)
                    allowed_risk_fraction = min(proposal.requested_risk_fraction, remaining_portfolio_risk)

                    max_by_risk_fraction_base = snapshot.equity * allowed_risk_fraction
                    max_by_risk_fraction_quote = max_by_risk_fraction_base / rate

                    # Combine all limits dynamically (D7)
                    safe_max_notional_quote = min(safe_max_notional_quote, max_by_risk_fraction_quote)

                    approved_notional = proposal.requested_notional
                    decision = "APPROVE"

                    if approved_notional > safe_max_notional_quote:
                        approved_notional = safe_max_notional_quote
                        decision = "MODIFY_DOWN"
                        reason_codes.append("CONSTRAINTS_SCALED_DOWN")

                    # Check cash
                    available_quote_cash = snapshot.cash.get(quote, 0.0) - snapshot.reserved.get(quote, 0.0)
                    if approved_notional > available_quote_cash:
                        if available_quote_cash > 0.0:
                            approved_notional = available_quote_cash
                            decision = "MODIFY_DOWN"
                            reason_codes.append("CASH_LIMIT_SCALED_DOWN")
                        else:
                            decision = "REJECT"
                            reason_codes.append("INSUFFICIENT_CASH")
                            approved_notional = 0.0

                    # Min notional check
                    if approved_notional < 1.0 and decision != "REJECT":
                        decision = "REJECT"
                        reason_codes.append("BELOW_MINIMUM_NOTIONAL")
                        approved_notional = 0.0

            # Calculate approved risk fraction from actual approved base notional (D7)
            approved_base_notional = approved_notional * rate
            approved_risk_fraction = approved_base_notional / snapshot.equity if snapshot.equity > 0.0 else 0.0

            # Enforce constraints (D7)
            if approved_notional > proposal.requested_notional:
                approved_notional = proposal.requested_notional
            if approved_risk_fraction > proposal.requested_risk_fraction:
                approved_risk_fraction = proposal.requested_risk_fraction

            reserved_capital = approved_notional if decision != "REJECT" else 0.0

            # Transactionally reserve inside the same cur (D3)
            if reserved_capital > 0.0:
                if self.db.use_sqlite:
                    row = cur.execute("SELECT cash, reserved FROM portfolio_cash WHERE currency = ?", (quote,)).fetchone()
                    cash_val, res_val = float(row[0]), float(row[1])
                    if cash_val - res_val >= reserved_capital:
                        cur.execute("UPDATE portfolio_cash SET reserved = reserved + ? WHERE currency = ?", (reserved_capital, quote))
                    else:
                        decision = "REJECT"
                        reason_codes.append("RESERVATION_RACE_CONCURRENCY_FAIL")
                        approved_notional = 0.0
                        reserved_capital = 0.0
                        approved_risk_fraction = 0.0
                else:
                    cur.execute("SELECT cash, reserved FROM portfolio_cash WHERE currency = %s FOR UPDATE", (quote,))
                    row = cur.fetchone()
                    cash_val, res_val = float(row["cash"]), float(row["reserved"])
                    if cash_val - res_val >= reserved_capital:
                        cur.execute("UPDATE portfolio_cash SET reserved = reserved + %s WHERE currency = %s", (reserved_capital, quote))
                    else:
                        decision = "REJECT"
                        reason_codes.append("RESERVATION_RACE_CONCURRENCY_FAIL")
                        approved_notional = 0.0
                        reserved_capital = 0.0
                        approved_risk_fraction = 0.0

            new_v = self._increment_version_tx(cur)

            # Persist audit row inside same transaction
            self._persist_allocation_audit_tx(
                cur,
                allocation_id=allocation_id,
                proposal_id=proposal.proposal_id,
                symbol=proposal.symbol,
                action=proposal.action,
                req_risk=proposal.requested_risk_fraction,
                app_risk=approved_risk_fraction,
                req_notional=proposal.requested_notional,
                app_notional=approved_notional,
                reserved=reserved_capital,
                status="PENDING" if reserved_capital > 0.0 else "REJECTED",
                reason_codes=reason_codes,
                port_version=new_v,
                port_digest=snapshot.digest,
                marks_provenance={"quote": quote, "conversion_rate_to_usdc": rate},
                config_hash=config_hash,
                code_sha=code_sha
            )

            return AllocationResult(
                allocation_id=allocation_id,
                proposal_id=proposal.proposal_id,
                symbol=proposal.symbol,
                decision=decision,
                approved_notional=approved_notional,
                approved_quantity=0.0,
                reserved_capital=reserved_capital,
                reason_codes=reason_codes,
                portfolio_version=new_v,
                portfolio_digest=snapshot.digest
            )

    def _increment_version_tx(self, cur) -> int:
        if self.db.use_sqlite:
            row = cur.execute("SELECT value FROM portfolio_metadata WHERE key = 'version'").fetchone()
            new_v = int(row[0]) + 1 if row else 1
            cur.execute("INSERT OR REPLACE INTO portfolio_metadata (key, value) VALUES ('version', ?)", (str(new_v),))
        else:
            cur.execute("SELECT value FROM portfolio_metadata WHERE key = 'version' FOR UPDATE")
            row = cur.fetchone()
            new_v = int(row["value"]) + 1 if row else 1
            cur.execute("""
                INSERT INTO portfolio_metadata (key, value) 
                VALUES ('version', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (str(new_v),))
        return new_v

    def _persist_allocation_audit_tx(
        self,
        cur,
        allocation_id: str,
        proposal_id: str,
        symbol: str,
        action: str,
        req_risk: float,
        app_risk: float,
        req_notional: float,
        app_notional: float,
        reserved: float,
        status: str,
        reason_codes: List[str],
        port_version: int,
        port_digest: str,
        marks_provenance: dict,
        config_hash: str,
        code_sha: str
    ):
        reasons_json = json.dumps(reason_codes)
        provenance_json = json.dumps(marks_provenance)
        if self.db.use_sqlite:
            cur.execute("""
                INSERT INTO portfolio_allocations (
                    allocation_id, proposal_id, symbol, action, requested_risk_fraction, approved_risk_fraction,
                    requested_notional, approved_notional, reserved_capital, status, reason_codes,
                    portfolio_version, portfolio_digest, marks_provenance, config_hash, code_sha
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                allocation_id, proposal_id, symbol, action, req_risk, app_risk,
                req_notional, app_notional, reserved, status, reasons_json,
                port_version, port_digest, provenance_json, config_hash, code_sha
            ))
        else:
            cur.execute("""
                INSERT INTO portfolio_allocations (
                    allocation_id, proposal_id, symbol, action, requested_risk_fraction, approved_risk_fraction,
                    requested_notional, approved_notional, reserved_capital, status, reason_codes,
                    portfolio_version, portfolio_digest, marks_provenance, config_hash, code_sha
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                allocation_id, proposal_id, symbol, action, req_risk, app_risk,
                req_notional, app_notional, reserved, status, reasons_json,
                port_version, port_digest, provenance_json, config_hash, code_sha
            ))

    def _persist_allocation_audit(
        self,
        allocation_id: str,
        proposal_id: str,
        symbol: str,
        action: str,
        req_risk: float,
        app_risk: float,
        req_notional: float,
        app_notional: float,
        reserved: float,
        status: str,
        reason_codes: List[str],
        port_version: int,
        port_digest: str,
        marks_provenance: dict,
        config_hash: str,
        code_sha: str
    ):
        with self._get_db_cursor_context() as cur:
            reasons_json = json.dumps(reason_codes)
            provenance_json = json.dumps(marks_provenance)
            if self.db.use_sqlite:
                cur.execute("""
                    INSERT INTO portfolio_allocations (
                        allocation_id, proposal_id, symbol, action, requested_risk_fraction, approved_risk_fraction,
                        requested_notional, approved_notional, reserved_capital, status, reason_codes,
                        portfolio_version, portfolio_digest, marks_provenance, config_hash, code_sha
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    allocation_id, proposal_id, symbol, action, req_risk, app_risk,
                    req_notional, app_notional, reserved, status, reasons_json,
                    port_version, port_digest, provenance_json, config_hash, code_sha
                ))
            else:
                cur.execute("""
                    INSERT INTO portfolio_allocations (
                        allocation_id, proposal_id, symbol, action, requested_risk_fraction, approved_risk_fraction,
                        requested_notional, approved_notional, reserved_capital, status, reason_codes,
                        portfolio_version, portfolio_digest, marks_provenance, config_hash, code_sha
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    allocation_id, proposal_id, symbol, action, req_risk, app_risk,
                    req_notional, app_notional, reserved, status, reasons_json,
                    port_version, port_digest, provenance_json, config_hash, code_sha
                ))

    # ────────────────────────── Reconcile and Reconstruction ──────────────────────────

    def reconcile_orphan_reservations(self):
        """
        Scans allocations in PENDING status, cross-checks active ExecutionIntents,
        and releases any orphan reservations transactionally (Requirement #4 / #7 / D5).
        """
        pending_allocations = []
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                rows = cur.execute("SELECT allocation_id, symbol, approved_notional FROM portfolio_allocations WHERE status = 'PENDING'").fetchall()
                for r in rows:
                    pending_allocations.append({"id": r[0], "symbol": r[1], "amount": float(r[2])})
            else:
                cur.execute("SELECT allocation_id, symbol, approved_notional FROM portfolio_allocations WHERE status = 'PENDING'")
                rows = cur.fetchall()
                for r in rows:
                    pending_allocations.append({"id": r["allocation_id"], "symbol": r["symbol"], "amount": float(r["approved_notional"])})

        for alloc in pending_allocations:
            # Check if there is a corresponding execution intent (Requirement D5)
            has_intent = False
            with self._get_db_cursor_context() as cur:
                if self.db.use_sqlite:
                    row = cur.execute("SELECT id FROM execution_intents WHERE allocation_id = ?", (alloc["id"],)).fetchone()
                    if row:
                        has_intent = True
                else:
                    cur.execute("SELECT id FROM execution_intents WHERE allocation_id = %s", (alloc["id"],))
                    row = cur.fetchone()
                    if row:
                        has_intent = True
            
            if not has_intent:
                # No execution intent exists -> Release reservation as orphan!
                p_mapping = get_product_mapping(alloc["symbol"])
                quote = p_mapping.canonical_symbol.split("/")[-1].upper()
                print(f"PortfolioEngine: Reconciling orphan reservation {alloc['id']} of {alloc['amount']} {quote}")
                self.release_reservation(alloc["id"], quote, alloc["amount"])
                
                # Mark allocation as RELEASED
                with self._get_db_cursor_context() as cur:
                    if self.db.use_sqlite:
                        cur.execute("UPDATE portfolio_allocations SET status = 'RELEASED' WHERE allocation_id = ?", (alloc["id"],))
                    else:
                        cur.execute("UPDATE portfolio_allocations SET status = 'RELEASED' WHERE allocation_id = %s", (alloc["id"],))

    def update_allocation_status(self, allocation_id: str, status: str, cur=None):
        with self._get_db_cursor_context(cur) as active_cur:
            if self.db.use_sqlite:
                active_cur.execute("UPDATE portfolio_allocations SET status = ? WHERE allocation_id = ?", (status, allocation_id))
            else:
                active_cur.execute("UPDATE portfolio_allocations SET status = %s WHERE allocation_id = %s", (status, allocation_id))

    def update_position(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        cur=None
    ):
        """
        Updates the position for a symbol. If quantity drops to 0, deletes the position.
        """
        with self._get_db_cursor_context(cur) as active_cur:
            if quantity <= 0.0:
                if self.db.use_sqlite:
                    active_cur.execute("DELETE FROM portfolio_positions WHERE symbol = ?", (symbol,))
                else:
                    active_cur.execute("DELETE FROM portfolio_positions WHERE symbol = %s", (symbol,))
            else:
                if self.db.use_sqlite:
                    active_cur.execute("""
                        INSERT OR REPLACE INTO portfolio_positions (symbol, quantity, entry_price, realized_pnl, unrealized_pnl, stop_loss_price, take_profit_price, updated_at)
                        VALUES (?, ?, ?, 0.0, 0.0, ?, ?, CURRENT_TIMESTAMP)
                    """, (symbol, quantity, entry_price, stop_loss_price, take_profit_price))
                else:
                    active_cur.execute("""
                        INSERT INTO portfolio_positions (symbol, quantity, entry_price, realized_pnl, unrealized_pnl, stop_loss_price, take_profit_price, updated_at)
                        VALUES (%s, %s, %s, 0.0, 0.0, %s, %s, now())
                        ON CONFLICT (symbol) DO UPDATE SET
                            quantity = EXCLUDED.quantity,
                            entry_price = EXCLUDED.entry_price,
                            stop_loss_price = EXCLUDED.stop_loss_price,
                            take_profit_price = EXCLUDED.take_profit_price,
                            updated_at = now()
                    """, (symbol, quantity, entry_price, stop_loss_price, take_profit_price))
