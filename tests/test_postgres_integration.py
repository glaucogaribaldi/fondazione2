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
        B3: Verify gap recovery bounds retries to 3 and marks gaps as EXPLICIT_UNAVAILABLE or RESOLVED.
        """
        mock_fetch.return_value = []
        
        start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 10, 15, tzinfo=UTC)
        
        res = asyncio.run(self.backfill_engine.scan_and_recover_gaps(self.prod, 300, start, end))
        
        self.assertEqual(res, 0) # 0 resolved
        
        # Verify no gap remains in DETECTED state. They must all be EXPLICIT_UNAVAILABLE!
        with self.backfill_engine._get_db_cursor_context() as cur:
            cur.execute("SELECT status, attempts FROM historical_gaps")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 3)
            for r in rows:
                self.assertEqual(r["status"], "EXPLICIT_UNAVAILABLE")
                self.assertEqual(r["attempts"], 3) # Tried 3 times in bounded retries!

    def test_pg_04_dataset_provenance_real(self):
        """
        B4: Load of two identical datasets/provenance must produce the same deterministic dataset ID.
        """
        raw = [[1700000000, 99.0, 101.0, 100.0, 100.5, 10.0]]
        self.backfill_engine._ingest_and_validate_candles(self.prod, 300, raw)
        
        start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 10, 15, tzinfo=UTC)
        
        ds1 = self.replay_engine.load_dataset_from_db(["BTC/USDC"], 300, start, end, end, code_sha="my_sha_123", config_hash="my_config_hash")
        ds2 = self.replay_engine.load_dataset_from_db(["BTC/USDC"], 300, start, end, end, code_sha="my_sha_123", config_hash="my_config_hash")
        
        self.assertEqual(ds1.dataset_id, ds2.dataset_id)
        self.assertFalse(ds1.dataset_id.startswith("ds-run-"))
        self.assertTrue(len(ds1.dataset_id) > 10)
        
        ds3 = self.replay_engine.load_dataset_from_db(["BTC/USDC"], 300, start, end, end, code_sha="different_sha", config_hash="my_config_hash")
        self.assertNotEqual(ds1.dataset_id, ds3.dataset_id)

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
            
        end = start + timedelta(minutes=25)
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
            self.assertEqual(float(row1["price"]), 97.0)
            expected_qty = 1000.0 / 97.0
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
            
            expected_pnl = expected_qty * 8.0
            self.assertAlmostEqual(float(row2["realized_pnl"]), expected_pnl, places=6)
            self.assertAlmostEqual(float(row2["unrealized_pnl"]), 0.0, places=6)
            self.assertAlmostEqual(float(row2["equity"]), expected_cash_after, places=6)
            
        self.assertAlmostEqual(result["realized_pnl"], expected_pnl, places=6)
        self.assertAlmostEqual(result["final_equity"], expected_cash_after, places=6)

if __name__ == "__main__":
    unittest.main()
