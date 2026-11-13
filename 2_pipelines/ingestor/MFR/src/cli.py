"""CLI entry point: dispatch by pipeline name.

Usage (inside the container):
    python -m src.cli facilities       # Ethiopian facilities (FHIR Location)
    python -m src.cli organizations    # facility owners (FHIR Organization)
    python -m src.cli all              # both, one run
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
    parser = argparse.ArgumentParser(prog="mfr-ingestor")
    parser.add_argument(
        "pipeline",
        choices=["facilities", "organizations", "all"],
        help="which pipeline to run",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    cfg = Config.from_env()
    log = logging.getLogger("mfr-ingestor")
    auth_mode = "bearer" if cfg.mfr_fhir_token else ("basic" if cfg.mfr_fhir_user else "none")
    log.info(
        "pipeline=%s base_url=%s auth=%s bucket=%s",
        args.pipeline, cfg.mfr_fhir_base_url, auth_mode, cfg.minio_raw_bucket,
    )

    tables = ["facilities", "organizations"] if args.pipeline == "all" else [args.pipeline]

    from .pipelines.facilities import run as run_facilities
    result = run_facilities(cfg, tables)

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
