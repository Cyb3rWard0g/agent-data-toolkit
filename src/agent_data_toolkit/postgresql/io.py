from __future__ import annotations

import contextlib
import logging
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Type-only imports so we don’t require heavy deps at import time
if TYPE_CHECKING:
    import pandas as pd
    import pyarrow as pa

_LOG = logging.getLogger(__name__)


def _require_pandas():
    try:
        import pandas as pd  # type: ignore

        return pd
    except Exception as e:
        raise RuntimeError(
            "pandas is required for DataFrame operations. "
            "Install extras with: pip install 'agent-data-toolkit[postgresql]'"
        ) from e


def _require_pyarrow():
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore

        return pa, pq
    except Exception as e:
        raise RuntimeError(
            "pyarrow is required for Arrow/Parquet operations. "
            "Install extras with: pip install 'agent-data-toolkit[postgresql]'"
        ) from e


def rows_to_dataframe(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """
    Convert an iterable of dict rows into a pandas DataFrame.

    - Returns an empty DataFrame when the iterable is empty.
    - pandas is imported lazily so importing this module doesn’t require the extras.
    """
    pd = _require_pandas()
    data = list(rows)
    return pd.DataFrame(data) if data else pd.DataFrame()


def dataframe_to_arrow(df: pd.DataFrame, schema: pa.Schema | None = None) -> pa.Table:
    """
    Convert a pandas DataFrame to a PyArrow Table.

    Args:
        df: Input DataFrame.
        schema: Optional explicit Arrow schema (recommended for all-null or mixed-type columns).
                Example:
                    >>> import pyarrow as pa
                    >>> sch = pa.schema([("id", pa.int64()), ("payload", pa.string())])
                    >>> table = dataframe_to_arrow(df, schema=sch)

    Returns:
        pyarrow.Table
    """
    pa, _ = _require_pyarrow()
    return pa.Table.from_pandas(df, schema=schema, preserve_index=False)


def write_parquet(
    df: pd.DataFrame,
    path: str | Path,
    *,
    compression: str = "zstd",
    overwrite: bool = True,
    schema: pa.Schema | None = None,
    file_mode: int | None = None,
) -> str:
    """
    Write a DataFrame to a Parquet file atomically (temp file + replace).

    Notes:
      - Atomic replace is POSIX-atomic; on Windows semantics may vary.
      - pandas/pyarrow are imported lazily, so importing this module is cheap.

    Args:
        df: DataFrame to write.
        path: Target file path.
        compression: One of {"zstd","snappy","gzip","brotli","none"}.
        overwrite: If False and file exists, raises FileExistsError.
        schema: Optional Arrow schema to enforce column types.
        file_mode: Optional chmod mode (e.g., 0o640).

    Returns:
        Absolute path to the written file.

    Raises:
        FileExistsError: if overwrite=False and file already exists.
        ValueError: for unsupported compression values.
        RuntimeError: if writing fails.
    """
    _ = _require_pandas()  # validate pandas presence
    pa, pq = _require_pyarrow()  # pyarrow + parquet

    p = Path(path)
    if p.exists() and not overwrite:
        raise FileExistsError(str(p))

    comp = compression.lower()
    allowed = {"zstd", "snappy", "gzip", "brotli", "none"}
    if comp not in allowed:
        raise ValueError(
            f"Unsupported compression codec: {compression!r}. Allowed: {sorted(allowed)}"
        )

    p.parent.mkdir(parents=True, exist_ok=True)

    try:
        table = dataframe_to_arrow(df, schema=schema)

        with tempfile.NamedTemporaryFile(dir=p.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            pq.write_table(table, tmp_path, compression=None if comp == "none" else comp)
            if file_mode is not None:
                with contextlib.suppress(Exception):
                    tmp_path.chmod(file_mode)
            tmp_path.replace(p)
        except Exception:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)
            raise

        if df.empty:
            _LOG.debug("Wrote empty parquet file: %s", str(p.resolve()))
        else:
            _LOG.debug(
                "Wrote parquet file (%d rows, %d cols): %s", len(df), df.shape[1], str(p.resolve())
            )

        return str(p.resolve())

    except FileExistsError:
        raise
    except Exception as e:
        _LOG.error("Failed to write parquet file: %s", str(e))
        raise RuntimeError(f"Failed to write parquet file: {e}") from e
