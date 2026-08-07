#!/usr/bin/env python3
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


required_files = [
    "docker-compose.yml",
    ".env.example",
    "config/risk.yml",
    "config/strategies.yml",
    "config/release.yml",
    "docs/INSTALL_OPENCLAW.md",
    "docs/PUBLIC_DASHBOARD.md",
    "monitoring/Caddyfile",
    "output/pdf/fondazione-semplice-metodo.pdf",
]
for relative in required_files:
    if not (ROOT / relative).is_file():
        fail(f"missing {relative}")

env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
for expected in ["TRADING_MODE=paper", "LIVE_ENABLED=false", "LIVE_CONFIRMATION="]:
    if expected not in env_text:
        fail(f"unsafe default: expected {expected}")

strategies = (ROOT / "config/strategies.yml").read_text(encoding="utf-8")
lanes = set(re.findall(r"^  (lane_[1-5]):$", strategies, flags=re.MULTILINE))
if lanes != {f"lane_{number}" for number in range(1, 6)}:
    fail("strategies.yml must define exactly lane_1 through lane_5")

compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
for service in [
    "decision-service",
    "kronos",
    "nemotron",
    "arena",
    "market-feed",
    "gateway",
    "octobot",
    "postgres",
]:
    if not re.search(rf"^  {re.escape(service)}:$", compose, flags=re.MULTILINE):
        fail(f"compose service missing: {service}")

print("Static configuration validation passed.")
