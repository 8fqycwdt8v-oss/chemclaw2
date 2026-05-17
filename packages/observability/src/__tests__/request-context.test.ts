import { describe, it, expect } from 'vitest';
import { requestContext, runWithRequestContext, mintRequestId } from '../request-context';

describe('request-context', () => {
  it('mints a unique id per call', () => {
    const a = mintRequestId();
    const b = mintRequestId();
    expect(a).not.toBe(b);
    expect(a.length).toBeGreaterThan(8);
  });

  it('propagates the context across awaits', async () => {
    const id = mintRequestId();
    await runWithRequestContext({ requestId: id, userId: 'u1' }, async () => {
      await new Promise((r) => setTimeout(r, 0));
      expect(requestContext.get()?.requestId).toBe(id);
      expect(requestContext.get()?.userId).toBe('u1');
    });
  });

  it('isolates concurrent contexts', async () => {
    const [a, b] = [mintRequestId(), mintRequestId()];
    await Promise.all([
      runWithRequestContext({ requestId: a }, async () => {
        await new Promise((r) => setTimeout(r, 5));
        expect(requestContext.get()?.requestId).toBe(a);
      }),
      runWithRequestContext({ requestId: b }, async () => {
        await new Promise((r) => setTimeout(r, 1));
        expect(requestContext.get()?.requestId).toBe(b);
      }),
    ]);
  });

  it('returns undefined outside a context', () => {
    expect(requestContext.get()).toBeUndefined();
  });
});
