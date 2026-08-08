import os
import sys
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from fastapi.testclient import TestClient

# Ensure search paths are set
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

# Set env before importing app
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["DECISION_API_KEY"] = "test-secret-key"
os.environ["TRADING_MODE"] = "paper"
os.environ["CONFIG_DIR"] = str(ROOT / "config")
os.environ["KRONOS_URL"] = "http://localhost:8081"
os.environ["NEMOTRON_URL"] = "http://localhost:30000"

from app.main import app
from app.models import DecisionRequest, MarketSnapshot, PortfolioSnapshot, Candle


class DecisionIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"X-API-Key": "test-secret-key"}

    def test_healthz_endpoint(self):
        """
        Verify health check is reachable and reports paper safety modes.
        """
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["trading_mode"], "paper")
        self.assertFalse(data["live_enabled"])

    def test_decision_and_finalize_integration_flow(self):
        """
        M6: Integration test of decision ASGI pipeline and finalization.
        """
        now = datetime.now(UTC)
        request_id = str(uuid.uuid4())
        
        req = DecisionRequest(
            request_id=request_id,
            mode="paper",
            lane_id="lane_1",
            symbol="BTC/USDC",
            timeframe="1m",
            market=MarketSnapshot(
                timestamp=now, bid=100.0, ask=100.1, candles=[
                    Candle(timestamp=now, open=100.0, high=101.0, low=99.0, close=100.0, volume=10)
                ] * 32
            ),
            portfolio=PortfolioSnapshot(
                equity=10000.0, cash=10000.0, daily_pnl_pct=0.0, open_positions=0, current_position_pct=0.0
            )
        )

        # 1. Trigger decide (will fallback to fail-closed/NO_TRADE on mock SGLang/Kronos unreachability, saving initial audit row)
        response = self.client.post("/v1/decision", json=req.model_dump(mode="json"), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        dec_data = response.json()
        self.assertEqual(dec_data["decision"], "NO_TRADE")

        # 2. Trigger finalize (should complete successfully in SQLite sandbox test)
        finalize_payload = {
            "request_id": request_id,
            "execution_intent": None,
            "execution_result": None
        }
        res_fin = self.client.post("/v1/decision/finalize", json=finalize_payload, headers=self.headers)
        self.assertEqual(res_fin.status_code, 200)
        self.assertEqual(res_fin.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
