# Climate ingestor: pulls the Climate API and writes Parquet to the lake (SeaweedFS S3) via dlt.
# One-shot job container: runs a pipeline by name, writes output, exits.

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

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]
