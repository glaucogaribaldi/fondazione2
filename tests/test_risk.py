import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.models import (  # noqa: E402
    Candle,
    DecisionRequest,
    MarketSnapshot,
    PortfolioSnapshot,
    Proposal,
)
from app.risk import (
    LIVE_CONFIRMATION_PHRASE,
    LaneSettings,
    RiskSettings,
    evaluate_risk,
)  # noqa: E402


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def request(*, mode="paper", age_seconds=5, spread_bps=10) -> DecisionRequest:
    candles = [
        Candle(
            timestamp=NOW - timedelta(minutes=5 * (32 - index)),
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101 + index,
            volume=10,
        )
        for index in range(32)
    ]
    bid = 100.0
    ask = bid * (1 + spread_bps / 10_000)
    return DecisionRequest(
        request_id="71c24bcb-7a88-43a5-9cdb-eb128404b661",
        mode=mode,
        lane_id="lane_1",
        symbol="BTC/USDC",
        timeframe="5m",
        market=MarketSnapshot(
            timestamp=NOW - timedelta(seconds=age_seconds), bid=bid, ask=ask, candles=candles
        ),
        portfolio=PortfolioSnapshot(
            equity=310,
            cash=310,
            daily_pnl_pct=0,
            open_positions=0,
            current_position_pct=0,
        ),
    )


GLOBAL = RiskSettings(allowed_symbols=frozenset({"BTC/USDC"}))
LANE = LaneSettings(
    minimum_confidence=0.7,
    max_position_pct=10,
    max_daily_loss_pct=2,
    max_open_positions=2,
    cooldown_minutes=30,
)
BUY = Proposal(
    action="BUY",
    allocation_pct=8,
    confidence=0.8,
    stop_loss_pct=1,
    take_profit_pct=2,
)


class RiskTests(unittest.TestCase):
    def test_valid_paper_buy_is_approved(self):
        result = evaluate_risk(request(), BUY, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.action, "BUY")

    def test_stale_market_fails_closed(self):
        result = evaluate_risk(request(age_seconds=180), BUY, GLOBAL, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertEqual(result.action, "HOLD")
        self.assertIn("STALE_MARKET_DATA", result.reasons)

    def test_allocation_limit_fails_closed(self):
        oversized = BUY.model_copy(update={"allocation_pct": 11})
        result = evaluate_risk(request(), oversized, GLOBAL, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertIn("ALLOCATION_LIMIT", result.reasons)

    def test_live_requires_both_controls(self):
        locked = evaluate_risk(request(mode="live"), BUY, GLOBAL, LANE, now=NOW)
        self.assertFalse(locked.approved)
        self.assertIn("LIVE_TRADING_LOCKED", locked.reasons)

        unlocked = evaluate_risk(
            request(mode="live"),
            BUY,
            GLOBAL,
            LANE,
            now=NOW,
            live_enabled=True,
            live_confirmation=LIVE_CONFIRMATION_PHRASE,
        )
        self.assertTrue(unlocked.approved)

    def test_missing_stop_loss_fails_closed(self):
        no_stop = BUY.model_copy(update={"stop_loss_pct": None})
        result = evaluate_risk(request(), no_stop, GLOBAL, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertIn("STOP_LOSS_REQUIRED", result.reasons)


if __name__ == "__main__":
    unittest.main()