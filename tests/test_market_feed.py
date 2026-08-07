import importlib.util
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "services" / "market-feed" / "app" / "helpers.py"
SPEC = importlib.util.spec_from_file_location("market_feed", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MarketFeedTests(unittest.TestCase):
    def test_product_quote_currency_is_preserved(self):
        self.assertEqual(MODULE.product_to_symbol("BTC-USDT"), "BTC/USDT")
        self.assertEqual(MODULE.product_to_symbol("ETH-USDC"), "ETH/USDC")

    def test_current_candle_is_excluded(self):
        now = int(datetime(2026, 1, 1, 12, 2, tzinfo=UTC).timestamp())
        current_start = now - (now % 300)
        previous_start = current_start - 300
        rows = [
            [current_start, 99, 102, 100, 101, 10],
            [previous_start, 98, 101, 99, 100, 9],
        ]
        candles = MODULE.normalize_candles(rows, now, 300, 96)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["close"], 100)

    def test_invalid_product_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.product_to_symbol("BTCUSDT")

    def test_granularity_uses_kronos_timeframe(self):
        self.assertEqual(MODULE.timeframe_label(300), "5m")
        self.assertEqual(MODULE.timeframe_label(3_600), "1h")
        with self.assertRaises(ValueError):
            MODULE.timeframe_label(120)
