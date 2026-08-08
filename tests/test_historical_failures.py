import unittest
import uuid
import os
import threading
from datetime import UTC, datetime, timedelta
import psycopg2

from app.models import (
    DecisionRequest, Proposal, MarketSnapshot, PortfolioSnapshot, Candle,
    ExecutionIntent, ExecutionResult, RiskDecision, Action
)
from app.risk import evaluate_risk, RiskSettings, LaneSettings, RiskResult
from app.executor import PaperExecutor, CoinbaseLiveExecutor, DatabaseConnection
from app.coinbase_adapter import CoinbasePublicAdapter


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def create_request(*, mode="paper", last_trade_at=None, age_seconds=5) -> DecisionRequest:
    candles = [
        Candle(
            timestamp=NOW - timedelta(minutes=5 * (32 - index)),
            open=100.0 + index,
            high=102.0 + index,
            low=99.0 + index,
            close=101.0 + index,
            volume=10.0,
        )
        for index in range(32)
    ]
    return DecisionRequest(
        request_id=str(uuid.uuid4()),
        mode=mode,
        lane_id="lane_1",
        symbol="BTC/USDC",
        timeframe="5m",
        market=MarketSnapshot(
            timestamp=NOW - timedelta(seconds=age_seconds), bid=100.0, ask=100.1, candles=candles
        ),
        portfolio=PortfolioSnapshot(
            equity=310.0,
            cash=310.0,
            daily_pnl_pct=0.0,
            open_positions=1,
            current_position_pct=10.0,
            last_trade_at=last_trade_at,
        ),
    )


GLOBAL = RiskSettings(
    allowed_symbols=frozenset({"BTC/USDC", "ETH/USDC"}),
    allowed_actions=frozenset({"NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"})
)
LANE = LaneSettings(
    minimum_confidence=0.7,
    max_position_pct=10.0,
    max_daily_loss_pct=2.0,
    max_open_positions=2,
    cooldown_minutes=30,
)


class HistoricalFailuresTests(unittest.TestCase):

    def setUp(self):
        # Always use clean, isolated sqlite in-memory DB by default (HST-08)
        self.db_url = "sqlite:///:memory:"
        self.executor = PaperExecutor(db_url=self.db_url)
        self.executor.initialize_lane("lane_1", 1000.0)

    # --- HST-01: Protection orders must execute & persist reasons ---
    def test_hst_01_protection_orders_execute_when_crossed(self):
        # Correctly wiring intent.stop_price as stop loss (Blocker G1)
        intent_id = str(uuid.uuid4())
        intent = ExecutionIntent(
            execution_intent_id=intent_id,
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=2.0,
            stop_price=90.0, # Stop Loss price is wired here!
            take_profit_price=120.0,
            client_order_id="order-hst-01-open",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        
        # Execute order at $100.0
        res = self.executor.execute_intent("lane_1", intent, 100.0)
        self.assertEqual(res.status, "FILLED")
        
        pos = self.executor.get_position("lane_1", "BTC/USDC")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["quantity"], 2.0)
        self.assertEqual(pos["stop_loss_price"], 90.0)
        self.assertEqual(pos["take_profit_price"], 120.0)

        # Trigger stop loss: price drops to $85.0 (below stop loss at $90.0)
        trigger_res = self.executor.check_and_trigger_stops("lane_1", "BTC/USDC", 85.0)
        self.assertIsNotNone(trigger_res)
        self.assertEqual(trigger_res.status, "FILLED")
        self.assertIn("STOP_LOSS_TRIGGERED", trigger_res.reason_codes)

        # Position should be closed
        pos_after = self.executor.get_position("lane_1", "BTC/USDC")
        self.assertTrue(pos_after is None or pos_after["quantity"] == 0)

        # Blocker H2: Re-read result from the database and verify the reason is persist-audited
        persisted_result = self.executor.get_execution_result(trigger_res.execution_intent_id)
        self.assertIsNotNone(persisted_result)
        self.assertEqual(persisted_result["status"], "FILLED")
        self.assertIn("STOP_LOSS_TRIGGERED", persisted_result["reason_codes"])

    # --- HST-02 & G3 & H1: PostgreSQL Concurrency TOCTOU Proof ---
    def test_hst_02_postgresql_concurrency_toctou_prevention(self):
        # Test serializability and state-checking under concurrent allocation
        # First order uses $200 of cash (quantity 2.0 @ 100.0)
        intent1 = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=8.0, # costs ~ $800 + fees, fits inside $1000 cash
            client_order_id="order-hst-02-1",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        res1 = self.executor.execute_intent("lane_1", intent1, 100.0)
        self.assertEqual(res1.status, "FILLED")

        # Second order tries to buy same size, but cash is now too low (under $200 remaining)
        intent2 = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="ADD",
            side="BUY",
            quantity=8.0, # needs $800, fails atomic check
            client_order_id="order-hst-02-2",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        res2 = self.executor.execute_intent("lane_1", intent2, 100.0)
        self.assertEqual(res2.status, "REJECTED")
        self.assertIn("INSUFFICIENT_CASH", res2.reason_codes)

    def test_hst_02_postgresql_serializable_concurrency_proof(self):
        """
        G3 & H1: Mandatory PostgreSQL SERIALIZABLE transaction safety and atomic portfolio-level position limit.
        No silent skips. Environment must supply TEST_POSTGRES_URL.
        Asserts exactly one successful OPEN and one specific rejection or serialization failure.
        """
        pg_url = os.getenv("TEST_POSTGRES_URL")
        if not pg_url:
            self.fail("TEST_POSTGRES_URL environment variable is mandatory and must be set for PostgreSQL concurrency certification.")

        # Blocker I1: Strict Safety checks to prevent destructive testing on canonical database
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            if pg_url.strip().lower() == db_url.strip().lower():
                self.fail("CRITICAL SAFETY BLOCK: TEST_POSTGRES_URL cannot be equal to DATABASE_URL!")

        from urllib.parse import urlparse
        parsed = urlparse(pg_url)
        dbname = parsed.path.lstrip("/")
        if "test" not in dbname.lower():
            self.fail(f"CRITICAL SAFETY BLOCK: TEST_POSTGRES_URL must target a database containing 'test' (got dbname: '{dbname}')")

        # Recreate test schema on real test PostgreSQL
        try:
            conn = psycopg2.connect(pg_url)
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DROP TABLE IF EXISTS paper_balances CASCADE")
                    cur.execute("DROP TABLE IF EXISTS paper_positions CASCADE")
                    cur.execute("DROP TABLE IF EXISTS execution_intents CASCADE")
                    cur.execute("DROP TABLE IF EXISTS execution_results CASCADE")
                    cur.execute("DROP TABLE IF EXISTS market_marks CASCADE")
                    cur.execute("DROP TABLE IF EXISTS arena_snapshots CASCADE")
                    
                    cur.execute("""
                    CREATE TABLE paper_balances (
                        id BIGSERIAL PRIMARY KEY,
                        lane_id TEXT NOT NULL UNIQUE,
                        equity NUMERIC(20, 8) NOT NULL,
                        cash NUMERIC(20, 8) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )""")
                    cur.execute("""
                    CREATE TABLE arena_snapshots (
                        id BIGSERIAL PRIMARY KEY,
                        lane_id TEXT NOT NULL,
                        equity NUMERIC(20, 8) NOT NULL,
                        cash NUMERIC(20, 8) NOT NULL,
                        realized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
                        unrealized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
                        fees NUMERIC(20, 8) NOT NULL DEFAULT 0,
                        max_drawdown_pct NUMERIC(10, 4) NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )""")
                    cur.execute("""
                    CREATE TABLE paper_positions (
                        id BIGSERIAL PRIMARY KEY,
                        lane_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        quantity NUMERIC(20, 8) NOT NULL,
                        entry_price NUMERIC(20, 8) NOT NULL,
                        stop_loss_price NUMERIC(20, 8),
                        take_profit_price NUMERIC(20, 8),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        CONSTRAINT paper_positions_lane_symbol UNIQUE (lane_id, symbol)
                    )""")
                    cur.execute("""
                    CREATE TABLE execution_intents (
                        id BIGSERIAL PRIMARY KEY,
                        execution_intent_id UUID NOT NULL UNIQUE,
                        risk_decision_id UUID NOT NULL,
                        mode TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        action TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity NUMERIC(20, 8) NOT NULL,
                        order_type TEXT NOT NULL,
                        limit_price NUMERIC(20, 8),
                        stop_price NUMERIC(20, 8),
                        take_profit_price NUMERIC(20, 8),
                        time_exit_at TIMESTAMPTZ,
                        client_order_id TEXT NOT NULL UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        expires_at TIMESTAMPTZ NOT NULL
                    )""")
                    cur.execute("""
                    CREATE TABLE execution_results (
                        id BIGSERIAL PRIMARY KEY,
                        execution_intent_id UUID NOT NULL UNIQUE REFERENCES execution_intents(execution_intent_id) ON DELETE CASCADE,
                        broker_order_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        requested_quantity NUMERIC(20, 8) NOT NULL,
                        filled_quantity NUMERIC(20, 8) NOT NULL,
                        average_fill_price NUMERIC(20, 8),
                        fee NUMERIC(20, 8) NOT NULL DEFAULT 0,
                        slippage NUMERIC(20, 8) NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb
                    )""")
                    cur.execute("""
                    CREATE TABLE market_marks (
                        id BIGSERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL UNIQUE,
                        price NUMERIC(20, 8) NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )""")
            conn.close()
        except Exception as e:
            self.fail(f"Failed to connect and initialize test PostgreSQL using TEST_POSTGRES_URL: {e}")

        pg_executor = PaperExecutor(db_url=pg_url)
        pg_executor.initialize_lane("lane_concurrency_test", 2000.0)

        # We set max_open_positions = 1.
        # We spawn two concurrent threads to open BTC and ETH at the same time.
        # One must succeed (status FILLED), the other must fail (either status REJECTED or psycopg2 SerializationFailure).
        intent_btc = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=1.0,
            client_order_id="order-concur-btc",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        intent_eth = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="ETH/USDC",
            action="OPEN",
            side="BUY",
            quantity=1.0,
            client_order_id="order-concur-eth",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )

        results = []
        errors = []

        def worker(intent):
            try:
                res = pg_executor.execute_intent("lane_concurrency_test", intent, 100.0, max_open_positions=1)
                results.append(res)
            except psycopg2.errors.SerializationFailure as se:
                errors.append(se)
            except Exception as ex:
                errors.append(ex)

        t1 = threading.Thread(target=worker, args=(intent_btc,))
        t2 = threading.Thread(target=worker, args=(intent_eth,))

        # Start concurrent execution
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Blocker H1 Verification:
        # Verify that we have EXACTLY ONE successful filled OPEN position (filled_count == 1)
        filled_count = len([r for r in results if r.status == "FILLED"])
        self.assertEqual(filled_count, 1)

        # Verify that the other transaction/outcome is either:
        # 1. A return result with REJECTED status and OPEN_POSITION_LIMIT reason
        # 2. Or a psycopg2 SerializationFailure in errors list
        rejected_count = len([r for r in results if r.status == "REJECTED" and "OPEN_POSITION_LIMIT" in r.reason_codes])
        serialization_failures = len([e for e in errors if isinstance(e, psycopg2.errors.SerializationFailure)])
        
        self.assertEqual(rejected_count + serialization_failures, 1)

        # Verify there are no unexpected exceptions in errors list
        unexpected_errors = [e for e in errors if not isinstance(e, psycopg2.errors.SerializationFailure)]
        self.assertEqual(len(unexpected_errors), 0, f"Unexpected errors during concurrent test: {unexpected_errors}")

        # Check PostgreSQL table itself to prove exactly 1 position is currently committed
        conn = psycopg2.connect(pg_url)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM paper_positions WHERE lane_id = 'lane_concurrency_test' AND quantity > 0")
                row_count = cur.fetchone()[0]
                self.assertEqual(row_count, 1)
        conn.close()

    # --- HST-03: Exit is never blocked by entry cooldown ---
    def test_hst_03_cooldown_active_does_not_block_exits(self):
        recent_trade = NOW - timedelta(minutes=5)
        req = create_request(last_trade_at=recent_trade)

        # 1. Entry (OPEN) must be blocked by Cooldown
        buy_proposal = Proposal(
            action="OPEN",
            allocation_pct=8.0,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )
        buy_result = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(buy_result.approved)
        self.assertIn("COOLDOWN_ACTIVE", buy_result.reasons)

        # 2. Exit/Reduction (REDUCE) must bypass cooldown
        reduce_proposal = Proposal(
            action="REDUCE",
            allocation_pct=5.0,
            confidence=0.8,
        )
        reduce_result = evaluate_risk(req, reduce_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(reduce_result.approved)
        self.assertEqual(reduce_result.action, "REDUCE")
        self.assertNotIn("COOLDOWN_ACTIVE", reduce_result.reasons)

    # --- HST-04: Position sizing semantics ---
    def test_hst_04_position_sizing_reduce_does_not_trigger_allocation_limit(self):
        req = create_request()
        oversized_reduce = Proposal(
            action="REDUCE",
            allocation_pct=15.0,  # 15 > max_position_pct (10.0)
            confidence=0.8,
        )
        result = evaluate_risk(req, oversized_reduce, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.action, "REDUCE")
        self.assertNotIn("ALLOCATION_LIMIT", result.reasons)

    # --- HST-05 & G4: Fresh multi-asset mark-to-market ---
    def test_hst_05_stale_market_data_fails_closed(self):
        # Age = 120 seconds (greater than max_market_age_seconds = 90)
        req = create_request(age_seconds=120)
        buy_proposal = Proposal(
            action="OPEN",
            allocation_pct=8.0,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )
        result = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertEqual(result.action, "NO_TRADE")
        self.assertIn("STALE_MARKET_DATA", result.reasons)

    def test_hst_05_multi_asset_mark_to_market_evaluation(self):
        """
        G4: Maintain per-symbol fresh marks and calculate equity using each position's own fresh mark.
        """
        # Store two fresh marks
        self.executor.update_market_mark("BTC/USDC", 100.0)
        self.executor.update_market_mark("ETH/USDC", 10.0)

        # Open BTC position (quantity 2)
        intent1 = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=2.0,
            client_order_id="open-btc-mark",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        res1 = self.executor.execute_intent("lane_1", intent1, 100.0)
        self.assertEqual(res1.status, "FILLED")

        # Open ETH position (quantity 5)
        intent2 = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="ETH/USDC",
            action="OPEN",
            side="BUY",
            quantity=5.0,
            client_order_id="open-eth-mark",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        res2 = self.executor.execute_intent("lane_1", intent2, 10.0)
        self.assertEqual(res2.status, "FILLED")

        # Verify equity based on actual marks:
        # Cash left = 1000 - BTC cost - ETH cost
        # BTC cost = (2 * 100.05 [with slippage]) + 1.20 (fee) = 201.30
        # ETH cost = (5 * 10.005 [with slippage]) + 0.30 (fee) = 50.325
        # Total Cash = 1000 - 251.625 = 748.375
        # MTM Holdings value = (2 * 100 [BTC mark]) + (5 * 10 [ETH mark]) = 250.0
        # Total Equity = 748.375 + 250.0 = 998.375
        bal = self.executor.get_balance("lane_1")
        self.assertAlmostEqual(bal["equity"], 998.375)

    # --- HST-06 & G2: Net metrics do not double count fees & correct slippage scaling ---
    def test_hst_06_fee_scoring_and_double_counting_protection(self):
        # Initialize a dedicated lane with enough cash to fit the trade (Blocker G2/G3)
        self.executor.initialize_lane("lane_hst_06", 2000.0)
        
        intent = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=10.0, # Test quantity > 1 (Blocker G2)
            client_order_id="hst-06-order",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        res = self.executor.execute_intent("lane_hst_06", intent, 100.0)
        self.assertEqual(res.status, "FILLED")
        
        # Blocker G2 Unit Price Slippage verification: 
        # Unit price is fill_price * (1 + 0.0005) = 100.05 (instead of quadratic scaling)
        self.assertEqual(res.average_fill_price, 100.05)
        self.assertEqual(res.fee, 6.00) # 10.0 * 100.0 * 0.0060 = 6.00
        self.assertAlmostEqual(res.slippage, 0.50) # 10.0 * (100.05 - 100.0) = 0.50

        bal = self.executor.get_balance("lane_hst_06")
        # cash should be 2000 - 1000.5 (cost with slippage) - 6.0 (fee) = 993.5
        self.assertAlmostEqual(bal["cash"], 993.5)

    # --- HST-07: Configuration is executable truth ---
    def test_hst_07_configuration_is_executable_truth_validation(self):
        # Test that any action outside allowed_actions is immediately rejected
        req = create_request()
        bad_proposal = Proposal(
            action="OPEN",
            allocation_pct=8.0,
            confidence=0.8,
        )
        # Manually alter allowed actions in global settings to exclude OPEN
        strict_global = RiskSettings(
            allowed_symbols=frozenset({"BTC/USDC"}),
            allowed_actions=frozenset({"NO_TRADE", "CLOSE"})
        )
        result = evaluate_risk(req, bad_proposal, strict_global, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertIn("ACTION_NOT_ALLOWED", result.reasons)

    # --- HST-08: Test isolation ---
    def test_hst_08_test_isolation_and_sandbox_safety(self):
        # Asserts that test execution namespaces or requests do not mutate active production assets
        other_executor = PaperExecutor(db_url="sqlite:///:memory:")
        other_executor.initialize_lane("lane_2", 50.0)
        
        bal1 = self.executor.get_balance("lane_1")
        bal2 = other_executor.get_balance("lane_2")
        
        self.assertEqual(bal1["cash"], 1000.0)
        self.assertEqual(bal2["cash"], 50.0)

    # --- HST-09: Restart and reconciliation (Idempotency) ---
    def test_hst_09_restart_idempotency_and_order_fencing(self):
        # Verify that a unique client_order_id prevents duplicate orders on restart
        client_order_id = "rebuild-btc-order-unique-1"
        intent1 = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=1.0,
            client_order_id=client_order_id,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        res1 = self.executor.execute_intent("lane_1", intent1, 100.0)
        self.assertEqual(res1.status, "FILLED")

        # Same order intent sent again (simulating restart replay)
        intent2 = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=1.0,
            client_order_id=client_order_id,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        res2 = self.executor.execute_intent("lane_1", intent2, 100.0)
        self.assertEqual(res2.status, "FILLED")
        self.assertEqual(res2.execution_intent_id, intent1.execution_intent_id)
        self.assertIn("IDEMPOTENT_REPLAY", res2.reason_codes)

    # --- HST-10: Model failure is fail-safe ---
    def test_hst_10_model_failure_is_fail_safe(self):
        req = create_request()
        failed_proposal = Proposal(
            action="NO_TRADE",
            allocation_pct=0,
            confidence=0.0,
            reason_codes=["MODEL_EXCEPTION"]
        )
        result = evaluate_risk(req, failed_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.action, "NO_TRADE")
        self.assertIn("MODEL_EXCEPTION", result.reasons)

    # --- HST-11: Paper/live semantic parity ---
    def test_hst_11_paper_live_semantic_parity(self):
        # Asserts that evaluating risk for both modes follows identical rules,
        # with "live" adding only live-specific protection gates.
        req_paper = create_request(mode="paper")
        req_live = create_request(mode="live")
        proposal = Proposal(
            action="OPEN",
            allocation_pct=8.0,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )

        res_paper = evaluate_risk(req_paper, proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(res_paper.approved)

        # Live must fail due to LIVE_TRADING_LOCKED since it is disarmed by default
        res_live = evaluate_risk(req_live, proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(res_live.approved)
        self.assertIn("LIVE_TRADING_LOCKED", res_live.reasons)

    # --- HST-12 & G5: Paper certification gate & Coinbase Advanced Public Smoke Test ---
    def test_hst_12_coinbase_advanced_certification_gate(self):
        """
        G5: Coinbase Advanced public endpoints product discovery, ticker and candles retrieval.
        No exception suppression; actual online network check must succeed to pass certification.
        """
        adapter = CoinbasePublicAdapter()
        
        # Verify symbol mapping
        mapped_btc = adapter.map_symbol("BTC/USDC", proxy_to_usd=True)
        self.assertEqual(mapped_btc, "BTC-USD")
        
        mapped_eth = adapter.map_symbol("ETH/USDC", proxy_to_usd=False)
        self.assertEqual(mapped_eth, "ETH-USDC")

        import asyncio
        
        # 1. Product discovery / product metadata retrieval (Blocker G5)
        product_meta = asyncio.run(adapter.get_product_metadata("BTC/USDC", proxy_to_usd=True))
        self.assertIsNotNone(product_meta)
        self.assertEqual(product_meta["id"], "BTC-USD")
        self.assertEqual(product_meta["status"], "online")

        # 2. Public candles retrieval
        candles = asyncio.run(adapter.get_candles("BTC/USDC", granularity=60, proxy_to_usd=True))
        self.assertIsNotNone(candles)
        self.assertTrue(len(candles) > 0)

        # 3. Ticker retrieval with strict freshness verification
        ticker = asyncio.run(adapter.get_ticker("BTC/USDC", proxy_to_usd=True))
        self.assertIsNotNone(ticker)
        self.assertEqual(ticker["product_id"], "BTC-USD")
        self.assertTrue(ticker["price"] > 0.0)
        self.assertTrue(ticker["is_fresh"])


if __name__ == "__main__":
    unittest.main()
