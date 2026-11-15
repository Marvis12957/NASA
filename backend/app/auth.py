"""Clerk session-JWT verification (JWKS) — optional and OFF by default.

Auth activates only when CLERK_ISSUER (or CLERK_JWKS_URL) is present in the
environment. With no Clerk configuration the app behaves exactly as before:
every route stays public (the demo link must never sit behind a login) and
GET /api/me simply reports that auth is not configured.

Setup (see docs/AUTH_CLERK.md): create a Clerk application, enable the
Google provider, then set in /workspace/.env:

    CLERK_ISSUER=https://<your-instance>.clerk.accounts.dev
    CLERK_SECRET_KEY=sk_test_...   (server-side only; not needed for JWKS
                                    verification — reserved for future Clerk
                                    Backend API calls; never commit it)

The frontend needs VITE_CLERK_PUBLISHABLE_KEY at build time.
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import HTTPException, Request

from app.neo4j_config import load_env_file


def _issuer() -> str:
    load_env_file()
    return (os.environ.get("CLERK_ISSUER") or "").strip().rstrip("/")


def _jwks_url() -> str:
    load_env_file()
    url = (os.environ.get("CLERK_JWKS_URL") or "").strip()
    if url:
        return url
    issuer = _issuer()
    return f"{issuer}/.well-known/jwks.json" if issuer else ""


def auth_enabled() -> bool:
    """True only when a Clerk issuer/JWKS is configured in the environment."""
    return bool(_jwks_url())


@lru_cache(maxsize=1)
def _jwks_client(jwks_url: str):
    # PyJWT is imported lazily so a missing dependency can never break the
    # public API while auth is off.
    import jwt

    return jwt.PyJWKClient(jwks_url)


def require_user(request: Request) -> dict:
    """Verify the Clerk session JWT from `Authorization: Bearer <token>`.

    Returns the token claims. Raises HTTPException 503 when auth is not
    configured, 401 when the token is missing or fails verification
    (signature against the instance JWKS, issuer, expiry).
    """
    if not auth_enabled():
        raise HTTPException(
            status_code=503,
            detail="Auth chưa được cấu hình (thiếu CLERK_ISSUER/CLERK_JWKS_URL).",
        )
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Thiếu Authorization: Bearer <session token>."
        )
    token = header[len("Bearer ") :].strip()
    import jwt

    issuer = _issuer()
    options: dict = {"require": ["exp", "sub"]}
    kwargs: dict = {}
    if issuer:
        kwargs["issuer"] = issuer
        options["require"].append("iss")
    try:
        signing_key = _jwks_client(_jwks_url()).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options=options,
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=401, detail=f"Token không hợp lệ ({exc.__class__.__name__})."
        ) from exc
    return claims


def public_user(claims: dict) -> dict:
    """The safe subset of claims exposed by GET /api/me."""
    return {
        "id": claims.get("sub"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "imageUrl": claims.get("image_url") or claims.get("picture"),
        "sessionId": claims.get("sid"),
    }
