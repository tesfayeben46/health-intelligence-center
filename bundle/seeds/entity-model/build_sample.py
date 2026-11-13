#!/usr/bin/env python3
"""
NHDW shared sampler: the single source of geography for the source generators.
Reads entity-model.yaml and the facility registry (the public MFR export for
Ethiopia) and emits a deterministic sample every source generator consumes.

Outputs (under ./_build/):
  regions.json       14 rows: region_id, name, code, epss_hub, population
  woredas.json       N rows: woreda_id, region_id, zone, name, urban_rural
  facilities.json    N rows: facility_id, mfr_uuid, name, tier_id,
                             woreda_id, region_id, ownership, opened_year
  periods.json       daily / weekly / monthly with Ethiopian + Gregorian
  programmes.json    flat array
  indicators.json    flat array (links to programme_id)
  commodities.json   flat array (links to programme_id, supplier)
  cadres.json        flat array
  epss_hubs.json     flat array

Run:
  python3 bundle/seeds/entity-model/build_sample.py
"""
from __future__ import annotations
import json, os, re, sys, random, hashlib, datetime
from pathlib import Path

# YAML loader · prefer system PyYAML, fall back to a minimal hand-parser
try:
    import yaml  # type: ignore
except ImportError:
    print("ERROR: install PyYAML  ->  pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT       = Path(__file__).resolve().parent

# Which country this sample is for. The entity model is the data layer of a
# country profile (et-v3 declares entity-model.yaml; another country declares
# its own), so it is chosen the same way as every other profile input.
#
# Resolution: --model <path>, then $NHDW_ENTITY_MODEL, then the active profile's
# layers.data.entity_model, then entity-model.yaml (the default when no profile
# is active).
def _resolve_model() -> Path:
    argv = sys.argv
    if "--model" in argv:
        return Path(argv[argv.index("--model") + 1])
    env = os.environ.get("NHDW_ENTITY_MODEL", "").strip()
    if env:
        return Path(env)
    try:
        import yaml as _y
        pdir  = Path(os.environ.get("NHDW_PROFILE_DIR", "/opt/nhdw-profiles"))
        state = Path(os.environ.get("NHDW_PROFILE_STATE_DIR", "/opt/nhdw-state"))
        key = ""
        for pointer in (state / "ACTIVE", pdir / "ACTIVE"):
            if pointer.is_file():
                key = pointer.read_text(encoding="utf-8").strip()
                break
        key = key or os.environ.get("NHDW_PROFILE", "").strip()
        if key:
            man = _y.safe_load((pdir / key / "profile.yaml").read_text(encoding="utf-8"))
            declared = (((man or {}).get("layers") or {}).get("data") or {}).get("entity_model")
            if declared:
                cand = Path(declared)
                return cand if cand.is_absolute() else ROOT.parents[2] / declared
    except Exception:
        pass                       # an unreadable profile falls back to the default
    return ROOT / "entity-model.yaml"


MODEL_PATH = _resolve_model()


def _marts_seed_path(name: str) -> Path:
    """Where a generated dbt seed goes.

    The default model owns the canonical seed names, because those are what
    dbt's seed config and every model referencing them expect. A non-default
    model writes alongside them under its own suffix instead of over them, so
    running another country's sampler cannot replace the default country's
    rows in a tracked file.

    A country whose seeds should become canonical is promoted explicitly, by
    pointing dbt at the suffixed file, not by whichever sampler ran last."""
    base = REPO_ROOT / "2_pipelines" / "marts" / "seeds"
    if _STEM == "entity-model":
        return base / name
    suffix = _STEM.replace("entity-model", "").lstrip(".-") or _STEM
    return base / f"{Path(name).stem}.{suffix}{Path(name).suffix}"


# Peek at the model early: the registry path is one of its fields, and MFR_PATH
# below needs it. The full load happens in main().
try:
    import yaml as _yaml_peek
    _MODEL_DOC = _yaml_peek.safe_load(MODEL_PATH.read_text(encoding="utf-8")) or {}
except Exception:
    _MODEL_DOC = {}
# One _build per model, so two countries' samples never overwrite each other and
# a generator cannot silently consume the wrong country's geography.
_STEM      = MODEL_PATH.stem
BUILD_DIR  = ROOT / "_build" if _STEM == "entity-model" else ROOT / f"_build-{_STEM}"
REPO_ROOT  = ROOT.parents[2]   # bundle/seeds/entity-model -> repo root
# The facility registry the sample is drawn from (an in-repo copy). Ethiopia's
# is the MFR export; Zambia's is the eLMIS registry pulled from the warehouse.
# The model names its own via `facility_registry`, so the sampler is not bound
# to one country's registry file.
_registry  = (_MODEL_DOC.get("facility_registry")
              if isinstance(_MODEL_DOC, dict) else None) or \
             "sample-data/facilities_2026-07-07.json"
MFR_PATH   = Path(_registry) if Path(_registry).is_absolute() else ROOT / _registry

# Tier <-> MFR facilityType crosswalk
# Maps our 9 model tiers to one-or-more MFR facilityType values so the
# sampler can pull real, region-locked facility names + names.
TIER_MFR_TYPES = {
    "HP":  {"Basic Health Post", "Comprehensive Health Post", "Health Post"},
    "HC":  {"Health Center"},
    "PHC": {"Primary Clinic", "Institution Based Primary Clinic"},
    "MC":  {"Medium Clinic", "Institution Based Medium Clinic"},
    "PH":  {"Primary Hospital"},
    "GH":  {"General Hospital"},
    "SH":  {"Specialised", "Specialized Hospital", "Tertiary Hospital"},
    "PH-": {"pharmacy", "Pharmacy"},
    "DS":  {"Drug Store"},
}

# MFR region names -> our model region IDs
REGION_NAME_TO_ID = {
    "Oromia Region":                   "ET-OR",
    "Amhara Region":                   "ET-AM",
    "Addis Ababa City Administration": "ET-AA",
    "Somali Region":                   "ET-SO",
    "South Ethiopia Region":           "ET-SE",
    "Central Ethiopia Region":         "ET-CE",
    "South West Ethiopia Region":      "ET-SW",
    "Tigray Region":                   "ET-TI",
    "Sidama Region":                   "ET-SI",
    "Benishangul-Gumuz Region":        "ET-BG",
    "Afar Region":                     "ET-AF",
    "Gambella Region":                 "ET-GA",
    "Dire Dawa City Administration":   "ET-DD",
    "Harari Region":                   "ET-HA",
    # legacy alias seen in older MFR snapshots
    "SNNP Region":                     "ET-SE",
}

MFR_OWNERSHIP_NORMALISED = {
    "Public/Government":     "public",
    "Private for profit":    "private",
    "Private Not for profit":"ngo",
    "Other Government":      "public",
}


def _slug(text: str) -> str:
    """woreda/zone -> stable URL-safe slug."""
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _short_hash(s: str, n: int = 6) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def load_model() -> dict:
    with open(MODEL_PATH) as f:
        return yaml.safe_load(f)


def load_mfr() -> list[dict]:
    """The facility registry. Ethiopia's MFR export wraps its rows in `data`;
    a registry pulled straight from the warehouse is a bare array. Accept
    either rather than making every future registry adopt one export's
    envelope."""
    with open(MFR_PATH) as f:
        doc = json.load(f)
    return doc["data"] if isinstance(doc, dict) else doc


def population_facility_set(model: dict, registry: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Use the registry as the facility set, rather than sampling from it.

    Ethiopia's registry is much larger than the sample the platform seeds, so
    `stratified_facility_sample` draws a sample that mirrors the real pyramid.
    Zambia's registry is the set of facilities the eLMIS extract contains; that
    is the population, and sampling it would discard real rows to synthesise a
    smaller imitation of them.

    Recognised by the model declaring `facility_registry_is_population: true`.
    Returns the same (facilities, districts, provinces) triple the stratified
    path returns, so every caller downstream is unchanged."""
    rnd = random.Random(model["random_seed"])
    mix_by_tier = {t["id"]: t.get("ownership_mix") or {"public": 1.0}
                   for t in model.get("facility_tiers", [])}

    def own(tier_id: str) -> str:
        mix = mix_by_tier.get(tier_id) or {"public": 1.0}
        r, acc = rnd.random(), 0.0
        for k, v in mix.items():
            acc += v
            if r <= acc:
                return k
        return "public"

    facilities, districts, provinces = [], {}, {}
    for f in registry:
        rid  = f"ZM-{f['region_id']}" if not str(f.get("region_id", "")).startswith("ZM-") \
               else f["region_id"]
        did  = str(f.get("district_code") or "")
        tier = f.get("facility_type_code") or "UNK"
        facilities.append({
            "facility_id": f.get("facility_code"),
            "mfr_uuid":    f.get("facility_code"),
            "name":        f.get("facility_name"),
            "tier_id":     tier,
            "woreda_id":   did,
            "region_id":   rid,
            "ownership":   own(tier),
            "opened_year": rnd.randint(1975, model.get("target_year", 2018) - 1),
            "lat":         f.get("lat"),
            "lng":         f.get("lng"),
            "coord_basis": f.get("coord_basis"),
        })
        districts.setdefault(did, {
            "woreda_id": did, "region_id": rid, "zone": f.get("region_name"),
            "name": f.get("district_name"), "urban_rural": "urban" if rnd.random() < .25 else "rural",
        })
        provinces.setdefault(rid, {"zone_id": rid, "region_id": rid, "name": f.get("region_name")})
    return facilities, list(districts.values()), list(provinces.values())


def stratified_facility_sample(model: dict, mfr: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (facilities, woredas, zones). Stratifies by (region x tier) using
    real MFR weights so the synthetic sample mirrors Ethiopia's facility
    pyramid + geographic distribution.
    """
    rnd = random.Random(model["random_seed"])

    # First, group every MFR facility by (region_id, model_tier)
    by_strata: dict[tuple[str, str], list[dict]] = {}
    for f in mfr:
        rid = REGION_NAME_TO_ID.get(f.get("region") or "")
        if not rid:
            continue
        ftype = f.get("facilityType") or ""
        # find which of our tiers this MFR type maps to
        tier_id = next((tid for tid, types in TIER_MFR_TYPES.items() if ftype in types), None)
        if not tier_id:
            continue
        # filter out non-operational
        if f.get("operationalStatus") != "Operational":
            continue
        if f.get("status") != "Approved":
            continue
        by_strata.setdefault((rid, tier_id), []).append(f)

    region_weight = {r["id"]: r["weight"] / 100.0 for r in model["regions"]}

    # Allocate per-tier count across regions, weighted by real region share
    facilities_out: list[dict] = []
    for tier in model["facility_tiers"]:
        tier_total = tier["count"]
        # distribute that count across regions by weight, biased by what's
        # actually available in MFR for that tier
        per_region: dict[str, int] = {}
        avail_per_region = {rid: len(by_strata.get((rid, tier["id"]), [])) for rid in region_weight}
        total_avail = sum(avail_per_region.values()) or 1
        for rid, w in region_weight.items():
            # blended allocation: 60% real population weight, 40% real availability
            blended = 0.6 * w + 0.4 * (avail_per_region[rid] / total_avail)
            per_region[rid] = max(0, int(round(blended * tier_total)))
        # rounding correction -> close to tier_total exactly
        delta = tier_total - sum(per_region.values())
        if delta:
            # add/remove from the region with the largest pool
            order = sorted(per_region.keys(), key=lambda r: -avail_per_region[r])
            i = 0
            while delta != 0 and i < len(order) * 4:
                step = 1 if delta > 0 else -1
                rid = order[i % len(order)]
                if avail_per_region[rid] > per_region[rid] + step >= 0:
                    per_region[rid] += step
                    delta -= step
                i += 1

        # Per region, sample without replacement; if MFR pool is too small,
        # fall back to synthesizing additional facilities in real woredas.
        for rid, n in per_region.items():
            pool = by_strata.get((rid, tier["id"]), [])
            picked = rnd.sample(pool, min(n, len(pool))) if pool else []
            for src in picked:
                facilities_out.append({
                    "facility_id": f"FAC-{rid[-2:]}-{tier['id']}-{_short_hash(str(src['id']))}",
                    "mfr_id":       src["id"],
                    "name":         src["name"],
                    "tier_id":      tier["id"],
                    "tier_name":    tier["name"],
                    "region_id":    rid,
                    "zone":         src.get("zone"),
                    "woreda":       src.get("woreda"),
                    "ownership":    MFR_OWNERSHIP_NORMALISED.get(src.get("ownership") or "", "private"),
                    "operational":  True,
                })
            # synthesize the rest deterministically in a real woreda already
            # sampled; keeps the geography real even when MFR has too few
            # matching tier+region rows
            synth_needed = n - len(picked)
            if synth_needed > 0:
                # any MFR facility in this region for naming context
                fallback_pool = [f for f in mfr if REGION_NAME_TO_ID.get(f.get("region") or "") == rid and f.get("woreda")]
                if not fallback_pool:
                    continue
                for k in range(synth_needed):
                    parent = rnd.choice(fallback_pool)
                    facilities_out.append({
                        "facility_id": f"FAC-{rid[-2:]}-{tier['id']}-S{_short_hash(f'{rid}-{tier['id']}-{k}-{model['random_seed']}')}",
                        "mfr_id":       None,                # synthetic
                        "name":         f"{parent.get('woreda') or 'Woreda'} {tier['name']} {k+1:02d}",
                        "tier_id":      tier["id"],
                        "tier_name":    tier["name"],
                        "region_id":    rid,
                        "zone":         parent.get("zone"),
                        "woreda":       parent.get("woreda"),
                        "ownership":    "public" if tier["id"] in ("HP", "HC", "PH", "GH", "SH") else "private",
                        "operational":  True,
                    })

    # Assign ownership categorically using the model's per-tier mix
    tier_mix = {t["id"]: t["ownership_mix"] for t in model["facility_tiers"]}
    for f in facilities_out:
        # honour MFR ownership where present, otherwise sample from the mix
        if f["ownership"] in ("public", "private", "ngo"):
            continue
        mix = tier_mix[f["tier_id"]]
        roll = rnd.random()
        acc = 0.0
        for own, p in mix.items():
            acc += p
            if roll <= acc:
                f["ownership"] = own
                break

    # Derive woreda + zone tables from the chosen facilities
    # Keyed by (region, zone, woreda): city administrations number their
    # woredas per sub-city ("Woreda 4" exists in Bole and in Kolfe), so a
    # region+name key would merge distinct woredas into one row and file their
    # facilities under the wrong sub-city. Bare-numbered woredas get a
    # zone-qualified display name ("Kolfe Sub city · Woreda 4"), since
    # "Woreda 4" alone is ambiguous on a national dashboard.
    _bare_woreda = re.compile(r"^woreda\s*\d+$", re.IGNORECASE)
    _city_regions = {"ET-AA", "ET-DD", "ET-HA"}  # city administrations = urban
    woredas: dict[tuple[str, str, str], dict] = {}
    zones:   dict[tuple[str, str], dict] = {}
    for f in facilities_out:
        if not f.get("woreda"):
            continue
        zone = f.get("zone") or ""
        wkey = (f["region_id"], zone, f["woreda"])
        if wkey not in woredas:
            disp = f["woreda"]
            if zone and _bare_woreda.match(disp.strip()):
                disp = f"{zone} · {disp}"
            woredas[wkey] = {
                "woreda_id": f"WOR-{f['region_id'][-2:]}-{_short_hash(zone + '|' + f['woreda'])}",
                "region_id": f["region_id"],
                "zone":      f["zone"],
                "name":      disp,
                "urban_rural": "urban"
                    if f["region_id"] in _city_regions
                    or any(t in (f["woreda"] or "").lower() for t in ("town", "city", "addis"))
                    else "rural",
            }
        f["woreda_id"] = woredas[wkey]["woreda_id"]
        zkey = (f["region_id"], f["zone"] or "")
        if zkey not in zones and f["zone"]:
            zones[zkey] = {
                "zone_id":   f"ZON-{f['region_id'][-2:]}-{_short_hash(f['zone'])}",
                "region_id": f["region_id"],
                "name":      f["zone"],
            }

    return facilities_out, list(woredas.values()), list(zones.values())


#  Calendar · Ethiopian <-> Gregorian
def gregorian_to_ethiopian(gdate: datetime.date) -> tuple[int, int, int]:
    """Approximate ET calendar conversion (good for FY mapping; not for births).
    The new ET year (Meskerem 1) lands on Sept 11 (or Sept 12 in leap years).
    """
    g_year = gdate.year
    # leap-year-adjusted new-year date
    leap = (g_year % 4 == 3)
    et_new_year = datetime.date(g_year, 9, 12 if leap else 11)
    if gdate >= et_new_year:
        et_year = g_year - 7
        days_into = (gdate - et_new_year).days
    else:
        et_year = g_year - 8
        prev_leap = ((g_year - 1) % 4 == 3)
        et_new_year_prev = datetime.date(g_year - 1, 9, 12 if prev_leap else 11)
        days_into = (gdate - et_new_year_prev).days
    # ET months 1-12 are 30 days, month 13 (Pagume) is 5 or 6 days
    if days_into < 360:
        et_month = days_into // 30 + 1
        et_day   = days_into % 30 + 1
    else:
        et_month = 13
        et_day   = days_into - 360 + 1
    return et_year, et_month, et_day


def build_periods(target_year: int, history_years: int) -> dict:
    """Daily / weekly / monthly / quarterly precomputed."""
    end   = datetime.date(target_year, 6, 30)
    start = datetime.date(target_year - history_years, 1, 1)
    days, months_set, quarters_set, weeks_set = [], set(), set(), set()
    d = start
    while d <= end:
        ey, em, ed = gregorian_to_ethiopian(d)
        iso_y, iso_w, _ = d.isocalendar()
        q = (d.month - 1) // 3 + 1
        days.append({
            "day_id":      d.isoformat(),
            "gregorian":   d.isoformat(),
            "et_year":     ey, "et_month": em, "et_day": ed,
            "iso_week":    f"{iso_y}-W{iso_w:02d}",
            "month":       d.strftime("%Y-%m"),
            "quarter":     f"{d.year}-Q{q}",
            "fy_ec":       f"FY{ey if em >= 1 else ey - 1}",
        })
        weeks_set.add((f"{iso_y}-W{iso_w:02d}", d.isoformat(), ey, em))
        months_set.add((d.strftime("%Y-%m"), ey, em))
        quarters_set.add((f"{d.year}-Q{q}", ey, em))
        d += datetime.timedelta(days=1)
    # Months / quarters can have multiple ET-year mappings (cross-year overlap).
    # Dedupe to one entry per Gregorian period, keeping the ET mapping that
    # corresponds to the first day of the period (most calendars treat
    # Gregorian -> ET assignment by the period's starting day).
    def _dedupe(rows, key, secondary_keys):
        seen, out = {}, []
        for r in rows:
            if r[key] in seen: continue
            seen[r[key]] = True
            out.append(r)
        return out

    months_rows = [{"month_id": m, "et_year": ey, "et_month": em}
                   for (m, ey, em) in months_set]
    months_rows.sort(key=lambda x: x["month_id"])
    months_rows = _dedupe(months_rows, "month_id", ["et_year", "et_month"])

    quarters_rows = [{"quarter_id": q, "et_year": ey, "et_month": em}
                     for (q, ey, em) in quarters_set]
    quarters_rows.sort(key=lambda x: x["quarter_id"])
    quarters_rows = _dedupe(quarters_rows, "quarter_id", ["et_year", "et_month"])

    return {
        "days":     days,
        "weeks":    sorted([{"week_id": w, "first_day": fd, "et_year": ey, "et_month": em}
                            for (w, fd, ey, em) in weeks_set], key=lambda x: x["first_day"]),
        "months":   months_rows,
        "quarters": quarters_rows,
    }


def main() -> None:
    model = load_model()
    print(f"loaded entity model · seed={model['random_seed']} · target={model['target_year']}")

    if not MFR_PATH.exists():
        print(f"ERROR: MFR JSON not found at {MFR_PATH}", file=sys.stderr)
        sys.exit(1)
    mfr = load_mfr()
    print(f"loaded MFR · {len(mfr):,} real facilities")

    if model.get("facility_registry_is_population"):
        facilities, woredas, zones = population_facility_set(model, mfr)
        print(f"registry is the population · {len(facilities)} facilities, "
              f"{len(woredas)} districts, {len(zones)} provinces (not sampled)")
    else:
        facilities, woredas, zones = stratified_facility_sample(model, mfr)
    periods = build_periods(model["target_year"], model["history_years"])

    BUILD_DIR.mkdir(exist_ok=True)
    outputs = {
        "regions.json":     model["regions"],
        "zones.json":       zones,
        "woredas.json":     woredas,
        "facilities.json":  facilities,
        "facility_tiers.json": model["facility_tiers"],
        "periods.json":     periods,
        "programmes.json":  model["programmes"],
        "indicators.json":  model["indicators"],
        "commodities.json": model["commodities"],
        "cadres.json":      model["cadres"],
        "epss_hubs.json":   model["epss_hubs"],
    }
    for fname, body in outputs.items():
        (BUILD_DIR / fname).write_text(json.dumps(body, indent=2, ensure_ascii=False))

    # EPSS hub catchment -> marts dbt seed
    # Single source of truth = the `serves` lists on entity-model epss_hubs
    # (real EPSS hubs supply multi-region catchments; CE/SW have no hub of
    # their own). weight = the region's share of facilities within the hub's
    # catchment, so a hub's stock is fully allocated (Σ weights = 1 per hub)
    # and supply marts can attribute availability to all 14 regions.
    # Emitted as a generated dbt seed: edit entity-model.yaml, not the CSV.
    from collections import Counter
    region_code = {r["id"]: r["code"] for r in model["regions"]}
    fac_by_region = Counter(f["region_id"] for f in facilities)
    catch_lines = ["hub_id,hub_name,region_id,weight"]
    for h in model["epss_hubs"]:
        served = h.get("serves") or [h["region_id"]]
        tot = sum(fac_by_region.get(r, 0) for r in served)
        for r in served:
            wgt = (fac_by_region.get(r, 0) / tot) if tot else 1.0 / len(served)
            catch_lines.append(
                f"{h['id']},{h['name'].replace(',', ' ')},{region_code[r]},{round(wgt, 4)}")
    marts_seed = _marts_seed_path("epss_hub_catchment.csv")
    marts_seed.write_text("\n".join(catch_lines) + "\n", encoding="utf-8")
    print(f"wrote hub catchment seed -> {marts_seed} ({len(catch_lines) - 1} rows)")

    # Disease -> response-commodity map -> marts dbt seed
    # Single source of truth = the disease_response section (see its comment
    # for the rule on unmapped diseases). Carries the commodity name, the
    # supply marts' join key (HCMIS item names come from this catalogue).
    commodity_name = {c["id"]: c["name"] for c in model["commodities"]}
    resp_lines = ["disease_code,commodity_id,commodity_name,role"]
    for m in model.get("disease_response", []):
        cname = commodity_name.get(m["commodity"])
        if not cname:
            raise SystemExit(f"disease_response maps unknown commodity {m['commodity']}")
        resp_lines.append(
            f"{m['disease']},{m['commodity']},{cname.replace(',', ' ')},{m['role']}")
    resp_seed = _marts_seed_path("disease_response_map.csv")
    resp_seed.write_text("\n".join(resp_lines) + "\n", encoding="utf-8")
    print(f"wrote disease response seed -> {resp_seed} ({len(resp_lines) - 1} rows)")

    # Commodity reference -> marts dbt seed
    # One catalogue row per commodity: programme (the Resource pages'
    # programme-filter join) + enables (the HMIS indicator this commodity
    # supports; the stockout->missed-services estimate joins through it; empty
    # enables = no clean linkage, so impact stays NULL). Joined to the
    # supply marts on commodity name (HCMIS item names come from here).
    ref_lines = ["commodity_id,commodity_name,programme_id,enables_indicator"]
    for c in model["commodities"]:
        ref_lines.append(
            f"{c['id']},{c['name'].replace(',', ' ')},{c['programme']},{c.get('enables', '')}")
    ref_seed = _marts_seed_path("commodity_reference.csv")
    ref_seed.write_text("\n".join(ref_lines) + "\n", encoding="utf-8")
    print(f"wrote commodity reference seed -> {ref_seed} ({len(ref_lines) - 1} rows)")

    # Data dictionary -> docs/data-dictionary.md
    # Generated from this entity model and the marts schema files: the single
    # source of truth for every element/attribute/entity the platform carries.
    # Written to docs/ (developer copy); synced into bundle/docs/ by
    # bundle/scripts/sync-docs.sh, and seed-docs.sh publishes it to the object
    # store (admin -> Documentation). Regenerated on every build_sample run.
    import glob as _glob
    dd = ["# Data dictionary", "",
          "GENERATED by `bundle/seeds/entity-model/build_sample.py` from",
          "`entity-model.yaml` and the marts schema files. Edit those, never this file.",
          ""]
    dd += ["## Programmes", "", "| id | name |", "|---|---|"]
    for prg in model["programmes"]:
        dd.append(f"| {prg['id']} | {prg['name']} |")
    dd += ["", "## HMIS indicators (DHIS2 data elements)", "",
           "| id | name | programme | calc | denominator | unit |", "|---|---|---|---|---|---|"]
    for i in model["indicators"]:
        dd.append(f"| {i['id']} | {i['name']} | {i.get('programme','')} | "
                  f"{i.get('calc','')} | {i.get('denom','')} | {i.get('unit','')} |")
    dd += ["", "## Commodities (supply catalogue)", "",
           "| id | name | programme | VEN | form | supplier | enables (service indicator) |",
           "|---|---|---|---|---|---|---|"]
    for c in model["commodities"]:
        dd.append(f"| {c['id']} | {c['name']} | {c['programme']} | {c.get('ven','')} | "
                  f"{c.get('form','')} | {c.get('supplier','')} | {c.get('enables','')} |")
    dd += ["", "## Outbreak response map (disease -> commodity)", "",
           "| disease | commodity | role |", "|---|---|---|"]
    for m in model.get("disease_response", []):
        dd.append(f"| {m['disease']} | {m['commodity']} | {m['role']} |")
    dd += ["", "Diseases with no tracked countermeasure (not mapped): "
           "MEN, RELF, reported as 'no tracked countermeasure'.", ""]
    dd += ["## Supply hubs and catchments", "", "| hub | name | home region | serves |", "|---|---|---|---|"]
    for h in model["epss_hubs"]:
        serves = ", ".join(h.get("serves", [h["region_id"]]))
        dd.append(f"| {h['id']} | {h['name']} | {h['region_id']} | {serves} |")
    dd += ["", "## Regions", "", "| id | code | name | population (M) |", "|---|---|---|---|"]
    for r in model["regions"]:
        dd.append(f"| {r['id']} | {r['code']} | {r['name']} | {r.get('population_mn','')} |")
    # marts: names + descriptions from the dbt schema files
    dd += ["", "## Marts (bi_* serving layer)", ""]
    for yml in sorted(_glob.glob(str(REPO_ROOT / "2_pipelines/marts/models/**/_models.yml"), recursive=True)):
        data = yaml.safe_load(open(yml)) or {}
        for mdl in data.get("models", []):
            desc = " ".join((mdl.get("description") or "").split())
            dd.append(f"### {mdl['name']}")
            dd.append("")
            dd.append(desc or "(no description)")
            dd.append("")
    # Same rule as the dbt seeds: the default model owns the canonical name,
    # another country writes beside it, so a run for one country never rewrites
    # another country's served data dictionary.
    dict_path = (REPO_ROOT / "docs" / "data-dictionary.md" if _STEM == "entity-model"
                 else REPO_ROOT / "docs" / f"data-dictionary.{_STEM.replace('entity-model', '').lstrip('.-')}.md")
    dict_path.write_text("\n".join(dd) + "\n", encoding="utf-8")
    print(f"wrote data dictionary -> {dict_path} ({len(dd)} lines)")

    print()
    print(f"wrote {len(outputs)} files -> {BUILD_DIR}")
    print(f"  facilities: {len(facilities):>5}")
    print(f"  woredas:    {len(woredas):>5}")
    print(f"  zones:      {len(zones):>5}")
    print(f"  days:       {len(periods['days']):>5}")
    print(f"  months:     {len(periods['months']):>5}")
    print()
    # per-region tally
    from collections import Counter
    by_region = Counter(f["region_id"] for f in facilities)
    by_tier   = Counter(f["tier_id"] for f in facilities)
    by_own    = Counter(f["ownership"] for f in facilities)
    print("facility tally · region:")
    for r, n in sorted(by_region.items(), key=lambda x: -x[1]):
        print(f"  {r}: {n}")
    print()
    print("facility tally · tier:")
    for t, n in sorted(by_tier.items(), key=lambda x: -x[1]):
        print(f"  {t}: {n}")
    print()
    print("facility tally · ownership:")
    for o, n in sorted(by_own.items(), key=lambda x: -x[1]):
        print(f"  {o}: {n}")


if __name__ == "__main__":
    main()
