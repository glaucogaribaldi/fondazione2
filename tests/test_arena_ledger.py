import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "services" / "arena" / "app" / "ledger.py"
SPEC = importlib.util.spec_from_file_location("arena_ledger", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ExecutionSettings = MODULE.ExecutionSettings
PaperLedger = MODULE.PaperLedger


class PaperLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = PaperLedger(
            str(Path(self.tempdir.name) / "arena.db"), ["lane_1", "lane_2"], 1_000
        )
        self.settings = ExecutionSettings(fee_bps=10, slippage_bps=0)

    def tearDown(self):
        self.ledger.connection.close()
        self.tempdir.cleanup()

    def test_buy_is_persistent_and_idempotent(self):
        decision = {
            "decision": "BUY",
            "approved_by_risk_engine": True,
            "allocation_pct": 10,
            "reason_codes": ["APPROVED"],
        }
        first = self.ledger.execute(
            "request-1", "lane_1", "BTC/USDC", decision, 99, 100, self.settings
        )
        second = self.ledger.execute(
            "request-1", "lane_1", "BTC/USDC", decision, 99, 100, self.settings
        )
        self.assertEqual(first["event"]["id"], second["event"]["id"])
        self.assertAlmostEqual(first["event"]["quantity"], 1)
        self.assertEqual(len(self.ledger.events()), 1)
        self.assertLess(first["portfolio"]["cash"], 900)

    def test_sell_without_position_becomes_hold(self):
        decision = {
            "decision": "SELL",
            "approved_by_risk_engine": True,
            "allocation_pct": 10,
            "reason_codes": [],
        }
        result = self.ledger.execute(
            "request-2", "lane_2", "BTC/USDC", decision, 99, 100, self.settings
        )
        self.assertEqual(result["event"]["action"], "HOLD")
        self.assertEqual(result["event"]["quantity"], 0)

    def test_ranking_contains_all_lanes(self):
        ranking = self.ledger.ranking()
        self.assertEqual([row["rank"] for row in ranking], [1, 2])
        self.assertEqual({row["lane_id"] for row in ranking}, {"lane_1", "lane_2"})

    def test_separate_smoke_ledger_cannot_mark_live_paper_positions(self):
        decision = {
            "decision": "BUY",
            "approved_by_risk_engine": True,
            "allocation_pct": 1,
            "reason_codes": ["BOOTSTRAP_PROBE"],
        }
        self.ledger.execute(
            "coinbase-real", "lane_1", "BTC/USDT", decision, 64_740, 64_755, self.settings
        )
        paper_equity = self.ledger.snapshot("lane_1", "BTC/USDT", 64_750)["equity"]

        smoke_ledger = PaperLedger(
            str(Path(self.tempdir.name) / "smoke.db"), ["lane_1", "lane_2"], 1_000
        )
        try:
            smoke_ledger.snapshot("lane_1", "BTC/USDT", 51_595)
            unchanged_equity = next(
                row["equity"] for row in self.ledger.ranking() if row["lane_id"] == "lane_1"
            )
        finally:
            smoke_ledger.connection.close()

        self.assertAlmostEqual(unchanged_equity, paper_equity)
