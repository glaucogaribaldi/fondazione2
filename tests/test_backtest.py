import unittest
import uuid
import sqlite3
from datetime import datetime, UTC, timedelta
from app.backtest import CoinbaseReplayEngine, HistoricalDataset
from app.products import CoinbaseProduct, registry

class TestBacktestEngine(unittest.TestCase):

    def setUp(self):
        self.db_url = "sqlite:///:memory:"
        self.engine = CoinbaseReplayEngine(db_url=self.db_url)
        
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

        # Insert some historical valid candles in SQLite DB
        self.start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        self.candles_raw = [
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (self.start + timedelta(minutes=0)).isoformat(), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0, "quality_state": "VALID"},
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (self.start + timedelta(minutes=5)).isoformat(), "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 10.0, "quality_state": "VALID"},
            {"product_id": "BTC-USDC", "canonical_symbol": "BTC/USDC", "granularity": 300, "candle_open": (self.start + timedelta(minutes=10)).isoformat(), "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 10.0, "quality_state": "VALID"}
        ]
        
        with self.engine._get_db_cursor_context() as cur:
            for c in self.candles_raw:
                cur.execute("""
                    INSERT INTO historical_candles (
                        product_id, canonical_symbol, granularity, candle_open, open, high, low, close, volume, quality_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (c["product_id"], c["canonical_symbol"], c["granularity"], c["candle_open"], c["open"], c["high"], c["low"], c["close"], c["volume"], c["quality_state"]))

    def test_strict_no_lookahead_enforcement(self):
        """
        Blocker F / Test 12: Every data accessor MUST reject or exclude observations with timestamp >= T.
        """
        end = self.start + timedelta(minutes=15)
        dataset = self.engine.load_dataset_from_db(["BTC/USDC"], 300, self.start, end, end)
        
        # At clock time T = start + 5 minutes, we must only see the 1st candle (open strictly before T)
        t_clock = self.start + timedelta(minutes=5)
        as_of_t = dataset.get_as_of(t_clock)
        
        self.assertEqual(len(as_of_t), 1)
        self.assertEqual(as_of_t[0]["candle_open"], self.start.isoformat())

    def test_chronological_replay_and_digest_reproducibility(self):
        """
        Blocker G / H / Test 13 & 14: Replay must execute chronologically,
        and two identical runs must produce exactly the same result digest.
        """
        end = self.start + timedelta(minutes=15)
        dataset = self.engine.load_dataset_from_db(["BTC/USDC"], 300, self.start, end, end)
        
        # Execute run 1
        res_1 = self.engine.run_backtest(dataset, initial_cash=10000.0, seed=42)
        
        # Execute run 2 (exact same dataset, seed, config)
        res_2 = self.engine.run_backtest(dataset, initial_cash=10000.0, seed=42)
        
        # Verifies that both runs produced exactly identical result digests (reproducibility!)
        self.assertEqual(res_1["result_digest"], res_2["result_digest"])

    def test_config_seed_changes_digest(self):
        """
        Test 15: Different config/seed changes result digest as expected.
        """
        end = self.start + timedelta(minutes=15)
        dataset = self.engine.load_dataset_from_db(["BTC/USDC"], 300, self.start, end, end)
        
        # Run with seed=42
        res_1 = self.engine.run_backtest(dataset, initial_cash=10000.0, seed=42)
        
        # Run with seed=100
        res_2 = self.engine.run_backtest(dataset, initial_cash=10000.0, seed=100)
        
        # Verifies that different seed results in a different config hash
        self.assertNotEqual(res_1["config_hash"], res_2["config_hash"])

    def test_backtest_storage_isolation(self):
        """
        Test 16: Backtest execution must NOT alter any PAPER runtime state or tables.
        """
        end = self.start + timedelta(minutes=15)
        dataset = self.engine.load_dataset_from_db(["BTC/USDC"], 300, self.start, end, end)
        
        # Run backtest
        self.engine.run_backtest(dataset)
        
        # Verify that PAPER balances and PAPER positions tables are completely empty / untouched
        with self.engine._get_db_cursor_context() as cur:
            bal_count = cur.execute("SELECT count(*) FROM paper_balances").fetchone()
            pos_count = cur.execute("SELECT count(*) FROM paper_positions").fetchone()
            self.assertEqual(bal_count[0], 0)
            self.assertEqual(pos_count[0], 0)

if __name__ == "__main__":
    unittest.main()
