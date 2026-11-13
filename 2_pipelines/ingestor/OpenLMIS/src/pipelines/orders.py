"""OpenLMIS fulfillment pipeline: orders + proofs of delivery.

`/api/orders` is Spring-paginated. `/api/proofsOfDelivery` 500s when called
unfiltered (observed on v3.19.2), so PoDs are fetched per order id. Demo
scale is tens of orders, and orders with no PoD return an empty page.
write_disposition="replace" (demo-scale snapshots, same reasoning as
requisitions).

Tables written:
    orders                (order headers + nested orderLineItems child table)
    proofs_of_delivery    (delivery confirmations, stamped with _order_id)
"""
from __future__ import annotations

import logging
from typing import Iterator

import dlt

from ..config import Config
from ._client import OpenLMISClient
from ._runner import run_resources

log = logging.getLogger(__name__)


def _resources(client: OpenLMISClient):
    order_ids: list[str] = []

    @dlt.resource(name="orders", write_disposition="replace")
    def _orders() -> Iterator[dict]:
        for row in client.paged("orders"):
            if row.get("id"):
                order_ids.append(row["id"])
            yield row
        log.info("orders: %d", len(order_ids))

    @dlt.resource(name="proofs_of_delivery", write_disposition="replace")
    def _pods() -> Iterator[dict]:
        # Runs after _orders in the same pipeline, so order_ids is populated.
        n = 0
        for oid in order_ids:
            try:
                for pod in client.paged("proofsOfDelivery", {"orderId": oid}):
                    pod["_order_id"] = oid
                    n += 1
                    yield pod
            except Exception as e:
                log.warning("proofsOfDelivery skipped for order %s: %s", oid, e)
        log.info("proofs_of_delivery: %d", n)

    return [_orders(), _pods()]


def run(cfg: Config) -> dict:
    client = OpenLMISClient(cfg)
    return run_resources(cfg, "orders", _resources(client))
