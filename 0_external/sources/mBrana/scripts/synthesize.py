#!/usr/bin/env python3
"""Generate mBrana's synthetic rows in the restored HCMISWCloud schema.

All rows are generated; the restored backup supplies the schema only. The
generator is driven by the shared demonstration entity model in
``bundle/seeds/entity-model/_build/*.json`` so every warehouse source (DHIS2,
EthioEMR, HCMIS, mBrana, MFR) joins on the same canonical IDs.

Order respects the FK dependency graph:
  Lookups -> Geography -> Org masters -> Commodity -> Institution -> Bridges
                                                              -> RRF
                                                              -> mFlowUSSD

Tables populated:
  Lookups & dimensions:
    mBrana.SiteStatus, mBrana.TrendStatus
    Commodity.CommodityType
    Institution.OwnershipType
    USM.EnvironmentGroup
    (the RRF.* tables are cleared but not populated)

  Geography (shared NHDW entity model):
    dbo.Region           <- regions.json     (14 regions)
    dbo.Zone             <- zones.json       (134 zones)
    dbo.Woreda           <- woredas.json     (capped by MBRANA_SYNTH_WOREDA_LIMIT, default 200)
    AdministrativeUnit.AdministrativeUnit (Country/Region/Zone/Woreda hierarchyid)

  Org & products:
    USM.Environment      <- epss_hubs.json   (19 EPSS hubs)
    Commodity.Product    <- commodities.json (34 commodities)

  Operational:
    dbo.Institution      <- facilities.json  (893, capped by MBRANA_SYNTH_FACILITY_LIMIT)

  Stock-on-hand and request rows cover the last MBRANA_SYNTH_MONTHS_HISTORY
  months of the shared calendar (periods.json).
    mBrana.InstitutionEnvironment

  Mobile sync (mFlowUSSD):
    mFlowUSSD.Commodities, Institutions, Users, SOHLogs,
    Requests, RequestLines, ASNs, ASNLines
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
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


ROOT = Path(__file__).resolve().parent.parent

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

SHARED_BUILD = _entity_build_dir(REPO_ROOT / "bundle" / "seeds" / "entity-model")
ENTITY_MODEL = REPO_ROOT / "bundle" / "seeds" / "entity-model" / "entity-model.yaml"

ENV_FILE = ROOT / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


# Defaults reflect the shared sample size
# WOREDA_LIMIT caps the woreda insert (SQL Server columns are sized small in
# this schema); 200 still covers every region.
WOREDA_LIMIT = int(os.environ.get("MBRANA_SYNTH_WOREDA_LIMIT", "200"))
# FACILITY_LIMIT caps dbo.Institution. Default = all 893.
FACILITY_LIMIT = int(os.environ.get("MBRANA_SYNTH_FACILITY_LIMIT", "893"))
# Older knobs, still read but no longer driving geography.
WOREDA_COUNT = int(os.environ.get("MBRANA_SYNTH_WOREDA_COUNT", str(WOREDA_LIMIT)))
FACILITIES_PER_WOREDA = int(os.environ.get("MBRANA_SYNTH_FACILITIES_PER_WOREDA", "5"))
MONTHS_HISTORY = int(os.environ.get("MBRANA_SYNTH_MONTHS_HISTORY", "24"))
MOBILE_USER_COUNT = int(os.environ.get("MBRANA_SYNTH_MOBILE_USERS", "200"))
SOH_TARGET_ROWS = int(os.environ.get("MBRANA_SYNTH_SOH_ROWS", "7200"))
# USSD last-mile reporting: only a subset of registered facilities have an
# active HEW reporting stock-on-hand; the rest are registered but dormant. This
# fraction drives mart_data_quality completeness (distinct logging / registered),
# which sits below the stronger feeds (DHIS2, HCMIS).
ACTIVE_REPORTER_FRACTION = float(os.environ.get("MBRANA_SYNTH_ACTIVE_FRAC", "0.58"))
REQUEST_TARGET = int(os.environ.get("MBRANA_SYNTH_REQUESTS", "616"))
ASN_TARGET = int(os.environ.get("MBRANA_SYNTH_ASNS", "184"))

# entity-model.yaml carries the canonical random_seed (20260624).
DEFAULT_SEED = 20260624
random.seed(int(os.environ.get("MBRANA_SYNTH_SEED", str(DEFAULT_SEED))))

CONTAINER = os.environ.get("MBRANA_CONTAINER", "hcmiswdm-sqlserver")  # shared MSSQL instance
SA_PASS = os.environ["MBRANA_SA_PASSWORD"]
DB = os.environ.get("MBRANA_DATABASE", "HCMISWCloud")

# Synthesis-contract version: bump whenever this script changes what it writes
# or the shared entity model it reads changes shape. Stamped into dbo.SynthMeta
# inside the same transaction as the data; bundle/scripts/sources-up.sh
# re-synthesizes on version drift, so a stale packed .bak cannot mask a change.
# v4 models partial USSD reporting: ~58% of registered facilities are active
# reporters, with varying month-to-month engagement.
SYNTH_VERSION = 4


# Static lookup tables (small, fixed)
COMMODITY_TYPES = [
    ("Vaccine",        "VAC"),
    ("Pharmaceutical", "PHR"),
    ("Medical Supply", "SUP"),
    ("Diagnostic",     "DIA"),
]
OWNERSHIP_TYPES = [
    (1, "Public/Government",     "PUB"),
    (2, "Private/For-Profit",    "PRV"),
    (3, "NGO/Faith-Based",       "NGO"),
    (4, "Military",              "MIL"),
]
SITE_STATUSES = [
    ("Active",      "ACT"),
    ("Inactive",    "INA"),
    ("Maintenance", "MNT"),
]
TREND_STATUSES = [
    ("Improving", "IMP"),
    ("Stable",    "STB"),
    ("Declining", "DEC"),
]
ENVIRONMENT_GROUPS = [
    (1, "Central",  "CENT"),
    (2, "Hub",      "HUB"),
    (3, "Facility", "FAC"),
]


# Commodity programme -> CommodityType mapping
# Maps shared commodity programme/form to the legacy 4 CommodityTypes.
def commodity_type_id(commodity: dict) -> int:
    prog = (commodity.get("programme") or "").upper()
    cid = (commodity.get("id") or "").upper()
    form = (commodity.get("form") or "").lower()
    if prog == "EPI" or cid.startswith("VAC-"):
        return 1  # Vaccine
    if form in ("kit", "cart") or "RDT" in cid or "GENEXPERT" in cid:
        return 4  # Diagnostic
    if form in ("net", "implant"):
        return 3  # Medical Supply
    return 2  # Pharmaceutical


def commodity_is_vaccine(commodity: dict) -> bool:
    return commodity_type_id(commodity) == 1


def own_id_for(ownership: str) -> int:
    o = (ownership or "public").lower()
    if "private" in o:
        return 2
    if "ngo" in o or "faith" in o:
        return 3
    if "military" in o:
        return 4
    return 1


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def q(s: str) -> str:
    if s is None:
        return ""
    return str(s).replace("'", "''")


# Shared-model loaders
def load_shared() -> dict:
    """Load every shared JSON sample. No fallbacks: if the build output is
    missing, fail (re-run bundle/seeds/entity-model/build_sample.py)."""
    if not SHARED_BUILD.exists():
        raise FileNotFoundError(
            f"Shared sample not found at {SHARED_BUILD}. "
            f"Run bundle/seeds/entity-model/build_sample.py first."
        )
    def _load(name: str):
        with (SHARED_BUILD / name).open() as f:
            return json.load(f)
    return {
        "regions":     _load("regions.json"),
        "zones":       _load("zones.json"),
        "woredas":     _load("woredas.json"),
        "facilities":  _load("facilities.json"),
        "epss_hubs":   _load("epss_hubs.json"),
        "commodities": _load("commodities.json"),
        "periods":     _load("periods.json"),
    }


# SQL builders
def build_clear() -> list[str]:
    """Idempotent FK-aware clear of every table we touch."""
    return [
        f"USE [{DB}];",
        "SET NOCOUNT ON;",
        "BEGIN TRANSACTION;",
        "DELETE FROM mFlowUSSD.ASNLines;",
        "DELETE FROM mFlowUSSD.ASNs;",
        "DELETE FROM mFlowUSSD.RequestLines;",
        "DELETE FROM mFlowUSSD.Requests;",
        "DELETE FROM mFlowUSSD.SOHLogs;",
        "DELETE FROM mFlowUSSD.Users;",
        "DELETE FROM mFlowUSSD.Institutions;",
        "DELETE FROM mFlowUSSD.Commodities;",
        "DELETE FROM RRF.RequestDetail;",
        "DELETE FROM RRF.Request;",
        "DELETE FROM RRF.Period;",
        "DELETE FROM RRF.PeriodType;",
        "DELETE FROM mBrana.InstitutionEnvironment;",
        # FK order matters: dbo.Institution -> USM.Environment -> mBrana.SiteStatus/
        # TrendStatus (USM.Environment carries FK_USM_Environment_SiteStatusID /
        # _TrendStatusID). Deleting the statuses first fails with Msg 547 and the
        # seed then collides with the leftover rows.
        "DELETE FROM dbo.Institution;",
        "DELETE FROM USM.Environment;",
        "DELETE FROM USM.EnvironmentGroup;",
        "DELETE FROM mBrana.SiteStatus;",
        "DELETE FROM mBrana.TrendStatus;",
        "DELETE FROM Commodity.Product;",
        "DELETE FROM Commodity.CommodityType;",
        "DELETE FROM Commodity.RRGroup;",
        "DELETE FROM Institution.OwnershipType;",
        "DELETE FROM Institution.InstitutionType;",
        "DELETE FROM dbo.Woreda;",
        "DELETE FROM dbo.Zone;",
        "DELETE FROM dbo.Region;",
        "DELETE FROM AdministrativeUnit.AdministrativeUnit;",
    ]


def build_lookups(lines: list[str]) -> None:
    lines.append("-- mBrana.SiteStatus")
    lines.append("SET IDENTITY_INSERT mBrana.SiteStatus ON;")
    for i, (name, code) in enumerate(SITE_STATUSES, start=1):
        lines.append(
            f"INSERT INTO mBrana.SiteStatus (ID, Name, SiteStatusCode, IsActive) "
            f"VALUES ({i}, N'{name}', N'{code}', 1);"
        )
    lines.append("SET IDENTITY_INSERT mBrana.SiteStatus OFF;")

    lines.append("-- mBrana.TrendStatus")
    lines.append("SET IDENTITY_INSERT mBrana.TrendStatus ON;")
    for i, (name, code) in enumerate(TREND_STATUSES, start=1):
        lines.append(
            f"INSERT INTO mBrana.TrendStatus (ID, Name, TrendStatusCode, IsActive) "
            f"VALUES ({i}, N'{name}', N'{code}', 1);"
        )
    lines.append("SET IDENTITY_INSERT mBrana.TrendStatus OFF;")

    lines.append("-- Commodity.CommodityType")
    lines.append("SET IDENTITY_INSERT Commodity.CommodityType ON;")
    for i, (name, code) in enumerate(COMMODITY_TYPES, start=1):
        lines.append(
            f"INSERT INTO Commodity.CommodityType (CommodityTypeID, Name, "
            f"Description, IsActive, rowguid, CommodityTypeCode, SN) "
            f"VALUES ({i}, N'{name}', N'{name}', 1, NEWID(), N'{code}', {i});"
        )
    lines.append("SET IDENTITY_INSERT Commodity.CommodityType OFF;")

    lines.append("-- Institution.OwnershipType")
    lines.append("SET IDENTITY_INSERT Institution.OwnershipType ON;")
    for oid, name, code in OWNERSHIP_TYPES:
        lines.append(
            f"INSERT INTO Institution.OwnershipType "
            f"(OwnershipTypeID, Name, OwnershipTypeCode, IsActive, rowguid, SN) "
            f"VALUES ({oid}, N'{name}', N'{code}', 1, NEWID(), {oid});"
        )
    lines.append("SET IDENTITY_INSERT Institution.OwnershipType OFF;")

    lines.append("-- USM.EnvironmentGroup")
    lines.append("SET IDENTITY_INSERT USM.EnvironmentGroup ON;")
    for gid, name, code in ENVIRONMENT_GROUPS:
        lines.append(
            f"INSERT INTO USM.EnvironmentGroup "
            f"(EnvironmentGroupID, Name, Description, IsActive, rowguid, EnvironmentGroupCode) "
            f"VALUES ({gid}, N'{name}', N'{name} tier', 1, NEWID(), N'{code}');"
        )
    lines.append("SET IDENTITY_INSERT USM.EnvironmentGroup OFF;")


def build_geography(lines: list[str], shared: dict):
    """Insert dbo.Region, dbo.Zone, dbo.Woreda from shared canonical sample.

    Returns:
      region_db: shared-region-id ("ET-OR") -> dbo.Region.ID (int)
      zone_db:   shared-zone-id  ("ZON-OR-3b7ce0") -> dbo.Zone.ID (int)
      zone_by_name_region: (region_id, zone_name) -> dbo.Zone.ID
      woreda_db: shared-woreda-id ("WOR-OR-04cc67") -> dbo.Woreda.ID (int)
      region_pcode_order: list[(pcode, name)] in the order they were inserted
    """
    regions = shared["regions"]
    zones = shared["zones"]
    woredas = shared["woredas"]

    lines.append("-- dbo.Region (from bundle/seeds/entity-model/_build/regions.json)")
    lines.append("SET IDENTITY_INSERT dbo.Region ON;")
    region_db: dict[str, int] = {}
    region_pcode_order: list[tuple[str, str]] = []
    for i, r in enumerate(regions, start=1):
        region_db[r["id"]] = i
        pcode = r["id"]                                          # e.g. ET-OR
        rname = r["name"]
        region_pcode_order.append((pcode, rname))
        lines.append(
            f"INSERT INTO dbo.Region (ID, RegionName, RegionCode, rowguid, "
            f"IsActive, CreatedDate, ModifiedDate, ModifiedBy, SN, PCode, "
            f"LATITUDE, LONGITUDE) "
            f"VALUES ({i}, '{q(rname)}', '{q(r['code'])}', NEWID(), 1, "
            f"'{now_str()}', '{now_str()}', 'synth', {i}, N'{q(pcode)}', 9.0, 38.7);"
        )
    lines.append("SET IDENTITY_INSERT dbo.Region OFF;")

    lines.append("-- dbo.Zone (from bundle/seeds/entity-model/_build/zones.json)")
    lines.append("SET IDENTITY_INSERT dbo.Zone ON;")
    zone_db: dict[str, int] = {}
    zone_by_name_region: dict[tuple[str, str], int] = {}
    for i, z in enumerate(zones, start=1):
        zone_db[z["zone_id"]] = i
        rdb = region_db.get(z["region_id"])
        if rdb is None:
            # Skip zones whose region wasn't loaded (shouldn't happen).
            continue
        zone_by_name_region[(z["region_id"], z["name"])] = i
        # PCode column is nvarchar(6), so use a compact code: "Z{seq:05d}".
        # The canonical zone_id stays in ZoneCode (varchar 50).
        pcode = f"Z{i:05d}"
        lines.append(
            f"INSERT INTO dbo.Zone (ID, ZoneName, RegionId, ZoneCode, IsActive, "
            f"rowguid, CreatedDate, ModifiedDate, ModifiedBy, SN, PCode, "
            f"LATITUDE, LONGITUDE) "
            f"VALUES ({i}, '{q(z['name'][:60])}', {rdb}, "
            f"'{q(z['zone_id'][:20])}', 1, NEWID(), '{now_str()}', '{now_str()}', "
            f"'synth', {i}, N'{pcode}', 9.0, 38.7);"
        )
    lines.append("SET IDENTITY_INSERT dbo.Zone OFF;")

    # Sample down woredas if over the limit, deterministically.
    if len(woredas) > WOREDA_LIMIT:
        # Stratified by region so every region keeps at least one woreda.
        by_region: dict[str, list] = defaultdict(list)
        for w in woredas:
            by_region[w["region_id"]].append(w)
        # Allocate proportional quota per region, min 1.
        total_regions = len(by_region)
        per_region_base = max(1, WOREDA_LIMIT // total_regions)
        sampled: list = []
        for rid, lst in by_region.items():
            random.shuffle(lst)
            sampled.extend(lst[:per_region_base])
        # Top up with extras, weighted by region size.
        remaining = [w for w in woredas if w not in sampled]
        random.shuffle(remaining)
        sampled.extend(remaining[: max(0, WOREDA_LIMIT - len(sampled))])
        woredas_use = sampled[:WOREDA_LIMIT]
    else:
        woredas_use = woredas

    lines.append(f"-- dbo.Woreda (from bundle/seeds/entity-model/_build/woredas.json, capped at {WOREDA_LIMIT})")
    lines.append("SET IDENTITY_INSERT dbo.Woreda ON;")
    woreda_db: dict[str, int] = {}
    for i, w in enumerate(woredas_use, start=1):
        zone_id = zone_by_name_region.get((w["region_id"], w["zone"]))
        if zone_id is None:
            # No matching zone: skip silently.
            continue
        woreda_db[w["woreda_id"]] = i
        wname = w["name"][:50]
        # PCode is nvarchar(6): "W{seq:05d}". The shared woreda_id lives in WoredaCode.
        pcode = f"W{i:05d}"
        lines.append(
            f"INSERT INTO dbo.Woreda (ID, WoredaName, ZoneID, WoredaCode, rowguid, "
            f"IsActive, CreatedDate, ModifiedDate, ModifiedBy, SN, PCode, "
            f"LATITUDE, LONGITUDE) "
            f"VALUES ({i}, '{q(wname)}', {zone_id}, "
            f"'{q(w['woreda_id'][:20])}', NEWID(), 1, "
            f"'{now_str()}', '{now_str()}', 'synth', {i}, "
            f"N'{pcode}', 9.0, 38.7);"
        )
    lines.append("SET IDENTITY_INSERT dbo.Woreda OFF;")

    return region_db, zone_db, zone_by_name_region, woreda_db, region_pcode_order, woredas_use


def build_admin_units(
    lines: list[str],
    shared: dict,
    region_db: dict[str, int],
    zone_by_name_region: dict[tuple[str, str], int],
    woreda_db: dict[str, int],
    woredas_use: list[dict],
):
    """Rebuild AdministrativeUnit hierarchy:
       /1/                               Country root (Ethiopia)
       /1/<regionN>/                     Region
       /1/<regionN>/<zoneN>/             Zone
       /1/<regionN>/<zoneN>/<woredaN>/   Woreda

    The numeric segments are the sibling index (1-based) within that parent.
    """
    lines.append("-- AdministrativeUnit.AdministrativeUnit (hierarchy mirror)")
    lines.append("SET IDENTITY_INSERT AdministrativeUnit.AdministrativeUnit ON;")
    au_id = 1
    # Country root.
    lines.append(
        f"INSERT INTO AdministrativeUnit.AdministrativeUnit "
        f"(AdministrativeUnitID, Name, Description, Hierarchy, "
        f"Latitude, Longitude, IsActive, rowguid, CreatedDate, ModifiedDate, SN, PCode) "
        f"VALUES ({au_id}, 'Ethiopia', 'Country root', hierarchyid::Parse('/1/'), "
        f"9.0, 38.7, 1, NEWID(), '{now_str()}', '{now_str()}', {au_id}, N'ET');"
    )
    au_id += 1

    # Index regions by insertion order for hierarchyid sibling numbering.
    region_order: dict[str, int] = {rid: i for i, rid in enumerate(region_db.keys(), start=1)}

    # Regions.
    region_au: dict[str, int] = {}
    for r in shared["regions"]:
        rid = r["id"]
        r_i = region_order[rid]
        region_au[rid] = au_id
        # AU.PCode is nvarchar(6); region.code is the 2-char ISO-ish code (OR, AM, ...).
        pcode = r["code"][:6]
        lines.append(
            f"INSERT INTO AdministrativeUnit.AdministrativeUnit "
            f"(AdministrativeUnitID, Name, Description, Hierarchy, "
            f"Latitude, Longitude, IsActive, rowguid, CreatedDate, ModifiedDate, SN, PCode) "
            f"VALUES ({au_id}, '{q(r['name'])}', 'Region', "
            f"hierarchyid::Parse('/1/{r_i}/'), "
            f"9.0, 38.7, 1, NEWID(), '{now_str()}', '{now_str()}', {au_id}, "
            f"N'{q(pcode)}');"
        )
        au_id += 1

    # Zones: sibling index within region.
    zones_by_region: dict[str, list[dict]] = defaultdict(list)
    for z in shared["zones"]:
        zones_by_region[z["region_id"]].append(z)
    zone_au: dict[tuple[str, str], int] = {}
    for rid, zones in zones_by_region.items():
        if rid not in region_order:
            continue
        r_i = region_order[rid]
        for z_i, z in enumerate(zones, start=1):
            zone_au[(rid, z["name"])] = au_id
            # AU.PCode is nvarchar(6); zone_id is too long. Use Z{au_id:05d}.
            pcode = f"Z{au_id:05d}"
            lines.append(
                f"INSERT INTO AdministrativeUnit.AdministrativeUnit "
                f"(AdministrativeUnitID, Name, Description, Hierarchy, "
                f"Latitude, Longitude, IsActive, rowguid, CreatedDate, ModifiedDate, SN, PCode) "
                f"VALUES ({au_id}, '{q(z['name'][:80])}', 'Zone', "
                f"hierarchyid::Parse('/1/{r_i}/{z_i}/'), "
                f"9.0, 38.7, 1, NEWID(), '{now_str()}', '{now_str()}', {au_id}, "
                f"N'{pcode}');"
            )
            au_id += 1

    # Woredas: sibling index within zone.
    woreda_au: dict[str, int] = {}    # woreda_id (shared) -> AU id
    # Group inserted woredas by their zone+region so sibling indices are stable.
    woredas_by_zone: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for w in woredas_use:
        if w["woreda_id"] in woreda_db:
            woredas_by_zone[(w["region_id"], w["zone"])].append(w)
    for (rid, zname), wlist in woredas_by_zone.items():
        if rid not in region_order:
            continue
        r_i = region_order[rid]
        # Zone sibling index within region.
        zones_in_region = zones_by_region.get(rid, [])
        zone_index = {z["name"]: i for i, z in enumerate(zones_in_region, start=1)}
        z_i = zone_index.get(zname)
        if z_i is None:
            continue
        for w_i, w in enumerate(wlist, start=1):
            woreda_au[w["woreda_id"]] = au_id
            # AU.PCode is nvarchar(6); woreda_id is too long. Use W{au_id:05d}.
            pcode = f"W{au_id:05d}"
            lines.append(
                f"INSERT INTO AdministrativeUnit.AdministrativeUnit "
                f"(AdministrativeUnitID, Name, Description, Hierarchy, "
                f"Latitude, Longitude, IsActive, rowguid, CreatedDate, ModifiedDate, SN, PCode) "
                f"VALUES ({au_id}, '{q(w['name'][:80])}', 'Woreda', "
                f"hierarchyid::Parse('/1/{r_i}/{z_i}/{w_i}/'), "
                f"9.0, 38.7, 1, NEWID(), '{now_str()}', '{now_str()}', {au_id}, "
                f"N'{pcode}');"
            )
            au_id += 1
    lines.append("SET IDENTITY_INSERT AdministrativeUnit.AdministrativeUnit OFF;")

    return region_au, zone_au, woreda_au


def build_environments(
    lines: list[str],
    shared: dict,
):
    """USM.Environment <- epss_hubs.json (19 EPSS hubs).

    Returns:
      hub_db: shared-hub-id ("ADAMA") -> USM.Environment.EnvironmentID (int)
    """
    hubs = shared["epss_hubs"]
    lines.append(f"-- USM.Environment (from bundle/seeds/entity-model/_build/epss_hubs.json: {len(hubs)} hubs)")
    lines.append("SET IDENTITY_INSERT USM.Environment ON;")
    hub_db: dict[str, int] = {}
    for i, h in enumerate(hubs, start=1):
        hub_db[h["id"]] = i
        grp = 1 if h["id"] == "CENT" else 2
        lines.append(
            f"INSERT INTO USM.Environment "
            f"(EnvironmentID, Name, Description, EnvironmentGroupID, IsActive, "
            f"rowguid, EnvironmentCode, OperationalStatus, SiteStatusID, TrendStatusID) "
            f"VALUES ({i}, N'{q(h['name'])}', N'{q(h['name'])} location', {grp}, 1, "
            f"NEWID(), N'{q(h['id'])}', 1, 1, 2);"
        )
    lines.append("SET IDENTITY_INSERT USM.Environment OFF;")
    return hub_db


def build_products(lines: list[str], shared: dict) -> list[dict]:
    """Commodity.Product <- commodities.json (every shared commodity).

    Returns the commodity list in insertion order so downstream tables
    (mFlowUSSD.Commodities) re-use the same numeric IDs.
    """
    commodities = shared["commodities"]
    lines.append(f"-- Commodity.Product (from bundle/seeds/entity-model/_build/commodities.json: {len(commodities)} commodities)")
    lines.append("SET IDENTITY_INSERT Commodity.Product ON;")
    for i, c in enumerate(commodities, start=1):
        ctype = commodity_type_id(c)
        is_vac = commodity_is_vaccine(c)
        lines.append(
            f"INSERT INTO Commodity.Product "
            f"(ProductID, Name, Description, CommodityTypeID, IsActive, rowguid, "
            f"SN, IsVaccine) VALUES "
            f"({i}, N'{q(c['name'])}', N'{q(c['id'])} · {q(c.get('programme',''))}', "
            f"{ctype}, 1, NEWID(), {i}, {1 if is_vac else 0});"
        )
    lines.append("SET IDENTITY_INSERT Commodity.Product OFF;")
    return commodities


def build_institutions(
    lines: list[str],
    shared: dict,
    region_db: dict[str, int],
    woreda_db: dict[str, int],
    woreda_au: dict[str, int],
    hub_db: dict[str, int],
):
    """dbo.Institution <- facilities.json.

    Stores the shared facility_id in dbo.Institution.Town (it's NVARCHAR and
    not otherwise used canonically), and the MFR ID in dbo.Institution.IvrID
    (BIGINT, which fits the numeric MFR id). This lets dbt joins use
    Town for shared joins and IvrID for MFR cross-references.

    Returns:
      institution_db: shared-facility-id ("FAC-OR-HP-84a0dc") -> dbo.Institution.ID
      institution_rows: list of dicts with the inserted facility metadata
        so downstream mFlowUSSD can pick HEW-eligible facilities.
    """
    facilities = shared["facilities"]
    # Build region -> epss_hub lookup so we can map institutions to environments.
    region_to_hub = {r["id"]: r["epss_hub"] for r in shared["regions"]}

    # Build region -> list of inserted woreda dbo.IDs so facilities whose
    # specific woreda was dropped (by WOREDA_LIMIT) still land somewhere in
    # the right region.
    woredas_by_region_db: dict[str, list[int]] = defaultdict(list)
    woreda_au_by_region: dict[str, list[int]] = defaultdict(list)
    # Iterate the shared-woreda list filtered to those actually inserted.
    woreda_shared_lookup = {w["woreda_id"]: w for w in shared["woredas"]}
    for sw_id, dbo_wid in woreda_db.items():
        sw = woreda_shared_lookup.get(sw_id)
        if not sw:
            continue
        woredas_by_region_db[sw["region_id"]].append(dbo_wid)
        au = woreda_au.get(sw_id)
        if au is not None:
            woreda_au_by_region[sw["region_id"]].append(au)

    # Apply facility cap.
    facs_use = facilities[:FACILITY_LIMIT]

    lines.append(f"-- dbo.Institution (from bundle/seeds/entity-model/_build/facilities.json: {len(facs_use)} facilities)")
    lines.append("SET IDENTITY_INSERT dbo.Institution ON;")
    institution_db: dict[str, int] = {}
    institution_rows: list[dict] = []
    _inst_name_seen: dict[tuple, int] = {}
    inst_id = 1
    for fac in facs_use:
        wid = woreda_db.get(fac.get("woreda_id"))
        au_id = woreda_au.get(fac.get("woreda_id"))
        if wid is None:
            # Specific woreda dropped by WOREDA_LIMIT: fall back to a random
            # inserted woreda in the same region.
            region_woredas = woredas_by_region_db.get(fac.get("region_id"))
            if not region_woredas:
                continue
            wid = random.choice(region_woredas)
            region_aus = woreda_au_by_region.get(fac.get("region_id"))
            au_id = random.choice(region_aus) if region_aus else None
        au_clause = f"{au_id}" if au_id else "NULL"
        own_id = own_id_for(fac.get("ownership"))
        hub = region_to_hub.get(fac.get("region_id"))
        env_id = hub_db.get(hub, 1) if hub else 1
        raw_name = (fac.get("name") or f"Facility {inst_id}").strip()[:190]
        # Enforce the U_dbo_Institution_Name_Woreda_EnvironmentID unique key:
        # suffix Roman numerals on (name, woreda, environment) collisions.
        key = (raw_name.lower(), wid, env_id)
        n_seen = _inst_name_seen.get(key, 0) + 1
        _inst_name_seen[key] = n_seen
        if n_seen > 1:
            raw_name = f"{raw_name} {'I' * n_seen}"   # II, III, IIII: distinct and plausible
        name = q(raw_name[:200])
        facility_id_shared = q(fac.get("facility_id", "")[:100])
        # dbo.Institution.IvrID is BIGINT NOT NULL with no default. Use the
        # real MFR id when available, else 0 (sentinel for synthetic facility).
        mfr_id = fac.get("mfr_id")
        mfr_clause = f"{int(mfr_id)}" if isinstance(mfr_id, (int, float)) else "0"
        kebele = f"Kebele {(inst_id % 10) + 1}"
        lines.append(
            f"INSERT INTO dbo.Institution "
            f"(ID, Name, Phone, Woreda, Town, Kebele, RUType, Ownership, Zone, "
            f"Active, InProcess, CreatedDate, ModifiedDate, ModifiedBy, SN, "
            f"IsUsedAtFacility, IsDraft, IsLocalSite, AdministrativeUnitID, "
            f"OperationalStatus, NUrowguid, rowguid, LATITUDE, LONGITUDE, "
            f"EnvironmentID, IvrID) "
            f"VALUES ({inst_id}, N'{name}', NULL, {wid}, "
            # Town := shared facility_id (canonical NHDW key)
            f"N'{facility_id_shared}', N'{kebele}', "
            f"NULL, {own_id}, NULL, 1, 0, '{now_str()}', '{now_str()}', "
            f"'synth', {inst_id}, 1, 0, 0, {au_clause}, 1, NEWID(), NEWID(), "
            # IvrID := MFR id (numeric MFR cross-reference)
            f"9.0, 38.7, {env_id}, {mfr_clause});"
        )
        institution_db[fac["facility_id"]] = inst_id
        institution_rows.append({
            "inst_id": inst_id,
            "facility_id": fac["facility_id"],
            "mfr_id": fac.get("mfr_id"),
            "tier_id": fac.get("tier_id"),
            "ownership": fac.get("ownership"),
            "region_id": fac.get("region_id"),
            "name": fac.get("name"),
            "env_id": env_id,
        })
        inst_id += 1
    lines.append("SET IDENTITY_INSERT dbo.Institution OFF;")
    return institution_db, institution_rows


def build_mbrana_bridges(lines: list[str], institution_rows: list[dict], hub_db: dict[str, int]):
    """mBrana.InstitutionEnvironment: bridge between facility and EPSS hub.

    HubEnvironmentID = the central EPSS environment (CENT).
    EnvironmentID    = the institution's serving regional hub (env_id on the row).
    """
    lines.append("-- mBrana.InstitutionEnvironment")
    lines.append("SET IDENTITY_INSERT mBrana.InstitutionEnvironment ON;")
    central_id = hub_db.get("CENT", 1)
    bridge_id = 1
    # Cap to avoid bloating; 600 is enough to cover HEW-eligible institutions.
    for row in institution_rows[:600]:
        lines.append(
            f"INSERT INTO mBrana.InstitutionEnvironment "
            f"(ID, HubEnvironmentID, InstitutionID, EnvironmentID, IsDefault) "
            f"VALUES ({bridge_id}, {central_id}, {row['inst_id']}, {row['env_id']}, 1);"
        )
        bridge_id += 1
    lines.append("SET IDENTITY_INSERT mBrana.InstitutionEnvironment OFF;")


def _last_n_months_from_periods(periods: dict, n: int) -> list[str]:
    months = [m["month_id"] for m in periods.get("months", [])]
    return months[-n:] if months else []


def build_mflowussd(
    lines: list[str],
    shared: dict,
    commodities: list[dict],
    institution_rows: list[dict],
):
    """Mobile flow tables, driven by the HEW-eligible facility subset.

    Eligibility rule: ownership='public' AND tier_id ∈ {HP, HC}. This matches
    real-world mBrana adoption (HEWs at primary care edge).
    """
    eligible = [
        r for r in institution_rows
        if (r.get("ownership") or "").lower() == "public"
        and r.get("tier_id") in ("HP", "HC")
    ]
    # If something pathological happened, fall back to all institutions.
    if not eligible:
        eligible = institution_rows

    # mFlowUSSD.Commodities (mirror Commodity.Product)
    lines.append("-- mFlowUSSD.Commodities (mirror of Commodity.Product)")
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Commodities ON;")
    for i, c in enumerate(commodities, start=1):
        prog = c.get("programme", "GEN")
        code = f"{prog[:3].upper()}-{i:03d}"
        lines.append(
            f"INSERT INTO mFlowUSSD.Commodities "
            f"(CommodityId, CommodityCode, CommodityName) "
            f"VALUES ({i}, N'{q(code)}', N'{q(c['name'])}');"
        )
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Commodities OFF;")

    n_comm = len(commodities)

    # mFlowUSSD.Institutions (one row per HEW-eligible facility, capped) ─
    mobile_sites = eligible[:max(MOBILE_USER_COUNT, 200)]
    lines.append(f"-- mFlowUSSD.Institutions ({len(mobile_sites)} HEW-eligible sites)")
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Institutions ON;")
    # Map shared facility_id -> mFlowUSSD.InstitutionId (sequential 1..N).
    mobile_inst_seq: dict[str, int] = {}
    for i, row in enumerate(mobile_sites, start=1):
        mobile_inst_seq[row["facility_id"]] = i
        lines.append(
            f"INSERT INTO mFlowUSSD.Institutions "
            f"(InstitutionId, InstitutionName, ParentInstitutionId) "
            f"VALUES ({i}, N'{q(row['facility_id'])}', NULL);"
        )
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Institutions OFF;")

    # mFlowUSSD.Users (HEWs / facility staff with phones)
    n_users = min(MOBILE_USER_COUNT, len(mobile_sites))
    lines.append(f"-- mFlowUSSD.Users ({n_users} mobile users)")
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Users ON;")
    for i in range(1, n_users + 1):
        phone = f"+2519{random.randint(10_000_000, 99_999_999)}"
        lines.append(
            f"INSERT INTO mFlowUSSD.Users "
            f"(UserId, UserName, PhoneNo, InstitutionId) "
            f"VALUES ({i}, N'HEW {i:04d}', N'{phone}', {i});"
        )
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Users OFF;")

    # mFlowUSSD.SOHLogs
    # Last MONTHS_HISTORY months of the shared calendar.
    months = _last_n_months_from_periods(shared["periods"], MONTHS_HISTORY)
    if not months:
        # Fallback: synthesize MONTHS_HISTORY months back from today.
        today = date.today().replace(day=1)
        months = []
        for m_back in range(MONTHS_HISTORY - 1, -1, -1):
            d = today
            for _ in range(m_back):
                d = (d.replace(day=1) - timedelta(days=1)).replace(day=1)
            months.append(d.strftime("%Y-%m"))
        months = list(reversed(months))

    # USSD last-mile reporting
    # Only ~ACTIVE_REPORTER_FRACTION of registered facilities have an active HEW
    # logging stock; the rest are registered but dormant (never log). Active
    # sites report with varying month-to-month engagement (some most months,
    # some sporadically), so mart_data_quality completeness (distinct logging /
    # registered) reads about 58%.
    n_months = len(months)
    n_sites = len(mobile_sites)
    site_ids = list(range(1, n_sites + 1))
    random.shuffle(site_ids)
    n_active = max(1, round(n_sites * ACTIVE_REPORTER_FRACTION))
    active_sites = sorted(site_ids[:n_active])
    # per-site engagement = share of months it reports (spread, centred ~0.6).
    engagement = {s: 0.30 + 0.65 * random.betavariate(2.2, 2.0) for s in active_sites}
    commodities_per_site = 5

    lines.append(
        f"-- mFlowUSSD.SOHLogs (last {n_months} months · {n_active}/{n_sites} "
        f"registered facilities actively reporting · varied engagement)")
    lines.append("SET IDENTITY_INSERT mFlowUSSD.SOHLogs ON;")

    log_id = 1
    for month in months:
        # Report day: a random day in the first four weeks of the month.
        try:
            y, m = month.split("-")
            base_day = date(int(y), int(m), 1)
        except Exception:
            base_day = date.today()
        for inst_seq in active_sites:
            # this active site reports this month with probability = its engagement
            if random.random() >= engagement[inst_seq]:
                continue
            d = base_day + timedelta(days=random.randint(0, 27))
            # a HEW logs a varying subset of their narrow kit (3..5 items)
            k = random.randint(3, commodities_per_site)
            for comm_id in random.sample(range(1, n_comm + 1), k=min(k, n_comm)):
                qty = random.randint(0, 5_000)
                lines.append(
                    f"INSERT INTO mFlowUSSD.SOHLogs "
                    f"(SohLogId, Date, InstitutionId, CommodityId, Quantity) "
                    f"VALUES ({log_id}, '{d.isoformat()}', {inst_seq}, {comm_id}, {qty});"
                )
                log_id += 1
    lines.append("SET IDENTITY_INSERT mFlowUSSD.SOHLogs OFF;")

    # mFlowUSSD.Requests + RequestLines (~616 total)
    lines.append(f"-- mFlowUSSD.Requests (~{REQUEST_TARGET} target)")
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Requests ON;")
    request_id_seq = 1
    # Even distribution of REQUEST_TARGET across mobile sites
    requests_left = REQUEST_TARGET
    site_indices = list(range(1, n_sites + 1))
    random.shuffle(site_indices)
    # First pass: round-robin 1 each, second pass: random until target hit.
    today = date.today()
    while requests_left > 0:
        for inst_seq in site_indices:
            if requests_left <= 0:
                break
            d = today - timedelta(days=random.randint(0, n_months * 30))
            status = random.choice(["PENDING", "APPROVED", "REJECTED", "FULFILLED"])
            lines.append(
                f"INSERT INTO mFlowUSSD.Requests "
                f"(RequestId, RequestDate, InstitutionId, RequestStatus) "
                f"VALUES ({request_id_seq}, '{d.isoformat()}', {inst_seq}, N'{status}');"
            )
            request_id_seq += 1
            requests_left -= 1
    lines.append("SET IDENTITY_INSERT mFlowUSSD.Requests OFF;")

    lines.append("SET IDENTITY_INSERT mFlowUSSD.RequestLines ON;")
    line_id = 1
    for rid in range(1, request_id_seq):
        for comm_id in random.sample(range(1, n_comm + 1), k=random.randint(2, 5)):
            qr = random.randint(50, 2_000)
            qa = qr if random.random() < 0.7 else 0
            lines.append(
                f"INSERT INTO mFlowUSSD.RequestLines "
                f"(RequestLineId, CommodityId, RequestId, Quantity, Approved) "
                f"VALUES ({line_id}, {comm_id}, {rid}, {qr}, {qa});"
            )
            line_id += 1
    lines.append("SET IDENTITY_INSERT mFlowUSSD.RequestLines OFF;")

    # mFlowUSSD.ASNs + ASNLines (~184 total)
    lines.append(f"-- mFlowUSSD.ASNs (~{ASN_TARGET} target)")
    lines.append("SET IDENTITY_INSERT mFlowUSSD.ASNs ON;")
    asn_id_seq = 1
    asns_left = ASN_TARGET
    random.shuffle(site_indices)
    while asns_left > 0:
        for inst_seq in site_indices:
            if asns_left <= 0:
                break
            d = today - timedelta(days=random.randint(0, n_months * 30))
            asn_type = random.choice(["DELIVERY", "TRANSFER", "RETURN"])
            asn_no = f"ASN-{asn_id_seq:08d}"
            volume = round(random.uniform(0.5, 50.0), 2)
            lines.append(
                f"INSERT INTO mFlowUSSD.ASNs "
                f"(ASNId, ASNDate, ASNNo, ASNType, InstitutionId, Volume) "
                f"VALUES ({asn_id_seq}, '{d.isoformat()}', N'{asn_no}', N'{asn_type}', "
                f"{inst_seq}, {volume});"
            )
            asn_id_seq += 1
            asns_left -= 1
    lines.append("SET IDENTITY_INSERT mFlowUSSD.ASNs OFF;")

    lines.append("SET IDENTITY_INSERT mFlowUSSD.ASNLines ON;")
    asn_line_id = 1
    for aid in range(1, asn_id_seq):
        for comm_id in random.sample(range(1, n_comm + 1), k=random.randint(1, 4)):
            qty = random.randint(50, 5_000)
            lines.append(
                f"INSERT INTO mFlowUSSD.ASNLines "
                f"(ASNLineId, CommodityId, ASNId, Quantity) "
                f"VALUES ({asn_line_id}, {comm_id}, {aid}, {qty});"
            )
            asn_line_id += 1
    lines.append("SET IDENTITY_INSERT mFlowUSSD.ASNLines OFF;")



def _synth_profile() -> str:
    """The country this synthesis is for, stamped beside the version so a
    packed dump is trusted on a boot for the same country and rejected on one
    for another. Mirrors the build-dir resolution above."""
    import os
    key = os.environ.get("NHDW_PROFILE", "").strip()
    if key:
        return key[:32]
    eb = os.environ.get("NHDW_ENTITY_BUILD", "").strip()
    if eb:
        from pathlib import Path as _P
        stem = _P(eb).name.replace("_build-entity-model", "").lstrip(".-")
        return (stem or "et-v3")[:32]
    return "et-v3"


def main() -> int:
    shared = load_shared()
    print(f"==> Loaded shared model: "
          f"{len(shared['regions'])} regions · "
          f"{len(shared['zones'])} zones · "
          f"{len(shared['woredas'])} woredas · "
          f"{len(shared['facilities'])} facilities · "
          f"{len(shared['epss_hubs'])} hubs · "
          f"{len(shared['commodities'])} commodities")
    print(f"    Caps: WOREDA_LIMIT={WOREDA_LIMIT}, FACILITY_LIMIT={FACILITY_LIMIT}, "
          f"MOBILE_USERS={MOBILE_USER_COUNT}, MONTHS_HISTORY={MONTHS_HISTORY}")

    lines = build_clear()
    build_lookups(lines)
    (region_db, zone_db, zone_by_name_region, woreda_db,
     region_pcode_order, woredas_use) = build_geography(lines, shared)
    region_au, zone_au, woreda_au = build_admin_units(
        lines, shared, region_db, zone_by_name_region, woreda_db, woredas_use)
    hub_db = build_environments(lines, shared)
    commodities = build_products(lines, shared)
    institution_db, institution_rows = build_institutions(
        lines, shared, region_db, woreda_db, woreda_au, hub_db)
    build_mbrana_bridges(lines, institution_rows, hub_db)
    build_mflowussd(lines, shared, commodities, institution_rows)
    # The synth marker is in the same transaction as the data, so a failed run
    # rolls back to the previous consistent data and marker pair.
    lines.extend([
        # Version and country: a packed dump stamped only with a version is
        # trusted by the seed-only-if-current check no matter which country it
        # holds. Profile is added idempotently for pre-existing tables.
        "IF OBJECT_ID('dbo.SynthMeta','U') IS NULL CREATE TABLE dbo.SynthMeta "
        "(SynthVersion INT NOT NULL, StampedAt DATETIME NOT NULL, Profile NVARCHAR(32) NULL);",
        "IF COL_LENGTH('dbo.SynthMeta','Profile') IS NULL "
        "ALTER TABLE dbo.SynthMeta ADD Profile NVARCHAR(32) NULL;",
        "DELETE FROM dbo.SynthMeta;",
        f"INSERT INTO dbo.SynthMeta (SynthVersion, StampedAt, Profile) "
        f"VALUES ({SYNTH_VERSION}, GETDATE(), '{_synth_profile()}');",
    ])
    lines.append("COMMIT;")

    sql_file = Path("/tmp/mbrana_seed.sql")
    sql_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"==> Wrote {sql_file.stat().st_size / 1024 / 1024:.1f} MB SQL "
          f"({len(lines):,} statements)")

    print("==> Copying into container...")
    subprocess.run(["docker", "cp", str(sql_file), f"{CONTAINER}:/tmp/seed.sql"], check=True)
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "chown",
                    "mssql:0", "/tmp/seed.sql"], check=True)

    print("==> Executing (several minutes for the mobile flow tables)...")
    subprocess.run([
        "docker", "exec", "-i", CONTAINER,
        "/opt/mssql-tools18/bin/sqlcmd",
        "-S", "localhost", "-U", "sa", "-P", SA_PASS,
        "-C", "-d", DB, "-i", "/tmp/seed.sql", "-b",
    ], check=True)

    print("==> Row counts:")
    subprocess.run([
        "docker", "exec", "-i", CONTAINER,
        "/opt/mssql-tools18/bin/sqlcmd",
        "-S", "localhost", "-U", "sa", "-P", SA_PASS,
        "-C", "-d", DB, "-Q", """
SELECT 'AdministrativeUnit.AdministrativeUnit'  AS tbl, COUNT(*) AS n FROM AdministrativeUnit.AdministrativeUnit
UNION ALL SELECT 'dbo.Region',                         COUNT(*) FROM dbo.Region
UNION ALL SELECT 'dbo.Zone',                           COUNT(*) FROM dbo.Zone
UNION ALL SELECT 'dbo.Woreda',                         COUNT(*) FROM dbo.Woreda
UNION ALL SELECT 'dbo.Institution',                    COUNT(*) FROM dbo.Institution
UNION ALL SELECT 'USM.Environment',                    COUNT(*) FROM USM.Environment
UNION ALL SELECT 'Commodity.Product',                  COUNT(*) FROM Commodity.Product
UNION ALL SELECT 'mBrana.SiteStatus',                  COUNT(*) FROM mBrana.SiteStatus
UNION ALL SELECT 'mBrana.TrendStatus',                 COUNT(*) FROM mBrana.TrendStatus
UNION ALL SELECT 'mBrana.InstitutionEnvironment',      COUNT(*) FROM mBrana.InstitutionEnvironment
UNION ALL SELECT 'mFlowUSSD.Commodities',              COUNT(*) FROM mFlowUSSD.Commodities
UNION ALL SELECT 'mFlowUSSD.Institutions',             COUNT(*) FROM mFlowUSSD.Institutions
UNION ALL SELECT 'mFlowUSSD.Users',                    COUNT(*) FROM mFlowUSSD.Users
UNION ALL SELECT 'mFlowUSSD.SOHLogs',                  COUNT(*) FROM mFlowUSSD.SOHLogs
UNION ALL SELECT 'mFlowUSSD.Requests',                 COUNT(*) FROM mFlowUSSD.Requests
UNION ALL SELECT 'mFlowUSSD.RequestLines',             COUNT(*) FROM mFlowUSSD.RequestLines
UNION ALL SELECT 'mFlowUSSD.ASNs',                     COUNT(*) FROM mFlowUSSD.ASNs
UNION ALL SELECT 'mFlowUSSD.ASNLines',                 COUNT(*) FROM mFlowUSSD.ASNLines;
""",
    ], check=True)

    print()
    print("OK: mBrana / HCMISWCloud synthesis complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
