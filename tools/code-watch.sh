#!/bin/bash
# code-watch.sh — watcher: log UNCOMMITTED source changes in /workspace.
# Every 15s it fingerprints the working tree (git status + tracked diff +
# hashes of untracked source files, scoped to source areas only, data/
# and backend/var/ RUNTIME output excluded). When the fingerprint changes,
# one JSON line is appended to /var/log/agent-platform/code-changes.jsonl
# (event=worktree).
# State + pid live OUTSIDE the repo so the watcher never triggers itself.
# v2 (2026-11-14): exclude backend/var — the team's crawl/extract .log/.pid
# files change every few seconds and caused false worktree events.
REPO=/workspace
STATE=/var/log/agent-platform/.code-watch-state
PIDFILE=/var/log/agent-platform/code-watch.pid
PATHSPEC=(backend hooks tools scripts tests docs .github AGENTS.md KANBAN.md README.md pytest.ini ':(exclude)backend/var')

echo $$ > "$PIDFILE"
cd "$REPO" || exit 1
python3 tools/code_log.py watcher_start >/dev/null 2>&1 || true

fingerprint() {
    {
        git status --porcelain -- "${PATHSPEC[@]}" 2>/dev/null
        git diff 2>/dev/null | sha256sum
        git ls-files --others --exclude-standard -- backend hooks tools scripts tests docs 2>/dev/null \
            | grep -v '^backend/var/' | sort | xargs -r sha256sum 2>/dev/null
    } | sha256sum | cut -d' ' -f1
}

last=""
[ -f "$STATE" ] && last=$(cat "$STATE")
current=$(fingerprint)
if [ -z "$last" ]; then
    # First ever run: take a silent baseline, do not log the whole tree.
    echo "$current" > "$STATE"; last="$current"
fi

while true; do
    sleep 15
    current=$(fingerprint)
    if [ "$current" != "$last" ]; then
        status_b64=$(git status --porcelain -- "${PATHSPEC[@]}" 2>/dev/null | base64 -w0)
        python3 tools/code_log.py worktree "$current" "$status_b64" >/dev/null 2>&1 || true
        echo "$current" > "$STATE"
        last="$current"
    fi
done
