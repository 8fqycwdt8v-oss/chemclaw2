import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
  // Tell Next.js/Webpack to transpile workspace packages from TypeScript source.
  // Without this, .js extension imports in ESM packages can't be resolved.
  transpilePackages: ['@chemclaw2/db', '@tiptap/react', '@tiptap/pm', '@tiptap/starter-kit', '@tiptap/extension-placeholder'],

  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
          { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' https://*.clerk.accounts.dev",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: https:",
              "connect-src 'self' https://api.anthropic.com https://api.openai.com https://cloud.langfuse.com https://*.clerk.accounts.dev wss://*.clerk.accounts.dev",
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "form-action 'self'",
            ].join('; '),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
