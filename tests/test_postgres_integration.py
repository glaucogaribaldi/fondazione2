import os
import sys
import unittest
import asyncio
from datetime import datetime, UTC, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.backfill import CoinbaseBackfillEngine
from app.backtest import CoinbaseReplayEngine, HistoricalDataset
from app.products import registry, CoinbaseProduct
from unittest.mock import MagicMock, AsyncMock, patch

class TestPostgresIntegration(unittest.TestCase):
    def setUp(self):
        self.postgres_url = os.environ.get("TEST_POSTGRES_URL")
        if not self.postgres_url:
            raise unittest.SkipTest("TEST_POSTGRES_URL environment variable is not set. Skipping real PostgreSQL integration tests.")
        
        self.backfill_engine = CoinbaseBackfillEngine(db_url=self.postgres_url)
        self.replay_engine = CoinbaseReplayEngine(db_url=self.postgres_url)
        
        # Svuota le tabelle per isolare i test (TRUNCATE CASCADE)
        with self.backfill_engine._get_db_cursor_context() as cur:
            cur.execute("TRUNCATE TABLE historical_candles CASCADE")
            cur.execute("TRUNCATE TABLE historical_backfill_checkpoints CASCADE")
            cur.execute("TRUNCATE TABLE historical_gaps CASCADE")
            cur.execute("TRUNCATE TABLE dataset_versions CASCADE")
            cur.execute("TRUNCATE TABLE replay_runs CASCADE")

        # Inserisci prodotto mock nel registry per coerenza
        registry._products.clear()
        self.prod = CoinbaseProduct(
            product_id="BTC-USDC",
            product_type="SPOT",
            base_currency="BTC",
            quote_currency="USDC",
            canonical_asset="BTC",
            canonical_symbol="BTC/USDC",
            execution_product_id="BTC-USDC",
            market_data_product_id="BTC-USD",
            market_data_is_proxy=True,
            is_disabled=False,
            trading_disabled=False,
            cancel_only=False,
            limit_only=False,
            post_only=False,
            base_increment=0.00000001,
            quote_increment=0.01,
            min_market_funds=1.0,
            market_data_eligible=True,
            paper_execution_eligible=True,
            updated_at=datetime.now(UTC)
        )
        registry._products["BTC-USDC"] = self.prod
        registry._initialized = True

    def test_pg_01_historical_ingest_and_query(self):
        """
        B1: Verify real PostgreSQL candle ingestion, validation, and querying.
        """
        raw_candles = [
            [1700000000, 99.0, 101.0, 100.0, 100.5, 10.0],  # Valid
            [1700000300, 102.0, 98.0, 100.0, 101.0, 10.0]   # Invalid (high < low)
        ]
        
        self.backfill_engine._ingest_and_validate_candles(self.prod, 300, raw_candles)
        
        # Check database rows
        with self.backfill_engine._get_db_cursor_context() as cur:
            cur.execute("SELECT candle_open, quality_state FROM historical_candles ORDER BY candle_open ASC")
            rows = cur.fetchall()
            
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["quality_state"], "VALID")
            self.assertEqual(rows[1]["quality_state"], "QUARANTINED")

    @patch("app.backfill.CoinbaseBackfillEngine._fetch_coinbase_candles_raw", new_callable=AsyncMock)
    def test_pg_02_checkpoint_partial_resume(self, mock_fetch):
        """
        B2: Checkpoint partial resume must fetch starting strictly from last_processed_time.
        """
        mock_fetch.return_value = []
        
        start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
        last_processed = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)
        
        # Save a PENDING checkpoint that was partially processed up to 11:00
        self.backfill_engine._save_checkpoint("BTC-USDC", 300, start, end, last_processed, "PENDING")
        
        # Trigger the backfill
        res = asyncio.run(self.backfill_engine.backfill_product("BTC/USDC", start, end, 300, resume=True))
        self.assertTrue(res)
        
        # Verify first call's start range begins EXACTLY from last_processed!
        mock_fetch.assert_called()
        first_call_args = mock_fetch.call_args_list[0][0]
        start_passed = first_call_args[1]
        self.assertEqual(start_passed, last_processed)

    @patch("app.backfill.CoinbaseBackfillEngine._fetch_coinbase_candles_raw", new_callable=AsyncMock)
    def test_pg_03_gap_recovery_bounded_and_state(self, mock_fetch):
        """
        C1: Verify gap recovery partial retry logic.
        Attempt 1 returns only Gap A, Attempt 2 returns B, Attempt 3 returns nothing (C is still missing).
        Result: A = RESOLVED, B = RESOLVED, C = EXPLICIT_UNAVAILABLE.
        """
        start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 10, 15, tzinfo=UTC)
        
        # We have 3 gaps: 10:00 (1786183200), 10:05 (1786183500), 10:10 (1786183800)
        # Mock fetch to simulate partial recovery returns:
        mock_fetch.side_effect = [
            [[1786183200, 99.0, 101.0, 100.0, 100.5, 10.0]],  # Attempt 1: only Gap A
            [[1786183500, 99.0, 101.0, 100.0, 100.5, 10.0]],  # Attempt 2: only Gap B
            []  # Attempt 3: C still missing
        ]
        
        res = asyncio.run(self.backfill_engine.scan_and_recover_gaps(self.prod, 300, start, end))
        
        # 2 gaps successfully resolved (A and B)
        self.assertEqual(res, 2)
        
        # Verify individual statuses in database
        with self.backfill_engine._get_db_cursor_context() as cur:
            cur.execute("SELECT gap_start, status, attempts FROM historical_gaps ORDER BY gap_start ASC")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 3)
            
            # Gap A (10:00) -> RESOLVED on 1st attempt
            self.assertEqual(rows[0]["status"], "RESOLVED")
            
            # Gap B (10:05) -> RESOLVED on 2nd attempt
            self.assertEqual(rows[1]["status"], "RESOLVED")
            
            # Gap C (10:10) -> EXPLICIT_UNAVAILABLE after 3 attempts
            self.assertEqual(rows[2]["status"], "EXPLICIT_UNAVAILABLE")
            self.assertEqual(rows[2]["attempts"], 3)

    def test_pg_04_dataset_provenance_real(self):
        """
        C2: Load dataset and execute run using dynamic authoritative CODE_SHA,
        verifying that X is valid, non-empty, and correctly matched in all contracts.
        Also verifies mismatch raises ValueError.
        """
        from app.backtest import get_current_code_sha
        sha_x = get_current_code_sha()
        
        self.assertIsNotNone(sha_x)
        self.assertNotEqual(sha_x, "unknown")
        self.assertEqual(len(sha_x), 40) # Valid 40-char SHA
        
        raw = [[1786183200, 99.0, 101.0, 100.0, 100.5, 10.0]]
        self.backfill_engine._ingest_and_validate_candles(self.prod, 300, raw)
        
        start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 10, 15, tzinfo=UTC)
        
        # Load dataset with SHA X
        ds = self.replay_engine.load_dataset_from_db(["BTC/USDC"], 300, start, end, end, code_sha=sha_x)
        self.assertEqual(ds.code_sha, sha_x)
        
        # Run backtest with SHA X (or None, which inherits ds.code_sha)
        result = self.replay_engine.run_backtest(ds, initial_cash=10000.0)
        
        # Verify same dynamic SHA matches in all places:
        # 1. Dataset ID determinism
        ds_alt = self.replay_engine.load_dataset_from_db(["BTC/USDC"], 300, start, end, end, code_sha=sha_x)
        self.assertEqual(ds.dataset_id, ds_alt.dataset_id)
        
        # 2. Database checks
        with self.replay_engine._get_db_cursor_context() as cur:
            # check dataset_versions
            cur.execute("SELECT code_sha FROM dataset_versions WHERE dataset_id = %s", (ds.dataset_id,))
            dv_sha = cur.fetchone()["code_sha"]
            self.assertEqual(dv_sha, sha_x)
            
            # check replay_runs
            cur.execute("SELECT code_sha FROM replay_runs WHERE run_id = %s", (result["run_id"],))
            rr_sha = cur.fetchone()["code_sha"]
            self.assertEqual(rr_sha, sha_x)
            
        # 3. Mismatch test (Atomic Provenance Check)
        different_sha = "a" * 40
        with self.assertRaises(ValueError):
            self.replay_engine.run_backtest(ds, initial_cash=10000.0, code_sha=different_sha)

    def test_pg_05_reconstruct_reproducible_accounting_run(self):
        """
        B5: Detailed numerical verification of cash, equity, PnL, fees, and ledger of an OPEN -> mark move -> CLOSE run.
        """
        start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        candles_raw = [
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (start + timedelta(minutes=0)).isoformat(), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0, "quality_state": "VALID"},
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (start + timedelta(minutes=5)).isoformat(), "open": 100.0, "high": 100.0, "low": 98.0, "close": 99.0, "volume": 10.0, "quality_state": "VALID"},
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (start + timedelta(minutes=10)).isoformat(), "open": 99.0, "high": 99.0, "low": 97.0, "close": 98.0, "volume": 10.0, "quality_state": "VALID"},
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (start + timedelta(minutes=15)).isoformat(), "open": 98.0, "high": 98.0, "low": 96.0, "close": 97.0, "volume": 10.0, "quality_state": "VALID"},
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (start + timedelta(minutes=20)).isoformat(), "open": 97.0, "high": 110.0, "low": 96.0, "close": 105.0, "volume": 10.0, "quality_state": "VALID"}
        ]
        
        for c in candles_raw:
            raw_format = [
                int(datetime.fromisoformat(c["candle_open"]).timestamp()),
                c["low"], c["high"], c["open"], c["close"], c["volume"]
            ]
            self.backfill_engine._ingest_and_validate_candles(self.prod, 300, [raw_format])
            
        end = start + timedelta(minutes=30)
        dataset = self.replay_engine.load_dataset_from_db(["BTC/USDC"], 300, start, end, end, code_sha="test_sha_123", config_hash="v1")
        
        result = self.replay_engine.run_backtest(
            dataset=dataset,
            initial_cash=10000.0,
            fee_rate=0.0,
            slippage_rate=0.0,
            seed=42,
            code_sha="test_sha_123"
        )
        
        self.assertEqual(result["trades_count"], 2)
        
        with self.replay_engine._get_db_cursor_context() as cur:
            cur.execute("SELECT * FROM replay_ledger WHERE run_id = %s ORDER BY id ASC", (result["run_id"],))
            ledger = cur.fetchall()
            
            self.assertEqual(len(ledger), 2)
            
            row1 = ledger[0]
            self.assertEqual(row1["action"], "OPEN")
            self.assertEqual(row1["side"], "BUY")
            self.assertEqual(float(row1["price"]), 98.0)
            expected_qty = 1000.0 / 98.0
            self.assertAlmostEqual(float(row1["quantity"]), expected_qty, places=6)
            self.assertAlmostEqual(float(row1["cash"]), 9000.0, places=6)
            self.assertAlmostEqual(float(row1["unrealized_pnl"]), 0.0, places=6)
            self.assertAlmostEqual(float(row1["realized_pnl"]), 0.0, places=6)
            self.assertAlmostEqual(float(row1["equity"]), 10000.0, places=6)
            
            row2 = ledger[1]
            self.assertEqual(row2["action"], "CLOSE")
            self.assertEqual(row2["side"], "SELL")
            self.assertEqual(float(row2["price"]), 105.0)
            self.assertAlmostEqual(float(row2["quantity"]), expected_qty, places=6)
            
            expected_proceeds = expected_qty * 105.0
            expected_cash_after = 9000.0 + expected_proceeds
            self.assertAlmostEqual(float(row2["cash"]), expected_cash_after, places=6)
            
            expected_pnl = expected_qty * 7.0
            self.assertAlmostEqual(float(row2["realized_pnl"]), expected_pnl, places=6)
            self.assertAlmostEqual(float(row2["unrealized_pnl"]), 0.0, places=6)
            self.assertAlmostEqual(float(row2["equity"]), expected_cash_after, places=6)
            
        self.assertAlmostEqual(result["realized_pnl"], expected_pnl, places=6)
        self.assertAlmostEqual(result["final_equity"], expected_cash_after, places=6)

if __name__ == "__main__":
    unittest.main()
