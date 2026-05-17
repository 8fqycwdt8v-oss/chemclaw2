import dns from 'dns';
import { z } from 'zod';
import ipaddr from 'ipaddr.js';
import { recordExternalFactSafe } from '@chemclaw2/db';
import { logger } from '@chemclaw2/observability';
import type { ToolDef } from './tool-def';

const ELN_BASE = process.env.ELN_API_BASE_URL ?? '';
const ELN_KEY = process.env.ELN_API_KEY ?? '';

async function assertElnHostNotPrivate(hostname: string): Promise<string | null> {
  try {
    const addresses = await dns.promises.lookup(hostname, { all: true });
    for (const { address } of addresses) {
      if (!ipaddr.isValid(address)) {
        return `SSRF blocked: ELN host ${hostname} resolved to an unrecognised address format`;
      }
      if (ipaddr.parse(address).range() !== 'unicast') {
        return `SSRF blocked: ELN host ${hostname} resolves to a non-public address`;
      }
    }
    return null;
  } catch {
    return `DNS resolution failed for ELN host: ${hostname}`;
  }
}

const elnFetchSchema = {
  experiment_id: z.string().describe('ELN experiment identifier (e.g. EXP-001)'),
};

export const elnFetchTool: ToolDef<typeof elnFetchSchema> = {
  name: 'eln_fetch_experiment',
  description: 'Fetch a read-only experiment record from the connected ELN system.',
  subagents: ['deep-research'],
  schema: elnFetchSchema,
  async execute(input) {
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

    const startMs = Date.now();
    let res: Response;
    try {
      res = await fetch(targetUrl.toString(), {
        redirect: 'error',
        headers: {
          Authorization: `Bearer ${ELN_KEY}`,
          Accept: 'application/json',
        },
      });
    } catch (err) {
      logger.error('eln_fetch_failed', {
        experiment_id: input.experiment_id,
        duration_ms: Date.now() - startMs,
      }, err);
      return { error: 'ELN fetch failed' };
    }

    if (!res.ok) {
      logger.warn('eln_http_error', {
        experiment_id: input.experiment_id,
        status: res.status,
        duration_ms: Date.now() - startMs,
      });
      return { error: `ELN responded with HTTP ${res.status}` };
    }

    // Guard response size before parsing — prevents OOM on unexpectedly large ELN records
    const MAX_BYTES = 500_000;
    const raw = await res.text();
    if (Buffer.byteLength(raw, 'utf8') > MAX_BYTES) {
      logger.warn('eln_oversized_response', {
        experiment_id: input.experiment_id,
        bytes: Buffer.byteLength(raw, 'utf8'),
      });
      return { error: 'ELN response exceeds size limit (500 KB)' };
    }
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(raw) as Record<string, unknown>;
    } catch (err) {
      logger.warn('eln_json_parse_failed', {
        experiment_id: input.experiment_id,
        sample: raw.slice(0, 300),
        length: raw.length,
      }, err);
      return { error: 'ELN response is not valid JSON' };
    }
    logger.info('eln_fetch_complete', {
      experiment_id: input.experiment_id,
      duration_ms: Date.now() - startMs,
    });
    return { experiment_id: input.experiment_id, data };
  },
};

/**
 * Wave-2a persistence wrapper: after a successful fetch, upsert the payload
 * into external_facts keyed by ('eln', experiment_id) so the next call (this
 * session or any other) can fast-path from world-state instead of re-hitting
 * the ELN API. Persistence failures are logged but never break the agent —
 * the in-band response always wins.
 */
export function createElnFetchTool(userId: string): ToolDef<typeof elnFetchSchema> {
  return {
    ...elnFetchTool,
    async execute(input) {
      const result = await elnFetchTool.execute(input);
      if (typeof result === 'object' && result && 'data' in result && !('error' in result)) {
        // No obvious text extract; FTS is opt-out by storing null content_text.
        await recordExternalFactSafe('eln', input.experiment_id, result, userId, null);
      }
      return result;
    },
  };
}
