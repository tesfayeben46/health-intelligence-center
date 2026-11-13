"""OpenLMIS requisitions pipeline.

`/api/requisitions/search` lists requisitions but omits line items; the
per-id `GET /api/requisitions/{id}` returns the full DTO including
`requisitionLineItems` (dlt auto-unnests those into a
`requisitions__requisition_line_items` child table). Demo scale is tens of
requisitions, so per-id fetching is cheap. write_disposition="replace":
requisitions mutate in place as they move through their status workflow
(INITIATED -> SUBMITTED -> AUTHORIZED -> APPROVED -> RELEASED), so a full
snapshot per run is the correct representation for the warehouse.
"""
from __future__ import annotations

import logging
from typing import Iterator

import dlt

from ..config import Config
from ._client import OpenLMISClient
from ._runner import run_resources

log = logging.getLogger(__name__)


@dlt.resource(name="requisitions", write_disposition="replace")
def _requisitions(client: OpenLMISClient) -> Iterator[dict]:
    n_full, n_fallback = 0, 0
    for stub in client.paged("requisitions/search"):
        rid = stub.get("id")
        try:
            full = client.get(f"requisitions/{rid}")
            n_full += 1
            yield full
        except Exception as e:  # keep the run alive; the stub still has status/facility/program
            log.warning("full fetch failed for requisition %s (%s); using search stub", rid, e)
            n_fallback += 1
            yield stub
    log.info("requisitions: %d full, %d stubs", n_full, n_fallback)


def run(cfg: Config) -> dict:
    client = OpenLMISClient(cfg)
    return run_resources(cfg, "requisitions", [_requisitions(client)])
