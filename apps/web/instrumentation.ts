export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { webEnv } = await import('./lib/env');
    const { LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASEURL, NODE_ENV } = webEnv();
    if (!LANGFUSE_PUBLIC_KEY || !LANGFUSE_SECRET_KEY) {
      if (NODE_ENV === 'production') {
        throw new Error('LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required in production');
      }
      console.warn('Langfuse keys missing — OTel tracing disabled');
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
          publicKey: LANGFUSE_PUBLIC_KEY,
          secretKey: LANGFUSE_SECRET_KEY,
          baseUrl: LANGFUSE_BASEURL,
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
