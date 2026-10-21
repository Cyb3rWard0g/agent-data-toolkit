"""Explicit opt-in live identity/workspace gate; never runs from ambient Azure settings."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from agent_data_toolkit.azure_monitor import AzureMonitorLogsClient

pytestmark = [
    pytest.mark.azure_live,
    pytest.mark.skipif(
        os.getenv("ADT_AZURE_LIVE") != "1",
        reason="Set ADT_AZURE_LIVE=1 and explicit AZURE_LOGS_* settings for live acceptance",
    ),
]


def test_live_identity_and_workspace_query():
    end = datetime.now(UTC)
    with AzureMonitorLogsClient.from_env() as client:
        result = client.query("print connector_probe=1", timespan=(end - timedelta(minutes=5), end))
    assert result.complete
    assert result.to_dataframe().iloc[0]["connector_probe"] == 1
