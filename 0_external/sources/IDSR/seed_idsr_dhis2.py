#!/usr/bin/env python3
"""Generate synthetic PHEM/IDSR weekly disease surveillance and write it into the DHIS2 replica via REST.

Ethiopia's IDSR flows through DHIS2, so IDSR is modelled as DHIS2 data (not a
separate source). This creates:
  · disease data elements  IDSR-<DZ>-C (confirmed cases) / IDSR-<DZ>-D (deaths)
    for ten diseases
  · a weekly data set      IDSR_WEEKLY, assigned to a fixed subset of level-4
    woreda org units (N_WOREDAS per region)
  · 53 weeks of generated values ending 2026-W27 at those woredas
The in-cluster DHIS2 ingestor then pulls them like any other data set.

The values are synthetic and deterministic (fixed RNG seed). Each disease has a
seasonal curve and a regional weight; climate-sensitive diseases are coupled
to the generated rainfall series with a per-disease lag; and the OUTBREAKS
list injects fixed outbreaks (disease, region, week, magnitude). Outbreak and
rainfall signals found downstream are the ones placed here.

Finally runs seed_population_dhis2.py (annual POP-TOTAL denominators per
woreda), so one DHIS2 seed entry point covers HMIS, IDSR and population.

Run after `seed_ethiopia.py` (creates the org tree) and
`0_external/sources/Climate/generate_climate.py` (writes the rainfall CSV).
Idempotent: deterministic UIDs; dataValueSets upsert on (DE, period, OU).
Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

def _repo_root() -> Path:
    """Walk up to the repo root (marker: bootstrap.sh), independent of how deep
    this file sits."""
    p = Path(__file__).resolve()
    while not (p / "bootstrap.sh").exists():
        if p.parent == p:
            raise RuntimeError("repo root (bootstrap.sh) not found above " + __file__)
        p = p.parent
    return p

REPO_ROOT = _repo_root()


RNG = random.Random(20260703)
ROOT = Path(__file__).resolve().parent
CLIMATE_CSV = REPO_ROOT / "bundle/seeds/sources/climate/weekly.csv"

N_WEEKS = 53
DS_CODE = "IDSR_WEEKLY"
DEFAULT_CC = "bjDvmb4bfuf"   # fallback; discovered live below


# DHIS2 REST client (mirrors seed_ethiopia.py)
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
    print(f"[inject_idsr] {m}", flush=True)


# DHIS2-style uid
_B62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
def uid(seed: str) -> str:
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    out = letters[h % 52]; h //= 52
    for _ in range(10):
        out += _B62[h % 62]; h //= 62
    return out


def iso_weeks():
    last_monday = date.fromisocalendar(2026, 27, 1)
    out = []
    for i in range(N_WEEKS - 1, -1, -1):
        mon = last_monday - timedelta(weeks=i)
        y, w, _ = mon.isocalendar()
        out.append((f"{y}W{w}", mon))         # DHIS2 weekly period yyyyWnn
    return out


# disease model
def _bump(w, c, s):
    d = min(abs(w - c), 53 - abs(w - c)); return pow(2.718281828, -(d * d) / (2 * s * s))
def malaria_season(w):    return 0.5 + 0.9 * _bump(w, 42, 8) + 0.35 * _bump(w, 18, 5)
def meningitis_season(w): return 0.4 + 1.1 * _bump(w, 8, 7)
def measles_season(w):    return 0.6 + 0.4 * _bump(w, 10, 9)
def cholera_season(w):    return 0.4 + 0.7 * _bump(w, 30, 7)
def dengue_season(w):     return 0.4 + 0.8 * _bump(w, 40, 6)
def flat_season(w):       return 0.85 + 0.15 * _bump(w, 26, 20)

DISEASES = [
    ("MAL",  "Malaria (confirmed)",        "MALARIA", "malaria", 42.0, 0.004, malaria_season,    7,  +1),
    ("MEN",  "Meningitis (meningococcal)", "EPI",     "belt",     3.0, 0.085, meningitis_season, 3,  -1),
    ("MEA",  "Measles",                    "EPI",     "pop",      2.4, 0.015, measles_season,    0,   0),
    ("CHOL", "Cholera (AWD)",              "WASH",    "lowland",  2.0, 0.021, cholera_season,    5,  +1),
    ("DENG", "Dengue fever",               "VBD",     "lowland",  1.8, 0.002, dengue_season,     6,  +1),
    ("YF",   "Yellow fever",               "EPI",     "malaria",  0.25,0.14,  cholera_season,    6,  +1),
    ("TB",   "Tuberculosis (new)",         "TB",      "pop",      6.5, 0.048, flat_season,       0,   0),
    ("TYPH", "Typhoid fever",              "WASH",    "pop",      4.0, 0.009, cholera_season,    4,  +1),
    ("RELF", "Relapsing fever",            "VBD",     "belt",     1.2, 0.03,  meningitis_season, 0,   0),
    ("NNT",  "Neonatal tetanus",           "EPI",     "lowland",  0.15,0.30,  flat_season,       0,   0),
]
REGION_ATTR = {
    "OR": (35, 1.00, 0.3, 0.4), "AM": (25, 0.55, 1.0, 0.2), "TI": (7, 0.20, 0.9, 0.2),
    "SO": (6, 0.45, 0.1, 1.0),  "SI": (5, 0.60, 0.2, 0.3),  "SW": (5, 0.95, 0.2, 0.5),
    "SE": (5, 0.70, 0.2, 0.5),  "CE": (5, 0.55, 0.3, 0.3),  "AF": (3, 0.35, 0.1, 1.0),
    "BG": (2, 0.95, 0.6, 0.6),  "GA": (1, 1.00, 0.2, 0.9),  "AA": (6, 0.05, 0.1, 0.0),
    "DD": (1, 0.40, 0.2, 0.9),  "HA": (1, 0.35, 0.2, 0.7),
}
OUTBREAKS = [("MEA","SO",12,6.0),("MEA","OR",47,5.0),("CHOL","AF",8,7.0),("CHOL","SO",33,8.0),
             ("CHOL","OR",36,4.0),("MEN","AM",6,5.5),("MEN","TI",4,4.0),("MAL","GA",44,3.0),
             ("DENG","DD",41,5.0)]
N_WOREDAS = {"OR": 6, "AM": 6, "TI": 4, "SO": 4, "SI": 3, "SW": 3, "SE": 3, "CE": 3,
             "AF": 3, "BG": 3, "GA": 2, "AA": 3, "DD": 2, "HA": 2}

def region_weight(field, pop_w, malaria, belt, lowland):
    return {"malaria": malaria, "belt": belt, "lowland": lowland, "pop": pop_w / 35.0, "": 1.0}[field]

def _binom(n, p): return sum(1 for _ in range(n) if RNG.random() < p)


def load_climate():
    rain, sums, counts = {}, defaultdict(float), defaultdict(int)
    with open(CLIMATE_CSV) as f:
        for r in csv.DictReader(f):
            wnum = int(r["iso_week"].split("-W")[1]); reg = r["region_id"]; mm = float(r["rainfall_mm"])
            rain[(reg, wnum)] = mm; sums[reg] += mm; counts[reg] += 1
    return rain, {reg: sums[reg] / counts[reg] for reg in counts}


def fetch_woredas(api):
    """{region_id: [(ou_uid, code)]} from live DHIS2 level-4 org units."""
    res = api.get("/api/organisationUnits.json", filter="level:eq:4",
                  fields="id,code", paging="false")
    by_region = defaultdict(list)
    for o in res.get("organisationUnits", []):
        code = o.get("code") or ""
        if code.startswith("WOR-"):
            by_region[code.split("-")[1]].append((o["id"], code))
    picked = {}
    for reg, lst in by_region.items():
        lst.sort(key=lambda t: t[1])
        picked[reg] = lst[:N_WOREDAS.get(reg, 3)]
    return picked


def discover_defaults(api):
    cc = api.get("/api/categoryCombos.json", filter="name:eq:default", fields="id").get("categoryCombos", [])
    coc = api.get("/api/categoryOptionCombos.json", filter="name:eq:default", fields="id").get("categoryOptionCombos", [])
    return (cc[0]["id"] if cc else DEFAULT_CC), (coc[0]["id"] if coc else None)


def seed_idsr(base=None, user=None, password=None):
    """Seed IDSR weekly disease surveillance into a running DHIS2. Called as the
    final step of the DHIS2 source seed (seed_ethiopia.py); also runnable
    standalone."""
    base = base or os.environ.get("DHIS2_BASE_URL", "http://localhost:8080")
    user = user or os.environ.get("DHIS2_ADMIN_USER", "admin")
    password = password or os.environ.get("DHIS2_ADMIN_PASSWORD", "district")
    api = Dhis2(base, user, password)

    cc_id, coc_id = discover_defaults(api)
    log(f"defaults: categoryCombo={cc_id} categoryOptionCombo={coc_id}")
    woredas = fetch_woredas(api)
    log(f"woredas: {sum(len(v) for v in woredas.values())} across {len(woredas)} regions")

    # 1) data elements ------------------------------------------------------
    de_id, elements = {}, []
    for dz, label, *_ in DISEASES:
        for metric, mname in (("C", "confirmed cases"), ("D", "deaths")):
            code = f"IDSR-{dz}-{metric}"
            eid = uid(code); de_id[(dz, metric)] = eid
            elements.append({
                "id": eid, "code": code, "name": f"IDSR {label}: {mname}",
                "shortName": f"IDSR {dz} {metric}"[:50], "valueType": "NUMBER",
                "aggregationType": "SUM", "domainType": "AGGREGATE",
                "categoryCombo": {"id": cc_id},
            })
    api.post("/api/metadata", {"dataElements": elements},
             importStrategy="CREATE_AND_UPDATE")
    log(f"imported {len(elements)} IDSR data elements")

    # 2) weekly dataSet -----------------------------------------------------
    ds_id = uid(DS_CODE)
    all_woreda_uids = [w[0] for lst in woredas.values() for w in lst]
    ds_payload = {
        "id": ds_id, "code": DS_CODE, "name": "IDSR Weekly Surveillance",
        "shortName": "IDSR Weekly", "periodType": "Weekly",
        "categoryCombo": {"id": cc_id},
        # Open the full historical + near-future weekly window so the 53-week
        # backfill imports (default settings reject old/edge weeks with E7644).
        "expiryDays": 0, "openFuturePeriods": 12, "openPeriodsAfterCoEndDate": 1200,
        "dataSetElements": [{"dataElement": {"id": e["id"]}} for e in elements],
        "organisationUnits": [{"id": u} for u in all_woreda_uids],
    }
    api.post("/api/metadata", {"dataSets": [ds_payload]},
             importStrategy="CREATE_AND_UPDATE")
    log(f"imported dataSet {DS_CODE} ({ds_id}) assigned to {len(all_woreda_uids)} woredas")

    # The metadata import records the period fields but does not open the periods
    # for data entry; only a full-object PUT does. Without it the 53-week backfill
    # is rejected with E7644 ("period does not conform to the open periods").
    full = api.get(f"/api/dataSets/{ds_id}.json", fields=":owner")
    full["expiryDays"] = 0
    full["openFuturePeriods"] = 60
    full["openPeriodsAfterCoEndDate"] = 1200
    api._req("PUT", f"/api/dataSets/{ds_id}", body=full)
    log("opened IDSR dataset periods (full-object PUT)")

    # 3) data values --------------------------------------------------------
    rain, base_rain = load_climate()
    weeks = iso_weeks()
    week_nums = {wk: int(wk.split("W")[1]) for wk, _ in weeks}
    ob = defaultdict(list)
    for dz, rg, cw, mag in OUTBREAKS:
        ob[(dz, rg)].append((cw, mag))

    values, total = [], 0
    for reg, (pop_w, malaria, belt, lowland) in REGION_ATTR.items():
        if reg not in woredas:
            continue
        br = base_rain.get(reg, 60.0)
        # The per-disease baseline is `base_rate`, not `base`: `base` is the DHIS2
        # URL passed on to seed_population() below.
        for dz, label, prog, field, base_rate, cfr, season_fn, lag, sign in DISEASES:
            rw = region_weight(field, pop_w, malaria, belt, lowland)
            if rw <= 0.01:
                continue
            for (ou_uid, code) in woredas[reg]:
                for wk, mon in weeks:
                    wnum = week_nums[wk]; s = season_fn(wnum)
                    cm = 1.0
                    if sign != 0:
                        prev = rain.get((reg, ((wnum - lag - 1) % 53) + 1), br)
                        cm = max(0.3, 1.0 + sign * 0.6 * (prev - br) / max(br, 1.0))
                    om = 1.0
                    for cwk, mag in ob.get((dz, reg), []):
                        om += mag * _bump(wnum, cwk, 3)
                    lam = base_rate * rw * (pop_w / max(1, len(woredas[reg]))) * 0.6 * s * cm * om
                    cases = int(RNG.gauss(lam, max(1.0, lam ** 0.5)))
                    if cases <= 0:
                        continue
                    deaths = _binom(cases, cfr)
                    values.append({"dataElement": de_id[(dz, "C")], "period": wk,
                                   "orgUnit": ou_uid, "value": str(cases)})
                    if deaths > 0:
                        values.append({"dataElement": de_id[(dz, "D")], "period": wk,
                                       "orgUnit": ou_uid, "value": str(deaths)})

    BATCH = 5000
    imported = ignored = 0
    for i in range(0, len(values), BATCH):
        batch = values[i:i + BATCH]
        summary = api.post_datavalues(batch)
        ic = summary.get("response", summary).get("importCount", {})
        imported += ic.get("imported", 0) + ic.get("updated", 0)
        ignored += ic.get("ignored", 0)
        total += len(batch)
        log(f"  pushed {total}/{len(values)} (imported+updated={imported}, ignored={ignored})...")
    log(f"DONE. {len(elements)} elements, dataSet {DS_CODE}, "
        f"{imported} dataValues imported ({ignored} ignored).")

    # 4) annual population denominators: sibling seeder, chained here the same
    # way seed_ethiopia.py chains this file, so one DHIS2 seed entry point
    # covers HMIS, IDSR and population. The ML input mart
    # (bi_idsr.mart_ml_disease_input) reads POP-TOTAL from the vault after the
    # next dataValueSets ingest.
    import importlib.util
    _pop = Path(__file__).resolve().parent / "seed_population_dhis2.py"
    _spec = importlib.util.spec_from_file_location("seed_population_dhis2", _pop)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _mod.seed_population(base, user, password)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed IDSR weekly surveillance into DHIS2")
    ap.add_argument("--base", default=os.environ.get("DHIS2_BASE_URL", "http://localhost:8080"))
    ap.add_argument("--user", default=os.environ.get("DHIS2_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("DHIS2_ADMIN_PASSWORD", "district"))
    a = ap.parse_args()
    raise SystemExit(seed_idsr(a.base, a.user, a.password))
