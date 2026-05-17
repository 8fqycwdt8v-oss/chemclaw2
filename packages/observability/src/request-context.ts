export type RequestContext = {
  requestId: string;
  userId?: string;
  sessionId?: string;
};

// AsyncLocalStorage only exists in the Node runtime; the Edge runtime (where
// `mintRequestId` is called from middleware) doesn't need context propagation
// because middleware runs once per request and hands work off via headers.
// Dynamically resolved so the Edge bundle doesn't pull in `node:async_hooks`.
type Storage<T> = {
  getStore(): T | undefined;
  run<R>(ctx: T, fn: () => R): R;
};

let _storage: Storage<RequestContext> | undefined;
function getStorage(): Storage<RequestContext> | undefined {
  if (_storage) return _storage;
  // process is present in Node but not in the Edge runtime
  if (typeof process === 'undefined' || !process.versions?.node) return undefined;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { AsyncLocalStorage } = require('node:async_hooks') as typeof import('node:async_hooks');
    _storage = new AsyncLocalStorage<RequestContext>() as Storage<RequestContext>;
    return _storage;
  } catch {
    return undefined;
  }
}

export const requestContext = {
  get(): RequestContext | undefined {
    return getStorage()?.getStore();
  },
};

export function runWithRequestContext<T>(ctx: RequestContext, fn: () => T): T {
  const storage = getStorage();
  if (!storage) return fn();
  return storage.run(ctx, fn);
}

// Stable id format. Web Crypto's `randomUUID` exists in both Node ≥19 and the
// Edge runtime, so middleware (Edge) and route handlers (Node) can share this
// implementation without dragging `node:crypto` into the Edge bundle.
export function mintRequestId(): string {
  try {
    if (typeof globalThis.crypto?.randomUUID === 'function') {
      return globalThis.crypto.randomUUID();
    }
  } catch {
    // fall through
  }
  return `req_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}
