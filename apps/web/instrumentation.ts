export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    // Process handlers must run in the same Node runtime as the route
    // handlers; the edge runtime cannot install them. Idempotent.
    const { installProcessHandlers } = await import('@chemclaw2/observability');
    installProcessHandlers('web');

    const publicKey = process.env.LANGFUSE_PUBLIC_KEY;
    const secretKey = process.env.LANGFUSE_SECRET_KEY;
    if (!publicKey || !secretKey) {
      if (process.env.NODE_ENV === 'production') {
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

    const sdk = new NodeSDK({
      spanProcessors: [
        new LangfuseSpanProcessor({
          publicKey,
          secretKey,
          baseUrl: process.env.LANGFUSE_BASEURL ?? 'https://cloud.langfuse.com',
        }),
      ],
      instrumentations: [getNodeAutoInstrumentations()],
    });

    sdk.start();

    // Flush spans before the process exits (Fly SIGTERM before container kill)
    const shutdown = () => sdk.shutdown().finally(() => process.exit(0));
    process.once('SIGTERM', shutdown);
    process.once('SIGINT', shutdown);
  }
}
