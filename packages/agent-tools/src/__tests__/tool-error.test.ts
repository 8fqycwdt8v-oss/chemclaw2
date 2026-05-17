import { describe, it, expect, vi, beforeEach } from 'vitest';
import { trace } from '@opentelemetry/api';
import { toolError } from '../tool-error';

const addEvent = vi.fn();
const span = { addEvent } as unknown as ReturnType<typeof trace.getActiveSpan>;

beforeEach(() => {
  addEvent.mockClear();
  vi.spyOn(trace, 'getActiveSpan').mockReturnValue(span!);
});

describe('toolError', () => {
  it('returns the error message verbatim for Error instances', () => {
    const r = toolError('compound_similarity_search', new Error('hnsw timeout'));
    expect(r).toEqual({ error: 'hnsw timeout' });
  });

  it('stringifies non-Error throws', () => {
    const r = toolError('lookup_hazard', 'pubchem 503');
    expect(r).toEqual({ error: 'pubchem 503' });
  });

  it('falls back to "<name> failed" when message is empty', () => {
    const r = toolError('wiki_upsert', new Error(''));
    expect(r).toEqual({ error: 'wiki_upsert failed' });
  });

  it('emits a tool.execute_failed OTel event with name + truncated message', () => {
    toolError('fetch_document', new Error('long ' + 'x'.repeat(600)));
    expect(addEvent).toHaveBeenCalledWith('tool.execute_failed', expect.objectContaining({
      tool: 'fetch_document',
    }));
    const args = addEvent.mock.calls[0][1] as { message: string };
    expect(args.message.length).toBeLessThanOrEqual(500);
  });

  it('does not throw when no active span is set', () => {
    vi.spyOn(trace, 'getActiveSpan').mockReturnValue(undefined);
    expect(() => toolError('x', new Error('y'))).not.toThrow();
  });
});
