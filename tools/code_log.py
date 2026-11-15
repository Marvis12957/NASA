#!/usr/bin/env python3
"""code_log.py — append one JSON line per source-code change event.

Log file: /var/log/agent-platform/code-changes.jsonl
(fallback: <repo>/logs/code-changes.jsonl, logs/ is a symlink to the same dir)

Usage:
  code_log.py commit                 -> collects HEAD info itself via git
  code_log.py push <local_ref> <local_sha> <remote_ref> <remote_sha> <remote>
  code_log.py worktree <fingerprint> <git_status_porcelain_base64>
  code_log.py watcher_start
Every line carries: ts (UTC), event, hostname, os user, git author,
AGENT_ID env, SSH_CONNECTION env, repo root, branch.
Never fails the caller: any error is swallowed and exit code stays 0,
except the pre-push logger call is best-effort by design (push rules
themselves live in the pre-push hook, not here).
"""
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

LOG_PRIMARY = "/var/log/agent-platform/code-changes.jsonl"


def git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


def log_path():
    if os.path.isdir(os.path.dirname(LOG_PRIMARY)):
        return LOG_PRIMARY
    root = git("rev-parse", "--show-toplevel")
    if root:
        p = os.path.join(root, "logs", "code-changes.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p
    return LOG_PRIMARY


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "host": os.uname().nodename,
        "os_user": os.environ.get("USER") or os.environ.get("LOGNAME"),
        "agent_id": os.environ.get("AGENT_ID"),
        "ssh_connection": os.environ.get("SSH_CONNECTION"),
        "repo": git("rev-parse", "--show-toplevel"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
    }
    if event == "commit":
        rec["commit"] = git("rev-parse", "HEAD")
        rec["author"] = git("log", "-1", "--format=%an <%ae>")
        rec["subject"] = git("log", "-1", "--format=%s")
        raw = git("diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD")
        rec["files"] = [ln.replace("\t", " ") for ln in raw.splitlines() if ln]
        stat = git("diff-tree", "--no-commit-id", "--shortstat", "-r", "HEAD")
        rec["diffstat"] = stat
    elif event == "push" and len(sys.argv) >= 7:
        rec.update({"local_ref": sys.argv[2], "local_sha": sys.argv[3],
                    "remote_ref": sys.argv[4], "remote_sha": sys.argv[5],
                    "remote": sys.argv[6]})
    elif event == "worktree" and len(sys.argv) >= 4:
        rec["fingerprint"] = sys.argv[2][:16]
        try:
            status = base64.b64decode(sys.argv[3]).decode("utf-8", "replace")
        except Exception:
            status = ""
        rec["changes"] = [ln for ln in status.splitlines() if ln][:100]
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
