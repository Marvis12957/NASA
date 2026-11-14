# SETUP.md — Deployment (after Discovery)

Prerequisites: the Discovery phase (spec §2) has been run on the server
and `DISCOVERY.md` exists in the repo root; the machine is reachable via
Tailscale/SSH; a git repo exists at the deployment path (e.g. `/workspace`).

## 1. Copy the package

```sh
# from this staging dir, on the server repo root:
cp -r <staging>/KANBAN.md <staging>/AGENTS.md <staging>/tools \
      <staging>/hooks <staging>/docs <staging>/scripts <staging>/tests .
chmod +x tools/agentctl hooks/* tests/run_tests.sh
```

## 2. Install git hooks

```sh
cp hooks/commit-msg hooks/pre-commit hooks/pre-push .git/hooks/
chmod +x .git/hooks/commit-msg .git/hooks/pre-commit .git/hooks/pre-push
```

## 3. Event logs (append-only, restricted)

```sh
mkdir -p logs
chmod 750 logs
# Board writes only via agentctl: keep KANBAN.md owned by the repo owner;
# do not make it group-writable for agent accounts if they are distinct.
chmod 640 KANBAN.md
```

## 4. SSH audit (after Discovery only)

```sh
scripts/install-ssh-audit.sh --dry-run   # review the plan first
scripts/install-ssh-audit.sh             # applies auditd/journald config
# Then run the host-level SSH tests with an isolated test account.
# Do NOT enforce fail-closed until the fail-closed test passes and a
# second admin session is open.
```

## 5. Smoke test

```sh
./tests/run_tests.sh
./tools/agentctl --board KANBAN.md tasks
./tools/agentctl --board KANBAN.md claim --next --agent smoke-test
./tools/agentctl --board KANBAN.md release <claimed-task> \
    --agent smoke-test --epoch <epoch>
./tools/agentctl --board KANBAN.md conflicts
```

Expected: tests all green, claim succeeds on a READY task, release returns
it to READY, `conflicts` prints "no conflicts".

## 6. CI (optional, if a GitHub remote exists)

Push `.github/workflows/ci.yml`; it validates the board schema and runs
the test suite on every push/PR.
