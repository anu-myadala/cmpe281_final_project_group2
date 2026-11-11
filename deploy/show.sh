#!/usr/bin/env bash
# Back-end views of C1 for the demo. Run it in the repository folder on the machine that runs the
# stack (on EC2: cd ~/scicp). It only reads; nothing here changes the data.
#
#   bash deploy/show.sh services   the containers and their health
#   bash deploy/show.sh tests      the C1 unit tests, in a one-off container (SQLite, mock MongoDB)
#   bash deploy/show.sh audit      the last 15 rows of the audit log (BR-12): who did what
#   bash deploy/show.sh models     the model versions of the registry and what the worker loaded
#   bash deploy/show.sh alerts     the newest alerts with the model version that decided them
#   bash deploy/show.sh logs       follow the API and the classifier worker
set -euo pipefail
cd "$(dirname "$0")/.."

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi
sql() { $DOCKER compose exec -T mysql mysql -uscicp -pscicp scicp --table -e "$1" 2>/dev/null; }
worker() { if $DOCKER compose ps --services 2>/dev/null | grep -qx c4; then echo c4; else echo stubs; fi; }

case "${1:-}" in
  services)
    $DOCKER compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
    ;;
  tests)
    # The tests bring their own database (SQLite) and fakes for C2, C3 and the storage, so the
    # running platform is not touched. The repository is mounted read-only.
    $DOCKER run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$(pwd)":/repo:ro -w /repo/c1/api scicp/c1-api:local \
      python -m pytest "${@:2}" -p no:cacheprovider
    ;;
  audit)
    sql "SELECT a.audit_id AS id, DATE_FORMAT(a.ts, '%H:%i:%s') AS utc, COALESCE(u.email, 'system') AS actor,
                a.action, a.entity, a.entity_id
         FROM audit_log a LEFT JOIN app_user u ON u.user_id = a.actor_user_id
         ORDER BY a.audit_id DESC LIMIT ${2:-15};"
    ;;
  models)
    sql "SELECT version, status, JSON_EXTRACT(metrics_json, '\$.macroF1') AS macro_f1, size_bytes,
                LEFT(sha256, 12) AS sha256, DATE_FORMAT(activated_at, '%m-%d %H:%i') AS activated_utc
         FROM model_version ORDER BY version_id;"
    $DOCKER compose logs --no-color "$(worker)" 2>&1 | grep -E "READY on|loading sound|FAILED" | tail -3
    ;;
  alerts)
    sql "SELECT a.alert_id AS id, DATE_FORMAT(a.created_at, '%H:%i:%s') AS utc, t.code AS tenant, a.vehicle_id AS car,
                a.alert_type_code AS type, a.severity, a.confidence AS conf, a.status, a.model_version
         FROM alert a JOIN tenant t ON t.tenant_id = a.tenant_id ORDER BY a.alert_id DESC LIMIT ${2:-8};"
    ;;
  logs)
    $DOCKER compose logs -f --tail 5 api "$(worker)"
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
