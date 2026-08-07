#!/usr/bin/env python3
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def scalar(value: str):
    value = value.strip().strip('"').strip("'")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def number_between(values: dict, key: str, minimum: float, maximum: float) -> None:
    value = values.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        fail(f"{key} must be numeric")
    if not minimum <= float(value) <= maximum:
        fail(f"{key} must be between {minimum} and {maximum}")


strategy_text = (ROOT / "config/strategies.yml").read_text(encoding="utf-8")
risk_text = (ROOT / "config/risk.yml").read_text(encoding="utf-8")
release_text = (ROOT / "config/release.yml").read_text(encoding="utf-8")

lanes: dict[str, dict] = {}
active_lane = None
for line in strategy_text.splitlines():
    if match := re.fullmatch(r"  (lane_[1-5]):", line):
        active_lane = match.group(1)
        lanes[active_lane] = {}
    elif active_lane and (match := re.fullmatch(r"    ([a-z_]+):\s*(.+)", line)):
        lanes[active_lane][match.group(1)] = scalar(match.group(2))

expected_lanes = {f"lane_{number}" for number in range(1, 6)}
if set(lanes) != expected_lanes:
    fail("strategy release must contain exactly lane_1 through lane_5")

release_match = re.search(r"^release_id:\s*(.+)$", release_text, flags=re.MULTILINE)
version_match = re.search(r"^version:\s*(\d+)$", release_text, flags=re.MULTILINE)
if not release_match or not scalar(release_match.group(1)):
    fail("release_id is required")
if not version_match or int(version_match.group(1)) != 1:
    fail("unsupported strategy release version")
release_id = scalar(release_match.group(1))

allocation_match = re.search(
    r"^  max_allocation_pct:\s*([0-9.]+)$", risk_text, flags=re.MULTILINE
)
if not allocation_match:
    fail("risk max_allocation_pct is required")
max_allocation = float(allocation_match.group(1))

probe_values = {
    key: scalar(value)
    for key, value in re.findall(
        r"^  ([a-z_]+):\s*(.+)$",
        release_text.split("bootstrap_probe:", 1)[1] if "bootstrap_probe:" in release_text else "",
        flags=re.MULTILINE,
    )
}
if probe_values:
    if not isinstance(probe_values.get("enabled"), bool):
        fail("bootstrap_probe.enabled must be boolean")
    if not str(probe_values.get("request_id_prefix", "")).startswith("coinbase-"):
        fail("bootstrap_probe.request_id_prefix must identify the Coinbase market feed")
    symbol = probe_values.get("symbol")
    allowed_symbols = set(
        re.search(r"allowed_symbols:\s*\[([^]]+)]", risk_text).group(1).split(",")
    )
    if symbol not in {item.strip() for item in allowed_symbols}:
        fail("bootstrap_probe.symbol must be allowed by risk.yml")
    number_between(probe_values, "allocation_pct", 0.1, max_allocation)
    number_between(probe_values, "stop_loss_pct", 0.25, 3.0)
    number_between(probe_values, "take_profit_pct", 0.1, 8.0)

names = []
for lane_id, lane in lanes.items():
    name = lane.get("name")
    if not isinstance(name, str) or not name.strip():
        fail(f"{lane_id}.name is required")
    names.append(name)
    if not isinstance(lane.get("ai_enabled"), bool):
        fail(f"{lane_id}.ai_enabled must be boolean")
    number_between(lane, "minimum_confidence", 0, 1)
    number_between(lane, "max_position_pct", 0, max_allocation)
    number_between(lane, "max_daily_loss_pct", 0, 10)
    number_between(lane, "max_open_positions", 0, 20)
    number_between(lane, "cooldown_minutes", 0, 1_440)

if len(names) != len(set(names)):
    fail("strategy names must be unique")

if probe_values and float(probe_values["allocation_pct"]) > min(
    float(lane["max_position_pct"]) for lane in lanes.values()
):
    fail("bootstrap probe allocation exceeds a lane position limit")

print(f"Strategy release {release_id} is valid.")
