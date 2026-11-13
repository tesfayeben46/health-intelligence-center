# MLflow tracking server image: pin a version + add the dependencies
# the stock pip-installed mlflow misses (psycopg2 for the PG backend,
# boto3 for the S3 artifact backend).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        mlflow==2.18.0 \
        psycopg2-binary==2.9.10 \
        boto3==1.35.45

EXPOSE 5000

# Use shell form so envs expand.
#
# Proxied artifacts (--serve-artifacts + mlflow-artifacts: default root).
# The server itself is the only component that holds the S3 credentials
# (AWS_* from mlflow-secrets, see mlflow.yaml). Clients (notebooks in a lab,
# the runner) upload/download through the tracking server over HTTP and never
# need S3 creds; they need only MLFLOW_TRACKING_URI. This is what lets
# `mlflow.log_artifact` / `*.log_model` work from a JupyterHub lab pod, which
# has no S3 creds.
#
#   --default-artifact-root "mlflow-artifacts:/"  -> new/Default experiments get
#       a proxied artifact root; clients resolve it to the server's
#       /api/2.0/mlflow-artifacts/ endpoint (no client S3 creds needed).
#   --artifacts-destination "${MLFLOW_DEFAULT_ARTIFACT_ROOT}" -> where the server
#       actually stores the bytes (s3://nhdw-artifacts/mlflow) using its own creds.
#   --serve-artifacts -> enable the proxy endpoint (explicit; default in 2.x).
#
# The seeded /models/* experiments keep their existing s3:// artifact roots
# recorded in the backend store; the server reads and writes those with its own S3
# creds, so they are unaffected. Only new/Default experiments use the proxy.
CMD mlflow server \
    --backend-store-uri "postgresql+psycopg2://${MLFLOW_DB_USER}:${MLFLOW_DB_PASSWORD}@${MLFLOW_DB_HOST}:${MLFLOW_DB_PORT}/${MLFLOW_DB_NAME}" \
    --default-artifact-root "mlflow-artifacts:/" \
    --artifacts-destination "${MLFLOW_DEFAULT_ARTIFACT_ROOT}" \
    --serve-artifacts \
    --host "${MLFLOW_HOST}" \
    --port "${MLFLOW_PORT}" \
    --workers "${MLFLOW_WORKERS}"
