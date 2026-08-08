import unittest
import asyncio
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, UTC, timedelta
from app.backfill import CoinbaseBackfillEngine
from app.products import registry, CoinbaseProduct
from app.executor import DatabaseConnection

class TestBackfillEngine(unittest.TestCase):

    def setUp(self):
        self.db_url = "sqlite:///:memory:"
        self.engine = CoinbaseBackfillEngine(db_url=self.db_url)
        
        # Populate mock product
        registry._products.clear()
        p = CoinbaseProduct(
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
        registry._products["BTC-USDC"] = p
        registry._initialized = True

    def test_idempotent_ingest_and_impossible_ohlcv_quarantine(self):
        """
        Blocker D / B: Test data-quality validation and impossible OHLCV quarantining.
        """
        # Valid candles list
        raw_valid = [
            [1700000000, 99.0, 101.0, 100.0, 100.5, 10.0]
        ]
        
        # Ingest valid candle
        self.engine._ingest_and_validate_candles(registry.get_product("BTC/USDC"), 300, raw_valid)
        
        # Read from DB and verify quality_state is VALID
        with self.engine._get_db_cursor_context() as cur:
            row = cur.execute("SELECT quality_state FROM historical_candles").fetchone()
            self.assertEqual(row[0], "VALID")

        # Invalid/impossible candle (high < low)
        raw_invalid = [
            [1700000300, 102.0, 98.0, 100.0, 101.0, 10.0]
        ]
        
        # Ingest invalid candle
        self.engine._ingest_and_validate_candles(registry.get_product("BTC/USDC"), 300, raw_invalid)
        
        # Read from DB and verify quality_state is QUARANTINED
        with self.engine._get_db_cursor_context() as cur:
            row = cur.execute("SELECT quality_state FROM historical_candles WHERE candle_open = ?", (datetime.fromtimestamp(1700000300, tz=UTC).isoformat(),)).fetchone()
            self.assertEqual(row[0], "QUARANTINED")

    def test_idempotency_conconflict_upsert(self):
        """
        Blocker B: Repeated ingestion on the same key must be idempotent.
        """
        raw = [
            [1700000000, 99.0, 101.0, 100.0, 100.5, 10.0]
        ]
        
        self.engine._ingest_and_validate_candles(registry.get_product("BTC/USDC"), 300, raw)
        self.engine._ingest_and_validate_candles(registry.get_product("BTC/USDC"), 300, raw) # Repeated ingest
        
        with self.engine._get_db_cursor_context() as cur:
            rows = cur.execute("SELECT count(*) FROM historical_candles").fetchone()
            self.assertEqual(rows[0], 1)

    @patch("app.backfill.CoinbaseBackfillEngine._fetch_coinbase_candles_raw", new_callable=AsyncMock)
    def test_checkpoints_and_resume(self, mock_fetch):
        """
        Blocker C: Backfill must support restart/resume based on checkpoints.
        """
        mock_fetch.return_value = []
        
        # Save a completed checkpoint
        start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)
        
        self.engine._save_checkpoint("BTC-USDC", 300, start, end, end, "COMPLETED")
        
        # Running the backfill must immediately return True and NOT fetch anything!
        res = asyncio.run(self.engine.backfill_product("BTC/USDC", start, end, 300, resume=True))
        self.assertTrue(res)
        mock_fetch.assert_not_called()

if __name__ == "__main__":
    unittest.main()
