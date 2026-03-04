"""
Static SQL snippets for PostgreSQL schema discovery and EXPLAIN helpers.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "LIST_SCHEMAS",
    "LIST_TABLES_ALL",
    "LIST_TABLES_BY_SCHEMA",
    "LIST_MATVIEWS_ALL",
    "LIST_MATVIEWS_BY_SCHEMA",
    "LIST_COLUMNS",
    "LIST_INDEXES",
    "LIST_CONSTRAINTS",
    "EXPLAIN",
    "EXPLAIN_ANALYZE",
    "EXPLAIN_JSON",
    "EXPLAIN_ANALYZE_JSON",
]

# ── Schemas / Tables / Columns ─────────────────────────────────────

LIST_SCHEMAS: Final[str] = """
SELECT schema_name AS name, schema_owner AS owner
FROM information_schema.schemata
ORDER BY schema_name;
"""
"""List all schemas in the database."""

LIST_TABLES_ALL: Final[str] = """
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_type IN ('BASE TABLE','VIEW')
ORDER BY table_schema, table_name;
"""
"""List all base tables and views across all schemas."""

LIST_TABLES_BY_SCHEMA: Final[str] = """
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_type IN ('BASE TABLE','VIEW')
  AND table_schema = %(schema)s
ORDER BY table_schema, table_name;
"""
"""List base tables and views for a specific schema."""

LIST_MATVIEWS_ALL: Final[str] = """
SELECT schemaname AS table_schema,
       matviewname AS table_name,
       'MATERIALIZED VIEW' AS table_type
FROM pg_matviews
ORDER BY schemaname, matviewname;
"""
"""List all materialized views across all schemas."""

LIST_MATVIEWS_BY_SCHEMA: Final[str] = """
SELECT schemaname AS table_schema,
       matviewname AS table_name,
       'MATERIALIZED VIEW' AS table_type
FROM pg_matviews
WHERE schemaname = %(schema)s
ORDER BY schemaname, matviewname;
"""
"""List materialized views for a specific schema."""

LIST_COLUMNS: Final[str] = """
SELECT column_name,
       data_type,
       is_nullable,
       column_default,
       ordinal_position
FROM information_schema.columns
WHERE table_schema = %(schema)s AND table_name = %(table)s
ORDER BY ordinal_position;
"""
"""List columns for a specific (schema, table)."""

# ── Indexes / Constraints ──────────────────────────────────────────

LIST_INDEXES: Final[str] = """
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = %(schema)s AND tablename = %(table)s
ORDER BY indexname;
"""
"""List indexes for a (schema, table)."""

LIST_CONSTRAINTS: Final[str] = """
SELECT tc.constraint_name,
       tc.constraint_type,
       kcu.column_name
FROM information_schema.table_constraints AS tc
LEFT JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
 AND tc.table_schema    = kcu.table_schema
WHERE tc.table_schema = %(schema)s AND tc.table_name = %(table)s
ORDER BY tc.constraint_name, kcu.ordinal_position NULLS LAST;
"""
"""List constraints for a (schema, table) with involved columns."""

# ── EXPLAIN helpers ────────────────────────────────────────────────

EXPLAIN: Final[str] = "EXPLAIN {sql}"
"""Prefix template for EXPLAIN (text format)."""

EXPLAIN_ANALYZE: Final[str] = "EXPLAIN ANALYZE {sql}"
"""Prefix template for EXPLAIN ANALYZE (text format)."""

EXPLAIN_JSON: Final[str] = "EXPLAIN (FORMAT JSON) {sql}"
"""Prefix template for EXPLAIN (FORMAT JSON)."""

EXPLAIN_ANALYZE_JSON: Final[str] = "EXPLAIN (ANALYZE, FORMAT JSON) {sql}"
"""Prefix template for EXPLAIN ANALYZE (FORMAT JSON)."""
