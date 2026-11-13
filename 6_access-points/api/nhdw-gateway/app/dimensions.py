"""Single registry of scope (ABAC) dimensions.

Holds the three mappings every caller needs:
  - dimension -> candidate mart columns (row-filter application in reports,
    exports and fetchers)
  - filter_id -> dimension (filter value caps in permissions.py)
  - dimension -> OPA input.context key (opa.py)

A scope dimension is a data-partitioning axis a user or role can be narrowed
on. Which mart a caller may query is an OPA subject and granularity decision,
not a row filter, so `mart` is not a dimension. Row enforcement maps a
dimension to the first candidate column present in the target table.

Dimension knowledge lives only here: modules that need one of these mappings
import it from this module and never re-declare it.
"""
from __future__ import annotations

# Canonical row-filterable scope dimensions. Order is stable for iteration.
DIMENSIONS: tuple[str, ...] = ("geography", "programme", "facility")

# dimension -> ordered candidate mart columns (the first present in the mart
# wins). Keys are singular because OPA emits only singular dimension keys in
# row_filter.
DIM_COLUMNS: dict[str, list[str]] = {
    "geography": ["region_id", "region", "woreda_uid", "woreda_name"],
    "programme": ["programme_id", "programme", "program", "program_id"],
    "facility":  ["facility_id", "facility_uid", "facility"],
    # 'commodity' is a plain UI narrowing dimension, like 'period'. It is absent
    # from FILTER_DIMENSION below because users are never commodity-scoped, so it
    # is always a wildcard and narrows only through the fetchers' commodity_col
    # WHERE clause and the generic row_filter application.
    "commodity": ["commodity_name", "commodity", "commodity_code"],
}

# filter-catalog id -> the scope dimension it narrows (role_scope.dimension,
# user_scope.kind). Used to cap a filter's pickable values by
# role_scope ∩ user_scope. Includes dimensions that scope workshop widgets
# (artifact_tier, workshop_group); they cap filter values but are not in
# DIM_COLUMNS because they never become a row_filter on a mart column. A filter
# absent here (e.g. 'period', 'project') has no scope dimension and is always a
# wildcard.
FILTER_DIMENSION: dict[str, str] = {
    "region":         "geography",
    "woreda":         "geography",
    "programme":      "programme",
    "facility":       "facility",
    "artifact_tier":  "artifact_tier",
    "workshop_group": "workshop_group",
}

# dimension -> the key OPA expects in input.context. The plural spellings are
# part of the policy contract in common.rego.
OPA_CONTEXT_KEY: dict[str, str] = {
    "geography": "geographies",
    "programme": "programs",
    "facility":  "facilities",
}


def columns_for(dimension: str) -> list[str]:
    """Candidate mart columns for a row_filter dimension (the first present wins).
    An unknown key falls back to [dimension], so callers need no None handling."""
    return DIM_COLUMNS.get(dimension, [dimension])
