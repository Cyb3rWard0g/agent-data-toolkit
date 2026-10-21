"""Azure Monitor Logs / Sentinel connector with optional Azure SDK dependencies."""

from .client import AzureMonitorLogsClient
from .connection import ConnectionInfo, ConnectionManager
from .queries import LogsQueryError, LogsTable, PartialResultError, QueryResult

__all__ = [
    "AzureMonitorLogsClient",
    "ConnectionInfo",
    "ConnectionManager",
    "LogsQueryError",
    "LogsTable",
    "PartialResultError",
    "QueryResult",
]
