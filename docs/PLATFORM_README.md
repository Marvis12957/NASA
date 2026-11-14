# Multi-Agent Hackathon Coordination System — Staging Package

Built and tested locally on 2026-11-14 while the target server
(`nasa-linux`) is unreachable. Everything repo-side is implemented and
verified; everything host-side is staged as design/docs and clearly marked
pending.

## Status

| Area | State | Evidence |
|---|---|---|
| KANBAN.md board + schema | ✅ Done | 2 example tasks; `agentctl tasks` validates |
| AGENTS.md rules | ✅ Done | — |
| ARCHITECTURE.md | ✅ Done | — |
| `tools/agentctl` CLI (claim/status/tasks/board/agents/conflicts, task add/ready/done, heartbeat/progress/block/submit/release/recover, history, audit) | ✅ Done, tested | **11/11 tests pass** — `tests/TEST_REPORT.md` |
| Atomic flock claiming + lease epochs + ownership checks | ✅ Done, tested | concurrency + stale-epoch + non-owner tests green |
| Git worktree isolation | ✅ Done, tested | distinct worktrees/branches in temp repo |
| JSONL event logs (schema per spec §8) | ✅ Done, tested | schema test green |
| Git hooks (commit-msg / pre-commit / pre-push) + CI | ✅ Done, verified | hooks exercised live in a temp repo |
| SSH audit design | 📝 Design only | `docs/SSH_AUDIT.md` — final config needs Discovery |
| SSH audit installer | 📝 Staged | `scripts/install-ssh-audit.sh` refuses to run without `DISCOVERY.md` |
| Setup / rollback docs | ✅ Done | `docs/SETUP.md`, `docs/ROLLBACK.md` |

## Pending server access (Discovery phase, spec §2)

- Run Discovery on `nasa-linux` first: OS/init, users/groups/sudo, SSH
  mechanism (Tailscale SSH vs OpenSSH vs PAM), Tailscale policies, repo
  state, shared users, existing audit services, alternative SSH entry
  points. Write findings to `DISCOVERY.md`.
- Finalise and deploy the SSH audit config from `docs/SSH_AUDIT.md`;
  test fail-closed behaviour **before** enforcing it, with a second admin
  session open.
- Re-run the concurrency test suite on the server
  (`tests/run_tests.sh`).

## Quickstart (deploy)

See `docs/SETUP.md`. In short: copy this package into the server repo,
install the hooks, run `tests/run_tests.sh`, smoke-test a claim/release
cycle, then run the Discovery-gated SSH audit installer.

## Layout

```
KANBAN.md                 single source of truth (2 example tasks, BACKLOG)
AGENTS.md                 mandatory rules for coding agents
docs/ARCHITECTURE.md      components, locking/lease, event correlation
docs/SSH_AUDIT.md         host audit design (pending Discovery)
docs/SETUP.md             deployment steps
docs/ROLLBACK.md          revert steps
tools/agentctl            the CLI (Python stdlib only)
hooks/                    commit-msg, pre-commit, pre-push
.github/workflows/ci.yml  board validation + test suite
scripts/install-ssh-audit.sh   staged installer (refuses without DISCOVERY.md)
tests/test_agentctl.py    11 tests exercising the real CLI
tests/run_tests.sh        runs the suite, writes TEST_REPORT.md
tests/TEST_REPORT.md      latest run: 11 passed, 0 failed
```
