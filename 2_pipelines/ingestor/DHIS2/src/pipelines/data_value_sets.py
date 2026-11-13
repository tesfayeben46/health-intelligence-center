"""DHIS2 dataValueSets pipeline: the primary fact-row extractor.

Watermark: `lastUpdated`. dlt manages the state under DLT_PIPELINE_DIR; on the
first run it falls back to DHIS2_INITIAL_LAST_UPDATED.

Strategy:
    1. Resolve the level-1 org unit (the "root").
    2. List every dataSet.
    3. For each dataSet, GET /api/dataValueSets with the root OU + children=true
       + lastUpdated=<watermark>. DHIS2 returns every dataValue under the tree
       updated since the watermark.
    4. Yield each dataValue, tagged with the source dataSet id.

`/api/analytics.json` is not used: it serves DHIS2's pre-aggregated analytics
tables, not the raw data values.
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


@dlt.resource(
    name="data_value_sets",
    write_disposition="append",
    primary_key=("data_element", "period", "org_unit", "category_option_combo", "attribute_option_combo"),
)
def data_value_sets(
    cfg: Config,
    last_updated=dlt.sources.incremental(
        "last_updated",
        initial_value=None,
    ),
) -> Iterator[dict]:
    client = DHIS2Client(cfg)
    cursor = last_updated.last_value or cfg.dhis2_initial_last_updated
    log.info("data_value_sets watermark = %s", cursor)

    # Resolve the scope org unit: dev override or the country-wide root.
    if cfg.dhis2_dev_root_ou:
        root_ou = {"id": cfg.dhis2_dev_root_ou, "name": f"<dev-scope {cfg.dhis2_dev_root_ou}>"}
    else:
        roots = [ou for ou in client.paginated(
            "organisationUnits",
            fields="id,name,level",
            extra_params={"filter": "level:eq:1"},
        )]
        if not roots:
            raise RuntimeError("DHIS2 has no level-1 organisationUnit; cannot scope dataValueSets request.")
        root_ou = roots[0]
    log.info("root org unit = %s (%s)", root_ou["name"], root_ou["id"])

    # Iterate over every dataSet (a small list: dozens to low hundreds).
    datasets = list(client.paginated("dataSets", fields="id,name"))
    log.info("found %d dataSets to extract", len(datasets))

    cap = cfg.dhis2_dev_max_rows or 0  # 0 = unbounded
    yielded = 0
    for ds in datasets:
        if cap and yielded >= cap:
            log.info("dev row cap %d reached; stopping", cap)
            break
        log.info("  dataSet %s (%s)", ds["name"], ds["id"])
        # `/api/dataValueSets.json` doesn't envelope-paginate in older versions;
        # use lastUpdated as the cursor and pull the full delta in one shot.
        # Dev mode: bracket by period (startDate/endDate) to keep responses
        # bounded: the v40 seed dump ignores `lastUpdated` and would otherwise
        # stream multi-GB JSON for the full tree.
        params = {
            "dataSet": ds["id"],
            "orgUnit": root_ou["id"],
            "children": "true",
        }
        if cfg.dhis2_dev_start_date and cfg.dhis2_dev_end_date:
            params["startDate"] = cfg.dhis2_dev_start_date
            params["endDate"] = cfg.dhis2_dev_end_date
        else:
            params["lastUpdated"] = cursor
        payload = client.get("dataValueSets.json", params=params)
        rows = payload.get("dataValues") or []
        log.info("  %d rows", len(rows))
        for dv in rows:
            if cap and yielded >= cap:
                break
            yielded += 1
            yield {
                "data_element": dv.get("dataElement"),
                "period": dv.get("period"),
                "org_unit": dv.get("orgUnit"),
                "category_option_combo": dv.get("categoryOptionCombo"),
                "attribute_option_combo": dv.get("attributeOptionCombo"),
                "value": dv.get("value"),
                "stored_by": dv.get("storedBy"),
                "created": dv.get("created"),
                "last_updated": dv.get("lastUpdated"),
                "comment": dv.get("comment"),
                "follow_up": dv.get("followUp"),
                "data_set": ds["id"],
            }


def run(cfg: Config) -> dict:
    destination, placeholders, started_at, run_id = build_destination(cfg)
    pipeline = dlt.pipeline(
        pipeline_name=f"{cfg.dlt_pipeline_name}_data_value_sets",
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )

    watermark_used = None
    watermark_advanced_to = None

    t0 = time.time()
    load_info = pipeline.run(data_value_sets(cfg), loader_file_format="parquet")
    elapsed = time.time() - t0

    # Pull watermark out of the pipeline state for the manifest.
    state = pipeline.state.get("sources", {}).get(pipeline.pipeline_name, {})
    inc_state = state.get("resources", {}).get("data_value_sets", {}).get("incremental", {}).get("last_updated", {})
    watermark_used = inc_state.get("initial_value") or cfg.dhis2_initial_last_updated
    watermark_advanced_to = inc_state.get("last_value")

    tables: set[str] = set()
    for pkg in load_info.load_packages:
        for job in pkg.jobs.get("completed_jobs", []):
            tn = job.job_file_info.table_name
            if tn.startswith("_dlt"):  # dlt state/loads bookkeeping, not a data table
                continue
            tables.add(tn)

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_paths = []
    for table_name in sorted(tables):
        file_count, bytes_written = measure_partition(cfg, table_name=table_name, placeholders=placeholders)
        payload = build_manifest(
            run_id=run_id,
            source=placeholders["source"],
            object_name=table_name,
            started_at=started_at,
            completed_at=completed_at,
            row_count=0,
            file_count=file_count,
            bytes_written=bytes_written,
            watermark_used=watermark_used,
            watermark_advanced_to=watermark_advanced_to,
        )
        manifest_paths.append(write_manifest(cfg, table_name=table_name, placeholders=placeholders, payload=payload))
        emit_source_lineage(source=placeholders["source"], table=table_name)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)

    return {
        "run_id": run_id,
        "tables": sorted(tables),
        "manifests": manifest_paths,
        "watermark_used": watermark_used,
        "watermark_advanced_to": watermark_advanced_to,
        "elapsed_seconds": elapsed,
    }
