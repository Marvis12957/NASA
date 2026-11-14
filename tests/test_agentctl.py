#!/usr/bin/env python3
"""Tests for tools/agentctl — exercise the REAL CLI via subprocess against
temp boards and temp git repos. Stdlib only (unittest).

Run: bash tests/run_tests.sh
"""
import concurrent.futures
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTCTL = os.path.join(ROOT, "tools", "agentctl")

# Load agentctl as a module to reuse its board renderer for fixtures.
_spec = importlib.util.spec_from_loader("agentctl_mod",
                                        loader=None)
_agentctl = importlib.util.module_from_spec(_spec)
with open(AGENTCTL, encoding="utf-8") as _fh:
    _code = compile(_fh.read(), AGENTCTL, "exec")
exec(_code, _agentctl.__dict__)


def make_task(tid, *, status="READY", priority="P2", scopes=None, deps=None,
              title=None):
    t = dict(_agentctl.TASK_TEMPLATE)
    t.update({
        "id": tid, "title": title or f"Task {tid}", "status": status,
        "priority": priority,
        "resource_scopes": scopes or [f"app/{tid.lower()}"],
        "dependencies": deps or [],
        "acceptance_criteria": ["done when green"],
        "updated_at": "2026-11-14T00:00:00Z",
    })
    return t


class CtlCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.board = os.path.join(self.dir, "KANBAN.md")
        self.lock = os.path.join(self.dir, ".agentctl.lock")
        self.events = os.path.join(self.dir, "logs")
        self.env = dict(os.environ)
        self.env.pop("AGENTCTL_LEASE_TIMEOUT", None)

    def tearDown(self):
        self.tmp.cleanup()

    def write_board(self, tasks):
        header = "# KANBAN — test board"
        with open(self.board, "w", encoding="utf-8") as fh:
            fh.write(_agentctl.render_board(header, {t["id"]: t for t in tasks}))

    def ctl(self, *args, env_extra=None, timeout=60):
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        cmd = [sys.executable, AGENTCTL,
               "--board", self.board, "--lock", self.lock,
               "--events", self.events, "--repo", self.dir, *args]
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env)

    def load(self):
        _, tasks = _agentctl.load_board(self.board)
        return tasks

    def claim_epoch(self, task_id):
        tasks = self.load()
        return tasks[task_id]["lease_epoch"]

    # -- (a) two agents claim the same task at once: exactly one wins -----
    def test_concurrent_same_task_claim(self):
        self.write_board([make_task("TASK-001")])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(self.ctl, "claim", "TASK-001", "--agent", a)
                    for a in ("agent-a", "agent-b")]
            results = [f.result() for f in futs]
        codes = sorted(r.returncode for r in results)
        self.assertEqual(codes, [0, 1], f"expected exactly one winner: {results}")
        tasks = self.load()
        self.assertEqual(tasks["TASK-001"]["status"], "CLAIMED")
        self.assertIn(tasks["TASK-001"]["owner"], ("agent-a", "agent-b"))

    # -- (b) overlapping resource scopes cannot be claimed concurrently -----
    def test_overlapping_scopes_blocked(self):
        self.write_board([
            make_task("TASK-001", scopes=["app/shared"]),
            make_task("TASK-002", scopes=["app/shared"]),
            make_task("TASK-003", scopes=["app/other"]),
        ])
        r1 = self.ctl("claim", "TASK-001", "--agent", "agent-a")
        self.assertEqual(r1.returncode, 0, r1.stdout)
        r2 = self.ctl("claim", "TASK-002", "--agent", "agent-b")
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("overlap", r2.stdout)
        r3 = self.ctl("claim", "TASK-003", "--agent", "agent-b")
        self.assertEqual(r3.returncode, 0, r3.stdout)

    # -- (c) multiple agents claim independent tasks concurrently -----------
    def test_concurrent_independent_claims(self):
        self.write_board([make_task(f"TASK-00{i}") for i in (1, 2, 3)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(self.ctl, "claim", f"TASK-00{i}",
                              "--agent", f"agent-{i}") for i in (1, 2, 3)]
            results = [f.result() for f in futs]
        self.assertTrue(all(r.returncode == 0 for r in results),
                        [r.stdout for r in results])
        tasks = self.load()
        self.assertTrue(all(t["status"] == "CLAIMED"
                            for t in tasks.values()))

    # -- (d) board stays valid after concurrent claims ----------------------
    def test_board_valid_after_concurrency(self):
        self.write_board([make_task(f"TASK-00{i}") for i in (1, 2, 3, 4)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(self.ctl, "claim", "--next", "--agent", f"a{i}")
                    for i in range(4)]
            results = [f.result() for f in futs]
        self.assertTrue(all(r.returncode == 0 for r in results))
        tasks = self.load()  # raises BoardError if malformed
        self.assertEqual(len(tasks), 4)
        owners = [t["owner"] for t in tasks.values()]
        self.assertEqual(len(set(owners)), 4)

    # -- (e) invalid task metadata rejected ---------------------------------
    def test_invalid_metadata_rejected(self):
        # Write the malformed board by hand: render_board() groups tasks by
        # known column, so it would silently drop a bad-status task.
        t = make_task("TASK-001", status="READY")
        t["status"] = "NOPE"
        with open(self.board, "w", encoding="utf-8") as fh:
            fh.write("# KANBAN — test board\n\n## READY\n\n### TASK-001\n\n```json\n")
            fh.write(json.dumps(t, indent=2, sort_keys=True))
            fh.write("\n```\n")
        r = self.ctl("tasks")
        self.assertEqual(r.returncode, 2)
        self.assertIn("TASK-001", r.stderr)
        self.assertIn("invalid status", r.stderr)

    # -- (f) heartbeat keeps lease alive; expiry allows recover ------------
    def test_heartbeat_and_lease_expiry_recover(self):
        self.write_board([make_task("TASK-001")])
        env = {"AGENTCTL_LEASE_TIMEOUT": "2"}
        r = self.ctl("claim", "TASK-001", "--agent", "agent-a", env_extra=env)
        self.assertEqual(r.returncode, 0, r.stdout)
        epoch = self.claim_epoch("TASK-001")
        # Recover too early must fail.
        r = self.ctl("recover", "TASK-001", "--agent", "agent-a", env_extra=env)
        self.assertNotEqual(r.returncode, 0)
        # Heartbeat refreshes the lease; still not recoverable right after.
        r = self.ctl("heartbeat", "TASK-001", "--agent", "agent-a",
                     "--epoch", str(epoch), env_extra=env)
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.ctl("recover", "TASK-001", "--agent", "agent-a", env_extra=env)
        self.assertNotEqual(r.returncode, 0)
        # After the timeout, recovery succeeds and preserves the worktree field.
        time.sleep(2.5)
        r = self.ctl("recover", "TASK-001", "--agent", "agent-a", env_extra=env)
        self.assertEqual(r.returncode, 0, r.stdout)
        tasks = self.load()
        self.assertEqual(tasks["TASK-001"]["status"], "RECOVERY")
        self.assertIsNone(tasks["TASK-001"]["owner"])

    # -- (g) stale lease epoch rejected on submit ---------------------------
    def test_stale_epoch_rejected(self):
        self.write_board([make_task("TASK-001")])
        r = self.ctl("claim", "TASK-001", "--agent", "agent-a")
        self.assertEqual(r.returncode, 0, r.stdout)
        epoch = self.claim_epoch("TASK-001")
        r = self.ctl("submit", "TASK-001", "--agent", "agent-a",
                     "--epoch", str(epoch + 1), "--commit", "abc123")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stale", r.stdout)
        tasks = self.load()
        self.assertEqual(tasks["TASK-001"]["status"], "CLAIMED")
        self.assertIsNone(tasks["TASK-001"]["commit_ref"])

    # -- (h) submit/progress by a non-owner rejected -----------------------
    def test_non_owner_rejected(self):
        self.write_board([make_task("TASK-001")])
        r = self.ctl("claim", "TASK-001", "--agent", "agent-a")
        self.assertEqual(r.returncode, 0, r.stdout)
        epoch = self.claim_epoch("TASK-001")
        r = self.ctl("progress", "TASK-001", "--agent", "agent-b",
                     "--epoch", str(epoch), "--summary", "intruder")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rejected", r.stdout)
        r = self.ctl("submit", "TASK-001", "--agent", "agent-b",
                     "--epoch", str(epoch))
        self.assertNotEqual(r.returncode, 0)
        tasks = self.load()
        self.assertEqual(tasks["TASK-001"]["status"], "CLAIMED")
        self.assertEqual(tasks["TASK-001"]["progress_summary"], "")

    # -- (i) worktree isolation in a real git repo --------------------------
    def test_worktree_isolation(self):
        subprocess.run(["git", "init", "-q", self.dir], check=True)
        subprocess.run(["git", "-C", self.dir, "config", "user.email",
                        "test@example.com"], check=True)
        subprocess.run(["git", "-C", self.dir, "config", "user.name",
                        "Test"], check=True)
        with open(os.path.join(self.dir, "README.md"), "w") as fh:
            fh.write("test repo\n")
        subprocess.run(["git", "-C", self.dir, "add", "."], check=True)
        subprocess.run(["git", "-C", self.dir, "commit", "-qm", "TASK-000: init"],
                       check=True)
        self.write_board([make_task("TASK-001"), make_task("TASK-002")])
        r1 = self.ctl("claim", "TASK-001", "--agent", "agent-a")
        r2 = self.ctl("claim", "TASK-002", "--agent", "agent-b")
        self.assertEqual(r1.returncode, 0, r1.stdout)
        self.assertEqual(r2.returncode, 0, r2.stdout)
        tasks = self.load()
        self.assertNotEqual(tasks["TASK-001"]["worktree"],
                            tasks["TASK-002"]["worktree"])
        self.assertNotEqual(tasks["TASK-001"]["branch"],
                            tasks["TASK-002"]["branch"])
        for tid in ("TASK-001", "TASK-002"):
            wt = os.path.join(self.dir, tasks[tid]["worktree"])
            self.assertTrue(os.path.isdir(wt), f"worktree missing: {wt}")
        # A second claim of an already-claimed task must fail, not reuse.
        r = self.ctl("claim", "TASK-001", "--agent", "agent-c")
        self.assertNotEqual(r.returncode, 0)

    # -- (j) event logs are valid JSONL with the required schema -----------
    def test_event_schema(self):
        self.write_board([make_task("TASK-001")])
        r = self.ctl("claim", "TASK-001", "--agent", "agent-a")
        self.assertEqual(r.returncode, 0, r.stdout)
        epoch = self.claim_epoch("TASK-001")
        r = self.ctl("progress", "TASK-001", "--agent", "agent-a",
                     "--epoch", str(epoch), "--summary", "halfway")
        self.assertEqual(r.returncode, 0, r.stdout)
        required = set(_agentctl.REQUIRED_EVENT_FIELDS)
        found = 0
        for fname in os.listdir(self.events):
            self.assertTrue(fname.endswith(".jsonl"))
            with open(os.path.join(self.events, fname)) as fh:
                for line in fh:
                    ev = json.loads(line)
                    self.assertTrue(required <= set(ev.keys()),
                                    f"missing fields in {fname}: {ev}")
                    found += 1
        self.assertGreaterEqual(found, 2)

    # -- (k) claim --next respects dependencies and priority ----------------
    def test_next_respects_deps_and_priority(self):
        # TASK-002 is P1 but depends on TASK-001 (P2): base must win first.
        self.write_board([
            make_task("TASK-001", priority="P2"),
            make_task("TASK-002", priority="P1", deps=["TASK-001"]),
            make_task("TASK-003", priority="P3"),
        ])
        r = self.ctl("claim", "--next", "--agent", "agent-a")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("TASK-001", r.stdout)
        # Finish TASK-001: progress -> submit -> done (human approval).
        epoch = self.claim_epoch("TASK-001")
        self.assertEqual(self.ctl("progress", "TASK-001", "--agent", "agent-a",
                                  "--epoch", str(epoch),
                                  "--summary", "done").returncode, 0)
        self.assertEqual(self.ctl("submit", "TASK-001", "--agent", "agent-a",
                                  "--epoch", str(epoch)).returncode, 0)
        self.assertEqual(self.ctl("task", "done", "TASK-001",
                                  "--agent", "human").returncode, 0)
        # Now TASK-002 (P1) beats TASK-003 (P3).
        r = self.ctl("claim", "--next", "--agent", "agent-b")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("TASK-002", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
