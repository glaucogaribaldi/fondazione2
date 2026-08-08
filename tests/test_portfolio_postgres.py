import os
import sys
import unittest
import uuid
import asyncio
from datetime import datetime, UTC, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.portfolio import PortfolioEngine, AllocationProposal, AllocationResult
from app.products import registry, CoinbaseProduct, get_product_mapping
from app.config import load_risk_settings, load_lane_settings
from app.models import ExecutionIntent

class TestPortfolioPostgres(unittest.TestCase):
    def setUp(self):
        self.postgres_url = os.environ.get("TEST_POSTGRES_URL")
        if not self.postgres_url:
            raise unittest.SkipTest("TEST_POSTGRES_URL environment variable is not set. Skipping real PostgreSQL Portfolio Engine integration tests.")
        
        self.portfolio = PortfolioEngine(db_url=self.postgres_url)
        
        # Clear tables CASCADE to isolate tests
        with self.portfolio._get_db_cursor_context() as cur:
            cur.execute("TRUNCATE TABLE portfolio_cash CASCADE")
            cur.execute("TRUNCATE TABLE portfolio_positions CASCADE")
            cur.execute("TRUNCATE TABLE portfolio_allocations CASCADE")
            cur.execute("TRUNCATE TABLE portfolio_metadata CASCADE")

        # Initialize mock products in registry for multi-quote testing
        registry._products.clear()
        
        # 1. BTC/USDC (using BTC-USD proxy)
        p1 = CoinbaseProduct(
            product_id="BTC-USDC", product_type="SPOT", base_currency="BTC", quote_currency="USDC",
            canonical_asset="BTC", canonical_symbol="BTC/USDC", execution_product_id="BTC-USDC",
            market_data_product_id="BTC-USD", market_data_is_proxy=True, is_disabled=False,
            trading_disabled=False, cancel_only=False, limit_only=False, post_only=False,
            base_increment=0.00000001, quote_increment=0.01, min_market_funds=1.0,
            market_data_eligible=True, paper_execution_eligible=True, updated_at=datetime.now(UTC)
        )
        # 2. ETH/USDC
        p2 = CoinbaseProduct(
            product_id="ETH-USDC", product_type="SPOT", base_currency="ETH", quote_currency="USDC",
            canonical_asset="ETH", canonical_symbol="ETH/USDC", execution_product_id="ETH-USDC",
            market_data_product_id="ETH-USD", market_data_is_proxy=True, is_disabled=False,
            trading_disabled=False, cancel_only=False, limit_only=False, post_only=False,
            base_increment=0.000001, quote_increment=0.01, min_market_funds=1.0,
            market_data_eligible=True, paper_execution_eligible=True, updated_at=datetime.now(UTC)
        )
        # 3. BTC/EUR
        p3 = CoinbaseProduct(
            product_id="BTC-EUR", product_type="SPOT", base_currency="BTC", quote_currency="EUR",
            canonical_asset="BTC", canonical_symbol="BTC/EUR", execution_product_id="BTC-EUR",
            market_data_product_id="BTC-EUR", market_data_is_proxy=False, is_disabled=False,
            trading_disabled=False, cancel_only=False, limit_only=False, post_only=False,
            base_increment=0.00000001, quote_increment=0.01, min_market_funds=1.0,
            market_data_eligible=True, paper_execution_eligible=True, updated_at=datetime.now(UTC)
        )
        # 4. USDT/USDC (Direct mapped exception)
        p4 = CoinbaseProduct(
            product_id="USDT-USDC", product_type="SPOT", base_currency="USDT", quote_currency="USDC",
            canonical_asset="USDT", canonical_symbol="USDT/USDC", execution_product_id="USDT-USDC",
            market_data_product_id="USDT-USDC", market_data_is_proxy=False, is_disabled=False,
            trading_disabled=False, cancel_only=False, limit_only=False, post_only=False,
            base_increment=0.01, quote_increment=0.0001, min_market_funds=1.0,
            market_data_eligible=True, paper_execution_eligible=True, updated_at=datetime.now(UTC)
        )
        # 5. EUR/USDC (to build EUR quote conversion path to USDC)
        p5 = CoinbaseProduct(
            product_id="EUR-USDC", product_type="SPOT", base_currency="EUR", quote_currency="USDC",
            canonical_asset="EUR", canonical_symbol="EUR/USDC", execution_product_id="EUR-USDC",
            market_data_product_id="EUR-USDC", market_data_is_proxy=False, is_disabled=False,
            trading_disabled=False, cancel_only=False, limit_only=False, post_only=False,
            base_increment=0.01, quote_increment=0.0001, min_market_funds=1.0,
            market_data_eligible=True, paper_execution_eligible=True, updated_at=datetime.now(UTC)
        )
        
        registry._products["BTC-USDC"] = p1
        registry._products["ETH-USDC"] = p2
        registry._products["BTC-EUR"] = p3
        registry._products["USDT-USDC"] = p4
        registry._products["EUR-USDC"] = p5
        registry._initialized = True

    def test_pe_01_multi_asset_shared_capital_accounting(self):
        """
        TASK-0006 Mandatory Test: Verify exact multi-asset shared capital and multi-quote accounting.
        """
        # Initialize with 10000.0 USDC base cash
        self.portfolio.initialize_portfolio(10000.0)
        
        # Add EUR cash (e.g. 1000.0 EUR) representing multi-quote cash
        self.portfolio.deposit_cash("EUR", 1000.0)
        
        # Setup marks:
        # BTC/USDC mark = 60000.0
        # ETH/USDC mark = 3000.0
        # EUR/USDC mark = 1.10 (so 1000.0 EUR converts to 1100.0 USDC)
        marks = {
            "BTC/USDC": 60000.0,
            "ETH/USDC": 3000.0,
            "EUR/USDC": 1.10,
            "BTC/EUR": 60000.0 / 1.10,
            "USDT/USDC": 1.0
        }
        get_mark = lambda sym: marks.get(sym)

        # Set up active positions in different assets
        # 1. BTC/USDC: 0.1 BTC (market value = 6000.0 USDC, cost = 5900.0 USDC)
        self.portfolio.update_position("BTC/USDC", 0.1, 59000.0)
        
        # 2. ETH/USDC: 1.0 ETH (market value = 3000.0 USDC, cost = 2900.0 USDC)
        self.portfolio.update_position("ETH/USDC", 1.0, 2900.0)

        # Load SNAPSHOT and check math
        snap = self.portfolio.load_portfolio_snapshot(get_mark)
        
        # Cash:
        # Base cash (USDC) = 10000.0
        # Other cash (EUR) converted = 1000 * 1.10 = 1100.0 USDC
        # Total Cash in Base = 11100.0 USDC
        self.assertEqual(snap.cash["USDC"], 10000.0)
        self.assertEqual(snap.cash["EUR"], 1000.0)
        
        # Position Market Values:
        # BTC market value = 0.1 * 60000.0 = 6000.0 USDC
        # ETH market value = 1.0 * 3000.0 = 3000.0 USDC
        # Total positions market value = 9000.0 USDC
        
        # Total portfolio equity = Cash (11100.0) + Positions (9000.0) = 20100.0 USDC
        self.assertAlmostEqual(snap.equity, 20100.0)
        
        # Unrealized PnL:
        # BTC unrealized = 0.1 * (60000.0 - 59000.0) = 100.0 USDC
        # ETH unrealized = 1.0 * (3000.0 - 2900.0) = 100.0 USDC
        # Total unrealized = 200.0 USDC
        self.assertAlmostEqual(snap.positions["BTC/USDC"]["unrealized_pnl"], 100.0)
        self.assertAlmostEqual(snap.positions["ETH/USDC"]["unrealized_pnl"], 100.0)

    def test_pe_02_stale_conversion_fail_closed(self):
        """
        TASK-0006 Mandatory Test: Stale/missing quote conversion fails closed for new entries but not exits.
        """
        self.portfolio.initialize_portfolio(10000.0)
        
        # Let's say EUR/USDC mark is None (stale/missing)
        marks = {
            "BTC/USDC": 60000.0,
            "EUR/USDC": None # missing conversion edge!
        }
        get_mark = lambda sym: marks.get(sym)

        # Allocation for a product using EUR quote (e.g. BTC/EUR) must fail closed!
        proposal = AllocationProposal(
            proposal_id=str(uuid.uuid4()),
            symbol="BTC/EUR",
            action="OPEN",
            requested_risk_fraction=0.10,
            requested_notional=1000.0
        )
        
        risk_settings = load_risk_settings()
        _, lane_settings = load_lane_settings("lane_1")
        
        res = self.portfolio.allocate(proposal, get_mark, risk_settings, lane_settings, "test_sha")
        
        self.assertEqual(res.decision, "REJECT")
        self.assertIn("STALE_CONVERSION_PATH", res.reason_codes)

        # But protective exit on an existing position must NOT be blocked!
        # Let's say we have an open position in BTC/EUR
        self.portfolio.update_position("BTC/EUR", 0.1, 50000.0)
        exit_proposal = AllocationProposal(
            proposal_id=str(uuid.uuid4()),
            symbol="BTC/EUR",
            action="CLOSE",
            requested_risk_fraction=1.0,
            requested_notional=5000.0
        )
        res_exit = self.portfolio.allocate(exit_proposal, get_mark, risk_settings, lane_settings, "test_sha")
        self.assertEqual(res_exit.decision, "APPROVE") # Bypasses checks!

    def test_pe_03_concurrent_allocations_no_oversubscription(self):
        """
        TASK-0006 Mandatory Test: Concurrent allocation proposals cannot oversubscribe cash or exceed risk budgets.
        """
        # Initialize with only 1500.0 USDC cash
        self.portfolio.initialize_portfolio(1500.0)
        # Create an active position in ETH to boost total equity to 10000.0,
        # so concentration limit is 3000.0, fully allowing 1000.0 requests!
        self.portfolio.update_position("ETH/USDC", 2.833334, 3000.0)
        
        marks = {"BTC/USDC": 60000.0, "ETH/USDC": 3000.0}
        get_mark = lambda sym: marks.get(sym)
        
        risk_settings = load_risk_settings()
        _, lane_settings = load_lane_settings("lane_1")
        import dataclasses
        lane_settings = dataclasses.replace(lane_settings, max_position_pct=80.0)
        
        # Proposal 1: Requests 1000.0 USDC (should be approved & reserve 1000.0)
        prop1 = AllocationProposal(
            proposal_id="proposal-1",
            symbol="BTC/USDC",
            action="OPEN",
            requested_risk_fraction=0.10,
            requested_notional=1000.0
        )
        
        # Proposal 2: Requests 1000.0 USDC (cash left is 1500 - 1000 = 500.0, so it must be scaled down to 500.0!)
        prop2 = AllocationProposal(
            proposal_id="proposal-2",
            symbol="BTC/USDC",
            action="OPEN",
            requested_risk_fraction=0.10,
            requested_notional=1000.0
        )
        
        res1 = self.portfolio.allocate(prop1, get_mark, risk_settings, lane_settings, "test_sha")
        res2 = self.portfolio.allocate(prop2, get_mark, risk_settings, lane_settings, "test_sha")
        
        self.assertEqual(res1.decision, "APPROVE")
        self.assertEqual(res1.reserved_capital, 1000.0)
        
        self.assertEqual(res2.decision, "MODIFY_DOWN")
        self.assertEqual(res2.reserved_capital, 500.0) # Scaled down to available cash exactly!

    def test_pe_04_restart_reconstruction_digest_parity(self):
        """
        TASK-0006 Mandatory Test: Restart reconstruction must reproduce the exact same state and digest.
        """
        self.portfolio.initialize_portfolio(10000.0)
        self.portfolio.deposit_cash("EUR", 500.0)
        self.portfolio.update_position("BTC/USDC", 0.1, 55000.0)
        
        marks = {"BTC/USDC": 60000.0, "EUR/USDC": 1.10}
        get_mark = lambda sym: marks.get(sym)
        
        # Capture snapshot 1
        snap1 = self.portfolio.load_portfolio_snapshot(get_mark)
        
        # Recreate engine equivalent
        new_engine = PortfolioEngine(db_url=self.postgres_url)
        
        # Capture snapshot 2 from new engine on same database
        snap2 = new_engine.load_portfolio_snapshot(get_mark)
        
        self.assertEqual(snap1.equity, snap2.equity)
        self.assertEqual(snap1.digest, snap2.digest)
        self.assertEqual(snap1.version, snap2.version)

    def test_pe_05_restart_non_distruttivo(self):
        """
        D1: Verify restart does NOT reset cash or positions.
        """
        self.portfolio.initialize_portfolio(10000.0)
        
        # Simulate active state changes (spend cash)
        with self.portfolio._get_db_cursor_context() as cur:
            cur.execute("UPDATE portfolio_cash SET cash = 9935.50 WHERE currency = 'USDC'")
            cur.execute("INSERT INTO portfolio_positions (symbol, quantity, entry_price, realized_pnl, unrealized_pnl) VALUES ('BTC/USDC', 0.1, 60000.0, 0.0, 0.0)")

        # Recreate engine and run initialization path again
        new_engine = PortfolioEngine(db_url=self.postgres_url)
        new_engine.initialize_portfolio(10000.0)
        
        # Verify cash has NOT been overwritten back to 10000.0!
        snap = new_engine.load_portfolio_snapshot(lambda s: 60000.0)
        self.assertEqual(snap.cash["USDC"], 9935.50)
        self.assertEqual(snap.positions["BTC/USDC"]["quantity"], 0.1)

    def test_pe_06_invalid_portfolio_valuation(self):
        """
        D2: Verify stale/missing marks fail closed for new entries but not exits.
        """
        self.portfolio.initialize_portfolio(10000.0)
        self.portfolio.update_position("BTC/USDC", 0.1, 60000.0)
        
        # BTC/USDC mark is missing/None (stale/invalid valuation)
        get_mark = lambda s: None
        
        snap = self.portfolio.load_portfolio_snapshot(get_mark)
        self.assertFalse(snap.valuation_valid)
        self.assertIn("BTC/USDC", snap.stale_missing_marks)

        risk_settings = load_risk_settings()
        _, lane_settings = load_lane_settings("lane_1")

        # New entry OPEN must be rejected fail-closed!
        prop_open = AllocationProposal(
            proposal_id="prop-open-fail",
            symbol="ETH/USDC",
            action="OPEN",
            requested_risk_fraction=0.05,
            requested_notional=500.0
        )
        res_open = self.portfolio.allocate(prop_open, get_mark, risk_settings, lane_settings, "test_sha")
        self.assertEqual(res_open.decision, "REJECT")
        self.assertIn("PORTFOLIO_VALUATION_INVALID", res_open.reason_codes)

        # Protective exit CLOSE must be approved bypassingly!
        prop_exit = AllocationProposal(
            proposal_id="prop-exit-bypass",
            symbol="BTC/USDC",
            action="CLOSE",
            requested_risk_fraction=1.0,
            requested_notional=6000.0
        )
        res_exit = self.portfolio.allocate(prop_exit, get_mark, risk_settings, lane_settings, "test_sha")
        self.assertEqual(res_exit.decision, "APPROVE")

    def test_pe_07_concurrency_safety_real(self):
        """
        D3: Real PostgreSQL concurrent allocations serializable conflict and retry test.
        """
        import threading
        # Initialize with 1500.0 USDC cash
        self.portfolio.initialize_portfolio(1500.0)
        # Create an active position in ETH to boost total equity to 10000.0,
        # so concentration limit is 3000.0, fully allowing 1000.0 requests!
        self.portfolio.update_position("ETH/USDC", 2.833334, 3000.0)
        
        marks = {"BTC/USDC": 60000.0, "ETH/USDC": 3000.0}
        get_mark = lambda s: marks.get(s)
        
        risk_settings = load_risk_settings()
        _, lane_settings = load_lane_settings("lane_1")
        import dataclasses
        lane_settings = dataclasses.replace(lane_settings, max_position_pct=80.0)

        results = []
        def run_allocation(prop_id):
            prop = AllocationProposal(
                proposal_id=prop_id,
                symbol="BTC/USDC",
                action="OPEN",
                requested_risk_fraction=0.50, # 50% limit ($5,000)
                requested_notional=1000.0
            )
            try:
                res = self.portfolio.allocate(prop, get_mark, risk_settings, lane_settings, "test_sha")
                results.append(res)
            except Exception as e:
                print(f"Concurrent thread failed: {e}")

        # Spawn two threads starting together
        t1 = threading.Thread(target=run_allocation, args=("concurrent-1",))
        t2 = threading.Thread(target=run_allocation, args=("concurrent-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both must succeed without throwing exception due to serializable conflict automatic retries.
        self.assertEqual(len(results), 2)
        
        # Together they must NEVER oversubscribe available cash budget of 1500.0!
        total_reserved = sum(r.reserved_capital for r in results)
        self.assertEqual(total_reserved, 1500.0)

    def test_pe_08_add_remaining_capacity(self):
        """
        D4: Verify ADD checks subtract current exposure from capacity limit.
        """
        self.portfolio.initialize_portfolio(20000.0)
        
        # Position exists with $4,000 value (quantity 0.066666, entry price 60000)
        self.portfolio.update_position("BTC/USDC", 0.066666, 60000.0)
        
        marks = {"BTC/USDC": 60000.0}
        get_mark = lambda s: marks.get(s)
        
        risk_settings = load_risk_settings()
        _, lane_settings = load_lane_settings("lane_1")
        import dataclasses
        # Limit to 25% max position = $6,000 limit ($24,000 total equity)
        lane_settings = dataclasses.replace(lane_settings, max_position_pct=25.0)

        # ADD of $3,000 must be scaled down to $2,000! ($6,000 capacity - $4,000 current = $2,000)
        prop = AllocationProposal(
            proposal_id="prop-add",
            symbol="BTC/USDC",
            action="ADD",
            requested_risk_fraction=0.50,
            requested_notional=3000.0
        )
        res = self.portfolio.allocate(prop, get_mark, risk_settings, lane_settings, "test_sha")
        self.assertEqual(res.decision, "MODIFY_DOWN")
        self.assertAlmostEqual(res.reserved_capital, 2000.0, places=1)

    def test_pe_09_link_allocation_execution_intent(self):
        """
        D5: Verify PENDING allocations without relative ExecutionIntents are marked RELEASED.
        """
        self.portfolio.initialize_portfolio(10000.0)
        
        prop_id_1 = str(uuid.uuid4())
        prop_id_2 = str(uuid.uuid4())
        
        # Save a PENDING allocation with ID 'alloc-xyz-1'
        with self.portfolio._get_db_cursor_context() as cur:
            self.portfolio._persist_allocation_audit_tx(
                cur,
                allocation_id="alloc-xyz-1",
                proposal_id=prop_id_1,
                symbol="BTC/USDC",
                action="OPEN",
                req_risk=0.10,
                app_risk=0.10,
                req_notional=1000.0,
                app_notional=1000.0,
                reserved=1000.0,
                status="PENDING",
                reason_codes=[],
                port_version=1,
                port_digest="digest-1",
                marks_provenance={},
                config_hash="v1",
                code_sha="test_sha"
            )
        self.portfolio.reserve_capital("alloc-xyz-1", "USDC", 1000.0)
        
        # Save another PENDING allocation with ID 'alloc-xyz-2' (this one HAS an ExecutionIntent!)
        with self.portfolio._get_db_cursor_context() as cur:
            self.portfolio._persist_allocation_audit_tx(
                cur,
                allocation_id="alloc-xyz-2",
                proposal_id=prop_id_2,
                symbol="BTC/USDC",
                action="OPEN",
                req_risk=0.10,
                app_risk=0.10,
                req_notional=1000.0,
                app_notional=1000.0,
                reserved=1000.0,
                status="PENDING",
                reason_codes=[],
                port_version=2,
                port_digest="digest-2",
                marks_provenance={},
                config_hash="v1",
                code_sha="test_sha"
            )
        self.portfolio.reserve_capital("alloc-xyz-2", "USDC", 1000.0)

        # Write ExecutionIntent linking alloc-xyz-2
        client_ord_id = f"client-ord-xyz-{uuid.uuid4()}"
        with self.portfolio._get_db_cursor_context() as cur:
            cur.execute("""
                INSERT INTO execution_intents (execution_intent_id, risk_decision_id, mode, symbol, action, side, quantity, order_type, client_order_id, expires_at, allocation_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), prop_id_2, "paper", "BTC/USDC", "OPEN", "BUY", 0.1, "MARKET", client_ord_id, (datetime.now(UTC) + timedelta(minutes=5)).isoformat(), "alloc-xyz-2"))

        # Reconcile orphan reservations
        self.portfolio.reconcile_orphan_reservations()
        
        # Verify alloc-xyz-1 is RELEASED
        with self.portfolio._get_db_cursor_context() as cur:
            cur.execute("SELECT status FROM portfolio_allocations WHERE allocation_id = 'alloc-xyz-1'")
            self.assertEqual(cur.fetchone()["status"], "RELEASED")
            
            # Verify alloc-xyz-2 remains PENDING
            cur.execute("SELECT status FROM portfolio_allocations WHERE allocation_id = 'alloc-xyz-2'")
            self.assertEqual(cur.fetchone()["status"], "PENDING")

    def test_pe_10_risk_fraction_bound(self):
        """
        D7: Verify requested_risk_fraction bounds approved_notional and approved_risk_fraction.
        """
        self.portfolio.initialize_portfolio(10000.0)
        
        marks = {"BTC/USDC": 60000.0}
        get_mark = lambda s: marks.get(s)
        
        risk_settings = load_risk_settings()
        _, lane_settings = load_lane_settings("lane_1")
        import dataclasses
        lane_settings = dataclasses.replace(lane_settings, max_position_pct=80.0)

        # Requested risk fraction is 1% ($100.0 limit), but requested_notional is $5,000.
        # Allocator must MODIFY_DOWN to $100.0!
        prop = AllocationProposal(
            proposal_id="prop-risk-bound",
            symbol="BTC/USDC",
            action="OPEN",
            requested_risk_fraction=0.01, # 1% limit
            requested_notional=5000.0
        )
        res = self.portfolio.allocate(prop, get_mark, risk_settings, lane_settings, "test_sha")
        
        self.assertEqual(res.decision, "MODIFY_DOWN")
        self.assertEqual(res.reserved_capital, 10000.0 * 0.01) # exactly 100.0!

    def test_pe_11_lane_setup_no_destructive_reset(self):
        """
        E1: Verify that initializing a new lane does NOT reset or mutate the existing global portfolio.
        """
        self.portfolio.initialize_portfolio(5000.0)
        self.portfolio.update_position("BTC/USDC", 0.1, 50000.0)
        self.portfolio.reserve_capital("alloc-dummy", "USDC", 500.0)

        get_mark = lambda s: 50000.0
        snap_before = self.portfolio.load_portfolio_snapshot(get_mark)

        # Initialize a new lane using PaperExecutor (which uses initialize_lane)
        from app.executor import PaperExecutor
        pg_url = os.getenv("TEST_POSTGRES_URL")
        if not pg_url:
            self.skipTest("TEST_POSTGRES_URL is required to run real Postgres E1 test")
            
        executor = PaperExecutor(db_url=pg_url)
        executor.initialize_lane("new_test_lane", 10000.0)

        # Reload snapshot and verify it is completely identical
        snap_after = self.portfolio.load_portfolio_snapshot(get_mark)
        self.assertEqual(snap_after.equity, snap_before.equity)
        self.assertEqual(snap_after.digest, snap_before.digest)
        self.assertEqual(snap_after.cash.get("USDC"), 5000.0)
        self.assertEqual(snap_after.reserved.get("USDC"), 500.0)
        self.assertEqual(snap_after.positions["BTC/USDC"]["quantity"], 0.1)

    def test_pe_12_concurrent_risk_budget_consumption_multi_quote(self):
        """
        E2: Verify that PENDING allocations consume risk budget, gross exposure, and concentration limits.
        """
        self.portfolio.initialize_portfolio(10000.0)
        
        # We manually insert a PENDING allocation on BTC/USDC
        with self.portfolio._get_db_cursor_context() as cur:
            self.portfolio._persist_allocation_audit_tx(
                cur,
                allocation_id="alloc-pending-btc",
                proposal_id="prop-pending-btc",
                symbol="BTC/USDC",
                action="OPEN",
                req_risk=0.40,
                app_risk=0.40,
                req_notional=4000.0,
                app_notional=4000.0,
                reserved=4000.0,
                status="PENDING",
                reason_codes=[],
                port_version=1,
                port_digest="",
                marks_provenance={},
                config_hash="v1",
                code_sha="test_sha"
            )
        self.portfolio.reserve_capital("alloc-pending-btc", "USDC", 4000.0)

        get_mark = lambda s: 50000.0
        snap = self.portfolio.load_portfolio_snapshot(get_mark)
        
        # Pending allocation of $4,000 must be included in gross exposure and risk budget!
        self.assertEqual(snap.gross_exposure, 4000.0)
        self.assertEqual(snap.risk_budget_consumed, 0.40)
        self.assertEqual(snap.concentration, 40.0) # 4000 / 10000 * 100

        # Now, try to allocate another proposal of $7,000.
        # Since $4,000 is already pending, remaining gross exposure is $50,000 - $4,000 = $46,000.
        # But our maximum allowed position pct is 80% ($8,000).
        # And our symbol concentration is capped at 30% of equity ($3,000).
        # Since BTC already has $4,000 pending, the concentration limit ($3,000) is exceeded!
        # So a new OPEN for BTC/USDC should be modified down to $0.0 (below min notional) or scaled down to $0.0!
        risk_settings = load_risk_settings()
        _, lane_settings = load_lane_settings("lane_1")
        
        prop = AllocationProposal(
            proposal_id="prop-new-btc",
            symbol="BTC/USDC",
            action="OPEN",
            requested_risk_fraction=0.30,
            requested_notional=3000.0
        )
        res = self.portfolio.allocate(prop, get_mark, risk_settings, lane_settings, "test_sha")
        self.assertEqual(res.decision, "REJECT")
        self.assertIn("BELOW_MINIMUM_NOTIONAL", res.reason_codes)

    def test_pe_13_single_transactional_boundary_failure_injection(self):
        """
        E3: Verify PostgreSQL single transactional boundary and failure injection rollback.
        """
        pg_url = os.getenv("TEST_POSTGRES_URL")
        if not pg_url:
            self.skipTest("TEST_POSTGRES_URL is required to run real Postgres E3 test")

        from app.executor import PaperExecutor
        executor = PaperExecutor(db_url=pg_url)
        executor.initialize_lane("failure_lane", 5000.0)
        executor.portfolio_engine.initialize_portfolio(5000.0)

        # Create a mock pending allocation
        alloc_id = str(uuid.uuid4())
        with executor.portfolio_engine._get_db_cursor_context() as cur:
            executor.portfolio_engine._persist_allocation_audit_tx(
                cur,
                allocation_id=alloc_id,
                proposal_id="prop-err-inj",
                symbol="BTC/USDC",
                action="OPEN",
                req_risk=0.10,
                app_risk=0.10,
                req_notional=500.0,
                app_notional=500.0,
                reserved=500.0,
                status="PENDING",
                reason_codes=[],
                port_version=1,
                port_digest="",
                marks_provenance={},
                config_hash="v1",
                code_sha="test_sha"
            )
            cur.execute("INSERT INTO portfolio_cash (currency, cash, reserved) VALUES ('USDC', 5000.0, 500.0) ON CONFLICT (currency) DO NOTHING")

        intent = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id="prop-err-inj",
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=0.01,
            client_order_id="client-err-inj-id",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            allocation_id=alloc_id
        )

        # Enable failure injection
        executor._test_failure_injection = True

        with self.assertRaises(RuntimeError):
            executor.execute_intent("failure_lane", intent, 50000.0)

        # Verify that the entire transaction rolled back and there is NO partial divergence!
        with executor.portfolio_engine._get_db_cursor_context() as cur:
            # Table paper_positions must be empty for this symbol
            cur.execute("SELECT COUNT(*) as count FROM paper_positions WHERE lane_id = 'failure_lane'")
            self.assertEqual(cur.fetchone()["count"], 0)

            # Table portfolio_allocations must remain PENDING (not COMMITTED)
            cur.execute("SELECT status FROM portfolio_allocations WHERE allocation_id = %s", (alloc_id,))
            self.assertEqual(cur.fetchone()["status"], "PENDING")

            # Table execution_intents must NOT contain this intent_id
            cur.execute("SELECT COUNT(*) as count FROM execution_intents WHERE execution_intent_id = %s", (intent.execution_intent_id,))
            self.assertEqual(cur.fetchone()["count"], 0)

    def test_pe_14_non_usdc_quote_execution_exact_notional(self):
        """
        E4: Verify non-USDC pair (BTC/EUR) quote execution, exact notional spending, and cash protection.
        """
        pg_url = os.getenv("TEST_POSTGRES_URL")
        if not pg_url:
            self.skipTest("TEST_POSTGRES_URL is required to run real Postgres E4 test")

        from app.executor import PaperExecutor
        executor = PaperExecutor(db_url=pg_url)
        executor.initialize_lane("eur_lane", 5000.0)
        executor.portfolio_engine.initialize_portfolio(5000.0)

        # Deposit EUR cash into the portfolio
        with executor.portfolio_engine._get_db_cursor_context() as cur:
            cur.execute("INSERT INTO portfolio_cash (currency, cash, reserved) VALUES ('EUR', 1000.0, 0.0) ON CONFLICT (currency) DO UPDATE SET cash = 1000.0, reserved = 0.0")

        alloc_id = str(uuid.uuid4())
        # Save a PENDING allocation of 500 EUR on BTC/EUR
        with executor.portfolio_engine._get_db_cursor_context() as cur:
            executor.portfolio_engine._persist_allocation_audit_tx(
                cur,
                allocation_id=alloc_id,
                proposal_id="prop-eur-test",
                symbol="BTC/EUR",
                action="OPEN",
                req_risk=0.10,
                app_risk=0.10,
                req_notional=500.0,
                app_notional=500.0,
                reserved=500.0,
                status="PENDING",
                reason_codes=[],
                port_version=1,
                port_digest="",
                marks_provenance={},
                config_hash="v1",
                code_sha="test_sha"
            )
            executor.portfolio_engine.reserve_capital(alloc_id, "EUR", 500.0, cur=cur)

        # We set EUR/USDC mark to 1.10
        executor.update_market_mark("EUR/USDC", 1.10)
        executor.update_market_mark("BTC/EUR", 50000.0)

        intent = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id="prop-eur-test",
            mode="paper",
            symbol="BTC/EUR",
            action="OPEN",
            side="BUY",
            quantity=0.01,
            client_order_id="client-order-eur-id",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            allocation_id=alloc_id
        )

        res = executor.execute_intent("eur_lane", intent, 50000.0)
        self.assertEqual(res.status, "FILLED")

        # Verify that actual spent is at most reserved_capital (500 EUR)
        total_cost = res.filled_quantity * res.average_fill_price + res.fee
        self.assertLessEqual(total_cost, 500.0)

        # Verify EUR cash and reserved cash in portfolio_cash
        with executor.portfolio_engine._get_db_cursor_context() as cur:
            cur.execute("SELECT cash, reserved FROM portfolio_cash WHERE currency = 'EUR'")
            row = cur.fetchone()
            eur_cash = float(row["cash"])
            eur_reserved = float(row["reserved"])
            
            self.assertAlmostEqual(eur_cash, 1000.0 - total_cost)
            self.assertEqual(eur_reserved, 0.0)

            # USDC cash must remain untouched (should still be 5000.0)
            cur.execute("SELECT cash FROM portfolio_cash WHERE currency = 'USDC'")
            usdc_cash = float(cur.fetchone()["cash"])
            self.assertEqual(usdc_cash, 5000.0)

if __name__ == "__main__":
    unittest.main()
