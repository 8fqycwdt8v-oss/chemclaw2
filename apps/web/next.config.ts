import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
  // Tell Next.js/Webpack to transpile workspace packages from TypeScript source.
  // Without this, .js extension imports in ESM packages can't be resolved.
  transpilePackages: ['@chemclaw2/db', '@tiptap/react', '@tiptap/pm', '@tiptap/starter-kit', '@tiptap/extension-placeholder'],
};

export default nextConfig;
