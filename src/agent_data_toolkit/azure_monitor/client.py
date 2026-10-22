"""Public facade for Azure Monitor Logs and Sentinel workspace queries."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .connection import ConnectionInfo, ConnectionManager
from .queries import QueryResult, query_logs

if TYPE_CHECKING:
    import pandas as pd


class AzureMonitorLogsClient:
    """Reusable synchronous client suitable for a long-lived notebook kernel."""

    def __init__(self, manager: ConnectionManager):
        self._manager = manager

    @classmethod
    def from_env(cls, *, credential: Any = None) -> AzureMonitorLogsClient:
        """Initialize using trusted kernel environment and optional caller-owned credential."""
        return cls(ConnectionManager.from_env(credential=credential))

    @classmethod
    def from_info(
        cls, info: ConnectionInfo, credential: Any, *, transport: Any = None
    ) -> AzureMonitorLogsClient:
        """Use an explicit workspace, caller-owned credential and optional SDK HttpTransport.

        Transport is trusted Python configuration, not a query argument. The SDK
        client owns its transport lifetime; closing this client closes it. A
        capture transport can mediate requests without exposing a source token.
        """
        return cls(ConnectionManager(info, credential, transport=transport))

    def query(
        self,
        query: str,
        *,
        timespan: tuple[datetime, datetime] | None,
        max_rows: int = 100_000,
        server_timeout: int = 120,
    ) -> QueryResult:
        """Execute once with an absolute API time filter or explicit None.

        None sends no additional API time filter. Use it when the query text
        already owns its occurrence-time predicates. Result time-filter metadata
        is then null; it does not claim an independently verified query window.
        """
        return query_logs(
            self._manager,
            query,
            timespan=timespan,
            max_rows=max_rows,
            server_timeout=server_timeout,
        )

    def query_df(
        self,
        query: str,
        *,
        timespan: tuple[datetime, datetime] | None,
        table: int = 0,
        allow_partial: bool = False,
        max_rows: int = 100_000,
        server_timeout: int = 120,
    ) -> pd.DataFrame:
        """Query once and convert a table; incomplete responses require explicit opt-in."""
        if isinstance(table, bool) or not isinstance(table, int) or table < 0:
            raise ValueError("table must be a nonnegative integer")
        if not isinstance(allow_partial, bool):
            raise ValueError("allow_partial must be a boolean")
        result = self.query(
            query, timespan=timespan, max_rows=max_rows, server_timeout=server_timeout
        )
        return result.to_dataframe(table, allow_partial=allow_partial)

    def close(self) -> None:
        """Release the service client and any connector-owned credential."""
        self._manager.close()

    def __enter__(self) -> AzureMonitorLogsClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
