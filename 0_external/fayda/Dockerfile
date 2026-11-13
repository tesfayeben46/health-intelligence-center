FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
        fastapi==0.115.6 \
        uvicorn[standard]==0.34.0 \
        python-jose[cryptography]==3.3.0 \
        cryptography==44.0.0 \
        PyYAML==6.0.2 \
        python-multipart==0.0.20

WORKDIR /app
COPY app /app/app
COPY seed /app/seed

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
