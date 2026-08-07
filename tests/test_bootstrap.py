import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "decision-service"))

from app.bootstrap import bootstrap_probe_proposal, should_run_bootstrap_probe  # noqa: E402
from app.models import Candle, DecisionRequest, MarketSnapshot, PortfolioSnapshot  # noqa: E402


NOW = datetime(2026, 8, 7, 8, 30, tzinfo=UTC)
SETTINGS = {
    "enabled": True,
    "symbol": "BTC/USDT",
    "request_id_prefix": "coinbase-BTC-USDT-",
    "allocation_pct": 1.0,
    "stop_loss_pct": 1.0,
    "take_profit_pct": 1.8,
}


def request(
    *,
    symbol="BTC/USDT",
    mode="paper",
    last_trade_at=None,
    request_id="coinbase-BTC-USDT-2026-08-07T08:30:00+00:00",
) -> DecisionRequest:
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
        request_id=request_id,
        mode=mode,
        lane_id="lane_1",
        symbol=symbol,
        timeframe="5m",
        market=MarketSnapshot(timestamp=NOW, bid=100, ask=100.1, candles=candles),
        portfolio=PortfolioSnapshot(
            equity=310,
            cash=310,
            daily_pnl_pct=0,
            open_positions=0,
            current_position_pct=0,
            last_trade_at=last_trade_at,
        ),
    )


class BootstrapProbeTests(unittest.TestCase):
    def test_first_paper_btc_decision_runs_probe(self):
        self.assertTrue(should_run_bootstrap_probe(request(), SETTINGS))

    def test_probe_never_runs_for_other_symbol_or_live(self):
        self.assertFalse(should_run_bootstrap_probe(request(symbol="ETH/USDT"), SETTINGS))
        self.assertFalse(should_run_bootstrap_probe(request(mode="live"), SETTINGS))

    def test_probe_never_runs_for_synthetic_smoke_request(self):
        self.assertFalse(
            should_run_bootstrap_probe(request(request_id="e2e-1786091400"), SETTINGS)
        )

    def test_probe_never_repeats_after_trade(self):
        self.assertFalse(should_run_bootstrap_probe(request(last_trade_at=NOW), SETTINGS))

    def test_probe_is_explicit_small_buy(self):
        proposal = bootstrap_probe_proposal(SETTINGS)
        self.assertEqual(proposal.action, "BUY")
        self.assertEqual(proposal.allocation_pct, 1.0)
        self.assertEqual(proposal.reason_codes, ["BOOTSTRAP_PROBE"])


if __name__ == "__main__":
    unittest.main()
