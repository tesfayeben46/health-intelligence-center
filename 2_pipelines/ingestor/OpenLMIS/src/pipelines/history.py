"""eLMIS v2 history pipeline: a direct Postgres read of the eLMIS (Zambia,
2016 to 2018) history database (0_external/sources/OpenLMIS), streamed to the
lake under its own source prefix `elmis_hist/` (separate dataset and manifests).
Source identity is preserved all the way into the vault, so an eLMIS history
facility is never conflated with an OpenLMIS v3 demo entity.

Same mechanism as the HCMISWDM/mBrana ingestors (dlt sql_database over
SQLAlchemy): the source is the database of a decommissioned system, read
directly. Full replace per run: history is immutable, so a re-run is a
byte-identical rewrite.

The largest table (history.requisition_line) uses the pyarrow backend for
bulk streaming.
"""
from __future__ import annotations

import logging
from typing import Iterable, NamedTuple

import dlt
from dlt.sources.sql_database import sql_table

from ..config import Config
from ._runner import run_resources

log = logging.getLogger(__name__)


class TableSpec(NamedTuple):
    schema: str
    table: str

    @property
    def dlt_name(self) -> str:
        return f"{self.schema}_{self.table}".lower()


# The mutation.* schema (provenance manifest + validation metrics) stays out
# of the lake: it is governance metadata, not health data;
# reconciliation reads it from the source DB directly.
TABLES: list[TableSpec] = [
    # reference (dimension surface)
    TableSpec("reference", "geographic_level"),
    TableSpec("reference", "geographic_zone"),
    TableSpec("reference", "facility_type"),
    TableSpec("reference", "facility"),
    TableSpec("reference", "product_category"),
    TableSpec("reference", "product_form"),
    TableSpec("reference", "product_group"),
    TableSpec("reference", "dosage_unit"),
    TableSpec("reference", "product"),
    TableSpec("reference", "programme"),
    TableSpec("reference", "programme_product"),
    TableSpec("reference", "processing_schedule"),
    TableSpec("reference", "reporting_period"),
    TableSpec("reference", "facility_programme_schedule"),
    # history (facts)
    TableSpec("history", "requisition"),
    TableSpec("history", "requisition_line"),
]


def _resources(cfg: Config, specs: Iterable[TableSpec]):
    url = cfg.hist_sqlalchemy_url()
    for spec in specs:
        res = sql_table(
            credentials=url,
            schema=spec.schema,
            table=spec.table,
            backend="pyarrow",
        )
        res.apply_hints(table_name=spec.dlt_name, write_disposition="replace")
        yield res


def run(cfg: Config, *, only: list[str] | None = None) -> dict:
    specs = [t for t in TABLES if not only or t.dlt_name in only]
    if not specs:
        raise RuntimeError(f"No tables matched `only`={only!r}")
    log.info("elmis_hist: extracting %d tables from %s:%s/%s",
             len(specs), cfg.hist_host, cfg.hist_port, cfg.hist_db)
    return run_resources(cfg, "history", list(_resources(cfg, specs)), source="elmis_hist")
