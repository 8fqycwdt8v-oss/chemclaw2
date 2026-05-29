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


def get(path: str, token: str, params: dict[str, Any] | None = None) -> Any:
    """Authenticated GET. Returns parsed JSON, or raises BackendError."""
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(f"{_base_url()}{path}", headers=headers, params=params)
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
