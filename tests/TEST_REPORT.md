# Test Report — agentctl

Generated: 2026-11-14T05:40:32Z (UTC)
Command: `python3 tests/test_agentctl.py -v`

## Summary

- Passed: 11
- Failed: 0
- Exit code: 0

## Per-test results

```
test_board_valid_after_concurrency (__main__.CtlCase.test_board_valid_after_concurrency) ... ok
test_concurrent_independent_claims (__main__.CtlCase.test_concurrent_independent_claims) ... ok
test_concurrent_same_task_claim (__main__.CtlCase.test_concurrent_same_task_claim) ... ok
test_event_schema (__main__.CtlCase.test_event_schema) ... ok
test_heartbeat_and_lease_expiry_recover (__main__.CtlCase.test_heartbeat_and_lease_expiry_recover) ... ok
test_invalid_metadata_rejected (__main__.CtlCase.test_invalid_metadata_rejected) ... ok
test_next_respects_deps_and_priority (__main__.CtlCase.test_next_respects_deps_and_priority) ... ok
test_non_owner_rejected (__main__.CtlCase.test_non_owner_rejected) ... ok
test_overlapping_scopes_blocked (__main__.CtlCase.test_overlapping_scopes_blocked) ... ok
test_stale_epoch_rejected (__main__.CtlCase.test_stale_epoch_rejected) ... ok
test_worktree_isolation (__main__.CtlCase.test_worktree_isolation) ... ok
```

## Full output

```
test_board_valid_after_concurrency (__main__.CtlCase.test_board_valid_after_concurrency) ... ok
test_concurrent_independent_claims (__main__.CtlCase.test_concurrent_independent_claims) ... ok
test_concurrent_same_task_claim (__main__.CtlCase.test_concurrent_same_task_claim) ... ok
test_event_schema (__main__.CtlCase.test_event_schema) ... ok
test_heartbeat_and_lease_expiry_recover (__main__.CtlCase.test_heartbeat_and_lease_expiry_recover) ... ok
test_invalid_metadata_rejected (__main__.CtlCase.test_invalid_metadata_rejected) ... ok
test_next_respects_deps_and_priority (__main__.CtlCase.test_next_respects_deps_and_priority) ... ok
test_non_owner_rejected (__main__.CtlCase.test_non_owner_rejected) ... ok
test_overlapping_scopes_blocked (__main__.CtlCase.test_overlapping_scopes_blocked) ... ok
test_stale_epoch_rejected (__main__.CtlCase.test_stale_epoch_rejected) ... ok
test_worktree_isolation (__main__.CtlCase.test_worktree_isolation) ... ok

----------------------------------------------------------------------
Ran 11 tests in 3.110s

OK
```
