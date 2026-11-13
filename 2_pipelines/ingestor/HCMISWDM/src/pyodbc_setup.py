"""Register pyodbc output converters for SQL Server types pyodbc doesn't decode."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine

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
    add = getattr(dbapi_conn, "add_output_converter", None)
    if not add:
        return
    for type_code, fn in _REGISTRY.items():
        add(type_code, fn)
