import os

import pytest


def _env_dsn():
    return os.getenv("PG_DSN") or os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL")


def pytest_collection_modifyitems(config, items):
    if _env_dsn():
        return
    skip_pg = pytest.mark.skip(reason="Set PG_DSN/POSTGRES_DSN/DATABASE_URL to run PG tests")
    for item in items:
        if "pg" in item.keywords:
            item.add_marker(skip_pg)


@pytest.fixture(scope="session")
def dsn():
    d = _env_dsn()
    assert d, "Set PG_DSN/POSTGRES_DSN/DATABASE_URL to run PG tests"
    return d
