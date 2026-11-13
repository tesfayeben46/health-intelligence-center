# NHDW staging dbt runner: dbt-core + dbt-clickhouse + dbt-utils.
#
# Why an image and not `pip install dbt-clickhouse` on the host: pinned
# versions, hermetic CI/Airflow execution, and zero contamination of the host
# Python env. The container is one-shot: it runs as a Job and exits when dbt
# finishes.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DBT_PROFILES_DIR=/dbt/profiles \
    DBT_PROJECT_DIR=/dbt

# dbt-clickhouse pins itself transitively against dbt-core. Keep both versions
# tight so updates are deliberate.
RUN pip install --no-cache-dir \
        dbt-core==1.10.16 \
        dbt-clickhouse==1.10.0 \
        boto3==1.35.0

WORKDIR /dbt

# Project files (models, macros, dbt_project.yml, profiles/) are baked into the
# image so every run uses the built project.
COPY . /dbt/

# Install deps declared in packages.yml (dbt-utils, etc.). Safe to repeat: dbt
# deps is idempotent.
RUN dbt deps --project-dir /dbt --profiles-dir /dbt/profiles || true

# Wrapper runs dbt, uploads target/ artifacts to the object store (SeaweedFS) under
# nhdw-artifacts/dbt/<DBT_PROJECT>/, and (when DBT_DOCS_OUT is mounted) runs
# `dbt docs generate` into the shared dbt-docs-content PVC so the dbt-docs
# surface can serve this project's lineage. Identical wrapper to the marts image.
# The apply_raw_views / build_processed_zone Jobs override `command`, so they
# bypass this wrapper and run their own scripts unchanged.
ENTRYPOINT ["python3", "/dbt/entrypoint.py"]
CMD ["--help"]
