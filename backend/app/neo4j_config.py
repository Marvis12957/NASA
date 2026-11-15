"""Aura connection settings from .env (never hard-coded passwords)."""

from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def neo4j_settings() -> tuple[str, str, str, str]:
    load_env_file()
    uri = (os.environ.get("NEO4J_URI") or "").strip()
    user = (
        os.environ.get("NEO4J_USERNAME")
        or os.environ.get("NEO4J_USER")
        or "neo4j"
    ).strip()
    password = (os.environ.get("NEO4J_PASSWORD") or "").strip()
    database = (os.environ.get("NEO4J_DATABASE") or "neo4j").strip() or "neo4j"
    return uri, user, password, database


def require_aura() -> tuple[str, str, str, str]:
    uri, user, password, database = neo4j_settings()
    missing = [name for name, value in (("NEO4J_URI", uri), ("NEO4J_PASSWORD", password)) if not value]
    if missing:
        raise SystemExit(
            "missing "
            + ", ".join(missing)
            + ". Create a free Neo4j Aura instance, copy .env.example to .env, "
            "paste the URI and password, then re-run."
        )
    return uri, user, password, database


def connect(uri: str, user: str, password: str, attempts: int = 6):
    from neo4j import GraphDatabase

    last = None
    for attempt in range(1, attempts + 1):
        try:
            driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=10)
            driver.verify_connectivity()
            return driver
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < attempts:
                time.sleep(min(2 * attempt, 8))
    raise last  # type: ignore[misc]
