#!/usr/bin/env python3
"""nhdw/ml-runner: the single execution primitive for all NHDW model code.

Every model (seed, community upload, external import) executes through an
ephemeral Job running this CLI. The main verbs:

  train <package-ref> [--alias champion] [--run-label L] [--trigger manual]
      package-ref = minio://nhdw-artifacts/ml-packages/<name>/<vN>/  (prefix
      of files, or a single tarball) or a mounted directory, e.g. /seed/<name>.
      Fetch -> pip-delta extras -> exec train.py per the package contract ->
      log pyfunc + signature to experiment /models/<name> -> register version ->
      set alias (@champion on the seed path) -> log the reference feature
      profile (drift contract) -> ops_ml.ml_run row.

  predict-batch <model_name> [--params '{}'] [--alias champion] [--trigger schedule]
      Resolve models:/<name>@champion -> run the package feature_spec.yaml
      query (logged with the model at train time) against the warehouse ->
      predict -> validate against the flat forecast schema (alarm_flag derived
      from alarm_prob >= 0.5) -> DELETE+INSERT by run_id into
      ops_ml.ml_predictions -> close the ml_run row.

  predict-adhoc <model_name> --input <minio uri csv> --output <minio uri csv>
      Staged CSV in -> signature-validate -> predict -> CSV out to object-store
      staging + ml_run row. Nothing is written to ops_ml.ml_predictions.

The runner owns all DB/MLflow/object-store I/O; package code never sees
credentials beyond the query results. Env contract: see README.md (reuses the
existing k8s secrets; no new secret material).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import mlflow.pyfunc
import pandas as pd
import yaml

from lib import db, drift, minio_io, mlflow_io, schemas
from lib import package as package_mod
from lib import profile as profile_mod

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s | %(message)s")
log = logging.getLogger("runner")


# train

def _train(package_ref: str, alias: str | None, trigger: str,
           run_label: str | None) -> int:
    """Fetch a package, train, log + register, set the alias, log the drift
    reference profile, record the ml_run. Shared by the `train` verb and the
    drift-triggered retrain so refit is generic, never per-model code."""
    t0 = time.perf_counter()
    mlflow_io.init()

    pkg_dir = package_mod.fetch(package_ref)
    pkg = package_mod.load(pkg_dir)
    run_label = run_label or f"train:{pkg.name}:{pkg.version}"

    try:
        package_mod.install_extras(pkg)

        spec_input = pkg.feature_spec["input"]
        df = db.fetch_df(spec_input["query"])
        if df.empty:
            raise RuntimeError(f"feature query for {pkg.name} returned 0 rows; "
                               "is the input mart populated?")
        df = schemas.validate_input_spec(df, spec_input["signature"])

        train_module = package_mod.import_train(pkg)
        params = dict(pkg.params)
        log.info("training %s on %d rows (params: %s)", pkg.name, len(df), params)
        model = train_module.train(df, params)
        if not isinstance(model, mlflow.pyfunc.PythonModel):
            raise TypeError(f"{pkg.name}/train.py returned {type(model).__name__}; "
                            "the contract requires an mlflow.pyfunc.PythonModel subclass")

        # One direct predict: proves the model runs and yields the output
        # frame the MLflow signature is inferred from. Batch models must
        # already emit the flat forecast schema here: fail at train time,
        # not on the first scheduled run.
        sample_out = mlflow_io.call_predict(model, df,
                                            package_mod.scalar_params(params) or None)
        if pkg.deployment_kind == "batch":
            schemas.validate_flat_output(sample_out, pkg.name, pkg.version)

        reg = mlflow_io.log_and_register(
            pkg, model, train_module, df, sample_out, params,
            run_label=run_label, alias=alias or None)

        ml_run_id = db.record_run(
            model_name=pkg.name, model_version=reg["version"],
            deployment_kind=pkg.deployment_kind, trigger_kind=trigger,
            run_label=run_label, input_filters={"package_ref": package_ref,
                                                "params": params},
            status="success", row_count=len(df),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            mlflow_run_id=reg["mlflow_run_id"],
            reference_profile_uri=reg["reference_profile_uri"])

        # Model-provided backtest rows (optional, generic): a package that
        # evaluates itself may expose `performance_rows` (one entry per
        # evaluation window with its metrics), and they land in
        # ops_ml.model_performance under the new version, so marts can read the
        # evidence the registered version was selected on. Backtest metrics are
        # named `backtest_*` by the package, never mistakable for live scoring.
        for w in (getattr(model, "performance_rows", None) or []):
            db.write_performance(pkg.name, reg["version"], w["window_start"],
                                 w["window_end"], w["metrics"],
                                 run_id=ml_run_id, sample_size=w.get("sample_size"))
        log.info("train done: %s version %s (@%s)", pkg.name, reg["version"],
                 alias or "-")
        return 0
    except Exception as exc:
        log.exception("train failed for %s", pkg.name)
        db.record_run(
            model_name=pkg.name, model_version="n/a",
            deployment_kind=pkg.deployment_kind, trigger_kind=trigger,
            run_label=run_label, input_filters={"package_ref": package_ref},
            status="failed", row_count=None,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error_msg=str(exc)[:4000])
        return 1


def cmd_train(args) -> int:
    return _train(args.package_ref, args.alias, args.trigger, args.run_label)


# predict-batch

def cmd_predict_batch(args) -> int:
    t0 = time.perf_counter()
    mlflow_io.init()
    name = args.model_name

    mv = mlflow_io.resolve(name, args.alias)

    spec_path = mlflow_io.download_run_artifact(mv.run_id, "package/feature_spec.yaml")
    if spec_path is None:
        raise SystemExit(f"models:/{name}@{args.alias} (v{mv.version}) has no "
                         "package/feature_spec.yaml artifact; was it trained "
                         "by the runner?")
    spec_input = (yaml.safe_load(spec_path.read_text()) or {}).get("input") or {}

    defaults_path = mlflow_io.download_run_artifact(mv.run_id, "package/params.yaml")
    defaults = (yaml.safe_load(defaults_path.read_text()) or {}) if defaults_path else {}
    cli_params = json.loads(args.params) if args.params else {}
    params = {**package_mod.scalar_params(defaults), **cli_params}

    run_id = db.open_run(name, mv.version, "batch", args.trigger,
                         run_label=f"batch:{name}:{mv.version}",
                         input_filters=params, mlflow_run_id=mv.run_id)
    try:
        df = db.fetch_df(spec_input["query"])
        if df.empty:
            log.warning("%s: feature query returned 0 rows; closing run empty", name)
            db.close_run(run_id, "success", row_count=0,
                         latency_ms=int((time.perf_counter() - t0) * 1000))
            return 0
        if spec_input.get("signature"):
            df = schemas.validate_input_spec(df, spec_input["signature"])

        model = mlflow_io.load_pyfunc(name, args.alias)
        out = model.predict(df, params=params or None)
        records = schemas.validate_flat_output(out, name, mv.version)
        written = db.write_predictions(run_id, records)
        db.close_run(run_id, "success", row_count=written,
                     latency_ms=int((time.perf_counter() - t0) * 1000))
        log.info("predict-batch done: %s v%s, %d rows", name, mv.version, written)
        return 0
    except Exception as exc:
        log.exception("predict-batch failed for %s", name)
        db.close_run(run_id, "failed", error_msg=str(exc)[:4000],
                     latency_ms=int((time.perf_counter() - t0) * 1000))
        return 1


# predict-adhoc

def cmd_predict_adhoc(args) -> int:
    t0 = time.perf_counter()
    mlflow_io.init()
    name = args.model_name

    mv = mlflow_io.resolve(name, args.alias)
    model = mlflow_io.load_pyfunc(name, args.alias)

    workdir = Path(tempfile.mkdtemp(prefix="nhdw-adhoc-"))
    local_in = minio_io.download(args.input, workdir / "input.csv")
    df = pd.read_csv(local_in)
    df = schemas.coerce_to_model_schema(df, model.metadata.get_input_schema())

    run_id = db.open_run(name, mv.version, "on_demand", args.trigger,
                         run_label=f"adhoc:{name}:{mv.version}",
                         input_filters={"input": args.input, "output": args.output},
                         mlflow_run_id=mv.run_id)
    try:
        out = model.predict(df)
        if not isinstance(out, pd.DataFrame):
            out = pd.DataFrame(out)
        local_out = workdir / "output.csv"
        out.to_csv(local_out, index=False)
        minio_io.upload(local_out, args.output)
        db.close_run(run_id, "success", row_count=len(out),
                     latency_ms=int((time.perf_counter() - t0) * 1000))
        log.info("predict-adhoc done: %s v%s, %d rows written to %s", name, mv.version,
                 len(out), args.output)
        return 0
    except Exception as exc:
        log.exception("predict-adhoc failed for %s", name)
        db.close_run(run_id, "failed", error_msg=str(exc)[:4000],
                     latency_ms=int((time.perf_counter() - t0) * 1000))
        return 1


# drift-check

def _feature_frame(mv, model_name: str) -> pd.DataFrame:
    """Re-run the champion's logged feature_spec query -> coerced frame (the
    exact shape train() profiled). Shared by drift + performance scoring."""
    spec_path = mlflow_io.download_run_artifact(mv.run_id, "package/feature_spec.yaml")
    if spec_path is None:
        raise SystemExit(f"{model_name} v{mv.version} has no package/feature_spec.yaml "
                         "artifact; was it trained by the runner?")
    spec_input = (yaml.safe_load(spec_path.read_text()) or {}).get("input") or {}
    df = db.fetch_df(spec_input["query"])
    if not df.empty and spec_input.get("signature"):
        df = schemas.validate_input_spec(df, spec_input["signature"])
    return df


def cmd_drift_check(args) -> int:
    """Generic per-feature drift: PSI/KS of the current feature frame vs the
    model's training reference profile -> ops_ml.model_drift. On PSI-breach,
    fire a generic drift-triggered retrain (refit @<alias>). Model-agnostic:
    the same code runs for every deployed model."""
    mlflow_io.init()
    name = args.model_name
    mv = mlflow_io.resolve(name, args.alias)

    # Drift rows attach to the latest predictions run; never open a fresh
    # ml_run (a monitoring run must not outrank the predict run in the
    # latest-successful-run reads that feed the forecast widgets).
    pr = db.latest_predict_run(name)
    if pr is None:
        log.warning("%s: no successful predictions run yet; skipping drift "
                    "(runs after the first predict-batch)", name)
        return 0
    run_id, _ = pr

    df = _feature_frame(mv, name)
    if df.empty:
        log.warning("%s: feature query returned 0 rows; skipping drift", name)
        return 0

    prof_path = mlflow_io.download_run_artifact(mv.run_id, mlflow_io.PROFILE_ARTIFACT)
    note = None
    bootstrapped = False
    if prof_path is None:
        # No baseline (model registered before the drift contract / imported
        # outside the runner): bootstrap one from this batch, labelled
        # 'first-seen'. PSI is ~0 by construction; no retrain this pass.
        reference = profile_mod.build_profile(df)
        mlflow_io.log_reference_profile(mv.run_id, reference)
        note = "first-seen"
        bootstrapped = True
        log.info("%s: no reference profile; bootstrapped a first-seen baseline", name)
    else:
        reference = json.loads(prof_path.read_text())

    records = drift.compare(reference, df, threshold=args.psi_threshold)
    db.write_drift(run_id, name, records, psi_threshold=args.psi_threshold, note=note)
    summary = drift.summarize(records)
    log.info("drift %s: %d features · max_psi=%.4f · drifted=%s", name,
             summary["features"], summary["max_psi"], summary["drifted_features"])

    if not bootstrapped and summary["n_drifted"] > 0 and args.retrain_on_drift:
        params = db.read_deployment_params(name)
        package_ref = (params.get("package_ref")
                       or f"minio://nhdw-artifacts/ml-packages/{name}/current/")
        log.warning("drift %s: PSI breach on %s; triggering retrain from %s",
                    name, summary["drifted_features"], package_ref)
        rc = _train(package_ref, alias=args.alias, trigger="schedule",
                    run_label=f"drift-retrain:{name}")
        if rc != 0:
            log.error("drift-triggered retrain FAILED for %s", name)
            return rc
        log.info("drift %s: retrain complete; fresh @%s + reference profile",
                 name, args.alias)
    return 0


# score-performance

def _iso_week_date(year, week) -> date | None:
    try:
        return date.fromisocalendar(int(year), min(int(week), 52), 1)
    except Exception:
        return None


def cmd_score_performance(args) -> int:
    """Optional actuals scorer: join the model's latest predictions against the
    declarative manifest `actuals:` block (mart + value_col + join_keys) ->
    MAE / MAPE / 95%-interval coverage in ops_ml.model_performance. Models with
    no actuals block are skipped (exit 0)."""
    mlflow_io.init()
    name = args.model_name
    mv = mlflow_io.resolve(name, args.alias)

    man_path = mlflow_io.download_run_artifact(mv.run_id, "package/manifest.yaml")
    manifest = (yaml.safe_load(man_path.read_text()) or {}) if man_path else {}
    actuals = manifest.get("actuals")
    if not actuals or not actuals.get("mart") or not actuals.get("join_keys"):
        log.info("%s: no actuals block; skipping performance scoring", name)
        return 0

    pr = db.latest_predict_run(name)
    if pr is None:
        log.warning("%s: no successful predictions run; skipping scoring", name)
        return 0
    run_id, version = pr
    preds = db.fetch_predictions_df(run_id, name)
    if preds.empty:
        log.warning("%s: latest run has 0 predictions; skipping scoring", name)
        return 0

    jk = actuals["join_keys"]                       # flat-key -> mart column
    keys = [k for k in ("district", "disease", "year", "week") if k in jk]
    value_col = actuals["value_col"]
    select = ", ".join(f"{jk[k]} AS {k}" for k in keys) + f", {value_col} AS actual"
    actual_df = db.fetch_df(f"SELECT {select} FROM {actuals['mart']}")
    if actual_df.empty:
        log.warning("%s: actuals mart %s returned 0 rows; skipping",
                    name, actuals["mart"])
        return 0

    # Align key dtypes on both sides, then inner-join (only weeks with both a
    # prediction and a ground-truth actual are scored).
    for frame in (preds, actual_df):
        for k in keys:
            if k in ("year", "week"):
                frame[k] = pd.to_numeric(frame[k], errors="coerce").astype("Int64")
            else:
                frame[k] = frame[k].astype(str).str.strip()
    actual_df["actual"] = pd.to_numeric(actual_df["actual"], errors="coerce")
    merged = preds.merge(actual_df, on=keys, how="inner").dropna(subset=["actual"])
    merged = merged[pd.to_numeric(merged["pred_cases_mean"], errors="coerce").notna()]
    n = int(len(merged))
    if n == 0:
        log.warning("%s: predictions and actuals do not overlap; nothing to score", name)
        return 0

    pred = pd.to_numeric(merged["pred_cases_mean"], errors="coerce").astype(float)
    act = merged["actual"].astype(float)
    abs_err = (pred - act).abs()
    mae = float(abs_err.mean())
    mape = float((abs_err / act.abs().clip(lower=1.0)).mean() * 100.0)

    lwr = pd.to_numeric(merged["pred_cases_lwr95"], errors="coerce")
    upr = pd.to_numeric(merged["pred_cases_upr95"], errors="coerce")
    band = merged.assign(lwr=lwr, upr=upr).dropna(subset=["lwr", "upr"])
    coverage = (float(((band["actual"] >= band["lwr"]) &
                       (band["actual"] <= band["upr"])).mean())
                if not band.empty else None)

    dates = [d for d in (_iso_week_date(y, w)
                         for y, w in zip(merged["year"], merged["week"])) if d]
    window_start = min(dates) if dates else None
    window_end = max(dates) if dates else None

    metrics = {"mae": mae, "mape": mape, "coverage95": coverage}
    db.write_performance(name, version, window_start, window_end, metrics,
                         run_id=run_id, sample_size=n)
    log.info("performance %s: n=%d · MAE=%.3f · MAPE=%.1f%% · cov95=%s",
             name, n, mae, mape, "n/a" if coverage is None else f"{coverage:.2f}")
    return 0


# probe-lake (change-gate for pipeline_refresh)

def cmd_probe_lake(args) -> int:
    """Fingerprint the raw lake so the pipeline_refresh DAG can gate the costly
    dbt rebuild on real change. Reads every `_manifest.json` sidecar under the
    raw bucket and rolls up, per source, the max watermark + row/byte/file
    totals. Emits JSON on stdout and to the Airflow XCom sidecar path when
    present (do_xcom_push). Model-agnostic; touches no model code."""
    import hashlib

    bucket = args.bucket
    s3 = minio_io.client()
    sources: dict[str, dict] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("_manifest.json"):
                continue
            source = key.split("/", 1)[0]
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            try:
                m = json.loads(body)
            except Exception:                       # skip an unreadable sidecar
                continue
            agg = sources.setdefault(source, {"watermark": None, "rows": 0,
                                              "bytes": 0, "manifests": 0})
            wm = m.get("watermark_advanced_to") or m.get("watermark_used")
            if wm and (agg["watermark"] is None or str(wm) > str(agg["watermark"])):
                agg["watermark"] = str(wm)
            agg["rows"] += int(m.get("row_count") or 0)
            agg["bytes"] += int(m.get("bytes_written") or 0)
            agg["manifests"] += 1

    canonical = json.dumps(sources, sort_keys=True)
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    result = {"bucket": bucket, "sources": sources,
              "fingerprint": fingerprint,
              "computed_at": datetime.now(timezone.utc).isoformat()}
    out = json.dumps(result, indent=2, sort_keys=True)
    print(out)
    xcom = Path("/airflow/xcom/return.json")
    if xcom.parent.exists():
        xcom.write_text(json.dumps(result))
    log.info("probe-lake: %d source(s), fingerprint=%s", len(sources), fingerprint[:12])
    return 0


# CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ml-runner", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)

    t = sub.add_parser("train", help="fetch a package, train, register, alias")
    t.add_argument("package_ref",
                   help="minio://nhdw-artifacts/ml-packages/<name>/<vN>/ or a "
                        "mounted dir like /seed/<name>")
    t.add_argument("--alias", default="champion",
                   help="registry alias to set on the new version "
                        "(default champion, the seed path; pass '' to skip)")
    t.add_argument("--run-label", default=None)
    t.add_argument("--trigger", default="manual",
                   choices=["schedule", "manual", "api"])
    t.set_defaults(fn=cmd_train)

    b = sub.add_parser("predict-batch",
                       help="champion model, warehouse query, flat predictions")
    b.add_argument("model_name")
    b.add_argument("--params", default=None,
                   help="JSON overrides merged over the package params.yaml")
    b.add_argument("--alias", default="champion")
    b.add_argument("--trigger", default="schedule",
                   choices=["schedule", "manual", "api"])
    b.set_defaults(fn=cmd_predict_batch)

    a = sub.add_parser("predict-adhoc",
                       help="staged CSV in, predict, CSV out to the object store")
    a.add_argument("model_name")
    a.add_argument("--input", required=True, help="minio://bucket/key.csv")
    a.add_argument("--output", required=True, help="minio://bucket/key.csv")
    a.add_argument("--alias", default="champion")
    a.add_argument("--trigger", default="api",
                   choices=["schedule", "manual", "api"])
    a.set_defaults(fn=cmd_predict_adhoc)

    d = sub.add_parser("drift-check",
                       help="per-feature PSI/KS vs the training reference "
                            "profile, written to ops_ml.model_drift (retrains on breach)")
    d.add_argument("model_name")
    d.add_argument("--alias", default="champion")
    d.add_argument("--psi-threshold", type=float, default=drift.DEFAULT_PSI_THRESHOLD,
                   help="PSI at/above which a feature is 'drifted' (default 0.2)")
    d.add_argument("--retrain-on-drift", dest="retrain_on_drift",
                   action="store_true", default=True,
                   help="refit @<alias> when any feature breaches (default on)")
    d.add_argument("--no-retrain-on-drift", dest="retrain_on_drift",
                   action="store_false",
                   help="write drift rows only; never trigger a retrain")
    d.set_defaults(fn=cmd_drift_check)

    s = sub.add_parser("score-performance",
                       help="join the latest predictions with the manifest actuals; "
                            "MAE/MAPE/coverage in ops_ml.model_performance")
    s.add_argument("model_name")
    s.add_argument("--alias", default="champion")
    s.set_defaults(fn=cmd_score_performance)

    pl = sub.add_parser("probe-lake",
                        help="fingerprint raw-lake manifests (pipeline_refresh "
                             "change-gate); JSON on stdout and XCom")
    pl.add_argument("--bucket", default="nhdw-raw")
    pl.set_defaults(fn=cmd_probe_lake)
    return p


if __name__ == "__main__":
    ns = build_parser().parse_args()
    sys.exit(ns.fn(ns))
