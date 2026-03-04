from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

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
from .schema import explain as explain_plan
from .schema import list_columns, list_schemas, list_tables, schema_batch
from .schema import schema as schema_info

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    import pandas as pd


class PostgresClient:
    """
    High-level façade around ConnectionManager with ergonomic helpers.

    Supports all psycopg 3 advanced features:
    - Connection pooling with configure/check/reset callbacks
    - Pipeline mode for batched queries
    - COPY protocol for bulk data loading
    - Server-side cursors for streaming large result sets
    - Prepared statements for repeated queries
    - Scalar queries for single-value results
    """

    def __init__(self, manager: ConnectionManager):
        self._m = manager

    @classmethod
    def from_env(cls, **kwargs) -> PostgresClient:
        """Create from environment variables (PG_DSN / POSTGRES_DSN / DATABASE_URL)."""
        return cls(ConnectionManager.from_env(**kwargs))

    @classmethod
    def from_dsn(cls, dsn: str, **kwargs) -> PostgresClient:
        """Create from a DSN string."""
        return cls(ConnectionManager(ConnectionInfo(dsn=dsn), **kwargs))

    @classmethod
    def from_info(cls, info: ConnectionInfo, **kwargs) -> PostgresClient:
        """Create from a ConnectionInfo object."""
        return cls(ConnectionManager(info, **kwargs))

    def __enter__(self) -> PostgresClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── Queries ────────────────────────────────────────────

    def query_rows(self, *args, **kwargs) -> list[dict]:
        """Execute a query and return rows as dicts."""
        return query_rows(self._m, *args, **kwargs)

    def query_scalar(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        """Execute a query and return the first column of the first row."""
        return query_scalar(self._m, sql, params)

    def query_namedtuples(self, *args, **kwargs) -> list:
        """
        Execute a query and return rows as namedtuples.

        Namedtuples offer dotted-attribute access (``row.column_name``)
        which is convenient for agent code that processes structured results.
        """
        return query_namedtuples(self._m, *args, **kwargs)

    def mogrify(self, sql: str, params: Mapping[str, Any] | None = None) -> str:
        """
        Return the query string with parameters merged (for debugging).

        Uses psycopg's ClientCursor.mogrify() when available for accurate
        rendering of how PostgreSQL will see the query.
        Does NOT execute the query.

        This is invaluable for agents that need to inspect or log the
        final SQL before execution.
        """
        return mogrify(self._m, sql, params)

    def query_df(self, *args, **kwargs) -> pd.DataFrame:
        """Execute a query and return a pandas DataFrame."""
        return query_df(self._m, *args, **kwargs)

    def stream_rows(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        *,
        itersize: int = 2000,
    ) -> Generator[dict, None, None]:
        """Lazily iterate over large result sets using a server-side cursor."""
        return stream_rows(self._m, sql, params, itersize=itersize)

    def stream_to_parquet(self, *args, **kwargs) -> str:
        """Stream a large SELECT directly to a Parquet file."""
        return stream_to_parquet(self._m, *args, **kwargs)

    # ── Pipeline / Batch ──────────────────────────────────

    def execute_batch(
        self,
        statements: Sequence[tuple[str, Mapping[str, Any] | None]],
    ) -> list[list[dict]]:
        """
        Execute multiple statements in a single roundtrip using pipeline mode.

        Args:
            statements: Sequence of ``(sql, params)`` tuples.

        Returns:
            List of result-row lists, one per statement.
        """
        return execute_batch(self._m, statements)

    # ── COPY (bulk data) ──────────────────────────────────

    def copy_rows_in(
        self,
        table: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
        *,
        schema_name: str | None = None,
    ) -> int:
        """Bulk-load rows using PostgreSQL COPY protocol."""
        return copy_rows_in(self._m, table, columns, rows, schema_name=schema_name)

    def copy_df_in(
        self,
        table: str,
        df: pd.DataFrame,
        *,
        schema_name: str | None = None,
        columns: Sequence[str] | None = None,
    ) -> int:
        """Bulk-load a DataFrame using PostgreSQL COPY protocol."""
        return copy_df_in(self._m, table, df, schema_name=schema_name, columns=columns)

    def copy_rows_out(self, sql: str) -> list[tuple]:
        """Bulk-export rows using PostgreSQL COPY TO protocol."""
        return copy_rows_out(self._m, sql)

    # ── Schema ────────────────────────────────────────────

    def list_schemas(self) -> list[dict[str, Any]]:
        """List all schemas."""
        return list_schemas(self._m)

    def list_tables(self, schema_name: str | None = None) -> list[dict[str, Any]]:
        """List tables and views, optionally filtered by schema."""
        return list_tables(self._m, schema_name)

    def list_columns(self, schema_name: str, table: str) -> list[dict[str, Any]]:
        """List columns for a table."""
        return list_columns(self._m, schema_name, table)

    def schema(self, **kwargs) -> dict[str, Any]:
        """Get schema information (tables, columns, constraints, indexes)."""
        return schema_info(self._m, **kwargs)

    def schema_batch(
        self,
        tables: Sequence[tuple[str, str]],
        **kwargs,
    ) -> dict[str, dict[str, Any]]:
        """
        Fetch schema details for multiple tables in a single roundtrip.

        Args:
            tables: Sequence of ``(schema_name, table_name)`` tuples.

        Returns:
            Dict keyed by ``"schema.table"`` with columns, constraints, indexes.
        """
        return schema_batch(self._m, tables, **kwargs)

    def explain(self, sql: str, **kwargs) -> dict[str, Any]:
        """Return EXPLAIN plan for a query."""
        return explain_plan(self._m, sql, **kwargs)

    # ── Lifecycle / Monitoring ────────────────────────────

    def close(self) -> None:
        """Close the client and its connection pool."""
        self._m.close()

    def healthcheck(self, **kwargs) -> dict[str, Any]:
        """Run a quick health check (SELECT 1)."""
        return self._m.healthcheck(**kwargs)

    def server_info(self) -> dict[str, Any]:
        """
        Return server metadata: version, timezone, backend PID, encoding.

        Uses psycopg's ConnectionInfo for accurate introspection.
        Useful for agents that need to adapt queries to the PG version.
        """
        return self._m.server_info()

    def pool_stats(self) -> dict[str, Any]:
        """Return pool metrics (15+ stats when pooling is enabled)."""
        return self._m.pool_stats()

    def pool_resize(self, min_size: int, max_size: int | None = None) -> None:
        """Dynamically resize the connection pool."""
        self._m.pool_resize(min_size, max_size)

    def pool_drain(self) -> None:
        """
        Replace all connections in the pool with fresh ones.

        Useful after changing settings or when connections may be stale.
        """
        self._m.pool_drain()

    @property
    def manager(self) -> ConnectionManager:
        """Access the underlying ConnectionManager."""
        return self._m
