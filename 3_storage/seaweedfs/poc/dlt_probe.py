#!/usr/bin/env python3
"""Q15: a real dlt run against SeaweedFS, mirroring 2_pipelines/ingestor/*/src/destination.py.

Same destination type, same credential spec, same layout string, same
extra_placeholders, same dataset_name convention, same custom region the
ingestors default to (et-addis-1). If this writes the contract path
    nhdw-raw/dhis2/<table>/year=/month=/day=/dhis2_<table>_<ts>_run-<id>_part-<file_id>.parquet
then an ingestor needs only MINIO_ENDPOINT and its credentials to target SeaweedFS.
"""
import datetime as dt, os, sys
from uuid import uuid4

# Workaround for botocore#3755 (see poc/README.md, Q16): botocore adds
# `Expect: 100-continue` to file uploads, which stalls after a zero-byte body, and
# dlt writes zero-byte `init` markers first, so the load step times out. Dropping
# the handler from botocore's built-in list before any session is created disables
# the header process-wide (boto3 and aiobotocore/s3fs both build sessions from
# this list). Opt-in via env so the unmodified baseline can still be measured.
if os.environ.get("DLT_STRIP_EXPECT") == "1":
    import botocore.handlers as _bh
    _bh.BUILTIN_HANDLERS[:] = [h for h in _bh.BUILTIN_HANDLERS if getattr(h[1], "__name__", "") != "add_expect_header"]

import dlt
from dlt.common.configuration.specs import AwsCredentials

ENDPOINT = os.environ["MINIO_ENDPOINT"]
REGION   = os.environ.get("MINIO_REGION", "et-addis-1")

run_ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
extra = {
    "source": "dhis2", "year": run_ts.strftime("%Y"), "month": run_ts.strftime("%m"),
    "day": run_ts.strftime("%d"), "ts_compact": run_ts.strftime("%Y%m%dT%H%M%SZ"),
    "run_id_short": str(uuid4()).replace("-", "")[:8],
}
layout = ("{table_name}/year={year}/month={month}/day={day}/"
          "{source}_{table_name}_{ts_compact}_run-{run_id_short}_part-{file_id}.{ext}")

dest = dlt.destinations.filesystem(
    bucket_url=f"s3://{os.environ.get('DLT_BUCKET', 'nhdw-raw')}",   # set DLT_BUCKET to a scratch bucket against a deployed endpoint
    credentials=AwsCredentials(
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        endpoint_url=ENDPOINT, region_name=REGION),
    layout=layout, extra_placeholders=extra)

@dlt.resource(name="analytics", write_disposition="append")
def analytics():
    for i in range(2000):
        yield {"facility_id": i, "facility_code": f"FAC-OR-HP-{i}",
               "value": i * 7, "reported_on": run_ts.date()}

pipe = dlt.pipeline(pipeline_name="poc_dhis2", destination=dest, dataset_name="dhis2")
info = pipe.run(analytics(), loader_file_format="parquet")
print(info)
jobs = [j for p in info.load_packages for j in p.jobs["completed_jobs"]]
print(f"\ncompleted jobs: {len(jobs)}")
for j in jobs:
    print("  ", j.file_path)
sys.exit(0 if jobs and not info.has_failed_jobs else 1)
