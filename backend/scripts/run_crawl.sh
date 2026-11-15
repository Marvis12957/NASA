#!/usr/bin/env bash
# Run the NASA source crawler detached from the SSH session.
# Usage: backend/scripts/run_crawl.sh [extra crawl_sources.py args]
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${CRAWL_PYTHON:-$HOME/venvs/nasa-crawl/bin/python}"
LOG=backend/var/crawl_sources.log
PID=backend/var/crawl_sources.pid
if [[ -f $PID ]] && kill -0 "$(cat $PID)" 2>/dev/null; then
  echo "crawler already running (pid $(cat $PID)); tail -f $LOG"; exit 0
fi
nohup setsid "$PY" -u backend/scripts/crawl_sources.py "$@" >"$LOG" 2>&1 < /dev/null &
echo $! > "$PID"
echo "started pid $(cat $PID); log: $LOG"
