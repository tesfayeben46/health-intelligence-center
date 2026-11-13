#!/usr/bin/env python3
"""Verify that an S3 endpoint holds exactly what an S3-layer backup manifest lists.

Three independent checks:
  1. object count per bucket
  2. byte size of every key
  3. sha256 of every body (ETags are not compared: multipart ETags differ by engine)

Usage:
  S3_ENDPOINT=http://… S3_ACCESS_KEY=… S3_SECRET_KEY=… \
    python3 verify_against_manifest.py <backup-dir>

Exit 0 only when all three checks pass for every bucket. Use it after
restore_from_backup.py to prove a restore or a copy between endpoints.
"""
import hashlib, json, os, sys, collections
import boto3
from botocore.client import Config

B = sys.argv[1].rstrip("/")
EP = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:18333")
s3 = boto3.client("s3", endpoint_url=EP, region_name=os.environ.get("S3_REGION", "us-east-1"),
                  aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "pocadmin"),
                  aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "pocadminsecret"),
                  config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))

# expected: from the ls manifest (size) + SHA256SUMS (hash)
exp_size, bucket = {}, None
for line in open(f"{B}/manifest/ls.jsonl"):
    if line.startswith("===BUCKET==="): bucket = line.split()[1]; continue
    try: d = json.loads(line)
    except ValueError: continue
    if d.get("type") == "file": exp_size[f"{bucket}/{d['key']}"] = d["size"]
exp_sha = {}
for line in open(f"{B}/manifest/SHA256SUMS"):
    h, _, k = line.rstrip("\n").partition("  "); exp_sha[k] = h
buckets = sorted({k.split("/", 1)[0] for k in exp_size})

fail = 0
print(f"{'bucket':<16} {'expected':>8} {'listed':>7} {'size ok':>8} {'sha ok':>7}  verdict")
for b in buckets:
    want = {k for k in exp_size if k.startswith(b + "/")}
    listed = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=b):
        for o in page.get("Contents", []):
            listed.add(f"{b}/{o['Key']}")
            if o["Key"] and f"{b}/{o['Key']}" in exp_size and o["Size"] != exp_size[f"{b}/{o['Key']}"]:
                fail += 1
    size_ok = sum(1 for k in want if k in listed)
    sha_ok = 0
    for k in sorted(want):
        if k not in listed: continue
        body = s3.get_object(Bucket=b, Key=k.split("/", 1)[1])["Body"].read()
        if hashlib.sha256(body).hexdigest() == exp_sha.get(k): sha_ok += 1
    ok = (len(want) == len(listed) == size_ok == sha_ok)
    fail += 0 if ok else 1
    print(f"{b:<16} {len(want):>8} {len(listed):>7} {size_ok:>8} {sha_ok:>7}  {'PASS' if ok else 'FAIL'}")
print("\nRESULT:", "ALL BUCKETS VERIFIED: counts, sizes and sha256 match the manifest" if not fail else f"FAILED ({fail})")
sys.exit(1 if fail else 0)
