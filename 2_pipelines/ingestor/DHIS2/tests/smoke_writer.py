"""Smoke test: write 5 synthetic rows through our destination factory.

Validates:
- destination.build_destination() produces a working dlt destination
- the layout writes Parquet files at the contract path
  s3://nhdw-raw/dhis2/smoke_test/year=YYYY/month=MM/day=DD/dhis2_smoke_test_<ts>_run-<id>_part-<NNNN>.parquet
- the manifest writer drops _manifest.json next to it
- the ingestor service-account can write to nhdw-raw (policy check)

Run from inside the container:
    docker run --rm --env-file .env --network nhdw_net nhdw/dhis2-ingestor:dev \
      -m tests.smoke_writer
"""
from __future__ import annotations

import time

import dlt

from src.config import Config
from src.destination import build_destination
from src.manifest import build_manifest, measure_partition, write_manifest


@dlt.resource(name="smoke_test", write_disposition="append")
def synthetic_rows():
    yield from [
        {"id": "OU0001", "name": "Bahir Dar Health Center", "level": 4, "value": 42},
        {"id": "OU0002", "name": "Mekelle Clinic",         "level": 4, "value": 17},
        {"id": "OU0003", "name": "Addis Ababa Hospital",   "level": 3, "value": 158},
        {"id": "OU0004", "name": "Adama Health Center",    "level": 4, "value": 89},
        {"id": "OU0005", "name": "Hawassa Hospital",       "level": 3, "value": 203},
    ]


def main() -> None:
    cfg = Config.from_env()
    destination, placeholders, started_at, run_id = build_destination(cfg)

    print(f"endpoint = {cfg.minio_endpoint}")
    print(f"bucket   = {cfg.minio_raw_bucket}")
    print(f"run_id   = {run_id}")
    print(f"path key = {placeholders['source']}/smoke_test/year={placeholders['year']}/month={placeholders['month']}/day={placeholders['day']}/")

    pipeline = dlt.pipeline(
        pipeline_name="dhis2_smoke",
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )

    t0 = time.time()
    info = pipeline.run(synthetic_rows(), loader_file_format="parquet")
    elapsed = time.time() - t0

    file_count, bytes_written = measure_partition(cfg, table_name="smoke_test", placeholders=placeholders)

    manifest_path = write_manifest(
        cfg,
        table_name="smoke_test",
        placeholders=placeholders,
        payload=build_manifest(
            run_id=run_id,
            source=placeholders["source"],
            object_name="smoke_test",
            started_at=started_at,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            row_count=5,
            file_count=file_count,
            bytes_written=bytes_written,
        ),
    )

    print(f"OK  {file_count} parquet file(s), {bytes_written} bytes, {elapsed:.2f}s")
    print(f"OK  manifest: {manifest_path}")


if __name__ == "__main__":
    main()
