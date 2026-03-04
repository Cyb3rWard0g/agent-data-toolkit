import pytest

pytestmark = pytest.mark.pg


def test_connect_and_select_one(dsn):
    from agent_data_toolkit.postgresql import PostgresClient

    pg = PostgresClient.from_dsn(dsn)
    try:
        rows = pg.query_rows("SELECT 1 AS ok")
        assert rows and rows[0]["ok"] == 1
    finally:
        pg.close()
