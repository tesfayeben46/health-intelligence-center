"""MLflow I/O for the ml-runner: tracking, registry, artifacts, aliases.

Registry conventions:
  * experiment            /models/<name>
  * registered model      <name>  (the registry key widgets + gateway use)
  * aliases, not stages   @champion serves; @challenger is the graduation slot
  * every trained version carries the package files as run artifacts under
    package/ (feature_spec.yaml drives predict-batch later) and the drift
    reference profile under drift/reference_profile.json.

Env: MLFLOW_TRACKING_URI (alias MLFLOW_URL); the default is the in-cluster
service DNS name.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import tempfile
import types
from pathlib import Path

import cloudpickle
import mlflow
import mlflow.pyfunc
from mlflow import MlflowClient
from mlflow.models import infer_signature

from lib import package as package_mod
from lib import profile as profile_mod

log = logging.getLogger("runner.mlflow")

DEFAULT_TRACKING_URI = "http://mlflow.nhdw.svc.cluster.local:5000"

# What the runner image bakes (keep in sync with ../Dockerfile). Logged as the
# model's pip_requirements so MLflow never guesses (guessing hits the network).
BAKED_REQUIREMENTS = [
    "mlflow==2.18.0",
    "statsmodels==0.14.2",
    "scikit-learn==1.5.2",
    "pandas==2.2.3",
    "numpy==1.26.4",
    "scipy==1.14.1",
]

PACKAGE_ARTIFACT_DIR = "package"
PROFILE_ARTIFACT = "drift/reference_profile.json"


def init() -> None:
    uri = (os.environ.get("MLFLOW_TRACKING_URI")
           or os.environ.get("MLFLOW_URL")
           or DEFAULT_TRACKING_URI)
    mlflow.set_tracking_uri(uri)
    log.info("MLflow tracking URI: %s", uri)


def call_predict(model, df, params: dict | None = None):
    """Invoke a PythonModel instance directly (pre-logging), tolerating both
    predict(self, ctx, df) and predict(self, ctx, df, params=None) shapes."""
    import inspect
    sig = inspect.signature(model.predict)
    if "params" in sig.parameters:
        return model.predict(None, df, params=params)
    return model.predict(None, df)


def log_and_register(pkg, model, train_module: types.ModuleType,
                     input_df, output_df, effective_params: dict,
                     run_label: str, alias: str | None) -> dict:
    """Log the trained pyfunc + signature + package artifacts + reference
    profile, register a new version, set the alias. Returns
    {version, mlflow_run_id, reference_profile_uri}.
    """
    name = pkg.name

    # train.py's classes live in a module that only exists inside this pod, so
    # pickle them by value so load_model works anywhere without the source.
    cloudpickle.register_pickle_by_value(train_module)

    params_schema = package_mod.scalar_params(effective_params) or None
    signature = infer_signature(input_df.head(100), output_df.head(100),
                                params=params_schema)

    mlflow.set_experiment(f"/models/{name}")
    with mlflow.start_run(run_name=run_label) as run:
        mlflow.log_params({k: str(v)[:250] for k, v in effective_params.items()})
        mlflow.set_tags({
            "nhdw.package_version": pkg.version,
            "nhdw.kind": pkg.manifest.get("kind", ""),
            "nhdw.deployment_kind": pkg.deployment_kind,
            "nhdw.engine": pkg.manifest.get("engine", ""),
            "nhdw.provenance": pkg.manifest.get("provenance", ""),
        })
        mlflow.log_metric("training_rows", len(input_df))

        # Model-provided evidence (optional and generic, never per-model code):
        # a package that evaluates itself exposes `metrics` (flat numbers) and
        # `evaluation` (named tables) on the returned model, and both land inside
        # this run, so the registered version carries its own evidence. Models
        # without them are unaffected.
        metrics = getattr(model, "metrics", None)
        if isinstance(metrics, dict):
            clean = {str(k): float(v) for k, v in metrics.items()
                     if isinstance(v, (int, float)) and not isinstance(v, bool)
                     and math.isfinite(float(v))}
            if clean:
                mlflow.log_metrics(clean)
        evaluation = getattr(model, "evaluation", None)
        if isinstance(evaluation, dict):
            for tname, table in evaluation.items():
                mlflow.log_dict({"rows": table} if isinstance(table, list) else table,
                                f"evaluation/{tname}.json")
        query = ((pkg.feature_spec or {}).get("input") or {}).get("query")
        if query:
            mlflow.set_tag("nhdw.feature_query_sha256",
                           hashlib.sha256(" ".join(query.split()).encode()).hexdigest()[:16])

        # The package files ride with the run: predict-batch pulls
        # package/feature_spec.yaml + package/params.yaml from the champion
        # version's run, and the gateway serves package/model_card.md.
        for fname in ("manifest.yaml", "feature_spec.yaml", "params.yaml",
                      "model_card.md", "requirements.txt", "train.py",
                      "forecast_contract.yaml"):
            fpath = pkg.root / fname
            if fpath.exists():
                mlflow.log_artifact(str(fpath), artifact_path=PACKAGE_ARTIFACT_DIR)

        # The package's committed EDA rides with the run too.
        if (pkg.root / "eda").is_dir():
            mlflow.log_artifacts(str(pkg.root / "eda"), artifact_path=f"{PACKAGE_ARTIFACT_DIR}/eda")

        # Reference feature profile (drift contract).
        reference_profile = profile_mod.build_profile(input_df)
        mlflow.log_dict(reference_profile, PROFILE_ARTIFACT)
        profile_uri = mlflow.get_artifact_uri(PROFILE_ARTIFACT)

        extras = []
        if pkg.requirements.exists():
            extras = [ln.strip() for ln in pkg.requirements.read_text().splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]
        mlflow.pyfunc.log_model(
            artifact_path="model",
            python_model=model,
            signature=signature,
            input_example=input_df.head(5),
            registered_model_name=name,
            pip_requirements=BAKED_REQUIREMENTS + extras,
        )
        mlflow_run_id = run.info.run_id

    client = MlflowClient()
    versions = [mv for mv in client.search_model_versions(f"name='{name}'")
                if mv.run_id == mlflow_run_id]
    if not versions:
        raise RuntimeError(f"registration failed: no model version for run {mlflow_run_id}")
    version = max(versions, key=lambda mv: int(mv.version)).version

    client.update_registered_model(name, description=pkg.manifest.get("description", ""))
    for tag_key in ("kind", "deployment_kind", "engine", "provenance"):
        client.set_model_version_tag(name, version, tag_key,
                                     str(pkg.manifest.get(tag_key, "")))
    if alias:
        client.set_registered_model_alias(name, alias, version)
        log.info("registered %s v%s and set alias @%s", name, version, alias)
    else:
        log.info("registered %s v%s (no alias set)", name, version)

    return {"version": version, "mlflow_run_id": mlflow_run_id,
            "reference_profile_uri": profile_uri}


def log_reference_profile(mlflow_run_id: str, reference_profile: dict) -> str:
    """Attach a drift reference profile to an existing run (drift bootstrap).

    Used when a deployed model has no `drift/reference_profile.json` , e.g. a
    model registered before the drift contract, or imported outside the runner.
    The first drift-check builds a baseline from the current batch and logs it
    here so every subsequent run has a stable comparison ('first-seen')."""
    import json
    tmp = Path(tempfile.mkdtemp(prefix="nhdw-refprof-")) / "reference_profile.json"
    tmp.write_text(json.dumps(reference_profile))
    MlflowClient().log_artifact(mlflow_run_id, str(tmp), artifact_path="drift")
    uri = f"runs:/{mlflow_run_id}/{PROFILE_ARTIFACT}"
    log.info("bootstrapped reference profile at %s", uri)
    return uri


def resolve(name: str, alias: str = "champion"):
    """models:/<name>@<alias> -> ModelVersion (raises if the alias is unset)."""
    mv = MlflowClient().get_model_version_by_alias(name, alias)
    log.info("resolved models:/%s@%s to version %s (run %s)", name, alias,
             mv.version, mv.run_id)
    return mv


def load_pyfunc(name: str, alias: str = "champion"):
    return mlflow.pyfunc.load_model(f"models:/{name}@{alias}")


def download_run_artifact(mlflow_run_id: str, artifact_path: str) -> Path | None:
    """Fetch one artifact from a run; None if it doesn't exist."""
    dest = tempfile.mkdtemp(prefix="nhdw-art-")
    try:
        local = mlflow.artifacts.download_artifacts(
            run_id=mlflow_run_id, artifact_path=artifact_path, dst_path=dest)
        return Path(local)
    except Exception as exc:                       # noqa: BLE001 (absence is legitimate)
        log.warning("artifact %s not found on run %s (%s)", artifact_path,
                    mlflow_run_id, exc)
        return None
