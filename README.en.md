<div align="center">

# CellFlow

**Configure a parsing plan for irregular Excel files once. After that, an API parses, validates and writes them into MySQL automatically — with one-click rollback.**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![ci](https://github.com/wuzhendong123/CellFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/wuzhendong123/CellFlow/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![React](https://img.shields.io/badge/react-18-61dafb)
![MySQL](https://img.shields.io/badge/mysql-8.0-4479a1)

[简体中文](README.md) · **English**

[Quick start](#quick-start) · [How it works](#how-it-works) · [Open API](#open-api) · [Docs](#documentation) · [Contributing](#contributing)

</div>

---

![Canvas](docs/images/canvas.jpg)

<details>
<summary>Key-value blocks, cross matrices, detail tables and total rows in one sheet can each be selected separately</summary>

![Region selection](docs/images/regions.jpg)

</details>

> The console UI is currently in Chinese. The documentation in [PRD.md](PRD.md), [TECH_DESIGN.md](TECH_DESIGN.md) and [WIREFRAME.md](WIREFRAME.md) is also in Chinese.

## Why CellFlow

Planners, operators and finance teams keep business data in Excel, but those sheets are rarely clean 2-D tables: global switches at the top, a cross matrix in the middle, detail rows below, and a totals row at the bottom. Turning them into usable data usually means writing parsing code for every sheet, or cleaning them by hand. That leads to three familiar problems:

| Problem | What CellFlow does |
|---|---|
| Inserting a column or moving a block breaks the parser | Locates regions by **headers, marker text and relative position**, not hard-coded coordinates |
| Errors are only found after going live | Validates cell by cell before writing, and maps every error **back to the original cell**; a **safety guard** runs before any write |
| Hard to roll back | Every write leaves a release record; **roll back** (whole table) or **undo** (by partition) |

Onboarding a new type of Excel needs **no code**: configure it once in the console, then business services just submit files.

## Features

- **Visual canvas**: connect nodes with edges; datasets flow between nodes and can be previewed at any point.
- **Multi-shape region selection**: key-value blocks, cross matrices, detail tables and multi-sheet merging, selected in a split view next to the canvas.
- **Rich node set**: source, filter, derived column (CEL expressions), select/rename, union, lookup, join, validator, pivot, and sink.
- **Cleaning and validation**: type conversion, empty-value rules, enum mapping, required, range, regex, unique, foreign key, expressions, and detail-vs-total reconciliation.
- **Safe writes**: a write happens only when validation and the safety guard both pass (delete ratio, row-count swing, empty-table protection). It runs in a transaction; on failure the business table is untouched.
- **Two write strategies**:
  - **Whole-table replace**: shadow table plus atomic swap, for files that are always a full snapshot.
  - **Partition replace**: delete the rows of the partitions in this batch (for example one day or one batch number), then insert the batch; other partitions stay untouched. Suited to one file per day writing into the same table.
- **Rollback and undo**: roll back a whole-table release to any earlier version; undo a single partition write.
- **Versions and trial runs**: regression-compare against historical files before publishing; trial-run any file without touching business tables.
- **Open API**: signed authentication, idempotency, validate-only mode, job queries, issue lists, callbacks.

## How it works

```
        Console (configure once)                         Runtime (every submitted file)
 ┌──────────────────────────────────────────┐      ┌──────────────────────────────────────────┐
 │ 1 Select regions  Locator × Shape × Cols  │      │ Business service ─signed upload─▶ Open API │
 │ 2 Build canvas    clean / join / validate │      │                              │            │
 │ 3 Bind table      column mapping, strategy│      │                              ▼            │
 │ 4 Trial run, regression, publish version  │      │       Job queue (Redis / arq) ── Worker    │
 └──────────────────────────────────────────┘      │                              │            │
                                                    │ parse ▶ validate ▶ guard ▶ transactional   │
                                                    │                       write to MySQL       │
                                                    │                              │            │
                                                    │     release record · change log · callback │
                                                    └──────────────────────────────────────────┘
```

**Write flow**

| Strategy | Flow |
|---|---|
| Whole-table replace | Create shadow table → write new data → verify → atomic swap; the old table is kept as the rollback source |
| Partition replace | In one transaction: lock and save the old rows of this batch's partitions (for undo) → a single `DELETE … WHERE partition_col = ?` → batched `INSERT` → verify the row count; any failure rolls everything back |

Only one job per plan runs at a time. For whole-table plans, several quick submissions execute only the latest file. For partition plans a file is one batch, so **every submission is executed**, in submission order.

## Quick start

Requires Docker Desktop (macOS / Linux) with `docker compose` v2.

```bash
git clone https://github.com/wuzhendong123/CellFlow.git && cd CellFlow
./cellflow.sh up                       # build and start MySQL, Redis, API + console, Worker (3–5 min the first time)
./cellflow.sh demo                     # optional: load a demo plan and generate demo Excel files in ./demo
./cellflow.sh submit demo/hero.xlsx    # submit a file with signed auth, like a business service, and wait for the result
./cellflow.sh open                     # open the console at http://localhost:8000
```

| Command | Description |
|---|---|
| `./cellflow.sh status` / `logs [api\|worker]` | Service status / logs |
| `./cellflow.sh mysql` | MySQL shell; inspect the business database `cellflow_biz` |
| `./cellflow.sh test` | Run backend tests in the container |
| `./cellflow.sh restart` | Rebuild and restart after updating the code |
| `./cellflow.sh down` / `reset` | Stop services / stop and wipe all data |

High-risk operations (publish, override, rollback, settings) need an operation token. The local default is `local-op-token`; change it with `CF_OP_TOKEN`. On port conflicts set `CF_PORT`, `CF_MYSQL_PORT` or `CF_REDIS_PORT`. All default secrets are local placeholders — **replace them in production**.

### Configuring a data source

In the console, **Data sources → New** offers two modes:

- **Direct**: host, port, user, password, database. The password is stored encrypted and never shown again. For the MySQL inside Docker use host `mysql`, port `3306`; for MySQL on the host machine use `host.docker.internal`.
- **Environment reference**: only a reference name is stored; the connection string comes from an environment variable (e.g. `CF_REF_BIZ_MYSQL`). Useful when you do not want passwords in the console.

## Open API

A business service only submits files and reads results. Requests are authenticated with an AppKey and a signature; see [TECH_DESIGN.md](TECH_DESIGN.md) for the signing rules.

| Endpoint | Description |
|---|---|
| `POST /open/v1/jobs` | Submit a file and create a job (`EXECUTE` writes, `VALIDATE_ONLY` only validates); supports idempotency keys and a callback URL |
| `GET /open/v1/jobs/{jobId}` | Job status and result |
| `GET /open/v1/jobs/{jobId}/issues` | Paged issue list with sheet, cell, field and original value |
| `GET /open/v1/pipelines/{code}` | Plan information |
| `GET /open/v1/pipelines/{code}/live` | Currently live version |

Job statuses: `PUBLISHED` written · `NO_CHANGE` identical to live · `VALIDATED` validate-only passed · `FAILED_VALIDATION` validation failed · `FAILED_GUARD` blocked by the safety guard · `FAILED_WRITE` write failed (business table untouched) · `SUPERSEDED` replaced by a newer job · `FAILED` system error.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2, openpyxl / pandas, [CEL](https://github.com/google/cel-spec) expressions (cel-python) |
| Queue | Redis, arq |
| Storage | MySQL 8.0 (metadata and business data); files and snapshots on a local directory or S3-compatible storage |
| Frontend | React 18, Ant Design, React Flow (canvas), Univer (spreadsheet view), Vite |
| Deployment | Docker Compose |

## Project layout

```
backend/cellflow/
  engine/      parsing engine: region location, shape detection, column cleaning, nodes and DAG execution
  runtime/     write runtime: whole-table / partition writers, executor, job queue
  services/    plans, releases, jobs, data sources, callbacks
  api/         console API and Open API
frontend/src/  console: canvas, region selection, table binding, jobs and release history
deploy/        MySQL init scripts
demo/          demo Excel files
```

## Development

```bash
make test      # backend tests
make lint      # ruff and frontend lint
make e2e       # end-to-end tests (Playwright)
make perf      # performance tests
```

The backend needs Python 3.11+, the frontend needs Node.js. CI runs backend lint and tests plus frontend lint, build and tests; see [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Documentation

The design documents are written in Chinese.

| Document | Content |
|---|---|
| [PRD.md](PRD.md) | Requirements, feature list and acceptance criteria |
| [TECH_DESIGN.md](TECH_DESIGN.md) | Technical design, data model, API contract, write and rollback design |
| [WIREFRAME.md](WIREFRAME.md) | Page wireframes and interaction notes |
| [TODO.md](TODO.md) · [PROGRESS.md](PROGRESS.md) | Task board and progress |

## Roadmap

Covered in v1: multi-shape regions, canvas, cleaning and validation, safety guard, whole-table and partition writes, rollback and undo, Open API.

Planned: xls / csv support, more transform nodes, incremental writes, alerts and notifications, SSO and role-based access, gray releases. See section 3 of [PRD.md](PRD.md) for the full scope.

## Contributing

Issues and pull requests are welcome.

1. Fork and branch from the main branch.
2. Add tests with your change; run `make lint` and `make test` before submitting.
3. For changes to the API contract or table schema, open an issue first to explain the motivation and impact, and update [TECH_DESIGN.md](TECH_DESIGN.md).
4. Never commit real secrets, passwords or connection strings; use obvious placeholders in examples.

## Security

If you find a vulnerability, please do not open a public issue. Read [SECURITY.md](SECURITY.md) and report it through GitHub's private vulnerability reporting.

## License

Released under the [Apache License 2.0](LICENSE).
