#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONFIRMATION="ERASE_FOUNDATION_VM_WITHOUT_BACKUP"
REPOSITORY_URL=""
REPOSITORY_REF="main"
APP_DIR="/opt/fondazionesemplice"
CONFIRM=""

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install_vm.sh --repo URL [--ref REF] --confirm ERASE_FOUNDATION_VM_WITHOUT_BACKUP

Destroys Docker workloads and /opt/fondazionesemplice, installs the GPU container stack,
clones the repository, and starts Fondazione in PAPER mode. It does not repartition the boot disk.
EOF
}

while (($#)); do
  case "$1" in
    --repo) REPOSITORY_URL="${2:-}"; shift 2 ;;
    --ref) REPOSITORY_REF="${2:-}"; shift 2 ;;
    --app-dir) APP_DIR="${2:-}"; shift 2 ;;
    --confirm) CONFIRM="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -n "$REPOSITORY_URL" ]] || { echo "--repo is required." >&2; exit 2; }
[[ "$CONFIRM" == "$CONFIRMATION" ]] || {
  echo "Destructive confirmation missing. Expected: $CONFIRMATION" >&2
  exit 2
}
[[ -r /etc/os-release ]] || { echo "Unsupported operating system." >&2; exit 1; }
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || { echo "Ubuntu is required." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive

echo "[1/7] Removing existing application and Docker workloads (no backup)."
if command -v docker >/dev/null 2>&1; then
  docker ps -aq | xargs -r docker rm -f
  docker system prune --all --force --volumes
fi
rm -rf -- "$APP_DIR"

echo "[2/7] Installing base packages and Docker."
apt-get update
apt-get install -y ca-certificates curl git gnupg openssl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${VERSION_CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[3/7] Installing NVIDIA Container Toolkit."
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl enable --now docker
systemctl restart docker

echo "[4/7] Cloning trusted source."
git clone --filter=blob:none --no-checkout "$REPOSITORY_URL" "$APP_DIR"
git -C "$APP_DIR" fetch --depth 1 origin "$REPOSITORY_REF"
git -C "$APP_DIR" checkout --detach FETCH_HEAD
cd "$APP_DIR"

echo "[5/7] Creating paper-only secrets."
db_password="$(openssl rand -hex 24)"
api_key="$(openssl rand -hex 32)"
grafana_password="$(openssl rand -hex 24)"
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

echo "[6/7] Running preflight and starting services."
./scripts/preflight.sh --gpu
docker compose --profile gpu --profile observability up -d --build

echo "[7/7] Waiting for health checks."
./scripts/smoke_test.sh --wait 600

cat <<EOF
Installation complete in PAPER mode.
Repository: $REPOSITORY_URL@$REPOSITORY_REF
Application: $APP_DIR
OctoBot: http://127.0.0.1:5001
Grafana: http://127.0.0.1:3000
Secrets: $APP_DIR/.env (mode 600)
EOF