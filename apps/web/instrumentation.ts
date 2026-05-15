export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { NodeSDK } = await import('@opentelemetry/sdk-node');
    const { getNodeAutoInstrumentations } = await import(
      '@opentelemetry/auto-instrumentations-node'
    );
    const { LangfuseSpanProcessor } = await import('@langfuse/otel');

    const sdk = new NodeSDK({
      spanProcessors: [
        new LangfuseSpanProcessor({
          publicKey: process.env.LANGFUSE_PUBLIC_KEY!,
          secretKey: process.env.LANGFUSE_SECRET_KEY!,
          baseUrl: process.env.LANGFUSE_BASEURL ?? 'https://cloud.langfuse.com',
        }),
      ],
      instrumentations: [getNodeAutoInstrumentations()],
    });

    sdk.start();
  }
}
