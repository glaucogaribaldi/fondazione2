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
        L4: Test stale market data fails closed.
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
        mock_response.json.return_value = {
            "request_id": "test-req-id-stale",
            "lane_id": "lane_1",
            "symbol": "BTC/USDC",
            "decision": "NO_TRADE",
            "allocation_pct": 0,
            "confidence": 0,
            "stop_loss_pct": None,
            "take_profit_pct": None,
            "valid_until": NOW.isoformat(),
            "approved_by_risk_engine": False,
            "reason_codes": ["STALE_MARKET_DATA"],
            "model_versions": {"forecast": "v1", "decision": "v1"}
        }
        mock_post.return_value = mock_response

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            run_one_cycle("lane_1", "BTC/USDC", self.executor, self.adapter, "test-api-key")
        )
        loop.close()

        # It completes successfully but decision is NO_TRADE (fails-closed)
        self.assertTrue(res)
        self.assertEqual(mock_post.call_count, 2)

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
