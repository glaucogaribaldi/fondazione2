#!/usr/bin/env bash
set -Eeuo pipefail

require_gpu=false
[[ "${1:-}" == "--gpu" ]] && require_gpu=true

command -v docker >/dev/null || { echo "Docker is missing." >&2; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose v2 is missing." >&2; exit 1; }
docker info >/dev/null || { echo "Docker daemon is unavailable." >&2; exit 1; }
[[ -f .env ]] || { echo ".env is missing; copy .env.example first." >&2; exit 1; }

if grep -Eq '^(TRADING_MODE=live|LIVE_ENABLED=true)$' .env; then
  echo "Preflight refuses live mode. Follow docs/PAPER_TO_LIVE.md manually." >&2
  exit 1
fi
if grep -Eq '=change-me($|-)' .env; then
  echo "Replace all change-me secrets in .env." >&2
  exit 1
fi

if $require_gpu; then
  command -v nvidia-smi >/dev/null || { echo "NVIDIA driver is missing." >&2; exit 1; }
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi >/dev/null
fi

docker compose config --quiet
echo "Preflight passed."
