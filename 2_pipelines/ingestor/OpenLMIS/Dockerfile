# OpenLMIS ingestor: the eLMIS v2 history lane (direct Postgres) and the OpenLMIS v3 API lane (OAuth2), writing Parquet to the lake (SeaweedFS S3) via dlt.
#
# This is a one-shot job container: the entrypoint runs a pipeline by name,
# writes its output, and exits. The orchestrator is responsible for
# scheduling and retries.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DLT_PIPELINE_DIR=/app/state/dlt

# Entrypoint = the CLI; pipeline name passed as the first arg.
# Example: docker run openlmis-ingestor all
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]
