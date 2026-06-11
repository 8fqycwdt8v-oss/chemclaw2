"""Unit tests for the Microsoft Graph connector.

No live tenant and no `msal` install required: the token path injects a fake
`msal` module and the data paths monkeypatch the shared `_fetch_validated`
helper, returning canned Graph JSON. These mirror the recorded delta/content
shapes Graph returns so the connector can be exercised before an Entra app
registration exists.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import httpx
import pytest

from api.integrations.sharepoint import graph_client as gc

_REQ = httpx.Request("GET", "https://graph.microsoft.com/v1.0/x")


def _resp(**kwargs: Any) -> httpx.Response:
    """An httpx.Response with a bound request so raise_for_status() works."""
    return httpx.Response(request=_REQ, **kwargs)


def test_from_env_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "MSGRAPH_TENANT_ID",
        "MSGRAPH_CLIENT_ID",
        "MSGRAPH_CLIENT_SECRET",
        "MSGRAPH_DRIVE_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    assert gc.GraphConfig.from_env() is None


def test_from_env_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tid")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MSGRAPH_DRIVE_ID", "drive-1")
    config = gc.GraphConfig.from_env()
    assert config is not None
    assert config.tenant_id == "tid"
    assert config.drive_id == "drive-1"


def _install_fake_msal(
    monkeypatch: pytest.MonkeyPatch, token_result: dict[str, Any]
) -> None:
    class _FakeApp:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
            return token_result

    fake = types.ModuleType("msal")
    fake.ConfidentialClientApplication = _FakeApp  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msal", fake)


@pytest.mark.asyncio
async def test_acquire_token_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_msal(monkeypatch, {"access_token": "tok-123"})
    config = gc.GraphConfig(
        tenant_id="t", client_id="c", client_secret="s", drive_id="d"
    )
    assert await gc.acquire_token(config) == "tok-123"


@pytest.mark.asyncio
async def test_acquire_token_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_msal(
        monkeypatch,
        {"error": "invalid_client", "error_description": "bad secret"},
    )
    config = gc.GraphConfig(
        tenant_id="t", client_id="c", client_secret="s", drive_id="d"
    )
    with pytest.raises(gc.GraphError):
        await gc.acquire_token(config)


@pytest.mark.asyncio
async def test_delta_paginates_and_captures_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    next_url = "https://graph.microsoft.com/v1.0/drives/d/root/delta?$skiptoken=p2"
    delta_url = "https://graph.microsoft.com/v1.0/drives/d/root/delta?token=NEXT"
    pages = [
        _resp(
            status_code=200,
            json={
                "value": [{"id": "1", "name": "a.pdf", "file": {}}],
                "@odata.nextLink": next_url,
            },
        ),
        _resp(
            status_code=200,
            json={
                "value": [{"id": "2", "name": "b.pdf", "file": {}}],
                "@odata.deltaLink": delta_url,
            },
        ),
    ]

    async def fake_fetch(
        url: str,
        *,
        enforce_domain_allowlist: bool,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        calls.append((url, headers))
        return pages.pop(0)

    monkeypatch.setattr(gc, "_fetch_validated", fake_fetch)

    items, new_link = await gc.delta("tok-123", "d")

    assert [i["id"] for i in items] == ["1", "2"]
    assert new_link == delta_url
    # First call starts at /root/delta; second follows the nextLink.
    assert calls[0][0] == f"{gc.GRAPH_BASE}/drives/d/root/delta"
    assert calls[1][0] == next_url
    # Bearer token is forwarded on every page.
    assert calls[0][1] == {"Authorization": "Bearer tok-123"}


@pytest.mark.asyncio
async def test_delta_resumes_from_stored_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = "https://graph.microsoft.com/v1.0/drives/d/root/delta?token=PREV"
    new = "https://graph.microsoft.com/v1.0/drives/d/root/delta?token=NEW"
    calls: list[str] = []

    async def fake_fetch(url: str, **_: Any) -> httpx.Response:
        calls.append(url)
        return _resp(status_code=200, json={"value": [], "@odata.deltaLink": new})

    monkeypatch.setattr(gc, "_fetch_validated", fake_fetch)

    items, new_link = await gc.delta("tok", "d", delta_link=stored)
    assert items == []
    assert new_link == new
    assert calls == [stored]


@pytest.mark.asyncio
async def test_delta_page_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nextLink chain that never terminates trips the page cap instead of
    looping (and accumulating items) forever."""
    loop_url = "https://graph.microsoft.com/v1.0/drives/d/root/delta?$skiptoken=again"

    async def fake_fetch(url: str, **_: Any) -> httpx.Response:
        return _resp(status_code=200, json={"value": [], "@odata.nextLink": loop_url})

    monkeypatch.setattr(gc, "_fetch_validated", fake_fetch)
    monkeypatch.setattr(gc, "_MAX_DELTA_PAGES", 3)

    with pytest.raises(gc.GraphError, match="pagination exceeded"):
        await gc.delta("tok", "d")


@pytest.mark.asyncio
async def test_download_item_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, **_: Any) -> httpx.Response:
        assert url == f"{gc.GRAPH_BASE}/drives/d/items/item-9/content"
        return _resp(status_code=200, content=b"%PDF-1.7 bytes")

    monkeypatch.setattr(gc, "_fetch_validated", fake_fetch)
    data = await gc.download_item("tok", "d", "item-9")
    assert data == b"%PDF-1.7 bytes"


def test_select_changed_files_classifies() -> None:
    items = [
        {"id": "root", "root": {}, "name": "root"},          # synthetic root
        {"id": "f1", "folder": {}, "name": "Reports"},        # folder
        {"id": "d1", "name": "spec.pdf", "file": {}},         # file change
        {"id": "d2", "deleted": {"state": "deleted"}},        # removal
        {"id": "d3", "file": {}},                             # file w/o name → skip
    ]
    files, deleted = gc.select_changed_files(items)
    assert [f["id"] for f in files] == ["d1"]
    assert deleted == ["d2"]
