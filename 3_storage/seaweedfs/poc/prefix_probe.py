#!/usr/bin/env python3
"""Q6 follow-up: which identity grant syntax gives prefix-scoped access.

A grant SeaweedFS does not recognise denies access both inside and outside the
prefix. This probe tries each plausible spelling against the same fixture and
reports which one gives the intended behaviour: allowed inside projects/, denied
on mlflow/.
"""
import json, subprocess, sys, time
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

ENDPOINT = "http://127.0.0.1:18333"
CFG = Config(signature_version="s3v4", s3={"addressing_style": "path"},
             retries={"max_attempts": 1, "mode": "standard"})

VARIANTS = [
    ("bucket only (control)",      ["Read:nhdw-artifacts", "Write:nhdw-artifacts", "List:nhdw-artifacts"]),
    ("prefix, no trailing slash",  ["Read:nhdw-artifacts/projects", "Write:nhdw-artifacts/projects", "List:nhdw-artifacts/projects"]),
    ("prefix, trailing slash",     ["Read:nhdw-artifacts/projects/", "Write:nhdw-artifacts/projects/", "List:nhdw-artifacts/projects/"]),
    ("prefix, glob suffix",        ["Read:nhdw-artifacts/projects/*", "Write:nhdw-artifacts/projects/*", "List:nhdw-artifacts/projects/*"]),
]

BASE = json.load(open("identities.json"))

def apply(actions):
    cfg = json.loads(json.dumps(BASE))
    for ident in cfg["identities"]:
        if ident["name"] == "project_sync":
            ident["actions"] = actions
    json.dump(cfg, open("identities.json", "w"), indent=2)
    subprocess.run(["docker", "restart", "swpoc-s3"], capture_output=True)
    for _ in range(30):
        try:
            boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                         aws_access_key_id="pocadmin", aws_secret_access_key="pocadminsecret",
                         config=CFG).list_buckets()
            return
        except Exception:
            time.sleep(1)

def probe(op, bucket, key):
    c = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                     aws_access_key_id="pocprojsync", aws_secret_access_key="pocprojsyncsecret",
                     config=CFG)
    try:
        if op == "get":    c.get_object(Bucket=bucket, Key=key)
        elif op == "put":  c.put_object(Bucket=bucket, Key=key, Body=b"probe")
        elif op == "del":  c.delete_object(Bucket=bucket, Key=key)
        return "ALLOW"
    except ClientError as e:
        return e.response.get("Error", {}).get("Code", "ERR")
    except Exception:
        return "ERR"

adm = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                   aws_access_key_id="pocadmin", aws_secret_access_key="pocadminsecret", config=CFG)
adm.put_object(Bucket="nhdw-artifacts", Key="projects/p1/nb.ipynb", Body=b"{}")
adm.put_object(Bucket="nhdw-artifacts", Key="mlflow/m1/MLmodel", Body=b"artifact")

print(f"\n{'variant':<28} {'get in':<10} {'put in':<10} {'get OUT':<10} {'put OUT':<10} {'del in':<10} verdict")
print("-" * 96)
winner = None
for label, actions in VARIANTS:
    apply(actions)
    adm.put_object(Bucket="nhdw-artifacts", Key="projects/p1/nb.ipynb", Body=b"{}")
    gi = probe("get", "nhdw-artifacts", "projects/p1/nb.ipynb")
    pi = probe("put", "nhdw-artifacts", "projects/p1/w.txt")
    go = probe("get", "nhdw-artifacts", "mlflow/m1/MLmodel")
    po = probe("put", "nhdw-artifacts", "mlflow/m1/HIJACK")
    di = probe("del", "nhdw-artifacts", "projects/p1/nb.ipynb")
    scoped = gi == "ALLOW" and pi == "ALLOW" and go != "ALLOW" and po != "ALLOW"
    v = "SCOPED" if scoped else ("bucket-wide" if gi == "ALLOW" and go == "ALLOW" else "no access")
    if scoped and winner is None:
        winner = label
    print(f"{label:<28} {gi:<10} {pi:<10} {go:<10} {po:<10} {di:<10} {v}")

print()
print(f"RESULT: prefix scoping {'WORKS via: ' + winner if winner else 'NOT achievable with any tested syntax'}")
json.dump(BASE, open("identities.json", "w"), indent=2)
subprocess.run(["docker", "restart", "swpoc-s3"], capture_output=True)
sys.exit(0 if winner else 2)
