"""
Lazy re-exports for the PostgreSQL connector so importing the package
doesn't require optional dependencies until a symbol is actually used.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

__all__ = [
    "ConnectionInfo",
    "ConnectionManager",
    "PostgresClient",
    "query_rows",
    "query_scalar",
    "query_namedtuples",
    "query_df",
    "mogrify",
    "stream_rows",
    "stream_to_parquet",
    "execute_batch",
    "copy_rows_in",
    "copy_rows_out",
    "copy_df_in",
    "list_schemas",
    "list_tables",
    "list_columns",
    "schema",
    "schema_batch",
    "explain",
]

# Type checkers get precise symbols without importing heavy deps at runtime.
if TYPE_CHECKING:
    from .client import PostgresClient
    from .connection import ConnectionInfo, ConnectionManager
    from .queries import (
        copy_df_in,
        copy_rows_in,
        copy_rows_out,
        execute_batch,
        mogrify,
        query_df,
        query_namedtuples,
        query_rows,
        query_scalar,
        stream_rows,
        stream_to_parquet,
    )
    from .schema import (  # noqa: F401
        explain,
        list_columns,
        list_schemas,
        list_tables,
        schema,
        schema_batch,
    )

# Minimal lazy re-export map (module_path, attribute_name)
_ATTRS: Final[dict[str, tuple[str, str]]] = {
    "ConnectionInfo": ("agent_data_toolkit.postgresql.connection", "ConnectionInfo"),
    "ConnectionManager": ("agent_data_toolkit.postgresql.connection", "ConnectionManager"),
    "PostgresClient": ("agent_data_toolkit.postgresql.client", "PostgresClient"),
    "query_rows": ("agent_data_toolkit.postgresql.queries", "query_rows"),
    "query_scalar": ("agent_data_toolkit.postgresql.queries", "query_scalar"),
    "query_namedtuples": ("agent_data_toolkit.postgresql.queries", "query_namedtuples"),
    "query_df": ("agent_data_toolkit.postgresql.queries", "query_df"),
    "mogrify": ("agent_data_toolkit.postgresql.queries", "mogrify"),
    "stream_rows": ("agent_data_toolkit.postgresql.queries", "stream_rows"),
    "stream_to_parquet": ("agent_data_toolkit.postgresql.queries", "stream_to_parquet"),
    "execute_batch": ("agent_data_toolkit.postgresql.queries", "execute_batch"),
    "copy_rows_in": ("agent_data_toolkit.postgresql.queries", "copy_rows_in"),
    "copy_rows_out": ("agent_data_toolkit.postgresql.queries", "copy_rows_out"),
    "copy_df_in": ("agent_data_toolkit.postgresql.queries", "copy_df_in"),
    "list_schemas": ("agent_data_toolkit.postgresql.schema", "list_schemas"),
    "list_tables": ("agent_data_toolkit.postgresql.schema", "list_tables"),
    "list_columns": ("agent_data_toolkit.postgresql.schema", "list_columns"),
    "schema": ("agent_data_toolkit.postgresql.schema", "schema"),
    "schema_batch": ("agent_data_toolkit.postgresql.schema", "schema_batch"),
    "explain": ("agent_data_toolkit.postgresql.schema", "explain"),
}


def __getattr__(name: str):
    if name not in _ATTRS:
        raise AttributeError(name)
    mod_name, attr = _ATTRS[name]
    try:
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    except ImportError as e:
        raise RuntimeError(
            "PostgreSQL connector requires optional dependencies. "
            "Install them with: pip install 'agent-data-toolkit[postgresql]'"
        ) from e


def __dir__():
    return sorted(set(list(globals().keys()) + __all__))
