#!/usr/bin/env bash
set -Eeuo pipefail

readonly EXPECTED_IP="35.239.91.187"
readonly CONFIRMATION="ERASE_OLD_FOUNDATION_AND_INSTALL_FONDAZIONE2_WITHOUT_BACKUP"
APP_DIR="/opt/fondazione2"
CONFIRM=""
REPOSITORY_URL="https://github.com/glaucogaribaldi/fondazione2.git"
REPOSITORY_REF="3b406536c2a03dc19615b95ff10168c8b829b10a"

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install_fondazione2.sh --confirm ERASE_OLD_FOUNDATION_AND_INSTALL_FONDAZIONE2_WITHOUT_BACKUP [--ref REBUILD_COMMIT_HASH]

Executes a complete clean wipe of the legacy fondazionesemplice stack on the verified GCP VPS,
sets up the Fondazione2 production directory (/opt/fondazione2), configures secure keys,
starts all services, and validates the paper baseline.
EOF
}

while (($#)); do
  case "$1" in
    --confirm) CONFIRM="${2:-}"; shift 2 ;;
    --ref) REPOSITORY_REF="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

# Check root privilege
if [[ $EUID -ne 0 ]]; then
  echo "Error: This script must be run as root (sudo)." >&2
  exit 1
fi

# Verify operator confirmation
if [[ "$CONFIRM" != "$CONFIRMATION" ]]; then
  echo "Error: Destructive confirmation missing. Expected: --confirm $CONFIRMATION" >&2
  exit 2
fi

# 1. Target Identity Verification (Safety Check)
echo "=== [1/9] Verifying Target Host Identity ==="
public_ip=$(curl -s --max-time 10 ifconfig.me || echo "")
if [[ "$public_ip" != "$EXPECTED_IP" ]]; then
  echo "Error: Public IP mismatch! Expected $EXPECTED_IP, but got '$public_ip'. Execution halted." >&2
  exit 3
fi
echo "Target identity verified. IP is $EXPECTED_IP. Hostname is $(hostname)."

# 2. Stop and Clean Legacy Containers (Disk Space Reclamation)
echo "=== [2/9] Cleaning Legacy fondazionesemplice Stack ==="
if command -v docker >/dev/null 2>&1; then
  echo "Stopping and deleting any running containers..."
  docker ps -aq | xargs -r docker rm -f || true
  echo "Pruning all unused docker data, volumes, and images to reclaim disk space..."
  docker system prune -a --volumes -f
fi

echo "Removing legacy directories..."
rm -rf /opt/fondazionesemplice || true
rm -rf "$APP_DIR" || true

# 3. Create Fresh Fondazione2 Directory
echo "=== [3/9] Creating Fresh Production Environment ==="
mkdir -p "$APP_DIR"

# 4. Clone Pinned Repository Commit
echo "=== [4/9] Fetching Fondazione2 Codebase ==="
git clone --filter=blob:none --no-checkout "$REPOSITORY_URL" "$APP_DIR"
git -C "$APP_DIR" fetch --depth 1 origin "$REPOSITORY_REF"
git -C "$APP_DIR" checkout --detach FETCH_HEAD
cd "$APP_DIR"

# 5. Generate Secure Secrets
echo "=== [5/9] Creating Secure Paper-Only Secrets ==="
db_password=$(openssl rand -hex 24)
api_key=$(openssl rand -hex 32)
grafana_password=$(openssl rand -hex 24)

cp .env.example .env
sed -i \
  -e "s|DECISION_API_KEY=.*|DECISION_API_KEY=${api_key}|" \
  -e "s|POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${db_password}|" \
  -e "s|DATABASE_URL=.*|DATABASE_URL=postgresql://fondazione:${db_password}@postgres:5432/fondazione|" \
  -e "s|GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=${grafana_password}|" \
  -e "s|AI_BACKEND=.*|AI_BACKEND=sglang|" \
  -e "s|KRONOS_BACKEND=.*|KRONOS_BACKEND=real|" \
  -e "s|TRADING_MODE=.*|TRADING_MODE=paper|" \
  -e "s|LIVE_ENABLED=.*|LIVE_ENABLED=false|" \
  .env

chmod 600 .env
echo "Secure secrets generated in $APP_DIR/.env (permissions 600)."

# 6. Verify NVIDIA drivers & toolkit
echo "=== [6/9] Verifying NVIDIA GPU Environment ==="
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "Warning: nvidia-smi not found! Attempting to reload drivers or continuing in simulation mode..." >&2
else
  nvidia-smi
fi

# 7. Incremental Service Spin-Up
echo "=== [7/9] Launching Fondazione2 Containers ==="
# Spin up databases & cache first
docker compose up -d postgres redis-cache redis-jobs

echo "Waiting for PostgreSQL to become healthy..."
until docker compose exec -T postgres pg_isready -U fondazione -d fondazione >/dev/null 2>&1; do
  sleep 2
done
echo "PostgreSQL is healthy."

# Spin up model servers
echo "Launching SGLang (Nemotron-Nano-9B-v2) and Kronos forecasting service..."
docker compose --profile gpu up -d kronos nemotron

# Launch decision aggregator and QuantDinger
echo "Launching Decision Service and QuantDinger processes..."
docker compose up -d decision-service quantdinger-api quantdinger-worker quantdinger-scheduler quantdinger-celery quantdinger-celery-beat

# Launch gateway and metrics
echo "Launching Observability Stack..."
docker compose --profile observability up -d prometheus grafana gateway

# 8. Post-Deploy Health Checks
echo "=== [8/9] Verifying Service Health ==="
sleep 15
docker compose ps

echo "Checking decision-service healthz..."
curl -fsS http://localhost:8080/healthz || { echo "Error: Decision-service failed health check!" >&2; exit 4; }

# 9. Verify Safety State
echo "=== [9/9] Verification Checklist ==="
mode_check=$(docker compose exec -T decision-service python -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('http://localhost:8080/healthz').read().decode())['trading_mode'])")
live_check=$(docker compose exec -T decision-service python -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('http://localhost:8080/healthz').read().decode())['live_enabled'])")

echo "CONFIRMED: TRADING_MODE=$mode_check"
echo "CONFIRMED: LIVE_ENABLED=$live_check"

if [[ "$mode_check" != "paper" || "$live_check" != "False" ]]; then
  echo "CRITICAL SAFETY FAIL: Live trading is enabled or mode is not paper! Shutting down immediately..." >&2
  docker compose down
  exit 5
fi

echo "=========================================================="
echo "Fondazione2 Rebuild Complete & Verified!"
echo "Baseline running in PAPER-FIRST mode. Live trading is DISARMED."
echo "=========================================================="
