import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { logger } from '../logger';
import { runWithRequestContext, mintRequestId } from '../request-context';

describe('logger', () => {
  // process.stdout/stderr.write has an overloaded signature that confuses
  // vi.spyOn's generic types; cast through unknown to keep the test typed.
  let stdoutSpy: ReturnType<typeof vi.fn>;
  let stderrSpy: ReturnType<typeof vi.fn>;
  let originalStdout: typeof process.stdout.write;
  let originalStderr: typeof process.stderr.write;

  beforeEach(() => {
    originalStdout = process.stdout.write.bind(process.stdout);
    originalStderr = process.stderr.write.bind(process.stderr);
    stdoutSpy = vi.fn(() => true);
    stderrSpy = vi.fn(() => true);
    process.stdout.write = stdoutSpy as unknown as typeof process.stdout.write;
    process.stderr.write = stderrSpy as unknown as typeof process.stderr.write;
  });
  afterEach(() => {
    process.stdout.write = originalStdout;
    process.stderr.write = originalStderr;
  });

  it('emits a JSON line with level, time, event', () => {
    logger.info('test_event', { foo: 'bar' });
    expect(stdoutSpy).toHaveBeenCalledOnce();
    const line = stdoutSpy.mock.calls[0][0] as string;
    const parsed = JSON.parse(line.trim());
    expect(parsed.level).toBe('info');
    expect(parsed.event).toBe('test_event');
    expect(parsed.foo).toBe('bar');
    expect(parsed.time).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it('writes errors and warnings to stderr', () => {
    logger.warn('warned', { x: 1 });
    logger.error('boom', { y: 2 });
    expect(stdoutSpy).not.toHaveBeenCalled();
    expect(stderrSpy).toHaveBeenCalledTimes(2);
  });

  it('serializes Error objects on the error field', () => {
    logger.error('failure', { op: 'thing' }, new Error('expected message'));
    const line = stderrSpy.mock.calls[0][0] as string;
    const parsed = JSON.parse(line.trim());
    expect(parsed.error.message).toBe('expected message');
    expect(parsed.error.name).toBe('Error');
  });

  it('includes request context fields when set', () => {
    const requestId = mintRequestId();
    runWithRequestContext({ requestId, userId: 'user_123' }, () => {
      logger.info('ctx_test', {});
    });
    const line = stdoutSpy.mock.calls[0][0] as string;
    const parsed = JSON.parse(line.trim());
    expect(parsed.request_id).toBe(requestId);
    expect(parsed.user_id).toBe('user_123');
  });

  it('honors LOG_LEVEL=warn to suppress info/debug', () => {
    const prev = process.env.LOG_LEVEL;
    process.env.LOG_LEVEL = 'warn';
    try {
      logger.info('skipped', {});
      logger.warn('kept', {});
      expect(stdoutSpy).not.toHaveBeenCalled();
      expect(stderrSpy).toHaveBeenCalledOnce();
    } finally {
      process.env.LOG_LEVEL = prev;
    }
  });

  it('never throws on circular references', () => {
    const cyclic: Record<string, unknown> = { name: 'cycle' };
    cyclic.self = cyclic;
    // Should not throw; should emit a fallback line.
    expect(() => logger.error('bad_payload', { cyclic })).not.toThrow();
    expect(stderrSpy).toHaveBeenCalledOnce();
    const parsed = JSON.parse((stderrSpy.mock.calls[0][0] as string).trim());
    expect(parsed.log_serialize_failed).toBe(true);
  });
});
