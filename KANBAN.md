# KANBAN — Flame in Freefall Hackathon

Single source of truth for task states and assignments. **All board changes
must go through `agentctl`** (see `AGENTS.md`). Direct edits are rejected by
the git hooks and will be overwritten by the next atomic board write.

Columns: BACKLOG → READY → CLAIMED → IN_PROGRESS → BLOCKED → REVIEW → DONE,
plus RECOVERY for tasks whose lease expired or was revoked.

Each task is a `### TASK-xxx` heading followed by one fenced JSON block.
`agentctl` validates this schema on every load.

## BACKLOG

### TASK-001

```json
{
  "acceptance_criteria": [
    "Dashboard renders PSI-69 FLEX droplet data from a local CSV snapshot",
    "FlameAtlas evidence view loads without network access"
  ],
  "branch": null,
  "commit_ref": null,
  "dependencies": [],
  "description": "EXAMPLE TASK \u2014 build the minimal FlameAtlas evidence view from PSI-69 data.",
  "id": "TASK-001",
  "last_heartbeat": null,
  "lease_epoch": 0,
  "owner": null,
  "pr_ref": null,
  "priority": "P1",
  "progress_summary": "",
  "resource_scopes": [
    "app/flame-atlas"
  ],
  "status": "BACKLOG",
  "title": "[EXAMPLE] FlameAtlas evidence view (PSI-69)",
  "updated_at": "2026-11-14T00:00:00Z",
  "worktree": null
}
```

### TASK-002

```json
{
  "acceptance_criteria": [
    "Scenario Center compares at least two PSI test runs side by side",
    "No placeholder numbers: every value traces to a PSI record"
  ],
  "branch": null,
  "commit_ref": null,
  "dependencies": [
    "TASK-001"
  ],
  "description": "EXAMPLE TASK \u2014 depends on TASK-001; compare two PSI droplet tests in Scenario Center.",
  "id": "TASK-002",
  "last_heartbeat": null,
  "lease_epoch": 0,
  "owner": null,
  "pr_ref": null,
  "priority": "P2",
  "progress_summary": "",
  "resource_scopes": [
    "app/scenario-center"
  ],
  "status": "BACKLOG",
  "title": "[EXAMPLE] Scenario Center comparison view",
  "updated_at": "2026-11-14T00:00:00Z",
  "worktree": null
}
```

### TASK-003

```json
{
  "acceptance_criteria": [
    "test suite green on server",
    "claim/heartbeat/submit/done lifecycle verified in a worktree",
    "ssh session collector writes ssh-events.jsonl"
  ],
  "branch": null,
  "commit_ref": null,
  "dependencies": [],
  "description": "Bootstrap task for the coordination platform itself: KANBAN board, agentctl, git hooks, CI, event logs, SSH audit collector v1.",
  "id": "TASK-003",
  "last_heartbeat": null,
  "lease_epoch": 0,
  "owner": null,
  "pr_ref": null,
  "priority": "P0",
  "progress_summary": "",
  "resource_scopes": [
    "agent-platform"
  ],
  "status": "BACKLOG",
  "title": "Setup multi-agent hackathon coordination platform",
  "updated_at": "2026-11-14T05:40:16.449718Z",
  "worktree": null
}
```

## READY

## CLAIMED

## IN_PROGRESS

## BLOCKED

## REVIEW

## DONE

## RECOVERY
