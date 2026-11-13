#!/usr/bin/env python3
"""Generate synthetic annual woreda population denominators and write them into the DHIS2 replica via REST.

Population is DHIS2 data in Ethiopia (census projections are entered as yearly
aggregate data values), so it is seeded at the source and flows through the
pipeline (source -> dlt ingestor -> lake -> ClickHouse -> bridge -> vault) with no
downstream changes: the dataValueSets ingestor iterates every data set. The ML
input mart (bi_idsr.mart_ml_disease_input) derives population from the
dataValue vault satellites on the yearly 'YYYY' periods.

Creates:
  · one data element   POP-TOTAL ('Population, total')
  · a yearly data set  ANNUAL_POPULATION ('Annual Population') assigned to
    every level-4 woreda org unit
  · yearly values for 2025 and 2026: 20k to 300k per woreda, derived from a
    hash of the woreda code (not census figures); 2026 is 2025 grown by
    about 2.6%.

Run after `seed_ethiopia.py` (needs the org tree). Chained from
seed_idsr_dhis2.py's seed_idsr(), so the DHIS2 seed has one entry point.
Idempotent: deterministic UIDs and CREATE_AND_UPDATE upserts; skips entirely
when POP-TOTAL dataValues already exist. Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DE_CODE = "POP-TOTAL"
DS_CODE = "ANNUAL_POPULATION"
YEARS = (2025, 2026)
GROWTH_RATE = 0.026            # Ethiopia annual population growth ≈ 2.6%
POP_MIN, POP_MAX = 20_000, 300_000
DEFAULT_CC = "bjDvmb4bfuf"     # fallback; discovered live below


# DHIS2 REST client (mirrors seed_idsr_dhis2.py / seed_ethiopia.py)
class Dhis2:
    def __init__(self, base, user, pw):
        self.base = base.rstrip("/")
        tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.headers = {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}

    def _req(self, method, path, body=None, params=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="ignore")
            raise RuntimeError(f"{method} {path} -> {e.code}: {err[:400]}") from e

    def get(self, path, **params):
        return self._req("GET", path, params=params or None)

    def post(self, path, body, **params):
        return self._req("POST", path, body=body, params=params or None)

    def post_datavalues(self, values):
        """POST dataValues tolerating 409 WARNING (DHIS2 imports the valid rows and
        reports per-row conflicts as WARNING, which is not a failure). Returns the
        ImportSummary either way."""
        url = f"{self.base}/api/dataValueSets?importStrategy=CREATE_AND_UPDATE&dryRun=false"
        data = json.dumps({"dataValues": values}).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode(errors="ignore") or "{}")
            if e.code == 409 and body.get("status") == "WARNING":
                return body
            raise RuntimeError(f"dataValueSets -> {e.code}: {json.dumps(body)[:400]}") from e


def log(m):
    print(f"[inject_population] {m}", flush=True)


# DHIS2-style uid (same derivation as seed_idsr_dhis2.py)
_B62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
def uid(seed: str) -> str:
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    out = letters[h % 52]; h //= 52
    for _ in range(10):
        out += _B62[h % 62]; h //= 62
    return out


def woreda_population(code: str) -> int:
    """Deterministic population for a woreda: 20k-300k, keyed on the
    woreda code alone so the value survives reseeds and ordering changes."""
    h = int(hashlib.sha1(f"POP:{code}".encode()).hexdigest(), 16)
    return POP_MIN + h % (POP_MAX - POP_MIN + 1)


def fetch_woredas(api):
    """[(ou_uid, code)] for all level-4 WOR- org units (population covers every
    woreda, not just the IDSR surveillance subset)."""
    res = api.get("/api/organisationUnits.json", filter="level:eq:4",
                  fields="id,code", paging="false")
    out = [(o["id"], o["code"]) for o in res.get("organisationUnits", [])
           if (o.get("code") or "").startswith("WOR-")]
    out.sort(key=lambda t: t[1])
    return out


def discover_defaults(api):
    cc = api.get("/api/categoryCombos.json", filter="name:eq:default", fields="id").get("categoryCombos", [])
    return cc[0]["id"] if cc else DEFAULT_CC


def seed_population(base=None, user=None, password=None):
    """Seed annual woreda population into a live DHIS2. Chained from
    seed_idsr_dhis2.seed_idsr() so it is part of the DHIS2 source seed; also
    runnable standalone for dev."""
    base = base or os.environ.get("DHIS2_BASE_URL", "http://localhost:8080")
    user = user or os.environ.get("DHIS2_ADMIN_USER", "admin")
    password = password or os.environ.get("DHIS2_ADMIN_PASSWORD", "district")
    api = Dhis2(base, user, password)

    de_id = uid(DE_CODE)

    # Seed only if empty: nothing to do when POP-TOTAL values exist. (Query the
    # data set only if it exists; dataValueSets returns 409 on unknown ids.)
    ds_existing = api.get("/api/dataSets.json", filter=f"code:eq:{DS_CODE}",
                          fields="id").get("dataSets", [])
    if ds_existing:
        existing = api.get("/api/dataValueSets.json", dataSet=ds_existing[0]["id"],
                           period=str(YEARS[0]), orgUnit=_root_ou(api), children="true")
        if existing.get("dataValues"):
            log(f"population already seeded ({len(existing['dataValues'])} POP-TOTAL "
                f"values for {YEARS[0]}): skipping")
            return 0

    cc_id = discover_defaults(api)
    log(f"defaults: categoryCombo={cc_id}")
    woredas = fetch_woredas(api)
    log(f"woredas: {len(woredas)} level-4 org units")

    # 1) data element --------------------------------------------------------
    element = {
        "id": de_id, "code": DE_CODE, "name": "Population, total",
        "shortName": "Population total", "valueType": "NUMBER",
        "aggregationType": "SUM", "domainType": "AGGREGATE",
        "categoryCombo": {"id": cc_id},
    }
    api.post("/api/metadata", {"dataElements": [element]},
             importStrategy="CREATE_AND_UPDATE")
    log(f"imported data element {DE_CODE} ({de_id})")

    # 2) yearly dataSet -------------------------------------------------------
    ds_id = uid(DS_CODE)
    ds_payload = {
        "id": ds_id, "code": DS_CODE, "name": "Annual Population",
        "shortName": "Annual Population", "periodType": "Yearly",
        "categoryCombo": {"id": cc_id},
        # Open past + current yearly periods so the 2025 backfill imports
        # (same E7644 guard as the IDSR weekly dataSet).
        "expiryDays": 0, "openFuturePeriods": 2, "openPeriodsAfterCoEndDate": 1200,
        "dataSetElements": [{"dataElement": {"id": de_id}}],
        "organisationUnits": [{"id": ou_uid} for ou_uid, _ in woredas],
    }
    api.post("/api/metadata", {"dataSets": [ds_payload]},
             importStrategy="CREATE_AND_UPDATE")
    log(f"imported dataSet {DS_CODE} ({ds_id}) assigned to {len(woredas)} woredas")

    # The metadata import records the period fields but does not open the
    # periods for data entry; only a full-object PUT does (as in
    # seed_idsr_dhis2.py for the weekly backfill).
    full = api.get(f"/api/dataSets/{ds_id}.json", fields=":owner")
    full["expiryDays"] = 0
    full["openFuturePeriods"] = 2
    full["openPeriodsAfterCoEndDate"] = 1200
    api._req("PUT", f"/api/dataSets/{ds_id}", body=full)
    log("opened Annual Population dataset periods (full-object PUT)")

    # 3) data values ----------------------------------------------------------
    values = []
    for ou_uid, code in woredas:
        pop = woreda_population(code)
        for i, year in enumerate(YEARS):
            grown = round(pop * (1.0 + GROWTH_RATE) ** i)
            values.append({"dataElement": de_id, "period": str(year),
                           "orgUnit": ou_uid, "value": str(grown)})

    BATCH = 5000
    imported = ignored = total = 0
    for i in range(0, len(values), BATCH):
        batch = values[i:i + BATCH]
        summary = api.post_datavalues(batch)
        ic = summary.get("response", summary).get("importCount", {})
        imported += ic.get("imported", 0) + ic.get("updated", 0)
        ignored += ic.get("ignored", 0)
        total += len(batch)
        log(f"  pushed {total}/{len(values)} (imported+updated={imported}, ignored={ignored})...")
    log(f"DONE. element {DE_CODE}, dataSet {DS_CODE}, "
        f"{imported} dataValues imported ({ignored} ignored) across years {YEARS}.")
    return 0


def _root_ou(api) -> str:
    roots = api.get("/api/organisationUnits.json", filter="level:eq:1",
                    fields="id", paging="false").get("organisationUnits", [])
    if not roots:
        raise RuntimeError("DHIS2 has no level-1 organisationUnit")
    return roots[0]["id"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed annual woreda population into DHIS2")
    ap.add_argument("--base", default=os.environ.get("DHIS2_BASE_URL", "http://localhost:8080"))
    ap.add_argument("--user", default=os.environ.get("DHIS2_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("DHIS2_ADMIN_PASSWORD", "district"))
    a = ap.parse_args()
    raise SystemExit(seed_population(a.base, a.user, a.password))
