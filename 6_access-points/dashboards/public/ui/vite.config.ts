import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Build output goes to ../dist. FastAPI serves dist/index.html at '/', the
// bundles under /assets, shared files under /static and the API under /api.
// The dev server proxies /api and /static to a local FastAPI on port 8088.
export default defineConfig({
  plugins: [react()],
  base: '/',
  // Resolve TypeScript sources before any .js twin emitted by `tsc -b`
  // (Vite's default order would pick the .js file first).
  resolve: {
    extensions: ['.tsx', '.ts', '.jsx', '.js', '.json', '.mjs'],
  },
  build: {
    outDir: path.resolve(__dirname, '../dist'),
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  },
  server: {
    proxy: {
      '/api':    'http://127.0.0.1:8088',
      '/static': 'http://127.0.0.1:8088',
    },
  },
});
