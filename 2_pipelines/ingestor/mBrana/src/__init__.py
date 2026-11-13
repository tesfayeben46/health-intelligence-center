# Eagerly register pyodbc output converters for SQL Server-specific types
# (hierarchyid etc) that the standard pyodbc decoder doesn't handle.
from . import pyodbc_setup  # noqa: F401
