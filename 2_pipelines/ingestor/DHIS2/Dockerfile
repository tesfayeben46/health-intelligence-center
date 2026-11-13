# DHIS2 ingestor: pulls from /api/* and writes Parquet to the lake (SeaweedFS S3) via dlt.
#
# This is a one-shot job container: the entrypoint runs a pipeline by name,
# writes its output, and exits. The orchestrator (Airflow) is responsible for
# scheduling and retries.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# dlt persists incremental state under this directory (we override
# destination-side state with PIPELINE_DIR pointing at /app/state via env).
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DLT_PIPELINE_DIR=/app/state/dlt

# Entrypoint = the CLI; pipeline name passed as the first arg.
# Example: docker run dhis2-ingestor metadata
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]
