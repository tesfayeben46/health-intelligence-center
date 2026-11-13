"""HCMIS WDM ingestor CLI.

    python -m src.cli extract                       # all tables
    python -m src.cli extract --only fact_dailyrecord,vaccine_issued
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
    for noisy in ("dlt", "s3fs", "botocore", "urllib3", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hcmiswdm-ingestor")
    parser.add_argument("pipeline", choices=["extract"])
    parser.add_argument("--only", default="")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    cfg = Config.from_env()
    log = logging.getLogger("hcmiswdm-ingestor")
    log.info("pipeline=%s host=%s db=%s bucket=%s", args.pipeline, cfg.hcmiswdm_host, cfg.hcmiswdm_database, cfg.minio_raw_bucket)

    only = [s.strip().lower() for s in args.only.split(",") if s.strip()] or None
    from .pipelines.extract import run as run_extract
    result = run_extract(cfg, only=only)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
