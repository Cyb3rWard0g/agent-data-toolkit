"""Connector boundaries using independent SDK-shaped responses, with no source access."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest

from agent_data_toolkit.azure_monitor import (
    AzureMonitorLogsClient,
    ConnectionInfo,
    ConnectionManager,
    LogsQueryError,
    PartialResultError,
)

WORKSPACE = "22222222-2222-4222-8222-222222222222"
WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))


def response(*, rows=None, status="Success"):
    table = SimpleNamespace(
        name="PrimaryResult",
        columns=["Device", "Count"],
        columns_types=["string", "long"],
        rows=[["host-1", 2]] if rows is None else rows,
    )
    return SimpleNamespace(
        status=status,
        tables=[table],
        partial_data=[table],
        partial_error=SimpleNamespace(code="PartialError", message="Source did not finish"),
    )


@pytest.fixture
def fixture(monkeypatch):
    from agent_data_toolkit.azure_monitor import connection

    credential = Mock(get_token=Mock())
    sdk = Mock()
    sdk.query_workspace.return_value = response()
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(connection, "_make_client", factory)
    manager = ConnectionManager(ConnectionInfo(WORKSPACE), credential)
    return SimpleNamespace(
        client=AzureMonitorLogsClient(manager),
        manager=manager,
        credential=credential,
        sdk=sdk,
        factory=factory,
    )


def test_query_once_preserves_schema_window_and_client(fixture):
    result = fixture.client.query("SecurityEvent | take 5", timespan=WINDOW)
    assert result.complete
    assert result.tables[0].rows == (("host-1", 2),)
    assert result.tables[0].column_types == ("string", "long")
    fixture.sdk.query_workspace.assert_called_once_with(
        WORKSPACE,
        "SecurityEvent | take 5",
        timespan=WINDOW,
        server_timeout=120,
        raw_response_hook=ANY,
    )
    frame = result.to_dataframe()
    assert frame.to_dict("records") == [{"Device": "host-1", "Count": 2}]
    assert frame.attrs["azure_monitor"]["complete"] is True
    fixture.client.query_df("SecurityEvent | take 1", timespan=WINDOW)
    fixture.factory.assert_called_once()
    assert fixture.sdk.query_workspace.call_count == 2


def test_explicit_none_disables_api_time_filter_without_claiming_a_window(fixture):
    result = fixture.client.query("SecurityEvent", timespan=None)
    assert fixture.sdk.query_workspace.call_args.kwargs["timespan"] is None
    assert result.metadata()["start_time"] is None
    assert result.metadata()["end_time"] is None


@pytest.mark.parametrize("rows", [[], [["host-1", 2]]])
def test_partial_results_retained_but_dataframe_requires_opt_in(fixture, rows):
    fixture.sdk.query_workspace.return_value = response(rows=rows, status="PartialError")
    result = fixture.client.query("SecurityEvent", timespan=WINDOW)
    assert not result.complete
    assert result.status == "partial"
    assert result.error_code == "PartialError"
    with pytest.raises(PartialResultError) as error:
        result.to_dataframe()
    assert error.value.result is result
    assert list(result.to_dataframe(allow_partial=True).columns) == ["Device", "Count"]


def test_empty_success_preserves_column_schema(fixture):
    fixture.sdk.query_workspace.return_value = response(rows=[])
    result = fixture.client.query("SecurityEvent", timespan=WINDOW)
    assert result.complete
    assert result.to_dataframe().shape == (0, 2)
    assert result.metadata()["tables"][0]["source_row_count"] == 0


def test_total_client_row_limit_is_explicit_across_tables(fixture):
    value = response(rows=[["one", 1], ["two", 2]])
    value.tables.append(response().tables[0])
    fixture.sdk.query_workspace.return_value = value
    result = fixture.client.query("SecurityEvent", timespan=WINDOW, max_rows=1)
    assert result.truncated
    assert not result.complete
    assert [len(t.rows) for t in result.tables] == [1, 0]
    assert [t.source_row_count for t in result.tables] == [2, 1]
    with pytest.raises(PartialResultError):
        result.to_dataframe()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": " "},
        {"query": "x" * 65_537},
        {"timespan": timedelta(hours=1)},
        {"timespan": WINDOW[::-1]},
        {"timespan": (datetime(2026, 1, 1), WINDOW[1])},
        {"max_rows": 0},
        {"max_rows": True},
        {"max_rows": 1_000_001},
        {"server_timeout": 601},
        {"server_timeout": False},
    ],
)
def test_invalid_query_rejected_before_sdk_or_credentials(fixture, kwargs):
    values = {"query": "SecurityEvent", "timespan": WINDOW, **kwargs}
    with pytest.raises(ValueError):
        fixture.client.query(**values)
    fixture.factory.assert_not_called()
    fixture.credential.get_token.assert_not_called()


@pytest.mark.parametrize("fault", ["status", "rows", "columns", "types", "duplicates", "tables"])
def test_malformed_response_is_never_a_complete_result(fixture, fault):
    value = response()
    if fault == "status":
        value.status = "Unknown"
    elif fault == "tables":
        value.tables = None
    elif fault == "rows":
        value.tables[0].rows = [["wrong-width"]]
    elif fault == "columns":
        value.tables[0].columns = []
    elif fault == "duplicates":
        value.tables[0].columns = ["Device", "Device"]
    else:
        value.tables[0].columns_types = []
    fixture.sdk.query_workspace.return_value = value
    with pytest.raises(LogsQueryError):
        fixture.client.query("SecurityEvent", timespan=WINDOW)


def test_request_failure_is_sanitized_and_not_retried(fixture):
    fixture.sdk.query_workspace.side_effect = RuntimeError("credential-canary")
    with pytest.raises(LogsQueryError) as error:
        fixture.client.query("SecurityEvent", timespan=WINDOW)
    assert "credential-canary" not in str(error.value)
    fixture.sdk.query_workspace.assert_called_once()


def test_close_is_idempotent_and_does_not_close_borrowed_credential(fixture):
    fixture.client.query("SecurityEvent", timespan=WINDOW)
    fixture.client.close()
    fixture.client.close()
    fixture.sdk.close.assert_called_once()
    fixture.credential.close.assert_not_called()
    with pytest.raises(RuntimeError, match="closed"):
        fixture.client.query("SecurityEvent", timespan=WINDOW)


def test_owned_credential_closed_even_when_sdk_close_fails(monkeypatch):
    from agent_data_toolkit.azure_monitor import connection

    credential = Mock()
    sdk = Mock()
    sdk.close.side_effect = RuntimeError("close failed")
    monkeypatch.setattr(connection, "_make_client", lambda *args: sdk)
    manager = ConnectionManager(ConnectionInfo(WORKSPACE), credential, owns_credential=True)
    assert manager.client is sdk
    with pytest.raises(RuntimeError, match="close failed"):
        manager.close()
    credential.close.assert_called_once()
    manager.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "invalid"),
        ("endpoint", "http://localhost"),
        ("endpoint", "https://user:password@example.org"),
        ("endpoint", "https://example.org/path"),
        ("audience", "https://example.org?token=x"),
    ],
)
def test_invalid_connection_rejected(field, value):
    with pytest.raises(ValueError):
        ConnectionInfo(**{"workspace_id": WORKSPACE, field: value})


def test_environment_auth_is_explicit_and_credential_is_reused(monkeypatch):
    from agent_data_toolkit.azure_monitor import connection

    monkeypatch.setenv("AZURE_LOGS_WORKSPACE_ID", WORKSPACE)
    monkeypatch.setenv("AZURE_LOGS_AUTH", "azure-cli")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    credential = Mock()
    factory = Mock(return_value=credential)
    monkeypatch.setattr(connection, "_environment_credential", factory)
    with AzureMonitorLogsClient.from_env():
        pass
    factory.assert_called_once_with("azure-cli", None)
    credential.close.assert_called_once()
