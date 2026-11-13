# HCMIS WDM ingestor: pulls from SQL Server (HCMISWDM database) and writes
# Parquet to the lake (SeaweedFS S3). HCMIS WDM exposes its data only through
# direct SQL Server access.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg apt-transport-https \
      unixodbc-dev \
      g++ \
    && rm -rf /var/lib/apt/lists/* \
    && install -d /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DLT_PIPELINE_DIR=/app/state/dlt

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]
