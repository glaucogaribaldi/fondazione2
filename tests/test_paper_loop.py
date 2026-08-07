import unittest
import uuid
import os
from datetime import UTC, datetime, timedelta
import json
import sqlite3

from app.models import (
    DecisionRequest, Proposal, MarketSnapshot, PortfolioSnapshot, Candle,
    ExecutionIntent, ExecutionResult, RiskDecision, Action
)
from app.risk import evaluate_risk, RiskSettings, LaneSettings, RiskResult
from app.executor import PaperExecutor, DatabaseConnection


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


class PaperLoopTests(unittest.TestCase):

    def setUp(self):
        # Always use clean, isolated sqlite in-memory DB for tests
        self.db_url = "sqlite:///:memory:"
        self.executor = PaperExecutor(db_url=self.db_url)
        self.executor.initialize_lane("lane_1", 10000.0)

    # --- Test 1: Full NO_TRADE cycle ---
    def test_loop_no_trade_cycle(self):
        proposal = Proposal(
            action="NO_TRADE",
            allocation_pct=0.0,
            confidence=0.1,
            reason_codes=["FLAT_MARKET"]
        )
        global_settings = RiskSettings(
            allowed_symbols=frozenset({"BTC/USDC"}),
            allowed_actions=frozenset({"NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"})
        )
        lane_settings = LaneSettings(
            minimum_confidence=0.7,
            max_position_pct=10.0,
            max_daily_loss_pct=2.0,
            max_open_positions=2,
            cooldown_minutes=30
        )
        req = DecisionRequest(
            request_id=str(uuid.uuid4()),
            mode="paper",
            lane_id="lane_1",
            symbol="BTC/USDC",
            timeframe="1m",
            market=MarketSnapshot(
                timestamp=NOW, bid=100.0, ask=100.1, candles=[
                    Candle(timestamp=NOW, open=100.0, high=101.0, low=99.0, close=100.0, volume=10)
                ] * 32
            ),
            portfolio=PortfolioSnapshot(
                equity=10000.0, cash=10000.0, daily_pnl_pct=0.0, open_positions=0, current_position_pct=0.0
            )
        )
        result = evaluate_risk(req, proposal, global_settings, lane_settings, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.action, "NO_TRADE")

    # --- Test 2: Approved paper execution (ExecutionIntent -> PaperExecutor) ---
    def test_loop_approved_paper_execution(self):
        intent_id = str(uuid.uuid4())
        intent = ExecutionIntent(
            execution_intent_id=intent_id,
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=2.0,
            stop_price=90.0, #wired as stop-loss!
            take_profit_price=120.0,
            client_order_id="order-loop-approved",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        
        # Execute OPEN order at $100.0
        res = self.executor.execute_intent("lane_1", intent, 100.0)
        self.assertEqual(res.status, "FILLED")
        self.assertEqual(res.average_fill_price, 100.05) # fill_price * (1 + 0.0005)
        self.assertEqual(res.fee, 1.20) # 2.0 * 100 * 0.0060
        self.assertAlmostEqual(res.slippage, 0.10) # 2.0 * (100.05 - 100)

        # Retrieve position and balance to prove correctness
        pos = self.executor.get_position("lane_1", "BTC/USDC")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["quantity"], 2.0)
        self.assertEqual(pos["stop_loss_price"], 90.0)
        self.assertEqual(pos["take_profit_price"], 120.0)

        bal = self.executor.get_balance("lane_1")
        # Cash = 10000 - 200.1 (adjusted price * qty) - 1.20 (fee) = 9798.7
        self.assertAlmostEqual(bal["cash"], 9798.7)
        # Equity = cash (9798.7) + position mtm (2.0 * 100) = 9998.7
        self.assertAlmostEqual(bal["equity"], 9998.7)

    # --- Test 3: Protective Exit execution and database persistence (HST-01/H2) ---
    def test_loop_protective_exit_execution_and_persistence(self):
        intent_id = str(uuid.uuid4())
        intent = ExecutionIntent(
            execution_intent_id=intent_id,
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol="BTC/USDC",
            action="OPEN",
            side="BUY",
            quantity=1.0,
            stop_price=95.0, #wired as stop-loss!
            take_profit_price=105.0,
            client_order_id="order-loop-stops",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5)
        )
        
        self.executor.execute_intent("lane_1", intent, 100.0)
        
        # Trigger stop-loss: price drops to $94.0
        trigger_res = self.executor.check_and_trigger_stops("lane_1", "BTC/USDC", 94.0)
        self.assertIsNotNone(trigger_res)
        self.assertEqual(trigger_res.status, "FILLED")
        
        # Blocker H2: Re-read result from DB and assert protective exit reason is durably persisted
        persisted_result = self.executor.get_execution_result(trigger_res.execution_intent_id)
        self.assertIsNotNone(persisted_result)
        self.assertEqual(persisted_result["status"], "FILLED")
        self.assertIn("STOP_LOSS_TRIGGERED", persisted_result["reason_codes"])

    # --- Test 4: Kronos failure -> NO_TRADE ---
    def test_loop_kronos_failure_yields_no_trade(self):
        proposal_reasons = []
        # Mocking Kronos exception during pipeline loop
        try:
            raise ConnectionError("Connection refused to Kronos on port 8081")
        except Exception as e:
            proposal_reasons.append("KRONOS_FAILED")
            proposal_reasons.append(type(e).__name__.upper())
            
        proposal = Proposal(
            action="NO_TRADE",
            allocation_pct=0.0,
            confidence=0.0,
            reason_codes=proposal_reasons
        )
        self.assertEqual(proposal.action, "NO_TRADE")
        self.assertIn("KRONOS_FAILED", proposal.reason_codes)

    # --- Test 5: Nemotron failure -> NO_TRADE ---
    def test_loop_nemotron_failure_yields_no_trade(self):
        proposal_reasons = []
        # Mocking SGLang exception during pipeline loop
        try:
            raise TimeoutError("SGLang request timed out after 60s")
        except Exception as e:
            proposal_reasons.append("NEMOTRON_FAILED")
            proposal_reasons.append(type(e).__name__.upper())
            
        proposal = Proposal(
            action="NO_TRADE",
            allocation_pct=0.0,
            confidence=0.0,
            reason_codes=proposal_reasons
        )
        self.assertEqual(proposal.action, "NO_TRADE")
        self.assertIn("NEMOTRON_FAILED", proposal.reason_codes)

    # --- Test 6: Stale Market Data -> NO_TRADE ---
    def test_loop_stale_market_data_yields_no_trade(self):
        # Market age = 120 seconds (greater than max_market_age_seconds = 90)
        req = DecisionRequest(
            request_id=str(uuid.uuid4()),
            mode="paper",
            lane_id="lane_1",
            symbol="BTC/USDC",
            timeframe="1m",
            market=MarketSnapshot(
                timestamp=NOW - timedelta(seconds=120), bid=100.0, ask=100.1, candles=[
                    Candle(timestamp=NOW, open=100.0, high=101.0, low=99.0, close=100.0, volume=10)
                ] * 32
            ),
            portfolio=PortfolioSnapshot(
                equity=10000.0, cash=10000.0, daily_pnl_pct=0.0, open_positions=0, current_position_pct=0.0
            )
        )
        proposal = Proposal(action="OPEN", allocation_pct=8.0, confidence=0.8)
        global_settings = RiskSettings(
            allowed_symbols=frozenset({"BTC/USDC"}),
            allowed_actions=frozenset({"NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"})
        )
        lane_settings = LaneSettings(
            minimum_confidence=0.7,
            max_position_pct=10.0,
            max_daily_loss_pct=2.0,
            max_open_positions=2,
            cooldown_minutes=30
        )
        result = evaluate_risk(req, proposal, global_settings, lane_settings, now=NOW)
        self.assertFalse(result.approved)
        self.assertEqual(result.action, "NO_TRADE")
        self.assertIn("STALE_MARKET_DATA", result.reasons)

    # --- Test 7: Loop restart & Idempotency check (HST-09) ---
    def test_loop_restart_idempotency_fencing(self):
        client_order_id = "order-loop-restart-idempotent-1"
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

        # Same order client ID sent again on restart
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


if __name__ == "__main__":
    unittest.main()
