import sys
import types
from unittest.mock import patch


def test_connect_and_select_one_unit(client):
    rows = client.query_rows("SELECT 1 AS ok")
    assert rows and rows[0]["ok"] == 1


def test_query_scalar(client):
    """query_scalar should return a single value, not a dict."""

    # Patch scalar_row import inside queries module
    def fake_scalar_row(cursor):
        return lambda values: values[0]

    fake_rows_mod = types.SimpleNamespace(scalar_row=fake_scalar_row)
    with patch.dict(sys.modules, {"psycopg.rows": fake_rows_mod}):
        result = client.query_scalar("SELECT 1 AS ok")
        # Our FakeCursor.fetchone() returns 1 when row_factory is set
        assert result == 1


def test_stream_rows(client):
    """stream_rows should yield dicts from a server-side cursor."""
    rows = list(client.stream_rows("SELECT 1 AS ok"))
    assert len(rows) == 1
    assert rows[0]["ok"] == 1


def test_execute_batch(client):
    """execute_batch should return a list of result lists."""
    statements = [
        ("SELECT 1 AS ok", None),
        ("SELECT 2 AS ok", None),
    ]
    results = client.execute_batch(statements)
    assert len(results) == 2
    assert results[0][0]["ok"] == 1


def test_copy_rows_in(client):
    """copy_rows_in should accept rows and return count."""
    count = client.copy_rows_in(
        "test_table",
        ["col1", "col2"],
        [(1, "a"), (2, "b")],
    )
    assert count == 2


def test_copy_rows_out(client):
    """copy_rows_out should return list of tuples."""
    rows = client.copy_rows_out("SELECT * FROM test_table")
    assert len(rows) == 2
    assert rows[0] == (1, "a")


def test_pool_stats_no_pool(client):
    """pool_stats should indicate no pool when not using pooling."""
    stats = client.pool_stats()
    assert stats["pool_enabled"] is False


def test_healthcheck(client):
    """healthcheck should return ok=True with fake connection."""
    result = client.healthcheck()
    assert result["ok"] is True


def test_context_manager(client):
    """Client should work as a context manager."""
    from agent_data_toolkit.postgresql import PostgresClient

    with PostgresClient.from_dsn("postgresql://fake/fakedb") as pg:
        rows = pg.query_rows("SELECT 1 AS ok")
        assert rows and rows[0]["ok"] == 1


def test_connection_info_defaults():
    """ConnectionInfo should have sensible defaults."""
    from agent_data_toolkit.postgresql import ConnectionInfo

    info = ConnectionInfo(dsn="postgresql://localhost/test")
    assert info.prepare_threshold == 5
    assert info.autocommit is True
    assert info.session_settings == {}
    assert info.application_name == "agent-data-toolkit"


def test_schema_introspection(client):
    """schema() should return a dict with 'tables' key."""
    result = client.schema()
    assert "tables" in result


def test_query_namedtuples(client):
    """query_namedtuples should return rows with dotted-attribute access."""
    import sys
    import types
    from collections import namedtuple
    from unittest.mock import patch

    # Create a fake namedtuple_row that returns namedtuples from our fake cursor
    OkRow = namedtuple("Row", ["ok"])

    def fake_namedtuple_row(cursor):
        return lambda values: OkRow(**values) if isinstance(values, dict) else OkRow(*values)

    fake_rows_mod = types.SimpleNamespace(namedtuple_row=fake_namedtuple_row)
    with patch.dict(sys.modules, {"psycopg.rows": fake_rows_mod}):
        rows = client.query_namedtuples("SELECT 1 AS ok")
        assert len(rows) >= 1


def test_server_info(client):
    """server_info should return a dict with at minimum a dsn key."""
    info = client.server_info()
    assert isinstance(info, dict)
    assert "dsn" in info


def test_pool_drain_no_pool(client):
    """pool_drain should raise when pool is not enabled."""
    import pytest

    with pytest.raises(RuntimeError, match="Pool is not enabled"):
        client.pool_drain()
