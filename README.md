# Agent Data Toolkit

An open source toolkit that provides modular connectors for AI agents to interact with diverse data systems. It standardizes connection and query patterns, handles configuration and errors, and delivers results in a consistent format. Designed to be reused by other projects, it enables frameworks like MCP servers or custom agent implementations to define tools on top of a common abstraction layer for seamless data access.

---

## Current Data Connectors

| Connector    | Status     | Extras name    | Notes                                      |
|--------------|-----------|----------------|--------------------------------------------|
| PostgreSQL   | ✅ Ready   | `postgresql`   | Supports queries, schema introspection, DataFrame helpers |
| Azure Monitor Logs / Sentinel | Ready | `azure-monitor` | Workspace KQL queries, retained source response, explicit partial-result handling, DataFrames |
| Elasticsearch| 🚧 Planned| `elasticsearch`| Query and aggregation support (future)     |
| MongoDB      | 🚧 Planned| `mongodb`      | Basic CRUD and aggregation pipelines       |
| Neo4j        | 🚧 Planned| `neo4j`        | Cypher query support (future)              |
| Splunk       | 🚧 Planned| `splunk`       | Search/query API support (future)          |


---

## Installation

### From PyPI

The easiest way is to install the package directly from PyPI:

```bash
# With uv (recommended)
uv pip install agent-data-toolkit

# Or with pip
pip install agent-data-toolkit
```

To enable a specific connector, install with extras. For example, PostgreSQL:

```bash
uv pip install "agent-data-toolkit[postgresql]"
# or
pip install "agent-data-toolkit[postgresql]"
```

### From Source

If you want to work with the latest code from GitHub:

```bash
git clone https://github.com/Cyb3rWard0g/agent-data-toolkit.git
cd agent-data-toolkit

# Create a virtual environment
uv venv .venv
source .venv/bin/activate

# Install in editable/dev mode
uv pip install -e ".[dev]"
# or
pip install -e ".[dev]"
```

You can still add extras here, e.g.:

```bash
uv pip install -e ".[dev,postgresql]"
```

---

## Quick Example

Using the `PostgreSQL` connector:

```python
from agent_data_toolkit.postgresql import PostgresClient

# Create client from environment variables (PG_DSN / POSTGRES_DSN / DATABASE_URL)
pg = PostgresClient.from_env()

rows = pg.query_rows("SELECT 1 AS ok")
print(rows)

pg.close()
```

### Azure Monitor Logs and Microsoft Sentinel

Install `agent-data-toolkit[azure-monitor]` in the environment that executes the
queries. For Jupyter, that is the **kernel environment**, which may be remote.
The connector uses Azure Monitor's Logs query API for the workspace's tables;
it does not manage Sentinel incidents, rules or workspace permissions.

For local development, authenticate with Azure CLI and configure the kernel:

```sh
az login
export AZURE_LOGS_WORKSPACE_ID="<workspace-guid>"
export AZURE_LOGS_AUTH=azure-cli
```

For an Azure-hosted workload, set `AZURE_LOGS_AUTH=managed-identity` and optionally
`AZURE_CLIENT_ID` for its explicitly selected user-assigned identity. There is no
automatic developer-credential fallback. The runtime identity needs query access
to the intended workspace/tables. An injected synchronous Azure `TokenCredential`
is also supported through `from_env(credential=...)` or `from_info(info, credential)`.

```python
from datetime import UTC, datetime
from agent_data_toolkit.azure_monitor import AzureMonitorLogsClient

logs = AzureMonitorLogsClient.from_env()
result = logs.query(
    "SecurityEvent | take 100",
    timespan=(
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
    ),
    max_rows=1000,
)
print(result.metadata())
events = result.to_dataframe()
# Keep logs and events in the notebook kernel for follow-up cells.
# Close the reusable client when finished:
logs.close()
```

`query()` retains every result table's names/types, the query/window, source
partial status, request ID and exact response bytes (`raw_response`) when received
through the SDK pipeline. `metadata()` exposes their hashes without rows or tokens.
An empty successful table retains its columns. `to_dataframe()` and `query_df()`
reject partial or client-truncated data by default; explicitly use
`allow_partial=True` after inspecting the result. A `PartialResultError` retains
its structured `result` for inspection without repeating the query.

`max_rows` caps retained rows across all tables **after the response arrives**.
It does not cap server scanning, network bytes or the retained native response.
Service limits, query design and `server_timeout` (1–600 seconds) remain separate
limits. `complete` describes successful API collection without client truncation;
it does not establish ingestion health or investigation coverage.

The SDK caches/renews tokens while the client is reused. No raw token is accepted
or written into notebook cells. Caller-injected credentials remain caller-owned;
`close()` releases SDK resources and credentials created by `from_env()`.
Cloud overrides use trusted `AZURE_LOGS_ENDPOINT` and `AZURE_LOGS_AUDIENCE` settings.
Any Python running in a kernel with an Azure identity can use that identity:
notebook tool arguments alone are not an authorization boundary.

Offline connector tests, including a controlled Azure SDK transport, need no
source identity. Live qualification is explicit:

```sh
ADT_AZURE_LIVE=1 pytest -q -m azure_live
```

This live probe verifies authentication and workspace query permission; it does
not qualify a particular security table, mapping or MATCH investigation outcome.

---

## Release Process

To publish a new release to PyPI:

0. Install dev dependencies
    ```sh
    uv pip install -e ".[dev]"
    ``` 
1. Ensure all changes are committed and tests pass:
    ```sh
    uv run pytest tests/
    ```
2. Create and push an **annotated tag** for the release:
    ```sh
    git tag -a v0.1.0 -m "Release 0.1.0"
    git push origin v0.1.0
    ```
3. Checkout the tag to ensure you are building exactly from it:
    ```sh
    git checkout v0.1.0
    ```
4. Clean old build artifacts:
    ```sh
    rm -rf dist
    rm -rf build
    rm -rf src/*.egg-info
    ```
5. Upgrade build and upload tools:
    ```sh
    uv pip install --upgrade build twine packaging setuptools wheel setuptools_scm
    ```
6. Build the package:
    ```sh
    uv run python -m build
    ```
7. (Optional) Check metadata:
    ```sh
    uv run twine check dist/*
    ```
8. Upload to PyPI:
    ```sh
    uv run twine upload dist/*
    ```

**Notes:**
* Twine ≥ 6 and packaging ≥ 24.2 are required for modern metadata support.
* Always build from the tag (`git checkout vX.Y.Z`) so setuptools_scm resolves the exact version.
* `git checkout v0.1.0` puts you in detached HEAD mode; that’s normal. When done, return to your branch with:
    ```sh
    git switch -
    ```
* If you’re building in CI, make sure tags are fetched:
    ```sh
    git fetch --tags --force --prune
    git fetch --unshallow || true
    ```

---
