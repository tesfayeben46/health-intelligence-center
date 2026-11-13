# Stage 1 · build the React/Vite SPA
FROM node:22-slim AS ui
WORKDIR /ui
COPY workshop/ui/package*.json ./
RUN npm ci --silent
COPY workshop/ui/ ./
RUN npm run build
# Vite outDir is ../dist to /dist

# Stage 2 · FastAPI runtime
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
        fastapi==0.115.6 \
        uvicorn[standard]==0.32.1 \
        httpx==0.28.1 \
        itsdangerous==2.2.0 \
        python-jose[cryptography]==3.3.0

WORKDIR /app
COPY workshop/app    /app/app
COPY workshop/static /app/static
COPY --from=ui /dist /app/dist
EXPOSE 80
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
