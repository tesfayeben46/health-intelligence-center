# Marts dbt runner: dbt-core + dbt-postgres.
#
# Reads from nhdw_vault_business + nhdw_vault_raw, writes into the bi_*
# schemas in postgres-vault. The schemas themselves are owned by
# 3_storage/postgres/init/01_schemas.sql; this image only populates them.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DBT_PROFILES_DIR=/dbt/profiles \
    DBT_PROJECT_DIR=/dbt

RUN pip install --no-cache-dir \
        dbt-core==1.10.16 \
        dbt-postgres==1.9.0 \
        boto3==1.35.0

WORKDIR /dbt
COPY . /dbt/

RUN dbt deps --project-dir /dbt --profiles-dir /dbt/profiles || true

# Wrapper runs dbt + uploads target/ to the object store (SeaweedFS) under nhdw-artifacts/dbt/<DBT_PROJECT>/.
# The gateway's /api/dq/* endpoints read those artifacts (manifest.json,
# run_results.json) to serve lineage + test-rollup data without bind-mounting.
ENTRYPOINT ["python3", "/dbt/entrypoint.py"]
CMD ["--help"]
