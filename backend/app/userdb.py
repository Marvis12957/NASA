"""Per-user persistence in Supabase Postgres (the Prisma-managed schema).

Stores the signed-in user (identity from verified Clerk claims), their
chat sessions and turns, and simple memory facts — so the agent can keep
memory per user across sessions, not just per browser session.

Connection: DIRECT_URL (session mode) from the root .env, falling back to
DATABASE_URL. Every function is best-effort: a database failure must never
break chat, so errors are swallowed and reported as False/None/[].
"""

from __future__ import annotations

import os
import re

from app.neo4j_config import load_env_file

_MAX_CONTENT = 8000

# Explicit self-statements worth keeping as long-term memory facts.
_FACT_RE = re.compile(
    r"\b(tôi tên là|tôi là|tôi làm|tôi công tác|tôi thích|tôi đang|"
    r"my name is|i am a|i'm a|i work|i study|i live in)\b",
    re.IGNORECASE,
)


def _dsn() -> str:
    load_env_file()
    return (os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL") or "").strip()


def _connect():
    import psycopg

    return psycopg.connect(_dsn(), autocommit=True, connect_timeout=8)


def upsert_user(user: dict) -> bool:
    """Insert/update the users row for a verified Clerk identity."""
    uid = (user or {}).get("id")
    if not uid or not _dsn():
        return False
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, email, name, "imageUrl", language, "createdAt", "updatedAt", "lastSeenAt")
                VALUES (%s, %s, %s, %s, %s, now(), now(), now())
                ON CONFLICT (id) DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, users.email),
                    name = COALESCE(EXCLUDED.name, users.name),
                    "imageUrl" = COALESCE(EXCLUDED."imageUrl", users."imageUrl"),
                    language = COALESCE(EXCLUDED.language, users.language),
                    "updatedAt" = now(),
                    "lastSeenAt" = now()
                """,
                (uid, user.get("email"), user.get("name"), user.get("imageUrl"), user.get("language")),
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def get_language(user_id: str) -> str | None:
    """The user's saved answer-language setting (code or free-typed name)."""
    if not user_id or not _dsn():
        return None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT language FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return (row[0] or None) if row else None
    except Exception:  # noqa: BLE001
        return None


def save_chat_turns(
    user_id: str, session_id: str, question: str, answer_text: str, mode: str = ""
) -> bool:
    """Append one Q&A pair to the user's chat history in Postgres."""
    if not user_id or not session_id or not _dsn():
        return False
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (id, "userId", "createdAt", "updatedAt")
                VALUES (%s, %s, now(), now())
                ON CONFLICT (id) DO UPDATE SET "updatedAt" = now()
                """,
                (session_id, user_id),
            )
            cur.executemany(
                """
                INSERT INTO chat_turns (id, "sessionId", "userId", role, content, "createdAt")
                VALUES (gen_random_uuid()::text, %s, %s, %s, %s, now())
                """,
                [
                    (session_id, user_id, "user", (question or "")[:_MAX_CONTENT]),
                    (session_id, user_id, "assistant", (answer_text or "")[:_MAX_CONTENT]),
                ],
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def recent_user_priors(user_id: str, limit: int = 4) -> list[dict]:
    """The user's most recent questions (any session), shaped like the
    session-memory turns initial_state expects — this is what lets a
    signed-in user's brand-new session still carry their context."""
    if not user_id or not _dsn():
        return []
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT content FROM chat_turns
                WHERE "userId" = %s AND role = 'user'
                ORDER BY "createdAt" DESC LIMIT %s
                """,
                (user_id, limit),
            )
            rows = [row[0] for row in cur.fetchall()]
        return [
            {"question": q, "scenario": None, "focusId": None, "rankedIds": [], "mode": ""}
            for q in reversed(rows)
        ]
    except Exception:  # noqa: BLE001
        return []


def capture_fact(user_id: str, question: str) -> bool:
    """Keep explicit self-statements ("tôi tên là…", "my name is…") as
    long-term memory facts for the user."""
    text = (question or "").strip()
    if not user_id or not text or not _FACT_RE.search(text) or not _dsn():
        return False
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_memories (id, "userId", kind, content, "createdAt", "updatedAt")
                VALUES (gen_random_uuid()::text, %s, 'fact', %s, now(), now())
                """,
                (user_id, text[:1000]),
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def user_overview(user_id: str) -> dict | None:
    """Counts + memory facts for GET /api/memory."""
    if not user_id or not _dsn():
        return None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM chat_sessions WHERE "userId" = %s', (user_id,))
            sessions = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM chat_turns WHERE "userId" = %s', (user_id,))
            turns = cur.fetchone()[0]
            cur.execute(
                """
                SELECT kind, content, "createdAt" FROM user_memories
                WHERE "userId" = %s ORDER BY "createdAt" DESC LIMIT 50
                """,
                (user_id,),
            )
            facts = [
                {"kind": kind, "content": content, "createdAt": created.isoformat()}
                for kind, content, created in cur.fetchall()
            ]
        return {"sessions": sessions, "turns": turns, "facts": facts}
    except Exception:  # noqa: BLE001
        return None
