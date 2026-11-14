# ROLLBACK.md — Reverting the Coordination System

## Revert the repo-side system

```sh
# 1. Remove the git hooks
rm -f .git/hooks/commit-msg .git/hooks/pre-commit .git/hooks/pre-push

# 2. Stop using agentctl; board becomes a plain Markdown file again.
#    KANBAN.md remains valid Markdown with the task JSON blocks intact.

# 3. Remove coordination state (optional; keep logs for audit)
rm -f .agentctl.lock
# Keep logs/ — do not delete audit history. Archive instead:
mv logs logs.archive-$(date +%Y%m%d)

# 4. Remove worktrees that are no longer needed (only after confirming
#    their branches are merged or their work is preserved)
git worktree list
git worktree remove workspaces/TASK-xxx   # per worktree, after review
git branch -d task/TASK-xxx               # per branch, after review
```

## Revert SSH audit changes

- Restore the backed-up SSH/auditd configuration from the backup the
  installer created (see `scripts/install-ssh-audit.sh` — it backs up
  `/etc/ssh/sshd_config`, auditd rules, and journald config before
  changing anything, and prints the backup paths).
- Restart the audit/SSH services only after verifying config syntax
  (`sshd -t`, `auditctl -R` check) and with a second admin session open.
- Never roll back by deleting logs; archive them instead.

## Rollback rules

- Never force-push to restore a branch; use normal merge/revert.
- Never delete a worktree that still holds unmerged work — check
  `git status`/`git log` in it first.
- The board file itself is the state; restoring an older KANBAN.md from
  git history is safe only when no agent is running (stop agents first).
