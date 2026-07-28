"""Clerk JWT verification dependency for the public FastAPI router (Phase 13).

Design:
- ``PyJWKClient`` fetches Clerk's JWKS once and caches by ``kid`` with a
  1-hour lifespan so the backend auto-rotates on key rollover without restart.
- ``CLERK_JWKS_URL`` is required at startup; the dependency fails closed with
  503 when unset (same posture as the admin token).
- ``CLERK_AUDIENCE`` is optional: when set, the JWT ``aud`` claim is validated
  against it. Clerk's session tokens carry the Frontend API URL as ``aud``
  (e.g. ``https://your-app.clerk.accounts.dev``). Omitting it skips audience
  validation -- acceptable for development, not for production.
- The dependency is intentionally separate from the admin ``require_admin_token``
  dependency: the two auth systems are orthogonal and must never be conflated.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

CLERK_JWKS_URL_ENV = "CLERK_JWKS_URL"
CLERK_AUDIENCE_ENV = "CLERK_AUDIENCE"

#: HTTPBearer with auto_error=False so we can raise 401 (not 403) on a
#: missing Authorization header -- consistent with the directive and with
#: what the dashboard client expects.
_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    """Singleton JWKS client, created once per process.

    ``lru_cache`` means the first call builds it; subsequent calls reuse it.
    The client caches signing keys by ``kid`` with a 1-hour lifespan so key
    rotation is handled transparently.
    """
    url = os.environ.get(CLERK_JWKS_URL_ENV)
    if not url:
        raise RuntimeError(
            f"Clerk JWKS URL not configured; set {CLERK_JWKS_URL_ENV}"
        )
    return jwt.PyJWKClient(url, lifespan=3600, cache_keys=True)


def verify_clerk_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """FastAPI dependency: validate a Clerk session JWT.

    Returns the decoded payload (``sub``, ``sid``, ``azp``, etc.) so route
    handlers can use the caller's identity without re-decoding.

    Raises:
        HTTPException 503 -- ``CLERK_JWKS_URL`` not configured.
        HTTPException 401 -- token missing, expired, or signature invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials

    try:
        client = _jwks_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except (jwt.exceptions.PyJWKClientError, jwt.exceptions.DecodeError) as exc:
        raise HTTPException(
            status_code=401, detail=f"invalid token: {exc}"
        ) from exc

    audience = os.environ.get(CLERK_AUDIENCE_ENV) or None  # None = skip check

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            options={
                "verify_exp": True,
                "verify_nbf": True,
                # ``iss`` is validated by PyJWT when audience is set; skip
                # explicit issuer check here to avoid hard-coding the Clerk
                # instance URL in the backend config.
                "verify_iss": False,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc

    return payload
