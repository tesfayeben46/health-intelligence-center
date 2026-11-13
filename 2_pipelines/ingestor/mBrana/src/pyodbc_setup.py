"""Register pyodbc output converters for SQL Server-specific types that pyodbc
doesn't know how to decode out of the box.

Imported eagerly from src/__init__.py so the listeners are active before any
dlt or sqlalchemy code runs.

Affected types we've seen in mBrana's HCMISWCloud schema:
    -151  hierarchyid   AdministrativeUnit.AdministrativeUnit.Hierarchy
                        dbo.AdministrativeUnit.Hierarchy
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine

# pyodbc type codes for Microsoft-extended SQL Server types.
SQL_SS_HIERARCHYID = -151
SQL_SS_GEOMETRY    = -150
SQL_SS_GEOGRAPHY   = -152

_REGISTRY = {
    SQL_SS_HIERARCHYID: lambda v: v.hex() if isinstance(v, (bytes, bytearray)) else (str(v) if v is not None else None),
    SQL_SS_GEOMETRY:    lambda v: v.hex() if isinstance(v, (bytes, bytearray)) else (str(v) if v is not None else None),
    SQL_SS_GEOGRAPHY:   lambda v: v.hex() if isinstance(v, (bytes, bytearray)) else (str(v) if v is not None else None),
}


@event.listens_for(Engine, "connect")
def _register_converters(dbapi_conn, _connection_record) -> None:
    # Only pyodbc connections expose add_output_converter; guard for others.
    add = getattr(dbapi_conn, "add_output_converter", None)
    if not add:
        return
    for type_code, fn in _REGISTRY.items():
        add(type_code, fn)
