import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Vite builds into ../dist. The FastAPI BFF (app/main.py) serves dist/index.html
// at '/', the hashed bundle under /assets and the icon sprite and images under
// /static, and proxies /gw/* to the gateway.
export default defineConfig({
  plugins: [react()],
  base: '/',
  // Resolve .tsx/.ts before .js so a stray .js file of the same name is never
  // picked over the TypeScript source.
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
      '/api':    'http://127.0.0.1:8085',
      '/static': 'http://127.0.0.1:8085',
    },
  },
});
