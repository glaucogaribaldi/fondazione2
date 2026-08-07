import unittest
from datetime import UTC, datetime, timedelta
from app.models import DecisionRequest, Proposal, MarketSnapshot, PortfolioSnapshot, Candle
from app.risk import evaluate_risk, RiskSettings, LaneSettings


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def create_request(*, mode="paper", last_trade_at=None, age_seconds=5) -> DecisionRequest:
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
    return DecisionRequest(
        request_id="81c24bcb-7a88-43a5-9cdb-eb128404b661",
        mode=mode,
        lane_id="lane_1",
        symbol="BTC/USDC",
        timeframe="5m",
        market=MarketSnapshot(
            timestamp=NOW - timedelta(seconds=age_seconds), bid=100.0, ask=100.1, candles=candles
        ),
        portfolio=PortfolioSnapshot(
            equity=310,
            cash=310,
            daily_pnl_pct=0,
            open_positions=1,
            current_position_pct=10,
            last_trade_at=last_trade_at,
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


class HistoricalFailuresTests(unittest.TestCase):

    def test_hst_03_cooldown_active_does_not_block_exits(self):
        # A recent trade within 5 minutes triggers cooldown (cooldown_minutes = 30)
        recent_trade = NOW - timedelta(minutes=5)
        req = create_request(last_trade_at=recent_trade)

        # 1. New exposure (BUY) MUST be blocked
        buy_proposal = Proposal(
            action="BUY",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )
        buy_result = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(buy_result.approved)
        self.assertIn("COOLDOWN_ACTIVE", buy_result.reasons)

        # 2. Exit/Reduction (REDUCE) MUST NOT be blocked
        reduce_proposal = Proposal(
            action="REDUCE",
            allocation_pct=5,
            confidence=0.8,
        )
        reduce_result = evaluate_risk(req, reduce_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(reduce_result.approved)
        self.assertEqual(reduce_result.action, "REDUCE")
        self.assertNotIn("COOLDOWN_ACTIVE", reduce_result.reasons)

        # 3. Full Close (CLOSE) MUST NOT be blocked
        close_proposal = Proposal(
            action="CLOSE",
            allocation_pct=0,
            confidence=0.8,
        )
        close_result = evaluate_risk(req, close_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(close_result.approved)
        self.assertEqual(close_result.action, "CLOSE")
        self.assertNotIn("COOLDOWN_ACTIVE", close_result.reasons)

    def test_hst_04_position_sizing_reduce_does_not_trigger_allocation_limit(self):
        # Even if reduction or allocation percent in proposal is larger than max_position_pct (10),
        # REDUCE should not be checked against ALLOCATION_LIMIT or blocked
        req = create_request()
        oversized_reduce = Proposal(
            action="REDUCE",
            allocation_pct=15, # 15 > max_position_pct (10)
            confidence=0.8,
        )
        result = evaluate_risk(req, oversized_reduce, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.action, "REDUCE")
        self.assertNotIn("ALLOCATION_LIMIT", result.reasons)

    def test_hst_05_stale_market_data_fails_closed(self):
        # Age = 120 seconds (greater than max_market_age_seconds = 90)
        req = create_request(age_seconds=120)
        buy_proposal = Proposal(
            action="BUY",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )
        result = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertEqual(result.action, "HOLD")
        self.assertIn("STALE_MARKET_DATA", result.reasons)

    def test_hst_10_model_failure_is_fail_safe(self):
        # If model proposal action is holding due to error or missing data,
        # evaluate_risk should handle it as a HOLD action
        req = create_request()
        failed_proposal = Proposal(
            action="HOLD",
            allocation_pct=0,
            confidence=0.0,
            reason_codes=["MODEL_EXCEPTION"]
        )
        result = evaluate_risk(req, failed_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.action, "HOLD")
        self.assertIn("MODEL_EXCEPTION", result.reasons)


if __name__ == "__main__":
    unittest.main()
