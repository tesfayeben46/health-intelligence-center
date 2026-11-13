"""Best-effort lineage emission to OpenMetadata (optional; ingestion never depends on it).

Emits the first hop of the platform's lineage graph, from the source system's
table to the ClickHouse raw view that reads its lake landing
(`raw_<source>__<table>`). Everything downstream (staging -> vault -> marts ->
widgets) is covered by the dbt connector.

Contract (independent of the `_manifest.json` sidecars, which this module never touches):
  * a no-op unless OM_LINEAGE_API and OM_LINEAGE_JWT are set;
  * every failure is swallowed after one warning line: an ingest run must
    never fail, slow down materially, or retry because the catalog is down;
  * idempotent: createOrUpdate PUTs, and OpenMetadata accepts a lineage edge
    that already exists.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

_API = os.environ.get("OM_LINEAGE_API", "").rstrip("/")          # e.g. http://openmetadata.nhdw.svc.cluster.local:8585/api
_JWT = os.environ.get("OM_LINEAGE_JWT", "")
_TIMEOUT = float(os.environ.get("OM_LINEAGE_TIMEOUT", "4"))
_SERVICE = "nhdw-sources"        # one umbrella service; one database per source system
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _call(method: str, path: str, payload: dict | None = None) -> dict | None:
    req = urllib.request.Request(
        f"{_API}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {_JWT}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as r:
        return json.loads(r.read() or b"{}")


def emit_source_lineage(*, source: str, table: str, source_system: str | None = None) -> None:
    """source: the lake prefix (e.g. 'elmis_hist'); table: the landed table name."""
    if not (_API and _JWT):
        return
    try:
        system = source_system or source
        _call("PUT", "/v1/services/databaseServices", {
            "name": _SERVICE, "serviceType": "CustomDatabase",
            "connection": {"config": {"type": "CustomDatabase",
                                      "sourcePythonClass": "",
                                      "connectionOptions": {"note": "logical source-system registry (lineage only)"}}},
            "description": "Source systems feeding the lake, registered by the ingestors' lineage emitters.",
        })
        _call("PUT", "/v1/databases", {"name": system, "service": _SERVICE,
                                       "description": f"Source system behind the '{source}' lake prefix."})
        _call("PUT", "/v1/databaseSchemas", {"name": "main", "database": f"{_SERVICE}.{system}"})
        src = _call("PUT", "/v1/tables", {"name": table, "databaseSchema": f"{_SERVICE}.{system}.main",
                                          "columns": []})
        raw_fqn = f"nhdw-staging.default.nhdw_staging.raw_{source}__{table}"
        q = urllib.parse.quote(raw_fqn, safe="")
        try:
            dst = _call("GET", f"/v1/tables/name/{q}")
        except urllib.error.HTTPError:
            return   # raw view not in the catalog yet: nothing to link
        _call("PUT", "/v1/lineage", {"edge": {
            "fromEntity": {"id": src["id"], "type": "table"},
            "toEntity": {"id": dst["id"], "type": "table"},
            "lineageDetails": {"description": f"dlt ingest: {system}.{table} to the lake ({source}/), read through a raw view"},
        }})
    except Exception as exc:                                     # noqa: BLE001
        print(f"[lineage] emit skipped ({source}/{table}): {exc}")
