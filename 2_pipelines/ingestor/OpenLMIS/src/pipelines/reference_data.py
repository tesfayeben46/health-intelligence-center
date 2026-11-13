"""OpenLMIS reference-data pipeline: full refresh of the dimension surface.

Why full-refresh (write_disposition="replace"): reference data is small
(demo scale: dozens-to-hundreds of rows per table) and all of it is needed to
interpret requisition/stock facts. Same reasoning as the MFR ingestor.

Tables written:
    facilities            (paged; includes supportedPrograms + geographicZone)
    geographic_zones      (paged)
    programs              (bare array endpoint)
    orderables            (paged; the product catalogue)
    processing_periods    (paged; requisition reporting periods)
    lots                  (paged; batch/expiry tracking)
"""
from __future__ import annotations

import logging
from typing import Iterator

import dlt

from ..config import Config
from ._client import OpenLMISClient
from ._runner import run_resources

log = logging.getLogger(__name__)


def _paged_resource(name: str, path: str):
    @dlt.resource(name=name, write_disposition="replace")
    def _r(client: OpenLMISClient) -> Iterator[dict]:
        log.info("fetching %s (%s)", name, path)
        yield from client.paged(path)
    return _r


REFERENCE_RESOURCES = [
    _paged_resource("facilities",         "facilities"),
    _paged_resource("geographic_zones",   "geographicZones"),
    _paged_resource("orderables",         "orderables"),
    _paged_resource("processing_periods", "processingPeriods"),
    _paged_resource("lots",               "lots"),
]


@dlt.resource(name="programs", write_disposition="replace")
def _programs(client: OpenLMISClient) -> Iterator[dict]:
    log.info("fetching programs (bare-array endpoint)")
    yield from client.get("programs")


def run(cfg: Config) -> dict:
    client = OpenLMISClient(cfg)
    resources = [r(client) for r in REFERENCE_RESOURCES] + [_programs(client)]
    return run_resources(cfg, "reference_data", resources)
