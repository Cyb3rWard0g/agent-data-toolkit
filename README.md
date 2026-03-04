# Agent Data Toolkit

An open source toolkit that provides modular connectors for AI agents to interact with diverse data systems. It standardizes connection and query patterns, handles configuration and errors, and delivers results in a consistent format. Designed to be reused by other projects, it enables frameworks like MCP servers or custom agent implementations to define tools on top of a common abstraction layer for seamless data access.

---

## Current Data Connectors

| Connector    | Status     | Extras name    | Notes                                      |
|--------------|-----------|----------------|--------------------------------------------|
| PostgreSQL   | ✅ Ready   | `postgresql`   | Supports queries, schema introspection, DataFrame helpers |
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