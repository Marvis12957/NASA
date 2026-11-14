# ARCHITECTURE.md — Multi-Agent Hackathon Coordination System

## Components

| Component | File | Role |
|---|---|---|
| Kanban board | `KANBAN.md` | Single source of truth for task state |
| Agent rules | `AGENTS.md` | Mandatory behaviour for coding agents |
| Architecture | `docs/ARCHITECTURE.md` (this file) | Shared boundaries |
| CLI | `tools/agentctl` | Task claiming, progress, recovery, audit |
| Lock | `.agentctl.lock` | Stable flock file, never replaced |
| Events | `logs/*.jsonl` | Append-only structured event logs |
| Git hooks | `hooks/` | Commit/push guards |
| CI | `.github/workflows/ci.yml` | Board validation + test suite |
| SSH audit | `docs/SSH_AUDIT.md` + `scripts/install-ssh-audit.sh` | Host-level audit (post-Discovery) |

## Data flow

```
human ── agentctl task add ──▶ KANBAN.md (BACKLOG)
human ── agentctl task ready ─▶ KANBAN.md (READY)
agent ── agentctl claim --next ─▶ flock → reload board → checks
        (READY? deps DONE? scopes free? owner free?)
        → atomic board write (CLAIMED, epoch+1)
        → git worktree add workspaces/TASK-xxx (branch task/TASK-xxx)
        → task-events.jsonl
agent ── heartbeat/progress ─▶ ownership+epoch check → board + events
agent ── agentctl submit ────▶ REVIEW (+ commit_ref / pr_ref)
human ── review / merge ─────▶ DONE (via agentctl task flow or PR merge)
```

## Locking and lease model

- One dedicated lock file (`.agentctl.lock`) is used for **every** board
  mutation. It is opened with `O_CREAT` and locked with `flock(LOCK_EX)`;
  it is never renamed or replaced, so the lock identity is stable.
- Every mutation reloads the board **after** acquiring the lock — never
  decide ownership from a cached copy.
- Board writes are atomic: write a temp file on the same filesystem,
  `fsync`, then `os.replace`.
- Lease: heartbeat interval 15 s, lease timeout 90 s
  (`AGENTCTL_LEASE_TIMEOUT` overrides the timeout). Each claim and each
  release/recovery increments `lease_epoch`; every mutating command must
  present the current epoch. A stale epoch is rejected and logged.
- Recovery does **not** happen automatically on SSH disconnect. A task is
  recoverable only after its lease expires (or with `--force-revoke` by a
  human). Recovery preserves the worktree, captures `git status`/`diff
  --stat` into the recovery event, clears the owner, increments the epoch,
  and moves the task to RECOVERY, from which it can be claimed again.

## Event correlation chain

```
SSH identity ──▶ SSH session ──▶ agent run ──▶ task ──▶ git changes ──▶ PR ──▶ merge
```

- `user_id` = agent id (`--agent` or `AGENT_ID` env).
- `ssh_session_id` = `SSH_SESSION_ID` env when the host collector sets it,
  otherwise derived from `SSH_CONNECTION` (`ssh:<client>:<server-port>`),
  otherwise `null` — never fabricated.
- `agent_run_id` = `AGENT_RUN_ID` env, or generated once per claim and
  stored on the task.
- `trace_id` = one UUID per `agentctl` invocation, shared by all events it
  emits. `command_id` names the command.
- Event categories live in separate files: `agent-events.jsonl`,
  `task-events.jsonl`, `git-events.jsonl`, `recovery-events.jsonl`.
  `ssh-events.jsonl` is reserved for the host-level collector; agents
  cannot write it. All timestamps are UTC ISO-8601. Events are
  append-only; normal agents must not delete or rewrite them.

## Git worktree isolation

- Every claimed task gets branch `task/TASK-xxx` and worktree
  `workspaces/TASK-xxx`. Two active tasks can never share a worktree path
  (`agentctl conflicts` detects collisions; claim refuses a path owned by
  another task).
- Direct pushes to `main`/`master` are blocked by `hooks/pre-push`.
  Force pushes are blocked. Commits must reference a task id
  (`hooks/commit-msg`). Commits on `main` are blocked (`hooks/pre-commit`).
- Integration happens via PRs; CI runs board validation and the test suite.

## Boundaries and non-goals

- No PostgreSQL, Redis, Kubernetes, or any database. State = one Markdown
  file + JSONL logs. This is deliberate: hackathon scale, human-inspectable.
- `agentctl` does not schedule work, does not run agents, and does not
  replace code review. Humans create, prioritise, and approve tasks.
- A file lock does **not** eliminate git merge conflicts. Conflicts are
  detected (`agentctl conflicts`), recorded as events, preserved (branches
  and diffs kept), and resolved in a separate integration worktree through
  a normal claim.

## Known limits

- Board consistency relies on every writer using `agentctl` under flock.
  A human editing `KANBAN.md` by hand can corrupt state; hooks and
  file-permission hardening (see `docs/SETUP.md`) mitigate, not eliminate,
  this.
- SSH command auditing is bounded by what the host mechanism observes
  (see `docs/SSH_AUDIT.md` for the honest coverage matrix). Shell history
  alone is NOT an audit solution.
- Lease expiry is time-based; a crashed agent's in-flight uncommitted work
  is preserved in its worktree but may be incomplete — recovery captures
  `git status`/`diff --stat`, not a full checkpoint.
- Concurrent claim correctness was tested locally (see
  `tests/TEST_REPORT.md`); on-server concurrency still needs a live test
  after deployment.
