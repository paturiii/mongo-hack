#!/usr/bin/env bash
# Local dev for Track 2: gateway (:8000) + web (:3000) [+ dev stub planner].
#   scripts/dev.sh up [--stub]   start in background (logs in .run/)
#   scripts/dev.sh down          stop everything
# Uses MONGODB_DB from env or .env. The stub planner refuses to run on the shared 'wander' DB.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .run

down() {
  for f in .run/*.pid; do
    [ -e "$f" ] || continue
    kill "$(cat "$f")" 2>/dev/null || true
    rm -f "$f"
  done
}

case "${1:-}" in
  up)
    down
    nohup .venv/bin/uvicorn track2_app.gateway.app:app --port 8000 > .run/gateway.log 2>&1 &
    echo $! > .run/gateway.pid
    if [[ "${2:-}" == "--stub" ]]; then
      nohup .venv/bin/python scripts/dev_stub_planner.py > .run/stub.log 2>&1 &
      echo $! > .run/stub.pid
    fi
    if [[ -d web/app && "${WEB:-1}" == "1" ]]; then
      (cd web; setsid nohup npm run dev > ../.run/web.log 2>&1 < /dev/null & echo $! > ../.run/web.pid)
    fi
    sleep 4
    curl -fsS localhost:8000/api/health && echo
    ;;
  down) down ;;
  *) echo "usage: scripts/dev.sh up [--stub] | down"; exit 1 ;;
esac
