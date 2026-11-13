"""MFR FHIR extraction: Ethiopian facilities (`Location`) and owners (`Organization`).

Why FHIR `/Location` and not the legacy REST facility dump: the legacy REST
endpoint returns ~11 flat fields (id, name, status, operationalStatus,
facilityType, parentFacilityType, ownership, region, zone, woreda). The FHIR
`Location` resource is a superset: it adds GPS coordinates, telecom contacts,
the full structured address, and a reference to the managing Organization
(ownership hierarchy). The high-value fields are flattened into columns and
the complete resource in `raw_resource`, so nothing the source returned is lost.

Why full-refresh (`replace`): a facility registry is slow-moving dimension data,
like DHIS2 metadata. A full pull each run removes a class of "stale
dimension" bugs and is cheap. Set MFR_FHIR_SINCE to switch to an incremental
`_lastUpdated` floor if needed.

Tables produced (note: the FHIR resource is `Location`, but the lake object is
named `facilities`, as these are Ethiopian health facilities):
    s3://nhdw-raw/mfr/facilities/year=YYYY/month=MM/day=DD/...
    s3://nhdw-raw/mfr/organizations/year=YYYY/month=MM/day=DD/...
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterator

import dlt

from ..config import Config
from ..destination import build_destination
from ..manifest import build_manifest, measure_partition, write_manifest
from ..lineage import emit_source_lineage
from ._client import MFRFHIRClient

log = logging.getLogger(__name__)


def _json(value) -> str | None:
    """Serialize a nested FHIR object/array to a JSON string.

    Keeps complex fields (identifier, telecom, address, the whole resource) as a
    single JSON column instead of letting dlt explode them into child tables.
    One flat `facilities` table with nothing lost; downstream dbt parses the JSON.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _first_coding(concept: dict | None) -> dict:
    """First coding of a CodeableConcept (or {} ). FHIR uses arrays everywhere."""
    if not concept:
        return {}
    codings = concept.get("coding") or []
    return codings[0] if codings else {}


def _flatten_address(address: dict | None) -> str | None:
    """FHIR Address -> single text line. Prefer `.text`, else join the parts."""
    if not address:
        return None
    if address.get("text"):
        return address["text"]
    parts = []
    parts.extend(address.get("line") or [])
    for key in ("city", "district", "state", "postalCode", "country"):
        if address.get(key):
            parts.append(address[key])
    return ", ".join(parts) if parts else None


def flatten_facility(loc: dict) -> dict:
    """FHIR Location -> one flat facility row. Scalar columns + JSON for nested."""
    position = loc.get("position") or {}
    managing = loc.get("managingOrganization") or {}
    part_of = loc.get("partOf") or {}
    type_coding = _first_coding((loc.get("type") or [{}])[0] if loc.get("type") else None)
    physical_type = _first_coding(loc.get("physicalType"))
    op_status = loc.get("operationalStatus") or {}
    return {
        "fhir_id": loc.get("id"),
        "identifier": _json(loc.get("identifier")),   # MFR codes / DHIS2 ids (JSON)
        "name": loc.get("name"),
        "status": loc.get("status"),                  # active | suspended | inactive
        "mode": loc.get("mode"),                      # instance | kind
        "operational_status": op_status.get("code") or op_status.get("display"),
        "facility_type_code": type_coding.get("code"),
        "facility_type_display": type_coding.get("display"),
        "physical_type_code": physical_type.get("code"),       # jurisdiction vs site
        "physical_type_display": physical_type.get("display"),
        "longitude": position.get("longitude"),
        "latitude": position.get("latitude"),
        "altitude": position.get("altitude"),
        "address": _flatten_address(loc.get("address")),
        "address_raw": _json(loc.get("address")),     # full structured object (JSON)
        "telecom": _json(loc.get("telecom")),         # phone, fax, email (JSON)
        "managing_organization_reference": managing.get("reference"),
        "managing_organization_display": managing.get("display"),
        "part_of_reference": part_of.get("reference"),   # parent facility / admin unit
        "part_of_display": part_of.get("display"),
        "raw_resource": _json(loc),                   # complete resource (JSON), nothing lost
    }


def _staffing(org: dict) -> tuple[int | None, int | None, int | None]:
    """Pull cadre counts from the facility staffing extension (nested sub-exts)."""
    for ext in (org.get("extension") or []):
        if str(ext.get("url", "")).endswith("/staffing"):
            m = {e.get("url"): e.get("valueInteger") for e in (ext.get("extension") or [])}
            return m.get("physician"), m.get("nurse"), m.get("hew")
    return None, None, None


def flatten_organization(org: dict) -> dict:
    """FHIR Organization -> one flat row (facility owners / managing bodies)."""
    type_coding = _first_coding((org.get("type") or [{}])[0] if org.get("type") else None)
    part_of = org.get("partOf") or {}
    phys, nurse, hew = _staffing(org)
    return {
        "fhir_id": org.get("id"),
        "identifier": _json(org.get("identifier")),
        "name": org.get("name"),
        "alias": _json(org.get("alias")),
        "active": org.get("active"),
        "type_code": type_coding.get("code"),
        "type_display": type_coding.get("display"),
        "telecom": _json(org.get("telecom")),
        "part_of_reference": part_of.get("reference"),
        "part_of_display": part_of.get("display"),
        "staff_phys": phys,
        "staff_nurse": nurse,
        "staff_hew": hew,
        "raw_resource": _json(org),
    }


# cli/table name -> (FHIR resource type, flattener)
# Pinned column schemas. dlt drops columns that are entirely null in a batch;
# declaring them here guarantees every Parquet file carries the full, stable
# schema regardless of nulls: this table is a contract surface for dbt.
def _cols(names_text: list[str], **typed: str) -> dict:
    schema = {n: {"data_type": "text", "nullable": True} for n in names_text}
    for n, t in typed.items():
        schema[n] = {"data_type": t, "nullable": True}
    return schema


_FACILITY_COLUMNS = _cols(
    [
        "fhir_id", "identifier", "name", "status", "mode", "operational_status",
        "facility_type_code", "facility_type_display", "physical_type_code",
        "physical_type_display", "address", "address_raw", "telecom",
        "managing_organization_reference", "managing_organization_display",
        "part_of_reference", "part_of_display", "raw_resource",
    ],
    longitude="double", latitude="double", altitude="double",
)

_ORGANIZATION_COLUMNS = _cols(
    [
        "fhir_id", "identifier", "name", "alias", "type_code", "type_display",
        "telecom", "part_of_reference", "part_of_display", "raw_resource",
    ],
    active="bool", staff_phys="bigint", staff_nurse="bigint", staff_hew="bigint",
)

# cli/table name -> (FHIR resource type, flattener, pinned columns)
_RESOURCES = {
    "facilities": ("Location", flatten_facility, _FACILITY_COLUMNS),
    "organizations": ("Organization", flatten_organization, _ORGANIZATION_COLUMNS),
}


def _build_resource(cfg: Config, client: MFRFHIRClient, table: str, resource_type: str, flatten, columns: dict):
    # max_table_nesting=0 is belt-and-suspenders: even if a future field slips
    # through un-serialized, dlt keeps it inline instead of spawning child tables.
    @dlt.resource(
        name=table,
        write_disposition="replace",
        primary_key="fhir_id",
        max_table_nesting=0,
        columns=columns,
    )
    def _r() -> Iterator[dict]:
        cap = cfg.mfr_dev_max_rows or 0
        n = 0
        for raw in client.search(resource_type, since=cfg.mfr_fhir_since or None):
            if cap and n >= cap:
                log.info("dev row cap %d reached for %s; stopping", cap, table)
                break
            n += 1
            yield flatten(raw)
    return _r


def run(cfg: Config, tables: list[str]) -> dict:
    """Run one or more MFR resource pulls. `tables` is a subset of {'facilities','organizations'}."""
    client = MFRFHIRClient(cfg)
    destination, placeholders, started_at, run_id = build_destination(cfg)
    pipeline = dlt.pipeline(
        pipeline_name=f"{cfg.dlt_pipeline_name}_facilities",
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )

    resources = []
    for table in tables:
        resource_type, flatten, columns = _RESOURCES[table]
        resources.append(_build_resource(cfg, client, table, resource_type, flatten, columns)())

    t0 = time.time()
    load_info = pipeline.run(resources, loader_file_format="parquet")
    elapsed = time.time() - t0

    written: set[str] = set()
    for pkg in load_info.load_packages:
        for job in pkg.jobs.get("completed_jobs", []):
            tn = job.job_file_info.table_name
            if not tn.startswith("_dlt"):
                written.add(tn)

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_paths = []
    for table_name in sorted(written):
        file_count, bytes_written = measure_partition(cfg, table_name=table_name, placeholders=placeholders)
        manifest_paths.append(write_manifest(
            cfg,
            table_name=table_name,
            placeholders=placeholders,
            payload=build_manifest(
                run_id=run_id,
                source=placeholders["source"],
                object_name=table_name,
                started_at=started_at,
                completed_at=completed_at,
                row_count=0,
                file_count=file_count,
                bytes_written=bytes_written,
                watermark_used=cfg.mfr_fhir_since or None,
            ),
        ))
        emit_source_lineage(source=placeholders["source"], table=table_name)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)

    return {
        "run_id": run_id,
        "tables": sorted(written),
        "manifests": manifest_paths,
        "since": cfg.mfr_fhir_since or None,
        "elapsed_seconds": elapsed,
    }
