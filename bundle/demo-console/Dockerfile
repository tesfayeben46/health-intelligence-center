# Build-only image for the demo-console SPA (React and Vite); it is never
# deployed. build-spa.sh builds it and copies /dist out, and console.py serves
# those files statically, so the host needs no Node toolchain.
FROM node:22-slim
WORKDIR /ui
COPY ui/package*.json ./
RUN npm install
COPY ui/ ./
RUN npm run build     # vite outDir '../dist' resolves to /dist
