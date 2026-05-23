"""SSRF allowlist audit.

Prints every entry in `api.agent.tool_helpers.ALLOWED_DOMAINS` along with
the live DNS resolution. Operators run this quarterly to confirm:

  * Every entry still resolves to a globally-routable address. A typo or
    a domain that has gone NXDOMAIN is dead weight — drop it.
  * No entry has silently flipped to a private/loopback range (rare but
    has happened with cloud-provider DNS rebinds).
  * The list still reflects the agent tools' actual outbound paths. An
    entry that no `_fetch_validated` call site uses is a permission
    surface with no purpose.

`python -m tools.audit_allowlist` exits 0 on a clean audit. Pass
`--quiet` to suppress the per-entry table; the exit code reflects only
the resolved-to-private failure mode (the failure CLAUDE.md §security
cares about).
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
import sys

from api.agent.tool_helpers import ALLOWED_DOMAINS


def _resolve(hostname: str) -> tuple[list[str], str | None]:
    """Return (ip_strings, error). Empty list + error string on failure."""
    try:
        addrs = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return [], str(e)
    seen: set[str] = set()
    for info in addrs:
        seen.add(str(info[4][0]))
    return sorted(seen), None


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the SSRF allowlist")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    failures = 0
    for entry in sorted(ALLOWED_DOMAINS):
        ips, err = _resolve(entry)
        if err:
            print(f"WARN  {entry:40s}  unresolved: {err}", file=sys.stderr)
            # Unresolved entries are not a security failure (the SSRF
            # guard re-resolves on every fetch). They are operational
            # rot — flag but don't fail the audit.
            continue
        private = [ip for ip in ips if _is_private(ip)]
        if private:
            failures += 1
            print(
                f"FAIL  {entry:40s}  resolves to PRIVATE address(es): {', '.join(private)}",
                file=sys.stderr,
            )
            continue
        if not args.quiet:
            print(f"ok    {entry:40s}  {', '.join(ips)}")
    if failures:
        print(f"\n{failures} domain(s) resolve to private addresses — investigate "
              "DNS rebinding or typo.", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"\nclean: {len(ALLOWED_DOMAINS)} domains audited")
    return 0


def _is_private(ip_str: str) -> bool:
    """Same `is_global + is_multicast` check as the SSRF guard."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable IPs are treated as failures, same as the guard
    return not addr.is_global or addr.is_multicast


if __name__ == "__main__":
    raise SystemExit(main())
