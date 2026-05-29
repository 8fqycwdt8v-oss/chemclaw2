"""Thin httpx wrapper that forwards the Entra access token to the backend.

Every call attaches ``Authorization: Bearer <token>`` — the token the
frontend obtained from Entra for the backend's audience. The backend
validates it and applies the AD-group gate; this client just surfaces the
status so the UI can distinguish 401 (re-login) from 403 (not in the group).
"""
from __future__ import annotations

from typing import Any

import httpx
import streamlit as st

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _base_url() -> str:
    return str(st.secrets["backend"]["api_url"]).rstrip("/")


class BackendError(Exception):
    """Raised for non-2xx backend responses, carrying the status code."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


def _build_url(path: str) -> str:
    """Join a server-relative path onto the backend base, refusing anything
    that could reparent the request to another host.

    Concatenating an arbitrary string onto the base is a token-exfiltration
    footgun: a path like ``@evil.com/x`` makes httpx parse ``evil.com`` as the
    host and ship the user's Bearer token there. Require an absolute,
    non-protocol-relative path and verify the resolved host is unchanged.
    """
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("path must be server-relative (start with a single '/')")
    base = _base_url()
    url = httpx.URL(base + path)
    if url.host != httpx.URL(base).host:
        raise ValueError("path must not change the request host")
    return str(url)


def get(path: str, token: str, params: dict[str, Any] | None = None) -> Any:
    """Authenticated GET. Returns parsed JSON, or raises BackendError."""
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(_build_url(path), headers=headers, params=params)
    if resp.status_code >= 400:
        raise BackendError(resp.status_code, _safe_detail(resp))
    return resp.json()


def health() -> dict[str, Any]:
    """Unauthenticated health probe — confirms the backend is reachable."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(f"{_base_url()}/api/health")
    resp.raise_for_status()
    return resp.json()


def _safe_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("detail", body))
    except ValueError:
        return resp.text[:200]
