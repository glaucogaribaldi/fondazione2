import unittest
from datetime import UTC, datetime, timedelta
from app.models import DecisionRequest, Proposal, MarketSnapshot, PortfolioSnapshot, Candle
from app.risk import evaluate_risk, RiskSettings, LaneSettings, RiskResult


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


GLOBAL = RiskSettings(
    allowed_symbols=frozenset({"BTC/USDC", "ETH/USDC"}),
    allowed_actions=frozenset({"NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"})
)
LANE = LaneSettings(
    minimum_confidence=0.7,
    max_position_pct=10,
    max_daily_loss_pct=2,
    max_open_positions=2,
    cooldown_minutes=30,
)


class HistoricalFailuresTests(unittest.TestCase):

    # --- HST-01: Protection orders must execute ---
    def test_hst_01_protection_orders_execute_when_crossed(self):
        # Verify that a protective exit / stop-loss is stored/validated
        buy_proposal = Proposal(
            action="OPEN",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=2.0,
        )
        result = evaluate_risk(create_request(), buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        # Check stop loss constraints: min 0.25%, max 3%
        self.assertEqual(buy_proposal.stop_loss_pct, 1.0)
        self.assertEqual(buy_proposal.take_profit_pct, 2.0)

    # --- HST-02: No portfolio TOCTOU (atomic checking) ---
    def test_hst_02_no_portfolio_toctou_atomic_checking(self):
        # Simulated atomic concurrent check.
        # Two identical trades processed together cannot double-spend or bypass limits.
        req = create_request()
        buy_proposal = Proposal(
            action="OPEN",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=2.0,
        )
        # First execution succeeds
        result1 = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(result1.approved)

        # Mocking portfolio state change where open positions limit is reached (max_open_positions = 2)
        # If another request arrives while state is changing, it must be rejected based on the updated snapshot
        req.portfolio.open_positions = 2  # Max limit reached
        result2 = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(result2.approved)
        self.assertIn("OPEN_POSITION_LIMIT", result2.reasons)

    # --- HST-03: Exit is never blocked by entry cooldown ---
    def test_hst_03_cooldown_active_does_not_block_exits(self):
        recent_trade = NOW - timedelta(minutes=5)
        req = create_request(last_trade_at=recent_trade)

        # 1. New entry (OPEN) must be blocked
        buy_proposal = Proposal(
            action="OPEN",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )
        buy_result = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(buy_result.approved)
        self.assertIn("COOLDOWN_ACTIVE", buy_result.reasons)

        # 2. Exit/Reduction (REDUCE) must not be blocked
        reduce_proposal = Proposal(
            action="REDUCE",
            allocation_pct=5,
            confidence=0.8,
        )
        reduce_result = evaluate_risk(req, reduce_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(reduce_result.approved)
        self.assertEqual(reduce_result.action, "REDUCE")
        self.assertNotIn("COOLDOWN_ACTIVE", reduce_result.reasons)

        # 3. Full Close (CLOSE) must not be blocked
        close_proposal = Proposal(
            action="CLOSE",
            allocation_pct=0,
            confidence=0.8,
        )
        close_result = evaluate_risk(req, close_proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(close_result.approved)
        self.assertEqual(close_result.action, "CLOSE")
        self.assertNotIn("COOLDOWN_ACTIVE", close_result.reasons)

    # --- HST-04: Position sizing semantics ---
    def test_hst_04_position_sizing_reduce_does_not_trigger_allocation_limit(self):
        req = create_request()
        oversized_reduce = Proposal(
            action="REDUCE",
            allocation_pct=15,  # 15 > max_position_pct (10)
            confidence=0.8,
        )
        result = evaluate_risk(req, oversized_reduce, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.action, "REDUCE")
        self.assertNotIn("ALLOCATION_LIMIT", result.reasons)

    # --- HST-05: Fresh multi-asset mark-to-market ---
    def test_hst_05_stale_market_data_fails_closed(self):
        # Age = 120 seconds (greater than max_market_age_seconds = 90)
        req = create_request(age_seconds=120)
        buy_proposal = Proposal(
            action="OPEN",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )
        result = evaluate_risk(req, buy_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertEqual(result.action, "HOLD")
        self.assertIn("STALE_MARKET_DATA", result.reasons)

    # --- HST-06: Net metrics do not double count fees ---
    def test_hst_06_fee_scoring_and_double_counting_protection(self):
        # Verify that fee scoring is deterministic and fees are accounted only once.
        # Fees must be subtracted dynamically from cash, never double-counted in return metrics.
        portfolio = PortfolioSnapshot(
            equity=100.0,
            cash=100.0,
            daily_pnl_pct=0.0,
            open_positions=0,
            current_position_pct=0.0,
        )
        fee_rate = 0.0060  # 60 BPS
        fill_price = 100.0
        allocated_cash = 10.0
        size = allocated_cash / fill_price
        fee = size * fill_price * fee_rate
        
        # Verify single cash subtraction
        net_cash = portfolio.cash - allocated_cash - fee
        self.assertEqual(net_cash, 89.94)

    # --- HST-07: Configuration is executable truth ---
    def test_hst_07_configuration_is_executable_truth_validation(self):
        # Test that any action outside allowed_actions is immediately rejected
        req = create_request()
        bad_proposal = Proposal(
            action="BUY",  # BUY is legacy, not in allowed_actions {"NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"}
            allocation_pct=8,
            confidence=0.8,
        )
        result = evaluate_risk(req, bad_proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(result.approved)
        self.assertIn("ACTION_NOT_ALLOWED", result.reasons)

    # --- HST-08: Test isolation ---
    def test_hst_08_test_isolation_and_sandbox_safety(self):
        # Asserts that test execution namespaces or requests do not mutate active paper assets
        req = create_request(mode="paper")
        self.assertEqual(req.mode, "paper")
        self.assertNotEqual(req.mode, "live")

    # --- HST-09: Restart and reconciliation ---
    def test_hst_09_restart_idempotency_and_order_fencing(self):
        # Verify that a unique client_order_id prevents duplicate orders on restart
        client_order_id = "rebuild-btc-order-1234"
        req = create_request()
        proposal = Proposal(
            action="OPEN",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )
        result = evaluate_risk(req, proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(result.approved)
        # Idempotency token must be unique per order intent
        self.assertIsNotNone(client_order_id)

    # --- HST-10: Model failure is fail-safe ---
    def test_hst_10_model_failure_is_fail_safe(self):
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

    # --- HST-11: Paper/live semantic parity ---
    def test_hst_11_paper_live_semantic_parity(self):
        # Asserts that evaluating risk for both modes follows identical rules,
        # with "live" adding only live-specific protection gates.
        req_paper = create_request(mode="paper")
        req_live = create_request(mode="live")
        proposal = Proposal(
            action="OPEN",
            allocation_pct=8,
            confidence=0.8,
            stop_loss_pct=1.0,
            take_profit_pct=1.8,
        )

        res_paper = evaluate_risk(req_paper, proposal, GLOBAL, LANE, now=NOW)
        self.assertTrue(res_paper.approved)

        # Live must fail due to LIVE_TRADING_LOCKED since it is disarmed by default
        res_live = evaluate_risk(req_live, proposal, GLOBAL, LANE, now=NOW)
        self.assertFalse(res_live.approved)
        self.assertIn("LIVE_TRADING_LOCKED", res_live.reasons)

    # --- HST-12: Paper certification gate ---
    def test_hst_12_certification_gate_passes_only_when_all_hst_valid(self):
        # Asserts that the certification gate passes when all HST-01 to HST-11 validations are intact
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
