"""Thin DHIS2 Web API client. There is no Python DHIS2 SDK; this wraps the
small slice of `/api/*` the ingestor needs.

Auth is HTTP Basic (the DHIS2 default). OAuth or a personal access token
would be added in `__init__`.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests

from ..config import Config

log = logging.getLogger(__name__)


class DHIS2Client:
    def __init__(self, cfg: Config):
        self.base_url = cfg.dhis2_base_url
        self.timeout = cfg.dhis2_timeout
        self.page_size = cfg.dhis2_page_size
        self.session = requests.Session()
        self.session.auth = (cfg.dhis2_user, cfg.dhis2_password)
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "nhdw-dhis2-ingestor/0.1",
        })

    def get(self, path: str, params: dict | None = None, *, retries: int = 3) -> dict:
        """GET with retry on transient errors (502/503/504/connection reset)."""
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        for attempt in range(retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code in (502, 503, 504):
                    raise requests.HTTPError(f"{r.status_code} on {url}", response=r)
                r.raise_for_status()
                # DHIS2 returns 200 with an empty body when a dataValueSets
                # query has no matching values: treat as the empty envelope.
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

    def paginated(self, resource: str, *, fields: str = ":owner", extra_params: dict | None = None) -> Iterator[dict]:
        """Iterate every row of a DHIS2 paginated metadata endpoint.

        `resource` is the API noun (e.g. 'organisationUnits'); the response
        envelope's data array is also keyed by that exact noun.
        """
        page = 1
        while True:
            params = {
                "paging": "true",
                "pageSize": self.page_size,
                "page": page,
                "fields": fields,
            }
            if extra_params:
                params.update(extra_params)
            payload = self.get(resource, params=params)
            items = payload.get(resource, [])
            if not items:
                return
            yield from items
            pager = payload.get("pager") or {}
            if page >= int(pager.get("pageCount") or 0):
                return
            page += 1
