const ELN_BASE = process.env.ELN_API_BASE_URL ?? '';
const ELN_KEY = process.env.ELN_API_KEY ?? '';

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

    // Trailing slash on base ensures new URL() appends rather than replaces the path prefix
    const base = ELN_BASE.endsWith('/') ? ELN_BASE : ELN_BASE + '/';
    const targetUrl = new URL(`experiments/${encodeURIComponent(input.experiment_id)}`, base);
    // Enforce same-origin to prevent SSRF via path traversal in experiment_id
    if (targetUrl.hostname !== baseUrl.hostname) {
      return { error: 'Constructed URL hostname does not match ELN_API_BASE_URL' };
    }

    const res = await fetch(targetUrl.toString(), {
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
