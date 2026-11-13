"""The explicit list of (schema, table) pairs the ingestor extracts from
HCMISWCloud, each with an optional incremental cursor column.

Maintaining an explicit list (vs reflecting every table in the DB) means:
- We don't accidentally pull HangFire / Temp / dbo.AspNet* internal tables
- A new table appearing in the source won't silently enter the warehouse
- The diff on this file *is* the schema-change audit trail

Every table is full-refresh (extract.py replaces each table on every run). The
`cursor` field records the column an incremental load would use; extract.py
does not read it.
"""
from __future__ import annotations

from typing import NamedTuple


class TableSpec(NamedTuple):
    schema: str
    table: str
    cursor: str | None = None  # column for dlt's incremental; None = full-refresh

    @property
    def dlt_name(self) -> str:
        """The dlt table name (and S3 object slot). Snake_case, schema-prefixed."""
        return f"{self.schema}_{self.table}".lower()


TABLES: list[TableSpec] = [
    # Administrative geography
    TableSpec("AdministrativeUnit", "AdministrativeUnit"),
    TableSpec("dbo", "Region"),
    TableSpec("dbo", "Zone"),
    TableSpec("dbo", "Woreda"),
    TableSpec("dbo", "Institution"),

    # Commodity master
    TableSpec("Commodity", "CommodityType"),
    TableSpec("Commodity", "Product"),

    # Institution / ownership
    TableSpec("Institution", "OwnershipType"),

    # User-management / environments
    TableSpec("USM", "Environment"),
    TableSpec("USM", "EnvironmentGroup"),

    # mBrana operational
    TableSpec("mBrana", "InstitutionEnvironment"),
    TableSpec("mBrana", "SiteStatus"),
    TableSpec("mBrana", "TrendStatus"),

    # mFlowUSSD (the USSD mobile workflow)
    TableSpec("mFlowUSSD", "Users"),
    TableSpec("mFlowUSSD", "Institutions"),
    TableSpec("mFlowUSSD", "Commodities"),
    TableSpec("mFlowUSSD", "Requests"),
    TableSpec("mFlowUSSD", "RequestLines"),
    TableSpec("mFlowUSSD", "ASNs"),
    TableSpec("mFlowUSSD", "ASNLines"),
    TableSpec("mFlowUSSD", "SOHLogs"),
]
