"""Microsoft Graph connector for SharePoint/OneDrive drive sync (app-only).

Reaches `graph.microsoft.com` over public HTTPS, so it works from the cloud
container without a VPN — unlike a raw SMB share on a private network, which the
SSRF guard (`_assert_not_private`) would correctly refuse. Design notes:

* **Token** — MSAL's client-credentials flow against Microsoft's fixed login
  host (`login.microsoftonline.com`). MSAL is the off-the-shelf auth library
  (CLAUDE.md operating principles) and issues its own HTTP to that single
  trusted, non-user-controlled host. It's imported inside the function so the
  package imports cleanly even where `msal` isn't installed (same graceful
  pattern as `pypdf` in the document-upload route).
* **Data calls** — `delta()` and `download_item()` go through the shared
  SSRF-pinned `_fetch_validated` helper. `graph.microsoft.com` and
  `sharepoint.com` (the host the `/content` endpoint 302-redirects to) are on
  `ALLOWED_DOMAINS`; `_fetch_validated` re-validates every redirect hop.

Env (read at construction via `GraphConfig.from_env`, never at import —
CLAUDE.md env rule):
  MSGRAPH_TENANT_ID, MSGRAPH_CLIENT_ID, MSGRAPH_CLIENT_SECRET, MSGRAPH_DRIVE_ID

Not yet wired into a worker — a later slice mounts it. Built and tested against
mocked Graph responses until an Entra app registration exists.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from pydantic import BaseModel

from api.agent.tool_helpers import _fetch_validated

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# `.default` requests the app-only application permissions consented in Entra
# (Sites.Read.All / Files.Read.All) rather than per-resource delegated scopes.
_GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


class GraphError(RuntimeError):
    """Raised when a Microsoft Graph call cannot be completed."""


class GraphConfig(BaseModel):
    """App-only Microsoft Graph credentials for one drive."""

    tenant_id: str
    client_id: str
    client_secret: str
    drive_id: str

    @classmethod
    def from_env(cls) -> GraphConfig | None:
        """Build config from MSGRAPH_* env vars, or None if any are missing.

        Returns None (with a warning) rather than raising so the future sync
        worker can no-op cleanly when the integration isn't configured — the
        same fail-soft posture as the ELN webhook when its secret is unset.
        """
        try:
            return cls(
                tenant_id=os.environ["MSGRAPH_TENANT_ID"],
                client_id=os.environ["MSGRAPH_CLIENT_ID"],
                client_secret=os.environ["MSGRAPH_CLIENT_SECRET"],
                drive_id=os.environ["MSGRAPH_DRIVE_ID"],
            )
        except KeyError as e:
            logger.warning("msgraph_not_configured missing=%s", e.args[0])
            return None


async def acquire_token(config: GraphConfig) -> str:
    """Acquire an app-only Graph access token via the client-credentials flow.

    MSAL's `acquire_token_for_client` is synchronous and caches tokens
    internally; we run it in the default executor so the blocking call does not
    stall the event loop. Raises GraphError on failure — the Graph error
    detail is logged server-side, never surfaced to a client (CLAUDE.md
    security-4).
    """
    try:
        import msal  # optional dep — imported inside fn for graceful failure
    except ImportError as e:
        raise GraphError("msal not installed; Microsoft Graph unavailable") from e

    loop = asyncio.get_running_loop()

    def _acquire() -> dict[str, Any]:
        app = msal.ConfidentialClientApplication(
            client_id=config.client_id,
            authority=f"https://login.microsoftonline.com/{config.tenant_id}",
            client_credential=config.client_secret,
        )
        return app.acquire_token_for_client(scopes=_GRAPH_SCOPE)

    result = await loop.run_in_executor(None, _acquire)
    token = result.get("access_token")
    if not token:
        logger.error(
            "msgraph_token_failed error=%s desc=%s",
            result.get("error"),
            result.get("error_description"),
        )
        raise GraphError("Failed to acquire Microsoft Graph token")
    return token


async def delta(
    token: str,
    drive_id: str,
    *,
    delta_link: str | None = None,
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], str]:
    """Page through a drive's delta feed and return (items, new_delta_link).

    On the first sync pass `delta_link=None` to start at `/root/delta`. On
    subsequent syncs pass the stored deltaLink to receive only items changed
    since. Follows `@odata.nextLink` pages and captures the terminal
    `@odata.deltaLink` to persist as the next cursor. If a Graph page omits the
    deltaLink (only happens mid-pagination), the previous cursor is retained.
    """
    url = delta_link or f"{GRAPH_BASE}/drives/{drive_id}/root/delta"
    headers = {"Authorization": f"Bearer {token}"}
    items: list[dict[str, Any]] = []
    new_delta_link = delta_link or ""
    while url:
        resp = await _fetch_validated(
            url, enforce_domain_allowlist=True, headers=headers, timeout=timeout
        )
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get("value", []))
        captured = body.get("@odata.deltaLink")
        if captured:
            new_delta_link = captured
        url = body.get("@odata.nextLink") or ""
    return items, new_delta_link


async def download_item(
    token: str,
    drive_id: str,
    item_id: str,
    *,
    timeout: float = 60.0,
) -> bytes:
    """Download a drive item's content via the Graph `/content` endpoint.

    `/content` 302-redirects to a short-lived pre-authenticated download URL on
    `*.sharepoint.com`; `_fetch_validated` follows and re-validates that hop
    against `ALLOWED_DOMAINS`.
    """
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
    headers = {"Authorization": f"Bearer {token}"}
    resp = await _fetch_validated(
        url, enforce_domain_allowlist=True, headers=headers, timeout=timeout
    )
    resp.raise_for_status()
    return resp.content


def select_changed_files(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split a delta result into (file items to ingest, deleted item ids).

    The delta feed mixes folders, the synthetic drive-root entry, file changes,
    and deletions. We keep only items carrying a `file` facet (with a name) for
    ingestion, collect ids of items carrying a `deleted` facet for the caller's
    removal policy, and drop folders / the root entry.
    """
    files: list[dict[str, Any]] = []
    deleted: list[str] = []
    for item in items:
        if item.get("deleted") is not None:
            item_id = item.get("id")
            if item_id:
                deleted.append(item_id)
        elif "file" in item and item.get("name"):
            files.append(item)
    return files, deleted
