"""Clerk JWT validation — Python port of @clerk/nextjs auth middleware.

Fetches JWKS from Clerk on startup, caches with 1-hour TTL,
validates incoming Bearer tokens using PyJWT.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600

_jwks_client: PyJWKClient | None = None
_jwks_loaded_at: float = 0.0


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_loaded_at
    now = time.monotonic()
    if _jwks_client is None or (now - _jwks_loaded_at) > _JWKS_TTL_SECONDS:
        clerk_domain = os.environ.get("CLERK_DOMAIN") or os.environ.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
        # Derive the JWKS URL: Clerk exposes it at https://<frontend-api>/v1/jwks
        # For local dev, fall back to the standard api.clerk.com endpoint.
        jwks_url = os.environ.get(
            "CLERK_JWKS_URL",
            "https://api.clerk.com/v1/jwks",
        )
        _jwks_client = PyJWKClient(jwks_url, headers={"User-Agent": "chemclaw2-backend/1.0"})
        _jwks_loaded_at = now
    return _jwks_client


async def get_current_user(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency that returns the Clerk userId from a Bearer JWT.

    Raises 401 if the header is absent or the token is invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.removeprefix("Bearer ").strip()

    clerk_secret = os.environ.get("CLERK_SECRET_KEY", "")

    # In local dev without real Clerk keys, accept a synthetic token
    # format "mock:<userId>" so the rest of the stack can be tested end-to-end.
    if clerk_secret.startswith("sk_test_REPLACE") or not clerk_secret:
        if token.startswith("mock:"):
            return token.removeprefix("mock:")
        raise HTTPException(status_code=401, detail="Unauthorized: configure CLERK_SECRET_KEY or use mock:<userId> token")

    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk tokens don't always have audience
        )
        user_id: str = claims.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="Unauthorized: missing sub claim")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
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
