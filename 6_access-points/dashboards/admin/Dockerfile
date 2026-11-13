# Stage 1 · build the React/Vite SPA
FROM node:22-slim AS ui
WORKDIR /ui
COPY admin/ui/package*.json ./
RUN npm ci --silent
COPY admin/ui/ ./
RUN npm run build

# Stage 2 · FastAPI runtime
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
        fastapi==0.115.6 \
        uvicorn[standard]==0.32.1 \
        httpx==0.28.1 \
        python-jose[cryptography]==3.3.0 \
        itsdangerous==2.2.0 \
        PyYAML==6.0.2

WORKDIR /app
COPY admin/app    /app/app
COPY admin/static /app/static
COPY --from=ui /dist /app/dist
EXPOSE 80
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
