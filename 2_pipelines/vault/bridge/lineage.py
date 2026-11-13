"""Best-effort lineage emission to OpenMetadata (optional; the sync never depends on it).

Emits the bridge's hop of the platform's lineage graph: the ClickHouse staging
model `nhdw_staging.stg_<x>` -> its Postgres landing copy `nhdw_landing.stg_<x>`.
The dbt connector covers raw view -> staging model (ClickHouse) and landing ->
vault -> marts (Postgres); this CH->PG copy is the one hop no tool can infer,
because it happens here, outside dbt.

Same contract as the ingestors' emitters (2_pipelines/ingestor/*/src/lineage.py):
  * a no-op unless OM_LINEAGE_API and OM_LINEAGE_JWT are set;
  * every failure is swallowed after one warning line: a sync must never fail,
    slow down materially, or retry because the catalog is down;
  * idempotent: an edge that already exists is accepted by OpenMetadata as-is;
  * both ends must already be catalogued; if either is not, nothing is linked.
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
_STAGING_FQN = "nhdw-staging.default.nhdw_staging.{table}"
_LANDING_FQN = "nhdw-vault.nhdw_vault.{schema}.{table}"
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


def _table_id(fqn: str) -> str | None:
    try:
        return (_call("GET", f"/v1/tables/name/{urllib.parse.quote(fqn, safe='')}") or {}).get("id")
    except urllib.error.HTTPError:
        return None   # not in the catalog yet: nothing to link


def emit_bridge_lineage(*, table: str, schema: str = "nhdw_landing") -> None:
    """table: the stg_* name, identical on both sides of the copy."""
    if not (_API and _JWT):
        return
    try:
        src = _table_id(_STAGING_FQN.format(table=table))
        dst = _table_id(_LANDING_FQN.format(schema=schema, table=table))
        if not (src and dst):
            return
        _call("PUT", "/v1/lineage", {"edge": {
            "fromEntity": {"id": src, "type": "table"},
            "toEntity": {"id": dst, "type": "table"},
            "lineageDetails": {"description": f"bridge sync: ClickHouse nhdw_staging.{table} to Postgres {schema}.{table}"},
        }})
    except Exception as exc:                                     # noqa: BLE001
        print(f"[lineage] emit skipped ({table}): {exc}")
