# AGENTS.md — Mandatory Behavioural Instructions for Coding Agents

Read this file, `docs/ARCHITECTURE.md`, and the current `KANBAN.md` before
doing any work. These rules are enforced by `tools/agentctl` and the git
hooks, not merely by prompting.

## Startup checklist (every agent run)

1. Read this file and `docs/ARCHITECTURE.md`.
2. Read `KANBAN.md` (via `agentctl board` or `agentctl tasks`).
3. Claim exactly one task through `agentctl claim --next` (or a specific
   task id given by a human).
4. Work only on the assigned task, only in its worktree.
5. Send a heartbeat at least every 15 seconds while working
   (`agentctl heartbeat TASK-xxx --agent <id> --epoch <n>`).
6. Publish progress regularly (`agentctl progress ... --summary "..."`).
7. Submit through `agentctl submit` when done; then claim the next task
   only after the previous one is completed or formally released.

## Hard rules

- **Never write `KANBAN.md` directly.** All board changes go through
  `agentctl`. Direct edits are rejected by hooks and lost on the next
  atomic board write.
- **Never invent new product tasks.** Humans create and prioritise tasks.
  You may *propose* a new task (`agentctl task add` stays in BACKLOG and
  needs a human to `task ready` it); you may never approve your own
  proposal or move it to READY yourself.
- **One task at a time.** If no READY task is claimable, remain IDLE and
  report that. Do not start unassigned work.
- **Work only in your worktree** (`workspaces/TASK-xxx`, branch
  `task/TASK-xxx`). Never touch another agent's worktree. Never commit to
  `main` and never push to `main`. Every commit message must reference its
  task id (`TASK-xxx`).
- **Respect your lease.** If your lease expires (no heartbeat for 90 s),
  your writes are rejected: heartbeat/progress/submit/release with a stale
  epoch fail. Do not work around this — run `agentctl recover` flow or
  re-claim the task.
- **Do not force-push** another agent's branch, ever. Do not claim that a
  file lock eliminates git merge conflicts — it does not; conflicts go
  through the recovery workflow (`agentctl conflicts`, then a human-approved
  resolution task).
- **Never delete or rewrite event logs** (`logs/*.jsonl` are append-only).
  Never write to `ssh-events.jsonl` — that file belongs to the host-level
  SSH audit collector.
- **Never log secrets** (passwords, keys, tokens) in progress summaries,
  commit messages, or events.

## Failure behaviour

- If `agentctl claim` reports "no task claimable", stop and wait (or ask
  a human). Do not retry in a tight loop; wait at least 60 seconds.
- If a command is rejected for ownership/epoch, stop touching that task.
- If you crash or disconnect, your task will be recovered by another
  agent after the lease expires; your worktree is preserved, never
  deleted.
- On any conflict, stop the affected publication/merge, run
  `agentctl conflicts`, and report to a human.

## GraphRAG extract identity and communities

LLM-extracted nodes use `{type}:{slug(name)}@{psi}` so identical labels stay
distinct (`fuel:pmma@PSI-25` vs `fuel:pmma@PSI-99`; `sensor:cir@PSI-69` vs
`hardware:cir@PSI-69`). Catalog Investigation/Scenario/Role ids stay global
(`inv:PSI-69`). Optional `canonical` on scoped fuels points at the catalog
fuel id. `--with-communities` writes hierarchical C0/C1/(C2) Community
nodes after extract. Checkpoint: `backend/var/graphrag_extract.jsonl`.
