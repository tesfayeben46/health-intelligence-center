"""NHDW FHIR-profile validator and CSV-to-FHIR transformer.

Sits between OpenHIM and HAPI. Each dataset has its own validator:

    POST /validate/hmis    (text/csv)   -> validate, transform to FHIR Bundle -> HAPI
    POST /validate/epi     (application/fhir+json Bundle) -> validate, forward to HAPI
    POST /validate/stock   (text/csv)   -> validate, transform to FHIR Bundle -> HAPI

Transform:
    HMIS row  -> fhir.Observation (status=final, category=public-health, code=indicator,
                effectivePeriod from period, valueQuantity from value)
    Stock row -> fhir.Observation (status=final, category=therapy, code=drug_code,
                effectiveDateTime=week_ending, valueQuantity from on_hand_units)
    Each transformed Bundle posts to HAPI as a `transaction` and the
    response is relayed back to the caller.

On failure: 422 with a FHIR OperationOutcome payload.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import ssl
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("fhir-mediator")

HAPI_BASE = os.environ.get("HAPI_BASE", "http://nhdw-fhir-smart-config:80")

app = FastAPI(title="NHDW FHIR Profile Mediator")


def _outcome(severity: str, code: str, diagnostics: str) -> dict[str, Any]:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": severity, "code": code, "diagnostics": diagnostics}],
    }


# EPI: FHIR Bundle validation


def _validate_epi_bundle(body: bytes) -> tuple[bool, str]:
    try:
        d = json.loads(body)
    except json.JSONDecodeError as e:
        return False, f"body is not valid JSON: {e}"
    if d.get("resourceType") != "Bundle":
        return False, f"expected resourceType=Bundle, got {d.get('resourceType')!r}"
    if d.get("type") not in ("transaction", "batch", "collection"):
        return False, f"Bundle.type must be transaction/batch/collection, got {d.get('type')!r}"
    entries = d.get("entry", [])
    if not entries:
        return False, "Bundle.entry is empty; at least one resource required"
    allowed = {"Patient", "Immunization", "Practitioner", "Location", "Organization"}
    for i, e in enumerate(entries):
        r = (e or {}).get("resource") or {}
        rt = r.get("resourceType")
        if rt not in allowed:
            return False, (f"entry[{i}].resource.resourceType={rt!r} "
                           f"not in allowed set {sorted(allowed)}")
    return True, "ok"


# HMIS: CSV validation

HMIS_REQUIRED_COLS = {"facility", "indicator", "period", "value"}


def _validate_hmis_csv(body: bytes) -> tuple[bool, str, int]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, f"CSV is not UTF-8: {e}", 0
    reader = csv.reader(io.StringIO(text))
    header_row = next(reader, None)
    if not header_row:
        return False, "CSV is empty", 0
    cols = {c.strip().lower() for c in header_row}
    missing = HMIS_REQUIRED_COLS - cols
    if missing:
        return False, f"CSV header missing required columns: {sorted(missing)}", 0
    rows = list(reader)
    return True, "ok", len(rows)


# Stock: CSV validation

STOCK_REQUIRED_COLS = {"drug_code", "on_hand_units", "week_ending"}


def _validate_stock_csv(body: bytes) -> tuple[bool, str, int]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, f"CSV is not UTF-8: {e}", 0
    reader = csv.reader(io.StringIO(text))
    header_row = next(reader, None)
    if not header_row:
        return False, "CSV is empty", 0
    cols = {c.strip().lower() for c in header_row}
    missing = STOCK_REQUIRED_COLS - cols
    if missing:
        return False, f"CSV header missing required columns: {sorted(missing)}", 0
    rows = list(reader)
    return True, "ok", len(rows)


# CSV -> FHIR transformers


def _hmis_csv_to_bundle(body: bytes) -> dict[str, Any]:
    """HMIS CSV rows -> FHIR transaction Bundle of Observations.

    Row shape: facility, indicator, period (YYYY-MM or YYYY-MM-DD), value.
    """
    reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
    entries: list[dict[str, Any]] = []
    for i, row in enumerate(reader):
        period = (row.get("period") or "").strip()
        # period may be "2026-05" or "2026-05-31"; normalise to a Period
        if len(period) == 7:
            start, end = f"{period}-01", f"{period}-31"
        else:
            start = end = period
        try:
            value = float(row.get("value", "") or 0)
        except ValueError:
            value = 0.0
        obs = {
            "resourceType": "Observation",
            "status": "final",
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "public-health-indicator", "display": "Public Health Indicator",
                }],
            }],
            "code": {"coding": [{
                "system": "http://nhdw.moh.gov.et/CodeSystem/hmis-indicator",
                "code": (row.get("indicator") or "unknown").strip(),
            }]},
            "subject": {"display": f"Facility {row.get('facility','').strip()}",
                        "identifier": {
                            "system": "http://nhdw.moh.gov.et/Identifier/facility",
                            "value": (row.get("facility") or "").strip(),
                        }},
            "effectivePeriod": {"start": start, "end": end},
            "valueQuantity": {"value": value, "unit": "count", "system": "http://unitsofmeasure.org"},
            "meta": {"source": "nhdw-mediator/hmis-csv"},
        }
        entries.append({
            "fullUrl": f"urn:uuid:hmis-{i}",
            "resource": obs,
            "request": {"method": "POST", "url": "Observation"},
        })
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def _stock_csv_to_bundle(body: bytes) -> dict[str, Any]:
    """Stock CSV rows -> FHIR transaction Bundle of Observations.

    Row shape: drug_code, on_hand_units, week_ending (YYYY-MM-DD).
    """
    reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
    entries: list[dict[str, Any]] = []
    for i, row in enumerate(reader):
        try:
            units = int(row.get("on_hand_units", "") or 0)
        except ValueError:
            units = 0
        obs = {
            "resourceType": "Observation",
            "status": "final",
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "therapy", "display": "Therapy",
                }],
            }],
            "code": {"coding": [{
                "system": "http://nhdw.moh.gov.et/CodeSystem/drug-code",
                "code": (row.get("drug_code") or "unknown").strip(),
            }]},
            "effectiveDateTime": (row.get("week_ending") or "").strip(),
            "valueQuantity": {"value": units, "unit": "unit", "system": "http://unitsofmeasure.org"},
            "meta": {"source": "nhdw-mediator/stock-csv"},
        }
        entries.append({
            "fullUrl": f"urn:uuid:stock-{i}",
            "resource": obs,
            "request": {"method": "POST", "url": "Observation"},
        })
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


async def _post_bundle(bundle: dict[str, Any]) -> tuple[int, str]:
    body = json.dumps(bundle).encode("utf-8")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{HAPI_BASE}/fhir/", content=body,
                         headers={"Content-Type": "application/fhir+json"})
    return r.status_code, r.text


# Endpoints


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/validate/epi")
async def validate_epi(request: Request) -> Any:
    body = await request.body()
    ok, msg = _validate_epi_bundle(body)
    if not ok:
        return JSONResponse(_outcome("error", "invariant", f"EPI Bundle invalid: {msg}"),
                            status_code=422)
    # Forward the validated Bundle to HAPI at /fhir/
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{HAPI_BASE}/fhir/", content=body,
                         headers={"Content-Type": "application/fhir+json"})
    return JSONResponse(content=json.loads(r.text or "{}"),
                        status_code=r.status_code)


@app.post("/validate/hmis")
async def validate_hmis(request: Request) -> Any:
    body = await request.body()
    ok, msg, nrows = _validate_hmis_csv(body)
    if not ok:
        return JSONResponse(_outcome("error", "invariant", f"HMIS CSV invalid: {msg}"),
                            status_code=422)
    bundle = _hmis_csv_to_bundle(body)
    status, text = await _post_bundle(bundle)
    log.info("HMIS CSV: %d rows -> HAPI transaction status=%s", nrows, status)
    try:
        body_json = json.loads(text or "{}")
    except json.JSONDecodeError:
        body_json = {"resourceType": "OperationOutcome",
                     "issue": [{"severity": "warning", "code": "informational",
                                "diagnostics": f"HAPI responded {status} with non-JSON body"}]}
    return JSONResponse(content=body_json, status_code=status)


@app.post("/validate/stock")
async def validate_stock(request: Request) -> Any:
    body = await request.body()
    ok, msg, nrows = _validate_stock_csv(body)
    if not ok:
        return JSONResponse(_outcome("error", "invariant", f"Stock CSV invalid: {msg}"),
                            status_code=422)
    bundle = _stock_csv_to_bundle(body)
    status, text = await _post_bundle(bundle)
    log.info("Stock CSV: %d rows -> HAPI transaction status=%s", nrows, status)
    try:
        body_json = json.loads(text or "{}")
    except json.JSONDecodeError:
        body_json = {"resourceType": "OperationOutcome",
                     "issue": [{"severity": "warning", "code": "informational",
                                "diagnostics": f"HAPI responded {status} with non-JSON body"}]}
    return JSONResponse(content=body_json, status_code=status)
