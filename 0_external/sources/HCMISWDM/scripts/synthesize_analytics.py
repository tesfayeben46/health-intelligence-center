#!/usr/bin/env python3
"""Generate the synthetic HCMIS WDM analytics layer: Dim.*, Commodity.* and Fact.* tables.

Layered on top of the base Vaccine.* synthesis (scripts/synthesize.py); all
rows are generated from the shared demonstration entity model.
This script populates:

  Dimensions (analytics layer):
    Dim.Mode          Central / Hub / Facility (3 rows)
    Dim.Program       programmes from bundle/seeds/entity-model/_build/programmes.json
    Dim.Environment   EPSS hubs (mirrors Vaccine.Environment IDs)
    Dim.Item          full commodity catalogue, EPI vaccines first (mirrors Vaccine.Item IDs)
    Dim.Date          daily rows built from bundle/seeds/entity-model/_build/periods.json
                      (Gregorian and Ethiopian columns from the shared calendar)
  Commodity reference data (master catalogue):
    Commodity.CommodityType, Country, SupplierType, Category, UnitOfIssue,
    DosageForm, Program, Manufacturer, Supplier, Product
        Supplier names come from the `supplier` field on commodities.json
        (GAVI, GFATM, PEPFAR, UNFPA, UNICEF, COVAX, WFP, EPSS).
        Product holds every commodity in the shared catalogue.
  Fact tables (the ingestor's extraction targets):
    Fact.DailyRecord     monthly facts by environment, product, mode and month
                         (capped at SYNTH_FACT_DAILY_CAP, default 80k rows)
    Fact.Inventory       monthly inventory snapshots by environment and mode

Run after scripts/synthesize.py.
"""

from __future__ import annotations

import json
import math as _math
import os
import random
import subprocess
import sys
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
ENV_FILE = ROOT / ".env"

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

if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DEFAULT_SEED = "20260624"  # entity-model random_seed
SEED = os.environ.get("SYNTH_RANDOM_SEED", DEFAULT_SEED).strip() or DEFAULT_SEED

# Synthesis-contract version: bump whenever synthesize.py or this script
# changes what it writes (values, distributions, schema) or the shared entity
# model they read changes shape. Stamped into dbo.SynthMeta at the end of a
# successful run; bundle/scripts/sources-up.sh re-synthesizes when a restored
# .bak carries a different (or no) version, so a stale packed .bak cannot mask
# a synthesizer change.
SYNTH_VERSION = 4
try:
    random.seed(int(SEED))
except ValueError:
    random.seed(SEED)

CONTAINER = "hcmiswdm-sqlserver"
SA_PASSWORD = os.environ["HCMISWDM_SA_PASSWORD"]
DB = os.environ.get("HCMISWDM_DATABASE", "HCMISWDM")
YEARS_HISTORY = int(os.environ.get("SYNTH_YEARS_HISTORY", "3"))
# Cap for Fact.DailyRecord. With 19 hubs x 34 commodities x 3 modes x 36 months
# the natural count is ~70k, under 80k; the cap keeps a larger
# SYNTH_YEARS_HISTORY from growing the table unbounded.
FACT_DAILY_CAP = int(os.environ.get("SYNTH_FACT_DAILY_CAP", "80000"))


# Load canonical shared data
def _load(name: str):
    return json.loads((SHARED_BUILD / name).read_text(encoding="utf-8"))


EPSS_HUBS = _load("epss_hubs.json")          # 19 hubs
PROGRAMMES = _load("programmes.json")        # 8 programmes
ALL_COMMODITIES = _load("commodities.json")  # 35 commodities
PERIODS = _load("periods.json")              # days/weeks/months/quarters
REGIONS = _load("regions.json")              # 14 regions

# EPI commodities (mirror of Vaccine.Item).
EPI_VACCINES = [c for c in ALL_COMMODITIES if c.get("programme") == "EPI"]

# Programme code -> ProgramID (1-based, in catalogue order).
PROG_TO_ID = {p["id"]: idx for idx, p in enumerate(PROGRAMMES, start=1)}

# Deduplicate supplier names while preserving first-seen order.
SUPPLIER_NAMES: list[str] = []
_seen: set[str] = set()
for c in ALL_COMMODITIES:
    s = c.get("supplier")
    if s and s not in _seen:
        SUPPLIER_NAMES.append(s)
        _seen.add(s)
SUPPLIER_TO_ID = {s: idx for idx, s in enumerate(SUPPLIER_NAMES, start=1)}

# Region groupings for Dim.Environment.EnvironmentGroupCode.
REGION_TO_GROUP = {r["id"]: r["code"] for r in REGIONS}

MODES = [(1, "Central", "CENT"), (2, "Hub", "HUB"), (3, "Facility", "FAC")]

COMMODITY_TYPES = [
    (1, "Vaccine", "VAC"),
    (2, "Pharmaceutical", "PHR"),
    (3, "Medical Supply", "SUP"),
    (4, "Diagnostic", "DIA"),
]
# Map shared `form` -> CommodityTypeID for sensible Product.CommodityTypeID.
FORM_TO_TYPE = {
    "vial": 1, "ampoule": 1,
    "tablet": 2, "capsule": 2, "sachet": 2, "cycle": 2,
    "kit": 4, "cart": 4,
    "net": 3, "implant": 3, "bag": 3,
}

COMMODITY_CATEGORIES = [
    (1, "Vaccines", "VAC"),
    (2, "Anti-malarials", "AMA"),
    (3, "Antiretrovirals", "ARV"),
    (4, "TB Drugs", "TBD"),
    (5, "Diagnostics", "DIA"),
    (6, "Family Planning", "FP"),
    (7, "NCD", "NCD"),
    (8, "Nutrition", "NUT"),
    (9, "MNCH", "MCH"),
    (10, "General", "GEN"),
]
PROG_TO_CATEGORY = {
    "EPI": 1, "MALARIA": 2, "HIV": 3, "TB": 4,
    "FP": 6, "NCD": 7, "NUTRITION": 8, "MNCH": 9,
}
# Map shared `form` -> Commodity.UnitOfIssue (FK to UNITS_OF_ISSUE).
UNITS_OF_ISSUE = [
    (1, "Dose", 1),
    (2, "Vial", 10),
    (3, "Tablet", 1),
    (4, "Ampoule", 1),
    (5, "Sachet", 1),
    (6, "Kit", 1),
    (7, "Net", 1),
    (8, "Cycle", 21),
    (9, "Cartridge", 1),
    (10, "Implant", 1),
    (11, "Bag", 1),
]
FORM_TO_UNITOFISSUE = {
    "vial": 2, "ampoule": 4, "tablet": 3, "sachet": 5,
    "kit": 6, "net": 7, "cycle": 8, "cart": 9,
    "implant": 10, "bag": 11,
}
DOSAGE_FORMS = [
    (1, "Solution for injection"), (2, "Oral suspension"),
    (3, "Tablet"), (4, "Capsule"), (5, "Diagnostic kit"),
    (6, "Net"), (7, "Sachet"), (8, "Implant"),
]
COUNTRIES = [
    (1, "Ethiopia",          "ET", "ETH", 231, "ET"),
    (2, "India",             "IN", "IND", 356, "IN"),
    (3, "Belgium",           "BE", "BEL",  56, "BE"),
    (4, "Switzerland",       "CH", "CHE", 756, "CH"),
    (5, "United States",     "US", "USA", 840, "US"),
    (6, "Korea, Republic of","KR", "KOR", 410, "KR"),
]
SUPPLIER_TYPES = [
    (1, "Direct Manufacturer", "DM"),
    (2, "Distributor", "DI"),
    (3, "Donation Agency", "DA"),
]
MANUFACTURERS = [
    ("Serum Institute of India",  2),
    ("Bharat Biotech",            2),
    ("GSK Biologicals",           3),
    ("Sanofi Pasteur",            3),
    ("Pfizer",                    5),
    ("Moderna",                   5),
    ("Janssen Pharmaceuticals",   5),
    ("LG Chem",                   6),
    ("SK Bioscience",             6),
    ("Ethiopian Pharmaceutical Manufacturing", 1),
]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def q(s: str) -> str:
    return s.replace("'", "''")


def gen_dim_mode() -> list[str]:
    out = ["-- Dim.Mode"]
    for mid, name, code in MODES:
        out.append(
            f"INSERT INTO Dim.Mode (ModeID, Name, Description, IsActive, rowguid, "
            f"ModeCode, SN) VALUES ({mid}, N'{name}', N'{name} operational mode',"
            f" 1, NEWID(), N'{code}', {mid});"
        )
    return out


def gen_dim_program() -> list[str]:
    """Dim.Program from bundle/seeds/entity-model/_build/programmes.json (8 rows)."""
    out = ["-- Dim.Program (from bundle/seeds/entity-model/_build/programmes.json)"]
    for idx, prg in enumerate(PROGRAMMES, start=1):
        code = prg["id"]
        name = prg["name"]
        desc = f"{name} programme"
        out.append(
            f"INSERT INTO Dim.Program (ProgramID, ProgramCode, Name, Description)"
            f" VALUES ({idx}, N'{q(code)}', N'{q(name)}', N'{q(desc)}');"
        )
    return out


def gen_dim_environment() -> list[str]:
    """Mirror Vaccine.Environment IDs so Fact-table joins line up."""
    out = ["-- Dim.Environment (mirrors Vaccine.Environment IDs)"]
    for idx, hub in enumerate(EPSS_HUBS, start=1):
        code = hub["id"]
        name = hub["name"]
        grp = REGION_TO_GROUP.get(hub.get("region_id", ""), "CENT")
        desc = f"hub_id={code} | region_id={hub.get('region_id', '')}"
        out.append(
            f"INSERT INTO Dim.Environment (EnvironmentID, Name, Description, "
            f"IsActive, rowguid, EnvironmentCode, EnvironmentGroupCode) "
            f"VALUES ({idx}, N'{q(name)}', N'{q(desc)}', 1, NEWID(), "
            f"N'{q(code)}', N'{q(grp)}');"
        )
    return out


def gen_dim_item() -> list[str]:
    """Dim.Item: every commodity in the shared model.

    Fact.DailyRecord generates rows for every commodity, so Dim.Item must
    carry the full catalogue (not just EPI). The first 10 IDs are still
    the EPI vaccines (preserves the Vaccine.Item ordering) so cross-source
    joins via vaccine commodity ids continue to work.
    """
    out = ["-- Dim.Item (full commodity catalogue · EPI first to mirror Vaccine.Item)"]
    seen_ids: set[str] = set()
    ordered: list[dict] = []
    for c in EPI_VACCINES:
        if c["id"] in seen_ids:
            continue
        ordered.append(c); seen_ids.add(c["id"])
    for c in ALL_COMMODITIES:
        if c["id"] in seen_ids:
            continue
        ordered.append(c); seen_ids.add(c["id"])
    for idx, c in enumerate(ordered, start=1):
        out.append(
            f"INSERT INTO Dim.Item (ItemID, SN, FullItemName) "
            f"VALUES ({idx}, {idx}, N'{q(c['name'])}');"
        )
    return out


# Dim.Date from periods.json
ETH_MONTHS = [
    "", "Meskerem", "Tikemt", "Hidar", "Tahesas", "Tir", "Yekatit",
    "Megabit", "Miyazya", "Genbot", "Sene", "Hamle", "Nehase", "Pagume",
]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday",
               "Friday", "Saturday", "Sunday"]
MONTHS_EN = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]


def gen_dim_date() -> list[str]:
    """One Dim.Date row per day in periods.json (~1,277 rows over ~3.5 yrs).

    All Ethiopian + Gregorian + ISO-week + FY-EC values come from the
    canonical periods.json so every source (DHIS2/HCMIS/MFR/EthioEMR/mBrana)
    agrees on the same calendar dimension.
    """
    out = [
        "-- Dim.Date (from bundle/seeds/entity-model/_build/periods.json: daily grain)",
        "SET IDENTITY_INSERT Dim.Date ON;",
    ]
    days = PERIODS.get("days", [])
    for d in days:
        g = date.fromisoformat(d["gregorian"])
        date_key = int(g.strftime("%Y%m%d"))
        full = g.strftime("%Y-%m-%d 00:00:00")
        weekday = g.weekday()  # 0=Mon
        day_name = WEEKDAYS_EN[weekday]
        weekend = "Weekend" if weekday >= 5 else "Weekday"
        # Ethiopian fiscal year = Hamle (Jul 8 ≈ month 7) start. We use the
        # FY-EC string from periods.json as the authoritative fiscal label.
        fy_ec = d.get("fy_ec", "FY0000")  # e.g. "FY2015"
        try:
            fy_year_num = int(fy_ec.replace("FY", ""))
        except ValueError:
            fy_year_num = g.year
        eth_y, eth_m, eth_d = d["et_year"], d["et_month"], d["et_day"]
        eth_full = f"{eth_y:04d}-{eth_m:02d}-{eth_d:02d}"
        is_last = "Y" if (g + timedelta(days=1)).day == 1 else "N"
        iso_week = d.get("iso_week", "")
        try:
            week_num = int(iso_week.split("-W")[1]) if "-W" in iso_week else g.isocalendar()[1]
        except (ValueError, IndexError):
            week_num = g.isocalendar()[1]
        cal_quarter = (g.month - 1) // 3 + 1
        # Ethiopian-FY month: Hamle (Jul=7) -> month 1 -> fiscal-month-of-year.
        if g.month >= 7:
            fy_mo = g.month - 6
        else:
            fy_mo = g.month + 6
        fy_qtr = (fy_mo - 1) // 3 + 1
        eth_month_name = ETH_MONTHS[eth_m] if 1 <= eth_m <= 13 else "Unknown"
        out.append(
            f"INSERT INTO Dim.Date (DateKey, FullDate, DateName, DateNameUS, "
            f"DateNameEU, [DayOfWeek], DayNameOfWeek, DayOfMonth, DayOfYear, "
            f"WeekdayWeekend, WeekOfYear, MonthName, MonthOfYear, IsLastDayOfMonth, "
            f"CalendarQuarter, CalendarYear, CalendarYearMonth, CalendarYearQtr, "
            f"FiscalMonthOfYear, FiscalQuarter, FiscalYear, FiscalYearMonth, "
            f"FiscalYearQtr, EthYear, EthMonth, EthDayOfMonth, EthDayNameOfWeek, "
            f"EthMonthName, EthFullDate, AuditKey, IsHoliday) VALUES "
            f"({date_key}, '{full}', '{g.strftime('%Y-%m-%d')}', "
            f"'{g.strftime('%m/%d/%Y')}', '{g.strftime('%d/%m/%Y')}', "
            f"{weekday + 1}, '{day_name:<10}', {g.day}, "
            f"{g.timetuple().tm_yday}, '{weekend}', {week_num}, "
            f"'{MONTHS_EN[g.month]:<10}', {g.month}, '{is_last}', "
            f"{cal_quarter}, {g.year}, "
            f"'{g.strftime('%Y-%m')}', '{g.year}Q{cal_quarter}', "
            f"{fy_mo}, {fy_qtr}, {fy_year_num}, '{fy_ec}', "
            f"'{fy_ec}Q{fy_qtr}', "
            f"{eth_y}, {eth_m}, {eth_d}, '', N'{q(eth_month_name)}', "
            f"'{eth_full}', {date_key}, 0);"
        )
    out.append("SET IDENTITY_INSERT Dim.Date OFF;")
    return out


def gen_commodity_reference() -> list[str]:
    """Commodity.* reference data and Product (every commodity in the shared catalogue)."""
    out = ["-- Commodity reference data"]

    out.append("SET IDENTITY_INSERT Commodity.CommodityType ON;")
    for cid, name, code in COMMODITY_TYPES:
        out.append(
            f"INSERT INTO Commodity.CommodityType "
            f"(CommodityTypeID, Name, Description, IsActive, rowguid, "
            f"CommodityTypeCode, SN) VALUES "
            f"({cid}, N'{name}', N'{name} type', 1, NEWID(), N'{code}', {cid});"
        )
    out.append("SET IDENTITY_INSERT Commodity.CommodityType OFF;")

    out.append("SET IDENTITY_INSERT Commodity.Country ON;")
    for cid, name, a2, a3, num, pcode in COUNTRIES:
        out.append(
            f"INSERT INTO Commodity.Country "
            f"(CountryID, Name, Alpha2code, Alpha3code, Numeric, rowguid, "
            f"Longitude, Latitude, PCode, IsActive) VALUES "
            f"({cid}, N'{q(name)}', N'{a2}', N'{a3}', {num}, NEWID(), "
            f"0, 0, N'{pcode}', 1);"
        )
    out.append("SET IDENTITY_INSERT Commodity.Country OFF;")

    out.append("SET IDENTITY_INSERT Commodity.SupplierType ON;")
    for sid, name, code in SUPPLIER_TYPES:
        out.append(
            f"INSERT INTO Commodity.SupplierType "
            f"(SupplierTypeID, Name, Description, IsActive, rowguid, "
            f"SupplierTypeCode) VALUES "
            f"({sid}, N'{name}', N'{name} category', 1, NEWID(), N'{code}');"
        )
    out.append("SET IDENTITY_INSERT Commodity.SupplierType OFF;")

    out.append("SET IDENTITY_INSERT Commodity.Category ON;")
    for cid, name, code in COMMODITY_CATEGORIES:
        out.append(
            f"INSERT INTO Commodity.Category "
            f"(CategoryID, Name, CategoryCode, Description, rowguid, IsActive) "
            f"VALUES ({cid}, N'{q(name)}', N'{code}', N'{q(name)} category', "
            f"NEWID(), 1);"
        )
    out.append("SET IDENTITY_INSERT Commodity.Category OFF;")

    out.append("SET IDENTITY_INSERT Commodity.UnitOfIssue ON;")
    for uid, name, qty in UNITS_OF_ISSUE:
        out.append(
            f"INSERT INTO Commodity.UnitOfIssue "
            f"(UnitofIssueID, DisplayText, Quantity, IsActive, rowguid, SN) "
            f"VALUES ({uid}, N'{name}', {qty}, 1, NEWID(), {uid});"
        )
    out.append("SET IDENTITY_INSERT Commodity.UnitOfIssue OFF;")

    out.append("SET IDENTITY_INSERT Commodity.DosageForm ON;")
    for did, name in DOSAGE_FORMS:
        out.append(
            f"INSERT INTO Commodity.DosageForm "
            f"(DosageFormID, Name, Description, IsActive, rowguid, SN) "
            f"VALUES ({did}, N'{q(name)}', N'{q(name)}', 1, NEWID(), {did});"
        )
    out.append("SET IDENTITY_INSERT Commodity.DosageForm OFF;")

    out.append("SET IDENTITY_INSERT Commodity.Program ON;")
    # ProgramCode is nvarchar(5): shorten the programme ids to 5 chars
    # (matches the short EPSS programme codes used by the upstream system).
    PROG_CODE_5 = {"HIV":"HIV", "TB":"TB", "EPI":"EPI", "MNCH":"MNCH",
                   "NCD":"NCD", "MALARIA":"MAL", "FP":"FP", "NUTRITION":"NUTR"}
    for idx, prg in enumerate(PROGRAMMES, start=1):
        code = PROG_CODE_5.get(prg["id"], prg["id"][:5])
        name = prg["name"]
        desc = f"{name} programme"
        out.append(
            f"INSERT INTO Commodity.Program "
            f"(ProgramID, Name, Description, ProgramCode, IsActive, rowguid, SN) "
            f"VALUES ({idx}, N'{q(name)}', N'{q(desc)}', N'{q(code)}', 1, NEWID(), {idx});"
        )
    out.append("SET IDENTITY_INSERT Commodity.Program OFF;")

    out.append("SET IDENTITY_INSERT Commodity.Manufacturer ON;")
    for idx, (name, country_id) in enumerate(MANUFACTURERS, start=1):
        out.append(
            f"INSERT INTO Commodity.Manufacturer "
            f"(ManufacturerID, Name, IsActive, rowguid, SN, CountryID, "
            f"Longitude, Latitude) VALUES "
            f"({idx}, N'{q(name)}', 1, NEWID(), {idx}, {country_id}, 0, 0);"
        )
    out.append("SET IDENTITY_INSERT Commodity.Manufacturer OFF;")

    # Suppliers come from the `supplier` field on commodities.json.
    # GAVI, GFATM, PEPFAR, UNFPA, UNICEF, COVAX and WFP are donors or agencies;
    # EPSS is the national direct-import channel.
    SUPPLIER_META = {
        "GAVI":   {"country_id": 4, "type_id": 3, "is_direct": True},   # CH
        "GFATM":  {"country_id": 4, "type_id": 3, "is_direct": True},   # CH
        "PEPFAR": {"country_id": 5, "type_id": 3, "is_direct": True},   # US
        "UNFPA":  {"country_id": 5, "type_id": 3, "is_direct": True},   # US (HQ NY)
        "UNICEF": {"country_id": 3, "type_id": 3, "is_direct": True},   # BE (Supply Div Copenhagen ≈ EU)
        "COVAX":  {"country_id": 4, "type_id": 3, "is_direct": True},   # CH
        "WFP":    {"country_id": 4, "type_id": 3, "is_direct": True},   # CH
        "EPSS":   {"country_id": 1, "type_id": 1, "is_direct": False},  # ET: direct manufacturer/import
    }

    out.append("SET IDENTITY_INSERT Commodity.Supplier ON;")
    for idx, sname in enumerate(SUPPLIER_NAMES, start=1):
        meta = SUPPLIER_META.get(sname, {"country_id": 1, "type_id": 2, "is_direct": False})
        out.append(
            f"INSERT INTO Commodity.Supplier "
            f"(SupplierID, Name, IsActive, rowguid, SN, CountryID, "
            f"SupplierTypeID, IsDirectDelivery) VALUES "
            f"({idx}, N'{q(sname)}', 1, NEWID(), {idx}, {meta['country_id']}, "
            f"{meta['type_id']}, {int(meta['is_direct'])});"
        )
    out.append("SET IDENTITY_INSERT Commodity.Supplier OFF;")

    # Every commodity becomes a Commodity.Product row (idempotent catalogue).
    out.append("SET IDENTITY_INSERT Commodity.Product ON;")
    for idx, c in enumerate(ALL_COMMODITIES, start=1):
        ctype = FORM_TO_TYPE.get(c.get("form", ""), 2)
        is_vac = 1 if c.get("programme") == "EPI" else 0
        desc_parts = [
            f"commodity_id={c['id']}",
            f"programme={c.get('programme', '')}",
            f"ven={c.get('ven', '')}",
            f"form={c.get('form', '')}",
            f"pack_size={c.get('pack_size', '')}",
            f"supplier={c.get('supplier', '')}",
        ]
        desc = " | ".join(desc_parts)
        out.append(
            f"INSERT INTO Commodity.Product "
            f"(ProductID, Name, Description, CommodityTypeID, IsActive, rowguid, "
            f"SN, IsVaccine, IsNPS) VALUES "
            f"({idx}, N'{q(c['name'])}', N'{q(desc)}', {ctype}, 1, NEWID(), "
            f"{idx}, {is_vac}, 0);"
        )
    out.append("SET IDENTITY_INSERT Commodity.Product OFF;")

    return out


def _month_eoms() -> list[date]:
    """End-of-month dates for the last YEARS_HISTORY years, drawn from
    periods.json so we use the same months everyone else does.

    Dedupes by month_id since periods.json may emit multiple ET mappings
    per Gregorian month (cross-year overlap).
    """
    eoms: list[date] = []
    seen: set[str] = set()
    today = date.today()
    cutoff = date(today.year - YEARS_HISTORY, today.month, 1)
    months = PERIODS.get("months", [])
    for m in months:
        try:
            mid = m["month_id"]
            if mid in seen:
                continue
            seen.add(mid)
            y, mo = mid.split("-")
            y, mo = int(y), int(mo)
        except (ValueError, KeyError):
            continue
        if mo == 12:
            eom = date(y, 12, 31)
        else:
            eom = date(y, mo + 1, 1) - timedelta(days=1)
        if cutoff <= eom <= today:
            eoms.append(eom)
    return eoms


def gen_fact_data() -> list[str]:
    """Fact.DailyRecord at monthly grain (Environment x Product x Mode x EOM).
    Volume: 19 hubs x 34 commodities x 3 modes x up to 36 months, about 70k rows.
    Capped at SYNTH_FACT_DAILY_CAP (default 80,000)."""
    out = ["-- Fact.DailyRecord (monthly grain, all 35 commodities)"]
    eoms = _month_eoms()
    n_envs = len(EPSS_HUBS)
    n_products = len(ALL_COMMODITIES)
    n_modes = len(MODES)
    natural = n_envs * n_products * n_modes * len(eoms)
    if natural > FACT_DAILY_CAP and len(eoms) > 0:
        # Down-sample by truncating to the most-recent months that fit.
        per_month = n_envs * n_products * n_modes
        if per_month > 0:
            max_months = max(1, FACT_DAILY_CAP // per_month)
            eoms = eoms[-max_months:]

    # Supply dynamics
    # Each hub and commodity pair gets a deterministic health profile, so its
    # months are coherent over time: ~68% healthy (months of stock 2.5-6),
    # ~20% tight (1.2-2.5), ~8% low (0.4-1.2) and ~4% crisis-prone with 1-2
    # contiguous zero-stock episodes (1-3 months). Drawing stock and
    # consumption independently would yield no stock-outs at all. Flows are
    # accounting-consistent: received ≈ issued + Δsoh + losses.
    n_months = len(eoms)

    def _profile(env_id: int, product_id: int):
        r = random.Random(env_id * 1_000_003 + product_id * 9_176 + 7)
        x = r.random()
        if x < 0.68:   base = r.uniform(2.5, 6.0)   # healthy
        elif x < 0.88: base = r.uniform(1.2, 2.5)   # tight
        elif x < 0.96: base = r.uniform(0.4, 1.2)   # low
        else:          base = r.uniform(0.3, 0.9)   # crisis-prone
        crisis = x >= 0.96
        amc = r.randint(150, 4_000)                  # stable monthly consumption
        out_months: set[int] = set()
        if crisis and n_months > 6:
            for _ in range(r.randint(1, 2)):
                start = r.randint(2, max(2, n_months - 4))
                for k in range(r.randint(1, 3)):
                    out_months.add(start + k)
        # mild seasonality phase so regions don't move in lockstep
        phase = r.uniform(0, 6.28)
        return base, amc, out_months, phase, r

    profiles: dict[tuple[int, int], tuple] = {}
    prev_soh: dict[tuple[int, int, int], int] = {}

    emitted = 0
    for mi, eom in enumerate(eoms):
        date_key = int(eom.strftime("%Y%m%d"))
        fy = eom.year if eom.month < 7 else eom.year + 1
        for env_id in range(1, n_envs + 1):
            for product_id in range(1, n_products + 1):
                key = (env_id, product_id)
                if key not in profiles:
                    profiles[key] = _profile(env_id, product_id)
                base, amc, out_months, phase, r = profiles[key]
                for mode_id, _, _ in MODES:
                    season = 1.0 + 0.25 * _math.sin(phase + mi / 12.0 * 6.28)
                    target_mos = 0.0 if mi in out_months else max(
                        0.05, base * season * r.uniform(0.85, 1.15))
                    amc12 = max(1, int(amc * r.uniform(0.9, 1.1)))
                    soh = int(target_mos * amc12)
                    standard_issued = max(0, int(amc12 * r.uniform(0.8, 1.1)))
                    facility_issued = int(standard_issued * 0.6)
                    center_hub_issued = standard_issued - facility_issued
                    expired = r.randint(0, max(1, soh // 60))
                    damaged = r.randint(0, max(1, soh // 200))
                    pkey = (env_id, product_id, mode_id)
                    received = max(0, soh - prev_soh.get(pkey, soh)
                                   + standard_issued + expired + damaged)
                    prev_soh[pkey] = soh
                    mos12 = round(soh / amc12, 2) if amc12 else 0
                    if mos12 < 3:
                        status = "Stock Out"
                    elif mos12 < 6:
                        status = "Below Min"
                    elif mos12 > 18:
                        status = "Above Max"
                    else:
                        status = "OK"
                    out.append(
                        f"INSERT INTO Fact.DailyRecord "
                        f"(ItemID, EnvironmentID, ModeID, DateID, MOS12, AMC12, SOH, "
                        f"SOHAmount, StandardIssued, AmountStandardIssued, "
                        f"FacilityIssued, AmountFacilityIssued, CenterHubIssued, "
                        f"AmountCenterHubIssued, Expired, AmountExpired, Damaged, "
                        f"AmountDamaged, Received, AmountReceived, StockStatus, "
                        f"StandardQuantityRequested, FiscalYear) VALUES "
                        f"({product_id}, {env_id}, {mode_id}, {date_key}, {mos12}, "
                        f"{amc12}, {soh}, {soh}, {standard_issued}, "
                        f"{standard_issued * 5}, {facility_issued}, "
                        f"{facility_issued * 5}, {center_hub_issued}, "
                        f"{center_hub_issued * 5}, {expired}, {expired * 5}, "
                        f"{damaged}, {damaged * 5}, {received}, {received * 5}, "
                        f"'{status}', {standard_issued + random.randint(0, 1000)}, "
                        f"'FY{fy % 100:02d}');"
                    )
                    emitted += 1
                    if emitted >= FACT_DAILY_CAP:
                        return out
    return out


def gen_fact_inventory() -> list[str]:
    """Monthly inventory snapshot per (Environment x Mode x EOM)."""
    out = ["-- Fact.Inventory"]
    eoms = _month_eoms()
    n_envs = len(EPSS_HUBS)
    for eom in eoms:
        date_key = int(eom.strftime("%Y%m%d"))
        fy = eom.year if eom.month < 7 else eom.year + 1
        for env_id in range(1, n_envs + 1):
            for mode_id, _, _ in MODES:
                amount_issued = random.randint(50_000, 5_000_000)
                soh_amount = random.randint(100_000, 10_000_000)
                amount_received = random.randint(40_000, 6_000_000)
                out.append(
                    f"INSERT INTO Fact.Inventory "
                    f"(EnvironmentID, ModeID, DateID, AmountIssued, SOHAmount, "
                    f"AmountReceived, FiscalYear) VALUES "
                    f"({env_id}, {mode_id}, {date_key}, {amount_issued}, "
                    f"{soh_amount}, {amount_received}, 'FY{fy % 100:02d}');"
                )
    return out


# Execution

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
    print(f"==> Loading shared entity model from {SHARED_BUILD}")
    print(f"    hubs={len(EPSS_HUBS)}  programmes={len(PROGRAMMES)}  "
          f"commodities={len(ALL_COMMODITIES)}  epi={len(EPI_VACCINES)}  "
          f"suppliers={len(SUPPLIER_NAMES)}  seed={SEED}")
    print(f"    fact_daily_cap={FACT_DAILY_CAP}  years_history={YEARS_HISTORY}")

    print("==> Generating SQL for dimensions + commodity + facts...")
    # No global BEGIN TRANSACTION: ~80k INSERTs in one batch and one transaction
    # exceed the 5 GB buffer pool (Msg 701, insufficient system memory). The
    # script is chunked into bounded batches below (GO every BATCH_STMTS); a
    # re-run after a mid-way failure is safe because the DELETE preamble is
    # idempotent.
    lines: list[str] = [
        f"USE [{DB}];",
        "SET NOCOUNT ON;",
        # Clear the synth marker first: the chunked batches below are not one
        # transaction, so a mid-way failure must leave no valid marker (the next
        # boot then re-synthesizes instead of trusting half-written facts).
        "IF OBJECT_ID('dbo.SynthMeta','U') IS NOT NULL DELETE FROM dbo.SynthMeta;",
        # Idempotent truncate-equivalent. Order respects FK dependencies:
        # Fact tables first, then Commodity (Product depends on
        # CommodityType), then Dim.
        "DELETE FROM Fact.Inventory;",
        "DELETE FROM Fact.DailyRecord;",
        "DELETE FROM Commodity.Product;",
        "DELETE FROM Commodity.Supplier;",
        "DELETE FROM Commodity.Manufacturer;",
        "DELETE FROM Commodity.Program;",
        "DELETE FROM Commodity.DosageForm;",
        "DELETE FROM Commodity.UnitOfIssue;",
        "DELETE FROM Commodity.Category;",
        "DELETE FROM Commodity.SupplierType;",
        "DELETE FROM Commodity.Country;",
        "DELETE FROM Commodity.CommodityType;",
        "DELETE FROM Dim.Date;",
        "DELETE FROM Dim.Item;",
        "DELETE FROM Dim.Environment;",
        "DELETE FROM Dim.Program;",
        "DELETE FROM Dim.Mode;",
    ]
    lines.extend(gen_dim_mode())
    lines.extend(gen_dim_program())
    lines.extend(gen_dim_environment())
    lines.extend(gen_dim_item())
    lines.extend(gen_dim_date())
    lines.extend(gen_commodity_reference())
    lines.extend(gen_fact_data())
    lines.extend(gen_fact_inventory())
    # Stamp the synth marker last, so it exists only if every batch above ran.
    lines.extend([
        "IF OBJECT_ID('dbo.SynthMeta','U') IS NULL CREATE TABLE dbo.SynthMeta "
        "(SynthVersion INT NOT NULL, StampedAt DATETIME NOT NULL, Profile NVARCHAR(32) NULL);",
        "IF COL_LENGTH('dbo.SynthMeta','Profile') IS NULL "
        "ALTER TABLE dbo.SynthMeta ADD Profile NVARCHAR(32) NULL;",
        f"INSERT INTO dbo.SynthMeta (SynthVersion, StampedAt, Profile) "
        f"VALUES ({SYNTH_VERSION}, GETDATE(), '{_synth_profile()}');",
    ])

    # Chunk into bounded sqlcmd batches: a GO every BATCH_STMTS statements lets
    # SQL Server execute and release memory incrementally instead of parsing one
    # very large batch. Session settings (IDENTITY_INSERT, NOCOUNT) survive GO on
    # the same connection, but NOCOUNT is re-asserted per batch for safety.
    BATCH_STMTS = 2000
    batched: list[str] = []
    since_go = 0
    for ln in lines:
        batched.append(ln)
        since_go += 1
        if since_go >= BATCH_STMTS:
            batched.append("GO")
            batched.append("SET NOCOUNT ON;")
            since_go = 0
    lines = batched

    sql_file = Path("/tmp/hcmiswdm_analytics_seed.sql")
    sql_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"==> Wrote {sql_file.stat().st_size / 1024 / 1024:.1f} MB SQL to {sql_file}")

    print("==> Copying into container...")
    subprocess.run(["docker", "cp", str(sql_file), f"{CONTAINER}:/tmp/analytics_seed.sql"], check=True)
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "chown", "mssql:0", "/tmp/analytics_seed.sql"], check=True)

    print("==> Executing (will take several minutes for the fact tables)...")
    subprocess.run([
        "docker", "exec", "-i", CONTAINER,
        "/opt/mssql-tools18/bin/sqlcmd", "-S", "localhost", "-U", "sa",
        "-P", SA_PASSWORD, "-C", "-d", DB, "-i", "/tmp/analytics_seed.sql", "-b",
    ], check=True)

    print("==> Row counts:")
    subprocess.run([
        "docker", "exec", "-i", CONTAINER,
        "/opt/mssql-tools18/bin/sqlcmd", "-S", "localhost", "-U", "sa",
        "-P", SA_PASSWORD, "-C", "-d", DB, "-Q",
        """
SELECT 'Dim.Mode'        AS tbl, COUNT(*) AS n FROM Dim.Mode
UNION ALL SELECT 'Dim.Program',         COUNT(*) FROM Dim.Program
UNION ALL SELECT 'Dim.Environment',     COUNT(*) FROM Dim.Environment
UNION ALL SELECT 'Dim.Item',            COUNT(*) FROM Dim.Item
UNION ALL SELECT 'Dim.Date',            COUNT(*) FROM Dim.Date
UNION ALL SELECT 'Commodity.Product',   COUNT(*) FROM Commodity.Product
UNION ALL SELECT 'Commodity.Manufacturer', COUNT(*) FROM Commodity.Manufacturer
UNION ALL SELECT 'Commodity.Supplier',  COUNT(*) FROM Commodity.Supplier
UNION ALL SELECT 'Commodity.Category',  COUNT(*) FROM Commodity.Category
UNION ALL SELECT 'Fact.DailyRecord',    COUNT(*) FROM Fact.DailyRecord
UNION ALL SELECT 'Fact.Inventory',      COUNT(*) FROM Fact.Inventory;
""",
    ], check=True)

    print()
    print("OK: analytics extension synthesis complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
