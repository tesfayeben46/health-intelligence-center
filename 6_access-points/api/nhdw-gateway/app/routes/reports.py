"""/api/reports: templated report generation.

A report is a named, scoped view over a mart. Generating it runs the same
OPA-gated path as /api/exports (the `export` verb plus the caller's row_filter
scope) and renders XLSX, CSV or HTML to the object store with a presigned
download. Templates are seeded (owner NULL, visible to all) or saved by a user
(owner = their kc_sub). There is no arbitrary SQL: a template names only a mart,
columns and default filters, so authorisation is exactly the mart-export
contract and a scoped user's report contains only their granted rows.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json as _json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from .. import mart_attrs, masking, minio_client, ops_db, opa, permissions, trino_client
from ..mart_attrs import MartAttrsMissing
from ..dimensions import columns_for

router = APIRouter(prefix="/api/reports", tags=["reports"])

_FMT = ("xlsx", "csv", "html")

# The row_filter dimension to mart column mapping is app/dimensions.py::columns_for.


async def _mart_columns(schema: str, table: str) -> set[str]:
    res = await trino_client.query(
        "SELECT column_name FROM marts.information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}'")
    return {r["column_name"] for r in res.get("rows", [])}


def _esc(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (s or "report").lower()).strip("-")[:48]


def _shape_template(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id":             str(r["id"]),
        "name":           r["name"],
        "description":    r.get("description"),
        "category":       r.get("category"),
        "mart":           f'{r["schema_name"]}.{r["table_name"]}',
        "default_format": r.get("default_format", "xlsx"),
        "system":         r.get("owner") is None,
    }


@router.get("/templates")
async def list_templates(user: UserCtx = Depends(get_user)) -> list[dict[str, Any]]:
    """System templates (owner NULL) and the caller's own."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, owner, name, description, category, schema_name,
                   table_name, default_format
            FROM ops_workspace.report_template
            WHERE owner IS NULL OR owner = $1::uuid
            ORDER BY owner NULLS FIRST, category, name;
        """, user.kc_sub)
    return [_shape_template(dict(r)) for r in rows]


def _render_report(payload: dict[str, Any], fmt: str,
                   meta: dict[str, Any]) -> tuple[bytes, str, str]:
    """Render {columns, rows} as (body, content_type, ext). `meta` carries the
    report title and citation for the XLSX and HTML header."""
    cols = payload.get("columns", []) or []
    rows = payload.get("rows", []) or []
    if fmt == "csv":
        out = io.StringIO()
        if cols:
            w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return out.getvalue().encode(), "text/csv", "csv"
    if fmt == "html":
        style = ("<!doctype html><meta charset=utf-8><style>"
                 "body{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#1f2933}"
                 "h1{font-size:1.3rem;margin:0 0 .2rem}.sub{color:#647084;margin:0 0 1rem}"
                 "table{border-collapse:collapse;width:100%;font-size:12px}"
                 "th,td{border:1px solid #d7dee8;padding:.35rem .5rem;text-align:left}"
                 "th{background:#f3f6fa}tr:nth-child(even) td{background:#fafcfe}</style>")
        head = (f"<h1>{_esc(meta.get('title',''))}</h1>"
                f"<p class=sub>{_esc(meta.get('citation',''))}</p>")
        thead = "<tr>" + "".join(f"<th>{_esc(c)}</th>" for c in cols) + "</tr>"
        trs = "".join(
            "<tr>" + "".join(f"<td>{_esc(r.get(c))}</td>" for c in cols) + "</tr>"
            for r in rows)
        return f"{style}{head}<table>{thead}{trs}</table>".encode(), "text/html", "html"
    if fmt == "xlsx":
        import datetime as _dt
        from decimal import Decimal
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        def _cell(v: Any) -> Any:
            # openpyxl rejects tz-aware datetimes, so strip tzinfo. Decimals are
            # fine; other types are stringified to stay Excel-safe.
            if isinstance(v, _dt.datetime) and v.tzinfo is not None:
                return v.replace(tzinfo=None)
            if isinstance(v, (str, int, float, Decimal, _dt.datetime, _dt.date, bool, type(None))):
                return v
            return str(v)

        wb = Workbook(); ws = wb.active; ws.title = "Report"
        ws.append([meta.get("title", "")]); ws["A1"].font = Font(bold=True, size=14)
        ws.append([meta.get("citation", "")]); ws.append([])
        hdr_row = ws.max_row + 1
        ws.append(cols)
        fill = PatternFill("solid", fgColor="E8EEF5")
        for c in ws[hdr_row]:
            c.font = Font(bold=True); c.fill = fill
        for r in rows:
            ws.append([_cell(r.get(c)) for c in cols])
        buf = io.BytesIO(); wb.save(buf)
        return (buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xlsx")
    raise HTTPException(400, f"unsupported format: {fmt}")


class GenerateIn(BaseModel):
    template_id: str
    format:      str | None = Field(default=None)


@router.post("/generate")
async def generate_report(user: UserCtx = Depends(get_user),
                          body: GenerateIn = Body(...)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        t = await conn.fetchrow("""
            SELECT id, owner, name, schema_name, table_name, columns, filters,
                   order_by, row_limit, default_format
            FROM ops_workspace.report_template
            WHERE id = $1::uuid AND (owner IS NULL OR owner = $2::uuid);
        """, body.template_id, user.kc_sub)
    if t is None:
        raise HTTPException(404, "template not found")
    fmt = (body.format or t["default_format"] or "xlsx").lower()
    if fmt not in _FMT:
        raise HTTPException(400, f"format must be one of {_FMT}")

    schema, table = t["schema_name"], t["table_name"]
    mart = f"{schema}.{table}"

    # OPA mart-export contract: verb 'export' on the mart's subject code, with
    # its data_class, granularity and sensitivity attributes and the caller's
    # scope, so the decision (allow plus row_filter obligation) matches the
    # widget read gate. The subject must be the catalog code, not the
    # schema.table string, which matches no Rego rule.
    try:
        attrs = await mart_attrs.lookup(schema, table)
    except MartAttrsMissing:
        raise HTTPException(422, f"mart {mart} is not in the governance catalog; not reportable")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        scope = await permissions.effective_user_scope(conn, user)
    colclass = await mart_attrs.columns_for_mart(schema, table)
    decision = await opa.decide(user, action="export", resource=attrs.subject,
                                attributes=attrs.opa_attributes, scope=scope,
                                columns={n: c.sensitivity for n, c in colclass.items()})
    if not decision.allow:
        await ops_db.log_policy_decision(
            user=user, action="export", resource=mart, decision="deny",
            rule_hits=decision.rule_hits, explanation={"reason": decision.reason})
        raise HTTPException(403, decision.reason or "denied by policy")

    filters = t["filters"]
    if isinstance(filters, str):
        try: filters = _json.loads(filters)
        except Exception: filters = {}
    filters = dict(filters or {})
    columns = list(t["columns"]) if t["columns"] else None
    order_by = list(t["order_by"]) if t["order_by"] else None
    row_limit = int(t["row_limit"] or 5000)

    # Enforce the decision at row and column level: AND OPA's row_filter into
    # the WHERE (mapped to the mart's columns) and drop masked columns.
    martcols = await _mart_columns(schema, table)
    for dim, vals in (decision.row_filter or {}).items():
        if not vals:
            continue
        col = next((c for c in columns_for(dim) if c in martcols), None)
        if col:
            filters[col] = list(vals)
    # Remove only drop-class masked columns before the query; other mask
    # classes stay in the SELECT and are transformed after fetch (as in
    # exports.py).
    masked = {c for c in (decision.masked_columns or [])
              if colclass.get(c) is None or colclass[c].mask_class == "drop"}
    if masked:
        base = columns or sorted(martcols)
        columns = [c for c in base if c not in masked]

    export_id = await ops_db.register_export(
        user=user, mart_scope=[mart], sql_text=None,
        filter_envelope={"filters": filters or {}, "scope": user.scope_dict(),
                         "report_template": str(t["id"]), "format": fmt},
        policy_decision={"allow": True, "rule_hits": decision.rule_hits,
                         "obligations": decision.obligations,
                         "masked_columns": decision.masked_columns},
        format=fmt, retention_ttl_days=90)
    await ops_db.log_policy_decision(
        user=user, action="export", resource=mart, decision="allow",
        rule_hits=decision.rule_hits, explanation={"obligations": decision.obligations},
        correlation_id=export_id)

    today = dt.date.today()
    minio_key = f"user={user.kc_sub or 'anon'}/{today:%Y/%m/%d}/report-{export_id}.{fmt}"
    try:
        result = await trino_client.fetch_mart(
            schema=schema, table=table, columns=columns,
            where=(filters or None), order_by=order_by, limit=row_limit)
        if decision.masked_columns:
            result["rows"], _dropped = masking.apply_masking(
                result["rows"], colclass, decision.masked_columns)
            if _dropped:
                result["columns"] = [c for c in result.get("columns", [])
                                     if c not in set(_dropped)]
        meta = {"title": t["name"],
                "citation": (f"NHDW, {mart}, generated by "
                             f"{user.username or user.kc_sub} on {today.isoformat()}")}
        body_bytes, content_type, ext = _render_report(result, fmt, meta)
        _etag, sha256, byte_count = await minio_client.write_export(
            key=minio_key, body=body_bytes, content_type=content_type)
        await ops_db.finalize_export(
            export_id=export_id, status="ready", minio_key=minio_key,
            row_count=result["row_count"], byte_count=byte_count, sha256=sha256,
            citation_blurb=meta["citation"])
        url = await minio_client.presign_get(
            key=minio_key, filename=f"{_slug(t['name'])}-{today.isoformat()}.{ext}")
    except HTTPException:
        await ops_db.finalize_export(export_id=export_id, status="failed",
                                     error_msg="render/query error")
        raise
    except Exception as exc:  # noqa: BLE001
        await ops_db.finalize_export(export_id=export_id, status="failed",
                                     error_msg=str(exc)[:1000])
        raise HTTPException(500, f"report generation failed: {exc}")
    return {"report_id": export_id, "name": t["name"], "mart": mart,
            "format": fmt, "row_count": result["row_count"], "download_url": url}
