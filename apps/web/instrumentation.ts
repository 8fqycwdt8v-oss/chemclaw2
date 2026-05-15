export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const publicKey = process.env.LANGFUSE_PUBLIC_KEY;
    const secretKey = process.env.LANGFUSE_SECRET_KEY;
    if (!publicKey || !secretKey) {
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
          publicKey,
          secretKey,
          baseUrl: process.env.LANGFUSE_BASEURL ?? 'https://cloud.langfuse.com',
        }),
      ],
      instrumentations: [getNodeAutoInstrumentations()],
    });

    sdk.start();
  }
}
