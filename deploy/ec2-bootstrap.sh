#!/usr/bin/env bash
# SCICP EC2 bootstrap (Ubuntu 24.04 LTS, t3.medium or larger, t3.large with the C4 worker; ports 22 and 80 open).
#
# Installs Docker Engine with the compose plugin, clones the repository, writes .env with the
# public hostname and fresh secrets, and starts the stack with the AWS override. Run it as the
# default user (ubuntu) over SSH:
#
#   curl -fsSL https://raw.githubusercontent.com/<org>/<repo>/main/deploy/ec2-bootstrap.sh | bash -s -- https://github.com/<org>/<repo>.git
#   # or, after a manual clone:
#   bash deploy/ec2-bootstrap.sh
#
# Optional environment before running: REPO_URL, REPO_DIR (default ~/scicp), BRANCH (main),
# PUBLIC_HOST (defaults to the instance's public DNS from the metadata service),
# AWS_STORAGE_BACKEND (local, the default: artifacts on the instance disk; or s3 with the bucket
# scicp-models in us-west-2 and the CORS rule of README.md), AWS_S3_ACCESS_KEY / AWS_S3_SECRET_KEY
# (only for s3 without an instance role), AWS_MAIL_MODE (smtp to MailHog, the default; log; ses),
# SCICP_STUBS_ONLY=1 (run C1 with the C2/C3 stand-ins even when the real C2 and C3 are in the repo).
#
# When the repository has the real C2 and C3 (docker-compose.c2.yml, docker-compose.c3.yml and
# docker-compose.c2c3.yml), they start too, and so does the C4 classifier worker when
# docker-compose.c4.yml is there (it needs about 3 GB more memory: use t3.large). The file list is written to .env as COMPOSE_FILE, so
# afterwards a plain "docker compose ps / logs / exec" in the folder uses the same files.
set -euo pipefail

REPO_URL="${1:-${REPO_URL:-}}"
REPO_DIR="${REPO_DIR:-$HOME/scicp}"
BRANCH="${BRANCH:-main}"

log() { printf '\n[scicp] %s\n' "$*"; }

# ---------------------------------------------------------------- 1. Docker Engine + compose plugin
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine"
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg git
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
else
  log "Docker already installed: $(docker --version)"
fi
# Use sudo for docker until the group change applies to a new login shell.
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi

# ---------------------------------------------------------------- 1b. Swap (the image builds of C1, C2 and C3 together need ~3 GB)
MEM_KB="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
if [ "$MEM_KB" -lt 7500000 ] && ! swapon --show | grep -q /swapfile; then
  log "Adding a 4 GB swap file (instance has $((MEM_KB / 1024)) MB of memory)"
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ---------------------------------------------------------------- 2. Repository
if [ -f "$(pwd)/docker-compose.yml" ] && [ -d "$(pwd)/c1" ]; then
  REPO_DIR="$(pwd)"
  log "Using the checkout in $REPO_DIR"
elif [ -d "$REPO_DIR/.git" ]; then
  log "Updating $REPO_DIR"
  git -C "$REPO_DIR" fetch --all --prune
  git -C "$REPO_DIR" checkout "$BRANCH"
  git -C "$REPO_DIR" pull --ff-only
else
  if [ -z "$REPO_URL" ]; then
    echo "REPO_URL is required for the first run (argument or environment variable)." >&2
    exit 1
  fi
  log "Cloning $REPO_URL into $REPO_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

# ---------------------------------------------------------------- 3. Public hostname (IMDSv2)
if [ -z "${PUBLIC_HOST:-}" ]; then
  TOKEN="$(curl -sS -m 2 -X PUT http://169.254.169.254/latest/api/token \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
  if [ -n "$TOKEN" ]; then
    PUBLIC_HOST="$(curl -sS -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/public-hostname || true)"
    if [ -z "$PUBLIC_HOST" ]; then
      PUBLIC_HOST="$(curl -sS -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" \
        http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
    fi
  fi
fi
if [ -z "${PUBLIC_HOST:-}" ]; then
  read -r -p "Public hostname or IP of this instance: " PUBLIC_HOST
fi
log "Public host: $PUBLIC_HOST"

# ---------------------------------------------------------------- 4. .env
if [ ! -f .env ]; then
  log "Writing .env from .env.example with fresh secrets"
  cp .env.example .env
  JWT="$(openssl rand -hex 32)"
  SVC="$(openssl rand -hex 32)"
  ROOT_PW="$(openssl rand -hex 12)"
  MQTT_PW="$(openssl rand -hex 12)"
  sed -i \
    -e "s|^JWT_SECRET=.*|JWT_SECRET=${JWT}|" \
    -e "s|^SERVICE_TOKEN_SECRET=.*|SERVICE_TOKEN_SECRET=${SVC}|" \
    -e "s|^MYSQL_ROOT_PASSWORD=.*|MYSQL_ROOT_PASSWORD=${ROOT_PW}|" \
    -e "s|^MQTT_PASSWORD=.*|MQTT_PASSWORD=${MQTT_PW}|" \
    -e "s|^WEB_PORT=.*|WEB_PORT=80|" \
    -e "s|^AWS_S3_ACCESS_KEY=.*|AWS_S3_ACCESS_KEY=${AWS_S3_ACCESS_KEY:-}|" \
    -e "s|^AWS_S3_SECRET_KEY=.*|AWS_S3_SECRET_KEY=${AWS_S3_SECRET_KEY:-}|" \
    -e "s|^AWS_MAIL_MODE=.*|AWS_MAIL_MODE=${AWS_MAIL_MODE:-smtp}|" \
    -e "s|^AWS_STORAGE_BACKEND=.*|AWS_STORAGE_BACKEND=${AWS_STORAGE_BACKEND:-local}|" \
    .env
fi
# set_env KEY VALUE: replace the line or append it.
set_env() {
  if grep -q "^$1=" .env; then sed -i -e "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
# The public host may change after a stop/start of the instance: always refresh it (and the
# alert links in C3's emails, which point at it).
set_env PUBLIC_HOST "${PUBLIC_HOST}"
set_env C3_PUBLIC_BASE_URL "http://${PUBLIC_HOST}"

# The compose files: the base and the AWS override, plus the real C3 and C2 when they are merged.
COMPOSE_FILES="docker-compose.yml:docker-compose.aws.yml"
LOG_SERVICES="api stubs"
if [ "${SCICP_STUBS_ONLY:-0}" != "1" ] && [ -f docker-compose.c3.yml ] && [ -f docker-compose.c2.yml ] \
    && [ -f docker-compose.c2c3.yml ]; then
  COMPOSE_FILES="${COMPOSE_FILES}:docker-compose.c3.yml:docker-compose.c2.yml:docker-compose.c2c3.yml"
  LOG_SERVICES="api c2 c3 stubs"
  if [ -f docker-compose.c4.yml ]; then
    COMPOSE_FILES="${COMPOSE_FILES}:docker-compose.c4.yml"
    LOG_SERVICES="api c2 c3 c4"
    log "Starting C1 with the real C2, C3 and the C4 classifier worker"
  else
    log "Starting C1 with the real C2 and C3"
  fi
else
  log "Starting C1 with the C2/C3 stand-ins"
fi
set_env COMPOSE_FILE "${COMPOSE_FILES}"
if [ -f docker-compose.c2.yml ] && [ -z "$(find c2/edge/datasets -mindepth 2 -name '*.wav' 2>/dev/null | head -1)" ]; then
  log "c2/edge/datasets has no clips: the simulated cars will send synthetic sounds (copy the course clips there first)"
fi
chmod 600 .env

# ---------------------------------------------------------------- 5. Up
log "Building and starting the stack (this takes a few minutes the first time)"
$DOCKER compose up -d --build

log "Waiting for the API"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1/api/v1/health >/dev/null 2>&1; then break; fi
  sleep 5
done
$DOCKER compose ps

cat <<EOF

SCICP is up.
  Web app     http://${PUBLIC_HOST}/
  API health  http://${PUBLIC_HOST}/api/v1/health
  MailHog     http://${PUBLIC_HOST}:8025/   (open port 8025 to your IP in the security group)
  API docs    ssh -L 8000:127.0.0.1:8000 ubuntu@${PUBLIC_HOST}  then  http://localhost:8000/api/v1/docs
  Compose     ${COMPOSE_FILES}  (COMPOSE_FILE in .env: run "sudo docker compose ..." in $(pwd))
  Logs        sudo docker compose logs -f ${LOG_SERVICES}
  Worker      sudo docker compose logs $( [ -f docker-compose.c4.yml ] && [ "${SCICP_STUBS_ONLY:-0}" != "1" ] && echo c4 || echo stubs ) | grep -i -E "ready|loading|failed"
Accounts (password Passw0rd!demo): anh@example.com, maria@bayshuttle.example, staff@scicp.example, admin@scicp.example
EOF
