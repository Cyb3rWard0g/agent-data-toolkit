from __future__ import annotations

import types
from contextlib import contextmanager

import pytest


class FakeCopy:
    """Minimal COPY context manager."""

    def __init__(self):
        self._rows_in: list = []
        self._rows_out: list[tuple] = [(1, "a"), (2, "b")]

    def write_row(self, row):
        self._rows_in.append(row)

    def rows(self):
        return iter(self._rows_out)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeCursor:
    def __init__(self, *, row_factory=None):
        self._sql = None
        # Minimal "description" to mimic psycopg so .fetchall() is valid
        self.description = [("ok",)]
        self.itersize = 100
        self._row_factory = row_factory

    def execute(self, sql, params=None):
        self._sql = str(sql)
        self.description = [("ok",)]

    def fetchall(self):
        return [{"ok": 1}]

    def fetchone(self):
        if self._row_factory is not None:
            return 1  # scalar_row returns the first column value
        # Support version queries for server_info
        if self._sql and "version()" in self._sql:
            return {"version": "PostgreSQL 17.0 (fake)"}
        return {"ok": 1}

    def fetchmany(self, size=None):
        return []

    def copy(self, stmt):
        return FakeCopy()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter([{"ok": 1}])


class FakePipeline:
    """Minimal pipeline context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    autocommit = True

    def cursor(self, name=None, *, row_factory=None, binary=False):
        return FakeCursor(row_factory=row_factory)

    def execute(self, sql, params=None):
        """Support for DISCARD ALL and other direct connection executes."""
        pass

    def pipeline(self):
        return FakePipeline()

    @contextmanager
    def transaction(self):
        yield

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _patch_conn(monkeypatch):
    """
    Patch connection creation + psycopg bits so tests don't need a real DB.
    """
    import agent_data_toolkit.postgresql.connection as conn_mod

    # Never open a real socket; always hand back our FakeConn
    monkeypatch.setattr(
        conn_mod.ConnectionManager,
        "_try_connect_once",
        lambda self, dsn: FakeConn(),
    )

    # Provide a minimal psycopg shim with the attributes we use
    def _fake_import_psycopg():
        sql_mod = types.SimpleNamespace(SQL=str, Identifier=str, Literal=lambda x: x)
        fake_psycopg = types.SimpleNamespace(sql=sql_mod)
        dict_row = object()  # not used by the fakes, but keeps signatures intact
        return fake_psycopg, dict_row

    monkeypatch.setattr(conn_mod, "_import_psycopg", _fake_import_psycopg)

    # Patch scalar_row import used by query_scalar
    import agent_data_toolkit.postgresql.queries as queries_mod

    _fake_scalar_row = object()

    def _fake_import_psycopg_for_queries():
        sql_mod = types.SimpleNamespace(SQL=str, Identifier=str, Literal=lambda x: x)
        fake_psycopg = types.SimpleNamespace(sql=sql_mod)
        return fake_psycopg, object()

    monkeypatch.setattr(queries_mod, "_import_psycopg", _fake_import_psycopg_for_queries)

    # Patch _import_sql so COPY operations don't fail when psycopg isn't importable
    class FakeSqlModule:
        """Mimic psycopg.sql with simple string-based composition."""

        @staticmethod
        def SQL(s):
            return s

        @staticmethod
        def Identifier(s):
            return s

        @staticmethod
        def Literal(x):
            return x

        @staticmethod
        def Composed(parts):
            return "".join(str(p) for p in parts)

    _fake_sql_mod = FakeSqlModule()
    _fake_sql_mod.SQL = type(
        "FakeSQL",
        (),
        {
            "__init__": lambda self, s: setattr(self, "_s", s),
            "__str__": lambda self: self._s,
            "format": lambda self, *a, **kw: self._s,
            "join": lambda self, parts: self._s.join(str(p) for p in parts),
        },
    )

    def _fake_import_sql():
        return types.SimpleNamespace(
            SQL=str,
            Identifier=str,
            Literal=lambda x: x,
            Composed=lambda parts: "".join(str(p) for p in parts),
        )

    monkeypatch.setattr(queries_mod, "_import_sql", _fake_import_sql)


@pytest.fixture
def client():
    from agent_data_toolkit.postgresql import PostgresClient

    return PostgresClient.from_dsn("postgresql://fake/fakedb")
