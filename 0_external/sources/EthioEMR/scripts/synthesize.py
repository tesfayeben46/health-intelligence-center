#!/usr/bin/env python3
"""Generate EthioEMR's synthetic clinical data on top of the restored OpenMRS schema.

All patients, providers, visits, encounters, observations and orders are
generated; none come from a real EMR. Every entity (facility, programme,
commodity, period) is drawn from the shared demonstration entity model in
`bundle/seeds/entity-model/_build/*.json`, so warehouse layers can join
EthioEMR rows to MFR, DHIS2, HCMIS and mBrana rows on stable shared keys.

It writes:
  - location          (facilities.json, tier_id ∈ {HC,PH,GH,SH}; uuid5 from
                       facility_id; shared facility_id + mfr_id encoded in
                       address1 for join-back)
  - program (updates) (rewrite `description` to embed the shared programme_id)
  - drug              (curated subset of commodities.json; drug.name stores
                       the shared commodity_id for join-back)
  - provider          (with associated person + person_name)
  - person + patient + person_name + person_address + patient_identifier
                       (generated Ethiopian names; addresses set to the facility
                       region/woreda)
  - visit             (encounter_datetime constrained to periods.json window)
  - encounter         (1-3 per visit, with provider linked via
                       encounter_provider)
  - obs               (curated numeric concept panel)
  - orders + drug_order (on ~70% of ART Refill, Consultation and ANC encounters)
  - patient_program   (HIV 12%, TB 8%, MNCH 22% women 15-49, NCD 14% ≥35yo)
  - conditions        (a few problem-list rows per NCD patient)
  - allergy           (lightweight allergy_status flip on a subset)

Approach: build one SQL file, docker cp it into ethioemr-mysql, load it with mysql.

Configurable via .env (.env.example sets 500 patients and 30 locations):
  ETHIOEMR_SYNTH_PATIENT_COUNT      default 1500
  ETHIOEMR_SYNTH_PROVIDER_COUNT     default 20
  ETHIOEMR_SYNTH_YEARS_HISTORY      default 3 (printed only; the window comes
                                              from periods.json)
  ETHIOEMR_SYNTH_SEED               default = shared entity-model random_seed
  ETHIOEMR_SYNTH_LOCATION_COUNT     default 0 (= use ALL HC/PH/GH/SH facilities;
                                              set >0 to cap for fast smoke runs)
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import uuid
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
SHARED_MODEL = REPO_ROOT / "bundle" / "seeds" / "entity-model" / "entity-model.yaml"

if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


# Shared entity model loaders
def _load_json(name: str):
    p = SHARED_BUILD / name
    if not p.exists():
        print(f"ERROR: missing shared sample {p}", file=sys.stderr)
        print("      run bundle/seeds/entity-model/build_sample.py first", file=sys.stderr)
        sys.exit(1)
    with p.open() as f:
        return json.load(f)


def _shared_seed_default() -> int:
    """Read random_seed from entity-model.yaml without requiring PyYAML."""
    if not SHARED_MODEL.exists():
        return 42
    for line in SHARED_MODEL.read_text().splitlines():
        s = line.strip()
        if s.startswith("random_seed:"):
            try:
                return int(s.split(":", 1)[1].split("#")[0].strip())
            except ValueError:
                return 42
    return 42


PROVIDER_COUNT = int(os.environ.get("ETHIOEMR_SYNTH_PROVIDER_COUNT", "20"))
PATIENT_COUNT  = int(os.environ.get("ETHIOEMR_SYNTH_PATIENT_COUNT", "1500"))
YEARS_HISTORY  = int(os.environ.get("ETHIOEMR_SYNTH_YEARS_HISTORY", "3"))
LOC_CAP        = int(os.environ.get("ETHIOEMR_SYNTH_LOCATION_COUNT", "0"))
SEED           = int(os.environ.get("ETHIOEMR_SYNTH_SEED", str(_shared_seed_default())))
random.seed(SEED)

CONTAINER  = "ethioemr-mysql"
DB         = os.environ.get("ETHIOEMR_DB_NAME", "openmrs")
ROOT_PW    = os.environ["ETHIOEMR_MYSQL_ROOT_PASSWORD"]

# Synthesis-contract version: bump whenever this script changes what it writes
# or the shared entity model it reads changes shape (v2: facility addresses
# carry sub-city-qualified Addis Ababa woreda names). Stamped into
# openmrs.synth_meta; bundle/scripts/sources-up.sh re-synthesizes on version
# drift. A packed dump without the marker is trusted with a warning, because it
# may carry runtime clinical edits.
SYNTH_VERSION = 2
ADMIN_USER_ID = 1   # 'admin' from the dump
UUID_NS    = uuid.NAMESPACE_DNS   # stable namespace for uuid5(facility_id)

# Reference data referenced by ID (present in the restored dump)
PATIENT_IDENTIFIER_TYPE_MRN   = 1   # OpenMRS Identification Number (required)
PATIENT_IDENTIFIER_TYPE_NATID = 5   # National ID

VISIT_TYPES = {"Outpatient": 1, "Inpatient": 3}

ENCOUNTER_TYPES = {
    "Triage":          6,
    "Consultation":    3,
    "ART Refill":     21,
    "ANC Mother Enrollment": 224,
    "Lab Order":      30,
    "Visit Note":     241,
}

ENCOUNTER_ROLE_CLINICIAN = 2

# OpenMRS program rows (id to shared programme_id). HIV, TB and MCH exist in
# the dump (ids 1, 4, 3); NCD is added as program 100 so all four warehouse
# programmes resolve.
PROGRAM_MAP = {
    "HIV":  {"program_id": 1,   "name": "HIV",                   "concept_id": 1422},
    "TB":   {"program_id": 4,   "name": "TB",                    "concept_id": 1574},
    "MNCH": {"program_id": 3,   "name": "MCH - Mother Services", "concept_id": 1623},
    "NCD":  {"program_id": 100, "name": "NCD",                   "concept_id": 5497},
}

# Curated commodity subset the EMR actually prescribes. drug.name embeds the
# shared commodity_id so dbt can join drug_order -> fct_dispense -> commodities.
EMR_COMMODITIES = [
    "HIV-TLD", "HIV-PAED",
    "TB-RHZE", "TB-ISO",
    "MN-OXYTOCIN", "MN-MGSO4", "MN-IRON-FOL",
    "NCD-AMLOD", "NCD-METFORMIN", "NCD-INSULIN", "NCD-LOSARTAN",
    "MAL-AL",
    "FP-DEPO", "FP-IMPLANON",
]

# Numeric concepts present in the dump.
# Format: (concept_id, name, low_normal, high_normal)
CONCEPTS_NUMERIC = [
    (21,    "Haemoglobin",                 10.4,  17.8),
    (678,   "White blood cells",            4.0,  11.0),
    (679,   "Red blood cells",              4.0,   6.1),
    (729,   "Platelets",                  134.0, 419.0),
    (730,   "CD4%",                        30.0,  60.0),
    (856,   "HIV viral load",              20.0, 100000.0),
    (887,   "Serum glucose",               70.0, 110.0),
    (1006,  "Total cholesterol (mmol/L)",   2.5,   5.17),
    (1015,  "Hematocrit",                  32.3,  51.9),
    (855,   "Erythrocyte sedimentation rate", 0.0, 30.0),
]

# Ethiopian-style first and family names, sampled at random for synthetic patients.
FIRST_NAMES_M = ["Abebe", "Tesfaye", "Yohannes", "Daniel", "Kebede", "Solomon",
                 "Mekonnen", "Aman", "Bekele", "Getahun", "Habtamu", "Kassahun",
                 "Mulugeta", "Negussie", "Sintayehu", "Tewodros", "Yared",
                 "Bereket", "Endalkachew", "Worku"]
FIRST_NAMES_F = ["Almaz", "Tigist", "Hiwot", "Selam", "Mulu", "Bethel",
                 "Yodit", "Mahlet", "Rahel", "Aster", "Birtukan", "Etenesh",
                 "Genet", "Konjit", "Lemlem", "Meaza", "Sara", "Tsehay",
                 "Workitu", "Zewditu"]
FATHER_NAMES = ["Tadesse", "Bekele", "Alemu", "Wolde", "Kebede", "Mengistu",
                "Girma", "Haile", "Tesfaye", "Demissie", "Asfaw", "Lemma",
                "Negash", "Tilahun", "Yimer", "Aklilu", "Berhanu", "Desta"]


# SQL helpers
def q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "''")


def now_str(d: datetime | None = None) -> str:
    return (d or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def stable_uuid(*parts: str) -> str:
    """uuid5 against UUID_NS so repeated runs produce the same value."""
    return str(uuid.uuid5(UUID_NS, "|".join(parts)))


# Period window from shared periods.json
def load_period_window():
    """Return (period_start, period_end) datetimes."""
    periods = _load_json("periods.json")
    days = periods["days"]
    start = datetime.strptime(days[0]["day_id"], "%Y-%m-%d")
    end = datetime.strptime(days[-1]["day_id"], "%Y-%m-%d")
    return start, end


def rand_period_datetime(start: datetime, end: datetime) -> datetime:
    """Random datetime uniformly inside the shared periods window."""
    span = (end - start).days
    return start + timedelta(
        days=random.randint(0, span),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )


def rand_dob_for_age(min_age: int, max_age: int) -> date:
    days = random.randint(min_age * 365, max_age * 365)
    return (datetime.now() - timedelta(days=days)).date()


def rand_dob() -> date:
    # ages 0-90, weighted broadly across population pyramid
    return rand_dob_for_age(0, 90)


def age_in_years(dob: date) -> int:
    today = datetime.now().date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# Generators
def gen_address_hierarchy() -> list[str]:
    """Address-hierarchy levels: Country -> Region -> Zone -> Woreda -> Kebele."""
    sql = ["-- address_hierarchy_level (Ethiopian admin tiers)"]
    levels = [
        (1, "Country", None, "country"),
        (2, "Region",  1,    "state_province"),
        (3, "Zone",    2,    "county_district"),
        (4, "Woreda",  3,    "city_village"),
        (5, "Kebele",  4,    "address1"),
    ]
    for lid, name, parent, field in levels:
        parent_sql = "NULL" if parent is None else str(parent)
        sql.append(
            f"INSERT INTO address_hierarchy_level (address_hierarchy_level_id, name, "
            f"parent_level_id, address_field, uuid, required) "
            f"VALUES ({lid}, '{name}', {parent_sql}, '{field}', "
            f"'{stable_uuid('addr_level', name)}', 1);"
        )
    return sql


def gen_locations() -> tuple[list[str], list[dict]]:
    """Load HC/PH/GH/SH facilities from shared sample and emit `location` rows.

    Returns the SQL list plus a list of dicts {location_id, facility_id, mfr_id,
    region_id, woreda} for downstream generators to use.
    """
    sql = ["-- location (shared facilities.json: HC/PH/GH/SH only)"]
    facilities = _load_json("facilities.json")
    allowed = {"HC", "PH", "GH", "SH"}
    facilities = [f for f in facilities if f.get("tier_id") in allowed]
    if LOC_CAP and LOC_CAP < len(facilities):
        facilities = random.sample(facilities, LOC_CAP)
    rows: list[dict] = []
    for idx, fac in enumerate(facilities, start=1):
        name = q(fac["name"])[:255]
        tier_name = q(fac.get("tier_name") or fac["tier_id"])[:255]
        region_id = fac["region_id"]
        woreda = q(fac.get("woreda") or "")[:255]
        zone = q(fac.get("zone") or "")[:255]
        facility_id = fac["facility_id"]
        mfr_id = fac["mfr_id"]
        # Encode the shared keys in address1 so warehouse joins can resolve
        # location to shared facility without a sidecar lookup.
        addr1 = f"FAC_ID={facility_id};MFR_ID={mfr_id}"
        loc_uuid = stable_uuid("location", facility_id)
        sql.append(
            f"INSERT INTO location (location_id, name, description, country, "
            f"state_province, county_district, city_village, address1, "
            f"creator, date_created, uuid) "
            f"VALUES ({idx}, '{name}', '{tier_name}', 'Ethiopia', "
            f"'{region_id}', '{zone}', '{woreda}', '{addr1}', "
            f"{ADMIN_USER_ID}, '{now_str()}', '{loc_uuid}');"
        )
        rows.append({
            "location_id": idx,
            "facility_id": facility_id,
            "mfr_id": mfr_id,
            "region_id": region_id,
            "woreda": fac.get("woreda") or "",
            "zone": fac.get("zone") or "",
        })
    return sql, rows


def gen_programs() -> list[str]:
    """Rewrite program.description so it carries the shared programme_id.

    Also inserts an NCD program row (id 100), which the dump does not have.
    Encoding `[programme_id=HIV]` in the description lets dbt resolve the
    OpenMRS program_id -> shared programme_id via a stable substring contract.
    """
    sql = ["-- program (shared programme_id smuggled into description)"]
    for pid_short, p in PROGRAM_MAP.items():
        desc = f"[programme_id={pid_short}] {p['name']} programme (NHDW)"
        # Idempotent: try INSERT, fall back to UPDATE on duplicate key.
        sql.append(
            f"INSERT INTO program (program_id, concept_id, name, description, "
            f"creator, date_created, retired, uuid) "
            f"VALUES ({p['program_id']}, {p['concept_id']}, '{q(p['name'])}', "
            f"'{q(desc)}', {ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('program', pid_short)}') "
            f"ON DUPLICATE KEY UPDATE description='{q(desc)}', name='{q(p['name'])}';"
        )
    return sql


def gen_drugs() -> tuple[list[str], dict[str, int]]:
    """Insert one `drug` row per curated commodity.

    drug.name holds the shared commodity_id (e.g. 'HIV-TLD'); drug.strength
    holds the human-readable commodity name. Returns SQL plus a map
    commodity_id -> drug_id for the order generator.
    """
    sql = ["-- drug (curated commodity subset; drug.name == shared commodity_id)"]
    commodities = {c["id"]: c for c in _load_json("commodities.json")}
    drug_map: dict[str, int] = {}
    for idx, cid in enumerate(EMR_COMMODITIES, start=1):
        c = commodities.get(cid)
        if not c:
            print(f"WARN: commodity {cid} not in shared sample, skipping",
                  file=sys.stderr)
            continue
        drug_id = idx
        # OpenMRS `drug` schema: name, strength, retired, uuid and a mandatory
        # concept_id (0 here; these rows are not joined through concepts).
        # The shared commodity_id goes in `name` for warehouse joins; the
        # human-readable name goes in `strength`, the closest free-text column.
        sql.append(
            f"INSERT INTO drug (drug_id, concept_id, name, strength, "
            f"combination, retired, creator, date_created, uuid) "
            f"VALUES ({drug_id}, 0, '{q(cid)}', '{q(c['name'])}', 0, 0, "
            f"{ADMIN_USER_ID}, '{now_str()}', "
            f"'{stable_uuid('drug', cid)}');"
        )
        drug_map[cid] = drug_id
    return sql, drug_map


def gen_providers_and_persons(start_person_id: int) -> tuple[list[str], list[tuple[int, int]]]:
    """Provider personas with their backing person + person_name rows."""
    sql = ["-- providers + their persons + names"]
    providers: list[tuple[int, int]] = []  # (provider_id, person_id)
    pid = start_person_id
    for prov_idx in range(1, PROVIDER_COUNT + 1):
        gender = random.choice(["M", "F"])
        first = random.choice(FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F)
        father = random.choice(FATHER_NAMES)
        grandfather = random.choice(FATHER_NAMES)
        dob = rand_dob_for_age(25, 65)
        sql.append(
            f"INSERT INTO person (person_id, gender, birthdate, birthdate_estimated, dead, "
            f"creator, date_created, voided, uuid, deathdate_estimated) "
            f"VALUES ({pid}, '{gender}', '{dob.isoformat()}', 0, 0, "
            f"{ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('provider_person', str(prov_idx))}', 0);"
        )
        sql.append(
            f"INSERT INTO person_name (preferred, person_id, given_name, middle_name, family_name, "
            f"creator, date_created, voided, uuid) "
            f"VALUES (1, {pid}, '{first}', '{father}', '{grandfather}', "
            f"{ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('provider_name', str(prov_idx))}');"
        )
        sql.append(
            f"INSERT INTO provider (provider_id, person_id, identifier, creator, date_created, "
            f"retired, uuid) "
            f"VALUES ({prov_idx}, {pid}, 'PROV-{prov_idx:04d}', "
            f"{ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('provider', str(prov_idx))}');"
        )
        providers.append((prov_idx, pid))
        pid += 1
    return sql, providers


def gen_patients(start_person_id: int, locations: list[dict]) -> tuple[list[str], list[dict], int]:
    """Synthetic patients: person, patient, person_name, person_address, identifier.

    Returns (sql, patient_dicts, next_person_id). Each patient dict carries
    person_id, gender, dob, age, location_id so downstream generators can apply
    cohort rules (NCD ≥35, MNCH women 15-49, etc.).
    """
    sql = ["-- patients (person + patient + person_name + person_address + identifier)"]
    patients: list[dict] = []
    pid = start_person_id
    for patient_idx in range(1, PATIENT_COUNT + 1):
        gender = random.choices(["M", "F"], weights=[48, 52])[0]
        first = random.choice(FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F)
        father = random.choice(FATHER_NAMES)
        grandfather = random.choice(FATHER_NAMES)
        dob = rand_dob()
        loc = random.choice(locations)
        loc_id = loc["location_id"]
        # person
        sql.append(
            f"INSERT INTO person (person_id, gender, birthdate, birthdate_estimated, dead, "
            f"creator, date_created, voided, uuid, deathdate_estimated) "
            f"VALUES ({pid}, '{gender}', '{dob.isoformat()}', 0, 0, "
            f"{ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('patient_person', str(patient_idx))}', 0);"
        )
        # name
        sql.append(
            f"INSERT INTO person_name (preferred, person_id, given_name, middle_name, family_name, "
            f"creator, date_created, voided, uuid) "
            f"VALUES (1, {pid}, '{first}', '{father}', '{grandfather}', "
            f"{ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('patient_name', str(patient_idx))}');"
        )
        # address: tied to the patient's home facility so geographic joins hold
        sql.append(
            f"INSERT INTO person_address (person_id, preferred, country, state_province, "
            f"county_district, city_village, address1, creator, date_created, voided, uuid) "
            f"VALUES ({pid}, 1, 'Ethiopia', '{q(loc['region_id'])}', "
            f"'{q(loc['zone'])}', '{q(loc['woreda'])}', "
            f"'Kebele {random.randint(1, 10)}', "
            f"{ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('patient_addr', str(patient_idx))}');"
        )
        # patient
        sql.append(
            f"INSERT INTO patient (patient_id, creator, date_created, voided, allergy_status) "
            f"VALUES ({pid}, {ADMIN_USER_ID}, '{now_str()}', 0, 'Unknown');"
        )
        mrn = f"MRN{patient_idx:08d}"
        sql.append(
            f"INSERT INTO patient_identifier (patient_id, identifier, identifier_type, "
            f"preferred, location_id, creator, date_created, voided, uuid) "
            f"VALUES ({pid}, '{mrn}', {PATIENT_IDENTIFIER_TYPE_MRN}, 1, {loc_id}, "
            f"{ADMIN_USER_ID}, '{now_str()}', 0, "
            f"'{stable_uuid('patient_mrn', str(patient_idx))}');"
        )
        if random.random() < 0.6:
            natid = f"ET{random.randint(10**11, 10**12 - 1)}"
            sql.append(
                f"INSERT INTO patient_identifier (patient_id, identifier, identifier_type, "
                f"preferred, location_id, creator, date_created, voided, uuid) "
                f"VALUES ({pid}, '{natid}', {PATIENT_IDENTIFIER_TYPE_NATID}, 0, {loc_id}, "
                f"{ADMIN_USER_ID}, '{now_str()}', 0, "
                f"'{stable_uuid('patient_natid', str(patient_idx))}');"
            )
        patients.append({
            "person_id": pid,
            "patient_idx": patient_idx,
            "gender": gender,
            "dob": dob,
            "age": age_in_years(dob),
            "location_id": loc_id,
        })
        pid += 1
    return sql, patients, pid


def gen_clinical_history(
    patients: list[dict],
    provider_ids: list[tuple[int, int]],
    locations: list[dict],
    drug_map: dict[str, int],
    period_start: datetime,
    period_end: datetime,
) -> list[str]:
    """Visits -> encounters -> obs -> drug orders. Bulk of the data."""
    sql = ["-- visits, encounters, encounter_provider, obs, orders, drug_order"]
    enc_provider_sql: list[str] = ["-- encounter_provider"]
    obs_sql: list[str] = ["-- obs"]
    orders_sql: list[str] = ["-- orders + drug_order"]

    visit_id_seq = 1
    enc_id_seq = 1
    obs_id_seq = 1
    order_id_seq = 1

    # Encounter type to the programmes whose commodities it typically
    # prescribes. Used to wire drug_order to the right commodity bucket.
    ENC_DRUG_PROGRAMMES = {
        ENCOUNTER_TYPES["ART Refill"]:               ["HIV"],
        ENCOUNTER_TYPES["Consultation"]:             ["NCD", "MALARIA", "TB"],
        ENCOUNTER_TYPES["ANC Mother Enrollment"]:    ["MNCH", "FP"],
    }
    commodities_by_programme: dict[str, list[str]] = {}
    for cid in EMR_COMMODITIES:
        prog = cid.split("-")[0]
        # Normalise programme prefixes used in commodity ids to programme_id.
        prog = {"MN": "MNCH", "MAL": "MALARIA"}.get(prog, prog)
        commodities_by_programme.setdefault(prog, []).append(cid)

    for pt in patients:
        patient_id = pt["person_id"]
        num_visits = random.randint(1, 8)
        for _ in range(num_visits):
            visit_dt = rand_period_datetime(period_start, period_end)
            visit_type = random.choices(
                list(VISIT_TYPES.values()), weights=[85, 15]
            )[0]
            loc_id = pt["location_id"]
            stop_dt = visit_dt + timedelta(hours=random.randint(1, 72))
            # Cap stop_dt at period_end to keep everything inside the window.
            if stop_dt > period_end:
                stop_dt = period_end
            sql.append(
                f"INSERT INTO visit (visit_id, patient_id, visit_type_id, date_started, "
                f"date_stopped, location_id, creator, date_created, voided, uuid) "
                f"VALUES ({visit_id_seq}, {patient_id}, {visit_type}, '{now_str(visit_dt)}', "
                f"'{now_str(stop_dt)}', {loc_id}, {ADMIN_USER_ID}, '{now_str()}', 0, "
                f"'{stable_uuid('visit', str(visit_id_seq))}');"
            )
            for _ in range(random.randint(1, 3)):
                enc_dt = visit_dt + timedelta(minutes=random.randint(0, 240))
                if enc_dt > period_end:
                    enc_dt = period_end
                enc_type = random.choice(list(ENCOUNTER_TYPES.values()))
                sql.append(
                    f"INSERT INTO encounter (encounter_id, encounter_type, patient_id, "
                    f"location_id, encounter_datetime, creator, date_created, voided, "
                    f"visit_id, uuid) "
                    f"VALUES ({enc_id_seq}, {enc_type}, {patient_id}, {loc_id}, "
                    f"'{now_str(enc_dt)}', {ADMIN_USER_ID}, '{now_str()}', 0, "
                    f"{visit_id_seq}, '{stable_uuid('encounter', str(enc_id_seq))}');"
                )
                prov_id, _ = random.choice(provider_ids)
                enc_provider_sql.append(
                    f"INSERT INTO encounter_provider (encounter_id, provider_id, "
                    f"encounter_role_id, creator, date_created, voided, uuid) "
                    f"VALUES ({enc_id_seq}, {prov_id}, {ENCOUNTER_ROLE_CLINICIAN}, "
                    f"{ADMIN_USER_ID}, '{now_str()}', 0, "
                    f"'{stable_uuid('enc_prov', str(enc_id_seq))}');"
                )
                # 3-12 obs per encounter
                num_obs = random.randint(3, 12)
                obs_concepts = random.sample(CONCEPTS_NUMERIC, k=min(num_obs, len(CONCEPTS_NUMERIC)))
                for concept_id, name, low, high in obs_concepts:
                    if random.random() < 0.8:
                        value = round(random.uniform(low, high), 2)
                    else:
                        spread = (high - low) * 0.3
                        value = round(random.choice([low - spread, high + spread]), 2)
                    obs_sql.append(
                        f"INSERT INTO obs (obs_id, person_id, concept_id, encounter_id, "
                        f"obs_datetime, location_id, value_numeric, creator, date_created, "
                        f"voided, uuid, status) "
                        f"VALUES ({obs_id_seq}, {patient_id}, {concept_id}, {enc_id_seq}, "
                        f"'{now_str(enc_dt)}', {loc_id}, {value}, {ADMIN_USER_ID}, "
                        f"'{now_str()}', 0, '{stable_uuid('obs', str(obs_id_seq))}', 'FINAL');"
                    )
                    obs_id_seq += 1

                # Drug order, when the encounter type implies a prescription
                progs = ENC_DRUG_PROGRAMMES.get(enc_type)
                if progs and random.random() < 0.7:
                    candidates: list[str] = []
                    for p in progs:
                        candidates.extend(commodities_by_programme.get(p, []))
                    if candidates:
                        commodity_id = random.choice(candidates)
                        drug_id = drug_map.get(commodity_id)
                        if drug_id is not None:
                            ord_uuid = stable_uuid("order", str(order_id_seq))
                            # orders supertype: order_type_id=2 is drug order in default OpenMRS.
                            # OpenMRS 2.x adds NOT NULL columns without defaults:
                            #   - order_number (varchar 50): we mint ORD-XXXXXXX
                            #   - order_action  (varchar 50): 'NEW' | 'REVISE' | 'DISCONTINUE'
                            #   - care_setting  (FK to care_setting): 1=Outpatient, 2=Inpatient
                            order_number = f"ORD-{order_id_seq:08d}"
                            care_setting_id = 1   # outpatient default; matches visit_type bias
                            orders_sql.append(
                                f"INSERT INTO orders (order_id, order_type_id, concept_id, "
                                f"orderer, encounter_id, patient_id, date_activated, "
                                f"date_created, creator, voided, uuid, order_number, "
                                f"order_action, care_setting) "
                                f"VALUES ({order_id_seq}, 2, {obs_concepts[0][0]}, "
                                f"{prov_id}, {enc_id_seq}, {patient_id}, '{now_str(enc_dt)}', "
                                f"'{now_str()}', {ADMIN_USER_ID}, 0, '{ord_uuid}', "
                                f"'{order_number}', 'NEW', {care_setting_id});"
                            )
                            orders_sql.append(
                                f"INSERT INTO drug_order (order_id, drug_inventory_id, "
                                f"dose, quantity) "
                                f"VALUES ({order_id_seq}, {drug_id}, 1, 30);"
                            )
                            order_id_seq += 1

                enc_id_seq += 1
            visit_id_seq += 1

    return sql + enc_provider_sql + obs_sql + orders_sql


def gen_patient_programs(patients: list[dict],
                         period_start: datetime,
                         period_end: datetime) -> list[str]:
    """Enroll patients into HIV / TB / MNCH / NCD per cohort rules.

    Rates:
      HIV  12% all patients
      TB    8% all patients
      MNCH 22% of women 15-49
      NCD  14% of patients ≥ 35
    """
    sql = ["-- patient_program (HIV/TB/MNCH/NCD per cohort rules)"]
    prog_id_seq = 1
    rules = [
        ("HIV",  lambda p: True,                         0.12),
        ("TB",   lambda p: True,                         0.08),
        ("MNCH", lambda p: p["gender"] == "F" and 15 <= p["age"] <= 49, 0.22),
        ("NCD",  lambda p: p["age"] >= 35,               0.14),
    ]
    for pt in patients:
        for prog_short, eligible, rate in rules:
            if not eligible(pt):
                continue
            if random.random() >= rate:
                continue
            prog = PROGRAM_MAP[prog_short]
            start = rand_period_datetime(period_start, period_end)
            still_active = random.random() < 0.7
            if still_active:
                end_sql = "NULL"
            else:
                end_dt = start + timedelta(days=random.randint(30, 720))
                if end_dt > period_end:
                    end_dt = period_end
                end_sql = f"'{now_str(end_dt)}'"
            sql.append(
                f"INSERT INTO patient_program (patient_program_id, patient_id, program_id, "
                f"date_enrolled, date_completed, location_id, creator, date_created, "
                f"voided, uuid) "
                f"VALUES ({prog_id_seq}, {pt['person_id']}, {prog['program_id']}, "
                f"'{now_str(start)}', {end_sql}, {pt['location_id']}, "
                f"{ADMIN_USER_ID}, '{now_str()}', 0, "
                f"'{stable_uuid('patient_program', str(prog_id_seq))}');"
            )
            prog_id_seq += 1
    return sql


def gen_conditions(patients: list[dict], period_start: datetime, period_end: datetime) -> list[str]:
    """One problem-list entry per NCD-age (≥35) patient at 20% rate.

    Concept ids: 117399 = Hypertension (CIEL), 119481 = Diabetes mellitus.
    """
    sql = ["-- conditions (problem list for NCD-aged patients)"]
    cond_seq = 1
    for pt in patients:
        if pt["age"] < 35 or random.random() >= 0.20:
            continue
        concept_id = random.choice([117399, 119481])
        onset = rand_period_datetime(period_start, period_end)
        sql.append(
            f"INSERT INTO conditions (condition_id, patient_id, condition_coded, "
            f"clinical_status, verification_status, onset_date, date_created, "
            f"creator, voided, uuid) "
            f"VALUES ({cond_seq}, {pt['person_id']}, {concept_id}, "
            f"'ACTIVE', 'CONFIRMED', '{now_str(onset)}', '{now_str()}', "
            f"{ADMIN_USER_ID}, 0, '{stable_uuid('condition', str(cond_seq))}');"
        )
        cond_seq += 1
    return sql


def gen_allergies(patients: list[dict]) -> list[str]:
    """Flip patient.allergy_status for a 6% subset; no detailed allergy rows."""
    sql = ["-- allergy_status flip for 6% of patients (no detail allergy rows)"]
    for pt in patients:
        if random.random() < 0.06:
            sql.append(
                f"UPDATE patient SET allergy_status='See List' "
                f"WHERE patient_id={pt['person_id']};"
            )
    return sql


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
    print(f"==> Seed = {SEED} | patients = {PATIENT_COUNT} | history = {YEARS_HISTORY}y")

    period_start, period_end = load_period_window()
    print(f"==> Period window: {period_start.date()} -> {period_end.date()}")

    out: list[str] = [
        # Marker table DDL first: MySQL DDL implicitly commits, so it must not
        # sit inside the data transaction below. The DELETE and the final
        # INSERT stamp are inside the transaction, so a failed run rolls back
        # to the previous consistent data and marker pair.
        "CREATE TABLE IF NOT EXISTS synth_meta "
        "(synth_version INT NOT NULL, stamped_at DATETIME NOT NULL, profile VARCHAR(32) NULL);",
        # adds the profile column to marker tables created without it
        "ALTER TABLE synth_meta ADD COLUMN IF NOT EXISTS profile VARCHAR(32) NULL;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "SET UNIQUE_CHECKS = 0;",
        "SET autocommit = 0;",
        "DELETE FROM synth_meta;",
        # Wipe previously synthesized rows. Leaves admin (person_id=1), the
        # concept dictionary, liquibasechangelog and OpenMRS module
        # infrastructure tables untouched. Order matters for FKs even with
        # checks off.
        "DELETE FROM drug_order;",
        "DELETE FROM orders WHERE order_id > 0;",
        "DELETE FROM drug;",
        "DELETE FROM conditions;",
        "DELETE FROM obs WHERE creator != 1 OR encounter_id IS NOT NULL;",
        "DELETE FROM encounter_provider;",
        "DELETE FROM encounter;",
        "DELETE FROM visit;",
        "DELETE FROM patient_program;",
        "DELETE FROM patient_identifier;",
        "DELETE FROM patient WHERE patient_id > 1;",
        "DELETE FROM person_address WHERE person_id > 1;",
        "DELETE FROM person_name   WHERE person_id > 1;",
        "DELETE FROM provider;",
        "DELETE FROM person WHERE person_id > 1;",
        "DELETE FROM location;",
        "DELETE FROM address_hierarchy_entry;",
        "DELETE FROM address_hierarchy_level;",
    ]

    print("==> Address hierarchy levels...")
    out.extend(gen_address_hierarchy())

    print("==> Locations (HC/PH/GH/SH from shared facilities.json)...")
    loc_sql, locations = gen_locations()
    print(f"    {len(locations)} facilities materialised as OpenMRS locations")
    out.extend(loc_sql)

    print("==> Programs (HIV/TB/MNCH/NCD with shared programme_id smuggled into description)...")
    out.extend(gen_programs())

    print(f"==> Drugs ({len(EMR_COMMODITIES)} curated commodities)...")
    drug_sql, drug_map = gen_drugs()
    out.extend(drug_sql)

    print(f"==> {PROVIDER_COUNT} providers...")
    prov_sql, providers = gen_providers_and_persons(start_person_id=2)
    out.extend(prov_sql)

    next_pid = 2 + PROVIDER_COUNT
    print(f"==> {PATIENT_COUNT} patients...")
    pat_sql, patients, _ = gen_patients(next_pid, locations)
    out.extend(pat_sql)

    print("==> Clinical history (visits, encounters, obs, drug orders)...")
    out.extend(gen_clinical_history(patients, providers, locations, drug_map,
                                    period_start, period_end))

    print("==> patient_program enrollments...")
    out.extend(gen_patient_programs(patients, period_start, period_end))

    print("==> conditions (NCD problem list)...")
    out.extend(gen_conditions(patients, period_start, period_end))

    print("==> allergy_status flips...")
    out.extend(gen_allergies(patients))

    out.extend([
        f"INSERT INTO synth_meta (synth_version, stamped_at, profile) "
        f"VALUES ({SYNTH_VERSION}, NOW(), '{_synth_profile()}');",
        "COMMIT;",
        "SET FOREIGN_KEY_CHECKS = 1;",
        "SET UNIQUE_CHECKS = 1;",
    ])

    sql_file = Path("/tmp/ethioemr_seed.sql")
    sql_file.write_text("\n".join(out), encoding="utf-8")
    print(f"==> Wrote {sql_file.stat().st_size / 1024 / 1024:.1f} MB SQL to {sql_file}")

    print("==> Copying into container...")
    subprocess.run(["docker", "cp", str(sql_file), f"{CONTAINER}:/tmp/seed.sql"], check=True)

    print("==> Loading...")
    subprocess.run(
        ["docker", "exec", "-i", CONTAINER,
         "bash", "-c",
         f"mysql -uroot -p{ROOT_PW} {DB} < /tmp/seed.sql"],
        check=True,
    )

    print("==> Row counts:")
    subprocess.run([
        "docker", "exec", "-i", CONTAINER,
        "mysql", "-uroot", f"-p{ROOT_PW}", DB, "-e",
        """
SELECT 'location'           AS t, COUNT(*) AS n FROM location
UNION ALL SELECT 'program',            COUNT(*) FROM program
UNION ALL SELECT 'drug',               COUNT(*) FROM drug
UNION ALL SELECT 'provider',           COUNT(*) FROM provider
UNION ALL SELECT 'person',             COUNT(*) FROM person
UNION ALL SELECT 'person_name',        COUNT(*) FROM person_name
UNION ALL SELECT 'person_address',     COUNT(*) FROM person_address
UNION ALL SELECT 'patient',            COUNT(*) FROM patient
UNION ALL SELECT 'patient_identifier', COUNT(*) FROM patient_identifier
UNION ALL SELECT 'visit',              COUNT(*) FROM visit
UNION ALL SELECT 'encounter',          COUNT(*) FROM encounter
UNION ALL SELECT 'encounter_provider', COUNT(*) FROM encounter_provider
UNION ALL SELECT 'obs',                COUNT(*) FROM obs
UNION ALL SELECT 'orders',             COUNT(*) FROM orders
UNION ALL SELECT 'drug_order',         COUNT(*) FROM drug_order
UNION ALL SELECT 'patient_program',    COUNT(*) FROM patient_program
UNION ALL SELECT 'conditions',         COUNT(*) FROM conditions;
""",
    ], check=True)

    print()
    print("OK: EthioEMR clinical synthesis complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
