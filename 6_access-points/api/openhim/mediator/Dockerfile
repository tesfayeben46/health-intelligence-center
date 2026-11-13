FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
        fastapi==0.115.6 \
        uvicorn[standard]==0.32.1 \
        httpx==0.28.1

WORKDIR /app
COPY app /app/app

EXPOSE 80
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
