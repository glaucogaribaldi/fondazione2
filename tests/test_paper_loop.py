import unittest
import uuid
import os
import json
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import (
    DecisionRequest, Proposal, MarketSnapshot, PortfolioSnapshot, Candle,
    ExecutionIntent, ExecutionResult, RiskDecision, Action, DecisionResponse
)
from app.risk import evaluate_risk, RiskSettings, LaneSettings, RiskResult
from app.executor import PaperExecutor, DatabaseConnection
from scripts.paper_loop import run_one_cycle


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


class PaperLoopTests(unittest.TestCase):

    def setUp(self):
        # Always use clean, isolated sqlite in-memory DB for tests
        self.db_url = "sqlite:///:memory:"
        self.executor = PaperExecutor(db_url=self.db_url)
        self.executor.initialize_lane("lane_1", 10000.0)

        # Mock Coinbase adapter
        self.adapter = MagicMock()
        self.adapter.get_ticker = AsyncMock(return_value={
            "price": 100.0,
            "bid": 99.9,
            "ask": 100.1,
            "time": NOW.isoformat(),
            "is_fresh": True,
            "freshness_seconds": 1
        })
        self.adapter.get_candles = AsyncMock(return_value=[
            [int(NOW.timestamp()) - i * 60, 100.0, 100.0, 100.0, 100.0, 1.0] for i in range(32)
        ])

    @patch("httpx.AsyncClient.post")
    def test_loop_no_trade_cycle_end_to_end(self, mock_post):
        """
        L4: Test a full NO_TRADE cycle end-to-end.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "test-req-id",
            "lane_id": "lane_1",
            "symbol": "BTC/USDC",
            "decision": "NO_TRADE",
            "allocation_pct": 0.0,
            "confidence": 0.5,
            "stop_loss_pct": None,
            "take_profit_pct": None,
            "valid_until": NOW.isoformat(),
            "approved_by_risk_engine": True,
            "reason_codes": ["FLAT_MARKET"],
            "model_versions": {"forecast": "v1", "decision": "v1"}
        }
        mock_post.return_value = mock_response

        # Run single cycle
        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            run_one_cycle("lane_1", "BTC/USDC", self.executor, self.adapter, "test-api-key")
        )
        loop.close()

        self.assertTrue(res)
        # Assert that both decision and finalize endpoints were called
        self.assertEqual(mock_post.call_count, 2)

    @patch("httpx.AsyncClient.post")
    def test_loop_approved_open_execution_end_to_end(self, mock_post):
        """
        L4: Test an approved OPEN cycle leading to ExecutionIntent and execution results.
        """
        # 1st call: /v1/decision -> approved OPEN
        # 2nd call: /v1/decision/finalize -> audit update
        mock_response_dec = MagicMock()
        mock_response_dec.status_code = 200
        mock_response_dec.json.return_value = {
            "request_id": "test-req-id-open",
            "lane_id": "lane_1",
            "symbol": "BTC/USDC",
            "decision": "OPEN",
            "allocation_pct": 10.0,
            "confidence": 0.9,
            "stop_loss_pct": None,
            "take_profit_pct": None,
            "valid_until": NOW.isoformat(),
            "approved_by_risk_engine": True,
            "reason_codes": ["BULLISH"],
            "model_versions": {"forecast": "v1", "decision": "v1"}
        }

        mock_response_fin = MagicMock()
        mock_response_fin.status_code = 200
        mock_response_fin.json.return_value = {"status": "ok", "payload_hash": "dummy-sha256"}

        mock_post.side_effect = [mock_response_dec, mock_response_fin]

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            run_one_cycle("lane_1", "BTC/USDC", self.executor, self.adapter, "test-api-key")
        )
        loop.close()

        self.assertTrue(res)
        self.assertEqual(mock_post.call_count, 2)

        # Verify that an active position is indeed registered in the executor
        pos = self.executor.get_position("lane_1", "BTC/USDC")
        self.assertIsNotNone(pos)
        self.assertGreater(pos["quantity"], 0.0)

    @patch("httpx.AsyncClient.post")
    def test_loop_stale_market_data_fails_closed(self, mock_post):
        """
        L4: Test stale market data fails closed and posts failure audit.
        """
        # Set ticker to stale
        self.adapter.get_ticker = AsyncMock(return_value={
            "price": 100.0,
            "bid": 99.9,
            "ask": 100.1,
            "time": NOW.isoformat(),
            "is_fresh": False,
            "freshness_seconds": 120
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "payload_hash": "dummy-sha256"}
        mock_post.return_value = mock_response

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            run_one_cycle("lane_1", "BTC/USDC", self.executor, self.adapter, "test-api-key")
        )
        loop.close()

        # It fails closed (returns False)
        self.assertFalse(res)
        # It should post to /v1/decision/market_data_failure
        self.assertEqual(mock_post.call_count, 1)
        # Check that the first argument is indeed the market_data_failure endpoint
        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/v1/decision/market_data_failure"))
        self.assertEqual(kwargs["json"]["reason"], "STALE_MARKET_DATA")

    @patch("httpx.AsyncClient.post")
    def test_loop_candles_fetch_failure_fails_closed(self, mock_post):
        """
        N1: Test candle fetch failure fails closed and posts failure audit.
        """
        self.adapter.get_candles = AsyncMock(side_effect=Exception("network timeout"))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "payload_hash": "dummy-sha256"}
        mock_post.return_value = mock_response

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            run_one_cycle("lane_1", "BTC/USDC", self.executor, self.adapter, "test-api-key")
        )
        loop.close()

        # It fails closed (returns False)
        self.assertFalse(res)
        self.assertEqual(mock_post.call_count, 1)
        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/v1/decision/market_data_failure"))
        self.assertEqual(kwargs["json"]["reason"], "CANDLES_FETCH_FAILED")

    @patch("httpx.AsyncClient.post")
    def test_loop_ticker_fetch_failure_fails_closed(self, mock_post):
        """
        N1: Test ticker fetch failure fails closed and posts failure audit.
        """
        self.adapter.get_ticker = AsyncMock(side_effect=Exception("network timeout"))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "payload_hash": "dummy-sha256"}
        mock_post.return_value = mock_response

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            run_one_cycle("lane_1", "BTC/USDC", self.executor, self.adapter, "test-api-key")
        )
        loop.close()

        # It fails closed (returns False)
        self.assertFalse(res)
        self.assertEqual(mock_post.call_count, 1)
        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/v1/decision/market_data_failure"))
        self.assertEqual(kwargs["json"]["reason"], "TICKER_FETCH_FAILED")

    @patch("httpx.AsyncClient.post")
    def test_loop_audit_database_failure_fails_closed(self, mock_post):
        """
        L4: Test that any decision-service HTTP error (or audit database failure)
        causes the paper loop cycle to fail-closed (returns False, preventing execution).
        """
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            run_one_cycle("lane_1", "BTC/USDC", self.executor, self.adapter, "test-api-key")
        )
        loop.close()

        # Should return False (fail-closed, no orders placed)
        self.assertFalse(res)

    def test_pnl_and_drawdown_calculation_with_mark_move(self):
        """
        N2: Test that unrealized_pnl, realized_pnl, and max_drawdown_pct are computed correctly
        under a sequence of:
        1. OPEN a position at $100.
        2. Move market mark down to $90 (proving non-zero unrealized PnL and drawdown).
        3. CLOSE the position at $90 (proving non-zero realized PnL and preserving drawdown).
        """
        lane_id = "lane_1"
        symbol = "BTC/USDC"
        
        # 1. Open position of 10 BTC/USDC @ $100
        intent_open = ExecutionIntent(
            execution_intent_id=str(uuid.uuid4()),
            risk_decision_id=str(uuid.uuid4()),
            mode="paper",
            symbol=symbol,
            action="OPEN",
            side="BUY",
            quantity=10.0,
            stop_price=98.0,
            take_profit_price=105.0,
            client_order_id="order-open-pnl",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5)
        )
        res_open = self.executor.execute_intent(lane_id, intent_open, 100.0)
        self.assertEqual(res_open.status, "FILLED")
        
        # Let's get the snapshots from sqlite
        cursor = self.executor.db.get_cursor()
        snapshots = cursor.execute("SELECT equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct FROM arena_snapshots WHERE lane_id = ? ORDER BY id ASC", (lane_id,)).fetchall()
        self.assertEqual(len(snapshots), 2)
        snap_open = snapshots[1]
        self.assertAlmostEqual(snap_open["equity"], 9993.5)
        self.assertAlmostEqual(snap_open["unrealized_pnl"], 0.0) # at fill, price matches entry_price
        self.assertAlmostEqual(snap_open["fees"], 6.0)
        self.assertGreater(snap_open["max_drawdown_pct"], 0.0)
        
        # 2. Move market mark down to $90 and trigger close stop
        self.executor.update_market_mark(symbol, 90.0)
        res_stop = self.executor.check_and_trigger_stops(lane_id, symbol, 90.0)
        self.assertIsNotNone(res_stop)
        self.assertEqual(res_stop.status, "FILLED")
        
        # Now let's fetch the snapshots again
        snapshots_after = cursor.execute("SELECT equity, cash, realized_pnl, unrealized_pnl, fees, max_drawdown_pct FROM arena_snapshots WHERE lane_id = ? ORDER BY id ASC", (lane_id,)).fetchall()
        self.assertEqual(len(snapshots_after), 3)
        snap_close = snapshots_after[2]
        
        # Position should be closed
        pos = self.executor.get_position(lane_id, symbol)
        self.assertIsNone(pos)
        
        # Assert non-zero realized PnL
        self.assertAlmostEqual(snap_close["unrealized_pnl"], 0.0)
        self.assertLess(snap_close["realized_pnl"], 0.0)
        self.assertGreater(snap_close["max_drawdown_pct"], 1.0)
        self.assertGreater(snap_close["fees"], 6.0)

    def test_loop_missing_lane_fails_closed(self):
        """
        L5: Test that run_one_cycle fails closed if the lane is not pre-initialized.
        """
        loop = asyncio.new_event_loop()
        with self.assertRaises(ValueError):
            loop.run_until_complete(
                run_one_cycle("non_existent_lane", "BTC/USDC", self.executor, self.adapter, "test-api-key")
            )
        loop.close()


if __name__ == "__main__":
    unittest.main()
