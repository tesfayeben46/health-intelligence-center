"""CLI entry point: dispatch by pipeline name.

Usage (inside the container):
    python -m src.cli metadata
    python -m src.cli data-value-sets
    python -m src.cli all
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
    # dlt is noisy at INFO; quiet a level.
    logging.getLogger("dlt").setLevel(logging.WARNING)
    logging.getLogger("s3fs").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dhis2-ingestor")
    parser.add_argument(
        "pipeline",
        choices=["metadata", "data-value-sets", "all"],
        help="which pipeline to run",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    cfg = Config.from_env()
    log = logging.getLogger("dhis2-ingestor")
    log.info("pipeline=%s base_url=%s bucket=%s", args.pipeline, cfg.dhis2_base_url, cfg.minio_raw_bucket)

    results: dict[str, object] = {}
    if args.pipeline in ("metadata", "all"):
        from .pipelines.metadata import run as run_metadata
        results["metadata"] = run_metadata(cfg)
    if args.pipeline in ("data-value-sets", "all"):
        from .pipelines.data_value_sets import run as run_dvs
        results["data_value_sets"] = run_dvs(cfg)

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
