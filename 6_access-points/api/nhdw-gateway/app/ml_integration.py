"""Integration hook that makes an approved project's model a catalogued,
monitored model.

The project graduation flow (routes/graduation.py `approve`) calls this when a
reviewer approves a project. It performs the same steps as
`bundle/scripts/seed-ml.sh`, so the model needs no per-model UI work:

  1. Locate a runner package under the project's files in the object store (a
     folder holding manifest.yaml, train.py and feature_spec.yaml, the runner's
     package contract). No package means no-op; many projects have none.
  2. Take the model name from manifest.yaml (sanitised), prefixed with the
     project slug if it would collide with one of the bundle-seeded models.
  3. Copy the package to s3://nhdw-artifacts/ml-packages/<name>/current/, the
     location seeded and uploaded packages use, with the manifest `name`
     rewritten to the chosen name (the runner registers under manifest.name,
     so this is the registry key).
  4. Upsert ops_ml.model_deployment (one row enrols the model in the nightly
     ml_batch_predict and ml_drift_check DAGs).
  5. Bind the project's model artifact (ops_artifact.artifact.mlflow_model_name)
     so the workshop catalogue joins it.
  6. Start one ephemeral nhdw/ml-runner Job that runs, in a single pod,
     train -> predict-batch -> drift-check -> score-performance (as seed-ml.sh
     does per model). train registers the pyfunc, the @champion alias and the
     drift reference; predict-batch fills ops_ml.ml_predictions for the
     ws-model-* and bi widgets; drift and performance seed the monitoring
     baselines. The Job runs asynchronously and never blocks approval.

The widgets are model-agnostic: once an MLflow @champion, a model_deployment
row and prediction rows exist, the workshop pair (ws-models-table,
ws-model-workbench) and the admin monitoring view (adm-models-datasets: drift,
performance, stored predictions) show the model with no new widget.

This hook never raises, because the approval is already committed when it
runs. Every failure is returned as {"integrated": False, "reason": ...}, which
the graduation route passes to the caller. The runner's train step sets
@champion itself (--alias champion), so no alias call is made here.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any
from uuid import uuid4

import yaml

from . import minio_client

log = logging.getLogger("gateway.ml_integration")

ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "nhdw-artifacts")
ML_PACKAGES_PREFIX = "ml-packages"          # <bucket>/ml-packages/<name>/current/

# The bundle-seeded models (bundle/seed-pack/app/ml/deployments.yaml). A project
# model whose manifest name collides with one of these is prefixed with the
# project slug, so an approval never overwrites a seeded model's registry entry,
# deployment row or predictions.
_RESERVED_MODEL_NAMES = {"ears", "outbreak", "seasonal", "ewars_lite",
                         "stockout_forecast"}

# Weekly cron, as for the seeds (informational: the DAG itself runs nightly).
_DEFAULT_BATCH_SCHEDULE = "0 4 * * 1"

_MANIFEST_RE = re.compile(r"^manifest\.ya?ml$", re.I)
_SPEC_RE     = re.compile(r"^feature_spec\.ya?ml$", re.I)


def _sanitize_model_name(raw: str, fallback: str) -> str:
    """Coerce to the ml.py model-name charset (^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$)
    and cap at 50 chars, leaving room for the RFC 1123 job-name suffixes the
    runner appends. An empty result uses the fallback."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", (raw or "").strip())
    name = re.sub(r"^[^A-Za-z0-9]+", "", name).strip("-._")[:50].strip("-._")
    return name or fallback


def _basename(key: str) -> str:
    return key.rsplit("/", 1)[-1]


async def integrate_approved_project_model(conn, project_id: str,
                                           user) -> dict[str, Any]:
    """Integrate an approved project's model. Returns a structured status and
    never raises, because the approval has already committed."""
    try:
        return await _integrate(conn, project_id, user)
    except Exception as exc:                                       # noqa: BLE001
        # Catch-all around the whole flow. The graduation route also guards the
        # call; the never-raise contract is enforced here as well.
        log.exception("integration failed for project %s", project_id)
        return {"integrated": False, "reason": f"integration error: {exc}"[:400]}


async def _integrate(conn, project_id: str, user) -> dict[str, Any]:
    files_prefix = f"projects/{project_id}/files/"

    # 1. Locate a runner package under the project files.
    objs = await minio_client.list_prefix(prefix=files_prefix,
                                          bucket=ARTIFACTS_BUCKET)
    keys = [o["key"] for o in objs
            if o["key"] != files_prefix and not o["key"].endswith("/.keep")]
    manifests = [k for k in keys if _MANIFEST_RE.match(_basename(k))]
    no_pkg = {"integrated": False,
              "reason": "no runner package (manifest.yaml + train.py + "
                        "feature_spec.yaml) found in project files"}
    if not manifests:
        return no_pkg
    # Prefer the shallowest manifest (root before package/ before model/).
    manifest_key = min(manifests, key=lambda k: (k.count("/"), len(k)))
    pkg_prefix = manifest_key.rsplit("/", 1)[0] + "/"        # containing folder
    pkg_keys = [k for k in keys if k.startswith(pkg_prefix)]

    has_train = any(_basename(k).lower() == "train.py" for k in pkg_keys)
    has_spec  = any(_SPEC_RE.match(_basename(k)) for k in pkg_keys)
    if not (has_train and has_spec):
        return no_pkg

    # 2. Model name: sanitise, and prefix on collision with a seeded model.
    prj = await conn.fetchrow(
        "SELECT slug FROM ops_workspace.project WHERE id = $1::uuid", project_id)
    slug = (prj["slug"] if prj and prj["slug"] else f"proj-{project_id[:8]}")
    slug_name = _sanitize_model_name(slug, fallback=f"proj-{project_id[:8]}")

    raw_manifest = await minio_client.read_object(key=manifest_key,
                                                  bucket=ARTIFACTS_BUCKET)
    try:
        manifest = yaml.safe_load(raw_manifest) if raw_manifest else {}
        if not isinstance(manifest, dict):
            manifest = {}
    except Exception:                                             # noqa: BLE001
        manifest = {}

    model_name = _sanitize_model_name(manifest.get("name") or "",
                                      fallback=slug_name)
    if model_name in _RESERVED_MODEL_NAMES:
        model_name = _sanitize_model_name(f"{slug}-{model_name}",
                                          fallback=slug_name)
        log.info("project %s model name collided with a seeded model; "
                 "namespaced to %s", project_id, model_name)

    # The runner registers under manifest.name, so rewrite it to the chosen
    # name. Other runner keys get conventional defaults only when absent, so a
    # manifest that passes the metadata check but omits runner keys still
    # trains; deeper problems then show up as a failed train Job.
    manifest["name"] = model_name
    manifest.setdefault("version", "v1")
    manifest.setdefault("kind", "forecaster")
    deployment_kind = manifest.setdefault("deployment_kind", "batch")
    if deployment_kind not in ("batch", "on_demand"):
        deployment_kind = manifest["deployment_kind"] = "batch"
    new_manifest_bytes = yaml.safe_dump(manifest, sort_keys=False).encode()

    dst_prefix = f"{ML_PACKAGES_PREFIX}/{model_name}/current/"
    package_ref = f"minio://{ARTIFACTS_BUCKET}/{dst_prefix}"

    # 3. Copy the package to ml-packages/<name>/current/.
    have_model_card = False
    params_yaml: dict = {}
    for k in pkg_keys:
        rel = k[len(pkg_prefix):]
        dst = dst_prefix + rel
        bn = _basename(k).lower()
        if k == manifest_key:
            await minio_client.write_export(
                key=dst, body=new_manifest_bytes,
                content_type="application/x-yaml", bucket=ARTIFACTS_BUCKET)
        else:
            await minio_client.copy_object(
                src_key=k, dst_key=dst, bucket=ARTIFACTS_BUCKET,
                src_bucket=ARTIFACTS_BUCKET)
        if bn == "model_card.md":
            have_model_card = True
        if bn in ("params.yaml", "params.yml"):
            try:
                raw = await minio_client.read_object(key=k,
                                                     bucket=ARTIFACTS_BUCKET)
                loaded = yaml.safe_load(raw) if raw else {}
                params_yaml = loaded if isinstance(loaded, dict) else {}
            except Exception:                                     # noqa: BLE001
                params_yaml = {}
    # model_card.md is required by the runner (package.load). Write a minimal
    # one when the package has none.
    if not have_model_card:
        stub = (f"# {model_name}\n\nIntegrated from approved project "
                f"`{slug}` ({project_id}). Model card not supplied by the "
                f"package author.\n")
        await minio_client.write_export(
            key=dst_prefix + "model_card.md", body=stub.encode(),
            content_type="text/markdown", bucket=ARTIFACTS_BUCKET)

    # 4. Upsert ops_ml.model_deployment.
    # params carries package_ref so retraining and drift-triggered refits find
    # the package, plus a provenance note. Pass a dict, not a JSON string: the
    # ops_db pool's jsonb codec already encodes with json.dumps, so a string
    # would be stored as a jsonb string, and the runner's read_deployment_params
    # accepts only a jsonb object. This matches what seed-ml.sh writes.
    dep_params = {"package_ref": package_ref, "source_project_id": project_id}
    schedule = _DEFAULT_BATCH_SCHEDULE if deployment_kind == "batch" else None
    await conn.execute(
        """
        INSERT INTO ops_ml.model_deployment
            (model_name, model_alias, deployment_kind, schedule, enabled, params)
        VALUES ($1, 'champion', $2, $3, true, $4)
        ON CONFLICT (model_name) DO UPDATE SET
            model_alias     = 'champion',
            deployment_kind = EXCLUDED.deployment_kind,
            schedule        = EXCLUDED.schedule,
            enabled         = true,
            params          = EXCLUDED.params,
            updated_at      = now();
        """,
        model_name, deployment_kind, schedule, dep_params)

    # 5. Bind the project's model artifact to the MLflow model name.
    artifact_bound = 0
    try:
        res = await conn.execute(
            """
            UPDATE ops_artifact.artifact
               SET mlflow_model_name = $1, updated_at = now()
             WHERE project_id = $2::uuid AND kind = 'model'
               AND (mlflow_model_name IS NULL OR mlflow_model_name <> $1);
            """,
            model_name, project_id)
        artifact_bound = int(res.rsplit(" ", 1)[-1]) if res else 0
    except Exception as exc:                                       # noqa: BLE001
        log.warning("artifact mlflow_model_name bind failed for %s: %s",
                    project_id, exc)

    # 6. Start one chained runner Job: train -> predict -> drift -> score.
    # train and predict-batch are required (&&); drift and score-performance
    # are best-effort baselines (|| true), since a model whose manifest has no
    # actuals block scores 0 rows. Same per-model sequence as seed-ml.sh; runs
    # asynchronously so approval never blocks.
    job_name = None
    job_err = None
    try:
        job_name = _spawn_integration_job(model_name, package_ref, user)
    except Exception as exc:                                       # noqa: BLE001
        log.exception("integration runner Job spawn failed for %s", model_name)
        job_err = str(exc)[:300]

    steps = {
        "package": True,
        "deployment": True,
        "artifact_bound": artifact_bound,
        "train_job": job_name,
        "predict": "chained" if job_name else "not-started",
        "drift": "chained-best-effort" if job_name else "not-started",
        "performance": "chained-best-effort" if job_name else "not-started",
    }
    out: dict[str, Any] = {
        "integrated": bool(job_name),
        "model_name": model_name,
        "package_ref": package_ref,
        "deployment_kind": deployment_kind,
        "steps": steps,
        "note": ("train -> predict-batch -> drift-check -> score-performance run "
                 "sequentially in one runner Job; the model appears in the "
                 "catalogue with @champion + predictions once it completes "
                 "(~1-3 min). The nightly ml_batch_predict + ml_drift_check "
                 "DAGs maintain it thereafter."),
    }
    if job_err:
        # Package, deployment row and artifact binding are in place, but the
        # model does not exist until a runner Job completes.
        out["integrated"] = False
        out["reason"] = (f"package + deployment wired, but runner Job spawn "
                         f"failed: {job_err}")
    return out


def _spawn_integration_job(model_name: str, package_ref: str, user) -> str:
    """Create one ephemeral nhdw/ml-runner Job whose single container chains
    the four verbs in a shell. Reuses `_runner_env`, `_k8s`, `_namespace` and
    `ML_RUNNER_IMAGE` from routes/ml.py so the secret and image contract has
    one source; the single-verb `_create_runner_job` cannot express a chain.
    Returns the Job name.

    routes/ml.py is imported lazily to avoid import-time coupling with the
    routes package (graduation.py imports this module at load time)."""
    from .routes import ml as mlr

    k8sc = mlr._k8s()
    ns = mlr._namespace()
    runner = "python3 /app/runner.py"
    mn = shlex.quote(model_name)
    ref = shlex.quote(package_ref)
    chain = (
        f"set -e; "
        f"{runner} train {ref} --alias champion --trigger api && "
        f"{runner} predict-batch {mn} --trigger api && "
        f"{{ {runner} drift-check {mn} --no-retrain-on-drift || true; }} && "
        f"{{ {runner} score-performance {mn} || true; }}"
    )

    job_name = f"ml-integrate-{mlr._sanitize_dns(model_name, 24)}-{uuid4().hex[:6]}"[:63].strip("-")
    labels = {"app": "ml-runner", "tier": "ml",
              "nhdw.io/mode": "integrate", "nhdw.io/model": model_name,
              "nhdw.io/created-by": "nhdw-gateway"}
    annotations = {"nhdw.io/triggered-by": getattr(user, "username", "") or "",
                   "nhdw.io/triggered-from": "graduation-approve",
                   "nhdw.io/model": model_name,
                   "nhdw.io/package-ref": package_ref}
    meta = k8sc.V1ObjectMeta(name=job_name, namespace=ns, labels=labels,
                             annotations=annotations)
    container = k8sc.V1Container(
        name="runner", image=mlr.ML_RUNNER_IMAGE, image_pull_policy="IfNotPresent",
        command=["/bin/sh", "-c"], args=[chain], env=mlr._runner_env(k8sc))
    job = k8sc.V1Job(
        metadata=meta,
        spec=k8sc.V1JobSpec(
            backoff_limit=0,
            ttl_seconds_after_finished=86400,
            template=k8sc.V1PodTemplateSpec(
                metadata=k8sc.V1ObjectMeta(labels=labels, annotations=annotations),
                spec=k8sc.V1PodSpec(restart_policy="Never",
                                    containers=[container]))))
    k8sc.BatchV1Api().create_namespaced_job(ns, job)
    log.info("integration Job %s spawned for model %s", job_name, model_name)
    return job_name
