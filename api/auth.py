"""Clerk JWT validation — Python port of @clerk/nextjs auth middleware.

Fetches JWKS from Clerk on startup, caches with 1-hour TTL,
validates incoming Bearer tokens using PyJWT.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import time
from typing import Any

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

# Matches the sub-claim regex enforced by the GUI (api_client.py:_SUB_RE).
# No dots or colons — they would break the svc token wire format.
_SVC_SUB_RE = re.compile(r"^[A-Za-z0-9_\-|]{1,255}$")
_SVC_TOKEN_MAX_AGE = 300  # seconds — bound replay window to ±5 min

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600

_jwks_client: PyJWKClient | None = None
_jwks_loaded_at: float = 0.0

# Populated by validate_auth_config() at app startup. Falsy means "no admins
# configured" — admin routes will refuse all access.
_admin_user_ids: frozenset[str] = frozenset()
_admin_ids_loaded: bool = False

# Tracks whether the per-request "ALLOW_MOCK_AUTH is enabled" warning has
# fired this process. The startup-time check in validate_auth_config()
# already refuses prod ENVs, so this is just to avoid log spam in dev.
_mock_auth_warned: bool = False

# Envs in which the mock-auth bypass is allowed. Anything else and an
# ALLOW_MOCK_AUTH=1 raises at startup.
_DEV_ENVS = {"dev", "development", "test", "local"}


def validate_auth_config() -> None:
    """One-shot startup validation of auth configuration.

    Loads ADMIN_USER_IDS into a frozenset, and refuses startup when
    ALLOW_MOCK_AUTH=1 is set outside a dev env (ENV ∉ {dev, development,
    test, local}).
    """
    global _admin_user_ids, _admin_ids_loaded

    env = os.environ.get("ENV", "").lower()
    is_dev = env in _DEV_ENVS

    if os.environ.get("ALLOW_MOCK_AUTH") == "1":
        if not is_dev:
            raise RuntimeError(
                f"ALLOW_MOCK_AUTH=1 is set but ENV={env!r} is not a dev "
                f"environment ({sorted(_DEV_ENVS)}). Refusing to start — "
                f"this combination would bypass Clerk in production."
            )
        logger.warning("ALLOW_MOCK_AUTH is enabled (ENV=%s) — never set this in production", env)

    # In non-dev environments, require CLERK_ISSUER so the `iss` claim is
    # validated. Without it, jwt.decode() is called with issuer=None and
    # skips issuer validation entirely — leaving only the signature check.
    if not is_dev and not os.environ.get("CLERK_ISSUER"):
        raise RuntimeError(
            f"CLERK_ISSUER must be set when ENV={env!r} (non-dev). "
            f"Without it, JWT issuer validation is silently skipped."
        )

    raw = os.environ.get("ADMIN_USER_IDS", "")
    _admin_user_ids = frozenset(x.strip() for x in raw.split(",") if x.strip())
    _admin_ids_loaded = True
    if not _admin_user_ids:
        logger.warning("ADMIN_USER_IDS env var is empty — all admin routes will return 403")
    else:
        logger.info("admin_user_ids_loaded count=%d", len(_admin_user_ids))


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


def _verify_svc_token(token: str, secret: str) -> str:
    """Verify a svc.{sub}.{iat}.{sig} service token issued by the GUI.

    Fail closed on any parse error, bad sig, or expired iat.
    """
    parts = token.split(".", 3)
    if len(parts) != 4 or parts[0] != "svc":
        raise HTTPException(status_code=401, detail="Unauthorized")
    _, sub, iat_str, sig = parts
    if not _SVC_SUB_RE.match(sub):
        logger.warning("svc_token_invalid_sub")
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        iat = int(iat_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized") from None
    age = int(time.time()) - iat
    if abs(age) > _SVC_TOKEN_MAX_AGE:
        logger.warning("svc_token_expired sub=%s age=%ds", sub, age)
        raise HTTPException(status_code=401, detail="Unauthorized")
    msg = f"{sub}:{iat}".encode()
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        logger.warning("svc_token_bad_sig sub=%s", sub)
        raise HTTPException(status_code=401, detail="Unauthorized")
    return sub


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
        global _mock_auth_warned
        if not _mock_auth_warned:
            logger.warning("ALLOW_MOCK_AUTH is enabled — NEVER set this in production")
            _mock_auth_warned = True
        if token.startswith("mock:"):
            user_id = token.removeprefix("mock:")
            if not user_id:
                raise HTTPException(status_code=401, detail="Unauthorized: empty mock user ID")
            return user_id

    # Service-token path: GUI → backend via HMAC-SHA256 signed token.
    # Only active when CHEMCLAW2_SERVICE_SECRET is set (production).
    service_secret = os.environ.get("CHEMCLAW2_SERVICE_SECRET")
    if service_secret and token.startswith("svc."):
        return _verify_svc_token(token, service_secret)

    try:
        client = _get_jwks_client()
        # get_signing_key_from_jwt may do a blocking HTTP fetch on cache miss;
        # run it in a thread pool to keep the event loop unblocked.
        loop = asyncio.get_running_loop()
        signing_key = await loop.run_in_executor(
            None, client.get_signing_key_from_jwt, token
        )
        try:
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey as _RSAPublicKey
            if not isinstance(signing_key.key, _RSAPublicKey):
                logger.warning("jwks_key_type_unexpected type=%s", type(signing_key.key).__name__)
                raise HTTPException(status_code=401, detail="Unauthorized")
        except ImportError:
            pass  # cryptography package unavailable; skip algorithm-confusion check
        # CLERK_ISSUER is required in non-dev envs (enforced at startup by
        # validate_auth_config()). In dev/test it may be unset, in which case
        # issuer validation is skipped.
        issuer = os.environ.get("CLERK_ISSUER") or None
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},  # Clerk tokens don't always have audience
        )
        user_id = claims.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="Unauthorized: missing sub claim")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.PyJWKClientError as e:
        # JWKS fetch failure (network error, kid not found, etc.) — fail closed.
        # Suppress the inner exception so 401 responses don't leak JWKS URLs
        # or network error detail (OWASP A05).
        logger.warning("jwks_client_error: %s", e)
        raise HTTPException(status_code=401, detail="Unauthorized") from None
    except jwt.InvalidTokenError as e:
        logger.warning("jwt_validation_failed: %s", e)
        raise HTTPException(status_code=401, detail="Unauthorized") from None


async def get_optional_user(authorization: str | None = Header(None)) -> str | None:
    """Like get_current_user but returns None instead of raising for unauthenticated requests."""
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException as exc:
        if exc.status_code != 401:
            logger.warning("get_optional_user_unexpected_error status=%d", exc.status_code)
            raise
        return None


async def get_admin_user(authorization: str | None = Header(None)) -> str:
    """Dependency that requires the caller to be in ADMIN_USER_IDS env var.

    ADMIN_USER_IDS is parsed once at startup by validate_auth_config(); this
    dependency just checks membership in the cached frozenset.
    """
    user_id = await get_current_user(authorization)
    if not _admin_ids_loaded:
        # Defensive: validate_auth_config() should have run at startup, but if
        # something imports get_admin_user before lifespan starts, fail closed.
        logger.error("admin_check_before_startup_init user=%s", user_id)
        raise HTTPException(status_code=503, detail="Auth not initialized")
    if user_id not in _admin_user_ids:
        logger.warning("admin_access_denied: user=%s", user_id)
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id
