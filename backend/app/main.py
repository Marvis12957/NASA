"""Flame in Freefall API: graph ranking + MiniLM chunk search over Neo4j Aura."""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from pydantic import BaseModel, Field

from app import llm
from app.agents import roster_public
from app.graph import GraphStore, open_store
from app.knowledge import resolve_psi_id
from app.memory import load_turns, save_turn
from app.neo4j_config import ROOT, load_env_file
from app.orchestrator import (
    _GUARD_MESSAGE,
    _payload_figures,
    compiled_graph,
    format_ask,
    image_answer_from_figures,
    initial_state,
)
from app.auth import auth_enabled, public_user, require_user
from app.evidence_filter import contains_image_refusal, wants_image
from app import userdb
from app.tools import SPECS
from app.viz import (
    compose,
    compose_coverage,
    compose_graph,
    compose_profile,
    compose_topic,
    extract_csv,
    missing,
)


load_env_file()
llm.WAIT_FOR_QUOTA = False  # request path: never sleep until an LLM quota window resets


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


# Limits for the routes that call the paid LLM (/api/ask, /api/ask/stream). See .env.example.
MAX_QUESTION_CHARS = _env_int("ASK_MAX_CHARS", 2000)
ASK_RATE_PER_MINUTE = _env_int("ASK_RATE_PER_MIN", 20)
ASK_MAX_CONCURRENT = _env_int("ASK_MAX_CONCURRENT", 4)
ASK_SLOT_TTL_SECONDS = 300.0
API_KEY = (os.environ.get("API_KEY") or "").strip()
CORS_ORIGINS = [item.strip() for item in (os.environ.get("CORS_ORIGINS") or "").split(",") if item.strip()]

_rate_lock = threading.Lock()
_rate_hits: dict[str, deque] = {}
_slot_lock = threading.Lock()
_active_asks: dict[str, float] = {}


def _client_key(request: Request) -> str:
    """Cloudflare puts the real client in CF-Connecting-IP; the API itself listens on localhost."""
    forwarded = request.headers.get("cf-connecting-ip") or (request.headers.get("x-forwarded-for") or "").split(",")[0]
    return forwarded.strip() or (request.client.host if request.client else "unknown")


def _check_api_key(request: Request) -> None:
    if API_KEY and not hmac.compare_digest(request.headers.get("x-api-key") or "", API_KEY):
        raise HTTPException(status_code=401, detail="Missing or wrong X-API-Key")


def _check_rate(request: Request) -> None:
    now = time.monotonic()
    key = _client_key(request)
    with _rate_lock:
        hits = _rate_hits.setdefault(key, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= ASK_RATE_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail="Too many questions, try again in a minute",
                headers={"Retry-After": "60"},
            )
        hits.append(now)
        if len(_rate_hits) > 10000:  # forget idle clients so the table stays bounded
            for idle in [name for name, times in _rate_hits.items() if not times or now - times[-1] > 60]:
                _rate_hits.pop(idle, None)


def _take_ask_slot(request: Request) -> str:
    """API key, per-client rate limit, then one of ASK_MAX_CONCURRENT LLM slots.

    Returns a token for _release_ask_slot. A slot that is never released (for example a
    stream whose client vanished before it started) expires after ASK_SLOT_TTL_SECONDS.
    """
    _check_api_key(request)
    _check_rate(request)
    now = time.monotonic()
    with _slot_lock:
        for token, started in list(_active_asks.items()):
            if now - started > ASK_SLOT_TTL_SECONDS:
                _active_asks.pop(token, None)
        if len(_active_asks) >= ASK_MAX_CONCURRENT:
            raise HTTPException(
                status_code=503,
                detail="Server busy, try again shortly",
                headers={"Retry-After": "5"},
            )
        token = uuid.uuid4().hex
        _active_asks[token] = now
    return token


def _release_ask_slot(token: str) -> None:
    with _slot_lock:
        _active_asks.pop(token, None)


def _release_after(iterator, token: str):
    try:
        yield from iterator
    finally:
        _release_ask_slot(token)


class AskBody(BaseModel):
    question: str = Field(..., min_length=0, max_length=MAX_QUESTION_CHARS)
    sessionId: str | None = None
    language: str | None = None


class VizBody(BaseModel):
    question: str = Field("", max_length=MAX_QUESTION_CHARS)
    state: dict | None = None


def get_store() -> GraphStore:
    store = open_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Neo4j Aura is not configured")
    return store


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = open_store()
    app.state.store = store
    yield
    if store is not None:
        store.close()
        open_store.cache_clear()


_DOCS = _env_flag("ENABLE_DOCS")
app = FastAPI(
    title="Flame in Freefall",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)
if CORS_ORIGINS:
    # The UI reaches the API through the Vite proxy (same origin), so CORS is off by default.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )


@app.exception_handler(ServiceUnavailable)
@app.exception_handler(SessionExpired)
async def neo4j_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    """Aura paused or unreachable mid-request: 503 instead of a 500 traceback."""
    return JSONResponse(status_code=503, content={"detail": "Neo4j Aura is unreachable"})


@app.get("/health")
def health() -> dict:
    store = open_store()
    reachable = bool(store and store.ping())
    return {"ok": reachable, "neo4j": reachable}


@app.get("/api/scenarios")
def scenarios() -> dict:
    return {"scenarios": get_store().list_scenarios()}


@app.get("/api/investigations")
def investigations() -> dict:
    return {"investigations": get_store().list_investigations()}


@app.get("/api/investigations/{psi_id}")
def investigation(psi_id: str) -> dict:
    store = get_store()
    resolved = resolve_psi_id(psi_id, store.investigation_names()) or psi_id.upper()
    card = store.get_investigation(resolved)
    if not card:
        raise HTTPException(status_code=404, detail=f"No investigation {psi_id}")
    return card


@app.get("/api/agents")
def agents() -> dict:
    return {"agents": roster_public(), "tools": SPECS}


def _authenticated_user(request: Request) -> dict | None:
    """The Clerk user for this request when auth is configured.

    Auth off (no Clerk env) → None and the API stays open as before.
    Auth on → a valid Bearer session token is REQUIRED (401 otherwise):
    the app is gated behind sign-in.
    """
    if not auth_enabled():
        return None
    return public_user(require_user(request))


@app.get("/api/me")
def me(request: Request) -> dict:
    """Current Clerk user for the caller's session JWT.

    Auth is optional and OFF unless CLERK_ISSUER/CLERK_JWKS_URL is set in
    the environment: without configuration this returns HTTP 200 with
    authEnabled=false. When configured, a valid Bearer session token
    (verified against the Clerk JWKS) yields the user — and upserts the
    users row in Supabase; a missing/invalid token yields 401.
    """
    if not auth_enabled():
        return {"authEnabled": False, "user": None}
    user = public_user(require_user(request))
    stored = userdb.upsert_user(user)
    return {"authEnabled": True, "user": user, "stored": stored}


class MeSyncBody(BaseModel):
    email: str | None = None
    name: str | None = None
    imageUrl: str | None = None
    language: str | None = None


@app.post("/api/me")
def me_sync(body: MeSyncBody, request: Request) -> dict:
    """Sync the Clerk profile into the users table after sign-in.

    Identity always comes from the verified JWT; the body only fills
    profile fields (email/name/avatar) the default session token may not
    carry.
    """
    if not auth_enabled():
        return {"authEnabled": False, "user": None}
    user = public_user(require_user(request))
    for key in ("email", "name", "imageUrl", "language"):
        value = getattr(body, key)
        if isinstance(value, str) and value.strip():
            user[key] = value.strip()[:320]
    stored = userdb.upsert_user(user)
    return {"authEnabled": True, "user": user, "stored": stored}


@app.get("/api/memory")
def memory_overview(request: Request) -> dict:
    """The signed-in user's stored memory: session/turn counts + facts."""
    if not auth_enabled():
        return {"authEnabled": False, "overview": None}
    user = public_user(require_user(request))
    return {"authEnabled": True, "overview": userdb.user_overview(user["id"])}


_MEDIA_ROOT = (ROOT / "data").resolve()
_MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@app.get("/api/media/{path:path}")
def media(path: str) -> FileResponse:
    """Serve retrieved figure images from the crawl data tree (read-only).

    The resolved path must stay inside ROOT/data and carry an image
    extension; anything else (traversal, .env, documents) is a 404.
    """
    target = (_MEDIA_ROOT / path).resolve()
    if (
        not target.is_relative_to(_MEDIA_ROOT)
        or target.suffix.lower() not in _MEDIA_EXTS
        or not target.is_file()
    ):
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(target)


def _rescue_image_figures(store: GraphStore, answer: dict) -> None:
    """Image intent must never end in a refusal while the library holds
    figures. The synthesis pipeline can legitimately end up with zero
    chunks (e.g. every retrieved chunk was failed-OCR and got filtered out
    of the synthesis evidence); in that case, search figure-bearing chunks
    directly at the endpoint layer and attach them. If the finalized text
    still falsely claims no images exist, replace it with a compact image
    answer — the figures themselves render below it in the UI.
    """
    question = answer.get("question") or ""
    if not wants_image(question):
        return
    if not answer.get("figures"):
        for query in (question, "flame in microgravity", "ngọn lửa vi trọng lực"):
            try:
                data = store.local_search(query, 8)
            except Exception:  # noqa: BLE001
                continue
            figures = _payload_figures(
                data.get("hits") or [], exclude_text=answer.get("text") or ""
            )
            if figures:
                answer["figures"] = figures
                break
    text = answer.get("text") or ""
    if answer.get("figures") and (contains_image_refusal(text) or text == _GUARD_MESSAGE):
        answer["text"] = image_answer_from_figures(answer["figures"], answer.get("answerLanguage"))


@app.post("/api/ask")
def ask(body: AskBody, request: Request) -> dict:
    user = _authenticated_user(request)
    token = _take_ask_slot(request)
    try:
        return _ask(body, user)
    finally:
        _release_ask_slot(token)


def _ask(body: AskBody, user: dict | None = None) -> dict:
    store = get_store()
    session_id = (body.sessionId or "").strip() or str(uuid.uuid4())
    prior, _memory_backend = load_turns(store, session_id)
    if user and not prior:
        # A signed-in user's new session still carries their context:
        # seed the follow-up memory from their stored questions.
        seeded = userdb.recent_user_priors(user["id"])
        if seeded:
            prior = seeded
    # Answer language: the question's own language wins, then the user's
    # setting (this request's picker value, else the one stored in the DB).
    user_language = (body.language or "").strip() or (userdb.get_language(user["id"]) if user else None)
    result = compiled_graph(store).invoke(initial_state(store, body.question, prior, user_language))
    answer = format_ask(result)
    _rescue_image_figures(store, answer)
    ranked_ids = [row.get("id") for row in (answer.get("ranked") or []) if row.get("id")]
    focus = answer.get("focusId") or (ranked_ids[0] if ranked_ids else None)
    scenario = result.get("scenario")
    memory_backend = save_turn(
        store,
        session_id,
        {
            "question": answer["question"],
            "scenario": scenario,
            "focusId": focus,
            "rankedIds": ranked_ids[:5],
            "mode": answer["mode"],
        },
    )
    if user:
        userdb.upsert_user(user)
        if (body.language or "").strip():
            userdb.upsert_user({**user, "language": body.language.strip()[:80]})
        userdb.save_chat_turns(user["id"], session_id, body.question, answer["text"], answer["mode"])
        userdb.capture_fact(user["id"], body.question)
    return {
        "answer": answer["text"],
        "ranked": answer["ranked"],
        "chunks": answer.get("chunks") or [],
        "communities": answer.get("communities") or [],
        "trace": answer["trace"],
        "mode": answer["mode"],
        "focusId": answer["focusId"],
        "question": answer["question"],
        "figures": answer.get("figures") or [],
        "retrieval": answer.get("retrieval"),
        "tables": answer.get("tables") or [],
        "sessionId": session_id,
        "memory": memory_backend,
    }


def _text_chunks(text: str, size: int = 48):
    """Split answer text into small chunks on word boundaries for SSE deltas."""
    chunks, i = [], 0
    while i < len(text):
        j = min(len(text), i + size)
        if j < len(text):
            k = max(text.rfind(" ", i, j), text.rfind("\n", i, j))
            if k > i:
                j = k + 1
        chunks.append(text[i:j])
        i = j
    return chunks


@app.post("/api/ask/stream")
def ask_stream(body: AskBody, request: Request):
    """SSE variant of /api/ask: live trace steps while the agents work,
    then the composed answer streamed out in small chunks."""
    user = _authenticated_user(request)
    token = _take_ask_slot(request)
    try:
        store = get_store()
        session_id = (body.sessionId or "").strip() or str(uuid.uuid4())
        prior, _memory_backend = load_turns(store, session_id)
        if user and not prior:
            seeded = userdb.recent_user_priors(user["id"])
            if seeded:
                prior = seeded
        user_language = (body.language or "").strip() or (userdb.get_language(user["id"]) if user else None)
        graph = compiled_graph(store)
        state0 = initial_state(store, body.question, prior, user_language)
    except BaseException:
        _release_ask_slot(token)
        raise

    def sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def gen():
        seen = 0
        final = None
        try:
            for snap in graph.stream(state0, stream_mode="values"):
                final = snap
                trace = snap.get("trace") or []
                while seen < len(trace):
                    step = trace[seen]
                    seen += 1
                    result = step.get("result") or {}
                    yield sse("trace", {
                        "agentId": step.get("agentId"),
                        "tool": step.get("tool"),
                        "summary": (result.get("summary") or "")[:220],
                    })
            answer = format_ask(final)
            _rescue_image_figures(store, answer)
            ranked_ids = [row.get("id") for row in (answer.get("ranked") or []) if row.get("id")]
            focus = answer.get("focusId") or (ranked_ids[0] if ranked_ids else None)
            memory_backend = save_turn(
                store,
                session_id,
                {
                    "question": answer["question"],
                    "scenario": final.get("scenario"),
                    "focusId": focus,
                    "rankedIds": ranked_ids[:5],
                    "mode": answer["mode"],
                },
            )
            if user:
                userdb.upsert_user(user)
                if (body.language or "").strip():
                    userdb.upsert_user({**user, "language": body.language.strip()[:80]})
                userdb.save_chat_turns(user["id"], session_id, body.question, answer["text"], answer["mode"])
                userdb.capture_fact(user["id"], body.question)
            yield sse("meta", {
                "mode": answer["mode"],
                "focusId": answer["focusId"],
                "ranked": answer["ranked"],
                "figures": answer.get("figures") or [],
                "retrieval": answer.get("retrieval"),
                "tables": answer.get("tables") or [],
                "sessionId": session_id,
                "memory": memory_backend,
            })
            for chunk in _text_chunks(answer["text"] or ""):
                yield sse("delta", {"text": chunk})
                time.sleep(0.012)
            yield sse("done", {})
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": str(exc)[:300]})

    return StreamingResponse(
        _release_after(gen(), token),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/viz")
def viz(body: VizBody) -> dict:
    """UISpec for OpenUI. Graph failures stay HTTP 200 with a MissingData block."""
    store = get_store()
    return compose(store, body.question, body.state)


@app.get("/api/viz/profile/{psi_id}")
def viz_profile(psi_id: str) -> dict:
    def build(store: GraphStore) -> dict:
        resolved = resolve_psi_id(psi_id, store.investigation_names()) or psi_id.upper()
        return compose_profile(store, resolved)

    return _viz_or_missing("profile", "Không đọc được hồ sơ", build)


@app.get("/api/viz/graph/{node_id}")
def viz_graph(node_id: str, depth: int = 1) -> dict:
    bounded = max(1, min(depth, 2))

    def build(store: GraphStore) -> dict:
        resolved = resolve_psi_id(node_id, store.investigation_names()) or node_id
        return compose_graph(store, resolved, depth=bounded)

    return _viz_or_missing("graph", "Không đọc được đồ thị", build)


def _viz_or_missing(intent: str, title: str, builder) -> dict:
    """Neo4j faults stay HTTP 200 with MissingData. A missing store stays 503."""
    try:
        store = get_store()
    except HTTPException:
        raise
    try:
        return builder(store)
    except Exception:  # noqa: BLE001
        return {
            "version": "1.0",
            "intent": intent,
            "title": title,
            "filters": [],
            "blocks": [missing(intent)],
            "followups": [],
        }


@app.get("/api/viz/coverage")
def viz_coverage() -> dict:
    return _viz_or_missing("coverage", "Không đọc được độ phủ", compose_coverage)


@app.get("/api/viz/topic")
def viz_topic(community: str | None = None, question: str = "") -> dict:
    state = {"filters": [{"kind": "community", "id": community}]} if community else None

    def build(store):
        return compose_topic(store, question, state)

    return _viz_or_missing("topic", "Không đọc được chủ đề", build)


@app.get("/api/viz/extract.csv")
def viz_extract_csv() -> Response:
    try:
        store = get_store()
    except HTTPException:
        raise
    try:
        body = extract_csv(store)
    except Exception:  # noqa: BLE001
        body = (
            "# warning: graph unavailable\n"
            "acronym,family,fuels,oxygenMin,oxygenMax,runCount,sampleForm,measurements,documentCount,chunkCount\n"
        )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="extract.csv"'},
    )
