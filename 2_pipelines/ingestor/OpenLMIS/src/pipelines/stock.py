"""OpenLMIS stock pipeline: stock cards with stock-on-hand.

`/api/stockCards` (stockmanagement v1) lists every stock card in one paged
sweep, each row carrying `stockOnHand` plus embedded facility / program /
orderable / lot DTOs, so no per-(facility, program) iteration needed (the v2
stockCardSummaries API requires both as mandatory params, which would mean
~10k calls across the 2,734 demo facilities).

write_disposition="replace": each run is a fresh point-in-time snapshot
(the mart layer can build history from lake partitions if ever needed).
"""
from __future__ import annotations

import logging
from typing import Iterator

import dlt

from ..config import Config
from ._client import OpenLMISClient
from ._runner import run_resources

log = logging.getLogger(__name__)


@dlt.resource(name="stock_cards", write_disposition="replace")
def _stock(client: OpenLMISClient) -> Iterator[dict]:
    n = 0
    for row in client.paged("stockCards"):
        n += 1
        yield row
    log.info("stock: %d stock cards", n)


def run(cfg: Config) -> dict:
    client = OpenLMISClient(cfg)
    return run_resources(cfg, "stock", [_stock(client)])
