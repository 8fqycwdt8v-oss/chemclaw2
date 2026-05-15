import dns from 'dns';
import ipaddr from 'ipaddr.js';

/**
 * Resolve hostname via DNS and reject if any resolved address is non-unicast
 * (loopback, private, link-local, multicast, etc.).
 *
 * This is a best-effort DNS rebinding guard for native fetch(). Two known gaps:
 * - TOCTOU: the resolved IP can change between this check and TCP connection.
 * - Intermediate redirect hops are not individually DNS-validated; only the
 *   initial URL and the final res.url are checked against both the allowlist and DNS.
 *   Multi-hop redirect chains through a compromised allowed domain remain a
 *   theoretical gap. Definitive protection requires infrastructure-level egress
 *   filtering or per-hop redirect interception.
 */
async function assertNotPrivateHost(hostname: string): Promise<void> {
  let addresses: Array<{ address: string; family: number }>;
  try {
    addresses = await dns.promises.lookup(hostname, { all: true });
  } catch {
    throw new Error(`DNS resolution failed for ${hostname}`);
  }
  for (const { address } of addresses) {
    if (!ipaddr.isValid(address)) continue;
    const parsed = ipaddr.parse(address);
    if (parsed.range() !== 'unicast') {
      throw new Error(`SSRF blocked: ${hostname} resolves to a non-public address`);
    }
  }
}

/**
 * fetch() wrapper that:
 * 1. Validates initial URL hostname against caller-supplied allowlist
 * 2. Performs a DNS pre-flight check to block private/loopback IPs
 * 3. Follows redirects, then re-validates the final hostname + DNS
 */
export async function safeFetch(
  url: string,
  allowedDomains: string[],
  init?: RequestInit,
): Promise<Response> {
  const isDomainAllowed = (hostname: string) => {
    const h = hostname.replace(/^www\./, '');
    return allowedDomains.some((d) => h === d || h.endsWith('.' + d));
  };

  const initial = new URL(url);
  if (!isDomainAllowed(initial.hostname)) {
    throw new Error(`Domain not allowed: ${initial.hostname}`);
  }
  await assertNotPrivateHost(initial.hostname);

  const res = await fetch(url, { ...init, redirect: 'follow' });

  // Post-redirect revalidation: hostname + DNS check on the final URL
  const final = new URL(res.url);
  if (!isDomainAllowed(final.hostname)) {
    throw new Error(`Redirect target domain not allowed: ${final.hostname}`);
  }
  if (final.hostname !== initial.hostname) {
    await assertNotPrivateHost(final.hostname);
  }

  return res;
}
