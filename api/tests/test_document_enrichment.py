"""Tests for api/integrations/document_enrichment.py.

Pure-unit — no DB, no network. CrossRef fetches are mocked via the
shared `_fetch_validated` SSRF helper.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from api.integrations import document_enrichment as de


# ── extract_doi ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("see 10.1234/abcd.5678", "10.1234/abcd.5678"),
        ("10.1093/nar/gkae123", "10.1093/nar/gkae123"),
        # Embedded in prose with trailing period (PDF artifact).
        ("Reference: 10.1038/nature12373.", "10.1038/nature12373"),
        # Inside parens.
        ("(see 10.1021/jacs.0c00001)", "10.1021/jacs.0c00001"),
        # Wrapped in angle brackets (PDF metadata convention).
        ("<10.1021/jacs.0c00001>", "10.1021/jacs.0c00001"),
        # No DOI present.
        ("This is just regular prose.", None),
        ("", None),
        # Looks DOI-ish but registrant code too short (must be 4-9 digits).
        ("10.12/short.suffix", None),
        # Empty suffix after the /.
        ("10.1234/", None),
    ],
)
def test_extract_doi(text: str, expected: str | None) -> None:
    assert de.extract_doi(text) == expected


def test_extract_doi_takes_first_match() -> None:
    text = "First 10.1111/aaa.bbb then 10.2222/ccc.ddd"
    assert de.extract_doi(text) == "10.1111/aaa.bbb"


# ── slugify_doi ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "doi,expected",
    [
        ("10.1234/abcd.5678", "10-1234-abcd-5678"),
        ("10.1038/Nature12373", "10-1038-nature12373"),
        ("10.1021/jacs.0c00001", "10-1021-jacs-0c00001"),
        # Multiple consecutive separators collapse to one hyphen.
        ("10.1234//xxx", "10-1234-xxx"),
        # Leading / trailing separators trimmed.
        (".10.1234/xyz.", "10-1234-xyz"),
    ],
)
def test_slugify_doi(doi: str, expected: str) -> None:
    assert de.slugify_doi(doi) == expected


# ── first_nonempty_line ──────────────────────────────────────────────────────


def test_first_nonempty_line_skips_blanks() -> None:
    assert de.first_nonempty_line("\n\n  \nReal title\nbody") == "Real title"


def test_first_nonempty_line_caps_at_max() -> None:
    out = de.first_nonempty_line("x" * 500, max_chars=200)
    assert out is not None and len(out) == 200


def test_first_nonempty_line_empty_input() -> None:
    assert de.first_nonempty_line("") is None
    assert de.first_nonempty_line("   \n  \n") is None


# ── normalize_crossref_response ──────────────────────────────────────────────


def test_normalize_crossref_full_record() -> None:
    body = {
        "status": "ok",
        "message": {
            "DOI": "10.1234/x",
            "title": ["A Study of Things"],
            "abstract": "<jats:p>An <jats:i>important</jats:i> finding.</jats:p>",
            "author": [
                {"given": "Alice", "family": "Smith"},
                {"given": "Bob", "family": "Jones"},
            ],
            "container-title": ["Journal of Important Things"],
            "issued": {"date-parts": [[2023, 1, 15]]},
        },
    }
    out = de.normalize_crossref_response(body)
    assert out["title"] == "A Study of Things"
    assert out["abstract"] == "An important finding."
    assert out["authors"] == ["Alice Smith", "Bob Jones"]
    assert out["container_title"] == "Journal of Important Things"
    assert out["published_year"] == 2023
    assert out["doi"] == "10.1234/x"


def test_normalize_crossref_missing_fields() -> None:
    """Missing fields shouldn't crash — every output key is optional."""
    out = de.normalize_crossref_response({"message": {}})
    assert out["title"] is None
    assert out["abstract"] is None
    assert out["authors"] == []
    assert out["container_title"] is None
    assert out["published_year"] is None
    assert out["doi"] is None


def test_normalize_crossref_handles_partial_author() -> None:
    body = {
        "message": {
            "author": [
                {"given": "Alice"},  # no family
                {"family": "Jones"},  # no given
                "not-a-dict",  # skipped
            ],
        },
    }
    out = de.normalize_crossref_response(body)
    assert out["authors"] == ["Alice", "Jones"]


def test_normalize_crossref_empty_message() -> None:
    """The CrossRef response shape varies; missing `message` shouldn't crash."""
    out = de.normalize_crossref_response({})
    assert out["title"] is None


# ── fetch_crossref_metadata ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_crossref_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        assert "api.crossref.org/works/10.1234/x" in url
        return httpx.Response(
            200,
            json={"message": {"DOI": "10.1234/x", "title": ["Found"]}},
        )

    monkeypatch.setattr("api.agent.tools._fetch_validated", _fake_fetch)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is not None
    assert out["title"] == "Found"
    assert out["doi"] == "10.1234/x"


@pytest.mark.asyncio
async def test_fetch_crossref_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(404)

    monkeypatch.setattr("api.agent.tools._fetch_validated", _fake_fetch)
    out = await de.fetch_crossref_metadata("10.1234/missing")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_crossref_network_error_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(url: str, **kw: Any) -> Any:
        raise httpx.ConnectError("simulated network error")

    monkeypatch.setattr("api.agent.tools._fetch_validated", _raise)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_crossref_ssrf_blocked_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the SSRF guard rejects (e.g. DNS rebind hit a private IP), the
    enrichment helper should swallow the rejection and return None so
    the upload still succeeds — just without metadata."""
    from api.agent.tools import _SSRFError

    async def _ssrf(url: str, **kw: Any) -> Any:
        raise _SSRFError("simulated SSRF reject")

    monkeypatch.setattr("api.agent.tools._fetch_validated", _ssrf)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_crossref_invalid_json_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    monkeypatch.setattr("api.agent.tools._fetch_validated", _fake_fetch)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is None
