#!/usr/bin/env python3
"""
MFR seed loader: posts the demonstration facility registry to the HAPI FHIR R4
server, one Organization resource per country, region, zone, woreda and
facility in the shared sample
(bundle/seeds/entity-model/_build/{regions,zones,woredas,facilities}).
The facilities are a sample of the public facility list plus generated
top-ups; staffing figures are derived deterministically (see _staff_for).

Real MFR has a top-down hierarchy:
    country  (Ethiopia)
        └─ region            partOf country
              └─ zone        partOf region
                    └─ woreda    partOf zone (or region for chartered cities)
                          └─ facility  partOf woreda

We mirror that with five tiers of Organization resources. Cross-references use
the FHIR `partOf` reference. All resources carry stable identifiers so the
dlt-MFR ingestor can be re-run idempotently against the same dataset.

POSTs are batched into FHIR transaction bundles of 100 entries so seeding
finishes in seconds rather than minutes. Idempotent: re-running uses
conditional create (`ifNoneExist`) so duplicate seed runs are safe.
"""
from __future__ import annotations
import hashlib
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path


# Per-facility cadre staffing (physician / nurse / HEW), generated
# deterministically from facility_id and tier so the region roll-up is stable.
# Base total scales with tier; the cadre mix follows the Ethiopian pattern
# (HP = HEW-heavy, HC/PHC = nurse-led, hospitals = physician and nurse). There
# is no iHRIS source, so staffing is a facility-registry attribute (cadres.json
# holds the WHO ratios).
_TIER_BASE = {"HP": 3, "HC": 18, "PHC": 28, "MC": 22, "PH": 70, "GH": 140, "SH": 260, "DS": 40}

def _staff_for(fid: str, tier_id: str) -> tuple[int, int, int]:
    h = int(hashlib.md5(fid.encode()).hexdigest()[:6], 16)
    jitter = 0.8 + (h % 41) / 100.0                         # 0.80 .. 1.20
    total = max(1, round(_TIER_BASE.get(tier_id, 12) * jitter))
    if tier_id == "HP":
        hew = max(1, round(total * 0.9)); nurse = max(0, total - hew); phys = 0
    elif tier_id in ("HC", "PHC", "MC"):
        nurse = round(total * 0.60); hew = round(total * 0.30); phys = max(0, total - nurse - hew)
    else:                                                    # hospitals
        phys = round(total * 0.25); nurse = round(total * 0.55); hew = max(0, total - phys - nurse)
    return phys, nurse, hew

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


ROOT  = Path(__file__).resolve().parents[1]

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

BUILD = _entity_build_dir(REPO_ROOT / "bundle" / "seeds" / "entity-model")
BASE  = os.environ.get("MFR_FHIR_BASE", "http://127.0.0.1:8093/fhir")
BATCH = 100

# Identifier system URIs (one per ID space)
SYS_FAC    = "https://mfr.moh.gov.et/facility-id"
SYS_WOREDA = "https://mfr.moh.gov.et/woreda-id"
SYS_ZONE   = "https://mfr.moh.gov.et/zone-id"
SYS_REGION = "https://mfr.moh.gov.et/region-id"
SYS_COUNTRY= "https://mfr.moh.gov.et/country-id"

# Organization-type codes (FHIR ValueSet, extended with the hierarchy levels)
TYPE_CODE = {
    "country": ("country",  "Country"),
    "region":  ("region",   "Region"),
    "zone":    ("zone",     "Zone"),
    "woreda":  ("woreda",   "Woreda"),
}


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = BASE.rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/fhir+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")
    except Exception as e:
        print(f"  ERROR {method} {url}: {e}", file=sys.stderr)
        return 0, {}


def org(rid_value: str, system: str, name: str,
        type_code: str | None = None, type_display: str | None = None,
        part_of_ref: str | None = None,
        address_woreda: str | None = None, address_zone: str | None = None,
        address_region: str | None = None, alias: list[str] | None = None) -> dict:
    """Build a single Organization resource."""
    res: dict = {
        "resourceType": "Organization",
        "active": True,
        "name": name,
        "identifier": [{"system": system, "value": rid_value, "use": "official"}],
    }
    if type_code:
        res["type"] = [{
            "coding": [{
                "system": "https://mfr.moh.gov.et/fhir/CodeSystem/org-level",
                "code": type_code,
                "display": type_display or type_code.capitalize(),
            }],
            "text": type_display or type_code.capitalize(),
        }]
    if part_of_ref:
        # Conditional reference: HAPI resolves to the existing Organization
        # whose identifier matches. Works across separate transaction bundles
        # without needing to track server-assigned IDs.
        res["partOf"] = {"reference": part_of_ref}
    addr: dict = {"country": "Ethiopia"}
    if address_woreda: addr["district"] = address_woreda
    if address_zone:   addr["city"] = address_zone
    if address_region: addr["state"] = address_region
    if len(addr) > 1:
        res["address"] = [addr]
    if alias:
        res["alias"] = alias
    return res


def entry(resource: dict, rid_value: str, system: str, full_url_id: str) -> dict:
    """Wrap a resource in a transaction-bundle entry that uses conditional
    create so the seed is idempotent (re-running won't dupe rows)."""
    return {
        "fullUrl": f"urn:uuid:{full_url_id}",
        "resource": resource,
        "request": {
            "method": "POST",
            "url": "Organization",
            "ifNoneExist": f"identifier={system}|{rid_value}",
        },
    }


def bundle_post(entries: list[dict]) -> tuple[int, int]:
    """POST one transaction-bundle. Returns (created, skipped)."""
    body = {"resourceType": "Bundle", "type": "transaction", "entry": entries}
    status, resp = http("POST", "", body)
    if status >= 400:
        print(f"  HTTP {status}: {json.dumps(resp)[:400]}", file=sys.stderr)
        return 0, 0
    created = skipped = 0
    for r in resp.get("entry", []):
        loc = (r.get("response") or {}).get("status", "")
        if loc.startswith("201"): created += 1
        elif loc.startswith("200"): skipped += 1
    return created, skipped


def post_in_batches(entries: list[dict], label: str) -> None:
    total_c = total_s = 0
    n = len(entries)
    for i in range(0, n, BATCH):
        chunk = entries[i:i + BATCH]
        c, s = bundle_post(chunk)
        total_c += c; total_s += s
        sys.stdout.write(f"\r  {label}: {i + len(chunk)}/{n}  · {total_c} created · {total_s} skipped")
        sys.stdout.flush()
    print()


def main() -> None:
    if not BUILD.exists():
        sys.exit(f"missing shared build · run build_sample.py first ({BUILD})")
    regions    = json.loads((BUILD / "regions.json").read_text())
    zones      = json.loads((BUILD / "zones.json").read_text())
    woredas    = json.loads((BUILD / "woredas.json").read_text())
    facilities = json.loads((BUILD / "facilities.json").read_text())

    # Helper · build a conditional reference HAPI can resolve by identifier
    def ref(system: str, value: str) -> str:
        return f"Organization?identifier={system}|{value}"

    # Country root
    country_uuid = "country-ethiopia"
    country = org("ET", SYS_COUNTRY, "Federal Democratic Republic of Ethiopia",
                  type_code="country", type_display="Country",
                  alias=["Ethiopia"])
    print("seeding country root")
    bundle_post([entry(country, "ET", SYS_COUNTRY, country_uuid)])

    # Regions
    region_entries: list[dict] = []
    for r in regions:
        rid = r["id"]
        region_entries.append(entry(
            org(rid, SYS_REGION, r["name"],
                type_code="region", type_display="Region",
                part_of_ref=ref(SYS_COUNTRY, "ET"),
                address_region=r["name"]),
            rid, SYS_REGION, f"reg-{rid}"))
    print(f"seeding regions ({len(region_entries)})")
    post_in_batches(region_entries, "regions")

    # Zones
    zone_entries: list[dict] = []
    for z in zones:
        rid = z["region_id"]; zid = z["zone_id"]
        zone_entries.append(entry(
            org(zid, SYS_ZONE, z["name"],
                type_code="zone", type_display="Zone",
                part_of_ref=ref(SYS_REGION, rid),
                address_region=next((r["name"] for r in regions if r["id"] == rid), None),
                address_zone=z["name"]),
            zid, SYS_ZONE, f"zon-{zid}"))
    print(f"seeding zones ({len(zone_entries)})")
    post_in_batches(zone_entries, "zones")

    # Woredas
    woreda_entries: list[dict] = []
    # zone lookup keyed by (region_id, zone_name) since woredas only carry zone NAME
    zone_by_name: dict[tuple[str, str], str] = {(z["region_id"], z["name"]): z["zone_id"] for z in zones}
    for w in woredas:
        rid = w["region_id"]; wid = w["woreda_id"]
        zid = zone_by_name.get((rid, w.get("zone") or ""))
        parent_ref = ref(SYS_ZONE, zid) if zid else ref(SYS_REGION, rid)
        woreda_entries.append(entry(
            org(wid, SYS_WOREDA, w["name"],
                type_code="woreda", type_display="Woreda",
                part_of_ref=parent_ref,
                address_region=next((r["name"] for r in regions if r["id"] == rid), None),
                address_zone=w.get("zone"),
                address_woreda=w["name"]),
            wid, SYS_WOREDA, f"wor-{wid}"))
    print(f"seeding woredas ({len(woreda_entries)})")
    post_in_batches(woreda_entries, "woredas")

    # Facilities
    fac_entries: list[dict] = []
    woreda_by_pair: dict[tuple[str, str], str] = {(w["region_id"], w["name"]): w["woreda_id"] for w in woredas}
    for f in facilities:
        rid = f["region_id"]
        wid = woreda_by_pair.get((rid, f.get("woreda") or ""))
        parent_ref = ref(SYS_WOREDA, wid) if wid else ref(SYS_REGION, rid)
        fid = f["facility_id"]
        type_text = f["tier_name"]
        res = org(fid, SYS_FAC, f["name"],
                  type_code=f["tier_id"].lower(), type_display=type_text,
                  part_of_ref=parent_ref,
                  address_region=next((r["name"] for r in regions if r["id"] == rid), None),
                  address_zone=f.get("zone"),
                  address_woreda=f.get("woreda"))
        # Add the MFR upstream ID as a second identifier if available
        if f.get("mfr_id"):
            res["identifier"].append({
                "system": "https://mfr.moh.gov.et/upstream-id",
                "value":  str(f["mfr_id"]),
                "use":    "secondary",
            })
        # Add the ownership-type extension + a per-facility staffing extension
        # (cadre counts, deterministic per facility_id and tier; the registry
        # is where HRH figures live in Ethiopia, and there is no iHRIS source).
        phys, nurse, hew = _staff_for(fid, f["tier_id"])
        res["extension"] = [
            {
                "url": "https://mfr.moh.gov.et/fhir/StructureDefinition/ownership",
                "valueCode": f["ownership"],
            },
            {
                "url": "https://mfr.moh.gov.et/fhir/StructureDefinition/staffing",
                "extension": [
                    {"url": "physician", "valueInteger": phys},
                    {"url": "nurse", "valueInteger": nurse},
                    {"url": "hew", "valueInteger": hew},
                ],
            },
        ]
        fac_entries.append(entry(res, fid, SYS_FAC, f"fac-{fid}"))
    print(f"seeding facilities ({len(fac_entries)})")
    post_in_batches(fac_entries, "facilities")

    # Done. Quick sanity counts
    status, resp = http("GET", "Organization?_summary=count")
    if status == 200:
        print(f"\nMFR now holds {resp.get('total')} Organization resources")
    else:
        print(f"\nmetadata check returned HTTP {status}")


if __name__ == "__main__":
    main()
