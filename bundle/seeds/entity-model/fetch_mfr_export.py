#!/usr/bin/env python3
"""fetch_mfr_export.py: re-fetch the full Ethiopian MFR facility export.

Recreates `sample-data/facilities_<YYYY-MM-DD>.json` (shape: {"data":[...]})
from the public Master Facility Registry at https://mfr.moh.gov.et by paging
`POST /api/Facility/GetFacilities`. The MFR source replica cannot produce this
file: it is seeded from the _build sample, not the full registry.

Each record carries the fields build_sample.py consumes:
  region, zone, woreda, facilityType, operationalStatus, status, ownership, name, id

Usage:
  python3 fetch_mfr_export.py                # writes sample-data/facilities_<today>.json
  python3 fetch_mfr_export.py --per-page 5000

Needs internet access: this is the one online step in the seed toolchain. Run
it manually when refreshing the registry snapshot; the bundle stays offline
because the snapshot is committed.
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

BASE = "https://mfr.moh.gov.et/api/Facility/GetFacilities"
OUT_DIR = Path(__file__).resolve().parent / "sample-data"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE          # gov endpoint's chain is often incomplete


def fetch_page(page: int, per_page: int, retries: int = 4) -> dict:
    body = json.dumps({"pageNumber": page, "showPerPage": per_page}).encode()
    req = urllib.request.Request(
        BASE, data=body, headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
                return json.load(r)
        except Exception as exc:                                # noqa: BLE001
            if attempt == retries:
                raise
            wait = 5 * attempt
            print(f"  page {page} attempt {attempt} failed ({exc}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-page", type=int, default=2000)
    args = ap.parse_args()

    first = fetch_page(1, args.per_page)
    model = first.get("model") or []
    if not model:
        print("ERROR: empty first page: API shape changed?", file=sys.stderr)
        return 1
    total = int(model[0].get("totalCount") or 0)
    pages = (total + args.per_page - 1) // args.per_page
    print(f"==> registry reports {total} facilities · {pages} pages x {args.per_page}")

    data: list[dict] = list(model)
    for p in range(2, pages + 1):
        rows = fetch_page(p, args.per_page).get("model") or []
        data.extend(rows)
        print(f"  page {p}/{pages} -> +{len(rows)} (cum {len(data)})")

    # drop the pagination bookkeeping fields from each record
    for rec in data:
        rec.pop("pageCount", None)
        rec.pop("totalCount", None)

    out = OUT_DIR / f"facilities_{date.today().isoformat()}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"data": data, "fetched_at": date.today().isoformat(),
                   "source": BASE, "total": len(data)}, f, ensure_ascii=False)
    print(f"==> wrote {out} ({out.stat().st_size/1e6:.1f} MB, {len(data)} facilities)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
