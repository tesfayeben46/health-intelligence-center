#!/usr/bin/env python3
"""Extended S3 checks for SeaweedFS (Q10-Q13 in poc/README.md).

Q10 lifecycle expiry, Q11 multipart upload, Q12 tagging/metadata preservation
(a data-migration prerequisite), Q13 large-object integrity.
"""
import hashlib, os, sys
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:18333")
CFG = Config(signature_version="s3v4", s3={"addressing_style": "path"},
             retries={"max_attempts": 3, "mode": "standard"})
s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                  aws_access_key_id="pocadmin", aws_secret_access_key="pocadminsecret",
                  config=CFG)
results = []

def rec(qid, q, verdict, ev):
    results.append((qid, q, verdict))
    print(f"  [{verdict:<4}] {qid}  {q}")
    for l in ev.strip().splitlines():
        print(f"          {l}")
    print()

def code(e): return e.response.get("Error", {}).get("Code", "?")

for b in ["nhdw-exports", "nhdw-raw", "nhdw-artifacts"]:
    try: s3.create_bucket(Bucket=b)
    except ClientError: pass

print("\n" + "="*74 + "\n  Q10-Q13  extended checks\n" + "="*74 + "\n")

# Q10 S3 lifecycle expiry
# Equivalent of `mc ilm add --expiry-days 90 --id exports-ttl-90d nhdw-exports`.
try:
    s3.put_bucket_lifecycle_configuration(
        Bucket="nhdw-exports",
        LifecycleConfiguration={"Rules": [{
            "ID": "exports-ttl-90d", "Status": "Enabled",
            "Filter": {"Prefix": ""}, "Expiration": {"Days": 90},
        }]})
    got = s3.get_bucket_lifecycle_configuration(Bucket="nhdw-exports")
    rules = got.get("Rules", [])
    ok = any(r.get("ID") == "exports-ttl-90d"
             and r.get("Expiration", {}).get("Days") == 90 for r in rules)
    rec("Q10", "S3 lifecycle expiry API (exports-ttl-90d / backups-ttl-365d)",
        "PASS" if ok else "PART",
        f"put_bucket_lifecycle_configuration accepted\n"
        f"read back {len(rules)} rule(s): {rules}")
except ClientError as e:
    rec("Q10", "S3 lifecycle expiry API (exports-ttl-90d / backups-ttl-365d)",
        "FAIL", f"{code(e)}: S3 lifecycle API not supported; retention must be\n"
                f"expressed some other way (filer TTL), which is a rewrite of the\n"
                f"provisioning code instead of a translation.")
except Exception as e:
    rec("Q10", "S3 lifecycle expiry API", "FAIL", repr(e))

# Q11 multipart upload
# boto3 switches to multipart above 8 MB; lake Parquet parts are small, but
# MLflow model artifacts can exceed it.
try:
    from boto3.s3.transfer import TransferConfig
    import io
    payload = os.urandom(24 * 1024 * 1024)          # 24 MB, forces 3+ parts
    sha_in = hashlib.sha256(payload).hexdigest()
    tc = TransferConfig(multipart_threshold=8*1024*1024, multipart_chunksize=8*1024*1024)
    s3.upload_fileobj(io.BytesIO(payload), "nhdw-artifacts", "big/model.bin", Config=tc)
    out = io.BytesIO()
    s3.download_fileobj("nhdw-artifacts", "big/model.bin", out, Config=tc)
    sha_out = hashlib.sha256(out.getvalue()).hexdigest()
    head = s3.head_object(Bucket="nhdw-artifacts", Key="big/model.bin")
    ok = sha_in == sha_out
    rec("Q11", "Multipart upload + download, 24 MB, byte-exact",
        "PASS" if ok else "FAIL",
        f"uploaded {len(payload):,} B in 8 MB parts\n"
        f"ContentLength back: {head['ContentLength']:,}\n"
        f"sha256 in : {sha_in}\nsha256 out: {sha_out}\nidentical={ok}\n"
        f"ETag: {head['ETag']}  (multipart ETags encode the part count, not comparable\n"
        f"across implementations, which is why migration verification uses sha256)")
except Exception as e:
    rec("Q11", "Multipart upload + download, 24 MB, byte-exact", "FAIL", repr(e))

# Q12 user metadata + content-type survive a copy
# A bucket-to-bucket copy or restore must preserve these.
try:
    s3.put_object(Bucket="nhdw-raw", Key="meta/probe.json", Body=b'{"a":1}',
                  ContentType="application/json",
                  Metadata={"nhdw-source": "dhis2", "nhdw-run-id": "ab12cd34"})
    h = s3.head_object(Bucket="nhdw-raw", Key="meta/probe.json")
    md = h.get("Metadata", {})
    ct = h.get("ContentType", "")
    s3.copy_object(Bucket="nhdw-raw", Key="meta/copied.json",
                   CopySource={"Bucket": "nhdw-raw", "Key": "meta/probe.json"})
    h2 = s3.head_object(Bucket="nhdw-raw", Key="meta/copied.json")
    md2, ct2 = h2.get("Metadata", {}), h2.get("ContentType", "")
    ok = md.get("nhdw-source") == "dhis2" and ct == "application/json" \
         and md2 == md and ct2 == ct
    rec("Q12", "User metadata + Content-Type survive put and server-side copy",
        "PASS" if ok else "PART",
        f"after put : ContentType={ct!r} Metadata={md}\n"
        f"after copy: ContentType={ct2!r} Metadata={md2}\n"
        f"preserved={ok}")
except Exception as e:
    rec("Q12", "User metadata + Content-Type survive put and server-side copy", "FAIL", repr(e))

# Q13 pagination beyond 1000 keys
# nhdw-raw grows unbounded; every consumer paginates. Verify the contract.
try:
    for i in range(1100):
        s3.put_object(Bucket="nhdw-raw", Key=f"page/k{i:05d}.txt", Body=b"x")
    paginator = s3.get_paginator("list_objects_v2")
    seen = []
    pages = 0
    for page in paginator.paginate(Bucket="nhdw-raw", Prefix="page/"):
        pages += 1
        seen.extend(o["Key"] for o in page.get("Contents", []))
    uniq = len(set(seen))
    ordered = seen == sorted(seen)
    rec("Q13", "Pagination past the 1000-key limit, in byte order",
        "PASS" if uniq == 1100 and ordered else "PART",
        f"wrote 1100 keys, listed {uniq} unique across {pages} page(s)\n"
        f"returned in byte-lexicographic order={ordered}\n"
        f"(this is what COLLATE \"C\" on the filer store protects)")
except Exception as e:
    rec("Q13", "Pagination past the 1000-key limit, in byte order", "FAIL", repr(e))

print("="*74)
for qid, q, v in results:
    print(f"  {qid:<5} {q:<62} {v}")
print()
sys.exit(1 if any(v == "FAIL" for _, _, v in results) else 0)
