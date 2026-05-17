import dns from 'dns';
import ipaddr from 'ipaddr.js';
import { Agent } from 'undici';
import { logger } from '@chemclaw2/observability';

/**
 * Resolve hostname via DNS and reject if any resolved address is non-unicast
 * (loopback, private, link-local, multicast, etc.).
 *
 * Used as a pre-flight check; the undici dispatcher below repeats the
 * range-check at connect time so the IP we actually open a socket to is
 * the IP that was range-checked — closing the prior TOCTOU window.
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
 * undici lookup hook that range-checks every candidate IP at connect time.
 * Returns the first public unicast address; raises an SSRF error otherwise.
 * Because this runs immediately before TCP connect, the IP we check is the
 * IP we connect to — no DNS-rebinding TOCTOU window.
 */
function safeLookup(
  hostname: string,
  options: dns.LookupOptions,
  cb: (err: NodeJS.ErrnoException | null, address: string, family: number) => void,
): void {
  dns.lookup(hostname, { ...options, all: true }, (err, results) => {
    if (err) return cb(err, '', 0);
    const list = Array.isArray(results) ? results : [results as unknown as { address: string; family: number }];
    for (const r of list) {
      if (!ipaddr.isValid(r.address)) {
        logger.warn('safe_fetch_ssrf_block_connect', { hostname, reason: 'unrecognised_address', address: r.address });
        return cb(new Error(`SSRF blocked: ${hostname} resolved to an unrecognised address format`), '', 0);
      }
      if (ipaddr.parse(r.address).range() !== 'unicast') {
        logger.warn('safe_fetch_ssrf_block_connect', { hostname, reason: 'non_unicast', address: r.address });
        return cb(new Error(`SSRF blocked: ${hostname} → ${r.address} (non-public)`), '', 0);
      }
    }
    const first = list[0];
    cb(null, first.address, first.family);
  });
}

// Singleton dispatcher shared across all safeFetch calls. The connect-time
// IP range check closes the TOCTOU window — the IP that passes is the IP
// undici opens a socket to.
const SAFE_DISPATCHER = new Agent({
  connect: { lookup: safeLookup },
});

/**
 * fetch() wrapper that:
 * 1. Validates initial URL hostname against caller-supplied allowlist
 * 2. Routes through an undici Agent whose lookup hook range-checks the IP
 *    at connect time (closes the TOCTOU window)
 * 3. After redirects, re-validates the final URL hostname against the
 *    allowlist (the dispatcher has already re-checked the IP).
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
  // Pre-flight check — undici will repeat this at connect time, but doing
  // it here gives a cleaner error message for the common case (private IP
  // in the URL itself).
  await assertNotPrivateHost(initial.hostname);

  const res = await fetch(url, {
    ...init,
    redirect: 'follow',
    // @ts-expect-error — Node's global fetch accepts undici Dispatcher at runtime; the type isn't surfaced on RequestInit.
    dispatcher: SAFE_DISPATCHER,
  });

  const final = new URL(res.url);
  if (!isDomainAllowed(final.hostname)) {
    logger.warn('safe_fetch_redirect_blocked', { initial_host: initial.hostname, final_host: final.hostname });
    throw new Error(`Redirect target domain not allowed: ${final.hostname}`);
  }

  return res;
}
