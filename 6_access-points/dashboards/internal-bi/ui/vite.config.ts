import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Vite output goes to ../dist. FastAPI (app/main.py) serves dist/index.html at
// '/', the hashed bundle under /assets and the icon sprite and images under
// /static, alongside its /auth/* and /gw/* routes.
export default defineConfig({
  plugins: [react()],
  base: '/',
  // Prefer the TypeScript sources: resolve .ts/.tsx before any .js file of the
  // same name.
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
      '/api':    'http://127.0.0.1:8087',
      '/static': 'http://127.0.0.1:8087',
    },
  },
});
