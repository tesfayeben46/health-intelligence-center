#!/usr/bin/env python3
"""Generate synthetic HCMIS WDM vaccine supply-chain rows on the restored schema.

All rows are generated; the restored backup supplies the schema only.

Approach:
1. Build INSERT statements as a single SQL file under /tmp/.
2. `docker cp` the file into the hcmiswdm-sqlserver container.
3. Execute via sqlcmd -i.

This avoids installing pymssql / pyodbc on the host.

Tables populated (Vaccine schema):
  - Environment    (EPSS hubs, from bundle/seeds/entity-model/_build/epss_hubs.json)
  - Unit           (units of measure: dose, vial, carton, …)
  - Item           (vaccine catalogue: EPI commodities from bundle/seeds/entity-model/_build/commodities.json)
  - Institution    (recipient facilities: every row of bundle/seeds/entity-model/_build/facilities.json)
  - DailyRecord    (weekly stock snapshot per environment x item x unit)
  - Received       (receipts into warehouse)
  - Issued         (issues out to institutions)
  - Expired        (expired stock disposals)
  - PurchaseOrder  (open and recently closed vaccine orders; table created if absent)

Cross-source contract:
  All reference data comes from the canonical NHDW shared entity model under
  ./bundle/seeds/entity-model/_build/*.json. The shared `facility_id`
  is preserved in Institution.Description (`facility_id=...`) so dbt can join
  HCMIS rows to MFR / DHIS2 / EthioEMR rows deterministically. Same for the
  EPSS hub `id` in Environment.Description (`hub_id=...`).

Configuration via `.env` (or env vars):
  SYNTH_FACILITY_COUNT  how many facilities to load: "all" (default) loads
                        every facility in facilities.json (~893). Numeric
                        values are clamped to the population size.
  SYNTH_YEARS_HISTORY   years of history, counted back from the run date. Default 3.
  SYNTH_RANDOM_SEED     reproducibility seed. Default = entity_model.random_seed.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
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

# Load .env
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Default seed comes from the shared entity-model random_seed (20260624).
DEFAULT_SEED = "20260624"
FACILITY_COUNT_RAW = os.environ.get("SYNTH_FACILITY_COUNT", "all").strip()
YEARS_HISTORY = int(os.environ.get("SYNTH_YEARS_HISTORY", "3"))
SEED = os.environ.get("SYNTH_RANDOM_SEED", DEFAULT_SEED).strip() or DEFAULT_SEED
SA_PASSWORD = os.environ["HCMISWDM_SA_PASSWORD"]
CONTAINER = "hcmiswdm-sqlserver"
DB = os.environ.get("HCMISWDM_DATABASE", "HCMISWDM")

try:
    random.seed(int(SEED))
except ValueError:
    random.seed(SEED)


# Load shared reference data
def _load(name: str):
    return json.loads((SHARED_BUILD / name).read_text(encoding="utf-8"))


EPSS_HUBS = _load("epss_hubs.json")            # 19 hubs (id, name, region_id)
REGIONS = _load("regions.json")                # 14 regions (id, …, epss_hub)
ALL_COMMODITIES = _load("commodities.json")    # 35 commodities
FACILITIES = _load("facilities.json")          # 893 facilities

# Vaccines = commodities filtered to programme == EPI.
#
# A country whose commodity set has no EPI programme has no vaccine lane to
# generate: Zambia's tracer list is the eLMIS one (ARV, EM, LAB …) and carries
# no vaccines at all. That is a fact about the archive, not a broken model, so
# it degrades to an empty lane and says so rather than asserting.
#
# The assertion still fires for a model that declares an EPI programme but has
# no commodities in it, which is a broken model.
VACCINES = [c for c in ALL_COMMODITIES if c.get("programme") == "EPI"]
_HAS_EPI_PROGRAMME = any(
    (c.get("programme") or "").upper() == "EPI" for c in ALL_COMMODITIES)
if not VACCINES and _HAS_EPI_PROGRAMME:
    raise AssertionError("commodities.json declares an EPI programme but no EPI commodity")
if not VACCINES:
    print("==> no EPI commodities in this country's model: the vaccine lane is "
          "empty for this run (Zambia's eLMIS tracer list carries no vaccines)")

# region_id -> epss hub id (e.g. ET-OR -> ADAMA).
REGION_TO_HUB = {r["id"]: r["epss_hub"] for r in REGIONS}

# Hub id (e.g. ADAMA) -> 1-based ordinal used as EnvironmentID.
HUB_TO_ENV_ID = {h["id"]: idx for idx, h in enumerate(EPSS_HUBS, start=1)}

# Units of measure (vaccine logistics common units).
UNITS = [
    (1, "Dose",   1,     True,  False),  # base unit
    (2, "Vial",   10,    False, True),   # 10 doses/vial typical
    (3, "Box",    50,    False, True),
    (4, "Carton", 1000,  False, True),
]


# SQL builder
def q(s: str) -> str:
    return s.replace("'", "''")


def gen_environments() -> list[str]:
    out = ["-- Vaccine.Environment (from bundle/seeds/entity-model/_build/epss_hubs.json)\n",
           "SET IDENTITY_INSERT [Vaccine].[Environment] ON;\n"]
    for idx, hub in enumerate(EPSS_HUBS, start=1):
        code = hub["id"]
        name = hub["name"]
        # Tag the canonical hub id + region in Description for cross-source join.
        desc = f"hub_id={code} | region_id={hub['region_id']} | synthetic"
        out.append(
            f"INSERT INTO [Vaccine].[Environment]"
            f" (EnvironmentID, Name, Description, rowguid, EnvironmentCode, IsActive)"
            f" VALUES ({idx}, N'{q(name)}', N'{q(desc)}',"
            f" NEWID(), N'{q(code)}', 1);"
        )
    out.append("SET IDENTITY_INSERT [Vaccine].[Environment] OFF;\n")
    return out


def gen_units() -> list[str]:
    # Unit has no IDENTITY column, so explicit IDs are fine.
    out = ["-- Vaccine.Unit (no IDENTITY column)\n"]
    for uid, name, qty, is_default, use_alt in UNITS:
        out.append(
            f"INSERT INTO [Vaccine].[Unit]"
            f" (UnitID, Name, AlternativeID, IsDefault, UseAlternateUnit,"
            f"  rowguid, Quantity, SN)"
            f" VALUES ({uid}, N'{q(name)}', NULL, {1 if is_default else 0},"
            f" {1 if use_alt else 0}, NEWID(), {qty}, {uid});"
        )
    return out


def gen_items() -> list[str]:
    """Vaccine.Item: EPI commodities from the shared catalogue."""
    out = ["-- Vaccine.Item (EPI commodities from bundle/seeds/entity-model/_build/commodities.json)\n"]
    for idx, c in enumerate(VACCINES, start=1):
        sn = c["id"]                  # e.g. VAC-BCG
        full = c["name"]              # e.g. BCG vaccine
        out.append(
            f"INSERT INTO [Vaccine].[Item]"
            f" (ItemID, FullItemName, ItemName, rowguid, SN, ProductCN)"
            f" VALUES ({idx}, N'{q(full)}', N'{q(sn)}', NEWID(), {idx},"
            f" N'{q(sn)}');"
        )
    return out


def _resolve_facility_count() -> int:
    """Honour SYNTH_FACILITY_COUNT: 'all' (default) means every facility; an
    int is clamped to the population size."""
    n = len(FACILITIES)
    if FACILITY_COUNT_RAW.lower() in ("", "all"):
        return n
    try:
        v = int(FACILITY_COUNT_RAW)
        return max(1, min(v, n))
    except ValueError:
        return n


def gen_institutions() -> list[str]:
    out = [
        "-- Vaccine.Institution (from bundle/seeds/entity-model/_build/facilities.json)\n",
        "SET IDENTITY_INSERT [Vaccine].[Institution] ON;\n",
    ]
    n = _resolve_facility_count()
    if n == len(FACILITIES):
        sample = list(FACILITIES)
    else:
        sample = random.sample(FACILITIES, n)

    for idx, fac in enumerate(sample, start=1):
        name = (fac.get("name") or f"Facility {idx}")[:200]
        region_id = fac.get("region_id", "")
        hub_code = REGION_TO_HUB.get(region_id, "CENT")
        env_id = HUB_TO_ENV_ID.get(hub_code, 1)
        # Embed canonical IDs so cross-source joins (MFR/DHIS2/EthioEMR) work
        # without surrogate-key arithmetic.
        desc_parts = [
            f"facility_id={fac.get('facility_id', '')}",
            f"mfr_id={fac.get('mfr_id', '')}",
            f"tier={fac.get('tier_id', '')}",
            f"ownership={fac.get('ownership', '')}",
            f"region_id={region_id}",
            f"zone={fac.get('zone', '')}",
            f"woreda={fac.get('woreda', '')}",
        ]
        desc = " | ".join(desc_parts)[:1000]
        out.append(
            f"INSERT INTO [Vaccine].[Institution]"
            f" (InstitutionID, Name, Description, SN, rowguid, EnvironmentID)"
            f" VALUES ({idx}, N'{q(name)}', N'{q(desc)}',"
            f" {idx}, NEWID(), {env_id});"
        )
    out.append("SET IDENTITY_INSERT [Vaccine].[Institution] OFF;\n")
    return out


def gen_daily_records() -> list[str]:
    """Weekly stock snapshots per (Environment x Item x Unit) with realistic
    noise. Two unit grains (Dose=1, Vial=2) are emitted so reconciliation
    queries can pivot doses<->vials. Sizing: 19 hubs x 10 EPI items x 2 units
    x ~157 weeks (3 yrs) ≈ 60k rows.
    """
    out = ["-- Vaccine.DailyRecord (weekly grain x 2 unit grains)\n",
           "SET IDENTITY_INSERT [Vaccine].[DailyRecord] ON;\n"]
    end = date.today()
    start = date(end.year - YEARS_HISTORY, end.month, 1)
    cur = start
    rid = 1
    n_envs = len(EPSS_HUBS)
    n_items = len(VACCINES)
    # Two grains: Dose (1, qty=1) and Vial (2, qty=10).
    UNIT_GRAINS = ((1, 1), (2, 10))
    while cur <= end:
        for env_id in range(1, n_envs + 1):
            # Central warehouse (EnvironmentID 1 = CENT) holds bigger volumes.
            scale = 5 if env_id == 1 else 1
            for item_id in range(1, n_items + 1):
                # Base figures in doses.
                received_doses = random.randint(0, 50_000) * scale
                issued_doses = random.randint(0, int(received_doses * 0.9)) if received_doses > 0 else 0
                expired_doses = random.randint(0, 200) * scale
                soh_doses = max(0, received_doses - issued_doses - expired_doses + random.randint(0, 30_000))
                for unit_id, qty_per in UNIT_GRAINS:
                    # Convert doses -> grain by integer-dividing by qty_per.
                    received = received_doses // qty_per
                    issued = issued_doses // qty_per
                    expired = expired_doses // qty_per
                    soh = soh_doses // qty_per
                    out.append(
                        f"INSERT INTO [Vaccine].[DailyRecord]"
                        f" (DailyRecordID, Date, ItemID, UnitID, EnvironmentID,"
                        f"  Received, Issued, Expired, SOH)"
                        f" VALUES ({rid}, '{cur.isoformat()}', {item_id}, {unit_id}, {env_id},"
                        f" {received}, {issued}, {expired}, {soh});"
                    )
                    rid += 1
        cur += timedelta(days=7)
    out.append("SET IDENTITY_INSERT [Vaccine].[DailyRecord] OFF;\n")
    return out


def gen_movements() -> list[str]:
    """Receipts, Issues and Expired transactions (sparser than DailyRecord)."""
    out = []
    end = date.today()
    start = date(end.year - YEARS_HISTORY, end.month, 1)
    n_envs = len(EPSS_HUBS)
    n_items = len(VACCINES)

    out.append("-- Vaccine.Received\n")
    out.append("SET IDENTITY_INSERT [Vaccine].[Received] ON;\n")
    rid = 1
    cur = start
    while cur <= end:
        # ~3 receipts per environment per month
        for env_id in range(1, n_envs + 1):
            for _ in range(random.randint(1, 4)):
                item = random.randint(1, n_items)
                qty = random.randint(500, 100_000)
                amt = qty * random.uniform(0.1, 5.0)
                d = cur + timedelta(days=random.randint(0, 27))
                out.append(
                    f"INSERT INTO [Vaccine].[Received]"
                    f" (ReceivedID, Date, ItemID, UnitID, EnvironmentID, Quantity, Amount)"
                    f" VALUES ({rid}, '{d.isoformat()}', {item}, 1, {env_id}, {qty}, {amt:.2f});"
                )
                rid += 1
        cur += timedelta(days=30)
    out.append("SET IDENTITY_INSERT [Vaccine].[Received] OFF;\n")

    out.append("-- Vaccine.Issued\n")
    out.append("SET IDENTITY_INSERT [Vaccine].[Issued] ON;\n")
    iid = 1
    cur = start
    while cur <= end:
        for env_id in range(1, n_envs + 1):
            for _ in range(random.randint(2, 6)):
                item = random.randint(1, n_items)
                qty = random.randint(100, 50_000)
                amt = qty * random.uniform(0.1, 5.0)
                d = cur + timedelta(days=random.randint(0, 27))
                out.append(
                    f"INSERT INTO [Vaccine].[Issued]"
                    f" (IssuedID, Date, ItemID, UnitID, EnvironmentID, Quantity, Amount)"
                    f" VALUES ({iid}, '{d.isoformat()}', {item}, 1, {env_id}, {qty}, {amt:.2f});"
                )
                iid += 1
        cur += timedelta(days=30)
    out.append("SET IDENTITY_INSERT [Vaccine].[Issued] OFF;\n")

    out.append("-- Vaccine.Expired\n")
    out.append("SET IDENTITY_INSERT [Vaccine].[Expired] ON;\n")
    eid = 1
    cur = start
    while cur <= end:
        for env_id in range(1, n_envs + 1):
            if random.random() < 0.3:  # expirations are sparser
                item = random.randint(1, n_items)
                qty = random.randint(10, 1_000)
                d = cur + timedelta(days=random.randint(0, 27))
                expiry = d - timedelta(days=random.randint(0, 60))
                out.append(
                    f"INSERT INTO [Vaccine].[Expired]"
                    f" (ExpiredID, Date, ExpiryDate, ItemID, UnitID, EnvironmentID, Quantity)"
                    f" VALUES ({eid}, '{d.isoformat()}', '{expiry.isoformat()}',"
                    f" {item}, 1, {env_id}, {qty});"
                )
                eid += 1
        cur += timedelta(days=30)
    out.append("SET IDENTITY_INSERT [Vaccine].[Expired] OFF;\n")

    return out


# Vaccine procurement channels (names only; no real procurement data).
PO_SUPPLIERS = [
    "UNICEF Supply Division", "Gavi / UNICEF", "Serum Institute of India",
    "Bharat Biotech", "GSK Biologicals", "Sanofi Pasteur",
    "EPSS Central Procurement", "WHO PQ Pooled Procurement",
]


def gen_purchase_orders() -> list[str]:
    """Vaccine.PurchaseOrder: the procurement pipeline (open and recently
    closed orders, by hub and vaccine). This table is not in the base .bak
    schema, so CREATE it (idempotent) before loading. ETA is derived downstream
    as ExpectedDeliveryDate − CURRENT_DATE so the widget stays live regardless
    of when the .bak was packed.

    FK reconciliation: ItemID (1..EPI) and EnvironmentID (1..hub) share the
    canonical Dim.Item / Dim.Environment / Vaccine.* id spaces, so the vault
    hubs (hk_hcmis_item / hk_hcmis_environment) join cleanly downstream.
    """
    out = [
        "-- Vaccine.PurchaseOrder (procurement pipeline: created if absent)\n",
        "IF OBJECT_ID('[Vaccine].[PurchaseOrder]', 'U') IS NULL\n"
        "CREATE TABLE [Vaccine].[PurchaseOrder] (\n"
        "    PurchaseOrderID       INT              NOT NULL PRIMARY KEY,\n"
        "    PoNumber              NVARCHAR(30)     NOT NULL,\n"
        "    ItemID                INT              NOT NULL,\n"
        "    EnvironmentID         INT              NOT NULL,\n"
        "    SupplierName          NVARCHAR(120)    NULL,\n"
        "    Quantity              INT              NOT NULL,\n"
        "    UnitCost              DECIMAL(18,2)    NULL,\n"
        "    OrderDate             DATE             NOT NULL,\n"
        "    ExpectedDeliveryDate  DATE             NULL,\n"
        "    Status                NVARCHAR(20)     NOT NULL,\n"
        "    rowguid               UNIQUEIDENTIFIER NOT NULL,\n"
        "    ModifiedDate          DATETIME         NOT NULL\n"
        ");\n",
        "DELETE FROM [Vaccine].[PurchaseOrder];\n",
    ]
    today = date.today()
    n_envs = len(EPSS_HUBS)
    n_items = len(VACCINES)
    poid = 1
    for env_id in range(1, n_envs + 1):
        for _ in range(random.randint(2, 4)):   # ~2-4 orders per hub
            item = random.randint(1, n_items)
            qty = random.randint(5_000, 500_000)
            unit_cost = round(random.uniform(0.1, 12.0), 2)
            ordered = today - timedelta(days=random.randint(2, 90))
            expected = ordered + timedelta(days=random.randint(30, 150))
            # Status derived from where `expected` sits vs today; ~15% closed.
            if random.random() < 0.15:
                status = "Delivered"
            elif expected <= today:
                status = "At Customs"            # due/overdue, not yet received
            elif (expected - today).days <= 30:
                status = "In Transit"
            else:
                status = "Ordered"
            supplier = random.choice(PO_SUPPLIERS)
            pono = f"PO-{ordered.year}-{poid:05d}"
            out.append(
                f"INSERT INTO [Vaccine].[PurchaseOrder]"
                f" (PurchaseOrderID, PoNumber, ItemID, EnvironmentID, SupplierName,"
                f" Quantity, UnitCost, OrderDate, ExpectedDeliveryDate, Status, rowguid, ModifiedDate)"
                f" VALUES ({poid}, N'{pono}', {item}, {env_id}, N'{q(supplier)}',"
                f" {qty}, {unit_cost}, '{ordered.isoformat()}', '{expected.isoformat()}',"
                f" N'{status}', NEWID(), GETDATE());"
            )
            poid += 1
    return out


# Execution
def main() -> int:
    sql_file = Path("/tmp/hcmiswdm_seed.sql")

    print(f"==> Loading shared entity model from {SHARED_BUILD}")
    print(f"    hubs={len(EPSS_HUBS)}  facilities={len(FACILITIES)}  "
          f"vaccines(EPI)={len(VACCINES)}  seed={SEED}")
    print(f"    facility_count={_resolve_facility_count()}  "
          f"years_history={YEARS_HISTORY}")

    print("==> Generating SQL...")
    lines: list[str] = [
        f"USE [{DB}];\n",
        "SET NOCOUNT ON;\n",
        "BEGIN TRANSACTION;\n",
        "-- Idempotent: truncate-equivalent delete in FK-safe order.\n",
        "DELETE FROM [Vaccine].[Expired];\n",
        "DELETE FROM [Vaccine].[Issued];\n",
        "DELETE FROM [Vaccine].[Received];\n",
        "DELETE FROM [Vaccine].[DailyRecord];\n",
        "DELETE FROM [Vaccine].[Institution];\n",
        "DELETE FROM [Vaccine].[Item];\n",
        "DELETE FROM [Vaccine].[Unit];\n",
        "DELETE FROM [Vaccine].[Environment];\n",
    ]
    lines.extend(gen_environments())
    lines.extend(gen_units())
    lines.extend(gen_items())
    lines.extend(gen_institutions())
    lines.extend(gen_daily_records())
    lines.extend(gen_movements())
    lines.extend(gen_purchase_orders())

    lines.append("COMMIT;\n")
    sql_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"==> Wrote {sql_file.stat().st_size / 1024 / 1024:.1f} MB SQL to {sql_file}")

    print("==> Copying into container...")
    subprocess.check_call(
        ["docker", "cp", str(sql_file), f"{CONTAINER}:/tmp/seed.sql"],
    )
    subprocess.check_call(
        ["docker", "exec", "-u", "root", CONTAINER, "chown", "mssql:0", "/tmp/seed.sql"],
    )

    print("==> Executing (this may take several minutes)...")
    subprocess.check_call([
        "docker", "exec", "-i", CONTAINER,
        "/opt/mssql-tools18/bin/sqlcmd",
        "-S", "localhost", "-U", "sa", "-P", SA_PASSWORD,
        "-C", "-d", DB, "-i", "/tmp/seed.sql", "-b",
    ])

    print("==> Verifying row counts...")
    subprocess.check_call([
        "docker", "exec", "-i", CONTAINER,
        "/opt/mssql-tools18/bin/sqlcmd",
        "-S", "localhost", "-U", "sa", "-P", SA_PASSWORD,
        "-C", "-d", DB,
        "-Q", """
SELECT 'Environment' AS table_name, COUNT(*) AS rows FROM Vaccine.Environment
UNION ALL SELECT 'Unit',         COUNT(*) FROM Vaccine.Unit
UNION ALL SELECT 'Item',         COUNT(*) FROM Vaccine.Item
UNION ALL SELECT 'Institution',  COUNT(*) FROM Vaccine.Institution
UNION ALL SELECT 'DailyRecord',  COUNT(*) FROM Vaccine.DailyRecord
UNION ALL SELECT 'Received',     COUNT(*) FROM Vaccine.Received
UNION ALL SELECT 'Issued',       COUNT(*) FROM Vaccine.Issued
UNION ALL SELECT 'Expired',      COUNT(*) FROM Vaccine.Expired;
""",
    ])

    print()
    print("OK: synthesis complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
