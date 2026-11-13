# NHDW Superset: Apache Superset, the Trino SQLAlchemy driver and the FAB OIDC
# config. Self-service SQL and BI for the trusted analyst set (see the
# superset_config.py header for the governance boundary). Single process, no
# Celery or Redis.
FROM apache/superset:4.0.2

USER root
# Trino dialect for SQL Lab over the marts catalog, and Authlib for FAB's
# AUTH_OAUTH provider (not in the base image). The base ships psycopg2 for the
# Postgres metadata store. trino is pinned for SQLAlchemy 1.4 compatibility.
# cryptography stays inside Superset's pin (>=42.0.4,<43); otherwise trino or
# Authlib pulls a newer one and Superset's pkg_resources check fails.
RUN pip install --no-cache-dir "trino==0.328.0" "Authlib>=1.3,<2" "cryptography>=42.0.4,<43"

COPY config/superset_config.py  /app/pythonpath/superset_config.py
COPY config/create_trino_db.py  /app/pythonpath/create_trino_db.py
ENV SUPERSET_CONFIG_PATH=/app/pythonpath/superset_config.py

USER superset
