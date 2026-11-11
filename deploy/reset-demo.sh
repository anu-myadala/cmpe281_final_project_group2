#!/usr/bin/env bash
# Puts the platform back to the fresh demo data. Run it in the repository folder on the machine that
# runs the stack (on EC2: cd ~/scicp && bash deploy/reset-demo.sh). It stops the stack, removes the
# data volumes (MySQL, MongoDB, model artifacts, clips, broker) but keeps the downloaded CLAP
# weights, starts the stack again and waits until the classifier worker has loaded the active
# model. About 2 minutes. Everything done in the web app since the last start is lost.
set -euo pipefail
cd "$(dirname "$0")/.."

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi
PROJECT="$($DOCKER compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"

echo "[scicp] Stopping the stack"
$DOCKER compose down
for volume in $($DOCKER volume ls -q --filter "label=com.docker.compose.project=${PROJECT}"); do
  case "$volume" in
    *hf-cache) echo "[scicp] Keeping $volume (CLAP weights)" ;;
    *) $DOCKER volume rm "$volume" >/dev/null && echo "[scicp] Removed $volume" ;;
  esac
done

echo "[scicp] Starting with fresh data"
$DOCKER compose up -d
if $DOCKER compose ps --services | grep -qx c4; then WORKER=c4; else WORKER=stubs; fi
echo "[scicp] Waiting for the classifier worker ($WORKER)"
for _ in $(seq 1 60); do
  if $DOCKER compose logs "$WORKER" 2>&1 | grep -q "READY on"; then break; fi
  sleep 5
done
# nginx keeps the address of the API it started with; a restart makes it look the new one up.
$DOCKER compose restart web >/dev/null
$DOCKER compose logs "$WORKER" 2>&1 | grep "READY on" | tail -1 || echo "[scicp] The worker is not READY yet: bash deploy/show.sh models"
$DOCKER compose ps --format 'table {{.Service}}\t{{.Status}}'
echo "[scicp] Fresh data is in. Wait about 5 minutes before a demo so that the cars have sent their first clips."
