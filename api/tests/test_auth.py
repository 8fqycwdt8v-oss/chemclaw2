"""Tests for `api.auth` — JWT validation, mock-auth, svc tokens, admin.

These are pure-unit tests. JWT signing keys are generated with `cryptography`
at test time; the JWKS client is monkey-patched to return the synthetic key.
No network, no real Clerk, no DB.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException


@pytest.fixture
def rsa_key() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


@pytest.fixture
def patched_jwks(monkeypatch: pytest.MonkeyPatch, rsa_key) -> rsa.RSAPublicKey:
    """Replace _get_jwks_client() with a stub that yields our test public key.

    The stub mirrors PyJWT's PyJWKClient API surface (just get_signing_key_from_jwt).
    """
    from api import auth as auth_mod

    _, pub = rsa_key

    class _Stub:
        class _Key:
            def __init__(self, k: rsa.RSAPublicKey) -> None:
                self.key = k

        def get_signing_key_from_jwt(self, token: str) -> "_Stub._Key":
            return _Stub._Key(pub)

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: _Stub())
    return pub


def _sign(priv: rsa.RSAPrivateKey, claims: dict[str, Any]) -> str:
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256")


# ── validate_auth_config ─────────────────────────────────────────────────────


def test_validate_refuses_mock_auth_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import validate_auth_config

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ALLOW_MOCK_AUTH", "1")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example.com")
    with pytest.raises(RuntimeError, match="ALLOW_MOCK_AUTH"):
        validate_auth_config()


def test_validate_requires_clerk_issuer_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import validate_auth_config

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    with pytest.raises(RuntimeError, match="CLERK_ISSUER"):
        validate_auth_config()


def test_validate_allows_missing_clerk_issuer_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import validate_auth_config

    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    # Must not raise.
    validate_auth_config()


def test_validate_succeeds_in_prod_with_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import validate_auth_config

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example.com")
    validate_auth_config()


# ── get_current_user: bearer header parsing ──────────────────────────────────


@pytest.mark.asyncio
async def test_no_header_raises_401() -> None:
    from api.auth import get_current_user

    with pytest.raises(HTTPException) as ei:
        await get_current_user(None)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_non_bearer_raises_401() -> None:
    from api.auth import get_current_user

    with pytest.raises(HTTPException) as ei:
        await get_current_user("Basic abc")
    assert ei.value.status_code == 401


# ── Mock auth path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_auth_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ALLOW_MOCK_AUTH is not '1', 'Bearer mock:foo' must NOT bypass
    real validation. The token falls through to JWKS verification and fails."""
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.delenv("CHEMCLAW2_SERVICE_SECRET", raising=False)
    with pytest.raises(HTTPException) as ei:
        await get_current_user("Bearer mock:attacker")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_mock_auth_works_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import get_current_user

    monkeypatch.setenv("ALLOW_MOCK_AUTH", "1")
    user = await get_current_user("Bearer mock:alice")
    assert user == "alice"


@pytest.mark.asyncio
async def test_mock_auth_rejects_empty_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import get_current_user

    monkeypatch.setenv("ALLOW_MOCK_AUTH", "1")
    with pytest.raises(HTTPException) as ei:
        await get_current_user("Bearer mock:")
    assert ei.value.status_code == 401


# ── JWT validation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_jwt_returns_sub(
    monkeypatch: pytest.MonkeyPatch, rsa_key, patched_jwks
) -> None:
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.delenv("CHEMCLAW2_SERVICE_SECRET", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)

    priv, _ = rsa_key
    now = int(time.time())
    token = _sign(priv, {"sub": "user-abc", "iat": now, "exp": now + 3600})
    assert await get_current_user(f"Bearer {token}") == "user-abc"


@pytest.mark.asyncio
async def test_expired_jwt_raises_401(
    monkeypatch: pytest.MonkeyPatch, rsa_key, patched_jwks
) -> None:
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.delenv("CHEMCLAW2_SERVICE_SECRET", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)

    priv, _ = rsa_key
    now = int(time.time())
    token = _sign(priv, {"sub": "user-abc", "iat": now - 7200, "exp": now - 3600})
    with pytest.raises(HTTPException) as ei:
        await get_current_user(f"Bearer {token}")
    assert ei.value.status_code == 401
    assert "expired" in (ei.value.detail or "").lower()


@pytest.mark.asyncio
async def test_jwt_missing_sub_raises_401(
    monkeypatch: pytest.MonkeyPatch, rsa_key, patched_jwks
) -> None:
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.delenv("CHEMCLAW2_SERVICE_SECRET", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)

    priv, _ = rsa_key
    now = int(time.time())
    token = _sign(priv, {"iat": now, "exp": now + 3600})
    with pytest.raises(HTTPException) as ei:
        await get_current_user(f"Bearer {token}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_jwt_wrong_issuer_raises_401(
    monkeypatch: pytest.MonkeyPatch, rsa_key, patched_jwks
) -> None:
    """When CLERK_ISSUER is set, jwt.decode validates the `iss` claim and
    rejects tokens with a mismatching issuer."""
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.delenv("CHEMCLAW2_SERVICE_SECRET", raising=False)
    monkeypatch.setenv("CLERK_ISSUER", "https://expected.clerk.example.com")

    priv, _ = rsa_key
    now = int(time.time())
    token = _sign(priv, {
        "sub": "user-abc",
        "iat": now,
        "exp": now + 3600,
        "iss": "https://attacker.example.com",
    })
    with pytest.raises(HTTPException) as ei:
        await get_current_user(f"Bearer {token}")
    assert ei.value.status_code == 401


# ── Svc tokens ───────────────────────────────────────────────────────────────


def _make_svc(sub: str, secret: str, iat: int | None = None) -> str:
    iat = iat if iat is not None else int(time.time())
    msg = f"{sub}:{iat}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return f"svc.{sub}.{iat}.{sig}"


@pytest.mark.asyncio
async def test_svc_token_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.setenv("CHEMCLAW2_SERVICE_SECRET", "super-secret")
    assert await get_current_user(f"Bearer {_make_svc('user-1', 'super-secret')}") == "user-1"


@pytest.mark.asyncio
async def test_svc_token_bad_sig(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.setenv("CHEMCLAW2_SERVICE_SECRET", "super-secret")
    # Token signed with wrong secret.
    with pytest.raises(HTTPException) as ei:
        await get_current_user(f"Bearer {_make_svc('user-1', 'WRONG-secret')}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_svc_token_replay_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service tokens older than 5 minutes are rejected — bounded replay window."""
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.setenv("CHEMCLAW2_SERVICE_SECRET", "super-secret")
    expired_iat = int(time.time()) - 600  # 10 min ago, past 5-min window
    with pytest.raises(HTTPException) as ei:
        await get_current_user(
            f"Bearer {_make_svc('user-1', 'super-secret', iat=expired_iat)}"
        )
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_svc_token_invalid_sub(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import get_current_user

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.setenv("CHEMCLAW2_SERVICE_SECRET", "super-secret")
    # sub contains a colon, which the regex disallows (would break wire format).
    bad = _make_svc("user:1", "super-secret")
    with pytest.raises(HTTPException) as ei:
        await get_current_user(f"Bearer {bad}")
    assert ei.value.status_code == 401


# ── Admin checks ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_fails_closed_before_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """If validate_auth_config() has not run yet, get_admin_user must
    return 503 — never silently allow."""
    from api import auth as auth_mod
    from api.auth import get_admin_user

    monkeypatch.setattr(auth_mod, "_admin_ids_loaded", False)
    monkeypatch.setattr(auth_mod, "_admin_user_ids", frozenset())
    monkeypatch.setenv("ALLOW_MOCK_AUTH", "1")
    with pytest.raises(HTTPException) as ei:
        await get_admin_user("Bearer mock:somebody")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_admin_denies_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from api import auth as auth_mod
    from api.auth import get_admin_user

    monkeypatch.setattr(auth_mod, "_admin_ids_loaded", True)
    monkeypatch.setattr(auth_mod, "_admin_user_ids", frozenset({"admin-1"}))
    monkeypatch.setenv("ALLOW_MOCK_AUTH", "1")
    with pytest.raises(HTTPException) as ei:
        await get_admin_user("Bearer mock:not-admin")
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_accepts_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from api import auth as auth_mod
    from api.auth import get_admin_user

    monkeypatch.setattr(auth_mod, "_admin_ids_loaded", True)
    monkeypatch.setattr(auth_mod, "_admin_user_ids", frozenset({"admin-1"}))
    monkeypatch.setenv("ALLOW_MOCK_AUTH", "1")
    assert await get_admin_user("Bearer mock:admin-1") == "admin-1"
