import os
from pathlib import Path

import yaml

from .risk import LaneSettings, RiskSettings


CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))


def _load_yaml(name: str) -> dict:
    with (CONFIG_DIR / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_risk_settings() -> RiskSettings:
    values = _load_yaml("risk.yml")["global"]
    return RiskSettings(
        allowed_symbols=frozenset(values["allowed_symbols"]),
        allowed_actions=frozenset(values.get("allowed_actions", ["NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"])),
        max_allocation_pct=float(values["max_allocation_pct"]),
        max_spread_bps=float(values["max_spread_bps"]),
        max_market_age_seconds=int(values["max_market_age_seconds"]),
        max_decision_ttl_seconds=int(values["max_decision_ttl_seconds"]),
        require_stop_loss_for_buy=bool(values["require_stop_loss_for_buy"]),
        min_stop_loss_pct=float(values["min_stop_loss_pct"]),
        max_stop_loss_pct=float(values["max_stop_loss_pct"]),
        max_take_profit_pct=float(values["max_take_profit_pct"]),
    )


def load_lane_settings(lane_id: str) -> tuple[dict, LaneSettings]:
    lane = _load_yaml("strategies.yml")["lanes"].get(lane_id)
    if lane is None:
        raise KeyError(lane_id)
    return lane, LaneSettings(
        minimum_confidence=float(lane["minimum_confidence"]),
        max_position_pct=float(lane["max_position_pct"]),
        max_daily_loss_pct=float(lane["max_daily_loss_pct"]),
        max_open_positions=int(lane["max_open_positions"]),
        cooldown_minutes=int(lane["cooldown_minutes"]),
    )


def load_bootstrap_probe() -> dict:
    probe = _load_yaml("release.yml").get("bootstrap_probe", {})
    return {
        "enabled": bool(probe.get("enabled", False)),
        "symbol": str(probe.get("symbol", "BTC/USDT")),
        "request_id_prefix": str(
            probe.get("request_id_prefix", "coinbase-BTC-USDT-")
        ),
        "allocation_pct": float(probe.get("allocation_pct", 1.0)),
        "stop_loss_pct": float(probe.get("stop_loss_pct", 1.0)),
        "take_profit_pct": float(probe.get("take_profit_pct", 1.8)),
    }
