#!/usr/bin/env python3
"""Restore an S3-layer backup (objects/ + manifest/) into an S3 endpoint.

Every object is put with its original key, Content-Type and user metadata
(x-amz-meta-*) from manifest/stat.jsonl: the attributes that `mc mirror` to a
local directory loses and that the gateway and MLflow rely on.

Resumable: an object already on the target with the same size is skipped, so a
rerun continues where the last one stopped.

Usage:
  S3_ENDPOINT=http://… S3_ACCESS_KEY=… S3_SECRET_KEY=… \
    python3 restore_from_backup.py <backup-dir> [--buckets a,b]
"""
import json, os, sys, collections
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

B = sys.argv[1].rstrip("/")
only = None
if "--buckets" in sys.argv: only = set(sys.argv[sys.argv.index("--buckets") + 1].split(","))
EP = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:18333")
# Explicit timeouts: with botocore's defaults a dead keep-alive connection can hang
# for many minutes without an error. A restore must fail visibly and be re-runnable.
s3 = boto3.client("s3", endpoint_url=EP, region_name=os.environ.get("S3_REGION", "us-east-1"),
                  aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "pocadmin"),
                  aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "pocadminsecret"),
                  config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                connect_timeout=5, read_timeout=30,
                                retries={"max_attempts": 5, "mode": "standard"}))

# Required with SeaweedFS 4.45 and botocore (botocore#3755, poc/README.md Q16):
# botocore adds `Expect: 100-continue` to every PutObject. On a zero-byte body the
# SeaweedFS gateway mishandles it and the keep-alive connection is left out of
# sync; the next request on that connection stalls about 8.5 s until a server
# timeout. dlt writes zero-byte `init` markers, so a lake copy hits this often.
# Stripping the header (harmless for S3) removes the stall. Other boto3/dlt
# consumers set BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE=true instead.
def _strip_expect(request, **kw):
    request.headers.pop("Expect", None)
s3.meta.events.register("before-send.s3.PutObject", _strip_expect)

def existing_sizes(bucket):
    """key -> size already on the target, so a rerun resumes instead of re-uploading."""
    out = {}
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for o in page.get("Contents", []): out[o["Key"]] = o["Size"]
    except ClientError: pass
    return out

# attributes per "bucket/key" from the stat manifest
attrs = {}
for line in open(f"{B}/manifest/stat.jsonl"):
    try: d = json.loads(line)
    except ValueError: continue
    if d.get("type") != "file": continue
    md = d.get("metadata") or {}
    ct = md.get("Content-Type", "application/octet-stream")
    user = {k[len("X-Amz-Meta-"):].lower(): v for k, v in md.items() if k.lower().startswith("x-amz-meta-")}
    attrs[d["name"]] = (ct, user)

per = collections.Counter(); skipped = collections.Counter(); failed = []
with_md = 0; buckets_done = set(); have = {}; done = 0
items = []
for root, _, files in os.walk(f"{B}/objects"):
    for f in files:
        path = os.path.join(root, f); name = os.path.relpath(path, f"{B}/objects")
        items.append((name, path))
items.sort()
print(f"{len(items)} objects in backup, restoring to {EP}")
for name, path in items:
    bucket, key = name.split("/", 1)
    if only and bucket not in only: continue
    if bucket not in buckets_done:
        try: s3.create_bucket(Bucket=bucket)
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"): raise
        buckets_done.add(bucket); have[bucket] = existing_sizes(bucket)
    size = os.path.getsize(path)
    if have[bucket].get(key) == size:
        skipped[bucket] += 1; continue                       # resume: already there, same size
    ct, user = attrs.get(name, ("application/octet-stream", {}))
    try:
        with open(path, "rb") as fh: body = fh.read()
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ct, Metadata=user)
        per[bucket] += 1; with_md += bool(user)
    except Exception as e:
        failed.append((name, f"{type(e).__name__}: {str(e)[:90]}"))
    done += 1
    if done % 50 == 0: print(f"  … {done} uploaded ({sum(skipped.values())} skipped, {len(failed)} failed)", flush=True)

for b in sorted(set(per) | set(skipped)):
    print(f"  {b:<16} restored {per[b]:>4}   already present {skipped[b]:>4}")
print(f"total restored {sum(per.values())}, skipped {sum(skipped.values())}, failed {len(failed)}; {with_md} carried user metadata")
for n, err in failed: print("  FAILED", n, err)
sys.exit(1 if failed else 0)
