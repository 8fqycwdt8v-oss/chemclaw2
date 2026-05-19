"""Tests for the SSRF guards in `api.agent.tools`.

`_assert_not_private` is the CLAUDE.md template for outbound HTTP — it
must fail closed on DNS error and reject any record that is not globally
routable. `_is_allowed_domain` enforces the agent's allowlist.

These tests mock `socket.getaddrinfo` so they run without network and
exercise the full address-classification logic on synthetic A/AAAA records.
"""
from __future__ import annotations

import socket
from typing import Any

import pytest

from api.agent.tools import _assert_not_private, _is_allowed_domain


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
