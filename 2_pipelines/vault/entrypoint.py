#!/usr/bin/env python3
"""dbt entrypoint wrapper that runs dbt then uploads target/ artifacts to the object store (SeaweedFS S3).

Behaves identically to the bare `dbt` ENTRYPOINT for any args (forwards them
straight through). Then, if `dbt build`/`dbt run`/`dbt test` succeeded and
the MINIO_* object-store env vars are present, it uploads the dbt target artifacts to
nhdw-artifacts/dbt/<project>/ for the gateway's /api/dq/* endpoints to read.

Exits with the same status as the wrapped dbt command. The upload is
best-effort: a failed upload logs a warning but doesn't fail the Job; the
lineage view shows "stale" until a later upload completes.
"""
from __future__ import annotations
import os
import pathlib
import subprocess
import sys


def main() -> int:
    rc = subprocess.call(["dbt", *sys.argv[1:]])
    if rc != 0:
        return rc

    project = os.environ.get("DBT_PROJECT", "")
    target  = pathlib.Path(os.environ.get("DBT_TARGET_DIR", "/dbt/target"))
    endpoint = os.environ.get("MINIO_ENDPOINT")
    bucket   = os.environ.get("MINIO_ARTIFACTS_BUCKET", "nhdw-artifacts")
    if not (project and endpoint):
        print("[entrypoint] DBT_PROJECT or MINIO_ENDPOINT unset; skipping upload")
        return rc

    try:
        import boto3
    except ImportError:
        print("[entrypoint] boto3 not installed; skipping upload", file=sys.stderr)
        return rc

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        region_name="us-east-1",
    )
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)

    n = 0
    for fname in ("manifest.json", "run_results.json", "catalog.json",
                  "sources.json", "graph_summary.json"):
        f = target / fname
        if not f.exists():
            continue
        key = f"dbt/{project}/{fname}"
        try:
            s3.upload_file(str(f), bucket, key)
            print(f"[entrypoint] uploaded s3://{bucket}/{key} "
                  f"({f.stat().st_size//1024} KiB)")
            n += 1
        except Exception as exc:                                # noqa: BLE001
            print(f"[entrypoint] upload failed {key}: {exc}", file=sys.stderr)
    print(f"[entrypoint] dbt-artifacts upload done: {n} files")

    # dbt docs generate writes to the shared dbt-docs-content PVC, so the
    # dbt-docs surface renders the full lineage DAG. Best-effort; PVC mounted at
    # DBT_DOCS_OUT (skipped when empty). Static site: index.html, manifest and catalog.
    docs_out = os.environ.get("DBT_DOCS_OUT")
    if docs_out and project and sys.argv[1:2] and sys.argv[1] in ("build", "run", "docs"):
        import shutil
        try:
            subprocess.call(["dbt", "docs", "generate", "--target",
                             os.environ.get("DBT_TARGET", "dev")])
            dest = pathlib.Path(docs_out) / project / "target"
            dest.mkdir(parents=True, exist_ok=True)
            for fn in ("index.html", "manifest.json", "catalog.json", "graph.gpickle"):
                src = target / fn
                if src.exists():
                    shutil.copy2(src, dest / fn)
            print(f"[entrypoint] dbt docs generated in {dest}")
            # catalog.json is created here (docs generate), after the artifact
            # upload above ran on a `build`; push it to the lake now so the
            # OpenMetadata dbt connector gets column types (needs manifest+catalog).
            cat = target / "catalog.json"
            if cat.exists():
                key = f"dbt/{project}/catalog.json"
                try:
                    s3.upload_file(str(cat), bucket, key)
                    print(f"[entrypoint] uploaded s3://{bucket}/{key} (docs pass)")
                except Exception as exc:                        # noqa: BLE001
                    print(f"[entrypoint] upload failed {key}: {exc}", file=sys.stderr)
        except Exception as exc:                                # noqa: BLE001
            print(f"[entrypoint] dbt docs generate failed: {exc}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
