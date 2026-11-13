#!/usr/bin/env python3
"""publish_public_datasets.py: materialise the public Open-Data catalogue.

Cuts each published dataset from bi_public.* into a CSV file in the anonymous
nhdw-static bucket, plus a catalogue.json manifest carrying provenance +
protection metadata (source system, grain, k>=5, last_refresh, row_count,
licence, download URL). The public dashboard reads the manifest and streams the
static files, the common open-data pattern (CKAN/data.gov): suppression
applied once at publish, citable snapshots, decoupled from the live
warehouse. Idempotent: overwrites each run. Runs as a boot Job after dbt-marts.

Everything published here is aggregate + k>=5 (enforced in the bi_public marts).

Env: PG_HOST/PORT/USER/PASSWORD/DATABASE, and for the object store (SeaweedFS
S3; the MINIO_* names are historical) MINIO_ENDPOINT, MINIO_ACCESS_KEY,
MINIO_SECRET_KEY, MINIO_PUBLIC_ENDPOINT (browser-facing base for the url field).
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import date, datetime, timezone

import boto3
import psycopg2

BUCKET = "nhdw-static"
LICENCE = "CC-BY 4.0"

# The published catalogue. `period_col` (if set) -> dataset period from min/max.
DATASETS = [
    {
        "id": "D-001", "title": "National coverage indicators by region",
        "topic": "EPI · MNCH · HIV · TB", "schema": "bi_public",
        "table": "mart_public_indicator_kpis", "source": "DHIS2 (HMIS)",
        "grain": "region, indicator, month", "period_col": "month_id",
        "desc": "HSDIP headline indicator achievement rates (Penta3, ANC4, "
                "SBA, mCPR, TB success, ART suppression, U5 stunting) by region and "
                "month. k-anonymity >= 5; every region cell has >=5 reporting facilities.",
    },
    {
        "id": "D-002", "title": "Programme reporting summary by region",
        "topic": "Surveillance", "schema": "bi_public",
        "table": "mart_public_summary", "source": "DHIS2 (HMIS)",
        "grain": "region, programme, month", "period_col": "month_id",
        "desc": "Programme-level facility reporting completeness and aggregate values "
                "by region and month. k-anonymity >= 5.",
    },
    {
        "id": "D-003", "title": "Facility distribution by region",
        "topic": "Health Facility Registry", "schema": "bi_public",
        "table": "bi_facility_distribution_public", "source": "MFR · DHIS2 org-units",
        "grain": "region", "period_col": None,
        "desc": "Count of health facilities per region from the national Master "
                "Facility Registry. Aggregate counts only.",
    },
    {
        "id": "D-004", "title": "Commodity availability (supply readiness)",
        "topic": "Supply chain", "schema": "bi_public",
        "table": "mart_supply_stockout_public", "source": "HCMIS (EPSS)",
        "grain": "national", "period_col": None,
        "desc": "Aggregate commodity availability / stockout summary across EPSS "
                "supply hubs. Aggregate + k-anonymity >= 5.",
    },
    {
        "id": "D-005", "title": "Notifiable disease surveillance (weekly) by region",
        "topic": "Disease surveillance · IDSR", "schema": "bi_public",
        "table": "mart_public_disease_surveillance", "source": "DHIS2 (IDSR / EWARS)",
        "grain": "region, disease, epi-week", "period_col": "iso_week",
        "desc": "Weekly confirmed cases and deaths for priority notifiable diseases "
                "(malaria, measles, cholera, meningitis, dengue, yellow fever, TB and "
                "more) by region and epidemiological week, from Ethiopia's IDSR "
                "reporting through DHIS2. Rolled up across woredas; any cell with "
                "fewer than 5 combined events is suppressed (k-anonymity >= 5).",
    },
    {
        "id": "D-006", "title": "Health workforce density by region",
        "topic": "Human resources for health", "schema": "bi_public",
        "table": "mart_public_workforce", "source": "MFR · DHIS2 population",
        "grain": "region", "period_col": None,
        "desc": "Health-worker counts by cadre (physicians, nurses, health extension "
                "workers) and WHO-comparable density per 10,000 population, per region, "
                "from the Master Facility Registry joined to population. Aggregate only.",
    },
    {
        "id": "D-007", "title": "Data quality & reporting completeness scorecard",
        "topic": "Data governance", "schema": "bi_public",
        "table": "mart_public_data_quality", "source": "All six ingested sources",
        "grain": "source, period, region", "period_col": "period_id",
        "desc": "Reporting completeness, timeliness and validity by source system, "
                "period and region across all six data feeds (DHIS2, HCMIS, mBrana, "
                "EthioEMR, MFR, IDSR). A transparency view of how complete the "
                "warehouse's own inputs are. Aggregate; small cells suppressed (k >= 5).",
    },
    {
        "id": "D-008", "title": "Immunization: zero-dose reach index by region",
        "topic": "EPI · Immunization", "schema": "bi_public",
        "table": "mart_public_zero_dose", "source": "DHIS2 · mBrana",
        "grain": "region, year", "period_col": "year_id",
        "desc": "Where are the unreached children? Penta-1 immunization reach index "
                "(annualized doses per 1,000 expected infants), expected-infant "
                "estimates and last-mile cold-chain stress by region and year, with "
                "equity hotspots flagged. Aggregate only.",
    },
]


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
    )


def main() -> int:
    pub_base = os.environ.get("MINIO_PUBLIC_ENDPOINT", "").rstrip("/")
    conn = psycopg2.connect(
        host=os.environ["PG_HOST"], port=os.environ.get("PG_PORT", "5432"),
        user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"],
        dbname=os.environ.get("PG_DATABASE", "nhdw_vault"),
    )
    s3 = _s3()
    try:
        s3.head_bucket(Bucket=BUCKET)
    except Exception:
        s3.create_bucket(Bucket=BUCKET)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    catalogue = []
    for d in DATASETS:
        fq = f'{d["schema"]}."{d["table"]}"'
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {fq}")
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        # write CSV to a buffer
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
        body = buf.getvalue().encode("utf-8")
        key = f"datasets/{d['id']}.csv"
        s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="text/csv")

        period = None
        if d["period_col"] and d["period_col"] in cols:
            vals = sorted(str(row[cols.index(d["period_col"])]) for row in rows
                          if row[cols.index(d["period_col"])] is not None)
            if vals:
                period = f"{vals[0]} to {vals[-1]}"

        catalogue.append({
            "id": d["id"], "title": d["title"], "topic": d["topic"],
            "tier": "aggregate", "source_system": d["source"], "grain": d["grain"],
            "k_anonymity": 5, "licence": LICENCE, "description": d["desc"],
            "formats": ["CSV"], "row_count": len(rows), "columns": cols,
            "size_bytes": len(body), "period": period, "last_refresh": now,
            "download_url": (f"{pub_base}/{BUCKET}/{key}" if pub_base else f"/{BUCKET}/{key}"),
            "key": key,
        })
        print(f"  {d['id']} {d['title']}: {len(rows)} rows, {len(body)} bytes")

    manifest = {
        "generated_at": now, "licence": LICENCE, "k_anonymity": 5,
        "count": len(catalogue), "datasets": catalogue,
    }
    s3.put_object(Bucket=BUCKET, Key="catalogue.json",
                  Body=json.dumps(manifest, default=str).encode("utf-8"),
                  ContentType="application/json")
    conn.close()
    print(f"published {len(catalogue)} datasets to {BUCKET}/catalogue.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
