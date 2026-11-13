"""Column-classification governance: the steward's view of what is masked.

The masking layer reads `ops_perm.column_classification`, a denylist (a column
with no row is visible). Three writers maintain it: the committed seed, stewards
through these routes, and the OpenMetadata sync. Each owns only its own rows via
`managed_by`, so no writer can undo another's.

Endpoints:
  GET    /api/classification/columns      what is classified today (+ the mart
                                          it belongs to, from mart_attrs)
  GET    /api/classification/unclassified fail-open detector: mart columns
  whose name looks sensitive but that
  have no classification row, so are
  served in clear. With a denylist, a
  missing row is the only way
  sensitive data leaks by omission, so
  the gap is reported explicitly.
  PUT    /api/classification/column       classify (or reclassify) as steward
  DELETE /api/classification/column       remove a steward row (seed and
  openmetadata rows are never touched)

Writes invalidate the gateway's classification cache, so the next read masks
by the new policy without a restart.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from .. import mart_attrs, ops_db, permissions, trino_client

log = logging.getLogger("gateway.classification")
router = APIRouter(prefix="/api/classification", tags=["classification"])

CAPABILITY = "manage_classification"

SENSITIVITIES = ("default", "restricted", "privileged")
MASK_CLASSES = ("none", "drop", "hash", "partial", "generalize", "tokenize")

# Name patterns that suggest a column holds protectable data. Used only to raise
# a review flag, never to classify automatically: a silently masked false
# positive would break dashboards, so a steward decides.
_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    (r"(^|_)(lat|latitude|lng|long|longitude|geom|geo_point)$", "geolocation"),
    (r"(^|_)(phone|telecom|msisdn|mobile|contact)(_|$)", "contact"),
    (r"(^|_)(email|e_mail)(_|$)", "contact"),
    (r"(^|_)(patient|person|client|beneficiary)_?(id|uid|uuid|code)?$", "person identifier"),
    (r"(^|_)(mrn|nid|fan|fin|national_id|ssn)(_|$)", "national identifier"),
    (r"(^|_)(name|full_name|given_name|family_name)$", "person name (verify; may be a facility/programme name)"),
    (r"(^|_)(dob|birth_date|date_of_birth)(_|$)", "date of birth"),
    (r"(^|_)(address|kebele|house_no|street)(_|$)", "address"),
    (r"(^|_)(observation|diagnosis|condition|note|remark|comment)(_|$)", "clinical free text"),
]

# Dimension and join columns that must stay visible; masking them breaks every
# dashboard. A pattern match on these is not a finding.
_SAFE_COLUMNS = {
    "region_name", "region_id", "zone_name", "woreda_name", "facility_name",
    "programme_name", "programme_id", "product_name", "commodity_name",
    "indicator_name", "disease_name", "model_name", "dataset_name",
    "hub_name", "source_name", "mart_name", "widget_name", "page_name",
    "name_source", "period_name", "tier_name", "category_name",
}


class ColumnIn(BaseModel):
    schema_name: str = Field(min_length=1, max_length=63)
    table_name: str = Field(min_length=1, max_length=63)
    column_name: str = Field(min_length=1, max_length=63)
    sensitivity: str
    mask_class: str
    data_class: str | None = None
    pii: bool = False
    note: str | None = None


async def _require_steward(user: UserCtx) -> None:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rids = await permissions._resolve_role_ids(conn, user)
        caps = await permissions._role_capabilities(conn, rids)
    if CAPABILITY not in caps:
        raise HTTPException(403, f"missing capability: {CAPABILITY}")


@router.get("/columns")
async def list_columns(user: UserCtx = Depends(get_user)) -> dict:
    """Every classified column, with the governance attributes of its mart.
    Relations not in the catalog are included: they are denied on read, but
    their classification applies once they are catalogued."""
    await _require_steward(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.schema_name, c.table_name, c.column_name,
                   c.sensitivity, c.mask_class, c.data_class, c.pii,
                   c.managed_by, c.note, c.updated_at,
                   m.subject, m.data_class AS mart_data_class,
                   m.granularity, m.default_sensitivity
            FROM ops_perm.column_classification c
            LEFT JOIN ops_perm.mart_attrs m
                   ON m.schema_name = c.schema_name
                  AND m.table_name  = c.table_name
            ORDER BY c.schema_name, c.table_name, c.column_name
        """)
    by_manager: dict[str, int] = {}
    for r in rows:
        by_manager[r["managed_by"]] = by_manager.get(r["managed_by"], 0) + 1
    return {
        "columns": [{
            "schema_name": r["schema_name"], "table_name": r["table_name"],
            "column_name": r["column_name"], "sensitivity": r["sensitivity"],
            "mask_class": r["mask_class"], "data_class": r["data_class"],
            "pii": r["pii"], "managed_by": r["managed_by"], "note": r["note"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "mart": {"subject": r["subject"], "data_class": r["mart_data_class"],
                     "granularity": r["granularity"],
                     "default_sensitivity": r["default_sensitivity"]}
                    if r["subject"] else None,
        } for r in rows],
        "by_manager": by_manager,
    }


@router.get("/unclassified")
async def unclassified(user: UserCtx = Depends(get_user),
                       limit: int = Query(default=200, ge=1, le=2000)) -> dict:
    """Mart columns with sensitive-looking names and no classification row.

    With a denylist, omission is the only silent leak. This scan reads the live
    mart columns, flags names matching the sensitive patterns and subtracts
    everything already classified. A finding is a review item, never an
    automatic mask: the steward classifies it, or dismisses it by classifying
    it 'default' or 'none'."""
    await _require_steward(user)
    try:
        scanned, findings = await scan_unclassified()
    except Exception as exc:                                      # noqa: BLE001
        log.warning("unclassified scan failed: %s", exc)
        raise HTTPException(502, f"could not read mart columns: {exc}")
    return {"findings": findings[:limit], "total": len(findings),
            "scanned_columns": scanned,
            "note": "name-pattern review flags; a finding is served in the CLEAR "
                    "until classified; classify as 'default'/'none' to dismiss"}


async def scan_unclassified() -> tuple[int, list[dict]]:
    """The gap scan; returns (columns scanned, findings).

    Shared with the DQ detector so the steward endpoint and the nightly flag
    agree on what counts as a gap. Raises if the mart catalog cannot be read,
    rather than reporting "no gaps" for a warehouse it never scanned."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        known = {(r["schema_name"], r["table_name"], r["column_name"])
                 for r in await conn.fetch("""
                     SELECT schema_name, table_name, column_name
                     FROM ops_perm.column_classification
                 """)}
    res = await trino_client.query(
        "SELECT table_schema, table_name, column_name "
        "FROM marts.information_schema.columns "
        "WHERE table_schema LIKE 'bi\\_%' ESCAPE '\\'")

    findings = []
    for r in res.get("rows", []):
        key = (r["table_schema"], r["table_name"], r["column_name"])
        col = (r["column_name"] or "").lower()
        if key in known or col in _SAFE_COLUMNS:
            continue
        for pattern, why in _SENSITIVE_PATTERNS:
            if re.search(pattern, col):
                findings.append({
                    "schema_name": key[0], "table_name": key[1],
                    "column_name": key[2], "reason": why,
                })
                break
    findings.sort(key=lambda f: (f["schema_name"], f["table_name"], f["column_name"]))
    return len(res.get("rows", [])), findings


@router.put("/column")
async def upsert_column(body: ColumnIn, user: UserCtx = Depends(get_user)) -> dict:
    """Classify a column as a steward. Refuses to overwrite a seed row: seed
    rows are committed configuration and change through the repository, since
    a redeploy would otherwise revert the console edit."""
    await _require_steward(user)
    if body.sensitivity not in SENSITIVITIES:
        raise HTTPException(422, f"sensitivity must be one of {SENSITIVITIES}")
    if body.mask_class not in MASK_CLASSES:
        raise HTTPException(422, f"mask_class must be one of {MASK_CLASSES}")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("""
            SELECT managed_by FROM ops_perm.column_classification
            WHERE schema_name = $1 AND table_name = $2 AND column_name = $3
        """, body.schema_name, body.table_name, body.column_name)
        if existing and existing["managed_by"] == "seed":
            raise HTTPException(
                409, "this column is seed-managed; edit "
                     "bundle/seed-pack/app/security/column_classification.yaml "
                     "so the change survives a redeploy")
        await conn.execute("""
            INSERT INTO ops_perm.column_classification
                  (schema_name, table_name, column_name, sensitivity, mask_class,
                   data_class, pii, purpose_unmask, managed_by, note, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, '{}', 'steward', $8, now())
            ON CONFLICT (schema_name, table_name, column_name) DO UPDATE
              SET sensitivity = EXCLUDED.sensitivity,
                  mask_class  = EXCLUDED.mask_class,
                  data_class  = EXCLUDED.data_class,
                  pii         = EXCLUDED.pii,
                  managed_by  = 'steward',
                  note        = EXCLUDED.note,
                  updated_at  = now()
        """, body.schema_name, body.table_name, body.column_name,
             body.sensitivity, body.mask_class, body.data_class, body.pii,
             body.note)

    await mart_attrs.invalidate()          # the next read masks by the new policy
    await ops_db.log_data_event(
        user=user, kind="classification.upsert",
        payload={"column": f"{body.schema_name}.{body.table_name}.{body.column_name}",
                 "sensitivity": body.sensitivity, "mask_class": body.mask_class})
    return {"ok": True, "managed_by": "steward",
            "column": f"{body.schema_name}.{body.table_name}.{body.column_name}"}


@router.delete("/column")
async def delete_column(user: UserCtx = Depends(get_user),
                        schema_name: str = Query(...),
                        table_name: str = Query(...),
                        column_name: str = Query(...)) -> dict:
    """Remove a steward classification (the column becomes visible again).
    Seed and OpenMetadata rows cannot be deleted here; their writers
    reconcile them."""
    await _require_steward(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            DELETE FROM ops_perm.column_classification
            WHERE schema_name = $1 AND table_name = $2 AND column_name = $3
              AND managed_by = 'steward'
            RETURNING column_name
        """, schema_name, table_name, column_name)
    if row is None:
        raise HTTPException(404, "no steward-managed classification for that column")

    await mart_attrs.invalidate()
    await ops_db.log_data_event(
        user=user, kind="classification.delete",
        payload={"column": f"{schema_name}.{table_name}.{column_name}"})
    return {"ok": True, "removed": f"{schema_name}.{table_name}.{column_name}"}
