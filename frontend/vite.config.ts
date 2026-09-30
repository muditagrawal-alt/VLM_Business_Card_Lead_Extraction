import { defineConfig } from 'vitest/config';
import { loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

/**
 * A Content-Security-Policy for the built page, allowing exactly one API origin.
 *
 * Caddy sends the policy as a header when it serves the SPA itself. A static
 * host has no Caddy, and the policy has to name the API's origin, which is
 * only known at build time, so it is written into the HTML instead. Build only:
 * the dev server relies on inline scripts and a websocket for hot reload.
 * frame-ancestors cannot be set from a meta tag; the static host's own headers
 * (vercel.json) cover framing.
 */
function contentSecurityPolicy(apiBase: string): Plugin {
  const api = apiBase ? ` ${new URL(apiBase).origin}` : '';
  const policy = [
    "default-src 'self'",
    `connect-src 'self'${api}`,
    `img-src 'self' data: blob:${api}`,
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    // Image compression runs in a worker built from a blob: URL.
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join('; ');
  return {
    name: 'content-security-policy',
    apply: 'build',
    transformIndexHtml: () => [
      {
        tag: 'meta',
        attrs: { 'http-equiv': 'Content-Security-Policy', content: policy },
        injectTo: 'head-prepend',
      },
    ],
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    tailwindcss(),
    contentSecurityPolicy(loadEnv(mode, process.cwd(), 'VITE_').VITE_API_BASE_URL ?? ''),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    // Dev-only: Caddy handles this path routing in production.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // Split by change cadence rather than by size: the framework and
        // data layer are stable while app code changes constantly, and one
        // combined chunk would re-download React on every deploy.
        //
        // Matched on the resolved module path rather than the package name,
        // because the entry actually imported is `react-dom/client`, which
        // an exact-name match silently misses — leaving react-dom in the
        // app chunk and the "react" chunk at a suspicious 4 kB.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'react';
          if (id.includes('node_modules/@tanstack/')) return 'data';
          if (/node_modules\/(motion|framer-motion)/.test(id)) return 'motion';
          return 'vendor';
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
}));
