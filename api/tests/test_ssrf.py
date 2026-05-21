"""Tests for the SSRF guards in `api.agent.tools`.

`_assert_not_private` / `_resolve_to_global_ip` is the CLAUDE.md template
for outbound HTTP — must fail closed on DNS error and reject any record
that is not globally routable. `_is_allowed_domain` enforces the agent's
allowlist. `_fetch_validated` is the shared helper that pins the resolved
IP for the actual connection, closing the DNS-rebinding TOCTOU window.

These tests mock `socket.getaddrinfo` so they run without network and
exercise the full address-classification logic on synthetic A/AAAA records.
"""
from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from api.agent.tools import (
    _assert_not_private,
    _fetch_validated,
    _is_allowed_domain,
    _pin_url_to_ip,
    _redact_ssrf_error,
    _resolve_to_global_ip,
    _SSRFError,
)


def _make_addrinfo(*ips: str) -> list[tuple[Any, ...]]:
    """Build a getaddrinfo-shaped result list with the given IP strings."""
    out: list[tuple[Any, ...]] = []
    for ip in ips:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        # (family, type, proto, canonname, sockaddr); we only read sockaddr[0]
        out.append((family, socket.SOCK_STREAM, 0, "", (ip, 0)))
    return out


# ── _is_allowed_domain ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host,expected",
    [
        ("nature.com", True),
        ("www.nature.com", True),
        ("a.b.nature.com", True),
        ("NATURE.COM", True),  # case-insensitive
        ("pubchem.ncbi.nlm.nih.gov", True),
        # Suffix tricks: must not pass
        ("nature.com.attacker.com", False),
        ("evil-nature.com", False),
        ("naturecom", False),
        # Unrelated host
        ("example.com", False),
        ("", False),
    ],
)
def test_is_allowed_domain(host: str, expected: bool) -> None:
    assert _is_allowed_domain(host) is expected


# ── _assert_not_private ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dns_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: Any, **kw: Any) -> Any:
        raise OSError("simulated DNS failure")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    with pytest.raises(ValueError, match="DNS resolution failed"):
        await _assert_not_private("example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ip,label",
    [
        ("127.0.0.1", "ipv4-loopback"),
        ("10.0.0.1", "ipv4-rfc1918-10"),
        ("172.16.0.1", "ipv4-rfc1918-172"),
        ("192.168.1.1", "ipv4-rfc1918-192"),
        ("169.254.169.254", "ipv4-link-local"),
        ("0.0.0.0", "ipv4-unspecified"),
        ("100.64.0.1", "ipv4-cgnat"),
        ("224.0.0.1", "ipv4-multicast"),
        ("::1", "ipv6-loopback"),
        ("fe80::1", "ipv6-link-local"),
        ("fc00::1", "ipv6-ula"),
    ],
)
async def test_private_ip_rejected(monkeypatch: pytest.MonkeyPatch, ip: str, label: str) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo(ip))
    with pytest.raises(ValueError, match="SSRF blocked"):
        await _assert_not_private("evil.example.com")


@pytest.mark.asyncio
async def test_public_ip_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("8.8.8.8"))
    # No raise = pass.
    await _assert_not_private("dns.google")


@pytest.mark.asyncio
async def test_mixed_records_rejected_if_any_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a hostname has even one private record, reject. A DNS-rebinding
    attacker who controls the authoritative server can answer with one
    public IP and one private IP — both must be checked."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **kw: _make_addrinfo("8.8.8.8", "127.0.0.1"),
    )
    with pytest.raises(ValueError, match="SSRF blocked"):
        await _assert_not_private("split-horizon.example.com")


@pytest.mark.asyncio
async def test_unrecognised_address_format_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """If getaddrinfo returns something that doesn't parse as an IP, we
    must fail closed rather than skipping the check."""
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("not-an-ip", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: fake)
    with pytest.raises(ValueError, match="unrecognised address format"):
        await _assert_not_private("bogus.example.com")


# ── _resolve_to_global_ip returns an IP ──────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_returns_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("8.8.8.8"))
    assert await _resolve_to_global_ip("dns.google") == "8.8.8.8"


@pytest.mark.asyncio
async def test_resolve_prefers_ipv4_when_both_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """When DNS returns both IPv4 and IPv6, IPv4 is preferred — the SNI
    extension path is identical for both but IPv4 has the widest library
    support."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **kw: _make_addrinfo("2606:4700::1111", "1.1.1.1"),
    )
    assert await _resolve_to_global_ip("dns.cloudflare.com") == "1.1.1.1"


# ── _pin_url_to_ip ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,ip,expected",
    [
        ("https://nature.com/path?q=1", "8.8.8.8", "https://8.8.8.8/path?q=1"),
        ("http://nature.com:8080/x", "8.8.8.8", "http://8.8.8.8:8080/x"),
        ("https://nature.com/x", "2606:4700::1", "https://[2606:4700::1]/x"),
        ("https://nature.com:443/x", "2606:4700::1", "https://[2606:4700::1]:443/x"),
    ],
)
def test_pin_url_to_ip(url: str, ip: str, expected: str) -> None:
    assert _pin_url_to_ip(url, ip) == expected


# ── _fetch_validated end-to-end with mocked HTTP ─────────────────────────────


class _StubAsyncClient:
    """Stand-in for httpx.AsyncClient that records what was requested.

    `_fetch_validated` opens one client and calls .get() one or more
    times (one per redirect hop). The stub captures every call and
    returns a queued response.
    """

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_StubAsyncClient":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str], extensions: dict[str, Any]) -> httpx.Response:
        self.calls.append({"url": url, "headers": dict(headers), "extensions": dict(extensions)})
        return self._responses.pop(0)


def _resp(status: int, *, headers: dict[str, str] | None = None, content: bytes = b"") -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, content=content)


@pytest.mark.asyncio
async def test_fetch_validated_pins_resolved_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request URL passed to httpx must contain the resolved IP, and
    the Host header + sni_hostname extension must carry the original
    hostname. This locks in the DNS-rebinding mitigation."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("1.1.1.1"))
    stub = _StubAsyncClient([_resp(200)])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    await _fetch_validated("https://www.nature.com/articles/x", enforce_domain_allowlist=True)

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["url"].startswith("https://1.1.1.1/")
    assert "www.nature.com" not in call["url"]
    assert call["headers"]["Host"] == "www.nature.com"
    assert call["extensions"]["sni_hostname"] == "www.nature.com"


@pytest.mark.asyncio
async def test_fetch_validated_revalidates_redirect_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each redirect hop must hit the SSRF guard again. Inject a redirect
    that points at a blocked domain and assert the helper raises."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("1.1.1.1"))
    stub = _StubAsyncClient([
        _resp(302, headers={"location": "https://attacker.example.com/"}),
    ])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    with pytest.raises(_SSRFError, match="not in the allowed list"):
        await _fetch_validated("https://www.nature.com/x", enforce_domain_allowlist=True)


@pytest.mark.asyncio
async def test_fetch_validated_follows_safe_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect within the allowlist must succeed and the second hop's
    request must also use IP pinning."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("1.1.1.1"))
    stub = _StubAsyncClient([
        _resp(301, headers={"location": "https://www.nature.com/landing"}),
        _resp(200, content=b"ok"),
    ])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    r = await _fetch_validated("https://nature.com/x", enforce_domain_allowlist=True)

    assert r.status_code == 200
    assert len(stub.calls) == 2
    # Both calls must have hit the pinned IP.
    assert all(c["url"].startswith("https://1.1.1.1/") for c in stub.calls)
    # Host header is rewritten per hop, not carried over.
    assert stub.calls[0]["headers"]["Host"] == "nature.com"
    assert stub.calls[1]["headers"]["Host"] == "www.nature.com"


@pytest.mark.asyncio
async def test_fetch_validated_rejects_private_ip_at_second_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the first hop resolves public but a redirect target resolves
    private, the second resolution must reject. This catches a
    DNS-rebinding attempt by an allowlisted authority."""
    resolutions = iter([
        _make_addrinfo("1.1.1.1"),  # nature.com → public
        _make_addrinfo("169.254.169.254"),  # www.nature.com → AWS metadata
    ])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: next(resolutions))
    stub = _StubAsyncClient([
        _resp(301, headers={"location": "https://www.nature.com/x"}),
    ])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    with pytest.raises(_SSRFError, match="non-public address"):
        await _fetch_validated("https://nature.com/x", enforce_domain_allowlist=True)


@pytest.mark.asyncio
async def test_fetch_validated_too_many_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("1.1.1.1"))
    stub = _StubAsyncClient([
        _resp(302, headers={"location": "https://nature.com/a"}),
        _resp(302, headers={"location": "https://nature.com/b"}),
        _resp(302, headers={"location": "https://nature.com/c"}),
    ])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    with pytest.raises(_SSRFError, match="Too many redirects"):
        await _fetch_validated(
            "https://nature.com/start",
            enforce_domain_allowlist=True,
            max_redirects=2,
        )


@pytest.mark.asyncio
async def test_fetch_validated_redirect_without_location_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("1.1.1.1"))
    stub = _StubAsyncClient([_resp(302)])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    with pytest.raises(_SSRFError, match="no Location header"):
        await _fetch_validated("https://nature.com/x", enforce_domain_allowlist=True)


@pytest.mark.asyncio
async def test_fetch_validated_relative_redirect_resolves_against_original_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `Location: /other` redirect must be resolved against the
    *hostname*, never against the pinned IP. Otherwise a follow-up
    redirect could be evaluated against an IP that bypasses the
    allowlist check."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("1.1.1.1"))
    stub = _StubAsyncClient([
        _resp(302, headers={"location": "/articles/2"}),
        _resp(200, content=b"ok"),
    ])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    r = await _fetch_validated("https://nature.com/articles/1", enforce_domain_allowlist=True)

    assert r.status_code == 200
    assert len(stub.calls) == 2
    # The second call still targets the pinned IP and still has the
    # nature.com Host header — the redirect was resolved against
    # nature.com, not against 1.1.1.1.
    assert stub.calls[1]["url"].startswith("https://1.1.1.1/")
    assert stub.calls[1]["headers"]["Host"] == "nature.com"


@pytest.mark.asyncio
async def test_fetch_validated_skips_allowlist_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For internal allowlists (e.g. ELN_API_BASE_URL configured by an
    admin), the helper should still pin the IP but skip the
    public-domain allowlist."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _make_addrinfo("1.1.1.1"))
    stub = _StubAsyncClient([_resp(200)])
    monkeypatch.setattr("api.agent.tools.httpx.AsyncClient", lambda **kw: stub)

    r = await _fetch_validated(
        "https://eln.internal-customer.example/api/x",
        enforce_domain_allowlist=False,
    )

    assert r.status_code == 200
    assert stub.calls[0]["url"].startswith("https://1.1.1.1/")
    # SSRF DNS check still ran — would have failed if IP were private.


# ── _redact_ssrf_error ───────────────────────────────────────────────────────


def test_redact_ssrf_error_strips_internal_detail_from_client_surface(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`_SSRFError` messages embed resolved IPs (`"... resolves to (10.0.0.1)"`).
    The client-surface response must not echo that text — CLAUDE.md §security-4
    (OWASP A05). The full message must still reach server logs for debugging.
    """
    exc = _SSRFError(
        "SSRF blocked: internal.example.com resolves to a non-public "
        "address (10.0.0.1)"
    )

    import logging
    with caplog.at_level(logging.WARNING, logger="api.agent.tools"):
        result = _redact_ssrf_error("fetch_document", exc)

    assert result == {"error": "URL rejected by SSRF guard"}
    assert "10.0.0.1" not in str(result)
    assert "internal.example.com" not in str(result)
    # Real detail logged server-side, with the tool name for correlation.
    assert "10.0.0.1" in caplog.text
    assert "tool=fetch_document" in caplog.text


def test_redact_ssrf_error_preserves_caller_identity_keys() -> None:
    """Callers pass identity context (`guideline`, `cid`, ...) so the agent
    can correlate the failure with its request — those must round-trip."""
    result = _redact_ssrf_error(
        "regulatory_fetch", _SSRFError("x"), guideline="ich-q3a",
    )
    assert result == {"error": "URL rejected by SSRF guard", "guideline": "ich-q3a"}

    result2 = _redact_ssrf_error(
        "pubchem_patent_lookup", _SSRFError("x"), cid=12345,
    )
    assert result2 == {"error": "URL rejected by SSRF guard", "cid": 12345}
