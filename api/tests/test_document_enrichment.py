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

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _fake_fetch)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is not None
    assert out["title"] == "Found"
    assert out["doi"] == "10.1234/x"


@pytest.mark.asyncio
async def test_fetch_crossref_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(404)

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _fake_fetch)
    out = await de.fetch_crossref_metadata("10.1234/missing")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_crossref_network_error_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(url: str, **kw: Any) -> Any:
        raise httpx.ConnectError("simulated network error")

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _raise)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_crossref_ssrf_blocked_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the SSRF guard rejects (e.g. DNS rebind hit a private IP), the
    enrichment helper should swallow the rejection and return None so
    the upload still succeeds — just without metadata."""
    from api.agent.tool_helpers import _SSRFError

    async def _ssrf(url: str, **kw: Any) -> Any:
        raise _SSRFError("simulated SSRF reject")

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _ssrf)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_crossref_invalid_json_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _fake_fetch)
    out = await de.fetch_crossref_metadata("10.1234/x")
    assert out is None


# ── resolve_compound_name_to_smiles ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_compound_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        assert "pubchem.ncbi.nlm.nih.gov" in url
        assert "aspirin" in url
        return httpx.Response(
            200,
            json={"PropertyTable": {"Properties": [{"CID": 2244, "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O"}]}},
        )

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _fake_fetch)
    out = await de.resolve_compound_name_to_smiles("aspirin")
    assert out == "CC(=O)OC1=CC=CC=C1C(=O)O"


@pytest.mark.asyncio
async def test_resolve_compound_handles_special_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PubChem requires URL-encoding for names containing slashes, spaces,
    parentheses, plus signs, etc. — IUPAC names are full of these."""
    seen_urls: list[str] = []

    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        seen_urls.append(url)
        return httpx.Response(404)

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _fake_fetch)
    await de.resolve_compound_name_to_smiles("(2S)-2-amino-3-methylbutanoic acid")

    assert seen_urls
    url = seen_urls[0]
    # Raw special chars must not appear in the path.
    assert "(2S)" not in url
    assert " " not in url


@pytest.mark.asyncio
async def test_resolve_compound_404_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(404)

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _fake_fetch)
    out = await de.resolve_compound_name_to_smiles("nonexistent-compound-xyz")
    assert out is None


@pytest.mark.asyncio
async def test_resolve_compound_empty_name_returns_none() -> None:
    out = await de.resolve_compound_name_to_smiles("")
    assert out is None


@pytest.mark.asyncio
async def test_resolve_compound_oversize_name_returns_none() -> None:
    out = await de.resolve_compound_name_to_smiles("x" * 500)
    assert out is None


@pytest.mark.asyncio
async def test_resolve_compound_empty_properties_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(200, json={"PropertyTable": {"Properties": []}})

    monkeypatch.setattr("api.agent.tool_helpers._fetch_validated", _fake_fetch)
    out = await de.resolve_compound_name_to_smiles("phantom")
    assert out is None


# ── extract_entities_from_text ───────────────────────────────────────────────


class _FakeBlock:
    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = payload


class _FakeResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def create(self, **kw: Any) -> _FakeResponse:
        return self._response


class _FakeAsyncAnthropic:
    def __init__(self, response: _FakeResponse) -> None:
        self.messages = _FakeMessages(response)


@pytest.mark.asyncio
async def test_extract_entities_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "compounds": [{"name": "aspirin", "context": "used in study"}],
        "citations": [{"identifier": "10.1234/abc", "context": "ref"}],
    }
    fake = _FakeAsyncAnthropic(_FakeResponse([_FakeBlock("extract_entities", payload)]))
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda: fake)

    out = await de.extract_entities_from_text("some chemistry document text...")
    assert out["compounds"] == [{"name": "aspirin", "context": "used in study"}]
    assert out["citations"] == [{"identifier": "10.1234/abc", "context": "ref"}]


@pytest.mark.asyncio
async def test_extract_entities_empty_text_short_circuits() -> None:
    out = await de.extract_entities_from_text("")
    assert out == {"compounds": [], "citations": []}


@pytest.mark.asyncio
async def test_extract_entities_returns_empty_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomMessages:
        async def create(self, **kw: Any) -> Any:
            raise RuntimeError("simulated Anthropic outage")

    class _BoomClient:
        messages = _BoomMessages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda: _BoomClient())
    out = await de.extract_entities_from_text("some text")
    assert out["compounds"] == []
    assert out["citations"] == []
    assert "error" in out


@pytest.mark.asyncio
async def test_extract_entities_handles_no_tool_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the model returns text instead of a tool call, return empty."""
    class _TextBlock:
        type = "text"
        text = "I refuse to use the tool."

    fake = _FakeAsyncAnthropic(_FakeResponse([_TextBlock()]))
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda: fake)
    out = await de.extract_entities_from_text("text")
    assert out["compounds"] == []
    assert out["citations"] == []
    assert out.get("error") == "no tool block"


@pytest.mark.asyncio
async def test_extract_entities_truncates_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long inputs are truncated to max_chars so token usage stays bounded."""
    received_content: list[str] = []

    class _CaptureMessages:
        async def create(self, **kw: Any) -> Any:
            received_content.append(kw["messages"][0]["content"])
            return _FakeResponse([_FakeBlock("extract_entities", {"compounds": [], "citations": []})])

    class _CaptureClient:
        messages = _CaptureMessages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda: _CaptureClient())
    big_text = "x" * 50_000
    await de.extract_entities_from_text(big_text, max_chars=1000)
    assert received_content
    assert len(received_content[0]) == 1000
