#!/bin/bash
# NHDW singleuser entrypoint: pull the project's files from the object store
# (SeaweedFS, S3 API) into the workdir before the lab opens, push edits back on
# a background loop, then hand off to the stock start-singleuser.sh.
set -u

if [ -n "${NHDW_PROJECT_PREFIX:-}" ]; then
  echo "[nhdw] project-scoped lab (${NHDW_PROJECT_SLUG:-?}): pulling project files..."
  python3 /usr/local/bin/nhdw_project_sync.py pull || \
    echo "[nhdw] WARNING: project pull failed; the lab opens without project files" >&2
  python3 /usr/local/bin/nhdw_project_sync.py loop &
fi

# On first launch, copy the MLflow quickstart notebook into the workdir. An
# existing copy is never overwritten, and the project sync does not touch it
# (it reconciles the projects/<id>/files/ prefix only).
NB_SRC=/opt/nhdw/mlflow_quickstart.ipynb
NB_DST=/home/jovyan/work/mlflow_quickstart.ipynb
if [ -f "$NB_SRC" ] && [ ! -e "$NB_DST" ]; then
  cp "$NB_SRC" "$NB_DST" 2>/dev/null && echo "[nhdw] seeded mlflow_quickstart.ipynb into ~/work"
fi

exec /usr/local/bin/start-singleuser.sh "$@"
