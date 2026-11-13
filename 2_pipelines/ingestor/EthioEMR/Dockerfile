# EthioEMR ingestor: reads the facility OpenMRS EMR directly over MySQL and
# writes FHIR-shaped Parquet to the lake (SeaweedFS S3).
#
# EthioEMR's native integration surface is CDC/JDBC on its MySQL backend; the
# ingestor SELECTs person/patient/patient_identifier/encounter/visit/obs and
# reshapes each row into the FHIR R4 resource shape the downstream vault expects.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
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
