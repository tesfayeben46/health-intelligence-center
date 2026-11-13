"""Direct-MySQL OpenMRS reader that reshapes rows into FHIR-shaped dicts.

Real EMRs expose a CDC/JDBC surface, so instead of standing up the OpenMRS web
app just to serve FHIR, we read the clinical tables straight from MySQL and
rebuild the *same* FHIR R4 resource shapes the downstream staging + vault
models already parse. dlt then flattens each nested dict with its default
snake_case + ``__`` naming convention, producing byte-compatible columns:

    FHIR JSON key                     dlt / parquet column
    id                                id
    meta.versionId                    meta__version_id
    meta.lastUpdated                  meta__last_updated
    meta.source                       meta__source
    subject.reference                 subject__reference
    subject.identifier.system         subject__identifier__system
    valueQuantity.value               value_quantity__value
    effectiveDateTime                 effective_date_time
    ...

Two design rules that keep the parquet schema stable and match the "raw surface
is all-string" contract:

  1. Every FHIR resource is emitted with the *full* leaf skeleton on every row:
     even when a field is absent we emit "" (empty string), never a Python
     ``None`` and never a bare missing key. dlt drops columns that are NULL in
     every row; the raw ClickHouse views are ``SELECT * FROM s3(...parquet)``,
     so a dropped column would make the staging model reference a non-existent
     identifier. Emitting "" guarantees the column exists.
  2. Every leaf value is a *string*. Numeric/date typing happens downstream
     (vault/marts cast explicitly), mirroring what a real dlt ingestor lands.

Identity mapping: OpenMRS `uuid` -> FHIR `id`. References use FHIR form
("Patient/<person.uuid>", "Encounter/<encounter.uuid>") so the vault's links
resolve as they would against a FHIR server.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Iterator

import pymysql
from pymysql.cursors import SSDictCursor

log = logging.getLogger(__name__)

# OpenMRS administrative gender code -> FHIR AdministrativeGender.
_GENDER = {"M": "male", "F": "female", "O": "other", "U": "unknown"}

# FHIR Encounter.class default for an ambulatory OpenMRS encounter, mirroring the
# OpenMRS FHIR2 module's EncounterTranslator default coding.
_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_CLASS_CODE = "AMB"
_CLASS_DISPLAY = "ambulatory"


def _s(v) -> str:
    """Coerce any MySQL value to a string leaf; NULL/None -> "" (empty)."""
    if v is None:
        return ""
    if isinstance(v, _dt.datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(v, _dt.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def _instant(v) -> str:
    """FHIR instant/dateTime string, or "" when absent."""
    if v is None:
        return ""
    if isinstance(v, _dt.datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%S")
    return _s(v)


def _date(v) -> str:
    """FHIR date (YYYY-MM-DD) string, or "" when absent."""
    if v is None:
        return ""
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime("%Y-%m-%d")
    return _s(v)


def _last_updated(row: dict) -> str:
    """meta.lastUpdated = coalesce(date_changed, date_created).

    date_changed is NULL on synthetic rows, so date_created is the fallback and
    also the incremental cursor.
    """
    return _instant(row.get("date_changed") or row.get("date_created"))


# A reused correlated-subquery fragment: pick one preferred person_name row.
_PREFERRED_NAME = """
    LEFT JOIN person_name pn ON pn.person_name_id = (
        SELECT x.person_name_id FROM person_name x
        WHERE x.person_id = {person} AND x.voided = 0
        ORDER BY x.preferred DESC, x.person_name_id ASC LIMIT 1)
"""


def _display_name(row: dict) -> str:
    parts = [row.get("given_name"), row.get("family_name")]
    name = " ".join(p for p in parts if p)
    return name.strip()


class OpenMRSReader:
    """Streams OpenMRS clinical tables and yields FHIR-shaped resource dicts."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.batch_size = max(1, cfg.mysql_batch_size)

    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.cfg.mysql_host,
            port=self.cfg.mysql_port,
            user=self.cfg.mysql_user,
            password=self.cfg.mysql_password,
            database=self.cfg.mysql_database,
            charset="utf8mb4",
            connect_timeout=self.cfg.mysql_timeout,
            read_timeout=self.cfg.mysql_timeout * 10,
            cursorclass=SSDictCursor,  # server-side, unbuffered: obs is large
        )

    def _stream(self, sql: str, params: tuple) -> Iterator[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                while True:
                    rows = cur.fetchmany(self.batch_size)
                    if not rows:
                        break
                    for row in rows:
                        yield row
        finally:
            conn.close()

    # Patient
    def iter_patients(self, since: str, strict: bool = False) -> Iterator[dict]:
        # strict=True on incremental re-runs: > excludes the stored boundary
        # (no re-reads, no dups); first run uses >= the configured floor.
        op = ">" if strict else ">="
        sql = f"""
            SELECT
                p.uuid          AS person_uuid,
                p.gender        AS gender,
                p.birthdate     AS birthdate,
                p.date_created  AS date_created,
                p.date_changed  AS date_changed,
                pn.given_name   AS given_name,
                pn.family_name  AS family_name
            FROM patient pt
            JOIN person p ON p.person_id = pt.patient_id
            {_PREFERRED_NAME.format(person="p.person_id")}
            WHERE pt.voided = 0 AND p.voided = 0 AND p.date_created {op} %s
        """
        for row in self._stream(sql, (since,)):
            name = _display_name(row)
            yield {
                "resourceType": "Patient",
                "id": _s(row["person_uuid"]),
                "meta": {
                    "versionId": "1",
                    "lastUpdated": _last_updated(row),
                    "source": "",
                },
                "gender": _GENDER.get((row.get("gender") or "").upper(), "unknown"),
                "birthDate": _date(row.get("birthdate")),
                # OpenMRS has no FHIR narrative; synthesise a minimal generated
                # div so the text__status / text__div columns always materialise.
                "text": {
                    "status": "generated",
                    "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{name or "Patient"}</div>',
                },
            }

    # Encounter
    def iter_encounters(self, since: str, strict: bool = False) -> Iterator[dict]:
        # strict=True on incremental re-runs: > excludes the stored boundary
        # (no re-reads, no dups); first run uses >= the configured floor.
        op = ">" if strict else ">="
        sql = f"""
            SELECT
                e.uuid               AS encounter_uuid,
                e.encounter_datetime AS encounter_datetime,
                e.date_created       AS date_created,
                e.date_changed       AS date_changed,
                p.uuid               AS person_uuid,
                pn.given_name        AS given_name,
                pn.family_name       AS family_name,
                v.uuid               AS visit_uuid,
                v.date_stopped       AS visit_stopped,
                loc.uuid             AS location_uuid,
                loc.name             AS location_name,
                pid.identifier       AS identifier_value,
                pit.name             AS identifier_type
            FROM encounter e
            JOIN person p ON p.person_id = e.patient_id
            LEFT JOIN visit v    ON v.visit_id     = e.visit_id
            LEFT JOIN location loc ON loc.location_id = e.location_id
            {_PREFERRED_NAME.format(person="e.patient_id")}
            LEFT JOIN patient_identifier pid ON pid.patient_identifier_id = (
                SELECT x.patient_identifier_id FROM patient_identifier x
                WHERE x.patient_id = e.patient_id AND x.voided = 0
                ORDER BY x.preferred DESC, x.patient_identifier_id ASC LIMIT 1)
            LEFT JOIN patient_identifier_type pit
                   ON pit.patient_identifier_type_id = pid.identifier_type
            WHERE e.voided = 0 AND e.date_created {op} %s
        """
        for row in self._stream(sql, (since,)):
            name = _display_name(row)
            visit_uuid = row.get("visit_uuid")
            visit_stopped = row.get("visit_stopped")
            loc_uuid = _s(row.get("location_uuid"))
            yield {
                "resourceType": "Encounter",
                "id": _s(row["encounter_uuid"]),
                "meta": {
                    "versionId": "1",
                    "lastUpdated": _last_updated(row),
                    "source": "",
                },
                # OpenMRS encounters are completed point-in-time events; open
                # visits (no stop) are still in progress.
                "status": "in-progress" if (visit_uuid and not visit_stopped) else "finished",
                "class": {
                    "system": _CLASS_SYSTEM,
                    "code": _CLASS_CODE,
                    "display": _CLASS_DISPLAY,
                },
                "subject": {
                    "reference": f"Patient/{_s(row['person_uuid'])}",
                    "type": "Patient",
                    "display": name,
                    "identifier": {
                        "system": _s(row.get("identifier_type")),
                        "value": _s(row.get("identifier_value")),
                    },
                },
                "period": {
                    "start": _instant(row.get("encounter_datetime")),
                    "end": _instant(visit_stopped),
                },
                # FHIR2 nests an OpenMRS encounter under its visit-as-Encounter.
                "partOf": {
                    "reference": f"Encounter/{_s(visit_uuid)}" if visit_uuid else "",
                },
                "serviceProvider": {
                    "reference": f"Organization/{loc_uuid}" if loc_uuid else "",
                    "display": _s(row.get("location_name")),
                    "identifier": {
                        "system": "",
                        "value": loc_uuid,
                    },
                },
            }

    # Observation
    def iter_observations(self, since: str, strict: bool = False) -> Iterator[dict]:
        # strict=True on incremental re-runs: > excludes the stored boundary
        # (no re-reads, no dups); first run uses >= the configured floor.
        op = ">" if strict else ">="
        sql = f"""
            SELECT
                o.uuid          AS obs_uuid,
                o.obs_datetime  AS obs_datetime,
                o.date_created  AS date_created,
                o.value_numeric AS value_numeric,
                o.value_text    AS value_text,
                o.value_datetime AS value_datetime,
                o.value_coded   AS value_coded,
                p.uuid          AS person_uuid,
                pn.given_name   AS given_name,
                pn.family_name  AS family_name,
                e.uuid          AS encounter_uuid,
                cnum.units      AS units,
                cn.name         AS concept_name,
                vcn.name        AS value_coded_name
            FROM obs o
            JOIN person p ON p.person_id = o.person_id
            LEFT JOIN encounter e ON e.encounter_id = o.encounter_id
            {_PREFERRED_NAME.format(person="o.person_id")}
            LEFT JOIN concept_numeric cnum ON cnum.concept_id = o.concept_id
            LEFT JOIN concept_name cn ON cn.concept_name_id = (
                SELECT x.concept_name_id FROM concept_name x
                WHERE x.concept_id = o.concept_id AND x.voided = 0
                ORDER BY (x.concept_name_type = 'FULLY_SPECIFIED') DESC,
                         x.locale_preferred DESC, x.concept_name_id ASC LIMIT 1)
            LEFT JOIN concept_name vcn ON vcn.concept_name_id = (
                SELECT x.concept_name_id FROM concept_name x
                WHERE x.concept_id = o.value_coded AND x.voided = 0
                ORDER BY (x.concept_name_type = 'FULLY_SPECIFIED') DESC,
                         x.locale_preferred DESC, x.concept_name_id ASC LIMIT 1)
            WHERE o.voided = 0 AND o.date_created {op} %s
        """
        for row in self._stream(sql, (since,)):
            name = _display_name(row)
            enc_uuid = row.get("encounter_uuid")

            # FHIR Observation.value[x] is polymorphic; OpenMRS stores exactly
            # one value_* per obs. Emit all leaves every row ("" when unused) so
            # the columns stay stable, and fill the one that applies.
            vq_value = vq_unit = ""
            vcc_text = value_string = value_dt = ""
            if row.get("value_numeric") is not None:
                vq_value = _s(row["value_numeric"])
                vq_unit = _s(row.get("units"))
            elif row.get("value_coded") is not None:
                vcc_text = _s(row.get("value_coded_name"))
            elif row.get("value_datetime") is not None:
                value_dt = _instant(row.get("value_datetime"))
            elif row.get("value_text") is not None:
                value_string = _s(row.get("value_text"))

            yield {
                "resourceType": "Observation",
                "id": _s(row["obs_uuid"]),
                "meta": {
                    "versionId": "1",
                    "lastUpdated": _last_updated(row),
                    "source": "",
                },
                "status": "final",
                "code": {"text": _s(row.get("concept_name"))},
                "subject": {
                    "reference": f"Patient/{_s(row['person_uuid'])}",
                    "display": name,
                },
                "encounter": {
                    "reference": f"Encounter/{_s(enc_uuid)}" if enc_uuid else "",
                },
                "effectiveDateTime": _instant(row.get("obs_datetime")),
                # OpenMRS obs are point-in-time; keep the period leaves present.
                "effectivePeriod": {"start": "", "end": ""},
                "issued": _instant(row.get("date_created")),
                "valueQuantity": {
                    "value": vq_value,
                    "unit": vq_unit,
                    "system": "",
                    "code": "",
                },
                "valueCodeableConcept": {"text": vcc_text},
                "valueString": value_string,
                "valueDateTime": value_dt,
                "method": {"text": ""},
            }
