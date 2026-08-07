#!/usr/bin/env bash
set -Eeuo pipefail

wait_seconds=120
if [[ "${1:-}" == "--wait" ]]; then
  wait_seconds="${2:-120}"
fi

deadline=$((SECONDS + wait_seconds))
urls=(
  "http://127.0.0.1:8080/healthz"
  "http://127.0.0.1:8082/healthz"
  "http://127.0.0.1:8083/healthz"
  "http://127.0.0.1:30000/health"
)
for url in "${urls[@]}"; do
  until curl -fsS "$url" >/dev/null; do
    ((SECONDS < deadline)) || { echo "Timed out waiting for $url" >&2; docker compose ps; exit 1; }
    sleep 5
  done
done

unhealthy="$(docker compose ps --format json | grep -c 'unhealthy' || true)"
[[ "$unhealthy" == "0" ]] || { docker compose ps; exit 1; }
set -a
source .env
set +a
python3 scripts/e2e_test.py
echo "Smoke tests passed."
