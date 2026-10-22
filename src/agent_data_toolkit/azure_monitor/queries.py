"""Structured Logs results with explicit partial-data and client-limit semantics."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import pandas as pd

    from .connection import ConnectionManager


class LogsQueryError(RuntimeError):
    """A query failed or its response could not be interpreted safely."""


class PartialResultError(LogsQueryError):
    """DataFrame conversion requires an explicit decision to accept incomplete data."""

    def __init__(self, result: QueryResult):
        super().__init__("Logs result is partial or client-truncated; inspect result metadata")
        self.result = result


@dataclass(frozen=True)
class LogsTable:
    """A source table retaining its schema even when it has no rows."""

    name: str
    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    source_row_count: int


@dataclass(frozen=True)
class QueryResult:
    """Result status describes this response, never global telemetry coverage."""

    status: Literal["success", "partial"]
    tables: tuple[LogsTable, ...]
    workspace_id: str
    query: str
    start_time: datetime | None
    end_time: datetime | None
    truncated: bool
    max_rows: int
    error_code: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    raw_response: bytes | None = field(default=None, repr=False)

    @property
    def complete(self) -> bool:
        """Whether the API succeeded and this connector retained every returned row."""
        return self.status == "success" and not self.truncated

    def metadata(self) -> dict[str, Any]:
        """Return JSON-compatible source status and schema, without rows or credentials."""
        return {
            "status": self.status,
            "complete": self.complete,
            "workspace_id": self.workspace_id,
            "query_sha256": sha256(self.query.encode()).hexdigest(),
            "request_id": self.request_id,
            "raw_response_sha256": (
                sha256(self.raw_response).hexdigest() if self.raw_response is not None else None
            ),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "truncated": self.truncated,
            "max_rows": self.max_rows,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "tables": [
                {
                    "name": table.name,
                    "columns": list(table.columns),
                    "column_types": list(table.column_types),
                    "row_count": len(table.rows),
                    "source_row_count": table.source_row_count,
                }
                for table in self.tables
            ],
        }

    def to_dataframe(self, table: int = 0, *, allow_partial: bool = False) -> pd.DataFrame:
        """Convert one table, explicitly opting in to incomplete results when needed."""
        if not isinstance(allow_partial, bool):
            raise ValueError("allow_partial must be a boolean")
        if not self.complete and not allow_partial:
            raise PartialResultError(self)
        if (
            isinstance(table, bool)
            or not isinstance(table, int)
            or not 0 <= table < len(self.tables)
        ):
            raise ValueError("table must select an existing result table")
        import pandas as pd

        selected = self.tables[table]
        frame = pd.DataFrame(selected.rows, columns=selected.columns)
        frame.attrs["azure_monitor"] = self.metadata()
        return frame


def validate_query(
    query: str, timespan: tuple[datetime, datetime] | None, max_rows: int, server_timeout: int
) -> tuple[datetime | None, datetime | None]:
    """Validate before credentials are used or any network request is sent."""
    if not isinstance(query, str) or not query.strip() or len(query) > 65_536:
        raise ValueError("query must contain 1 to 65536 characters")
    if timespan is not None and (not isinstance(timespan, tuple) or len(timespan) != 2):
        raise ValueError("timespan must be an absolute (start, end) datetime tuple")
    for value in timespan or ():
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timespan requires timezone-aware datetimes")
    start, end = tuple(value.astimezone(UTC) for value in timespan) if timespan else (None, None)
    if start is not None and end is not None and start >= end:
        raise ValueError("timespan start must precede end")
    for name, value, upper in (
        ("max_rows", max_rows, 1_000_000),
        ("server_timeout", server_timeout, 600),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f"{name} must be an integer between 1 and {upper}")
    return start, end


def _validate_native_response(body: bytes) -> None:
    """Check wire structure before trusting the SDK's permissive model conversion."""
    try:
        value = json.loads(body)
    except (ValueError, TypeError):
        raise LogsQueryError("Malformed native Azure Monitor Logs response") from None
    if not isinstance(value, dict) or not isinstance(value.get("tables"), list):
        raise LogsQueryError("Missing native Azure Monitor Logs tables")
    error = value.get("error")
    if error is not None and (
        not isinstance(error, dict)
        or not isinstance(error.get("code"), str)
        or not error["code"]
        or not isinstance(error.get("message"), str)
    ):
        raise LogsQueryError("Malformed native Azure Monitor Logs partial error")
    if not value["tables"] and error is None:
        raise LogsQueryError("Successful Logs query returned no table envelope")
    for table in value["tables"]:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise LogsQueryError("Malformed native Azure Monitor Logs table")
        columns, rows = table.get("columns"), table.get("rows")
        if not isinstance(columns, list) or not columns or not isinstance(rows, list):
            raise LogsQueryError("Malformed native Azure Monitor Logs schema")
        names = []
        for column in columns:
            if (
                not isinstance(column, dict)
                or not isinstance(column.get("name"), str)
                or not column["name"]
                or not isinstance(column.get("type"), str)
                or not column["type"]
            ):
                raise LogsQueryError("Malformed native Azure Monitor Logs column")
            names.append(column["name"])
        if len(names) != len(set(names)) or any(
            not isinstance(row, list) or len(row) != len(columns) for row in rows
        ):
            raise LogsQueryError("Malformed native Azure Monitor Logs rows")


def query_logs(
    manager: ConnectionManager,
    query: str,
    *,
    timespan: tuple[datetime, datetime] | None,
    max_rows: int = 100_000,
    server_timeout: int = 120,
) -> QueryResult:
    """Execute once, retaining partial status and applying a total client-side row cap.

    max_rows limits retained rows across tables, not server scan volume or network
    bytes. Azure service limits and the server timeout remain independent bounds.
    The explicit time interval is passed unchanged to the SDK after UTC conversion.
    """
    start, end = validate_query(query, timespan, max_rows, server_timeout)
    client = manager.client
    evidence: dict[str, Any] = {}

    def capture_response(pipeline_response: Any) -> None:
        response = pipeline_response.http_response
        evidence["body"] = response.body()
        evidence["request_id"] = response.headers.get("x-ms-request-id")

    try:
        response = client.query_workspace(
            manager.info.workspace_id,
            query,
            timespan=(start, end) if timespan is not None else None,
            server_timeout=server_timeout,
            raw_response_hook=capture_response,
        )
    except Exception:
        # SDK exception strings may contain transport details. Never echo credentials.
        raise LogsQueryError("Azure Monitor Logs request failed") from None
    if evidence.get("body") is not None:
        _validate_native_response(evidence["body"])
    status = getattr(response, "status", None)
    if status not in ("Success", "PartialError"):
        raise LogsQueryError("Unsupported Azure Monitor Logs result status")
    partial = status == "PartialError"
    source_tables = getattr(response, "partial_data" if partial else "tables", None)
    error = getattr(response, "partial_error", None) if partial else None
    if (
        not isinstance(source_tables, list)
        or (not partial and not source_tables)
        or (partial and error is None)
    ):
        raise LogsQueryError("Malformed Azure Monitor Logs result")
    remaining = max_rows
    tables = []
    truncated = False
    for source in source_tables:
        columns = getattr(source, "columns", None)
        types = getattr(source, "columns_types", None)
        rows = getattr(source, "rows", None)
        if (
            not isinstance(getattr(source, "name", None), str)
            or not isinstance(columns, list)
            or not columns
            or any(not isinstance(name, str) or not name for name in columns)
            or len(set(columns)) != len(columns)
            or not isinstance(types, list)
            or len(types) != len(columns)
            or any(not isinstance(kind, str) or not kind for kind in types)
            or not isinstance(rows, list)
        ):
            raise LogsQueryError("Malformed Azure Monitor Logs table")
        # Azure SDK rows are LogsTableRow objects, not plain lists. Normalize
        # their sequence values without accepting strings, mappings or scalars.
        normalized = []
        for row in rows:
            if isinstance(row, (str, bytes, bytearray, Mapping)):
                raise LogsQueryError("Malformed Azure Monitor Logs row")
            try:
                values = tuple(row)
            except TypeError:
                raise LogsQueryError("Malformed Azure Monitor Logs row") from None
            if len(values) != len(columns):
                raise LogsQueryError("Malformed Azure Monitor Logs row width")
            normalized.append(values)
        retained = normalized[:remaining]
        remaining -= len(retained)
        truncated |= len(retained) < len(rows)
        tables.append(
            LogsTable(source.name, tuple(columns), tuple(types), tuple(retained), len(rows))
        )
    return QueryResult(
        status="partial" if partial else "success",
        tables=tuple(tables),
        workspace_id=manager.info.workspace_id,
        query=query,
        start_time=start,
        end_time=end,
        truncated=truncated,
        max_rows=max_rows,
        error_code=str(error.code)[:200] if error is not None else None,
        error_message=str(error.message)[:2000] if error is not None else None,
        request_id=evidence.get("request_id"),
        raw_response=evidence.get("body"),
    )
