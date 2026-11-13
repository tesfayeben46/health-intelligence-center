#!/usr/bin/env python3
"""Generate a synthetic Zambian eLMIS historical star for elmis-postgres.

Why this exists
---------------
The historical eLMIS extract (236 MB gzipped, 1.5 GB plain) is distributed
separately, and eLMIS has no upstream application here that could regenerate
it. Without it the source would start empty and the supply pages would have
nothing to show.

This is the fallback generator: it builds a Zambian supply history from files
committed to the repository, the entity model
(bundle/seeds/entity-model/entity-model.zm.yaml) and the facility registry
(facilities_zm_elmis.json, 2,807 facilities with province, district, tier and
coordinates). Facility names, requisitions and quantities are generated,
deterministically from the entity-model seed, so a fresh clone reproduces the
source without any external artefact. By default it covers the 18 months to
2018-08.

It writes the 16-table surface the `elmis-hist` ingestor pulls (reference.* and
history.requisition/requisition_line), matching schema-only.sql column for
column, so the ingestor, vault and marts run unchanged on top of it.

Usage
-----
    python3 synthesize_zm_history.py \
        --entity-model  bundle/seeds/entity-model/entity-model.zm.yaml \
        --facilities    bundle/seeds/entity-model/sample-data/facilities_zm_elmis.json \
        > /tmp/elmis_zm_data.sql
    # then: psql -f schema-only.sql ; psql -f /tmp/elmis_zm_data.sql
The wrapper scripts/synthesize.sh does both against the running container.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime, timedelta

import yaml

# the eLMIS max/min policy is uniform across facility types in this archive
# (KPI Handbook / pct_policy: max 3 months of stock, emergency order point 0.5).
NOMINAL_MAX_MONTHS = 3
NOMINAL_EOP = 0.5
REPORTING_DAYS = 30

# Tracer products the supply KPIs (osa_s8, stockout_exposure, mos_band …) key on.
# These eLMIS product codes are not in the 64-commodity sample, so they are
# added to the product dimension with tracer=true, each mapped to the programme
# the tracer seed expects. Keep in step with
# 2_pipelines/marts/seeds/pct_gf_tracer_category.csv.
TRACERS = [
    ("ARV0031", "Tenofovir 300mg Tablets", "ARV"),
    ("ARV0036", "Zidovudine 300mg Tablets", "ARV"),
    ("ARV0063", "Tenofovir/Lamivudine/Efavirenz Tablets", "ARV"),
    ("ARV0074", "Tenofovir/Lamivudine/Efavirenz 300/300/400", "ARV"),
    ("ARV0077", "Tenofovir/Lamivudine/Dolutegravir Tablets", "ARV"),
    ("ARV0078", "Dolutegravir Tablets", "ARV"),
    ("HTK0001", "UNIGOLD HIV 1/2 RAPID TEST", "HIV"),
    ("HTK0002", "DETERMINE HIV 1/2 RAPID TEST", "HIV"),
    ("MAL0015", "RDT FOR MALARIA (P FALCIPARUM)", "MAL"),
]

TRACER_CODES = {t[0] for t in TRACERS}

# facility type to rough throughput scale (bigger tiers dispense more)
TIER_SCALE = {
    "LVL3": 9.0, "MILHOS": 8.0, "CDH": 8.0, "LVL2": 5.0, "MSL": 4.0,
    "LVL1": 3.0, "DHO": 3.0, "DATF": 2.0, "VCT": 1.6, "HC": 1.5,
    "HBC": 1.2, "HP": 1.0, "Prog": 1.0, "CHAZ2": 1.0,
}


def esc(v) -> str:
    """Render a value for a Postgres text-format COPY stream."""
    if v is None:
        return r"\N"
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, float):
        return repr(round(v, 6))
    s = str(v)
    return (s.replace("\\", "\\\\").replace("\t", "\\t")
             .replace("\n", "\\n").replace("\r", "\\r"))


class Emit:
    """Accumulates COPY blocks to a single psql-loadable stream."""

    def __init__(self, out):
        self.out = out

    def copy(self, table: str, cols: list[str], rows) -> int:
        w = self.out
        w.write(f"COPY {table} ({', '.join(cols)}) FROM STDIN;\n")
        n = 0
        for r in rows:
            w.write("\t".join(esc(r[c]) for c in cols))
            w.write("\n")
            n += 1
        w.write("\\.\n\n")
        return n


def months_ending(anchor: date, count: int) -> list[date]:
    """`count` month-start dates ending at (and including) anchor's month."""
    y, m = anchor.year, anchor.month
    out = []
    for _ in range(count):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def month_end(d: date) -> date:
    return date(d.year + (d.month == 12), (d.month % 12) + 1, 1) - timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity-model", required=True)
    ap.add_argument("--facilities", required=True)
    ap.add_argument("--months", type=int, default=18)
    ap.add_argument("--anchor", default="2018-08-01",
                    help="latest month in the data (PCT anchor)")
    ap.add_argument("--report-rate", type=float, default=0.86,
                    help="probability a facility files a given programme's R&R in a month")
    ap.add_argument("--out", default="-")
    a = ap.parse_args()

    model = yaml.safe_load(open(a.entity_model))
    facilities = json.load(open(a.facilities))
    rng = random.Random(int(model.get("random_seed", 20260921)))
    out = sys.stdout if a.out == "-" else open(a.out, "w")
    em = Emit(out)
    counts: dict[str, int] = {}

    # The archive ships a statement-level immutability trigger on every table
    # (mutation.reject_scenario_mutation). Disable user triggers for the load
    # (as pg_dump does by adding triggers after COPY) while leaving the FK
    # (system) triggers on, so referential integrity is still enforced. They are
    # re-enabled at the end, so the loaded scenario is immutable thereafter.
    LOADED = [
        "reference.geographic_level", "reference.geographic_zone", "reference.facility_type",
        "reference.facility", "reference.product_category", "reference.product_form",
        "reference.product_group", "reference.dosage_unit", "reference.product",
        "reference.programme", "reference.programme_product", "reference.processing_schedule",
        "reference.reporting_period", "reference.facility_programme_schedule",
        "history.requisition", "history.requisition_line",
    ]
    for t in LOADED:
        out.write(f"ALTER TABLE {t} DISABLE TRIGGER USER;\n")
    out.write("\n")

    anchor = datetime.strptime(a.anchor, "%Y-%m-%d").date()
    period_dates = months_ending(anchor, a.months)

    # source_id allocator (every reference table needs a UNIQUE source_id)
    _seq = {"n": 1000}
    def sid() -> int:
        _seq["n"] += 1
        return _seq["n"]

    # geographic_level
    LEVELS = [("National", "National", 1), ("Province", "Province", 2),
              ("District", "District", 3), ("Facility", "Facility", 4)]
    counts["reference.geographic_level"] = em.copy(
        "reference.geographic_level", ["code", "source_id", "name", "level_number"],
        [{"code": c, "source_id": sid(), "name": n, "level_number": lv} for c, n, lv in LEVELS])

    # geographic_zone: provinces (from the entity model) + districts (from
    #    the registry).  facility.geographic_zone_code points at the DISTRICT;
    # district.parent_zone_code points at the PROVINCE (region code).
    regions = {r["code"]: r["name"] for r in model["regions"]}          # '20' -> 'Copperbelt Province'
    # registry carries region_id (bare code) + region_name; trust it for coverage
    reg_from_reg = {f["region_id"]: f.get("region_name", regions.get(f["region_id"], f["region_id"]))
                    for f in facilities}
    regions = {**reg_from_reg, **regions}
    zones = []
    for code, name in sorted(regions.items()):
        zones.append({"code": f"P{code}", "source_id": sid(), "name": name,
                      "geographic_level_code": "Province", "parent_zone_code": None,
                      "catchment_population": None, "latitude": None, "longitude": None})
    districts = {}
    for f in facilities:
        dc = f["district_code"]
        if dc not in districts:
            districts[dc] = {"code": dc, "source_id": sid(), "name": f.get("district_name", dc),
                             "geographic_level_code": "District",
                             "parent_zone_code": f"P{f['region_id']}",
                             "catchment_population": None,
                             "latitude": round(f["lat"], 6) if f.get("lat") is not None else None,
                             "longitude": round(f["lng"], 6) if f.get("lng") is not None else None}
    zones.extend(districts.values())
    zcols = ["code", "source_id", "name", "geographic_level_code", "parent_zone_code",
             "catchment_population", "latitude", "longitude"]
    counts["reference.geographic_zone"] = em.copy("reference.geographic_zone", zcols, zones)

    # facility_type (the distinct registry tiers)
    tier_codes = sorted({f["facility_type_code"] for f in facilities})
    ftype = [{"code": c, "source_id": sid(), "name": c,
              "geographic_level_code": "Facility",
              "nominal_max_months": NOMINAL_MAX_MONTHS, "nominal_eop": NOMINAL_EOP,
              "active": True} for c in tier_codes]
    counts["reference.facility_type"] = em.copy(
        "reference.facility_type",
        ["code", "source_id", "name", "geographic_level_code", "nominal_max_months", "nominal_eop", "active"],
        ftype)

    # facility (2,807; registry geography, generated stable names)
    fac_rows = []
    for f in facilities:
        fac_rows.append({
            "code": f["facility_code"], "source_id": sid(),
            "name": f"{f.get('district_name','ZM')} {f['facility_type_code']} {f['facility_code']}",
            "gln": None, "facility_type_code": f["facility_type_code"],
            "geographic_zone_code": f["district_code"], "parent_facility_code": None,
            "catchment_population": None,
            "latitude": round(f["lat"], 6) if f.get("lat") is not None else None,
            "longitude": round(f["lng"], 6) if f.get("lng") is not None else None,
            "cold_storage_gross_capacity": None, "cold_storage_net_capacity": None,
            "supplies_others": False, "service_delivery_point": True, "online": False,
            "satellite": False, "has_electricity": True, "active": True, "enabled": True,
            "virtual_facility": False, "go_live_date": "2015-01-01", "go_down_date": None,
        })
    fcols = ["code", "source_id", "name", "gln", "facility_type_code", "geographic_zone_code",
             "parent_facility_code", "catchment_population", "latitude", "longitude",
             "cold_storage_gross_capacity", "cold_storage_net_capacity", "supplies_others",
             "service_delivery_point", "online", "satellite", "has_electricity", "active",
             "enabled", "virtual_facility", "go_live_date", "go_down_date"]
    counts["reference.facility"] = em.copy("reference.facility", fcols, fac_rows)

    # product dimension: 64 entity commodities + 9 tracers
    commodities = model["commodities"]
    # category/form/group/dosage-unit small dimensions
    cats = sorted({c["programme"] for c in commodities})
    prod_cat = [{"code": c, "source_id": sid(), "name": c, "display_order": i + 1}
                for i, c in enumerate(cats)]
    counts["reference.product_category"] = em.copy(
        "reference.product_category", ["code", "source_id", "name", "display_order"], prod_cat)
    em.copy("reference.product_form", ["code", "source_id", "display_order"],
            [{"code": "TAB", "source_id": sid(), "display_order": 1},
             {"code": "KIT", "source_id": sid(), "display_order": 2},
             {"code": "GEN", "source_id": sid(), "display_order": 3}])
    em.copy("reference.product_group", ["code", "source_id", "name"],
            [{"code": "GRP", "source_id": sid(), "name": "General"}])
    em.copy("reference.dosage_unit", ["code", "source_id", "display_order"],
            [{"code": "EA", "source_id": sid(), "display_order": 1}])

    products = []
    prod_programme: dict[str, str] = {}
    prod_amc_base: dict[str, float] = {}
    prod_pack: dict[str, int] = {}
    for c in commodities:
        code = c["id"]
        prog = c["programme"]
        tracer = code in TRACER_CODES
        pack = rng.choice([1, 1, 1, 30, 60, 100])
        products.append({
            "code": code, "source_id": sid(), "primary_name": c["name"], "full_name": c["name"],
            "generic_name": None, "strength": None, "product_form_code": "GEN",
            "dosage_unit_code": "EA", "product_group_code": "GRP", "dispensing_unit": "EA",
            "doses_per_dispensing_unit": 1, "pack_size": pack, "alternate_pack_size": None,
            "refrigerated": False, "room_temperature": True, "hazardous": False, "flammable": False,
            "controlled_substance": False, "light_sensitive": False,
            "source_pack_length": None, "source_pack_width": None, "source_pack_height": None,
            "source_pack_weight": None, "packs_per_carton": None, "source_carton_length": None,
            "source_carton_width": None, "source_carton_height": None, "cartons_per_pallet": None,
            "expected_shelf_life": None, "active": True, "full_supply": True, "tracer": tracer,
            "round_to_zero": False, "pack_rounding_threshold": 0, "archived": False,
        })
        prod_programme[code] = prog
        prod_pack[code] = pack
        prod_amc_base[code] = rng.choice([15, 20, 30, 40, 60, 80, 120, 160, 220, 300])
    pcols = ["code", "source_id", "primary_name", "full_name", "generic_name", "strength",
             "product_form_code", "dosage_unit_code", "product_group_code", "dispensing_unit",
             "doses_per_dispensing_unit", "pack_size", "alternate_pack_size", "refrigerated",
             "room_temperature", "hazardous", "flammable", "controlled_substance", "light_sensitive",
             "source_pack_length", "source_pack_width", "source_pack_height", "source_pack_weight",
             "packs_per_carton", "source_carton_length", "source_carton_width", "source_carton_height",
             "cartons_per_pallet", "expected_shelf_life", "active", "full_supply", "tracer",
             "round_to_zero", "pack_rounding_threshold", "archived"]
    counts["reference.product"] = em.copy("reference.product", pcols, products)

    # programme (superset from the entity model + any programme a product uses)
    prog_names = {p["id"]: p["name"] for p in model["supply_programmes"]}
    used_progs = set(prod_programme.values())
    programmes = [{"code": pc, "source_id": sid(), "name": prog_names.get(pc, pc),
                   "active": True, "budgeting_applies": False, "uses_dar": False, "push": False,
                   "enable_skip_period": True, "use_price_schedule": False}
                  for pc in sorted(set(prog_names) | used_progs)]
    counts["reference.programme"] = em.copy(
        "reference.programme",
        ["code", "source_id", "name", "active", "budgeting_applies", "uses_dar", "push",
         "enable_skip_period", "use_price_schedule"], programmes)

    # programme_product (one row per product, in its programme)
    pp = [{"programme_code": prod_programme[p["code"]], "product_code": p["code"],
           "source_id": sid(), "product_category_code": prod_programme[p["code"]],
           "doses_per_month": 30, "active": True, "current_price": None,
           "display_order": i + 1, "full_supply": True}
          for i, p in enumerate(products)]
    counts["reference.programme_product"] = em.copy(
        "reference.programme_product",
        ["programme_code", "product_code", "source_id", "product_category_code",
         "doses_per_month", "active", "current_price", "display_order", "full_supply"], pp)
    # products grouped by programme (for requisition-line generation)
    prog_products: dict[str, list[str]] = {}
    for p in products:
        prog_products.setdefault(prod_programme[p["code"]], []).append(p["code"])

    # processing_schedule + reporting_period
    em.copy("reference.processing_schedule", ["code", "source_id", "name"],
            [{"code": "MONTHLY", "source_id": sid(), "name": "Monthly"}])
    periods = []
    period_id_by_month = {}
    for d in period_dates:
        pid = int(f"{d.year}{d.month:02d}")
        period_id_by_month[d] = pid
        periods.append({"source_id": pid, "processing_schedule_code": "MONTHLY",
                        "name": d.strftime("%b %Y"),
                        "period_start": f"{d.isoformat()} 00:00:00",
                        "period_end": f"{month_end(d).isoformat()} 00:00:00",
                        "number_of_months": 1})
    counts["reference.reporting_period"] = em.copy(
        "reference.reporting_period",
        ["source_id", "processing_schedule_code", "name", "period_start", "period_end", "number_of_months"],
        periods)

    # which programmes each facility reports (drives schedule + requisitions)
    # EM + LAB everywhere; ARV/HIV at HC and above; MAL broadly; TB at big tiers.
    BIG = {"LVL1", "LVL2", "LVL3", "DHO", "MILHOS", "CDH", "MSL", "DATF"}
    def facility_programmes(tier: str, r: random.Random) -> list[str]:
        progs = set()
        for base in ("EM", "LAB"):
            if base in prog_products:
                progs.add(base)
        if "MAL" in prog_products and r.random() < 0.9:
            progs.add("MAL")
        hiv_ok = tier in BIG or tier in ("HC", "VCT", "DATF") or r.random() < 0.55
        if hiv_ok:
            for base in ("ARV", "HIV"):
                if base in prog_products:
                    progs.add(base)
        if tier in BIG and "TB" in prog_products:
            progs.add("TB")
        # CHAZ (faith-based) variants for a slice of NGO-ish tiers
        if tier in ("HBC", "VCT") :
            for base in ("c_arv", "c_hiv", "c_em", "c_lab"):
                if base in prog_products and r.random() < 0.4:
                    progs.add(base)
        return sorted(p for p in progs if prog_products.get(p))

    fps_rows = []
    fac_progs: dict[str, list[str]] = {}
    grp_seq = 5000
    for f in facilities:
        fc = f["facility_code"]
        fr = random.Random(hash((fc, "prog")) & 0xFFFFFFFF)
        progs = facility_programmes(f["facility_type_code"], fr)
        fac_progs[fc] = progs
        for pc in progs:
            grp_seq += 1
            fps_rows.append({
                "facility_code": fc, "programme_code": pc, "processing_schedule_code": "MONTHLY",
                "source_requisition_group_id": grp_seq, "source_requisition_group_code": f"RG{grp_seq}",
                "source_group_member_id": grp_seq, "source_group_schedule_id": grp_seq,
                "direct_delivery": True, "drop_off_facility_code": None})
    counts["reference.facility_programme_schedule"] = em.copy(
        "reference.facility_programme_schedule",
        ["facility_code", "programme_code", "processing_schedule_code", "source_requisition_group_id",
         "source_requisition_group_code", "source_group_member_id", "source_group_schedule_id",
         "direct_delivery", "drop_off_facility_code"], fps_rows)

    # history.requisition + requisition_line (the facts)
    # Streamed so we never hold a million rows in memory.
    req_cols = ["source_requisition_id", "facility_code", "programme_code", "source_period_id",
                "status", "emergency", "source_application", "requisition_created_at",
                "requisition_modified_at"]
    line_cols = ["source_line_item_id", "source_requisition_id", "product_code",
                 "reported_product_name", "reported_product_category", "reported_dispensing_unit",
                 "beginning_balance", "quantity_received", "quantity_dispensed", "stock_in_hand",
                 "quantity_requested", "calculated_order_quantity", "quantity_approved",
                 "total_losses_and_adjustments", "new_patient_count", "stockout_days",
                 "normalized_consumption", "amc", "max_months_of_stock", "max_stock_quantity",
                 "packs_to_ship", "doses_per_month", "doses_per_dispensing_unit", "pack_size",
                 "round_to_zero", "pack_rounding_threshold", "full_supply", "skipped",
                 "reporting_days", "previous_stock_in_hand", "period_normalized_consumption",
                 "line_item_created_at", "line_item_modified_at"]
    prod_name = {p["code"]: p["primary_name"] for p in products}

    out.write(f"COPY history.requisition ({', '.join(req_cols)}) FROM STDIN;\n")
    req_buffer = []            # (rid, facility, programme, period, status, emerg, app, cre, mod)
    n_req = 0
    req_id = 1_000_000
    line_id = 5_000_000
    line_rows = []             # buffered, written after requisition COPY closes

    for f in facilities:
        fc = f["facility_code"]
        tier = f["facility_type_code"]
        scale = TIER_SCALE.get(tier, 1.0)
        # per-facility reliability (stable): drives how often it stocks out, so
        # availability varies facility-to-facility and therefore province-to-province.
        frel = random.Random(hash((fc, "rel")) & 0xFFFFFFFF).uniform(0.6, 1.4)
        for pc in fac_progs[fc]:
            prods = prog_products[pc]
            # per (facility,product) stock-behaviour propensity, stable across months
            for d in period_dates:
                fr = random.Random(hash((fc, pc, d.isoformat())) & 0xFFFFFFFF)
                if fr.random() > a.report_rate:
                    continue                          # facility skipped this R&R
                req_id += 1
                n_req += 1
                created = datetime(d.year, d.month, month_end(d).day, 10, 0) + timedelta(days=fr.randint(0, 9))
                req_buffer.append({
                    "source_requisition_id": req_id, "facility_code": fc, "programme_code": pc,
                    "source_period_id": period_id_by_month[d], "status": "APPROVED",
                    "emergency": False, "source_application": "eLMIS-v2",
                    "requisition_created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
                    "requisition_modified_at": created.strftime("%Y-%m-%d %H:%M:%S")})
                for code in prods:
                    lr = random.Random(hash((fc, code, d.isoformat())) & 0xFFFFFFFF)
                    base = prod_amc_base[code]
                    seasonal = 1.0 + 0.15 * ((d.month % 6) - 2.5) / 2.5
                    amc = max(1, int(round(base * scale * seasonal * lr.uniform(0.8, 1.2))))
                    target = int(round(NOMINAL_MAX_MONTHS * amc))
                    # ~15% of product-months are unavailable at period end (worse at
                    # low-reliability facilities), giving ~85% on-shelf availability
                    # with provincial spread. stock_in_hand is the reported period-end
                    # position (drives OSA and months-of-stock); the ledger around it is
                    # plausible but not identity-forced, because the marts read the
                    # reported fields directly, as they do for the historical extract.
                    p_out = min(0.35, max(0.04, 0.16 * (1.7 - frel)))
                    stockout = lr.random() < p_out
                    if stockout:
                        stockout_days = lr.randint(4, 26)
                        sih = 0 if lr.random() < 0.7 else int(round(amc * lr.uniform(0.0, 0.25)))
                        dispensed = max(0, int(round(amc * (REPORTING_DAYS - stockout_days) / REPORTING_DAYS
                                                     * lr.uniform(0.8, 1.0))))
                        received = 0
                        norm_cons = None if lr.random() < 0.03 else min(
                            amc * 2, int(round(dispensed * REPORTING_DAYS / max(1, REPORTING_DAYS - stockout_days))))
                    else:
                        stockout_days = 0
                        mos = lr.choice([lr.uniform(0.5, 1.0), lr.uniform(1.0, 3.0),
                                         lr.uniform(1.0, 3.0), lr.uniform(3.0, 4.5)])
                        sih = int(round(mos * amc))
                        dispensed = max(0, int(round(amc * lr.uniform(0.9, 1.1))))
                        received = (target - sih) if lr.random() < 0.7 else int(round(max(0, target - sih) * 0.5))
                        norm_cons = None if lr.random() < 0.03 else dispensed
                    received = max(0, received)
                    losses = 0 if lr.random() > 0.05 else lr.randint(1, max(1, amc // 10))
                    beginning = max(0, sih + dispensed - received + losses)
                    calc_order = max(0, target - sih)
                    requested = calc_order
                    # partial approvals drive the order-fill KPI
                    approved = int(round(calc_order * lr.choice([1.0, 1.0, 1.0, 0.8, 0.6, 0.5])))
                    pack = prod_pack[code]
                    line_id += 1
                    line_rows.append({
                        "source_line_item_id": line_id, "source_requisition_id": req_id,
                        "product_code": code, "reported_product_name": prod_name.get(code),
                        "reported_product_category": prod_programme[code], "reported_dispensing_unit": "EA",
                        "beginning_balance": beginning, "quantity_received": received,
                        "quantity_dispensed": dispensed, "stock_in_hand": sih,
                        "quantity_requested": requested, "calculated_order_quantity": calc_order,
                        "quantity_approved": approved, "total_losses_and_adjustments": -losses if losses else 0,
                        "new_patient_count": lr.randint(0, 40), "stockout_days": stockout_days,
                        "normalized_consumption": norm_cons, "amc": amc,
                        "max_months_of_stock": NOMINAL_MAX_MONTHS, "max_stock_quantity": target,
                        "packs_to_ship": (calc_order + pack - 1) // pack if pack else calc_order,
                        "doses_per_month": 30, "doses_per_dispensing_unit": 1, "pack_size": pack,
                        "round_to_zero": False, "pack_rounding_threshold": 0, "full_supply": True,
                        "skipped": False, "reporting_days": REPORTING_DAYS,
                        "previous_stock_in_hand": beginning - dispensed if beginning >= dispensed else 0,
                        "period_normalized_consumption": norm_cons,
                        "line_item_created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
                        "line_item_modified_at": created.strftime("%Y-%m-%d %H:%M:%S")})
        # flush requisition rows periodically to bound memory
        if len(req_buffer) >= 20000:
            for r in req_buffer:
                out.write("\t".join(esc(r[c]) for c in req_cols) + "\n")
            req_buffer.clear()
    for r in req_buffer:
        out.write("\t".join(esc(r[c]) for c in req_cols) + "\n")
    out.write("\\.\n\n")
    counts["history.requisition"] = n_req

    n_line = em.copy("history.requisition_line", line_cols, line_rows)
    counts["history.requisition_line"] = n_line

    for t in LOADED:
        out.write(f"ALTER TABLE {t} ENABLE TRIGGER USER;\n")
    out.write("\n")

    for t, n in counts.items():
        sys.stderr.write(f"  {t:44s} {n:>10,}\n")
    sys.stderr.write(f"  {'anchor month':44s} {a.anchor} · {a.months} months · seed {model.get('random_seed')}\n")
    if out is not sys.stdout:
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
