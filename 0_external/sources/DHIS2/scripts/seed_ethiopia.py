#!/usr/bin/env python3
"""Generate the DHIS2 replica's demonstration data: an Ethiopia-shaped model and synthetic HMIS values.

Run once DHIS2 has finished migrations (`/api/ping` returns "pong"); works on
an empty instance. All writes go through the REST API; no SQL.

Steps: (1) 5-level org-unit tree, (2) dataElements (1 per indicator),
(3) dataElementGroups (1 per programme), (4) one 'HMIS Monthly' dataSet
assigned to every facility, (5) synthetic monthly dataValues over 36 months,
(6) the IDSR weekly seeder (../IDSR/seed_idsr_dhis2.py), which in turn runs
the woreda population seeder.

Org units, indicators and programmes come from the shared demonstration
entity model (bundle/seeds/entity-model). No operational data is used.

Idempotent: UIDs are deterministic SHA-256 hashes of the shared IDs, so
re-runs no-op for metadata; dataValueSets upserts on (DE, period, OU).
Stdlib only (urllib, json, hashlib, random, base64, argparse, datetime).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
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


# --- Paths & constants ----------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]

# Which country's sample this generator consumes.
#
# build_sample.py writes Ethiopia's sample to _build/ and any other country's to
# _build-<model stem>/, so the two never overwrite each other. Resolving the
# matching directory (rather than hardcoding _build) keeps a non-default
# profile from receiving Ethiopian rows.
#
# Order: $NHDW_ENTITY_BUILD, then the ACTIVE profile's declared entity model,
# then _build. A deployment with no profile is unchanged.
#
# Copied, not imported: these source projects are kept
# independent of each other and of the repo's packages.
def _entity_build_dir(default_root):
    import os as _os
    from pathlib import Path as _P
    env = _os.environ.get("NHDW_ENTITY_BUILD", "").strip()
    if env:
        return _P(env)
    try:
        import yaml as _y
        pdir  = _P(_os.environ.get("NHDW_PROFILE_DIR", "/opt/nhdw-profiles"))
        state = _P(_os.environ.get("NHDW_PROFILE_STATE_DIR", "/opt/nhdw-state"))
        key = ""
        for ptr in (state / "ACTIVE", pdir / "ACTIVE"):
            if ptr.is_file():
                key = ptr.read_text(encoding="utf-8").strip()
                break
        key = key or _os.environ.get("NHDW_PROFILE", "").strip()
        if key:
            man = _y.safe_load((pdir / key / "profile.yaml").read_text(encoding="utf-8"))
            declared = (((man or {}).get("layers") or {}).get("data") or {}).get("entity_model")
            if declared:
                stem = _P(declared).stem
                if stem != "entity-model":
                    return default_root / f"_build-{stem}"
    except Exception:
        pass
    return default_root / "_build"

SHARED = _entity_build_dir(REPO_ROOT / "bundle" / "seeds" / "entity-model")
ENTITY_MODEL = REPO_ROOT / "bundle" / "seeds" / "entity-model" / "entity-model.yaml"

DEFAULT_BASE = os.environ.get("DHIS2_BASE_URL", "http://127.0.0.1:8080")
DEFAULT_USER = os.environ.get("DHIS2_ADMIN_USER", "admin")
DEFAULT_PASS = os.environ.get("DHIS2_ADMIN_PASSWORD", "district")

DATASET_CODE = "HMIS_MONTHLY"
DATASET_NAME = "HMIS Monthly"
HISTORY_MONTHS = 36
DATAVALUE_CAP = 800_000  # Safety valve, not a budget: the submission model's
                         # natural volume is ~680k (893 facilities x 36 months x
                         # tier service profiles x ~88% compliance). A lower cap
                         # truncates reporting, so completeness metrics would
                         # measure the cap rather than reporting behaviour.

# Salt for the deterministic per-facility reporting profiles; main() reassigns
# it from the entity-model seed so re-runs are reproducible.
SEEDABLE_PROFILE_SALT = "profile"

# Region bias (relative call-volume multiplier).
REGION_BIAS = {
    "ET-OR": 1.7, "ET-AM": 1.3, "ET-AA": 1.1, "ET-SO": 0.8,
    "ET-SE": 0.9, "ET-CE": 0.9, "ET-SW": 0.7, "ET-TI": 0.9,
    "ET-SI": 0.8, "ET-BG": 0.5, "ET-AF": 0.5, "ET-GA": 0.4,
    "ET-DD": 0.5, "ET-HA": 0.4,
}

# Tier bias (hospitals see more counts per facility-month than HPs).
TIER_BIAS = {
    "HP": 0.4, "HC": 1.0, "PHC": 0.7, "MC": 1.2,
    "PH": 2.5, "GH": 4.0, "SH": 6.0, "PH-": 0.3, "DS": 0.2,
}

# Programme seasonality (multiplier by Gregorian month 1..12).
SEASONALITY = {
    "MALARIA": [0.5, 0.5, 0.6, 0.7, 0.9, 1.3, 1.6, 1.5, 1.2, 0.9, 0.7, 0.6],
    "EPI":     [1.0, 1.0, 1.1, 1.1, 1.0, 0.9, 0.9, 1.0, 1.1, 1.1, 1.0, 1.0],
    "MNCH":    [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    "HIV":     [1.0] * 12,
    "TB":      [1.1, 1.1, 1.0, 1.0, 0.9, 0.9, 0.9, 1.0, 1.0, 1.1, 1.1, 1.1],
    "NCD":     [1.0] * 12,
    "FP":      [1.0] * 12,
    "NUTRITION": [0.9, 0.9, 1.0, 1.1, 1.2, 1.3, 1.2, 1.1, 1.0, 0.9, 0.9, 0.9],
}

# Base monthly count (HC, mid-region, no season) per indicator id prefix.
BASE_MEAN = {
    "HIV-TEST": 80, "HIV-POS": 4,  "HIV-ART-NEW": 3, "HIV-ART-CURR": 60,
    "TB-NOTIF": 6,  "TB-BACT": 4,  "TB-TX-NEW": 5,  "TB-LTFU": 1,
    "BCG": 22, "PENTA1": 24, "PENTA3": 22, "MR1": 21, "MR2": 18,
    "ANC1": 30, "ANC4": 22, "SBA": 18, "PNC1": 17,
    "HTN-SCRN": 90, "HTN-DX": 12, "HTN-TX": 35,
    "MAL-SUSP": 135, "MAL-MICRO": 32, "MAL-CONF": 18, "MAL-RDT": 90,
    "MAL-TX-AL": 16, "MAL-DEATH": 1,
    "FP-NEW": 25, "FP-IMPL": 6, "FP-DEPO": 40, "FP-CYP": 70,
    "NUT-SAM": 4, "NUT-RUTF": 30,
    # EPI safety surveillance (small counts per facility-month).
    "AEFI-MILD": 6, "AEFI-SERIOUS": 1, "CC-EXCURSION": 2,
}

# Reporting model
# DHIS2 reporting is a monthly facility submission: a facility either submits
# its month's report (containing all elements it offers) or misses it.
# Modelling completeness per facility-month, not per indicator, keeps woreda
# completeness independent of how many facilities a woreda has. National
# monthly reporting compliance is about 90%.

# Which indicators a facility reports is its tier's service profile.
# A facility offers an indicator when TIER_RANK[tier] >= IND_MIN_RANK[id]
# (health posts run the community subset; only hospitals report the
# cohort-outcome and registry-grade indicators).
TIER_RANK = {"DS": 0, "PH-": 0, "HP": 1, "PHC": 2, "HC": 3, "MC": 3,
             "PH": 4, "GH": 5, "SH": 6}
IND_MIN_RANK = {
    **{i: 0 for i in (
        "ANC1", "BCG", "PENTA1", "PENTA3", "MR1", "MR2", "AEFI-MILD",
        "MAL-SUSP", "MAL-MICRO", "MAL-RDT", "MAL-CONF", "MAL-TX-AL",
        "FP-NEW", "FP-DEPO", "NUT-SAM", "NUT-RUTF", "HIV-TEST")},
    **{i: 2 for i in ("ANC4", "HTN-SCRN", "FP-IMPL", "CC-EXCURSION")},
    # PNC1 shares the SBA tier: it means facility-based postnatal care after a
    # facility delivery. Reporting it where SBA is not reported would make
    # region PNC1 exceed SBA.
    **{i: 3 for i in (
        "SBA", "PNC1", "HIV-POS", "HIV-ART-NEW", "TB-NOTIF", "TB-BACT", "TB-TX-NEW",
        "HTN-DX", "HTN-TX", "AEFI-SERIOUS", "NUT-SAM-CURE", "FP-CYP", "MAL-DEATH")},
    **{i: 4 for i in (
        "HIV-ART-CURR", "HIV-VL-SUPP", "TB-TX-SUCC", "TB-LTFU", "HTN-CTRL",
        "MMR", "PENTA3-COV", "ANC4-COV", "SBA-COV", "FP-MCPR", "NUT-STUNT",
        "PHEM-TIMELY", "PHEM-COMPL", "MAL-INC", "MAL-ITN-COV", "MAL-IRS-COV")},
}
DEFAULT_MIN_RANK = 3

# Monthly submission compliance by tier: hospitals are near-perfect, health
# posts miss more (connectivity). A laggard cohort and outage streaks sit on top.
TIER_COMPLIANCE = {"DS": .70, "PH-": .70, "HP": .82, "PHC": .87, "HC": .92,
                   "MC": .92, "PH": .95, "GH": .96, "SH": .97}
LAGGARD_SHARE = 0.07          # chronic under-reporters
LAGGARD_COMPLIANCE = (0.45, 0.65)
ELEMENT_OMISSION = 0.03       # per-element miss inside a submitted report

# Funnel coherence: these counts are stages of the same cohort, so each is a
# binomial draw on the parent's value in the same facility-month. Cascade
# monotonicity is then structural (aggregates inherit it) instead of relying
# on independent means lining up. child: (parent, rate_lo, rate_hi).
FUNNEL_CHAIN = {
    "HIV-POS":     ("HIV-TEST", 0.03, 0.07),
    "HIV-ART-NEW": ("HIV-POS",  0.75, 0.95),
    "TB-BACT":     ("TB-NOTIF", 0.55, 0.75),
    "TB-TX-NEW":   ("TB-NOTIF", 0.80, 0.95),
    "ANC4":        ("ANC1",     0.60, 0.80),
    "SBA":         ("ANC4",     0.75, 0.95),
    "PNC1":        ("SBA",      0.80, 0.97),
    "HTN-DX":      ("HTN-SCRN", 0.08, 0.16),
    "MAL-MICRO":   ("MAL-SUSP", 0.15, 0.30),
    "MAL-CONF":    ("MAL-RDT",  0.10, 0.30),
    "MAL-TX-AL":   ("MAL-CONF", 0.85, 1.00),
}
# Prevalent stocks derived from their inflow (persistent per-facility ratio):
# on-treatment / on-ART populations move slowly, they don't re-randomise monthly.
STOCK_OF_FLOW = {"HTN-TX": ("HTN-DX", 2.0, 3.2),
                 "HIV-ART-CURR": ("HIV-ART-NEW", 12.0, 20.0)}


def _binom(rng: random.Random, n: int, p: float) -> int:
    """Binomial draw (exact for small n, normal approximation beyond)."""
    if n <= 0 or p <= 0:
        return 0
    if p >= 1:
        return n
    if n <= 64:
        return sum(1 for _ in range(n) if rng.random() < p)
    mu, sd = n * p, math.sqrt(n * p * (1 - p))
    return max(0, min(n, int(round(rng.gauss(mu, sd)))))


# --- Tiny HTTP client (stdlib only) ---------------------------------------
class Dhis2:
    def __init__(self, base: str, user: str, pwd: str):
        self.base = base.rstrip("/")
        tok = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {tok}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _req(self, method: str, path: str, body=None, params=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                raw = r.read()
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="ignore")
            raise RuntimeError(f"{method} {path} -> {e.code}: {err[:300]}") from e

    def get(self, path, **params):
        return self._req("GET", path, params=params or None)

    def post(self, path, body, **params):
        return self._req("POST", path, body=body, params=params or None)

    def put(self, path, body):
        return self._req("PUT", path, body=body)

    def exists(self, resource: str, uid: str) -> bool:
        try:
            self._req("GET", f"/api/{resource}/{uid}", params={"fields": "id"})
            return True
        except RuntimeError as e:
            if "404" in str(e):
                return False
            raise

    def ping(self, retries: int = 30, delay: int = 5) -> None:
        for _ in range(retries):
            try:
                with urllib.request.urlopen(f"{self.base}/api/ping", timeout=5) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(delay)
        raise RuntimeError(f"DHIS2 not responding at {self.base}/api/ping")


# --- Deterministic 11-char DHIS2 UID  ^[A-Za-z][A-Za-z0-9]{10}$ ----------
_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ALNUM = _ALPHA + "0123456789"


def make_uid(*parts: str) -> str:
    """Stable 11-char UID derived from the joined parts."""
    digest = hashlib.sha256("::".join(parts).encode()).digest()
    n = int.from_bytes(digest[:9], "big")
    first = _ALPHA[n % len(_ALPHA)]
    rest = []
    n //= len(_ALPHA)
    for _ in range(10):
        rest.append(_ALNUM[n % len(_ALNUM)])
        n //= len(_ALNUM)
    return first + "".join(rest)


# --- Helpers --------------------------------------------------------------
def load_random_seed() -> int:
    if not ENTITY_MODEL.exists():
        return 20260624
    m = re.search(r"^random_seed:\s*(\d+)", ENTITY_MODEL.read_text(), re.M)
    return int(m.group(1)) if m else 20260624


def load_json(name: str):
    return json.loads((SHARED / name).read_text())


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def log(msg: str) -> None:
    print(f"[seed_ethiopia] {msg}", flush=True)


def upsert(api: Dhis2, resource: str, uid: str, payload: dict) -> str:
    """Create-or-update a metadata object by UID. Returns 'created' or 'kept'."""
    payload = {**payload, "id": uid}
    if api.exists(resource, uid):
        return "kept"
    api.post(f"/api/{resource}", payload)
    return "created"


def metadata_import(api: Dhis2, payload: dict, label: str) -> None:
    """Bulk metadata import via /api/metadata (server handles upsert)."""
    log(f"  importing {label} ({sum(len(v) for v in payload.values())} objects)...")
    api.post("/api/metadata", payload,
             importStrategy="CREATE_AND_UPDATE",
             atomicMode="NONE",
             flushMode="OBJECT")


# --- Payload builders -----------------------------------------------------
def _ou(uid, name, code, parent=None, opening="1900-01-01"):
    o = {"id": uid, "name": name, "shortName": name[:50],
         "code": code, "openingDate": opening}
    if parent:
        o["parent"] = {"id": parent}
    return o


def build_orgunits(regions, zones, woredas, facilities):
    """Return (units, country_uid, {facility_id: uid}); units are in level order."""
    today = date.today().isoformat()
    units = []
    country_uid = make_uid("ou", "country", "ET")
    units.append(_ou(country_uid, "Ethiopia", "ET"))

    reg_uid = {}
    for r in regions:
        uid = make_uid("ou", "region", r["id"])
        reg_uid[r["id"]] = uid
        units.append(_ou(uid, r["name"], r["id"], country_uid))

    zon_uid = {}
    for z in zones:
        uid = make_uid("ou", "zone", z["zone_id"])
        zon_uid[z["zone_id"]] = uid
        units.append(_ou(uid, z["name"], z["zone_id"], reg_uid[z["region_id"]]))

    zone_by_region_name = {(z["region_id"], z["name"]): z["zone_id"] for z in zones}

    wor_uid = {}
    for w in woredas:
        z_id = zone_by_region_name.get((w["region_id"], w["zone"]))
        parent = zon_uid[z_id] if z_id else reg_uid[w["region_id"]]
        uid = make_uid("ou", "woreda", w["woreda_id"])
        wor_uid[w["woreda_id"]] = uid
        units.append(_ou(uid, w["name"], w["woreda_id"], parent))

    fac_uid = {}
    for f in facilities:
        parent = wor_uid.get(f.get("woreda_id") or "") or reg_uid[f["region_id"]]
        uid = make_uid("ou", "facility", f["facility_id"])
        fac_uid[f["facility_id"]] = uid
        nm = f["name"].strip() or f["facility_id"]
        units.append(_ou(uid, nm[:230], f["facility_id"], parent, opening=today))

    return units, country_uid, fac_uid


def build_data_elements(api: Dhis2, indicators):
    """Return list of DE dicts (with stable UIDs) and {indicator_id: uid}."""
    # Fetch default categoryCombo once.
    cc = api.get("/api/categoryCombos", filter="name:eq:default", fields="id")
    cc_uid = cc["categoryCombos"][0]["id"]

    des, ind_uid = [], {}
    for ind in indicators:
        uid = make_uid("de", ind["id"])
        ind_uid[ind["id"]] = uid
        is_pct = ind["calc"] in ("pct_of", "coverage")
        des.append({
            "id": uid,
            "name": ind["name"][:230],
            "shortName": ind["name"][:50],
            "code": ind["id"],
            "domainType": "AGGREGATE",
            "valueType": "PERCENTAGE" if is_pct else "NUMBER",
            "aggregationType": "SUM",
            "categoryCombo": {"id": cc_uid},
            "zeroIsSignificant": False,
        })
    return des, ind_uid, cc_uid


def build_de_groups(programmes, indicators, ind_uid):
    by_prog = {}
    for ind in indicators:
        by_prog.setdefault(ind["programme"], []).append(ind_uid[ind["id"]])
    groups = []
    for p in programmes:
        uid = make_uid("deg", p["id"])
        groups.append({
            "id": uid,
            "name": p["name"][:230],
            "shortName": p["name"][:50],
            "code": p["id"],
            "dataElements": [{"id": u} for u in by_prog.get(p["id"], [])],
        })
    return groups


def build_dataset(de_uids, cc_uid, facility_uids, required_attr_uids=None):
    uid = make_uid("ds", DATASET_CODE)
    payload = {
        "id": uid,
        "name": DATASET_NAME,
        "shortName": DATASET_NAME[:50],
        "code": DATASET_CODE,
        "periodType": "Monthly",
        "categoryCombo": {"id": cc_uid},
        "openFuturePeriods": 0,
        "expiryDays": 0,
        "timelyDays": 15,
        "dataSetElements": [
            {"dataSet": {"id": uid}, "dataElement": {"id": de_uid}}
            for de_uid in de_uids
        ],
        "organisationUnits": [{"id": u} for u in facility_uids],
    }
    if required_attr_uids:
        payload["attributeValues"] = [
            {"attribute": {"id": a}, "value": "HMIS"} for a in required_attr_uids
        ]
    return uid, payload


# --- Data values ----------------------------------------------------------
def synth_value(rng: random.Random, ind: dict, fac: dict, month_idx: int,
                gregorian_month: int) -> float:
    """Produce one plausible monthly value for (indicator, facility, month)."""
    base = BASE_MEAN.get(ind["id"], 10)
    bias = REGION_BIAS.get(fac["region_id"], 1.0) * TIER_BIAS.get(fac["tier_id"], 1.0)
    season = SEASONALITY.get(ind["programme"], [1.0] * 12)[gregorian_month - 1]
    # Mild upward trend over history (newer months slightly higher).
    trend = 1.0 + 0.005 * month_idx
    mean = max(0.0, base * bias * season * trend)
    noise = rng.gauss(1.0, 0.35)

    calc = ind["calc"]
    if calc in ("pct_of", "coverage"):
        # Percentages: cluster around a programme-dependent midpoint, chosen
        # to resemble published Ethiopian figures (EDHS 2019, WHO-UNICEF,
        # WHO GTB); SBA-COV and FP-MCPR follow the EDHS 2019 national values.
        midpoint = {
            "HIV-VL-SUPP": 86, "TB-TX-SUCC": 84, "HTN-CTRL": 38,
            "PENTA3-COV": 78, "ANC4-COV": 64, "NUT-SAM-CURE": 78,
            "NUT-STUNT": 33, "PHEM-TIMELY": 81, "PHEM-COMPL": 88,
            "SBA-COV": 52, "FP-MCPR": 41,
            "MAL-ITN-COV": 65, "MAL-IRS-COV": 22,
        }.get(ind["id"], 70)
        v = max(0.0, min(100.0, rng.gauss(midpoint, 8) * (0.9 + 0.1 * bias)))
        return round(v, 1)
    if calc.startswith("rate_per_"):
        return round(max(0.0, mean * noise * 0.1), 2)
    # Counts: integer, never negative.
    return float(max(0, int(round(mean * noise))))


def gen_data_values(rng, indicators, facilities, months, fac_uid, ind_uid,
                    cap: int):
    """Yield {dataElement,period,orgUnit,value} dicts up to `cap`.

    Facility-month submission model (see the Reporting model block above):
      1. Each facility gets a deterministic profile: its service subset
         (tier rank vs IND_MIN_RANK), a compliance rate (tier base, 7%
         chronic laggards), and 1-3-month outage streaks.
      2. Each month the facility either submits (all profile indicators,
         ~3% element omission) or misses the month entirely.
      3. Funnel indicators are binomial draws on their parent's value in the
         same facility-month (FUNNEL_CHAIN) and prevalent stocks track their
         inflow (STOCK_OF_FLOW), so cascades are monotone by construction.
    """
    if not facilities or not indicators or not months:
        return

    n_months = len(months)
    ind_by_id = {i["id"]: i for i in indicators}

    # Topological order: funnel parents before children, then everything else.
    ordered: list[dict] = []
    placed: set[str] = set()

    def _place(iid: str):
        if iid in placed or iid not in ind_by_id:
            return
        parent = FUNNEL_CHAIN.get(iid, (None,))[0] or STOCK_OF_FLOW.get(iid, (None,))[0]
        if parent:
            _place(parent)
        placed.add(iid)
        ordered.append(ind_by_id[iid])

    for i in indicators:
        _place(i["id"])

    # Deterministic per-facility profile: service subset, compliance, outages,
    # funnel rates and stock ratios (persistent: cohorts don't re-randomise).
    profiles: dict[str, dict] = {}
    for f in facilities:
        r = random.Random(f"{SEEDABLE_PROFILE_SALT}|{f['facility_id']}")
        rank = TIER_RANK.get(f["tier_id"], 3)
        offered = [i for i in ordered
                   if rank >= IND_MIN_RANK.get(i["id"], DEFAULT_MIN_RANK)]
        compliance = TIER_COMPLIANCE.get(f["tier_id"], 0.90)
        # Region tilt: weaker-infrastructure regions report a little less.
        compliance += (REGION_BIAS.get(f["region_id"], 1.0) - 1.0) * 0.04
        if r.random() < LAGGARD_SHARE:
            compliance = r.uniform(*LAGGARD_COMPLIANCE)
        compliance = max(0.30, min(0.99, compliance))
        # 0-2 connectivity outages across the history, 1-3 months each.
        outages: set[int] = set()
        for _ in range(r.randint(0, 2)):
            start = r.randint(0, max(0, n_months - 4))
            for k in range(r.randint(1, 3)):
                outages.add(start + k)
        rates = {iid: r.uniform(lo, hi) for iid, (par, lo, hi) in FUNNEL_CHAIN.items()}
        stock_ratio = {iid: r.uniform(lo, hi) for iid, (par, lo, hi) in STOCK_OF_FLOW.items()}
        profiles[f["facility_id"]] = {
            "offered": offered, "compliance": compliance, "outages": outages,
            "rates": rates, "stock_ratio": stock_ratio,
        }

    total = 0
    for m_idx, m in enumerate(months):
        if total >= cap:
            log(f"dataValue cap {cap} reached at month {m['month_id']}: "
                f"raise --cap to extend the series")
            return
        gm = int(m["month_id"].split("-")[1])
        period = m["month_id"].replace("-", "")  # DHIS2 monthly = YYYYMM
        for f in facilities:
            prof = profiles[f["facility_id"]]
            if m_idx in prof["outages"] or rng.random() > prof["compliance"]:
                continue  # missed submission: the whole month is absent
            # Compute first, emit second: the facility's cohort funnel exists
            # whether or not each element makes it into the report, so chain
            # children always read their parent's true value. An omitted
            # parent hides a number; it must not desync the funnel.
            vals: dict[str, float] = {}
            for ind in prof["offered"]:
                iid = ind["id"]
                if iid in FUNNEL_CHAIN:
                    parent = FUNNEL_CHAIN[iid][0]
                    base = int(vals.get(parent, -1))
                    if base < 0:  # parent outside the tier profile: fallback draw
                        base = int(synth_value(rng, ind_by_id.get(parent, ind), f, m_idx, gm)) \
                            if parent in ind_by_id else 0
                    v: float = float(_binom(rng, base, prof["rates"][iid]))
                elif iid in STOCK_OF_FLOW:
                    parent = STOCK_OF_FLOW[iid][0]
                    inflow = vals.get(parent)
                    if inflow is None:
                        inflow = float(synth_value(rng, ind_by_id.get(parent, ind), f, m_idx, gm)) \
                            if parent in ind_by_id else 0.0
                    v = float(max(0, int(round(
                        inflow * prof["stock_ratio"][iid] * rng.gauss(1.0, 0.08)))))
                else:
                    v = synth_value(rng, ind, f, m_idx, gm)
                vals[iid] = v
            # Funnel-coherent omission: if a funnel parent is omitted this
            # month, its descendants are omitted too. A facility cannot report
            # "20 on ART" while omitting the positives that fed them, and
            # independent omission would let a region-year sum of a child
            # exceed its parent in small regions (checked by cascade_monotonicity).
            omitted: set[str] = set()
            for ind in prof["offered"]:
                iid = ind["id"]
                parent = FUNNEL_CHAIN.get(iid, (None,))[0]
                if (parent and parent in omitted) or rng.random() < ELEMENT_OMISSION:
                    omitted.add(iid)
                    continue
                yield {
                    "dataElement": ind_uid[iid],
                    "period": period,
                    "orgUnit": fac_uid[f["facility_id"]],
                    "value": str(vals[iid]),
                }
                total += 1
                if total >= cap:
                    log(f"dataValue cap {cap} reached mid-month {m['month_id']}: "
                        f"raise --cap to avoid a truncated tail")
                    return


# --- Main -----------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Seed DHIS2 with NHDW Ethiopia model")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--password", default=DEFAULT_PASS)
    ap.add_argument("--skip-values", action="store_true",
                    help="Only push metadata; skip generating dataValues.")
    ap.add_argument("--cap", type=int, default=DATAVALUE_CAP,
                    help="Max dataValues to push (default 800000).")
    args = ap.parse_args()

    seed = load_random_seed()
    rng = random.Random(seed)
    global SEEDABLE_PROFILE_SALT
    SEEDABLE_PROFILE_SALT = f"profile-{seed}"
    log(f"seed={seed} base={args.base}")

    regions    = load_json("regions.json")
    zones      = load_json("zones.json")
    woredas    = load_json("woredas.json")
    facilities = load_json("facilities.json")
    indicators = load_json("indicators.json")
    programmes = load_json("programmes.json")
    periods    = load_json("periods.json")
    months_all = periods["months"]
    months = months_all[-HISTORY_MONTHS:] if len(months_all) >= HISTORY_MONTHS else months_all

    log(f"loaded: {len(regions)} regions, {len(zones)} zones, "
        f"{len(woredas)} woredas, {len(facilities)} facilities, "
        f"{len(indicators)} indicators, {len(months)} months")

    api = Dhis2(args.base, args.user, args.password)
    api.ping()
    log("DHIS2 reachable.")

    # 1) Org-unit hierarchy ------------------------------------------------
    log("Step 1/5 · organisationUnits")
    units, country_uid, fac_uid = build_orgunits(regions, zones, woredas, facilities)
    log(f"  total org units to upsert: {len(units)}")
    # Import level by level so parent always exists when child arrives.
    # We rebuild level groups from the units list (parent absence marks L1).
    levels = []
    cur, last_count = list(units), -1
    while cur and len(cur) != last_count:
        ready_ids = {u["id"] for layer in levels for u in layer} | {country_uid}
        next_layer = [u for u in cur if "parent" not in u or u["parent"]["id"] in ready_ids]
        if not next_layer:
            break
        levels.append(next_layer)
        last_count = len(cur)
        cur = [u for u in cur if u["id"] not in {n["id"] for n in next_layer}]
    for i, layer in enumerate(levels, 1):
        for batch in chunks(layer, 100):
            metadata_import(api, {"organisationUnits": batch}, f"OU L{i} batch")

    # Scope the current (admin) user's data-capture, data-view and search org
    # units to the Ethiopia country root. On an empty DHIS2 the admin has no
    # scope over the new tree and dataValue imports fail with E7617 ("org unit
    # not in hierarchy of current user").
    me_id = api.get("/api/me", fields="id")["id"]
    for coll in ("organisationUnits", "dataViewOrganisationUnits", "teiSearchOrganisationUnits"):
        try:
            api._req("POST", f"/api/users/{me_id}/{coll}/{country_uid}")
        except RuntimeError as e:
            log(f"  ! assign {coll} failed: {e}")
    log(f"  assigned admin user {me_id} to country root {country_uid}")

    # 2) Data elements + DE groups ----------------------------------------
    log("Step 2/5 · dataElements")
    des, ind_uid, cc_uid = build_data_elements(api, indicators)
    for batch in chunks(des, 200):
        metadata_import(api, {"dataElements": batch}, "DE batch")

    log("Step 3/5 · dataElementGroups")
    degs = build_de_groups(programmes, indicators, ind_uid)
    metadata_import(api, {"dataElementGroups": degs}, "DE groups")

    # 3) Data set ---------------------------------------------------------
    log("Step 4/5 · dataSet HMIS Monthly")
    facility_uids = [fac_uid[f["facility_id"]] for f in facilities]
    de_uids = [ind_uid[i["id"]] for i in indicators]
    attrs = api.get("/api/attributes",
                    filter=["dataSetAttribute:eq:true", "mandatory:eq:true"],
                    fields="id,name", paging="false")
    required_attr_uids = [a["id"] for a in attrs.get("attributes", [])]
    if required_attr_uids:
        log(f"  satisfying {len(required_attr_uids)} required dataSet attribute(s): {required_attr_uids}")
    ds_uid, ds_payload = build_dataset(de_uids, cc_uid, facility_uids, required_attr_uids)
    metadata_import(api, {"dataSets": [ds_payload]}, "dataSet")

    # 4) Data values ------------------------------------------------------
    if args.skip_values:
        log("Step 5/5 · skipped (--skip-values)")
        log("DONE.")
        return 0

    log(f"Step 5/5 · dataValues (cap={args.cap})")
    pushed = 0
    buf = []
    BATCH = 5000
    for dv in gen_data_values(rng, indicators, facilities, months,
                              fac_uid, ind_uid, args.cap):
        buf.append(dv)
        if len(buf) >= BATCH:
            api.post("/api/dataValueSets",
                     {"dataValues": buf},
                     importStrategy="CREATE_AND_UPDATE",
                     dryRun="false")
            pushed += len(buf)
            log(f"  pushed {pushed} dataValues...")
            buf = []
    if buf:
        api.post("/api/dataValueSets",
                 {"dataValues": buf},
                 importStrategy="CREATE_AND_UPDATE",
                 dryRun="false")
        pushed += len(buf)

    log(f"pushed {pushed} dataValues total.")

    # Step 6: IDSR weekly disease surveillance. Ethiopia reports weekly
    # surveillance through DHIS2, so it is seeded as part of the DHIS2 source
    # seed. Requires the climate CSV first (case counts are coupled to rainfall
    # with a lag).
    if not args.skip_values:
        log("Step 6 · IDSR weekly disease surveillance")
        import importlib.util
        _idsr = ROOT.parent / "IDSR" / "seed_idsr_dhis2.py"
        _spec = importlib.util.spec_from_file_location("seed_idsr_dhis2", _idsr)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.seed_idsr(args.base, args.user, args.password)

    log("DONE. Tip: trigger analytics rebuild with "
        "`curl -u admin:district -X POST {base}/api/resourceTables/analytics`"
        .format(base=args.base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
