"""Thin OpenLMIS v3 API client.

Auth is OAuth2 password grant against the auth service (`/api/oauth/token`,
client credentials via HTTP Basic). Tokens expire (TOKEN_DURATION, default
30 min); on a 401 the client re-authenticates once and retries the request.

Pagination is Spring-Data style: zero-based `page`/`size` params, envelope
`{"content": [...], "totalPages": N, ...}`. A few endpoints (e.g. /api/programs)
return a bare JSON array instead; use `get()` directly for those.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests

from ..config import Config

log = logging.getLogger(__name__)


class OpenLMISClient:
    def __init__(self, cfg: Config):
        cfg.require_api()   # API lane only; the `history` verb never builds a client
        self.base_url = cfg.openlmis_base_url
        self.timeout = cfg.openlmis_timeout
        self.page_size = cfg.openlmis_page_size
        self._user = cfg.openlmis_user
        self._password = cfg.openlmis_password
        self._client = cfg.openlmis_oauth_client
        self._secret = cfg.openlmis_oauth_secret
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "nhdw-openlmis-ingestor/0.1",
        })
        self._authenticate()

    def _authenticate(self) -> None:
        r = requests.post(
            f"{self.base_url}/api/oauth/token",
            params={"grant_type": "password", "username": self._user, "password": self._password},
            auth=(self._client, self._secret),
            timeout=self.timeout,
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        self.session.headers["Authorization"] = f"Bearer {token}"
        log.info("obtained OAuth token (expires_in=%ss)", r.json().get("expires_in"))

    def get(self, path: str, params: dict | None = None, *, retries: int = 3) -> Any:
        """GET with one re-auth on 401 and retry on transient errors."""
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        reauthed = False
        for attempt in range(retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 401 and not reauthed:
                    log.info("token expired; re-authenticating")
                    self._authenticate()
                    reauthed = True
                    continue
                # 500 is retryable here too: fulfillment wraps transient upstream
                # rate-limit 429s from its own referencedata calls in a 500.
                if r.status_code in (500, 502, 503, 504):
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

    def paged(self, path: str, extra_params: dict | None = None) -> Iterator[dict]:
        """Iterate every row of a Spring-Data paginated endpoint (zero-based)."""
        page = 0
        while True:
            params: dict = {"page": page, "size": self.page_size}
            if extra_params:
                params.update(extra_params)
            payload = self.get(path, params=params)
            items = payload.get("content", [])
            if not items:
                return
            yield from items
            total_pages = int(payload.get("totalPages") or 0)
            page += 1
            if page >= total_pages:
                return
