'use client';

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html>
      <body>
        <main style={{ display: 'flex', minHeight: '100vh', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '1rem', padding: '2rem', textAlign: 'center' }}>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Application error</h1>
          <p>An unexpected error occurred.</p>
          <button onClick={reset} style={{ border: '1px solid #ccc', borderRadius: '4px', padding: '0.5rem 1rem' }}>
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
