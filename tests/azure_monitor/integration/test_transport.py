"""Real Azure SDK authentication/decoding with an in-memory transport, no Azure access."""

import json
import time
from datetime import UTC, datetime

import pytest

pytest.importorskip("azure.monitor.query", reason="Install the azure-monitor extra")

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.monitor.query import LogsQueryClient

from agent_data_toolkit.azure_monitor import (
    AzureMonitorLogsClient,
    ConnectionInfo,
    ConnectionManager,
    LogsQueryError,
)


class Credential:
    def __init__(self, lifetime):
        self.calls = 0
        self.lifetime = lifetime

    def get_token(self, *scopes, **kwargs):
        self.calls += 1
        return AccessToken("controlled-fixture-token", int(time.time()) + self.lifetime)


class Response(HttpResponse):
    def __init__(self, request, payload):
        super().__init__(request, None)
        self.status_code = 200
        self.headers = {"content-type": "application/json", "x-ms-request-id": "source-request-1"}
        self.content_type = "application/json"
        self.value = json.dumps(payload).encode()

    def body(self):
        return self.value

    def json(self):
        return json.loads(self.value)


class Transport(HttpTransport):
    def __init__(self):
        self.requests = []
        self.responses = []
        self.payload = None

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def send(self, request, **kwargs):
        self.requests.append(request)
        body = json.loads(request.body)
        value = {
            "tables": [
                {
                    "name": "PrimaryResult",
                    "columns": [{"name": "Count", "type": "long"}],
                    "rows": [[1]],
                }
            ]
        }
        if body["query"] == "partial":
            value["error"] = {"code": "PartialError", "message": "Controlled partial response"}
        response = Response(request, value if self.payload is None else self.payload)
        self.responses.append(response)
        return response


@pytest.mark.parametrize("query", ["success", "partial"])
@pytest.mark.parametrize("lifetime,expected_token_requests", [(3600, 1), (1, 2)])
def test_sdk_transport_retains_native_response_and_reuses_client(
    monkeypatch, query, lifetime, expected_token_requests
):
    from agent_data_toolkit.azure_monitor import connection

    info = ConnectionInfo("22222222-2222-4222-8222-222222222222")
    credential = Credential(lifetime)
    transport = Transport()
    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda credential, info: LogsQueryClient(
            credential,
            endpoint=info.endpoint,
            audience=info.audience,
            transport=transport,
            retry_total=0,
        ),
    )
    window = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    with AzureMonitorLogsClient(ConnectionManager(info, credential)) as client:
        first = client.query(query, timespan=window)
        second = client.query(query, timespan=window)
    assert first.raw_response == transport.responses[0].body()
    assert first.request_id == "source-request-1"
    assert first.metadata()["raw_response_sha256"]
    assert first.status == ("partial" if query == "partial" else "success")
    assert second.tables[0].rows == ((1,),)
    assert len(transport.requests) == 2
    assert credential.calls == expected_token_requests
    request = transport.requests[0]
    assert request.url.startswith(f"{info.endpoint}/v1/workspaces/{info.workspace_id}/query")
    assert json.loads(request.body)["query"] == query


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tables": []},
        {
            "tables": [
                {
                    "name": "PrimaryResult",
                    "columns": [{"name": "Count", "type": "long"}],
                    "rows": [{"Count": 1}],
                }
            ]
        },
        {
            "tables": [
                {
                    "name": "PrimaryResult",
                    "columns": [{"name": "Count", "type": "long"}],
                    "rows": ["1"],
                }
            ]
        },
        {
            "tables": [
                {
                    "name": "PrimaryResult",
                    "columns": [{"name": "Count", "type": "long"}],
                    "rows": [[1, 2]],
                }
            ]
        },
    ],
)
def test_malformed_native_envelopes_are_not_complete_results(monkeypatch, payload):
    from agent_data_toolkit.azure_monitor import connection

    transport = Transport()
    transport.payload = payload
    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda credential, info: LogsQueryClient(
            credential,
            transport=transport,
            retry_total=0,
        ),
    )
    info = ConnectionInfo("22222222-2222-4222-8222-222222222222")
    with AzureMonitorLogsClient.from_info(info, Credential(3600)) as client:
        with pytest.raises(LogsQueryError):
            client.query(
                "probe",
                timespan=(
                    datetime(2026, 1, 1, tzinfo=UTC),
                    datetime(2026, 1, 2, tzinfo=UTC),
                ),
            )
    assert len(transport.requests) == 1
