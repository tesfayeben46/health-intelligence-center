"""Climate weekly API: a source replica in the shape of a public climate data
service (NASA POWER / CHIRPS style), serving weekly meteorological values as
CSV. The Climate dlt ingestor pulls from here into the lake.

Endpoints:
  GET /healthz
  GET /api/v1/climate/weekly?region=&start=&end=&updated_since=&offset=&limit=
      -> text/csv with columns:
        iso_week, week_start, region_id, rainfall_mm, temp_celsius, humidity_pct, source_record_hash
      `updated_since` (an ISO date, compared against week_start) is the incremental
      cursor the ingestor uses to pull only newer rows. Rows are returned ordered by
      (week_start, region_id) so paging with offset/limit is stable.

Data is loaded once at startup from CLIMATE_CSV (default /data/weekly.csv), the
synthetic series written by 0_external/sources/Climate/generate_climate.py.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import date

from fastapi import FastAPI, Query, Response

CSV_PATH = os.environ.get("CLIMATE_CSV", "/data/weekly.csv")
COLUMNS = ["iso_week", "week_start", "region_id", "rainfall_mm",
           "temp_celsius", "humidity_pct", "source_record_hash"]

app = FastAPI(title="Ethiopia Climate Weekly API", version="1.0.0")
_ROWS: list[dict] = []


def _load() -> None:
    global _ROWS
    with open(CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (r["week_start"], r["region_id"]))
    _ROWS = rows


@app.on_event("startup")
def _startup() -> None:
    _load()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "rows": len(_ROWS)}


@app.get("/api/v1/climate/weekly")
def weekly(
    region: str | None = Query(None, description="filter to one region_id"),
    start: str | None = Query(None, description="week_start >= (ISO date)"),
    end: str | None = Query(None, description="week_start <= (ISO date)"),
    updated_since: str | None = Query(None, description="incremental cursor: week_start > (ISO date)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(10000, ge=1, le=100000),
) -> Response:
    rows = _ROWS
    if region:
        rows = [r for r in rows if r["region_id"] == region]
    if start:
        rows = [r for r in rows if r["week_start"] >= start]
    if end:
        rows = [r for r in rows if r["week_start"] <= end]
    if updated_since:
        rows = [r for r in rows if r["week_start"] > updated_since]
    page = rows[offset:offset + limit]

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    for r in page:
        w.writerow({c: r.get(c, "") for c in COLUMNS})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "X-Total-Count": str(len(rows)),
            "X-Returned-Count": str(len(page)),
        },
    )
