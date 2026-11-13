# NHDW singleuser lab image: scipy stack, warehouse and ML clients, and the
# project-sync entrypoint. Spawned per user by the in-cluster JupyterHub
# (KubeSpawner); not run standalone.
FROM jupyter/scipy-notebook:python-3.11

USER root
COPY start-nhdw.sh /usr/local/bin/start-nhdw.sh
COPY nhdw_project_sync.py /usr/local/bin/nhdw_project_sync.py
RUN chmod 0755 /usr/local/bin/start-nhdw.sh /usr/local/bin/nhdw_project_sync.py

# nhdw_mlflow: MLflow helper installed into site-packages so `import nhdw_mlflow`
# works from any notebook (the ~/work PVC would hide a file baked into the
# workdir). The quickstart notebook is staged in /opt/nhdw and copied into
# ~/work on first launch by start-nhdw.sh, never overwriting an existing copy.
COPY nhdw_mlflow.py /opt/conda/lib/python3.11/site-packages/nhdw_mlflow.py
COPY mlflow_quickstart.ipynb /opt/nhdw/mlflow_quickstart.ipynb
RUN chmod 0644 /opt/conda/lib/python3.11/site-packages/nhdw_mlflow.py \
               /opt/nhdw/mlflow_quickstart.ipynb

USER ${NB_UID}
# Warehouse and registry clients on top of the scipy stack (pandas, sklearn and
# matplotlib are in the base). Versions pinned for the offline bundle.
RUN pip install --no-cache-dir \
        mlflow-skinny==2.22.0 \
        boto3==1.35.99 \
        psycopg2-binary==2.9.10 \
        trino==0.330.0

WORKDIR /home/jovyan/work
