# Vault dbt runner: dbt-core + dbt-postgres + AutomateDV.
#
# AutomateDV (formerly dbtvault) is the community-standard package for Data
# Vault automation in dbt. Its hub_load / link_load / sat_load macros
# implement the canonical insert-on-change semantics:
#   - hubs: insert only if business key not already in hub.
#   - links: insert only if combination of hub_hash_keys not already in link.
#   - sats: insert only if hash_diff differs from the most recent sat row.
# That is the "only insert on real change" requirement, handled at the macro
# level with no custom SQL per sat.
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

# Build-time contract gate: every sat's payload/hashdiff must come from the
# macros/sat_payloads.sql single source of truth (payload == hashdiff +
# documented audit-timestamp exclusions). Drift fails the image build;
# stdlib-only, no DB needed.
RUN python3 scripts/check-sat-payload-alignment.py

RUN dbt deps --project-dir /dbt --profiles-dir /dbt/profiles || true

# Wrapper runs dbt, uploads target/ artifacts to the object store (SeaweedFS) under
# nhdw-artifacts/dbt/<DBT_PROJECT>/, and (when DBT_DOCS_OUT is mounted) runs
# `dbt docs generate` into the shared dbt-docs-content PVC so the dbt-docs
# surface can serve this project's lineage. Identical wrapper to the marts image.
ENTRYPOINT ["python3", "/dbt/entrypoint.py"]
CMD ["--help"]
