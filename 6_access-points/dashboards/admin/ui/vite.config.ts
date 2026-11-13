import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Vite builds to ../dist. FastAPI (app/main.py) serves dist/index.html at '/',
// dist/assets at /assets and the static sprite/CSS at /static, and proxies
// /gw/* to the gateway.
export default defineConfig({
  plugins: [react()],
  base: '/',
  // Resolve .tsx/.ts before .js so a stray .js twin never shadows a TS source.
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
      '/api':    'http://127.0.0.1:8086',
      '/static': 'http://127.0.0.1:8086',
    },
  },
});
