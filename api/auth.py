"""Microsoft Entra ID (Azure AD) JWT validation.

The Streamlit frontend logs the user in against Entra (MSAL, authorization-code
flow) and acquires an access token whose audience is *this* backend API
(``api://<AZURE_BACKEND_CLIENT_ID>/access_as_user``). It forwards that token
verbatim as a ``Bearer`` header — a pass-through, not an On-Behalf-Of exchange,
because the backend's own downstream calls (Anthropic, OpenAI, Postgres, the
MCP servers) are not Entra-protected and so never need a second token hop.

This module fetches Entra's public JWKS, caches it with a 1-hour TTL, and
validates each incoming token's signature, issuer, audience, and expiry. Access
is gated to a single Microsoft Entra security group via an **app role**: the
group is assigned to one app role on the backend app registration, the role
arrives in the ``roles`` claim, and ``AZURE_REQUIRED_ROLE`` must be present.
"Assignment required" on the enterprise app is the primary gate (Entra refuses
to mint a token for a non-member); the role check here is defence in depth.

Identity is the ``oid`` claim — the stable directory object id for the user,
which is the same across every app in the tenant. (``sub`` is pairwise per app
and would change if the token audience ever changed.)
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

# Entra config env vars that must be present outside dev so token validation
# is not silently weakened (issuer + audience + role gate all derive from them).
_REQUIRED_ENTRA_VARS = ("AZURE_TENANT_ID", "AZURE_BACKEND_CLIENT_ID", "AZURE_REQUIRED_ROLE")


def validate_auth_config() -> None:
    """One-shot startup validation of auth configuration.

    Loads ADMIN_USER_IDS into a frozenset, refuses startup when
    ALLOW_MOCK_AUTH=1 is set outside a dev env, and — outside dev — requires
    the Entra config (tenant, audience, required role) so token validation
    and the group gate are actually enforced rather than silently skipped.
    """
    global _admin_user_ids, _admin_ids_loaded

    env = os.environ.get("ENV", "").lower()
    is_dev = env in _DEV_ENVS

    if os.environ.get("ALLOW_MOCK_AUTH") == "1":
        if not is_dev:
            raise RuntimeError(
                f"ALLOW_MOCK_AUTH=1 is set but ENV={env!r} is not a dev "
                f"environment ({sorted(_DEV_ENVS)}). Refusing to start — "
                f"this combination would bypass Entra in production."
            )
        logger.warning("ALLOW_MOCK_AUTH is enabled (ENV=%s) — never set this in production", env)

    # Outside dev, require the full Entra config. Without AZURE_TENANT_ID the
    # JWKS URL and expected issuer can't be derived; without
    # AZURE_BACKEND_CLIENT_ID audience validation is skipped (token-confusion
    # risk); without AZURE_REQUIRED_ROLE the AD-group gate doesn't fire.
    if not is_dev:
        missing = [v for v in _REQUIRED_ENTRA_VARS if not os.environ.get(v)]
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} must be set when ENV={env!r} (non-dev). "
                f"Without them Entra token validation or the AD-group gate is "
                f"silently skipped."
            )

    raw = os.environ.get("ADMIN_USER_IDS", "")
    _admin_user_ids = frozenset(x.strip() for x in raw.split(",") if x.strip())
    _admin_ids_loaded = True
    if not _admin_user_ids:
        logger.warning("ADMIN_USER_IDS env var is empty — all admin routes will return 403")
    else:
        logger.info("admin_user_ids_loaded count=%d", len(_admin_user_ids))


def _make_jwks_client() -> PyJWKClient:
    """Create a PyJWKClient pointing at the Entra tenant's public JWKS endpoint.

    Entra publishes signing keys at
    ``https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys``.
    """
    tenant = os.environ.get("AZURE_TENANT_ID")
    if not tenant:
        raise RuntimeError("AZURE_TENANT_ID must be set to validate Entra tokens")
    jwks_url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
    return PyJWKClient(jwks_url, headers={"User-Agent": "chemclaw2-backend/1.0"})


def _expected_issuer() -> str | None:
    """The v2.0 token issuer for the configured tenant, or None if unset.

    Requires the backend app registration to set ``accessTokenAcceptedVersion``
    to 2 so tokens carry this issuer (v1 tokens use ``sts.windows.net``).
    """
    tenant = os.environ.get("AZURE_TENANT_ID")
    return f"https://login.microsoftonline.com/{tenant}/v2.0" if tenant else None


def _expected_audience() -> list[str] | None:
    """Accepted ``aud`` values: the backend client id and its App ID URI form."""
    client_id = os.environ.get("AZURE_BACKEND_CLIENT_ID")
    if not client_id:
        return None
    return [client_id, f"api://{client_id}"]


def _require_app_role(claims: dict[str, Any]) -> None:
    """Enforce the AD-group gate via the app-role claim. Fail closed (403).

    The Entra security group is assigned to a single app role on the backend
    app registration, so membership surfaces as ``roles: [<AZURE_REQUIRED_ROLE>]``.
    No-op when AZURE_REQUIRED_ROLE is unset (dev).
    """
    required = os.environ.get("AZURE_REQUIRED_ROLE")
    if not required:
        return
    roles = claims.get("roles") or []
    if required not in roles:
        logger.warning("entra_role_denied oid=%s required=%s", claims.get("oid"), required)
        raise HTTPException(status_code=403, detail="Forbidden")


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
    """FastAPI dependency that returns the Entra user's oid from a Bearer JWT.

    Raises 401 if the header is absent or the token is invalid, and 403 if the
    user lacks the required app role (AD-group gate).

    Local dev: set ALLOW_MOCK_AUTH=1 and pass "Bearer mock:<userId>" to bypass
    Entra validation. This env var must never be set in production.
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
        # Issuer + audience are required in non-dev envs (enforced at startup by
        # validate_auth_config()). In dev/test they may be unset, in which case
        # the corresponding check is skipped.
        issuer = _expected_issuer()
        audience = _expected_audience()
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"verify_aud": audience is not None},
        )
        # AD-group gate (app role). Raises 403; not a jwt error, so it
        # propagates past the except clauses below unchanged.
        _require_app_role(claims)
        user_id = claims.get("oid", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="Unauthorized: missing oid claim")
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
        if exc.status_code not in (401, 403):
            logger.warning("get_optional_user_unexpected_error status=%d", exc.status_code)
            raise
        return None


async def get_admin_user(authorization: str | None = Header(None)) -> str:
    """Dependency that requires the caller to be in ADMIN_USER_IDS env var.

    ADMIN_USER_IDS is parsed once at startup by validate_auth_config(); this
    dependency just checks membership in the cached frozenset. The ids are
    Entra ``oid`` values (the same string get_current_user returns).
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
