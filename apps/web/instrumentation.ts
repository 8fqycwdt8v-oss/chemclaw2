export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    // Process handlers must run in the same Node runtime as the route
    // handlers; the edge runtime cannot install them. Idempotent.
    const { installProcessHandlers } = await import('@chemclaw2/observability');
    installProcessHandlers('web');

    const { webEnv } = await import('./lib/env');
    const { LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASEURL, NODE_ENV } = webEnv();
    if (!LANGFUSE_PUBLIC_KEY || !LANGFUSE_SECRET_KEY) {
      if (NODE_ENV === 'production') {
        throw new Error('LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required in production');
      }
      const { logger } = await import('@chemclaw2/observability');
      logger.warn('langfuse_keys_missing_tracing_disabled');
      return;
    }

    const { NodeSDK } = await import('@opentelemetry/sdk-node');
    const { getNodeAutoInstrumentations } = await import(
      '@opentelemetry/auto-instrumentations-node'
    );
    const { LangfuseSpanProcessor } = await import('@langfuse/otel');

    // Security: by default the HTTP and pg auto-instrumentations capture
    // request headers and full SQL parameter values. Both ship to Langfuse
    // and may contain user prompts, SMILES, or Bearer tokens. Redact
    // secret-bearing headers; turn off pg enhanced reporting so SQL
    // parameter values stay off-span (the Drizzle-parameterized template
    // is fine on its own).
    const sdk = new NodeSDK({
      spanProcessors: [
        new LangfuseSpanProcessor({
          publicKey: LANGFUSE_PUBLIC_KEY,
          secretKey: LANGFUSE_SECRET_KEY,
          baseUrl: LANGFUSE_BASEURL,
        }),
      ],
      instrumentations: [getNodeAutoInstrumentations({
        '@opentelemetry/instrumentation-http': {
          requestHook: (span) => {
            for (const attr of [
              'http.request.header.authorization',
              'http.request.header.cookie',
              'http.request.header.x-api-key',
            ]) {
              span.setAttribute(attr, '[REDACTED]');
            }
          },
        },
        '@opentelemetry/instrumentation-pg': {
          enhancedDatabaseReporting: false,
        },
      })],
    });

    sdk.start();

    // Flush spans before the process exits (Fly SIGTERM before container kill)
    const shutdown = () => sdk.shutdown().finally(() => process.exit(0));
    process.once('SIGTERM', shutdown);
    process.once('SIGINT', shutdown);
  }
}
