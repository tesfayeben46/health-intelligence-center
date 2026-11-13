"""Cached mart catalog that supplies OPA input attributes on every mart read.

OPA's per-role policies branch on `subject` codes (PT, HP, FAC, PRD, PRG) and
on `data_class`, `granularity` and `sensitivity`. A table name such as
`marts.bi_hiv_cascade_total` carries none of these, so the gateway looks the
mart up here before calling OPA. A mart that is not catalogued is denied under
OPA_ENFORCE=true.

The cache loads on first lookup and is invalidated by the admin UI after a
catalog edit; a process restart also picks up edits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import ops_db

log = logging.getLogger("gateway.mart_attrs")


@dataclass(frozen=True)
class MartAttrs:
    schema: str
    table: str
    subject: str               # PT | HP | FAC | PRD | PRG  (OPA resource.subject)
    data_class: str            # DC.ENC | DC.PRO | ...
    granularity: str           # raw | aggregate | deidentified
    sensitivity: str           # default | restricted | privileged

    @property
    def opa_attributes(self) -> dict[str, str]:
        """Spread-ready dict for opa.decide(attributes=...)."""
        return {
            "data_class":  self.data_class,
            "granularity": self.granularity,
            "sensitivity": self.sensitivity,
        }


@dataclass(frozen=True)
class ColumnClass:
    """One classified column (ops_perm.column_classification). The store is a
    denylist: a column with no row is 'default' (visible). Rows exist only for
    columns that need protection, keyed on the dbt relation names."""
    column: str
    sensitivity: str           # default | restricted | privileged
    mask_class: str            # none | drop | hash | partial | generalize | tokenize
    pii: bool


class MartAttrsMissing(Exception):
    """Raised when a mart is not catalogued, so OPA cannot be applied to it."""
    def __init__(self, schema: str, table: str):
        super().__init__(f"mart_attrs catalog has no entry for {schema}.{table}; "
                         f"add a row to bundle/seed-pack/app/security/mart_attrs.yaml")
        self.schema = schema
        self.table  = table


_cache: dict[tuple[str, str], MartAttrs] | None = None


async def _load_cache() -> dict[tuple[str, str], MartAttrs]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT schema_name, table_name, subject, data_class,
                   granularity, default_sensitivity
            FROM ops_perm.mart_attrs
        """)
    return {
        (r["schema_name"], r["table_name"]): MartAttrs(
            schema=r["schema_name"], table=r["table_name"],
            subject=r["subject"], data_class=r["data_class"],
            granularity=r["granularity"], sensitivity=r["default_sensitivity"],
        ) for r in rows
    }


async def lookup(schema: str, table: str) -> MartAttrs:
    """Cache-first lookup. Raises MartAttrsMissing if absent."""
    global _cache
    if _cache is None:
        _cache = await _load_cache()
        log.info("mart_attrs cache loaded, %d entries", len(_cache))
    hit = _cache.get((schema, table))
    if not hit:
        raise MartAttrsMissing(schema, table)
    return hit


async def catalog_keys() -> set[tuple[str, str]]:
    """Every (schema, table) in the catalog, i.e. the queryable marts. A mart
    absent here gets 403 from the SQL executor and get_mart under OPA_ENFORCE.
    The mart browser lists exactly this set, so it never offers an
    uncatalogued intermediate table."""
    global _cache
    if _cache is None:
        _cache = await _load_cache()
        log.info("mart_attrs cache loaded, %d entries", len(_cache))
    return set(_cache.keys())


_col_cache: dict[tuple[str, str], dict[str, ColumnClass]] | None = None


async def _load_col_cache() -> dict[tuple[str, str], dict[str, ColumnClass]]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT schema_name, table_name, column_name, sensitivity, mask_class, pii
            FROM ops_perm.column_classification
        """)
    out: dict[tuple[str, str], dict[str, ColumnClass]] = {}
    for r in rows:
        out.setdefault((r["schema_name"], r["table_name"]), {})[r["column_name"]] = \
            ColumnClass(column=r["column_name"], sensitivity=r["sensitivity"],
                        mask_class=r["mask_class"], pii=r["pii"])
    return out


async def columns_for_mart(schema: str, table: str) -> dict[str, ColumnClass]:
    """The classified columns of one relation ({} when none are classified,
    per the denylist). The caller sends {name: sensitivity} to OPA, which
    decides the masked_columns, then applies each masked column's mask_class
    via app/masking.py.

    The schema is normalised to its bare name here. Callers hold it either as
    the bare `bi_supply` that ops_perm.column_classification keys on or as the
    Trino-qualified `marts.bi_supply`. Because the store is a denylist, a key
    mismatch would return {} and silently serve every classified column in
    clear, so normalisation is done once here rather than by each caller."""
    global _col_cache
    if _col_cache is None:
        _col_cache = await _load_col_cache()
        log.info("column_classification cache loaded, %d relations, %d columns",
                 len(_col_cache), sum(len(v) for v in _col_cache.values()))
    bare = schema.rsplit(".", 1)[-1] if "." in schema else schema
    if bare != schema:
        log.warning("columns_for_mart: catalog-qualified schema %r normalised "
                    "to %r; pass the bare schema name", schema, bare)
    return _col_cache.get((bare, table), {})


async def invalidate() -> None:
    """Force the next lookup to re-read both catalogs (mart attributes and
    column classification). Called by the admin UI after an edit and by the
    seed reload path."""
    global _cache, _col_cache
    _cache = None
    _col_cache = None
    log.info("mart_attrs + column_classification caches invalidated")


# Startup self-test
@dataclass
class SelfTestCase:
    """A (role, mart) pair with an expected allow or deny verdict. The gateway
    runs these at startup and fails fast under OPA_ENFORCE=true if OPA
    disagrees, which catches drift between the catalog and the Rego policy."""
    role: str
    schema: str
    table: str
    expect_allow: bool
    note: str = ""


# Positive and negative cases per role. Every role is covered, so drift
# between the catalog and the Rego policy on any role fails the startup gate
# before traffic is served.
SELF_TESTS: list[SelfTestCase] = [
    SelfTestCase("R-DEC", "marts", "bi_programme_hiv_cascade_total", True,
                 "R-DEC reads PRG aggregate"),
    SelfTestCase("R-DEC", "vault_business", "enc_patient_visit", False,
                 "R-DEC must NOT read raw PT encounters"),

    SelfTestCase("R-PUB-ANON", "marts", "bi_service_flagship_attainment", True,
                 "R-PUB-ANON reads public programme indicators"),
    SelfTestCase("R-PUB-ANON", "marts", "bi_safety_aefi_lineage", False,
                 "R-PUB-ANON must NOT read AEFI restricted lineage"),

    SelfTestCase("R-CONS-ANA", "marts", "bi_programme_hiv_cascade_total", True,
                 "R-CONS-ANA reads PRG aggregate"),
    SelfTestCase("R-CONS-ANA", "vault_business", "enc_patient_visit", False,
                 "R-CONS-ANA must NOT read raw PT encounters (deid only)"),

    SelfTestCase("R-PROD-SUBMIT", "marts", "bi_programme_hiv_cascade_total", False,
                 "R-PROD-SUBMIT is write-only on submissions; mart read denied"),

    SelfTestCase("R-OPS-ENG", "marts", "bi_programme_hiv_cascade_total", False,
                 "R-OPS-ENG operates on infra; data-plane denied"),
    SelfTestCase("R-OPS-SEC", "marts", "bi_programme_hiv_cascade_total", False,
                 "R-OPS-SEC reads audit/observability; production marts denied"),

    # R-SUPER reads everything.
    SelfTestCase("R-SUPER", "marts", "bi_programme_hiv_cascade_total", True,
                 "R-SUPER wildcard access"),
    SelfTestCase("R-SUPER", "vault_business", "enc_patient_visit", True,
                 "R-SUPER reads even raw PT encounters"),

    # Roles that are deny-all on the data plane by design.
    SelfTestCase("R-STEW",      "marts", "bi_programme_hiv_cascade_total", False,
                 "R-STEW intentional deny-all on data plane"),
    SelfTestCase("R-OPS-ADM",   "marts", "bi_programme_hiv_cascade_total", False,
                 "R-OPS-ADM intentional deny-all on data plane"),
    SelfTestCase("R-CONS-SCI",  "marts", "bi_programme_hiv_cascade_total", False,
                 "R-CONS-SCI uses workshop sandbox; prod marts denied"),
    SelfTestCase("R-CONS-RES",  "marts", "bi_programme_hiv_cascade_total", False,
                 "R-CONS-RES requires k-anon public marts (not in v1)"),
    SelfTestCase("R-CONS-PRT",  "marts", "bi_programme_hiv_cascade_total", True,
                 "R-CONS-PRT (partner/donor) reads PRG aggregate; DC.PRO+DC.CAT "
                 "aggregate grant, #69/I-8 (was deny-all husk); raw/deid stay denied"),
    SelfTestCase("R-CONS-PRT",  "vault_business", "enc_patient_visit", False,
                 "R-CONS-PRT aggregate grant is scoped; raw PT encounters denied"),
    SelfTestCase("R-PROD-CLIN", "marts", "bi_programme_hiv_cascade_total", False,
                 "R-PROD-CLIN uses SMART-on-FHIR per-patient; bulk denied"),
]


async def run_self_test(opa_module) -> list[tuple[SelfTestCase, bool, str]]:
    """Returns [(case, observed_allow, reason)]. The caller decides whether to
    fail fast (OPA_ENFORCE=true) or only log warnings."""
    from .auth import UserCtx
    results: list[tuple[SelfTestCase, bool, str]] = []
    for case in SELF_TESTS:
        try:
            attrs = await lookup(case.schema, case.table)
        except MartAttrsMissing as e:
            results.append((case, False, f"catalog miss: {e}"))
            continue
        u = UserCtx(
            kc_sub="00000000-0000-0000-0000-000000000000",
            username="self-test", realm="test",
            roles=[case.role], primary_role=case.role,
            geographies=[], programs=[], purpose_of_use="public-health",
            email="", full_name="",
        )
        d = await opa_module.decide(
            u, action="read", resource=attrs.subject,
            attributes=attrs.opa_attributes,
        )
        observed = bool(d.allow) and not d.degraded
        # Under OPA_ENFORCE=false the gateway turns a deny into an allow with
        # an "[advisory]" tag; count that as a deny when judging the case.
        if "[enforce-off]" in (d.reason or "") or "[advisory]" in (d.reason or ""):
            observed = False
        results.append((case, observed, d.reason or ""))
    return results


# Startup masking self-test
@dataclass
class MaskSelfTestCase:
    """A (role, classified columns) pair with the masked set expected from the
    live OPA. Catches drift between reveal sets and the Rego policy, as the
    allow/deny matrix above does for table-level policy."""
    role: str
    idp_trust: str
    columns: dict[str, str]            # {column: sensitivity} as the gateway sends it
    expect_masked: list[str]           # sorted
    note: str = ""


_MASK_FIXTURE = {"lat": "restricted", "lng": "restricted", "patient_id": "privileged"}

MASK_SELF_TESTS: list[MaskSelfTestCase] = [
    MaskSelfTestCase("R-CONS-ANA", "high", _MASK_FIXTURE, ["patient_id"],
                     "analyst reveals restricted (maps plot GPS); privileged masked"),
    MaskSelfTestCase("R-PD-HIV", "high", _MASK_FIXTURE, ["patient_id"],
                     "R-DEC alias inherits the r-dec reveal set"),
    MaskSelfTestCase("R-PUB-ANON", "high", _MASK_FIXTURE,
                     ["lat", "lng", "patient_id"],
                     "no reveal grant; every classified column masked"),
    MaskSelfTestCase("R-OPS-SEC", "high", _MASK_FIXTURE, [],
                     "security officer reveals restricted AND privileged"),
    MaskSelfTestCase("R-SUPER", "high", _MASK_FIXTURE, [],
                     "wildcard reveals everything"),
    MaskSelfTestCase("R-CONS-ANA", "low", _MASK_FIXTURE,
                     ["lat", "lng", "patient_id"],
                     "low-trust identity never sees a classified column clear"),
]


async def run_mask_self_test(opa_module) -> list[tuple[MaskSelfTestCase, list[str], str]]:
    """Returns [(case, observed_masked_sorted, reason)]. Uses a fixture mart
    the analyst role can read; the allow verdict does not matter because OPA
    returns masked_columns on any decision not denied by the trust guard."""
    from .auth import UserCtx
    results: list[tuple[MaskSelfTestCase, list[str], str]] = []
    for case in MASK_SELF_TESTS:
        u = UserCtx(
            kc_sub="00000000-0000-0000-0000-000000000000",
            username="mask-self-test", realm="test",
            roles=[case.role], primary_role=case.role,
            geographies=[], programs=[], purpose_of_use="public-health",
            email="", full_name="", idp_trust=case.idp_trust,
        )
        d = await opa_module.decide(
            u, action="read", resource="PRD",
            attributes={"data_class": "DC.STK", "granularity": "aggregate",
                        "sensitivity": "default"},
            columns=case.columns,
        )
        if d.degraded:
            results.append((case, ["<opa-degraded>"], d.reason or ""))
            continue
        results.append((case, sorted(d.masked_columns), d.reason or ""))
    return results
