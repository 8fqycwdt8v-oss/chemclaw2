"""Shared helpers for the in-process MCP tool surface in `tools.py`.

Three concerns live here:

1. **SSRF guards** — `_resolve_to_global_ip`, `_fetch_validated`,
   `_redact_ssrf_error`, etc. These are the CLAUDE.md §security-5
   template for outbound HTTP and the §security-4 client-surface
   redaction policy. The agent's external-fetch tools (`fetch_document`,
   `lookup_regulatory_guidance`, `patent_coverage`, etc.) and the
   document-enrichment integration both depend on this module.

2. **HTML → text** — `_html_to_text` for parsing fetched documents.

3. **Paper-chunk ingest + heuristic proposal** — `_resolve_chunk_params`,
   `_ingest_paper_chunks`, `_heuristic_propose`. Pure utility wrappers
   over the queries layer; kept here to avoid duplicating them across
   the tool functions that need them.

`_SSRFError` is a `ValueError` subclass so callers can `except ValueError`
for legacy handling while we still throw the more specific type.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class _PredictedConditionsPayload(BaseModel):
    """Schema for the `record_predicted_conditions` tool's nested conditions arg."""
    catalysts: list[str] = Field(default_factory=list)
    solvents: list[str] = Field(default_factory=list)
    reagents: list[str] = Field(default_factory=list)
    temperature_c: float | None = None


# ── SSRF protection ───────────────────────────────────────────────────────────

async def _resolve_to_global_ip(hostname: str) -> str:
    """Resolve a hostname and return the first public IP, or raise ValueError.

    Single point of DNS resolution: callers should *use this IP* as the
    connection target (see `_fetch_validated`) rather than passing the
    hostname back through httpx, which would re-resolve at connect time
    and open a DNS-rebinding TOCTOU window. CLAUDE.md §security-5.

    Uses run_in_executor so the blocking getaddrinfo call does not stall
    the event loop. Fails closed on DNS error, unrecognised address
    format, private/loopback/link-local/multicast/etc.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
    except OSError as e:
        raise ValueError(f"DNS resolution failed for {hostname}: {e}") from e
    chosen: str | None = None
    for info in infos:
        # getaddrinfo's sockaddr is typed as (str | int, ...) because IPv6's
        # 4-tuple includes integer flowinfo/scope_id; the [0] slot is always
        # a hostname/IP string.
        ip_str = str(info[4][0])
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError(f"SSRF blocked: unrecognised address format {ip_str}") from None
        # is_global treats multicast (224.0.0.0/4, ff00::/8) as globally
        # routed, so check it separately — outbound HTTP must never target
        # a multicast destination.
        if not addr.is_global or addr.is_multicast:
            raise ValueError(f"SSRF blocked: {hostname} resolves to a non-public address ({ip_str})")
        # Prefer IPv4 (first match) — more libraries handle it cleanly,
        # and the SNI extension path is identical for v4/v6.
        if chosen is None or (":" in chosen and ":" not in ip_str):
            chosen = ip_str
    if chosen is None:
        raise ValueError(f"DNS returned no records for {hostname}")
    return chosen


# ── Allowed domains ───────────────────────────────────────────────────────────

ALLOWED_DOMAINS = [
    'pubchem.ncbi.nlm.nih.gov',
    'pubmed.ncbi.nlm.nih.gov',
    'doi.org',
    'crossref.org',
    'chemrxiv.org',
    'rsc.org',
    'acs.org',
    'nature.com',
    'sciencedirect.com',
    'elsevier.com',
    'ich.org',
    'cactus.nci.nih.gov',  # NCI CACTUS — name↔SMILES↔CAS resolver
]


def _is_allowed_domain(hostname: str) -> bool:
    h = hostname.lower()
    return any(h == d or h.endswith('.' + d) for d in ALLOWED_DOMAINS)


# ── Validated HTTP fetch with IP pinning ──────────────────────────────────────


def _pin_url_to_ip(url: str, ip: str) -> str:
    """Replace the hostname in `url` with `ip`, preserving scheme/port/path/query.

    IPv6 addresses are wrapped in brackets so the resulting URL is valid.
    The caller still passes the original hostname via SNI + Host header
    (see `_fetch_validated`), so TLS verification continues to work.
    """
    parsed = urlparse(url)
    bracketed = f"[{ip}]" if ":" in ip else ip
    netloc = f"{bracketed}:{parsed.port}" if parsed.port is not None else bracketed
    if parsed.username:
        creds = parsed.username + (f":{parsed.password}" if parsed.password else "")
        netloc = f"{creds}@{netloc}"
    return parsed._replace(netloc=netloc).geturl()


class _SSRFError(ValueError):
    """Raised by `_fetch_validated` when an SSRF guard rejects the request."""


def _redact_ssrf_error(tool: str, exc: _SSRFError, **extra: Any) -> dict[str, Any]:
    """Log an SSRF rejection server-side and return a generic error to the agent.

    `_SSRFError` messages embed resolved IPs and DNS detail (e.g.
    "blocked: example.com resolves to a non-public address (10.0.0.1)"),
    useful for ops debugging but not safe to surface across the MCP/agent
    client boundary (CLAUDE.md §security-4, OWASP A05). Caller-supplied
    identity keys (`guideline`, `cid`, etc.) are preserved so the agent
    can correlate the failure with its request.
    """
    logger.warning("tool_ssrf_blocked tool=%s: %s", tool, exc)
    return {"error": "URL rejected by SSRF guard", **extra}


async def _fetch_validated(
    url: str,
    *,
    enforce_domain_allowlist: bool,
    max_redirects: int = 5,
    timeout: float = 15.0,
    headers: dict[str, str] | None = None,
    user_agent: str = "chemclaw2/1.0 (research assistant)",
) -> httpx.Response:
    """Fetch `url` with SSRF guards applied at every hop.

    For each URL in the redirect chain:
      1. Validate the hostname against `ALLOWED_DOMAINS` if requested.
      2. Resolve DNS exactly once and reject non-global / multicast IPs.
      3. Rewrite the URL to point at the resolved IP, pass the original
         hostname via SNI + Host header so TLS cert verification still
         matches the certificate's SAN/CN.
      4. Send the request without httpx-level redirect following — this
         function handles redirects explicitly so each new hop is
         re-validated.

    Raises `_SSRFError` (a ValueError subclass) for any guard failure;
    other exceptions (timeout, connection reset, etc.) propagate as
    `httpx.HTTPError` for the caller to handle.
    """
    current = url
    base_headers = {"User-Agent": user_agent, **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            parsed = urlparse(current)
            hostname = (parsed.hostname or "").lower()
            if not hostname:
                raise _SSRFError(f"Invalid URL: missing hostname in {current[:100]}")
            if enforce_domain_allowlist and not _is_allowed_domain(hostname):
                raise _SSRFError(f"Domain '{hostname}' is not in the allowed list")
            try:
                ip = await _resolve_to_global_ip(hostname)
            except ValueError as e:
                # Re-raise as _SSRFError so callers' single `except _SSRFError`
                # branch covers both allowlist + DNS rejection paths.
                raise _SSRFError(str(e)) from e
            pinned = _pin_url_to_ip(current, ip)
            # `Host` header preserves the hostname for HTTP routing on the
            # peer; `sni_hostname` makes httpcore use the hostname for both
            # TLS SNI and certificate verification (server_hostname).
            req_headers = {**base_headers, "Host": hostname}
            response = await client.get(
                pinned,
                headers=req_headers,
                extensions={"sni_hostname": hostname},
            )
            if not response.is_redirect:
                return response
            location = response.headers.get("location", "").strip()
            if not location:
                raise _SSRFError("Redirect response with no Location header")
            # Resolve relative redirects against the *original* hostname
            # (not the pinned IP) so the next hop's allowlist check runs
            # against the real authority.
            current = str(httpx.URL(f"{parsed.scheme}://{hostname}").join(location))
        raise _SSRFError("Too many redirects")


# ── HTML → text ───────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    """Very lightweight HTML → text. Strips tags, decodes common entities."""
    import html as html_lib
    text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── paper-chunk ingest ────────────────────────────────────────────────────────

def _resolve_chunk_params() -> tuple[int, int]:
    """Read PAPER_CHUNK_SIZE / PAPER_CHUNK_OVERLAP env vars with defaults
    (1500 / 200) and validate. Misconfigured values log a warning and
    fall back to defaults — never silently land 50-char chunks."""
    default_size, default_overlap = 1500, 200
    try:
        size = int(os.environ.get("PAPER_CHUNK_SIZE", str(default_size)))
        overlap = int(os.environ.get("PAPER_CHUNK_OVERLAP", str(default_overlap)))
    except ValueError:
        logger.warning("invalid PAPER_CHUNK_* env vars (non-numeric); using defaults")
        return default_size, default_overlap
    if not (200 <= size <= 5000) or not (0 <= overlap < size):
        logger.warning(
            "invalid PAPER_CHUNK_* env vars (size=%d overlap=%d); using defaults",
            size, overlap,
        )
        return default_size, default_overlap
    return size, overlap


async def _ingest_paper_chunks(
    paper_id: str,
    content_text: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Chunk + embed + persist a paper body. Returns the count written.

    Chunk size + overlap come from PAPER_CHUNK_SIZE / PAPER_CHUNK_OVERLAP
    env vars (defaults 1500 / 200, validated by `_resolve_chunk_params`).
    Embedding failure is non-fatal: chunks land with embedding=NULL so FTS
    retrieval still works; semantic retrieval will simply skip them.
    """
    from api.db.queries.paper_chunks import chunk_paper_text, insert_paper_chunks
    chunk_size, overlap = _resolve_chunk_params()
    parts = chunk_paper_text(content_text, chunk_size=chunk_size, overlap=overlap)
    if not parts:
        return 0
    # Embed in one batch — text-embedding-3-small is happy with arrays.
    embeddings: list[list[float] | None]
    try:
        from api.embeddings import embed_texts
        raw = await embed_texts([p[2] for p in parts])
        embeddings = list(raw)
    except Exception:
        logger.exception("paper_chunk_embed_failed paper_id=%s", paper_id)
        embeddings = [None] * len(parts)
    chunks = [
        {
            "chunk_idx": idx,
            "section": section,
            "page": None,
            "text": txt,
            "embedding": emb,
        }
        for (idx, section, txt), emb in zip(parts, embeddings, strict=True)
    ]
    # insert_paper_chunks manages its own transaction; we just provide a session.
    async with session_factory() as db:
        return await insert_paper_chunks(db, paper_id, chunks)


# ── propose_next_conditions: stage-0 heuristic helper ─────────────────────────

async def _heuristic_propose(
    session_factory: async_sessionmaker[AsyncSession],
    campaign_id: str,
    n_proposals: int,
) -> dict[str, Any]:
    """V1 condition-proposer logic. Called when no parameter_spec exists
    or when BOFIRE isn't installed. Same shape as before this PR: rank
    completed steps by yield, return best + temperature tweak + solvent
    swap."""
    from api.db.queries.campaign_steps import list_campaign_steps

    async with session_factory() as db:
        steps = await list_campaign_steps(db, campaign_id)

    completed = [s for s in steps if (s.get("status") == "complete"
                                      and s.get("result") is not None)]
    if not completed:
        return {
            "campaign_id": campaign_id,
            "best_so_far": None,
            "proposals": [],
            "strategy": (
                "no completed steps yet — record at least one outcome "
                "before proposing"
            ),
        }

    def _yield(step: dict[str, Any]) -> float:
        res = step.get("result") or {}
        if not isinstance(res, dict):
            return float("-inf")
        for key in ("yield", "yield_percent", "yield_pct"):
            v = res.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        return float("-inf")

    completed.sort(key=_yield, reverse=True)
    best = completed[0]
    best_conditions = best.get("conditions") or {}
    best_yield = _yield(best)

    proposals: list[dict[str, Any]] = [{
        "conditions": dict(best_conditions) if isinstance(best_conditions, dict) else {},
        "rationale": (
            f"Exploit — reproduce best-seen ({best_yield:.1f}% yield) "
            "before perturbing."
        ),
    }]
    if isinstance(best_conditions, dict) and "temperature" in best_conditions:
        try:
            t = float(best_conditions["temperature"])
            proposals.append({
                "conditions": {**best_conditions, "temperature": round(t + 10.0, 1)},
                "rationale": "Exploit/tweak — best conditions with temperature +10°C.",
            })
        except (TypeError, ValueError):
            logger.debug(
                "temperature_parse_failed campaign=%s value=%r",
                campaign_id, best_conditions.get("temperature"),
            )
    seen_solvents = {
        s.get("conditions", {}).get("solvent")
        for s in completed
        if isinstance(s.get("conditions"), dict) and s["conditions"].get("solvent")
    }
    seen_solvents.discard(None)
    if isinstance(best_conditions, dict) and best_conditions.get("solvent"):
        for solvent in sorted(seen_solvents):
            if solvent != best_conditions["solvent"]:
                proposals.append({
                    "conditions": {**best_conditions, "solvent": solvent},
                    "rationale": f"Explore — swap solvent to {solvent}.",
                })
                break

    return {
        "campaign_id": campaign_id,
        "best_so_far": {"conditions": best_conditions, "yield": best_yield},
        "proposals": proposals[: max(1, n_proposals)],
        "strategy": "heuristic-v1",
    }
