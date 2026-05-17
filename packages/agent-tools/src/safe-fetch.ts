import dns from 'dns';
import ipaddr from 'ipaddr.js';
import { logger } from '@chemclaw2/observability';

/**
 * Resolve hostname via DNS and reject if any resolved address is non-unicast
 * (loopback, private, link-local, multicast, etc.).
 *
 * This is a best-effort DNS rebinding guard for native fetch(). Two known gaps:
 * - TOCTOU: the resolved IP can change between this check and TCP connection.
 * - Intermediate redirect hops are not individually DNS-validated.
 */
async function assertNotPrivateHost(hostname: string): Promise<void> {
  let addresses: Array<{ address: string; family: number }>;
  const startMs = Date.now();
  try {
    addresses = await dns.promises.lookup(hostname, { all: true });
  } catch (err) {
    logger.warn('dns_lookup_failed', {
      hostname,
      duration_ms: Date.now() - startMs,
    }, err);
    throw new Error(`DNS resolution failed for ${hostname}`);
  }
  for (const { address } of addresses) {
    // Fail closed: treat unrecognised address formats as non-public to prevent bypass
    if (!ipaddr.isValid(address)) {
      logger.warn('safe_fetch_ssrf_block', { hostname, reason: 'unrecognised_address', address });
      throw new Error(`SSRF blocked: ${hostname} resolved to an unrecognised address format`);
    }
    const parsed = ipaddr.parse(address);
    if (parsed.range() !== 'unicast') {
      logger.warn('safe_fetch_ssrf_block', { hostname, reason: 'non_unicast', range: parsed.range() });
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
    logger.warn('safe_fetch_domain_blocked', { hostname: initial.hostname });
    throw new Error(`Domain not allowed: ${initial.hostname}`);
  }
  await assertNotPrivateHost(initial.hostname);

  const res = await fetch(url, { ...init, redirect: 'follow' });

  // Post-redirect revalidation: hostname + DNS check on the final URL
  const final = new URL(res.url);
  if (!isDomainAllowed(final.hostname)) {
    logger.warn('safe_fetch_redirect_blocked', { initial_host: initial.hostname, final_host: final.hostname });
    throw new Error(`Redirect target domain not allowed: ${final.hostname}`);
  }
  if (final.hostname !== initial.hostname) {
    await assertNotPrivateHost(final.hostname);
  }

  return res;
}
