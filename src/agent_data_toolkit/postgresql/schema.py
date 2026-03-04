from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from .connection import ConnectionManager
from .sql import (
    EXPLAIN,
    EXPLAIN_ANALYZE,
    EXPLAIN_ANALYZE_JSON,
    EXPLAIN_JSON,
    LIST_COLUMNS,
    LIST_CONSTRAINTS,
    LIST_INDEXES,
    LIST_MATVIEWS_ALL,
    LIST_MATVIEWS_BY_SCHEMA,
    LIST_SCHEMAS,
    LIST_TABLES_ALL,
    LIST_TABLES_BY_SCHEMA,
)

__all__ = [
    "list_schemas",
    "list_tables",
    "list_columns",
    "schema",
    "explain",
]

_LOG = logging.getLogger(__name__)


def list_schemas(manager: ConnectionManager) -> list[dict[str, Any]]:
    """
    List database schemas.

    Returns:
        List of dict rows with: name, owner.
    """
    try:
        with manager.connection() as conn, conn.cursor() as cur:
            _LOG.debug("Listing schemas")
            cur.execute(LIST_SCHEMAS)
            return list(cur.fetchall())
    except Exception as e:
        _LOG.error("Failed to list schemas: %s", str(e))
        raise RuntimeError(f"Failed to list schemas: {e}") from e


def list_tables(manager: ConnectionManager, schema_name: str | None = None) -> list[dict[str, Any]]:
    """
    List base tables and views, optionally filtered by schema.

    Returns:
        List of dict rows with: table_schema, table_name, table_type.
    """
    try:
        with manager.connection() as conn, conn.cursor() as cur:
            if schema_name:
                _LOG.debug("Listing tables for schema=%s", schema_name)
                cur.execute(LIST_TABLES_BY_SCHEMA, {"schema": schema_name})
            else:
                _LOG.debug("Listing tables (all schemas)")
                cur.execute(LIST_TABLES_ALL)
            return list(cur.fetchall())
    except Exception as e:
        _LOG.error("Failed to list tables: %s", str(e))
        raise RuntimeError(f"Failed to list tables: {e}") from e


def list_columns(manager: ConnectionManager, schema_name: str, table: str) -> list[dict[str, Any]]:
    """
    List columns for a (schema, table).

    Returns:
        List of dict rows (column_name, data_type, is_nullable, column_default, ordinal_position).
    """
    if not schema_name or not table:
        raise ValueError("Both schema_name and table are required.")
    try:
        with manager.connection() as conn, conn.cursor() as cur:
            _LOG.debug("Listing columns for %s.%s", schema_name, table)
            cur.execute(LIST_COLUMNS, {"schema": schema_name, "table": table})
            return list(cur.fetchall())
    except Exception as e:
        _LOG.error("Failed to list columns for %s.%s: %s", schema_name, table, str(e))
        raise RuntimeError(f"Failed to list columns for {schema_name}.{table}: {e}") from e


def schema(
    manager: ConnectionManager,
    *,
    schema_name: str | None = None,
    table: str | None = None,
    include_indexes: bool = True,
    include_constraints: bool = True,
    include_matviews: bool = False,
) -> dict[str, Any]:
    """
    Return schema information using a **single connection** and
    **pipeline mode** to batch all metadata queries in one roundtrip.

    Args:
        manager: ConnectionManager instance.
        schema_name: Optional schema filter; REQUIRED when ``table`` is provided.
        table: Optional table name for detailed info.
        include_indexes: Include index metadata for (schema, table).
        include_constraints: Include constraints for (schema, table).
        include_matviews: Include materialized views alongside tables/views.

    Returns:
        Dict with keys: 'tables', and optionally 'columns', 'constraints', 'indexes'.
    """
    if table and not schema_name:
        raise ValueError("When requesting table details, schema_name must be provided.")

    out: dict[str, Any] = {}

    # Collect all queries we need to run into a batch.
    # We'll use a single connection for all of them — and pipeline mode
    # when multiple queries are needed for maximum efficiency.
    try:
        with manager.connection() as conn:
            # ── Determine queries to run ──
            queries: list[tuple[str, str, dict | None]] = []  # (label, sql, params)

            if schema_name:
                queries.append(("tables", LIST_TABLES_BY_SCHEMA, {"schema": schema_name}))
            else:
                queries.append(("tables", LIST_TABLES_ALL, None))

            if include_matviews:
                if schema_name:
                    queries.append(("matviews", LIST_MATVIEWS_BY_SCHEMA, {"schema": schema_name}))
                else:
                    queries.append(("matviews", LIST_MATVIEWS_ALL, None))

            if schema_name and table:
                queries.append(("columns", LIST_COLUMNS, {"schema": schema_name, "table": table}))
                if include_constraints:
                    queries.append(
                        ("constraints", LIST_CONSTRAINTS, {"schema": schema_name, "table": table})
                    )
                if include_indexes:
                    queries.append(
                        ("indexes", LIST_INDEXES, {"schema": schema_name, "table": table})
                    )

            # ── Execute ──
            # Use pipeline mode when we have > 1 query for a single-roundtrip batch.
            results: dict[str, list[dict]] = {}
            if len(queries) == 1:
                label, sql_text, params = queries[0]
                with conn.cursor() as cur:
                    cur.execute(sql_text, params) if params else cur.execute(sql_text)
                    results[label] = list(cur.fetchall()) if cur.description else []
            else:
                try:
                    with conn.pipeline():
                        cursors: list[tuple[str, Any]] = []
                        for label, sql_text, params in queries:
                            cur = conn.cursor()
                            if params:
                                cur.execute(sql_text, params)
                            else:
                                cur.execute(sql_text)
                            cursors.append((label, cur))

                        for label, cur in cursors:
                            results[label] = list(cur.fetchall()) if cur.description else []
                            cur.close()
                except Exception:
                    # Pipeline mode may not be supported (old libpq).
                    # Fall back to sequential execution on same connection.
                    _LOG.debug("Pipeline not available, falling back to sequential execution")
                    results = {}
                    for label, sql_text, params in queries:
                        with conn.cursor() as cur:
                            cur.execute(sql_text, params) if params else cur.execute(sql_text)
                            results[label] = list(cur.fetchall()) if cur.description else []

            # ── Assemble output ──
            out["tables"] = results.get("tables", [])
            if "matviews" in results:
                out["tables"].extend(results["matviews"])
            if "columns" in results:
                out["columns"] = results["columns"]
            if "constraints" in results:
                out["constraints"] = results["constraints"]
            if "indexes" in results:
                out["indexes"] = results["indexes"]

    except Exception as e:
        _LOG.error("Schema introspection failed: %s", str(e))
        raise RuntimeError(f"Schema introspection failed: {e}") from e

    return out


def schema_batch(
    manager: ConnectionManager,
    tables: Sequence[tuple[str, str]],
    *,
    include_indexes: bool = True,
    include_constraints: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Fetch columns (and optionally indexes + constraints) for **multiple tables**
    in a single roundtrip using pipeline mode.

    This is the killer feature for agents that need to understand the full
    database schema — instead of N sequential roundtrips, everything is
    fetched in one batch.

    Args:
        manager: ConnectionManager instance.
        tables: Sequence of ``(schema_name, table_name)`` tuples.
        include_indexes: Include index metadata for each table.
        include_constraints: Include constraints for each table.

    Returns:
        Dict keyed by ``"schema.table"`` with sub-dicts containing
        ``columns``, ``constraints`` (optional), ``indexes`` (optional).
    """
    if not tables:
        return {}

    out: dict[str, dict[str, Any]] = {}

    # Build all queries
    queries: list[tuple[str, str, str, dict]] = []  # (key, field, sql, params)
    for schema_name, table_name in tables:
        key = f"{schema_name}.{table_name}"
        p = {"schema": schema_name, "table": table_name}
        queries.append((key, "columns", LIST_COLUMNS, p))
        if include_constraints:
            queries.append((key, "constraints", LIST_CONSTRAINTS, p))
        if include_indexes:
            queries.append((key, "indexes", LIST_INDEXES, p))

    try:
        with manager.connection() as conn:
            try:
                with conn.pipeline():
                    cursors: list[tuple[str, str, Any]] = []
                    for key, field_name, sql_text, params in queries:
                        cur = conn.cursor()
                        cur.execute(sql_text, params)
                        cursors.append((key, field_name, cur))

                    for key, field_name, cur in cursors:
                        if key not in out:
                            out[key] = {}
                        out[key][field_name] = list(cur.fetchall()) if cur.description else []
                        cur.close()
            except Exception:
                _LOG.debug("Pipeline not available, falling back to sequential execution")
                out = {}
                for key, field_name, sql_text, params in queries:
                    with conn.cursor() as cur:
                        cur.execute(sql_text, params)
                        if key not in out:
                            out[key] = {}
                        out[key][field_name] = list(cur.fetchall()) if cur.description else []
    except Exception as e:
        _LOG.error("Batch schema introspection failed: %s", str(e))
        raise RuntimeError(f"Batch schema introspection failed: {e}") from e

    return out


def explain(
    manager: ConnectionManager,
    sql: str,
    *,
    analyze: bool = False,
    params: Mapping[str, Any] | None = None,
    fmt: str = "text",
) -> dict[str, Any]:
    """
    Return EXPLAIN (or EXPLAIN ANALYZE) for a given query.

    Args:
        manager: ConnectionManager instance.
        sql: SQL statement to explain.
        analyze: If True, use EXPLAIN ANALYZE (actually executes the query).
        params: Bind parameters. Pass None when there are no params.
        fmt: ``"text"`` for classic plan lines, or ``"json"`` for structured plan.

    Returns:
        ``{"analyze": bool, "format": str, "plan": ...}``
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL to EXPLAIN must be a non-empty string.")
    fmt_norm = fmt.lower()
    if fmt_norm not in ("text", "json"):
        raise ValueError("fmt must be 'text' or 'json'.")

    exec_params = params if params else None

    try:
        template = (
            EXPLAIN_ANALYZE_JSON
            if (fmt_norm == "json" and analyze)
            else EXPLAIN_JSON
            if (fmt_norm == "json")
            else EXPLAIN_ANALYZE
            if analyze
            else EXPLAIN
        )
        stmt = template.format(sql=sql)

        with manager.connection() as conn, conn.cursor() as cur:
            _LOG.debug(
                "EXPLAIN%s %s: %s",
                " ANALYZE" if analyze else "",
                fmt_norm.upper(),
                sql[:120].replace("\n", " "),
            )
            if exec_params is None:
                cur.execute(stmt)
            else:
                cur.execute(stmt, exec_params)

            if not cur.description:
                return {"analyze": analyze, "format": fmt_norm, "plan": []}

            rows = list(cur.fetchall())

        if fmt_norm == "text":
            return {
                "analyze": analyze,
                "format": "text",
                "plan": [r["QUERY PLAN"] for r in rows],
            }

        plan_cell = rows[0]["QUERY PLAN"] if rows else []
        return {"analyze": analyze, "format": "json", "plan": plan_cell}

    except Exception as e:
        _LOG.error("EXPLAIN failed: %s", str(e))
        raise RuntimeError(f"EXPLAIN failed: {e}") from e
