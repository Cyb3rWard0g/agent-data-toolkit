from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ───────────────────────── internals / utils ─────────────────────────


def _import_psycopg() -> tuple[Any, Any]:
    """
    Lazy-import psycopg so the module can be imported without PostgreSQL extras.

    Returns:
        (psycopg, dict_row)

    Raises:
        RuntimeError: if psycopg isn't available in the kernel env.
    """
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore

        return psycopg, dict_row
    except Exception as e:
        raise RuntimeError(
            "The PostgreSQL connector requires psycopg. "
            "Install extras with: pip install 'agent-data-toolkit[postgresql]' "
            "or directly: pip install 'psycopg[binary]'"
        ) from e


def _import_row_factories() -> dict[str, Any]:
    """
    Lazy-import all psycopg row factories.

    Returns:
        Dict mapping factory name to factory callable.
    """
    try:
        from psycopg.rows import (  # type: ignore
            dict_row,
            namedtuple_row,
            scalar_row,
            tuple_row,
        )

        return {
            "dict_row": dict_row,
            "namedtuple_row": namedtuple_row,
            "scalar_row": scalar_row,
            "tuple_row": tuple_row,
        }
    except Exception:
        return {}


def _import_conninfo():
    """
    Lazy-import psycopg.conninfo utilities for robust DSN handling.

    These handle all libpq conninfo formats (URI, key=value, service files)
    and respect PG* environment variable hierarchy.

    Returns:
        (conninfo_to_dict, make_conninfo) or (None, None) if unavailable.
    """
    try:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo  # type: ignore

        return conninfo_to_dict, make_conninfo
    except Exception:
        return None, None


def _import_psycopg_pool():
    """
    Return (ConnectionPool, NullConnectionPool) if available; otherwise (None, None).
    Kept lazy to avoid forcing the optional dependency.
    """
    try:
        from psycopg_pool import ConnectionPool, NullConnectionPool  # type: ignore

        return ConnectionPool, NullConnectionPool
    except Exception:
        return None, None


def _redact_dsn(dsn: str) -> str:
    """
    Produce a redacted, log-safe view of a DSN (no password).
    Never raises: falls back to a generic string on parse errors.
    """
    try:
        u = urlparse(dsn)
        if not u.scheme:
            return "dsn=<raw>; (unable to parse safely for redaction)"
        user = u.username or ""
        host = u.hostname or ""
        db = (u.path or "").lstrip("/")
        q = dict(parse_qsl(u.query))
        return (
            f"driver={u.scheme} user={user} host={host} "
            f"port={u.port} db={db} sslmode={q.get('sslmode')!r}"
        )
    except Exception:
        return "dsn=<unparsed>; (redaction parse failed)"


def _with_query_param(dsn: str, key: str, value: str | None) -> str:
    """
    Return DSN with a specific query parameter set (or removed if value is None).
    Silently returns input on parse errors or when DSN has no URI scheme.
    """
    try:
        u = urlparse(dsn)
        if not u.scheme:
            return dsn
        q = dict(parse_qsl(u.query))
        if value is None:
            q.pop(key, None)
        else:
            q[key] = value
        return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))
    except Exception:
        return dsn


def _ensure_sslmode(dsn: str, default: str = "require", *, enforce_minimum: bool = False) -> str:
    """
    Ensure DSN contains an sslmode. If missing, add ?sslmode=<default>.
    If enforce_minimum=True and sslmode is present but weak (disable/allow/prefer),
    replace it with <default>. Never raises on parse errors; returns original DSN on failure.
    """
    try:
        u = urlparse(dsn)
        if not u.scheme:
            return dsn
        q = dict(parse_qsl(u.query))
        if "sslmode" not in q:
            q["sslmode"] = default
            return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))
        if enforce_minimum:
            if str(q.get("sslmode", "")).lower() in {"disable", "allow", "prefer"}:
                q["sslmode"] = default
                return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))
        return dsn
    except Exception:
        return dsn


def _override_sslmode(dsn: str, new_mode: str) -> str:
    """Force-override sslmode to new_mode; returns original DSN on parse errors."""
    return _with_query_param(dsn, "sslmode", new_mode)


def _is_localhost_host(dsn: str) -> bool:
    """
    Heuristic: treat DSN host as localhost/loopback when safe to fall back to non-SSL.
    """
    try:
        u = urlparse(dsn)
        host = (u.hostname or "").lower()
        return host in {"", "localhost", "127.0.0.1", "::1"}
    except Exception:
        return False


def dsn_from_env(*, require_ssl: bool = False, default_sslmode: str = "require") -> str:
    """
    Resolve DSN from kernel environment variables in this order:
      - PG_DSN
      - POSTGRES_DSN
      - DATABASE_URL

    Args:
        require_ssl: If True, ensure DSN has sslmode >= <default_sslmode>.
        default_sslmode: sslmode to use when require_ssl=True and DSN has none (or is weak).

    Raises:
        RuntimeError: if no DSN env var is set.
    """
    dsn = (
        os.environ.get("PG_DSN") or os.environ.get("POSTGRES_DSN") or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        raise RuntimeError("Set PG_DSN / POSTGRES_DSN / DATABASE_URL in the kernel environment.")
    return (
        _ensure_sslmode(dsn, default_sslmode, enforce_minimum=require_ssl) if require_ssl else dsn
    )


# ───────────────────────── public config types ─────────────────────────


@dataclass(frozen=True)
class ConnectionInfo:
    """
    Connection configuration used by ConnectionManager.

    Attributes:
        dsn: PostgreSQL connection string (can include ?sslmode=...).
        connect_timeout: TCP/connection timeout (seconds).
        application_name: Appears in pg_stat_activity for observability.
        statement_timeout_ms: Per-session statement timeout (ms). None = don't set.
        search_path: Optional schema search_path to set after connect.
        autocommit: Enable autocommit (default True for notebook/agent workflows).
        prepare_threshold: After this many executions of the same query,
                           psycopg auto-prepares it server-side for a perf boost.
                           5 is good for agents repeating schema introspection queries.
                           Set to 0 for immediate preparation, None to disable.
        session_settings: Extra key/value pairs applied via ``SET <key> = <value>``.
    """

    dsn: str
    connect_timeout: int = 10
    application_name: str = "agent-data-toolkit"
    statement_timeout_ms: int | None = 30_000
    search_path: str | list[str] | None = None
    autocommit: bool = True
    prepare_threshold: int | None = 5
    session_settings: dict[str, str | int] = field(default_factory=dict)


# ───────────────────────── manager ─────────────────────────


class ConnectionManager:
    """
    Manages PostgreSQL connections for the kernel, with optional connection pooling.

    Leverages psycopg 3 / psycopg_pool advanced features:

    * **Pool callbacks** — ``configure`` (apply session settings once per new
      physical connection), ``check`` (validate before checkout), ``reset``
      (clean up on return).
    * **Prepared statements** — ``prepare_threshold`` for auto-preparing
      repeated queries (big win for agents).
    * **Pool lifecycle** — explicit ``open()``/``close()`` with proper
      ``max_lifetime``, ``max_idle``, ``reconnect_timeout``.
    * **NullConnectionPool** — serverless-friendly: no idle connections, same API.
    * **Dynamic resize** — ``pool_resize()`` at runtime.
    * **Rich stats** — ``pool_stats()`` exposes 15+ metrics.
    """

    def __init__(
        self,
        info: ConnectionInfo,
        *,
        # ── Pool toggle ──────────────────────────────
        use_pool: bool = False,
        null_pool: bool = False,
        # ── Pool sizing ──────────────────────────────
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        # ── Pool lifecycle tuning ────────────────────
        pool_max_lifetime: float = 3600.0,
        pool_max_idle: float = 600.0,
        pool_reconnect_timeout: float = 300.0,
        pool_max_waiting: int = 0,
        pool_num_workers: int = 3,
        # ── Custom callbacks (advanced) ──────────────
        pool_reconnect_failed: Callable | None = None,
        # ── Transient failure handling (non-pooled) ──
        connect_retries: int = 0,
        connect_backoff_base: float = 0.5,
        # ── SSL fallback ─────────────────────────────
        ssl_fallback_on_error: bool = False,
        ssl_fallback_allow_any_host: bool = False,
    ):
        self.info = info
        self._pool: Any = None
        self._pool_opened = False
        self.logger = logging.getLogger(f"{__name__}.ConnectionManager")
        self._connect_retries = max(0, int(connect_retries))
        self._connect_backoff_base = max(0.0, float(connect_backoff_base))
        self._ssl_fallback_on_error = bool(ssl_fallback_on_error)
        self._ssl_fallback_allow_any_host = bool(ssl_fallback_allow_any_host)

        if use_pool:
            self._init_pool(
                null_pool=null_pool,
                min_size=pool_min_size,
                max_size=pool_max_size,
                timeout=pool_timeout,
                max_lifetime=pool_max_lifetime,
                max_idle=pool_max_idle,
                reconnect_timeout=pool_reconnect_timeout,
                max_waiting=pool_max_waiting,
                num_workers=pool_num_workers,
                reconnect_failed=pool_reconnect_failed,
            )

    # ────────────────── pool initialisation ──────────────────

    def _init_pool(
        self,
        *,
        null_pool: bool,
        min_size: int,
        max_size: int,
        timeout: float,
        max_lifetime: float,
        max_idle: float,
        reconnect_timeout: float,
        max_waiting: int,
        num_workers: int,
        reconnect_failed: Callable | None,
    ) -> None:
        ConnectionPool, NullConnectionPool = _import_psycopg_pool()
        if ConnectionPool is None:
            raise RuntimeError(
                "psycopg_pool is required for pooling. "
                "Install with: pip install 'psycopg_pool' or the extras: "
                "pip install 'agent-data-toolkit[postgresql]'"
            )

        PoolClass = NullConnectionPool if null_pool else ConnectionPool
        psycopg, dict_row = _import_psycopg()

        # Connect kwargs — passed to psycopg.Connection.connect() by the pool
        # every time a new physical connection is created.
        connect_kwargs: dict[str, Any] = {
            "connect_timeout": self.info.connect_timeout,
            "row_factory": dict_row,
            "application_name": self.info.application_name,
            "autocommit": self.info.autocommit,
        }
        if self.info.prepare_threshold is not None:
            connect_kwargs["prepare_threshold"] = self.info.prepare_threshold

        pool_kwargs: dict[str, Any] = {
            "kwargs": connect_kwargs,
            "min_size": 0 if null_pool else min_size,
            "max_size": max_size,
            "timeout": timeout,
            "max_lifetime": max_lifetime,
            "max_idle": max_idle,
            "reconnect_timeout": reconnect_timeout,
            "max_waiting": max_waiting,
            "num_workers": num_workers,
            "open": False,  # explicit lifecycle — we call open() ourselves
            "name": f"adt-pool-{id(self)}",
            # ── Pool callbacks (the key psycopg_pool feature) ──
            "configure": self._pool_configure,
            "check": ConnectionPool.check_connection,
            "reset": self._pool_reset,
        }
        if reconnect_failed is not None:
            pool_kwargs["reconnect_failed"] = reconnect_failed
        else:
            pool_kwargs["reconnect_failed"] = self._default_reconnect_failed

        try:
            self._pool = PoolClass(self.info.dsn, **pool_kwargs)
            self._pool.open(wait=True, timeout=timeout)
            self._pool_opened = True
            label = "NullConnectionPool" if null_pool else "ConnectionPool"
            self.logger.info(
                "Opened %s (min=%d, max=%d, max_lifetime=%ds, max_idle=%ds)",
                label,
                pool_kwargs["min_size"],
                max_size,
                max_lifetime,
                max_idle,
            )
        except Exception as e:
            if self._should_attempt_ssl_fallback(self.info.dsn):
                insecure_dsn = _override_sslmode(self.info.dsn, "disable")
                try:
                    self._pool = PoolClass(insecure_dsn, **pool_kwargs)
                    self._pool.open(wait=True, timeout=timeout)
                    self._pool_opened = True
                    self.logger.warning(
                        "Opened pool WITHOUT SSL after failure (host is local). DSN=%s",
                        _redact_dsn(insecure_dsn),
                    )
                except Exception as e2:
                    self.logger.error("Pool init failed (fallback also failed): %s", e2)
                    raise
            else:
                self.logger.error("Failed to initialise connection pool: %s", str(e))
                raise

    # ── Pool callbacks ──────────────────────────────────────

    def _pool_configure(self, conn: Any) -> None:
        """
        Called once per *new* physical connection the pool creates.

        Session settings are applied here — they persist for the lifetime
        of this physical connection instead of being re-applied on every checkout.
        """
        self._apply_session_settings(conn)
        self.logger.debug("Configured new pool connection")

    @staticmethod
    def _pool_reset(conn: Any) -> None:
        """
        Called when a connection is returned to the pool.

        ``DISCARD ALL`` resets search_path, prepared statements, temp tables,
        advisory locks, etc.  Safe for autocommit connections.
        """
        conn.execute("DISCARD ALL")

    def _default_reconnect_failed(self, pool: Any) -> None:
        """Called when the pool can't reconnect for ``reconnect_timeout`` seconds."""
        self.logger.critical(
            "Pool reconnection failed after exhausting reconnect_timeout. Pool: %s",
            getattr(pool, "name", "unknown"),
        )

    # ────────────────── factory methods ──────────────────

    @classmethod
    def from_env(
        cls,
        *,
        force_ssl: bool = True,
        min_sslmode: str = "require",
        **kwargs,
    ) -> ConnectionManager:
        """
        Create a ConnectionManager using DSN from kernel env vars.

        Args:
            force_ssl: Ensure DSN has sslmode >= min_sslmode (default: True).
            min_sslmode: Minimum sslmode to enforce when force_ssl=True.
            **kwargs: Forwarded to constructor (e.g., use_pool=True, connect_retries=2).
        """
        dsn = dsn_from_env(require_ssl=force_ssl, default_sslmode=min_sslmode)
        return cls(ConnectionInfo(dsn=dsn), **kwargs)

    # ────────────────── session settings ──────────────────

    def _apply_session_settings(self, conn: Any) -> None:
        """
        Apply session-level settings on a connection.

        Uses SET (not SET LOCAL) so changes persist for the session even
        when autocommit=True.
        """
        psycopg, _ = _import_psycopg()
        SQL = psycopg.sql.SQL
        Identifier = psycopg.sql.Identifier

        st = self.info.statement_timeout_ms
        sp = self.info.search_path
        extra = self.info.session_settings

        with conn.cursor() as cur:
            if st is not None:
                cur.execute(f"SET statement_timeout = {int(st)}")

            if sp:
                if isinstance(sp, str):
                    cur.execute("SET search_path = %s", (sp,))
                else:
                    parts = [Identifier(s) for s in sp]
                    query = SQL("SET search_path TO {}").format(SQL(", ").join(parts))
                    cur.execute(query)

            for key, value in extra.items():
                stmt = SQL("SET {} = %s").format(Identifier(str(key)))
                cur.execute(stmt, (value,))

    # ────────────────── SSL fallback policy ──────────────────

    def _should_attempt_ssl_fallback(self, dsn: str) -> bool:
        if not self._ssl_fallback_on_error:
            return False
        if self._ssl_fallback_allow_any_host:
            return True
        return _is_localhost_host(dsn)

    # ────────────────── raw connection (non-pooled) ──────────────────

    def _try_connect_once(self, dsn: str) -> Any:
        psycopg, dict_row = _import_psycopg()
        self.logger.info("Connecting to PostgreSQL (%s).", _redact_dsn(dsn))

        kwargs: dict[str, Any] = {
            "connect_timeout": self.info.connect_timeout,
            "row_factory": dict_row,
            "application_name": self.info.application_name,
        }
        if self.info.prepare_threshold is not None:
            kwargs["prepare_threshold"] = self.info.prepare_threshold

        conn = psycopg.connect(dsn, **kwargs)
        conn.autocommit = self.info.autocommit
        self._apply_session_settings(conn)
        self.logger.info("Connection established.")
        return conn

    def _new_connection(self) -> Any:
        """Open and configure a new psycopg connection with retry/backoff."""
        attempts = self._connect_retries + 1
        delay = self._connect_backoff_base
        last_exc: Exception | None = None

        for i in range(1, attempts + 1):
            try:
                return self._try_connect_once(self.info.dsn)
            except Exception as e:
                last_exc = e
                self.logger.error("Connect failed (attempt %d/%d): %s", i, attempts, str(e))
                if i < attempts and delay > 0:
                    time.sleep(delay)
                    delay *= 2.0

        if self._should_attempt_ssl_fallback(self.info.dsn):
            insecure_dsn = _override_sslmode(self.info.dsn, "disable")
            try:
                self.logger.warning(
                    "SSL failed; trying NON-SSL fallback (dev only). DSN=%s",
                    _redact_dsn(insecure_dsn),
                )
                return self._try_connect_once(insecure_dsn)
            except Exception as e2:
                last_exc = e2
                self.logger.error("NON-SSL fallback also failed: %s", str(e2))

        raise RuntimeError(f"Failed to connect to PostgreSQL: {last_exc}") from last_exc

    # ────────────────── public API ──────────────────

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """
        Context manager yielding a connection (new or from pool).

        When using a pool, the pool's ``configure`` callback has already applied
        session settings when the connection was first created, and the ``check``
        callback validates it before checkout.
        """
        if self._pool is None:
            conn = self._new_connection()
            try:
                yield conn
            finally:
                try:
                    conn.close()
                except Exception as e:
                    self.logger.warning("Error closing connection: %s", str(e))
        else:
            # pool.connection() is a context manager that automatically calls
            # check on checkout and reset on return.
            with self._pool.connection() as conn:
                yield conn

    def close(self) -> None:
        """Close the connection pool (if any)."""
        try:
            if self._pool and self._pool_opened:
                self._pool.close()
                self._pool_opened = False
                self.logger.info("Connection pool closed.")
        except Exception as e:
            self.logger.warning("Error closing connection pool: %s", str(e))

    def pool_drain(self) -> None:
        """
        Replace all connections in the pool with fresh ones.

        Useful after changing session settings, type adapters, or when
        connections may have been left in an inconsistent state.
        """
        if not self._pool:
            raise RuntimeError("Pool is not enabled — cannot drain.")
        if hasattr(self._pool, "drain"):
            self._pool.drain()
            self.logger.info("Pool drained — all connections will be replaced.")
        else:
            self.logger.warning("Pool does not support drain().")

    def server_info(self) -> dict[str, Any]:
        """
        Return server metadata: version, timezone, backend PID, etc.

        Leverages psycopg's ``ConnectionInfo`` for accurate introspection
        without manual queries where possible.

        Returns:
            Dict with keys: server_version, server_version_str, timezone,
            backend_pid, encoding, transaction_status, dsn (redacted).
        """
        try:
            with self.connection() as conn:
                info: dict[str, Any] = {"dsn": _redact_dsn(self.info.dsn)}

                # Use psycopg's ConnectionInfo if available
                ci = getattr(conn, "info", None)
                if ci is not None:
                    info["server_version"] = getattr(ci, "server_version", None)
                    info["timezone"] = getattr(ci, "timezone", None)
                    info["backend_pid"] = getattr(ci, "backend_pid", None)
                    info["encoding"] = getattr(ci, "encoding", None)
                    info["transaction_status"] = str(getattr(ci, "transaction_status", None))
                    pv = getattr(ci, "parameter_status", None)
                    if pv:
                        info["server_version_str"] = pv("server_version")
                        info["server_encoding"] = pv("server_encoding")
                        info["client_encoding"] = pv("client_encoding")
                        info["is_superuser"] = pv("is_superuser")
                        info["standard_conforming_strings"] = pv("standard_conforming_strings")
                else:
                    # Fallback: query for version
                    with conn.cursor() as cur:
                        cur.execute("SELECT version()")
                        row = cur.fetchone()
                        info["server_version_str"] = (
                            row["version"] if isinstance(row, dict) else row
                        )

                return info
        except Exception as e:
            self.logger.warning("Failed to get server info: %s", e)
            return {"error": str(e)}

    def pool_stats(self) -> dict[str, Any]:
        """
        Return comprehensive pool statistics.

        Keys include: pool_min, pool_max, pool_size, pool_available,
        requests_waiting, requests_num, requests_queued, requests_wait_ms,
        requests_errors, usage_ms, returns_bad, connections_num,
        connections_ms, connections_errors, connections_lost.
        """
        if not self._pool:
            return {"pool_enabled": False}
        try:
            stats: dict[str, Any] = {"pool_enabled": True}
            if hasattr(self._pool, "get_stats"):
                stats.update(self._pool.get_stats())
            if hasattr(self._pool, "name"):
                stats["pool_name"] = self._pool.name
            return stats
        except Exception as e:
            self.logger.warning("Error getting pool stats: %s", str(e))
            return {"pool_enabled": True, "error": str(e)}

    def pool_resize(self, min_size: int, max_size: int | None = None) -> None:
        """Dynamically resize the connection pool at runtime."""
        if not self._pool:
            raise RuntimeError("Pool is not enabled — cannot resize.")
        self._pool.resize(min_size=min_size, max_size=max_size)
        self.logger.info("Pool resized: min=%d, max=%s", min_size, max_size)

    def pool_check(self) -> None:
        """Verify all idle connections; discard broken or expired ones."""
        if not self._pool:
            raise RuntimeError("Pool is not enabled — cannot check.")
        self._pool.check()
        self.logger.debug("Pool check completed.")

    def healthcheck(self, *, timeout: int = 5) -> dict[str, Any]:
        """
        Run a simple ``SELECT 1`` to verify connectivity.

        Returns:
            ``{"ok": True}`` on success, else ``{"ok": False, "error": "..."}``.
        """
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout * 1000)}")
                cur.execute("SELECT 1")
                _ = cur.fetchone()
            return {"ok": True}
        except Exception as e:
            self.logger.warning("Healthcheck failed: %s", e)
            return {"ok": False, "error": str(e)}

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"dsn={_redact_dsn(self.info.dsn)}, "
            f"pool={'yes' if self._pool else 'no'}, "
            f"autocommit={self.info.autocommit}, "
            f"prepare_threshold={self.info.prepare_threshold}, "
            f"app={self.info.application_name!r})"
        )
