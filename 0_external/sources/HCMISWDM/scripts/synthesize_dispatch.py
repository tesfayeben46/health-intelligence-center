#!/usr/bin/env python3
"""Load the demonstration EPSS dispatch (route-optimisation) plan into HCMIS WDM.

Alongside the stock module (synthesize.py) and the analytics Fact layer
(synthesize_analytics.py), this models the periodic network-optimisation pass
that produces a dispatch plan: which regional hub supplies which facility, how
much of each commodity, on which vehicle run, and the km and cost saved against
a naive one-truck-per-facility baseline. The plan is loaded as five source
tables so the pipeline carries it downstream like any other HCMIS table:

    Dim.DispatchHub       EPSS hubs (the warehouses), with coordinates and fleet
    Dim.DispatchFacility  delivery facilities (the "nodes"): coordinates, region, type
    Dim.DispatchPeriod    monthly planning periods with the plan-level KPI and
                          savings summary for each (units, runs, km and cost saved)
    Fact.Dispatch         one row per movement line (hub -> facility x commodity)
    Fact.DispatchRun      one row per vehicle run (stops and road path as JSON)

Why the values come from plan.ts:
    The plan is a generated, deterministic demonstration plan committed as
    `6_access-points/dashboards/internal-bi/ui/src/ct/data/plan.ts`. Re-solving
    here would drift from what the dashboard was built against, so this loader
    parses plan.ts's runtime arrays and mirrors them 1:1 into SQL Server.
    plan.ts is the single source of truth. No randomness.

Nested plan.ts fields (Movement.why, VehicleRun.stops, VehicleRun.path) are
stored as JSON strings in NVARCHAR(MAX) columns, keeping the raw surface flat
for the dlt ingestor; typing and JSON parsing happen downstream in the vault
and marts. Run after synthesize.py and synthesize_analytics.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def _repo_root() -> Path:
    p = Path(__file__).resolve()
    while not (p / "bootstrap.sh").exists():
        if p.parent == p:
            raise RuntimeError("repo root (bootstrap.sh) not found above " + __file__)
        p = p.parent
    return p


REPO_ROOT = _repo_root()
ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
PLAN_TS = (REPO_ROOT / "6_access-points" / "dashboards" / "internal-bi"
           / "ui" / "src" / "ct" / "data" / "plan.ts")

# Synthesis-contract version for the dispatch plan. Stamped into
# dbo.DispatchSynthMeta; bundle/scripts/sources-up.sh re-loads when a restored
# .bak carries a different (or no) version, so a stale pack cannot mask a change.
DISPATCH_SYNTH_VERSION = 1

if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SA_PASSWORD = os.environ["HCMISWDM_SA_PASSWORD"]
CONTAINER = "hcmiswdm-sqlserver"
DB = os.environ.get("HCMISWDM_DATABASE", "HCMISWDM")


# plan.ts parsing
def _plan_lines() -> list[str]:
    if not PLAN_TS.exists():
        raise RuntimeError(f"plan.ts not found at {PLAN_TS}")
    return PLAN_TS.read_text(encoding="utf-8").splitlines()


def _parse_array(lines: list[str], const_name: str):
    """Extract the JSON array literal assigned to `export const <name> ... = [ ... ]`.

    plan.ts writes each big array as one physical line; the assignment may carry a
    TypeScript type annotation (`Movement[]`) and a trailing `as {...}[]` cast, so
    we bracket-match from the first `= [` to the matching `]`.
    """
    for ln in lines:
        m = re.match(rf"\s*export const {re.escape(const_name)}\b", ln)
        if not m:
            continue
        i = ln.find("= [")
        if i < 0:
            continue
        i += 2
        depth = 0
        for j in range(i, len(ln)):
            if ln[j] == "[":
                depth += 1
            elif ln[j] == "]":
                depth -= 1
                if depth == 0:
                    return json.loads(ln[i:j + 1])
    raise RuntimeError(f"array export const {const_name} not found in plan.ts")


def _parse_object(lines: list[str], const_name: str) -> dict:
    """Extract a JS object literal (`export const SUMMARY = { period: "…", legs: 545, };`).

    These use unquoted keys + trailing commas (not valid JSON), so we normalise:
    quote bare keys, strip trailing commas, then json.loads.
    """
    text = "\n".join(lines)
    m = re.search(rf"export const {re.escape(const_name)}\s*=\s*\{{", text)
    if not m:
        raise RuntimeError(f"object export const {const_name} not found in plan.ts")
    start = m.end() - 1
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                body = text[start:j + 1]
                break
    else:
        raise RuntimeError(f"unterminated object literal for {const_name}")
    body = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


# SQL helpers
def q(s: str) -> str:
    return str(s).replace("'", "''")


def sbit(v) -> str:
    return "1" if v else "0"


def snum(v) -> str:
    if v is None:
        return "NULL"
    return repr(float(v)) if isinstance(v, float) else str(v)


def sstr(v) -> str:
    return "NULL" if v is None else f"N'{q(v)}'"


def sjson(v) -> str:
    return f"N'{q(json.dumps(v, separators=(',', ':')))}'"


# Table DDL (created if absent; not part of the base .bak schema)
DDL = """
IF OBJECT_ID('[Dim].[DispatchHub]', 'U') IS NULL
CREATE TABLE [Dim].[DispatchHub] (
    HubID NVARCHAR(20) NOT NULL PRIMARY KEY, Name NVARCHAR(120) NOT NULL,
    ShortName NVARCHAR(60) NULL, Zone NVARCHAR(60) NULL,
    Lng FLOAT NULL, Lat FLOAT NULL, IsHub BIT NOT NULL DEFAULT 0, Vehicles INT NULL);

IF OBJECT_ID('[Dim].[DispatchFacility]', 'U') IS NULL
CREATE TABLE [Dim].[DispatchFacility] (
    NodeID NVARCHAR(20) NOT NULL PRIMARY KEY, Name NVARCHAR(200) NOT NULL,
    Region NVARCHAR(80) NULL, FacilityType NVARCHAR(80) NULL,
    Lng FLOAT NULL, Lat FLOAT NULL, LineCount INT NULL, StoreCap INT NULL);

IF OBJECT_ID('[Dim].[DispatchPeriod]', 'U') IS NULL
CREATE TABLE [Dim].[DispatchPeriod] (
    PeriodID NVARCHAR(20) NOT NULL PRIMARY KEY, Label NVARCHAR(60) NULL, SortOrder INT NULL,
    Movements INT NULL, Units BIGINT NULL, Runs INT NULL, Facilities INT NULL, AvgFill FLOAT NULL,
    OverflowLegs INT NULL, MinMonths INT NULL, MaxMonths INT NULL,
    UncoveredNeeds INT NULL, AtRiskFacilities INT NULL,
    OptimizedKm BIGINT NULL, BaselineKm BIGINT NULL, KmSaved BIGINT NULL, KmSavedPct INT NULL,
    OptimizedCost BIGINT NULL, BaselineCost BIGINT NULL, CostSaved BIGINT NULL, CostSavedPct INT NULL,
    VehiclesUsed INT NULL, NaiveVehicles INT NULL, VehiclesSaved INT NULL);

IF OBJECT_ID('[Fact].[Dispatch]', 'U') IS NULL
CREATE TABLE [Fact].[Dispatch] (
    MovementID NVARCHAR(20) NOT NULL PRIMARY KEY, Period NVARCHAR(20) NOT NULL,
    FromHubID NVARCHAR(20) NOT NULL, ToNodeID NVARCHAR(20) NOT NULL,
    Drug NVARCHAR(120) NULL, DrugShort NVARCHAR(60) NULL, CommodityKey NVARCHAR(40) NULL,
    Units INT NULL, Amc INT NULL, Mos FLOAT NULL, DaysToStockout INT NULL, Priority INT NULL,
    LaneKm INT NULL, CostUnits INT NULL, SequenceIndex INT NULL, VehicleRunID NVARCHAR(30) NULL,
    FillRate FLOAT NULL, NearestHubID NVARCHAR(20) NULL, AltHubID NVARCHAR(20) NULL,
    Reason NVARCHAR(30) NULL, AssignDeltaKm INT NULL, AssignDeltaCost INT NULL,
    BaselineHubID NVARCHAR(20) NULL, BaselineKm INT NULL,
    Planned BIT NULL, Repaired BIT NULL, Capped BIT NULL, WhyJson NVARCHAR(MAX) NULL);

IF OBJECT_ID('[Fact].[DispatchRun]', 'U') IS NULL
CREATE TABLE [Fact].[DispatchRun] (
    RunID NVARCHAR(30) NOT NULL PRIMARY KEY, Period NVARCHAR(20) NOT NULL,
    VehicleType NVARCHAR(80) NULL, CapacityUnits INT NULL, OriginHubID NVARCHAR(20) NULL,
    TotalRoadKm INT NULL, TotalUnits INT NULL, FillRate FLOAT NULL, CostTotal INT NULL,
    CostPerUnit FLOAT NULL, KmSavedVsBaseline INT NULL, Depart NVARCHAR(10) NULL,
    Eta NVARCHAR(10) NULL, Status NVARCHAR(20) NULL,
    StopsJson NVARCHAR(MAX) NULL, PathJson NVARCHAR(MAX) NULL);
"""


def gen_hubs(warehouses) -> list[str]:
    out = ["-- Dim.DispatchHub (EPSS hubs / warehouses from plan.ts WAREHOUSES)"]
    for w in warehouses:
        out.append(
            "INSERT INTO [Dim].[DispatchHub] (HubID, Name, ShortName, Zone, Lng, Lat, IsHub, Vehicles)"
            f" VALUES ({sstr(w['id'])}, {sstr(w['name'])}, {sstr(w.get('short'))}, {sstr(w.get('zone'))},"
            f" {snum(w.get('lng'))}, {snum(w.get('lat'))}, {sbit(w.get('hub'))}, {snum(w.get('vehicles'))});"
        )
    return out


def gen_facilities(nodes) -> list[str]:
    out = ["-- Dim.DispatchFacility (delivery nodes from plan.ts NODES)"]
    for n in nodes:
        out.append(
            "INSERT INTO [Dim].[DispatchFacility] (NodeID, Name, Region, FacilityType, Lng, Lat, LineCount, StoreCap)"
            f" VALUES ({sstr(n['id'])}, {sstr(n['name'])}, {sstr(n.get('region'))}, {sstr(n.get('type'))},"
            f" {snum(n.get('lng'))}, {snum(n.get('lat'))}, {snum(n.get('lineCount'))}, {snum(n.get('storeCap'))});"
        )
    return out


def gen_periods(period_rows) -> list[str]:
    out = ["-- Dim.DispatchPeriod (planning periods + plan-level KPI/savings summary)"]
    cols = ("PeriodID, Label, SortOrder, Movements, Units, Runs, Facilities, AvgFill, OverflowLegs,"
            " MinMonths, MaxMonths, UncoveredNeeds, AtRiskFacilities, OptimizedKm, BaselineKm, KmSaved,"
            " KmSavedPct, OptimizedCost, BaselineCost, CostSaved, CostSavedPct, VehiclesUsed, NaiveVehicles,"
            " VehiclesSaved")
    for p in period_rows:
        vals = ", ".join([
            sstr(p["period_id"]), sstr(p["label"]), snum(p["sort_order"]),
            snum(p["movements"]), snum(p["units"]), snum(p["runs"]), snum(p["facilities"]),
            snum(p["avg_fill"]), snum(p["overflow_legs"]), snum(p["min_months"]), snum(p["max_months"]),
            snum(p["uncovered_needs"]), snum(p["at_risk_facilities"]), snum(p["optimized_km"]),
            snum(p["baseline_km"]), snum(p["km_saved"]), snum(p["km_saved_pct"]), snum(p["optimized_cost"]),
            snum(p["baseline_cost"]), snum(p["cost_saved"]), snum(p["cost_saved_pct"]),
            snum(p["vehicles_used"]), snum(p["naive_vehicles"]), snum(p["vehicles_saved"]),
        ])
        out.append(f"INSERT INTO [Dim].[DispatchPeriod] ({cols}) VALUES ({vals});")
    return out


def gen_movements(movements) -> list[str]:
    out = ["-- Fact.Dispatch (one row per movement line from plan.ts MOVEMENTS)"]
    cols = ("MovementID, Period, FromHubID, ToNodeID, Drug, DrugShort, CommodityKey, Units, Amc, Mos,"
            " DaysToStockout, Priority, LaneKm, CostUnits, SequenceIndex, VehicleRunID, FillRate,"
            " NearestHubID, AltHubID, Reason, AssignDeltaKm, AssignDeltaCost, BaselineHubID, BaselineKm,"
            " Planned, Repaired, Capped, WhyJson")
    for m in movements:
        vals = ", ".join([
            sstr(m["id"]), sstr(m["period"]), sstr(m["fromId"]), sstr(m["toId"]),
            sstr(m.get("drug")), sstr(m.get("drugShort")), sstr(m.get("commodityKey")),
            snum(m.get("units")), snum(m.get("amc")), snum(m.get("mos")), snum(m.get("daysToStockout")),
            snum(m.get("priority")), snum(m.get("laneKm")), snum(m.get("costUnits")),
            snum(m.get("sequenceIndex")), sstr(m.get("vehicleRunId")), snum(m.get("fillRate")),
            sstr(m.get("nearestHubId")), sstr(m.get("altHubId")), sstr(m.get("reason")),
            snum(m.get("assignDeltaKm")), snum(m.get("assignDeltaCost")), sstr(m.get("baselineHubId")),
            snum(m.get("baselineKm")), sbit(m.get("planned")), sbit(m.get("repaired")),
            sbit(m.get("capped")), sjson(m.get("why", {})),
        ])
        out.append(f"INSERT INTO [Fact].[Dispatch] ({cols}) VALUES ({vals});")
    return out


def gen_runs(runs) -> list[str]:
    out = ["-- Fact.DispatchRun (one row per vehicle run from plan.ts VEHICLE_RUNS)"]
    cols = ("RunID, Period, VehicleType, CapacityUnits, OriginHubID, TotalRoadKm, TotalUnits, FillRate,"
            " CostTotal, CostPerUnit, KmSavedVsBaseline, Depart, Eta, Status, StopsJson, PathJson")
    for r in runs:
        vals = ", ".join([
            sstr(r["id"]), sstr(r["period"]), sstr(r.get("vehicleType")), snum(r.get("capacityUnits")),
            sstr(r.get("originHubId")), snum(r.get("totalRoadKm")), snum(r.get("totalUnits")),
            snum(r.get("fillRate")), snum(r.get("costTotal")), snum(r.get("costPerUnit")),
            snum(r.get("kmSavedVsBaseline")), sstr(r.get("depart")), sstr(r.get("eta")),
            sstr(r.get("status")), sjson(r.get("stops", [])), sjson(r.get("path", [])),
        ])
        out.append(f"INSERT INTO [Fact].[DispatchRun] ({cols}) VALUES ({vals});")
    return out


# Per-period stat derivation
# baseline_cost is a plan-level figure the solver computes against a naive
# one-truck-per-facility policy; it is not a simple movement-column sum, so for
# non-current periods we approximate it with the current period's per-km rate
# (calibrated so the current period reproduces plan.ts's BASELINE exactly, then
# overridden verbatim below). Everything else is exactly derivable.
_KM_COST_RATE = 0.647  # cost units saved per km, calibrated from CURRENT_PERIOD


def build_period_rows(periods, movements, runs, summary, baseline, current_period) -> list[dict]:
    by_period_m = defaultdict(list)
    for m in movements:
        by_period_m[m["period"]].append(m)
    by_period_r = defaultdict(list)
    for r in runs:
        by_period_r[r["period"]].append(r)

    rows = []
    for order, p in enumerate(periods):
        pid = p["id"]
        cm = by_period_m.get(pid, [])
        cv = by_period_r.get(pid, [])
        facilities = {m["toId"] for m in cm}
        # Naive baseline: one dedicated round-trip per facility from its baseline hub.
        fac_bkm = defaultdict(float)
        for m in cm:
            fac_bkm[m["toId"]] = max(fac_bkm[m["toId"]], m.get("baselineKm", 0) or 0)
        baseline_km = round(sum(2 * v for v in fac_bkm.values()))
        optimized_km = round(sum(r.get("totalRoadKm", 0) or 0 for r in cv))
        km_saved = baseline_km - optimized_km
        optimized_cost = round(sum(r.get("costTotal", 0) or 0 for r in cv))
        baseline_cost = round(optimized_cost + km_saved * _KM_COST_RATE)
        cost_saved = baseline_cost - optimized_cost
        mos_vals = [m.get("mos", 0) or 0 for m in cm]
        row = {
            "period_id": pid, "label": p.get("label"), "sort_order": order,
            "movements": len(cm), "units": sum(m.get("units", 0) or 0 for m in cm),
            "runs": len(cv), "facilities": len(facilities),
            "avg_fill": round(sum(r.get("fillRate", 0) or 0 for r in cv) / len(cv), 3) if cv else 0,
            "overflow_legs": sum(1 for m in cm if m.get("reason") == "capacity-overflow"),
            "min_months": round(min(mos_vals)) if mos_vals else 0,
            "max_months": round(max(mos_vals)) if mos_vals else 0,
            "uncovered_needs": 0, "at_risk_facilities": 0,
            "optimized_km": optimized_km, "baseline_km": baseline_km, "km_saved": km_saved,
            "km_saved_pct": round(100 * km_saved / baseline_km) if baseline_km else 0,
            "optimized_cost": optimized_cost, "baseline_cost": baseline_cost, "cost_saved": cost_saved,
            "cost_saved_pct": round(100 * cost_saved / baseline_cost) if baseline_cost else 0,
            "vehicles_used": len(cv), "naive_vehicles": len(facilities),
            "vehicles_saved": max(0, len(facilities) - len(cv)),
        }
        rows.append(row)

    # Override the current period with plan.ts's own SUMMARY and BASELINE verbatim,
    # so the endpoint reproduces the published figures exactly.
    for row in rows:
        if row["period_id"] != current_period:
            continue
        row.update({
            "movements": summary["legs"], "units": summary["unitsAcrossLegs"],
            "runs": summary["vehicleRuns"], "facilities": summary["facilities"],
            "avg_fill": summary["avgFillRate"], "overflow_legs": summary["overflowLegs"],
            "min_months": summary["minMonths"], "max_months": summary["maxMonths"],
            "uncovered_needs": summary["uncoveredNeeds"], "at_risk_facilities": summary["atRiskFacilities"],
            "optimized_km": baseline["optimizedKm"], "baseline_km": baseline["baselineKm"],
            "km_saved": baseline["kmSaved"], "km_saved_pct": baseline["kmSavedPct"],
            "optimized_cost": baseline["optimizedCost"], "baseline_cost": baseline["baselineCost"],
            "cost_saved": baseline["costSaved"], "cost_saved_pct": baseline["costSavedPct"],
            "vehicles_used": baseline["vehiclesUsed"], "naive_vehicles": baseline["naiveVehicles"],
            "vehicles_saved": baseline["vehiclesSaved"],
        })
    return rows


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
    print(f"==> Parsing dispatch plan from {PLAN_TS}")
    lines = _plan_lines()
    warehouses = _parse_array(lines, "WAREHOUSES")
    nodes = _parse_array(lines, "NODES")
    movements = _parse_array(lines, "MOVEMENTS")
    runs = _parse_array(lines, "VEHICLE_RUNS")
    periods = _parse_array(lines, "PERIODS")
    current_period = None
    for ln in lines:
        m = re.match(r'\s*export const CURRENT_PERIOD\s*=\s*"([^"]+)"', ln)
        if m:
            current_period = m.group(1)
            break
    summary = _parse_object(lines, "SUMMARY")
    baseline = _parse_object(lines, "BASELINE")
    print(f"    warehouses={len(warehouses)} nodes={len(nodes)} movements={len(movements)} "
          f"runs={len(runs)} periods={len(periods)} current={current_period}")

    period_rows = build_period_rows(periods, movements, runs, summary, baseline, current_period)

    print("==> Generating SQL...")
    stmts: list[str] = [
        f"USE [{DB}];", "SET NOCOUNT ON;",
        # Marker cleared first: a mid-way failure must leave no valid marker.
        "IF OBJECT_ID('dbo.DispatchSynthMeta','U') IS NOT NULL DELETE FROM dbo.DispatchSynthMeta;",
        DDL,
        "DELETE FROM [Fact].[Dispatch];",
        "DELETE FROM [Fact].[DispatchRun];",
        "DELETE FROM [Dim].[DispatchPeriod];",
        "DELETE FROM [Dim].[DispatchFacility];",
        "DELETE FROM [Dim].[DispatchHub];",
    ]
    stmts += gen_hubs(warehouses)
    stmts += gen_facilities(nodes)
    stmts += gen_periods(period_rows)
    stmts += gen_runs(runs)
    stmts += gen_movements(movements)
    stmts += [
        "IF OBJECT_ID('dbo.DispatchSynthMeta','U') IS NULL CREATE TABLE dbo.DispatchSynthMeta "
        "(SynthVersion INT NOT NULL, StampedAt DATETIME NOT NULL, Profile NVARCHAR(32) NULL);",
        "IF COL_LENGTH('dbo.DispatchSynthMeta','Profile') IS NULL "
        "ALTER TABLE dbo.DispatchSynthMeta ADD Profile NVARCHAR(32) NULL;",
        f"INSERT INTO dbo.DispatchSynthMeta (SynthVersion, StampedAt, Profile) VALUES ({DISPATCH_SYNTH_VERSION}, GETDATE(), '{_synth_profile()}');",
    ]

    # Chunk into bounded sqlcmd batches (GO every BATCH_STMTS), the same
    # memory-safety pattern as synthesize_analytics.py.
    BATCH_STMTS = 2000
    batched: list[str] = []
    since_go = 0
    for ln in stmts:
        batched.append(ln)
        since_go += 1
        if since_go >= BATCH_STMTS:
            batched += ["GO", "SET NOCOUNT ON;"]
            since_go = 0

    sql_file = Path("/tmp/hcmiswdm_dispatch_seed.sql")
    sql_file.write_text("\n".join(batched), encoding="utf-8")
    print(f"==> Wrote {sql_file.stat().st_size / 1024 / 1024:.1f} MB SQL to {sql_file}")

    print("==> Copying into container...")
    subprocess.run(["docker", "cp", str(sql_file), f"{CONTAINER}:/tmp/dispatch_seed.sql"], check=True)
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "chown", "mssql:0",
                    "/tmp/dispatch_seed.sql"], check=True)

    print("==> Executing...")
    subprocess.run([
        "docker", "exec", "-i", CONTAINER, "/opt/mssql-tools18/bin/sqlcmd",
        "-S", "localhost", "-U", "sa", "-P", SA_PASSWORD, "-C", "-d", DB,
        "-i", "/tmp/dispatch_seed.sql", "-b",
    ], check=True)

    print("==> Row counts:")
    subprocess.run([
        "docker", "exec", "-i", CONTAINER, "/opt/mssql-tools18/bin/sqlcmd",
        "-S", "localhost", "-U", "sa", "-P", SA_PASSWORD, "-C", "-d", DB, "-Q",
        """
SELECT 'Dim.DispatchHub'      AS tbl, COUNT(*) AS n FROM Dim.DispatchHub
UNION ALL SELECT 'Dim.DispatchFacility', COUNT(*) FROM Dim.DispatchFacility
UNION ALL SELECT 'Dim.DispatchPeriod',   COUNT(*) FROM Dim.DispatchPeriod
UNION ALL SELECT 'Fact.Dispatch',        COUNT(*) FROM Fact.Dispatch
UNION ALL SELECT 'Fact.DispatchRun',     COUNT(*) FROM Fact.DispatchRun;
""",
    ], check=True)

    print()
    print("OK: dispatch plan load complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
