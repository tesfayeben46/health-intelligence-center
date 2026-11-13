"""Thin MFR FHIR R4 client: paginated search over Bundle `next` links.

No FHIR SDK; plain requests. MFR exposes a conformant FHIR R4 server; the
Bundle pagination protocol is fully spec-defined, so we follow `link[next]`
until it is gone, so one resource type drains completely before the next starts.

Auth: MFR may front the FHIR server with HTTP Basic or a bearer token. Both are
supported via MFR_FHIR_USER/_PASSWORD or MFR_FHIR_TOKEN; leave blank for an
unauthenticated endpoint.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterator

import requests

from ..config import Config

log = logging.getLogger(__name__)


class MFRFHIRClient:
    def __init__(self, cfg: Config):
        self.base_url = cfg.mfr_fhir_base_url
        self.timeout = cfg.mfr_fhir_timeout
        self.page_size = cfg.mfr_fhir_page_size
        self.max_pages = cfg.mfr_fhir_max_pages  # 0 = unbounded
        self.session = requests.Session()
        # Auth precedence: bearer token > HTTP Basic > none. Switching Basic<->Bearer
        # is a one-line .env change (set/clear MFR_FHIR_TOKEN); no code edit. The
        # mode is recorded so a throttle comparison shows up in the logs.
        if cfg.mfr_fhir_token:
            self.auth_mode = "bearer"
            self.session.headers["Authorization"] = f"Bearer {cfg.mfr_fhir_token}"
        elif cfg.mfr_fhir_user:
            self.auth_mode = "basic"
            self.session.auth = (cfg.mfr_fhir_user, cfg.mfr_fhir_password)
        else:
            self.auth_mode = "none"
        self.session.headers.update({
            "Accept": "application/fhir+json",
            "User-Agent": "nhdw-mfr-ingestor/0.1",
        })

    def _get(self, url: str, *, params: dict | None = None, retries: int = 3) -> dict:
        """GET with retry on transient errors (502/503/504/connection reset)."""
        for attempt in range(retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code in (502, 503, 504):
                    raise requests.HTTPError(f"{r.status_code} on {url}", response=r)
                r.raise_for_status()
                if not r.content.strip():
                    return {}
                return r.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                if attempt + 1 >= retries:
                    raise
                wait = 2 ** attempt
                log.warning("retry %d/%d on %s after %ds: %s", attempt + 1, retries, url, wait, e)
                time.sleep(wait)
        raise RuntimeError("unreachable")

    def search(self, resource_type: str, *, since: str | None = None) -> Iterator[dict]:
        """Paginated search across all resources of a type. Yields raw FHIR resources.

        Follows the Bundle `next` link until exhausted (or max_pages, if set).
        `since` filters on FHIR `_lastUpdated` (ISO 8601); empty = full pull.
        """
        params = {"_count": self.page_size}
        if since:
            params["_lastUpdated"] = f"ge{since}"
        url = f"{self.base_url}/{resource_type}"
        pages = 0
        total = 0
        t0 = time.time()
        while url:
            if self.max_pages and pages >= self.max_pages:
                log.warning("hit max_pages=%d for %s; stopping early", self.max_pages, resource_type)
                break
            # Params only apply to the first request; `next` links are absolute
            # and already carry the cursor/filters.
            payload = self._get(url, params=params if pages == 0 else None)
            for entry in payload.get("entry") or []:
                resource = entry.get("resource")
                if resource:
                    total += 1
                    yield resource
            url = None
            for link in payload.get("link") or []:
                if link.get("relation") == "next":
                    url = link.get("url")
                    break
            pages += 1
        elapsed = time.time() - t0
        rate = total / elapsed if elapsed > 0 else 0.0
        # Throughput line: compare across MFR_FHIR_TOKEN set (bearer) vs unset
        # (basic) to decide whether Basic auth is throttling.
        log.info(
            "%s: drained %d resource(s) over %d page(s) in %.1fs (%.1f rec/s, auth=%s)",
            resource_type, total, pages, elapsed, rate, self.auth_mode,
        )
