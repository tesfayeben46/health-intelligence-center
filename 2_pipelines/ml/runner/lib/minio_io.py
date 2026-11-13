"""Object-store (SeaweedFS S3) I/O for the ml-runner: package fetch + ad-hoc CSV staging.

URI forms accepted everywhere: `minio://bucket/key…` or `s3://bucket/key…`.

Env contract (no new secret material: reuses what the MLflow client already
gets in the pod):

  MINIO_ENDPOINT     default -> MLFLOW_S3_ENDPOINT_URL
                             -> http://seaweedfs-s3.nhdw.svc.cluster.local:8333
  MINIO_ACCESS_KEY   default -> AWS_ACCESS_KEY_ID
  MINIO_SECRET_KEY   default -> AWS_SECRET_ACCESS_KEY
  AWS_DEFAULT_REGION default et-addis-1
"""
from __future__ import annotations

import logging
import os
import tarfile
from pathlib import Path

import boto3

log = logging.getLogger("runner.minio")

_DEFAULT_ENDPOINT = "http://seaweedfs-s3.nhdw.svc.cluster.local:8333"


def client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_ENDPOINT")
        or os.environ.get("MLFLOW_S3_ENDPOINT_URL", _DEFAULT_ENDPOINT),
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY")
        or os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "et-addis-1"),
    )


def parse_uri(uri: str) -> tuple[str, str]:
    """minio://bucket/some/key -> ('bucket', 'some/key')."""
    for scheme in ("minio://", "s3://"):
        if uri.startswith(scheme):
            bucket, _, key = uri[len(scheme):].partition("/")
            if not bucket or not key:
                raise ValueError(f"malformed object URI: {uri}")
            return bucket, key
    raise ValueError(f"expected a minio:// or s3:// URI, got: {uri}")


def download(uri: str, dest: Path) -> Path:
    bucket, key = parse_uri(uri)
    dest.parent.mkdir(parents=True, exist_ok=True)
    client().download_file(bucket, key, str(dest))
    log.info("downloaded %s to %s", uri, dest)
    return dest


def upload(local: Path, uri: str) -> None:
    bucket, key = parse_uri(uri)
    client().upload_file(str(local), bucket, key)
    log.info("uploaded %s to %s", local, uri)


def download_prefix(uri: str, dest_dir: Path) -> int:
    """Mirror every object under a prefix URI into dest_dir (package layout).

    minio://nhdw-artifacts/ml-packages/<name>/<vN>/  ->  dest_dir/<files…>
    A single `.tar.gz` under the prefix (the gateway upload shape) is
    extracted in place instead of mirrored.
    """
    bucket, prefix = parse_uri(uri.rstrip("/") + "/")
    s3 = client()
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(o["Key"] for o in page.get("Contents", []))
    if not keys:
        raise FileNotFoundError(f"no objects under {uri}")

    tarballs = [k for k in keys if k.endswith((".tar.gz", ".tgz"))]
    if len(keys) == 1 and tarballs:
        local_tar = dest_dir / Path(tarballs[0]).name
        dest_dir.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, tarballs[0], str(local_tar))
        with tarfile.open(local_tar) as tf:
            tf.extractall(dest_dir, filter="data")   # 'data' filter: no abs paths / traversal
        local_tar.unlink()
        log.info("extracted package tarball %s to %s", tarballs[0], dest_dir)
        return len(keys)

    for key in keys:
        rel = key[len(prefix):]
        if not rel:            # the prefix marker object itself
            continue
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(target))
    log.info("mirrored %d objects from %s to %s", len(keys), uri, dest_dir)
    return len(keys)
