import dns from 'dns';
import ipaddr from 'ipaddr.js';

const ELN_BASE = process.env.ELN_API_BASE_URL ?? '';
const ELN_KEY = process.env.ELN_API_KEY ?? '';

async function assertElnHostNotPrivate(hostname: string): Promise<string | null> {
  try {
    const addresses = await dns.promises.lookup(hostname, { all: true });
    for (const { address } of addresses) {
      if (!ipaddr.isValid(address)) continue;
      if (ipaddr.parse(address).range() !== 'unicast') {
        return `SSRF blocked: ELN host ${hostname} resolves to a non-public address`;
      }
    }
    return null;
  } catch {
    return `DNS resolution failed for ELN host: ${hostname}`;
  }
}

export const elnFetchTool = {
  name: 'eln_fetch_experiment',
  description: 'Fetch a read-only experiment record from the connected ELN system.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      experiment_id: { type: 'string', description: 'ELN experiment identifier (e.g. EXP-001)' },
    },
    required: ['experiment_id'],
  },
  async execute(input: { experiment_id: string }) {
    if (!ELN_BASE) return { error: 'ELN integration not configured (ELN_API_BASE_URL not set)' };

    let baseUrl: URL;
    try {
      baseUrl = new URL(ELN_BASE);
    } catch {
      return { error: 'Invalid ELN_API_BASE_URL' };
    }

    if (!ELN_KEY) return { error: 'ELN integration not configured (ELN_API_KEY not set)' };

    const dnsError = await assertElnHostNotPrivate(baseUrl.hostname);
    if (dnsError) return { error: dnsError };

    // Trailing slash on base ensures new URL() appends rather than replaces the path prefix
    const base = ELN_BASE.endsWith('/') ? ELN_BASE : ELN_BASE + '/';
    const targetUrl = new URL(`experiments/${encodeURIComponent(input.experiment_id)}`, base);
    // Compare full origin (scheme + host + port) to prevent scheme-downgrade and port-swap SSRF
    if (targetUrl.origin !== baseUrl.origin) {
      return { error: 'Constructed URL does not match ELN_API_BASE_URL origin' };
    }

    const res = await fetch(targetUrl.toString(), {
      redirect: 'error',
      headers: {
        Authorization: `Bearer ${ELN_KEY}`,
        Accept: 'application/json',
      },
    });

    if (!res.ok) return { error: `ELN responded with HTTP ${res.status}` };
    const data = await res.json() as Record<string, unknown>;
    return { experiment_id: input.experiment_id, data };
  },
};
