# Quickstart: PostgreSQL

Spin up a local PostgreSQL instance and try **agent-data-toolkit** from a Jupyter notebook.

This quickstart is self-contained: Docker brings up Postgres (seeded with a tiny schema and sample data), and a notebook exercises the full toolkit API — queries, schema introspection, pipeline mode, COPY bulk loading, streaming, and Parquet export.

## 1) Start PostgreSQL

1) Copy the environment file and adjust if desired:

```bash
cp .env.example .env
```

2) Bring up the database:

```bash
docker compose up -d
```

The service is named `mcp-postgres`, listens on `127.0.0.1:5432`, and is auto-seeded by the SQL files in `docker-entrypoint-initdb.d/` on first start.

## 2) Python environment

### Using uv (recommended)

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### Using pip

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3) Run the Notebooks

### 01_getting_started

* Open `notebooks/01_getting_started.ipynb` and run cells top-to-bottom.
* It will:
    * Connect via `PostgresClient` and run a health check
    * Run scalar, row, and DataFrame queries
    * Inspect schema (tables, columns, constraints, indexes)
    * Batch multiple queries in a single roundtrip (pipeline mode)
    * Stream large results with server-side cursors
    * Bulk-load data with PostgreSQL COPY protocol
    * Export results to Parquet