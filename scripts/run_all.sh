#!/usr/bin/env bash
# Run the whole app locally (all 3 tracks) against MONGODB_DB (default from .env = shared "wander").
#   scripts/run_all.sh up     gateway :8000, web :3000, Track 3 workers + planner, Track 1 disruption runner
#   scripts/run_all.sh down
# Logs: .run/*.log
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .run
source ~/.nvm/nvm.sh >/dev/null 2>&1 || true

start() {  # name, command...
  local name=$1; shift
  setsid nohup "$@" > ".run/$name.log" 2>&1 < /dev/null &
  echo $! > ".run/$name.pid"
}

down() {
  for f in .run/*.pid; do
    [ -e "$f" ] || continue
    kill -9 -- "-$(cat "$f")" 2>/dev/null || kill -9 "$(cat "$f")" 2>/dev/null || true
    rm -f "$f"
  done
}

case "${1:-}" in
  up)
    down
    start gateway  .venv/bin/uvicorn track2_app.gateway.app:app --port 8000 --timeout-graceful-shutdown 3
    start workers  .venv/bin/python -m track3_research.workers.run --concurrency 8
    start planner  .venv/bin/python -m track3_research.planner.run
    start track1   .venv/bin/python -m track1_disruption.run
    (cd web && start_web() { setsid nohup npm run dev > ../.run/web.log 2>&1 < /dev/null & echo $! > ../.run/web.pid; }; start_web)
    sleep 8
    curl -fsS localhost:8000/api/health && echo
    echo "app: http://localhost:3000   logs: .run/"
    ;;
  down) down ;;
  *) echo "usage: scripts/run_all.sh up|down"; exit 1 ;;
esac
