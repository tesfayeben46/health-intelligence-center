#!/usr/bin/env python3
"""SeaweedFS S3 compatibility harness.

Checks, against a running endpoint, the S3 behaviour the platform depends on
(Q1-Q9, Q16 in poc/README.md). Every check prints PASS / FAIL / PARTIAL and the
observable that decided it, so the verdict can be checked against the output.

Runs against the local PoC stack by default; point S3_ENDPOINT at a deployed
endpoint to use it as a regression suite.

Usage:  python3 compat_test.py
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import time
import urllib.request
import urllib.error

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:18333")
REGION = os.environ.get("S3_REGION", "us-east-1")
# Against a deployed endpoint the PoC's fixed identities do not exist: pass the
# admin pair via env and --admin-only, which skips the identity-specific checks
# (Q6/Q6b/Q7/Q9).
ADMIN_AK = os.environ.get("S3_ADMIN_ACCESS_KEY", "pocadmin")
ADMIN_SK = os.environ.get("S3_ADMIN_SECRET_KEY", "pocadminsecret")
ADMIN_ONLY = "--admin-only" in sys.argv

# Mirrors the client config in 6_access-points/api/nhdw-gateway/app/minio_client.py.
CFG = Config(signature_version="s3v4",
             s3={"addressing_style": "path"},
             retries={"max_attempts": 3, "mode": "standard"})

BUCKETS = ["nhdw-raw", "nhdw-processed", "nhdw-archive",
           "nhdw-artifacts", "nhdw-static", "nhdw-docs", "nhdw-exports"]

results: list[tuple[str, str, str, str]] = []   # (id, question, verdict, evidence)


def client(ak: str, sk: str):
    return boto3.client("s3", endpoint_url=ENDPOINT, region_name=REGION,
                        aws_access_key_id=ak, aws_secret_access_key=sk, config=CFG)


def record(qid: str, question: str, verdict: str, evidence: str) -> None:
    results.append((qid, question, verdict, evidence))
    mark = {"PASS": "PASS", "FAIL": "FAIL", "PARTIAL": "PART", "N/A": " -- "}[verdict]
    print(f"  [{mark}] {qid}  {question}")
    for line in evidence.strip().splitlines():
        print(f"         {line}")
    print()


def err_code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "?")


def section(t: str) -> None:
    print(f"\n{'='*74}\n  {t}\n{'='*74}\n")


admin = client(ADMIN_AK, ADMIN_SK)

# setup
section("SETUP: create the seven NHDW buckets")
for b in BUCKETS:
    try:
        admin.create_bucket(Bucket=b)
        print(f"  created {b}")
    except ClientError as e:
        print(f"  {b}: {err_code(e)}")
print()

# Q1 boto3 SigV4 + path style
section("Q1-Q5  Core S3 data plane")

body = b"nhdw compatibility probe\n" * 64
sha_local = hashlib.sha256(body).hexdigest()
try:
    admin.put_object(Bucket="nhdw-raw", Key="probe/hello.txt", Body=body,
                     ContentType="text/plain")
    got = admin.get_object(Bucket="nhdw-raw", Key="probe/hello.txt")
    data = got["Body"].read()
    ctype = got.get("ContentType", "")
    ok = data == body
    record("Q1", "boto3 SigV4 + path-style put/get works unmodified",
           "PASS" if ok else "FAIL",
           f"put {len(body)} B, got {len(data)} B, sha match={sha_local == hashlib.sha256(data).hexdigest()}\n"
           f"ContentType round-tripped as: {ctype!r}")
except Exception as e:
    record("Q1", "boto3 SigV4 + path-style put/get works unmodified", "FAIL", repr(e))

# Q2 presigned GET and PUT
try:
    url = admin.generate_presigned_url("get_object",
        Params={"Bucket": "nhdw-raw", "Key": "probe/hello.txt"}, ExpiresIn=300)
    with urllib.request.urlopen(url, timeout=10) as r:
        fetched = r.read()
    get_ok = fetched == body

    put_url = admin.generate_presigned_url("put_object",
        Params={"Bucket": "nhdw-raw", "Key": "probe/presigned-put.txt"}, ExpiresIn=300)
    req = urllib.request.Request(put_url, data=b"presigned put payload", method="PUT")
    with urllib.request.urlopen(req, timeout=10) as r:
        put_status = r.status
    back = admin.get_object(Bucket="nhdw-raw", Key="probe/presigned-put.txt")["Body"].read()
    put_ok = put_status in (200, 201) and back == b"presigned put payload"

    record("Q2", "Presigned GET and PUT URLs work (browser download paths)",
           "PASS" if (get_ok and put_ok) else ("PARTIAL" if get_ok else "FAIL"),
           f"presigned GET: {len(fetched)} B, identical={get_ok}\n"
           f"presigned PUT: HTTP {put_status}, readback identical={put_ok}")
except Exception as e:
    record("Q2", "Presigned GET and PUT URLs work (browser download paths)", "FAIL", repr(e))

# Q3 server-side copy_object
try:
    admin.copy_object(Bucket="nhdw-artifacts", Key="copied/hello.txt",
                      CopySource={"Bucket": "nhdw-raw", "Key": "probe/hello.txt"})
    cp = admin.get_object(Bucket="nhdw-artifacts", Key="copied/hello.txt")["Body"].read()
    record("Q3", "Server-side copy_object across buckets and prefixes",
           "PASS" if cp == body else "FAIL",
           f"copied nhdw-raw/probe/hello.txt -> nhdw-artifacts/copied/hello.txt\n"
           f"bytes identical={cp == body}")
except Exception as e:
    record("Q3", "Server-side copy_object across buckets and prefixes", "FAIL", repr(e))

# Q4 list_objects_v2 + partitioned key layout
try:
    keys = [
        "dhis2/analytics/year=2026/month=09/day=04/dhis2_analytics_20260904T101500Z_run-ab12cd34_part-0f1e2d3c4b.parquet",
        "dhis2/analytics/year=2026/month=09/day=03/dhis2_analytics_20260903T101500Z_run-ab12cd34_part-1a2b3c4d5e.parquet",
        "mfr/facility/year=2026/month=09/day=04/mfr_facility_20260904T101500Z_run-99887766_part-aabbccddee.parquet",
    ]
    for k in keys:
        admin.put_object(Bucket="nhdw-raw", Key=k, Body=b"PAR1fakeparquet")
    lr = admin.list_objects_v2(Bucket="nhdw-raw", Prefix="dhis2/analytics/year=2026/month=09/")
    found = sorted(o["Key"] for o in lr.get("Contents", []))
    delim = admin.list_objects_v2(Bucket="nhdw-raw", Prefix="dhis2/analytics/", Delimiter="/")
    prefixes = [p["Prefix"] for p in delim.get("CommonPrefixes", [])]
    record("Q4", "dlt partitioned key layout lists correctly (prefix + delimiter)",
           "PASS" if len(found) == 2 and prefixes else "PARTIAL",
           f"prefix query returned {len(found)} of 2 expected keys\n"
           f"CommonPrefixes under dhis2/analytics/: {prefixes}\n"
           f"deep key preserved verbatim: {found[0].split('/')[-1][:52]}..." if found else "no keys returned")
except Exception as e:
    record("Q4", "dlt partitioned key layout lists correctly (prefix + delimiter)", "FAIL", repr(e))

# Q5 ETag format and stability
try:
    h1 = admin.head_object(Bucket="nhdw-raw", Key="probe/hello.txt")
    et1 = h1["ETag"].strip('"')
    admin.put_object(Bucket="nhdw-raw", Key="probe/hello2.txt", Body=body)
    et2 = admin.head_object(Bucket="nhdw-raw", Key="probe/hello2.txt")["ETag"].strip('"')
    md5 = hashlib.md5(body).hexdigest()
    stable = et1 == et2
    is_md5 = et1 == md5
    record("Q5", "ETag format and stability across identical re-upload",
           "PASS" if stable else "PARTIAL",
           f"ETag(A)={et1}\nETag(B, same bytes)={et2}\nmd5(content)={md5}\n"
           f"stable across re-upload={stable}   equals content-md5={is_md5}")
except Exception as e:
    record("Q5", "ETag format and stability across identical re-upload", "FAIL", repr(e))

# Q6 prefix-scoped credentials
if ADMIN_ONLY:
    print("  (--admin-only: Q6/Q6b/Q7/Q9 identity checks skipped; verify them per identity)\n")
if not ADMIN_ONLY: section("Q6  Prefix-scoped credentials  (the assessment's highest-risk assumption)")

if not ADMIN_ONLY:
    psync = client("pocprojsync", "pocprojsyncsecret")
    try:
        admin.put_object(Bucket="nhdw-artifacts", Key="projects/p1/notebook.ipynb", Body=b"{}")
        admin.put_object(Bucket="nhdw-artifacts", Key="mlflow/model-v1/MLmodel", Body=b"artifact")
    except Exception as e:
        print(f"  !! Q6 fixture setup failed: {e!r}\n")

    allowed_read = allowed_write = denied_outside_read = denied_outside_write = None
    try:
        psync.get_object(Bucket="nhdw-artifacts", Key="projects/p1/notebook.ipynb")
        allowed_read = "ALLOWED"
    except ClientError as e:
        allowed_read = f"DENIED ({err_code(e)})"
    try:
        psync.put_object(Bucket="nhdw-artifacts", Key="projects/p1/new.txt", Body=b"x")
        allowed_write = "ALLOWED"
    except ClientError as e:
        allowed_write = f"DENIED ({err_code(e)})"
    try:
        psync.get_object(Bucket="nhdw-artifacts", Key="mlflow/model-v1/MLmodel")
        denied_outside_read = "ALLOWED  <-- escalation"
    except ClientError as e:
        denied_outside_read = f"DENIED ({err_code(e)})"
    try:
        psync.put_object(Bucket="nhdw-artifacts", Key="mlflow/model-v1/HIJACKED", Body=b"x")
        denied_outside_write = "ALLOWED  <-- escalation"
    except ClientError as e:
        denied_outside_write = f"DENIED ({err_code(e)})"

    scoped_ok = ("ALLOWED" == allowed_read and "ALLOWED" == allowed_write
                 and denied_outside_read.startswith("DENIED")
                 and denied_outside_write.startswith("DENIED"))
    record("Q6", "Credentials can be scoped to a PREFIX, not just a bucket",
           "PASS" if scoped_ok else "FAIL",
           f"identity 'project_sync' granted Read/Write/List on  nhdw-artifacts/projects  only\n"
           f"  read   inside  projects/ : {allowed_read}\n"
           f"  write  inside  projects/ : {allowed_write}\n"
           f"  read   OUTSIDE (mlflow/) : {denied_outside_read}\n"
           f"  write  OUTSIDE (mlflow/) : {denied_outside_write}")

    # Can delete be withheld while write is granted? (An S3 policy can grant PutObject without DeleteObject.)
    try:
        psync.delete_object(Bucket="nhdw-artifacts", Key="projects/p1/new.txt")
        del_verdict, del_ev = "FAIL", "DELETE ALLOWED: cannot withhold delete while granting write"
    except ClientError as e:
        del_verdict, del_ev = "PASS", f"DELETE DENIED ({err_code(e)})"
    record("Q6b", "Delete can be withheld while Write is granted", del_verdict,
           f"MinIO's nhdw-project-sync policy grants Get+Put and omits DeleteObject.\n{del_ev}\n"
           f"Only meaningful when Q6 passes; if the grant is rejected outright,\n"
           f"every operation is denied and 'delete denied' proves nothing.")

    # Q7 bucket-scoped isolation
    section("Q7-Q9  Isolation, versioning, anonymous access")

    ing = client("pocingestor", "pocingestorsecret")
    try:
        ing.put_object(Bucket="nhdw-raw", Key="probe/ingestor.txt", Body=b"ok")
        in_own = "ALLOWED"
    except ClientError as e:
        in_own = f"DENIED ({err_code(e)})"
    try:
        ing.put_object(Bucket="nhdw-processed", Key="probe/should-fail.txt", Body=b"x")
        in_other = "ALLOWED  <-- isolation broken"
    except ClientError as e:
        in_other = f"DENIED ({err_code(e)})"
    record("Q7", "Bucket-level isolation between service accounts",
           "PASS" if in_own == "ALLOWED" and in_other.startswith("DENIED") else "FAIL",
           f"identity 'nhdw_ingestor' granted Read/Write/List on nhdw-raw only\n"
           f"  write to nhdw-raw       : {in_own}\n"
           f"  write to nhdw-processed : {in_other}")

# Q8 versioning
try:
    admin.put_bucket_versioning(Bucket="nhdw-archive",
                                VersioningConfiguration={"Status": "Enabled"})
    st = admin.get_bucket_versioning(Bucket="nhdw-archive").get("Status", "<none>")
    admin.put_object(Bucket="nhdw-archive", Key="v.txt", Body=b"version-one")
    admin.put_object(Bucket="nhdw-archive", Key="v.txt", Body=b"version-two")
    vers = admin.list_object_versions(Bucket="nhdw-archive", Prefix="v.txt")
    n = len(vers.get("Versions", []))
    cur = admin.get_object(Bucket="nhdw-archive", Key="v.txt")["Body"].read()
    old = [v for v in vers.get("Versions", []) if not v.get("IsLatest")]
    recovered = b""
    if old:
        recovered = admin.get_object(Bucket="nhdw-archive", Key="v.txt",
                                     VersionId=old[0]["VersionId"])["Body"].read()
    ok = st == "Enabled" and n >= 2 and recovered == b"version-one"
    record("Q8", "Bucket versioning: enable, list versions, recover an overwrite",
           "PASS" if ok else ("PARTIAL" if n >= 2 else "FAIL"),
           f"get_bucket_versioning -> {st}\n"
           f"versions of v.txt after two puts: {n}\n"
           f"current bytes: {cur!r}\n"
           f"recovered prior version: {recovered!r}")
except Exception as e:
    record("Q8", "Bucket versioning: enable, list versions, recover an overwrite", "FAIL", repr(e))

if not ADMIN_ONLY:
    # Q9 anonymous read
    try:
        admin.put_object(Bucket="nhdw-static", Key="open-data/catalogue.json",
                         Body=b'{"datasets":[]}', ContentType="application/json")
        admin.put_object(Bucket="nhdw-artifacts", Key="private/secret.txt", Body=b"private")

        def anon_get(bucket: str, key: str) -> str:
            url = f"{ENDPOINT}/{bucket}/{key}"
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    return f"HTTP {r.status} ({len(r.read())} B)"
            except urllib.error.HTTPError as e:
                return f"HTTP {e.code}"
            except Exception as e:
                return repr(e)

        pub = anon_get("nhdw-static", "open-data/catalogue.json")
        priv = anon_get("nhdw-artifacts", "private/secret.txt")
        ok = pub.startswith("HTTP 200") and not priv.startswith("HTTP 200")
        record("Q9", "Anonymous read allowed on nhdw-static and DENIED elsewhere",
               "PASS" if ok else "FAIL",
               f"identity 'anonymous' granted Read/List on nhdw-static only\n"
               f"  unauthenticated GET nhdw-static/open-data/catalogue.json : {pub}\n"
               f"  unauthenticated GET nhdw-artifacts/private/secret.txt    : {priv}")
    except Exception as e:
        record("Q9", "Anonymous read allowed on nhdw-static and DENIED elsewhere", "FAIL", repr(e))

# Q16 zero-byte PUT followed by a data PUT on one keep-alive connection
# botocore sends `Expect: 100-continue` on an empty body and then misreads the
# next response on that connection (botocore#3755), so the next request stalls
# about 8.5 s. dlt writes zero-byte `init` markers, so this affects lake copies and
# dlt loads as well as plain boto3. Stripping the header avoids it.
section("Q16  Zero-byte PUT then data PUT on one connection (botocore Expect header)")
import time as _time
def _seq(c):
    out = []
    for name, body in (("q16-d1", b"x" * 200), ("q16-z", b""), ("q16-d2", b"y" * 200)):
        t = _time.time(); c.put_object(Bucket="nhdw-raw", Key=name, Body=body); out.append((_time.time() - t) * 1000)
    return out
try:
    plain = client(ADMIN_AK, ADMIN_SK)
    fixed = client(ADMIN_AK, ADMIN_SK)
    fixed.meta.events.register("before-send.s3.PutObject", lambda request, **kw: request.headers.pop("Expect", None) and None)
    a = _seq(plain); b = _seq(fixed)
    stall = a[2] > 1000; fixed_ok = b[2] < 1000
    record("Q16", "Data PUT after a zero-byte PUT does not stall (with Expect stripped)",
           "PASS" if fixed_ok else "FAIL",
           f"boto3 default : put-after-empty = {a[2]:.0f} ms  ({'STALLS' if stall else 'ok'})\n"
           f"Expect stripped: put-after-empty = {b[2]:.0f} ms\n"
           f"{'every boto3 consumer that may write an empty object needs the header stripped (see migrate/restore_from_backup.py)' if stall else 'this SeaweedFS build does not exhibit the stall; keep the check'}")
except Exception as e:
    record("Q16", "Data PUT after a zero-byte PUT does not stall (with Expect stripped)", "FAIL", repr(e))

# summary
section("COMPATIBILITY MATRIX")
w = max(len(q) for _, q, _, _ in results)
for qid, q, verdict, _ in results:
    print(f"  {qid:<4} {q:<{w}}  {verdict}")
fails = [r for r in results if r[2] == "FAIL"]
parts = [r for r in results if r[2] == "PARTIAL"]
print(f"\n  {len(results)} checks: "
      f"{sum(1 for r in results if r[2]=='PASS')} pass, {len(parts)} partial, {len(fails)} fail")
sys.exit(1 if fails else 0)
