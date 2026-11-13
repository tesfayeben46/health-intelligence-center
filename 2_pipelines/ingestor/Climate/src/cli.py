"""CLI entry point: dispatch by pipeline name.

Usage (inside the container):
    python -m src.cli weekly    # weekly meteorological observations
    python -m src.cli all       # alias for weekly
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
    for noisy in ("dlt", "s3fs", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="climate-ingestor")
    parser.add_argument("pipeline", choices=["weekly", "all"], help="which pipeline to run")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    cfg = Config.from_env()
    log = logging.getLogger("climate-ingestor")
    log.info("pipeline=%s base_url=%s bucket=%s", args.pipeline, cfg.climate_api_base_url, cfg.minio_raw_bucket)

    from .pipelines.weekly import run as run_weekly
    result = run_weekly(cfg)
    log.info("done: %s", json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
