import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
});
