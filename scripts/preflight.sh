#!/usr/bin/env bash
set -Eeuo pipefail

require_gpu=false
[[ "${1:-}" == "--gpu" ]] && require_gpu=true

command -v docker >/dev/null || { echo "Docker is missing." >&2; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose v2 is missing." >&2; exit 1; }
docker info >/dev/null || { echo "Docker daemon is unavailable." >&2; exit 1; }
[[ -f .env ]] || { echo ".env is missing; copy .env.example first." >&2; exit 1; }

# GCP GCE Metadata Target Identity Gate (Blocker J1)
echo "=== Verifying Target Cloud Identity ==="
metadata_available=false
if curl -s -f -o /dev/null -H "Metadata-Flavor: Google" --max-time 3 http://metadata.google.internal/computeMetadata/v1/instance/name 2>/dev/null; then
  metadata_available=true
fi

if ! $metadata_available; then
  echo "Error: Google Compute Engine metadata is not available! Preflight must be executed on GCP GCE 'fondazione' VPS." >&2
  exit 1
fi

instance_name=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name)
instance_zone=$(basename "$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone)")
internal_ip=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/ip)
public_ip=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)

echo "Discovered GCP Instance details:"
echo "- Instance Name: $instance_name"
echo "- Zone: $instance_zone"
echo "- Internal IP: $internal_ip"
echo "- Public IP: $public_ip"

if [[ "$instance_name" != "fondazione" ]]; then
  echo "Error: Instance name mismatch! Expected 'fondazione', got '$instance_name'." >&2
  exit 1
fi

if [[ "$instance_zone" != "us-central1-a" ]]; then
  echo "Error: Zone mismatch! Expected 'us-central1-a', got '$instance_zone'." >&2
  exit 1
fi

if [[ "$internal_ip" != "10.128.0.16" ]]; then
  echo "Error: Internal IP mismatch! Expected '10.128.0.16', got '$internal_ip'." >&2
  exit 1
fi

if [[ "$public_ip" != "35.239.91.187" ]]; then
  echo "Error: Public IP mismatch! Expected '35.239.91.187', got '$public_ip'." >&2
  exit 1
fi

echo "Target cloud identity successfully verified!"

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
