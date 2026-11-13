#!/usr/bin/env python3
"""Project and lab workdir reconciliation for NHDW project-scoped labs.

Keeps the JupyterLab workdir in step with the project's files in the object
store (SeaweedFS, S3 API), using boto3 only:

  pull           mirror s3://$NHDW_SYNC_BUCKET/$NHDW_PROJECT_PREFIX to ~/work/project
                 (once at container start, before the lab opens)
  push           mirror ~/work/project to the same prefix (new files and files
                 whose size differs; never deletes remote objects)
  pull-datasets  download linked datasets added after the lab started
  loop           push and pull-datasets every $NHDW_SYNC_INTERVAL seconds
                 (default 60; the entrypoint runs this in the background)

Exits 0 with a note when the lab is not project-scoped or the sync
credentials are absent; an unscoped lab works normally.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PREFIX   = os.environ.get("NHDW_PROJECT_PREFIX", "")
BUCKET   = os.environ.get("NHDW_SYNC_BUCKET", "nhdw-artifacts")
ENDPOINT = os.environ.get("NHDW_SYNC_ENDPOINT", "")
KEY      = os.environ.get("NHDW_SYNC_ACCESS_KEY", "")
SECRET   = os.environ.get("NHDW_SYNC_SECRET_KEY", "")
WORKDIR  = Path(os.environ.get("NHDW_PROJECT_DIR",
                               str(Path.home() / "work" / "project")))
MARKER   = ".keep"          # the gateway's empty-prefix marker, never mirrored


def _client():
    import boto3
    from botocore.client import Config
    return boto3.client("s3", endpoint_url=ENDPOINT,
                        aws_access_key_id=KEY, aws_secret_access_key=SECRET,
                        config=Config(signature_version="s3v4",
                                      s3={"addressing_style": "path"}))


def _remote_index(s3) -> dict[str, int]:
    out: dict[str, int] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents") or []:
            rel = obj["Key"][len(PREFIX):]
            if rel and not rel.endswith(MARKER):
                out[rel] = obj.get("Size", 0)
    return out


def pull() -> int:
    s3 = _client()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for rel, _size in _remote_index(s3).items():
        dst = WORKDIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(BUCKET, PREFIX + rel, str(dst))
        n += 1
    print(f"[nhdw-sync] pull: {n} file(s) -> {WORKDIR}", flush=True)
    return 0


def pull_datasets() -> int:
    """Pull only the link-managed datasets/ subtree (files/datasets/<id>.csv).

    The gateway writes a linked dataset there after a lab may already be
    running, and push never downloads. Datasets are regenerated on each link,
    so mirroring them every cycle is idempotent: only missing or size-changed
    objects are downloaded, and nothing is deleted."""
    s3 = _client()
    sub = PREFIX + "datasets/"
    dst_root = WORKDIR / "datasets"
    n = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=sub):
        for obj in page.get("Contents") or []:
            rel = obj["Key"][len(sub):]
            if not rel or rel.endswith(MARKER):
                continue
            dst = dst_root / rel
            if dst.exists() and dst.stat().st_size == obj.get("Size", -1):
                continue                 # already have this exact object
            dst.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, obj["Key"], str(dst))
            n += 1
    if n:
        print(f"[nhdw-sync] pull datasets: {n} file(s) -> {dst_root}", flush=True)
    return 0


def push() -> int:
    s3 = _client()
    if not WORKDIR.exists():
        return 0
    remote = _remote_index(s3)
    n = 0
    for path in WORKDIR.rglob("*"):
        if not path.is_file() or ".ipynb_checkpoints" in path.parts:
            continue
        rel = str(path.relative_to(WORKDIR))
        if remote.get(rel) == path.stat().st_size:
            continue                     # same size: skip
        s3.upload_file(str(path), BUCKET, PREFIX + rel)
        n += 1
    if n:
        print(f"[nhdw-sync] push: {n} file(s) -> s3://{BUCKET}/{PREFIX}", flush=True)
    return 0


def main() -> int:
    if not (PREFIX and ENDPOINT and KEY and SECRET):
        print("[nhdw-sync] not project-scoped (or no sync creds), skipping",
              flush=True)
        return 0
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        return pull()
    if cmd == "push":
        return push()
    if cmd == "pull-datasets":
        return pull_datasets()
    if cmd == "loop":
        interval = int(os.environ.get("NHDW_SYNC_INTERVAL", "60"))
        while True:
            time.sleep(interval)
            try:
                push()
                pull_datasets()   # bring down datasets linked mid-session
            except Exception as exc:            # noqa: BLE001 - keep looping
                print(f"[nhdw-sync] sync cycle failed: {exc}", file=sys.stderr,
                      flush=True)
    print(f"usage: {sys.argv[0]} pull|push|pull-datasets|loop", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
