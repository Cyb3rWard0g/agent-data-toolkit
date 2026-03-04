from __future__ import annotations

import contextlib
import logging
import tempfile
from collections.abc import Generator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .connection import ConnectionManager, _import_psycopg
from .io import rows_to_dataframe

if TYPE_CHECKING:
    import pandas as pd
    import pyarrow.parquet as pq

_LOG = logging.getLogger(__name__)


# ───────────────────────── internals / lazy deps ─────────────────────────


def _require_pandas():
    """Lazy-import pandas with a friendly extras message."""
    try:
        import pandas as pd  # type: ignore

        return pd
    except Exception as e:
        raise RuntimeError(
            "pandas is required for DataFrame operations. "
            "Install extras with: pip install 'agent-data-toolkit[postgresql]'"
        ) from e


def _require_pyarrow():
    """Lazy-import pyarrow + parquet with a friendly extras message."""
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore

        return pa, pq
    except Exception as e:
        raise RuntimeError(
            "pyarrow is required for Arrow/Parquet operations. "
            "Install extras with: pip install 'agent-data-toolkit[postgresql]'"
        ) from e


def _import_sql():
    """
    Lazy import for psycopg.sql utilities.

    Returns:
        The psycopg.sql module (provides SQL, Identifier, Literal, Composed, etc.).
    """
    from psycopg import sql  # type: ignore

    return sql


# ───────────────────── query statement builder ─────────────────────


def _build_query(
    *,
    sql: str | None,
    schema_name: str | None,
    table_name: str | None,
    columns: str | Sequence[str],
    where: str | None,
    order_by: str | None,
    limit: int | None,
):
    """
    Build the final (statement, needs_params) pair for raw-SQL or table mode.

    Returns:
        (sql_obj, is_raw_mode) — sql_obj is either a plain string or
        a ``psycopg.sql.Composed`` object.

    Raises:
        ValueError: on invalid inputs.
    """
    if sql is not None:
        # Raw SQL mode
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("SQL query must be a non-empty string.")
        sql_clean = sql.strip().rstrip(";")

        if limit is not None:
            sql_mod = _import_sql()
            return (
                sql_mod.SQL("SELECT * FROM ({}) AS _adt_sub LIMIT {}").format(
                    sql_mod.SQL(sql_clean),
                    sql_mod.Literal(int(limit)),
                ),
                True,
            )
        return sql_clean, True

    if schema_name and table_name:
        # Table mode (identifier-safe)
        sql_mod = _import_sql()

        if columns == "*":
            cols_sql = sql_mod.SQL("*")
        else:
            if not isinstance(columns, Sequence) or isinstance(columns, str):
                raise ValueError("columns must be '*' or a sequence of column names")
            cols_sql = sql_mod.SQL(", ").join(sql_mod.Identifier(c) for c in columns)

        base = sql_mod.SQL("SELECT {} FROM {}.{}").format(
            cols_sql,
            sql_mod.Identifier(schema_name),
            sql_mod.Identifier(table_name),
        )
        parts = [base]
        if where:
            parts.append(sql_mod.SQL(" WHERE " + where))
        if order_by:
            parts.append(sql_mod.SQL(" ORDER BY " + order_by))
        if limit is not None:
            parts.append(sql_mod.SQL(" LIMIT {}").format(sql_mod.Literal(int(limit))))

        return sql_mod.Composed(parts), False

    raise ValueError(
        "Provide either `sql` (raw SQL mode) OR `schema_name` + `table_name` (table mode)."
    )


# ───────────────────────────── public API ─────────────────────────────


def query_rows(
    manager: ConnectionManager,
    sql: str | None = None,
    *,
    schema_name: str | None = None,
    table_name: str | None = None,
    columns: str | Sequence[str] = "*",
    where: str | None = None,
    order_by: str | None = None,
    limit: int | None = None,
    params: Mapping[str, Any] | None = None,
) -> list[dict]:
    """
    Execute a query and return rows as dicts.

    Two modes
    ---------
    1. **Raw SQL** — ``sql="SELECT ..."`` with optional ``params`` and ``limit``.
    2. **Table mode** — ``schema_name`` + ``table_name`` with safe identifier quoting.

    Important
    ---------
    Do **not** pass ``params={}`` (empty dict) — psycopg will scan for ``%``
    placeholders and break on string-literal percents (e.g. ``ILIKE '%foo%'``).
    Pass ``None`` when you have no parameters.
    """
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("Query parameters must be a Mapping or None.")

    sql_final, _ = _build_query(
        sql=sql,
        schema_name=schema_name,
        table_name=table_name,
        columns=columns,
        where=where,
        order_by=order_by,
        limit=limit,
    )
    exec_params = params if params else None  # treat empty dict as None

    try:
        with manager.connection() as conn, conn.cursor() as cur:
            _LOG.debug("Executing query: %s", str(sql_final)[:200].replace("\n", " "))
            if exec_params is None:
                cur.execute(sql_final)
            else:
                cur.execute(sql_final, exec_params)
            rows = list(cur.fetchall()) if cur.description else []
            _LOG.debug("Query returned %d rows.", len(rows))
            return rows
    except Exception as e:
        _LOG.error("Query execution failed: %s", str(e))
        raise RuntimeError(f"Query execution failed: {e}") from e


def query_scalar(
    manager: ConnectionManager,
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> Any:
    """
    Execute a query and return the first column of the first row.

    Ideal for ``SELECT count(*)``, ``SELECT max(id)``, ``SELECT pg_database_size(...)``
    and similar single-value queries.

    Returns:
        The scalar value, or ``None`` if the result set is empty.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL query must be a non-empty string.")
    exec_params = params if params else None

    try:
        psycopg, _ = _import_psycopg()
        from psycopg.rows import scalar_row  # type: ignore

        with manager.connection() as conn:
            with conn.cursor(row_factory=scalar_row) as cur:
                if exec_params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, exec_params)
                return cur.fetchone()
    except Exception as e:
        _LOG.error("Scalar query failed: %s", str(e))
        raise RuntimeError(f"Scalar query failed: {e}") from e


def query_namedtuples(
    manager: ConnectionManager,
    sql: str | None = None,
    *,
    schema_name: str | None = None,
    table_name: str | None = None,
    columns: str | Sequence[str] = "*",
    where: str | None = None,
    order_by: str | None = None,
    limit: int | None = None,
    params: Mapping[str, Any] | None = None,
) -> list:
    """
    Execute a query and return rows as namedtuples.

    Namedtuples provide dotted-attribute access (``row.column_name``) which
    is more ergonomic than dict access for structured agent code.

    Supports both raw SQL and table modes (same interface as ``query_rows``).

    Returns:
        List of namedtuple instances.
    """
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("Query parameters must be a Mapping or None.")

    sql_final, _ = _build_query(
        sql=sql,
        schema_name=schema_name,
        table_name=table_name,
        columns=columns,
        where=where,
        order_by=order_by,
        limit=limit,
    )
    exec_params = params if params else None

    try:
        from psycopg.rows import namedtuple_row  # type: ignore

        with manager.connection() as conn:
            with conn.cursor(row_factory=namedtuple_row) as cur:
                _LOG.debug("Executing namedtuple query: %s", str(sql_final)[:200])
                if exec_params is None:
                    cur.execute(sql_final)
                else:
                    cur.execute(sql_final, exec_params)
                rows = list(cur.fetchall()) if cur.description else []
                _LOG.debug("Namedtuple query returned %d rows.", len(rows))
                return rows
    except Exception as e:
        _LOG.error("Namedtuple query failed: %s", str(e))
        raise RuntimeError(f"Namedtuple query failed: {e}") from e


def mogrify(
    manager: ConnectionManager,
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> str:
    """
    Return the query string with parameters merged — for debugging/logging.

    Uses psycopg's ``ClientCursor.mogrify()`` for accurate rendering of how
    PostgreSQL would see the final query. Does NOT execute the query.

    This is invaluable for AI agents that need to inspect or log the
    final SQL before execution, or for building explain/audit trails.

    Args:
        manager: ConnectionManager instance.
        sql: SQL query with optional placeholders.
        params: Bind parameters (or None).

    Returns:
        The rendered SQL string.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL query must be a non-empty string.")

    try:
        from psycopg import ClientCursor  # type: ignore

        with manager.connection() as conn:
            with ClientCursor(conn) as cur:
                return cur.mogrify(sql, params)
    except ImportError:
        # Fallback: return the raw SQL with a note
        if params:
            return f"{sql}  -- params: {params}"
        return sql
    except Exception as e:
        _LOG.error("Mogrify failed: %s", str(e))
        raise RuntimeError(f"Mogrify failed: {e}") from e


def query_df(
    manager: ConnectionManager,
    sql: str | None = None,
    *,
    schema_name: str | None = None,
    table_name: str | None = None,
    columns: str | Sequence[str] = "*",
    where: str | None = None,
    order_by: str | None = None,
    limit: int | None = None,
    params: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """DataFrame wrapper of ``query_rows`` (supports both raw SQL and table modes)."""
    rows = query_rows(
        manager,
        sql,
        schema_name=schema_name,
        table_name=table_name,
        columns=columns,
        where=where,
        order_by=order_by,
        limit=limit,
        params=params,
    )
    return rows_to_dataframe(rows)


def stream_rows(
    manager: ConnectionManager,
    sql: str,
    params: Mapping[str, Any] | None = None,
    *,
    itersize: int = 2000,
) -> Generator[dict, None, None]:
    """
    Lazily iterate over a large result set using a **server-side cursor**.

    Rows are fetched in batches of ``itersize`` to avoid loading the entire
    result set into memory.  Perfect for agents processing millions of rows.

    Args:
        manager: ConnectionManager instance.
        sql: SQL SELECT to execute.
        params: Bind parameters (or None).
        itersize: Batch fetch size (default 2000).

    Yields:
        dict rows one at a time.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL query must be a non-empty string.")
    exec_params = params if params else None

    with manager.connection() as conn:
        # Server-side cursor requires a transaction context.
        with conn.transaction():
            with conn.cursor(name="adt_stream") as cur:
                cur.itersize = itersize
                if exec_params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, exec_params)
                yield from cur


def stream_to_parquet(
    manager: ConnectionManager,
    sql: str,
    path: str | Path,
    params: Mapping[str, Any] | None = None,
    *,
    chunk_rows: int = 50_000,
    progress_every: int = 10,
) -> str:
    """
    Stream a large SELECT result directly to a Parquet file via a server-side cursor.

    Uses atomic write (temp file + replace) for safety.

    Args:
        manager: ConnectionManager instance.
        sql: SQL SELECT to execute.
        path: Output Parquet file path.
        params: Bind parameters (or None).
        chunk_rows: fetchmany() chunk size (> 0).
        progress_every: Log a DEBUG message every N chunks.

    Returns:
        Absolute output file path.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL query must be a non-empty string.")
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("Query parameters must be a Mapping or None.")
    if not isinstance(chunk_rows, int) or chunk_rows <= 0:
        raise ValueError("chunk_rows must be a positive integer.")

    pd = _require_pandas()
    pa, pq = _require_pyarrow()

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    exec_params = params if params else None

    writer: pq.ParquetWriter | None = None
    tmp_path: Path | None = None
    total_rows = 0

    try:
        with manager.connection() as conn:
            _LOG.debug("Streaming to parquet: %s", sql[:200].replace("\n", " "))
            with conn.transaction():
                with conn.cursor(name="adt_parquet_stream") as cur:
                    cur.itersize = chunk_rows
                    if exec_params is None:
                        cur.execute(sql)
                    else:
                        cur.execute(sql, exec_params)

                    with tempfile.NamedTemporaryFile(dir=p.parent, delete=False) as tmp:
                        tmp_path = Path(tmp.name)

                    chunks = 0
                    while True:
                        chunk = cur.fetchmany(chunk_rows)
                        if not chunk:
                            break
                        df = pd.DataFrame(chunk)
                        table = pa.Table.from_pandas(df, preserve_index=False)
                        if writer is None:
                            writer = pq.ParquetWriter(tmp_path, table.schema)
                        writer.write_table(table)

                        chunks += 1
                        total_rows += len(df)
                        if progress_every and (chunks % progress_every == 0):
                            _LOG.debug("... streamed %d chunks / %d rows", chunks, total_rows)

        if writer is not None:
            writer.close()

        if tmp_path is not None:
            tmp_path.replace(p)

        _LOG.debug("Streaming complete: %s (~%d rows)", str(p.resolve()), total_rows)
        return str(p.resolve())

    except Exception as e:
        _LOG.error("Failed to stream to parquet: %s", str(e))
        raise RuntimeError(f"Failed to stream to parquet: {e}") from e
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
        if tmp_path is not None and tmp_path.exists():
            with contextlib.suppress(Exception):
                tmp_path.unlink()


def execute_batch(
    manager: ConnectionManager,
    statements: Sequence[tuple[str, Mapping[str, Any] | None]],
) -> list[list[dict]]:
    """
    Execute multiple statements in a single network roundtrip using
    psycopg's **pipeline mode**.

    Pipeline mode batches all queries into one client→server flush,
    dramatically reducing latency for agents that need several queries
    at once (e.g. schema introspection across many tables).

    Args:
        manager: ConnectionManager instance.
        statements: Sequence of (sql, params) tuples.  ``params`` may be
                    ``None`` for parameter-less queries.

    Returns:
        List of result-row lists (one per statement).  Statements that
        return no rows produce an empty list.

    Note:
        Pipeline mode is NOT compatible with COPY, server-side cursors,
        or LISTEN/NOTIFY.  Use regular ``query_rows`` for those.
    """
    if not statements:
        return []

    results: list[list[dict]] = []
    try:
        with manager.connection() as conn:
            with conn.pipeline():
                cursors = []
                for sql_text, params in statements:
                    cur = conn.cursor()
                    if params:
                        cur.execute(sql_text, params)
                    else:
                        cur.execute(sql_text)
                    cursors.append(cur)

                # After exiting the pipeline context, results are available
                for cur in cursors:
                    if cur.description:
                        results.append(list(cur.fetchall()))
                    else:
                        results.append([])
                    cur.close()

        return results
    except Exception as e:
        _LOG.error("Pipeline batch execution failed: %s", str(e))
        raise RuntimeError(f"Pipeline batch execution failed: {e}") from e


def copy_rows_in(
    manager: ConnectionManager,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    schema_name: str | None = None,
) -> int:
    """
    Bulk-load rows into a table using PostgreSQL COPY protocol.

    COPY is **orders of magnitude faster** than row-by-row INSERT for large
    data loads.  This function uses ``cursor.copy()`` with ``write_row()``
    which handles Python type adaptation automatically.

    Args:
        manager: ConnectionManager instance.
        table: Target table name.
        columns: Column names in the order matching row values.
        rows: Iterable of row tuples/lists.
        schema_name: Optional schema qualifier.

    Returns:
        Number of rows written.
    """
    if not columns:
        raise ValueError("columns must be a non-empty sequence.")

    sql_mod = _import_sql()

    if schema_name:
        tbl = sql_mod.SQL("{}.{}").format(
            sql_mod.Identifier(schema_name), sql_mod.Identifier(table)
        )
    else:
        tbl = sql_mod.Identifier(table)

    cols = sql_mod.SQL(", ").join(sql_mod.Identifier(c) for c in columns)
    stmt = sql_mod.SQL("COPY {} ({}) FROM STDIN").format(tbl, cols)

    count = 0
    try:
        with manager.connection() as conn:
            with conn.cursor() as cur:
                with cur.copy(stmt) as copy:
                    for row in rows:
                        copy.write_row(row)
                        count += 1
        _LOG.debug("COPY IN: %d rows written to %s", count, table)
        return count
    except Exception as e:
        _LOG.error("COPY IN failed: %s", str(e))
        raise RuntimeError(f"COPY IN failed: {e}") from e


def copy_rows_out(
    manager: ConnectionManager,
    sql: str,
) -> list[tuple]:
    """
    Bulk-export rows using PostgreSQL COPY TO protocol.

    Much faster than a regular SELECT + fetchall for very large exports.

    Args:
        manager: ConnectionManager instance.
        sql: A ``COPY (...) TO STDOUT`` statement, or a table/query
             that will be wrapped as ``COPY (sql) TO STDOUT``.

    Returns:
        List of row tuples.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL must be a non-empty string.")

    sql_clean = sql.strip()
    # If user passed a SELECT, wrap it in COPY ... TO STDOUT
    if not sql_clean.upper().startswith("COPY"):
        sql_mod = _import_sql()
        stmt = sql_mod.SQL("COPY ({}) TO STDOUT").format(sql_mod.SQL(sql_clean))
    else:
        stmt = sql_clean

    result: list[tuple] = []
    try:
        with manager.connection() as conn:
            with conn.cursor() as cur:
                with cur.copy(stmt) as copy:
                    for row in copy.rows():
                        result.append(row)
        _LOG.debug("COPY OUT: %d rows read.", len(result))
        return result
    except Exception as e:
        _LOG.error("COPY OUT failed: %s", str(e))
        raise RuntimeError(f"COPY OUT failed: {e}") from e


def copy_df_in(
    manager: ConnectionManager,
    table: str,
    df: pd.DataFrame,
    *,
    schema_name: str | None = None,
    columns: Sequence[str] | None = None,
) -> int:
    """
    Bulk-load a pandas DataFrame into a table using PostgreSQL COPY protocol.

    Args:
        manager: ConnectionManager instance.
        table: Target table name.
        df: DataFrame to load.
        schema_name: Optional schema qualifier.
        columns: Column names to use. Defaults to ``df.columns``.

    Returns:
        Number of rows written.
    """
    _require_pandas()
    cols = list(columns or df.columns)
    rows = [tuple(row) for row in df[cols].itertuples(index=False, name=None)]
    return copy_rows_in(manager, table, cols, rows, schema_name=schema_name)
