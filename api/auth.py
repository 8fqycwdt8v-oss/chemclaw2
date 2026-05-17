"""Clerk JWT validation — Python port of @clerk/nextjs auth middleware.

Fetches JWKS from Clerk on startup, caches with 1-hour TTL,
validates incoming Bearer tokens using PyJWT.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600

_jwks_client: PyJWKClient | None = None
_jwks_loaded_at: float = 0.0


def _make_jwks_client() -> PyJWKClient:
    """Create a PyJWKClient pointing at the Clerk Frontend API JWKS endpoint.

    Clerk's public JWKS is at https://<clerk-domain>/.well-known/jwks.json.
    Set CLERK_JWKS_URL to override (required in production).
    """
    jwks_url = os.environ.get("CLERK_JWKS_URL")
    if not jwks_url:
        # Derive from CLERK_DOMAIN if set, else fall back to api.clerk.com for
        # environments that configure tokens without a custom domain.
        clerk_domain = os.environ.get("CLERK_DOMAIN", "")
        jwks_url = (
            f"https://{clerk_domain}/.well-known/jwks.json"
            if clerk_domain
            else "https://api.clerk.com/v1/jwks"
        )
    return PyJWKClient(jwks_url, headers={"User-Agent": "chemclaw2-backend/1.0"})


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_loaded_at
    now = time.monotonic()
    if _jwks_client is None or (now - _jwks_loaded_at) > _JWKS_TTL_SECONDS:
        _jwks_client = _make_jwks_client()
        _jwks_loaded_at = now
    return _jwks_client


async def get_current_user(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency that returns the Clerk userId from a Bearer JWT.

    Raises 401 if the header is absent or the token is invalid.

    Local dev: set ALLOW_MOCK_AUTH=1 and pass "Bearer mock:<userId>" to bypass
    Clerk validation. This env var must never be set in production.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.removeprefix("Bearer ").strip()

    # Mock auth: only active when explicitly enabled via env var.
    if os.environ.get("ALLOW_MOCK_AUTH") == "1":
        if token.startswith("mock:"):
            user_id = token.removeprefix("mock:")
            if not user_id:
                raise HTTPException(status_code=401, detail="Unauthorized: empty mock user ID")
            return user_id

    try:
        client = _get_jwks_client()
        # get_signing_key_from_jwt may do a blocking HTTP fetch on cache miss;
        # run it in a thread pool to keep the event loop unblocked.
        loop = asyncio.get_event_loop()
        signing_key = await loop.run_in_executor(
            None, client.get_signing_key_from_jwt, token
        )
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk tokens don't always have audience
        )
        user_id = claims.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="Unauthorized: missing sub claim")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWKClientError as e:
        # JWKS fetch failure (network error, kid not found, etc.) — fail closed
        logger.warning("jwks_client_error: %s", e)
        raise HTTPException(status_code=401, detail="Unauthorized")
    except jwt.InvalidTokenError as e:
        logger.warning("jwt_validation_failed: %s", e)
        raise HTTPException(status_code=401, detail="Unauthorized")


async def get_optional_user(authorization: str | None = Header(None)) -> str | None:
    """Like get_current_user but returns None instead of raising for unauthenticated requests."""
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None


async def get_admin_user(authorization: str | None = Header(None)) -> str:
    """Dependency that requires the caller to be in ADMIN_USER_IDS env var."""
    user_id = await get_current_user(authorization)
    raw = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = {x.strip() for x in raw.split(",") if x.strip()}
    if user_id not in admin_ids:
        logger.warning("admin_access_denied: user=%s", user_id)
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id
