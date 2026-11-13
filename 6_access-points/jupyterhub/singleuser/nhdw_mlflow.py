"""nhdw_mlflow: one-import MLflow setup for NHDW workshop labs.

`import nhdw_mlflow` at the top of a notebook points MLflow at the in-cluster
tracking server, scoped to the project's experiment. Artifacts and models
upload through the tracking server (proxied artifacts), so the lab needs no S3
credentials, only MLFLOW_TRACKING_URI, which the Hub injects.

    import nhdw_mlflow            # prints the UI URL + your project experiment
    import mlflow

    with nhdw_mlflow.start_run("my-first-run") as run:
        mlflow.log_param("alpha", 0.1)
        mlflow.log_metric("rmse", 0.42)
        mlflow.sklearn.log_model(model, "model")   # uploads via the server

    print(nhdw_mlflow.run_url(run))                 # clickable UI link

The experiment defaults to /projects/<your-project-slug> (from the project you
opened the lab under); outside a project it falls back to "Default".
"""
from __future__ import annotations

import os

import mlflow

# Internal service DNS; the Hub injects this into every lab.
TRACKING_URI = os.environ.get(
    "MLFLOW_TRACKING_URI", "http://mlflow.nhdw.svc.cluster.local:5000")

# Public (browser) UI base for clickable links; the internal service DNS is not
# reachable from a browser. Override with MLFLOW_PUBLIC_URL if the host differs.
UI_BASE = os.environ.get(
    "MLFLOW_PUBLIC_URL", "https://mlflow.127.0.0.1.nip.io").rstrip("/")


def _experiment_name() -> str:
    slug = os.environ.get("NHDW_PROJECT_SLUG")
    return (os.environ.get("MLFLOW_EXPERIMENT_NAME")
            or (f"/projects/{slug}" if slug else "Default"))


EXPERIMENT_NAME = _experiment_name()

mlflow.set_tracking_uri(TRACKING_URI)
try:
    # Creates the experiment on first use (with the server's proxied artifact
    # root), or resolves the existing one.
    _experiment = mlflow.set_experiment(EXPERIMENT_NAME)
except Exception as exc:                                    # noqa: BLE001
    _experiment = None
    print(f"[nhdw_mlflow] WARNING: could not reach the tracking server "
          f"({TRACKING_URI}): {exc}")


def start_run(name: str | None = None):
    """Start (and return) an MLflow run in this project's experiment. Use as a
    context manager:  `with nhdw_mlflow.start_run('run-name') as run: ...`."""
    return mlflow.start_run(run_name=name)


def run_url(run) -> str:
    """Clickable MLflow UI URL for a run (pass the object `start_run` returns)."""
    info = getattr(run, "info", run)
    return f"{UI_BASE}/#/experiments/{info.experiment_id}/runs/{info.run_id}"


def experiment_url() -> str:
    """Clickable MLflow UI URL for this project's experiment."""
    if _experiment is not None:
        return f"{UI_BASE}/#/experiments/{_experiment.experiment_id}"
    return UI_BASE


print(f"[nhdw_mlflow] tracking server : {TRACKING_URI}")
print(f"[nhdw_mlflow] experiment      : {EXPERIMENT_NAME}")
print(f"[nhdw_mlflow] MLflow UI        : {experiment_url()}")
