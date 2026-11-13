"""DHIS2 metadata pipeline: full refresh of reference data.

Why full-refresh and not incremental: metadata changes infrequently and all of
it (data elements, org units, category combos) is needed to interpret the
fact-style `dataValueSets` rows. A daily full-refresh is cheap (KBs to a few MB)
and removes a class of "stale dimension" bugs in the warehouse.

The dataValueSets pipeline is the one with the watermark.

Tables written:
    organisation_units
    data_elements
    data_element_groups
    data_sets
    category_combos
    category_option_combos
    indicators
    programs
"""
from __future__ import annotations

import logging
import time
from typing import Iterator

import dlt

from ..config import Config
from ..destination import build_destination
from ..manifest import build_manifest, measure_partition, write_manifest
from ..lineage import emit_source_lineage
from ._client import DHIS2Client

log = logging.getLogger(__name__)


def _resource(name: str, fields: str):
    """Build a dlt resource that streams one DHIS2 metadata endpoint."""
    @dlt.resource(name=name, write_disposition="replace")
    def _r(client: DHIS2Client) -> Iterator[dict]:
        log.info("fetching %s (fields=%s)", name, fields)
        # DHIS2 API noun is camelCase; our table name is snake_case.
        api_noun = {
            "organisation_units": "organisationUnits",
            "data_elements": "dataElements",
            "data_element_groups": "dataElementGroups",
            "data_sets": "dataSets",
            "category_combos": "categoryCombos",
            "category_option_combos": "categoryOptionCombos",
            "indicators": "indicators",
            "programs": "programs",
        }[name]
        yield from client.paginated(api_noun, fields=fields)
    return _r


# Per-resource field projections. Avoid `:all`: it pulls heavyweight nested
# objects (translations, sharing, attributes) we don't need at the lake layer.
METADATA_RESOURCES = [
    _resource("organisation_units",     "id,name,code,shortName,level,path,parent,openingDate,closedDate,lastUpdated,featureType,coordinates"),
    _resource("data_elements",          "id,name,code,shortName,valueType,aggregationType,domainType,categoryCombo,lastUpdated"),
    _resource("data_element_groups",    "id,name,code,dataElements,lastUpdated"),
    _resource("data_sets",              "id,name,code,periodType,categoryCombo,dataSetElements,organisationUnits,lastUpdated"),
    _resource("category_combos",        "id,name,code,categories,categoryOptionCombos,lastUpdated"),
    _resource("category_option_combos", "id,name,code,categoryCombo,categoryOptions,lastUpdated"),
    _resource("indicators",             "id,name,code,shortName,numerator,denominator,indicatorType,lastUpdated"),
    _resource("programs",               "id,name,code,shortName,programType,trackedEntityType,lastUpdated"),
]


def run(cfg: Config) -> dict:
    destination, placeholders, started_at, run_id = build_destination(cfg)
    client = DHIS2Client(cfg)

    pipeline = dlt.pipeline(
        pipeline_name=f"{cfg.dlt_pipeline_name}_metadata",
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )

    # Bind the client into each resource by partial application.
    resources = [r(client) for r in METADATA_RESOURCES]

    t0 = time.time()
    load_info = pipeline.run(resources, loader_file_format="parquet")
    elapsed = time.time() - t0
    log.info("pipeline complete in %.1fs", elapsed)

    # Discover the tables dlt actually wrote (by inspecting completed jobs).
    tables: set[str] = set()
    for pkg in load_info.load_packages:
        for job in pkg.jobs.get("completed_jobs", []):
            tn = job.job_file_info.table_name
            if tn.startswith("_dlt"):  # dlt state/loads bookkeeping, not a data table
                continue
            tables.add(tn)

    # For each table, list its just-written partition and measure authoritatively.
    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_paths: list[str] = []
    for table_name in sorted(tables):
        file_count, bytes_written = measure_partition(cfg, table_name=table_name, placeholders=placeholders)
        payload = build_manifest(
            run_id=run_id,
            source=placeholders["source"],
            object_name=table_name,
            started_at=started_at,
            completed_at=completed_at,
            row_count=0,  # dlt's per-table count requires a row scan; cheap to add later via pyarrow
            file_count=file_count,
            bytes_written=bytes_written,
        )
        manifest_paths.append(write_manifest(cfg, table_name=table_name, placeholders=placeholders, payload=payload))
        emit_source_lineage(source=placeholders["source"], table=table_name)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)

    return {
        "run_id": run_id,
        "tables": sorted(tables),
        "manifests": manifest_paths,
        "elapsed_seconds": elapsed,
    }
