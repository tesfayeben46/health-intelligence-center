"""CLI entry point: dispatch by pipeline name.

Usage (inside the container):
    python -m src.cli history          # eLMIS v2 history DB -> elmis_hist/
    python -m src.cli reference-data   # OpenLMIS v3 API lane (suspended)
    python -m src.cli requisitions
    python -m src.cli stock
    python -m src.cli orders
    python -m src.cli all              # all v3 API pipelines
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import Config


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logging.getLogger("dlt").setLevel(logging.WARNING)
    logging.getLogger("s3fs").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openlmis-ingestor")
    parser.add_argument(
        "pipeline",
        choices=["reference-data", "requisitions", "stock", "orders", "all", "history"],
        help="which pipeline to run ('all' = the live v3 API pipelines; "
             "'history' = the legacy eLMIS v2 DB -> elmis_hist/ lake prefix)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    cfg = Config.from_env()
    log = logging.getLogger("openlmis-ingestor")
    log.info("pipeline=%s base_url=%s bucket=%s", args.pipeline, cfg.openlmis_base_url, cfg.minio_raw_bucket)

    results: dict[str, object] = {}
    if args.pipeline in ("reference-data", "all"):
        from .pipelines.reference_data import run as run_reference
        results["reference_data"] = run_reference(cfg)
    if args.pipeline in ("requisitions", "all"):
        from .pipelines.requisitions import run as run_requisitions
        results["requisitions"] = run_requisitions(cfg)
    if args.pipeline in ("stock", "all"):
        from .pipelines.stock import run as run_stock
        results["stock"] = run_stock(cfg)
    if args.pipeline in ("orders", "all"):
        from .pipelines.orders import run as run_orders
        results["orders"] = run_orders(cfg)
    if args.pipeline == "history":
        from .pipelines.history import run as run_history
        results["history"] = run_history(cfg)

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
