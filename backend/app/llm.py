"""Chat LLM for GraphRAG extract/summaries. Never logs secret values."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.neo4j_config import load_env_file

PROVIDERS = (
    ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    ("grok", "GROK_API_KEY", "https://api.x.ai/v1/chat/completions", "grok-4-fast"),
    ("xai", "XAI_API_KEY", "https://api.x.ai/v1/chat/completions", "grok-4-fast"),
    ("gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", "gemini-2.0-flash"),
    ("gemini", "GOOGLE_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", "gemini-2.0-flash"),
)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Batch scripts wait for the quota window to reset on HTTP 429. The API sets this
# to False (see app.main) so a request never sleeps past its own timeout.
WAIT_FOR_QUOTA = (os.environ.get("LLM_WAIT_FOR_QUOTA") or "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
QUOTA_RETRY_PAUSE_SECONDS = 5.0


def request_timeout(fallback: int = 120, cap: int = 120) -> int:
    """Finite HTTP timeout in seconds. Caps huge API_TIMEOUT_MS so extract cannot stall forever."""
    raw = (os.environ.get("API_TIMEOUT_MS") or "").strip()
    if raw.isdigit():
        seconds = max(1, int(raw) // 1000)
    else:
        seconds = fallback
    return min(seconds, cap)


def _model_override() -> str:
    return (
        os.environ.get("GRAPHRAG_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("GROK_MODEL")
        or ""
    ).strip()


def _anthropic_messages_url(base: str) -> str:
    base = (base or DEFAULT_ANTHROPIC_URL).rstrip("/")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _detect_anthropic() -> dict[str, str] | None:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    auth_token = (os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    if not api_key and not auth_token:
        return None
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip() or DEFAULT_ANTHROPIC_URL
    return {
        "name": "anthropic",
        "env": "ANTHROPIC_API_KEY" if api_key else "ANTHROPIC_AUTH_TOKEN",
        "url": _anthropic_messages_url(base),
        "model": _model_override() or DEFAULT_ANTHROPIC_MODEL,
        "key": api_key or auth_token,
        "auth_token": auth_token or api_key,
    }


def detect_provider() -> dict[str, str] | None:
    load_env_file()
    anthropic = _detect_anthropic()
    if anthropic:
        return anthropic
    for name, env_name, url, model in PROVIDERS:
        key = (os.environ.get(env_name) or "").strip()
        if not key:
            continue
        return {
            "name": "grok" if name == "xai" else name,
            "env": env_name,
            "url": url,
            "model": _model_override() or model,
            "key": key,
            "auth_token": "",
        }
    return None


def provider_label() -> str | None:
    info = detect_provider()
    if not info:
        return None
    return f"{info['name']}/{info['model']}"


_RESET_AT_RE = re.compile(
    r"Window resets at:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+)",
    re.IGNORECASE,
)


def quota_wait_seconds(
    message: str,
    *,
    retry_after: str | None = None,
    now: datetime | None = None,
) -> float:
    """Seconds to sleep on HTTP 429. Prefer quota window reset; never log secrets."""
    clock = now or datetime.now(timezone.utc)
    match = _RESET_AT_RE.search(message or "")
    if match:
        raw = match.group(1).rstrip(".")
        try:
            reset = datetime.fromisoformat(raw)
        except ValueError:
            reset = None
        if reset is not None:
            if reset.tzinfo is None:
                reset = reset.replace(tzinfo=timezone.utc)
            wait = (reset - clock).total_seconds() + 5.0
            return max(15.0, wait)
    if retry_after:
        try:
            return max(15.0, float(retry_after.strip()))
        except ValueError:
            pass
    return 60.0


def complete_json(system: str, user: str, *, timeout: int | None = None) -> dict[str, Any]:
    raw = complete_text(system, user, json_mode=True, timeout=timeout)
    return parse_json_object(raw)


EMPTY_REPLY_RETRIES = 2


def complete_text(
    system: str, user: str, *, json_mode: bool = False, timeout: int | None = None
) -> str:
    """One completion. An empty reply is retried (the proxy sometimes answers with no text
    in under a second: 11-18% of calls in the 2026-11-15 test runs) as long as the whole
    call stays inside `timeout`."""
    seconds = request_timeout() if timeout is None else timeout
    deadline = time.monotonic() + max(1, int(seconds))
    text = ""
    for _attempt in range(1 + EMPTY_REPLY_RETRIES):
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            break
        text = _complete_once(system, user, json_mode=json_mode, timeout=remaining)
        if (text or "").strip():
            return text
    return text


def _complete_once(
    system: str, user: str, *, json_mode: bool = False, timeout: int | None = None
) -> str:
    info = detect_provider()
    if not info:
        raise RuntimeError("no LLM API key")
    seconds = request_timeout() if timeout is None else timeout
    name = info["name"]
    if name in {"openai", "grok"}:
        return _openai_compatible(info, system, user, json_mode=json_mode, timeout=seconds)
    if name == "anthropic":
        if json_mode and "json" not in system.lower():
            system = system + "\nReturn a single JSON object only."
        return _anthropic(info, system, user, timeout=seconds)
    if name == "gemini":
        return _gemini(info, system, user, timeout=seconds)
    raise RuntimeError(f"unsupported provider {name}")


def parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    body = candidates[-1]
    repaired = re.sub(r",\s*([}\]])", r"\1", body)
    repaired = re.sub(r"\}\s*\{", "},{", repaired)
    repaired = re.sub(r"\]\s*\[", "],[", repaired)
    if repaired not in candidates:
        candidates.append(repaired)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
            return {"items": data}
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise json.JSONDecodeError("empty JSON", text, 0)


def _is_timeout_reason(reason: object) -> bool:
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    name = type(reason).__name__ if reason is not None else ""
    text = str(reason or "").lower()
    return name in {"timeout", "timeouterror"} or "timed out" in text


def _urlopen_json(request: urllib.request.Request, timeout: int) -> dict[str, Any]:
    """Read JSON with both socket idle timeout and wall-clock timeout.

    urllib's timeout only fires between packets, so a slow trickle can stall
    longer than the intended LLM budget. The executor deadline caps wall clock.
    """
    seconds = max(1, int(timeout))

    def worker() -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(worker)
        try:
            return future.result(timeout=seconds)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"wall-clock timeout after {seconds}s") from None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _post(url: str, payload: dict, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    seconds = max(1, int(timeout))
    deadline = time.monotonic() + seconds
    while True:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        attempt_seconds = seconds if WAIT_FOR_QUOTA else max(1, int(deadline - time.monotonic()))
        try:
            return _urlopen_json(request, attempt_seconds)
        except urllib.error.HTTPError as exc:
            snippet = exc.read().decode("utf-8", errors="replace")[:240].replace("\n", " ")
            retry_after = None
            if exc.headers:
                retry_after = exc.headers.get("Retry-After")
            if exc.code == 429:
                wait = quota_wait_seconds(snippet, retry_after=retry_after)
                if not WAIT_FOR_QUOTA:
                    # Request path: never sleep until the quota window resets. Retry
                    # briefly inside the caller's timeout, then fail so the API falls back.
                    pause = min(wait, QUOTA_RETRY_PAUSE_SECONDS)
                    if deadline - time.monotonic() <= pause + 1:
                        raise RuntimeError(f"LLM HTTP 429 quota; resets in about {int(wait)}s") from None
                    time.sleep(pause)
                    continue
                print(f"LLM HTTP 429 quota; sleeping {int(wait)}s until reset", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"LLM HTTP {exc.code} {snippet}") from None
        except urllib.error.URLError as exc:
            if _is_timeout_reason(getattr(exc, "reason", None)):
                raise RuntimeError(f"LLM request timed out after {seconds}s") from None
            raise RuntimeError("LLM URL error") from None
        except TimeoutError:
            raise RuntimeError(f"LLM request timed out after {seconds}s") from None
        except socket.timeout:
            raise RuntimeError(f"LLM request timed out after {seconds}s") from None


def _openai_compatible(
    info: dict[str, str], system: str, user: str, *, json_mode: bool, timeout: int
) -> str:
    payload: dict[str, Any] = {
        "model": info["model"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode and info["name"] == "openai":
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {info['key']}",
        "Content-Type": "application/json",
    }
    data = _post(info["url"], payload, headers, timeout)
    return str(data["choices"][0]["message"]["content"] or "")


def _anthropic(info: dict[str, str], system: str, user: str, timeout: int) -> str:
    payload: dict[str, Any] = {
        "model": info["model"],
        "max_tokens": 4096,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if (os.environ.get("ENABLE_TOOL_SEARCH") or "").strip().lower() in {"1", "true", "yes"}:
        payload["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if info.get("key"):
        headers["x-api-key"] = info["key"]
    token = info.get("auth_token") or info.get("key") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = _post(info["url"], payload, headers, timeout)
    parts = data.get("content") or []
    if parts:
        return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")
    return ""


def _gemini(info: dict[str, str], system: str, user: str, timeout: int) -> str:
    url = info["url"].format(model=info["model"]) + f"?key={info['key']}"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    headers = {"Content-Type": "application/json"}
    data = _post(url, payload, headers, timeout)
    candidates = data.get("candidates") or []
    if not candidates:
        return "{}"
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(str(part.get("text") or "") for part in parts)
