# MFR ingestor: pulls the Master Facility Registry over FHIR R4 and writes
# Parquet to the lake (SeaweedFS S3).
#
# MFR's richest exposure path is its FHIR R4 server (/Location for facilities,
# /Organization for owners). We paginate the FHIR Bundle `next` links until the
# registry is fully drained.

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
