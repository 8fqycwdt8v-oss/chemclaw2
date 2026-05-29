"""Microsoft Entra ID (Azure AD) login for the Streamlit frontend via MSAL.

Implements the OAuth2 authorization-code flow (with PKCE) as a *confidential*
client: Streamlit runs server-side and holds a client secret. We deliberately
request the backend's own API scope —
``api://<backend_client_id>/access_as_user`` — so the resulting access token's
audience is the chemclaw2 backend. That token is forwarded verbatim as a
``Bearer`` header (a pass-through, not an On-Behalf-Of exchange).

Access is gated at the directory: the backend enterprise app has
"Assignment required = Yes" and the AD security group is assigned to an app
role, so Entra refuses to mint a token for a non-member. The backend
re-checks the app role in the ``roles`` claim as defence in depth.

The MSAL token cache is kept in ``st.session_state`` so ``acquire_token_silent``
can refresh the access token without bouncing the user back to the login page —
that's the "good session" requirement.
"""
from __future__ import annotations

from typing import Any

import msal
import streamlit as st

_CACHE_KEY = "_msal_token_cache"
_FLOW_KEY = "_msal_auth_flow"
_RESULT_KEY = "_msal_result"


def _settings() -> dict[str, str]:
    """Read Entra config from st.secrets (see .streamlit/secrets.toml.example)."""
    s = st.secrets["azure"]
    return {
        "tenant_id": s["tenant_id"],
        "client_id": s["frontend_client_id"],
        "client_secret": s["frontend_client_secret"],
        "backend_client_id": s["backend_client_id"],
        "redirect_uri": s["redirect_uri"],
    }


def _scopes() -> list[str]:
    """The single delegated scope exposed by the backend API."""
    return [f"api://{_settings()['backend_client_id']}/access_as_user"]


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    blob = st.session_state.get(_CACHE_KEY)
    if blob:
        cache.deserialize(blob)
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        st.session_state[_CACHE_KEY] = cache.serialize()


def _build_app(cache: msal.SerializableTokenCache) -> msal.ConfidentialClientApplication:
    cfg = _settings()
    return msal.ConfidentialClientApplication(
        client_id=cfg["client_id"],
        authority=f"https://login.microsoftonline.com/{cfg['tenant_id']}",
        client_credential=cfg["client_secret"],
        token_cache=cache,
    )


def login_url() -> str:
    """Start an auth-code flow and return the Entra authorization URL.

    The flow dict (state + PKCE verifier) is stashed in session_state so it
    survives the redirect back from Entra.
    """
    cache = _load_cache()
    app = _build_app(cache)
    flow = app.initiate_auth_code_flow(
        scopes=_scopes(),
        redirect_uri=_settings()["redirect_uri"],
    )
    st.session_state[_FLOW_KEY] = flow
    _save_cache(cache)
    return flow["auth_uri"]


def complete_login(auth_response: dict[str, str]) -> dict[str, Any] | None:
    """Exchange the redirect's ?code=… for tokens. Returns the MSAL result.

    Returns None (and surfaces the error in the UI) on failure, e.g. when the
    user is not assigned to the app (Entra denies the token).
    """
    flow = st.session_state.get(_FLOW_KEY)
    if not flow:
        return None
    cache = _load_cache()
    app = _build_app(cache)
    result = app.acquire_token_by_auth_code_flow(flow, auth_response)
    _save_cache(cache)
    st.session_state.pop(_FLOW_KEY, None)
    if "access_token" not in result:
        # error / error_description set by MSAL — show it, don't crash.
        st.error(f"Login failed: {result.get('error_description', result.get('error', 'unknown error'))}")
        return None
    st.session_state[_RESULT_KEY] = result
    return result


def get_token() -> str | None:
    """Return a valid backend access token, refreshing silently if possible.

    Returns None when the user is not logged in (caller should show login).
    """
    result = st.session_state.get(_RESULT_KEY)
    if not result:
        return None
    cache = _load_cache()
    app = _build_app(cache)
    accounts = app.get_accounts()
    if accounts:
        refreshed = app.acquire_token_silent(_scopes(), account=accounts[0])
        _save_cache(cache)
        if refreshed and "access_token" in refreshed:
            st.session_state[_RESULT_KEY] = refreshed
            return refreshed["access_token"]
    # No cached account or silent refresh failed — fall back to the last
    # result (may be near expiry; the user re-logs in if the backend 401s).
    return result.get("access_token")


def current_user() -> dict[str, Any]:
    """Identity claims from the id token (name, username, oid, roles)."""
    result = st.session_state.get(_RESULT_KEY) or {}
    return result.get("id_token_claims", {})


def logout() -> None:
    for key in (_RESULT_KEY, _CACHE_KEY, _FLOW_KEY):
        st.session_state.pop(key, None)
