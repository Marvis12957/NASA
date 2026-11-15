import os
from pathlib import Path

import pytest

os.environ.setdefault("RERANK_CROSS_ENCODER", "0")
os.environ.setdefault("AGENT_TOOL_CHOICE", "0")
os.environ.setdefault("AGENT_QUERY_REWRITE", "0")


@pytest.fixture(autouse=True)
def _hide_demo_snapshots(monkeypatch):
    """Keep backend/var/viz_snapshots.json out of unit tests. A test that
    checks the short-circuit sets SNAPSHOT_PATH itself."""
    monkeypatch.setattr(
        "app.viz.SNAPSHOT_PATH",
        Path("/tmp/nasa-viz-snapshots-absent.json"),
    )
