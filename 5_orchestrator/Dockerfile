# nhdw/airflow: Airflow with the NHDW DAGs baked in. Deployed by
# 1_infrastructure/k8s/base/airflow.yaml as a scheduler + webserver Deployment
# (LocalExecutor).
#
# The base image already ships everything the DAGs need (the postgres provider
# with psycopg2, and the cncf-kubernetes provider for KubernetesPodOperator), so
# this image only copies files and runs build-time checks (no apt or pip step).

FROM apache/airflow:2.10.4-python3.12

# --chown: the image runs as the `airflow` user (group root, per the official
# image convention); root-owned DAG files would break __pycache__ writes.
COPY --chown=airflow:0 dags/ /opt/airflow/dags/

# Native Keycloak OIDC for the webserver. Read only by the webserver from
# $AIRFLOW_HOME; the scheduler and init containers ignore it.
COPY --chown=airflow:0 webserver_config.py /opt/airflow/webserver_config.py

# Build-time checks: the providers the DAGs import must resolve, the DAGs must
# compile, and the OIDC security-manager base class that webserver_config
# extends must be importable (the build fails if the FAB provider drops it).
RUN python -c "import psycopg2; import airflow.providers.cncf.kubernetes.operators.pod" \
    && python -c "from airflow.providers.fab.auth_manager.security_manager.override import FabAirflowSecurityManagerOverride; import authlib" \
    && python -m compileall -q /opt/airflow/dags /opt/airflow/webserver_config.py
