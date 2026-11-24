# AI Knowledge Copilot

Backend service built with **FastAPI**, **PostgreSQL** and **Docker**. Dependencies are managed with **uv**.

This README explains how the project is organised and how to work with it day to day. If you are new to the project, read it top to bottom once.

## Table of contents

1. [Tech stack](#tech-stack)
2. [Quick start (Docker)](#quick-start-docker)
3. [Local development (without Docker for the app)](#local-development)
4. [Project structure](#project-structure)
5. [How a request flows through the code](#how-a-request-flows-through-the-code)
6. [Configuration](#configuration)
7. [Database and migrations](#database-and-migrations)
8. [Code quality: ruff, mypy, pre-commit](#code-quality)
9. [Tests](#tests)
10. [AI/RAG service](#airag-service)
11. [CI (GitHub Actions)](#ci-github-actions)
11. [Docker setup explained](#docker-setup-explained)
13. [How to add a new feature](#how-to-add-a-new-feature)
14. [Troubleshooting](#troubleshooting)

---

## Tech stack

| Tool | What it is used for |
|------|---------------------|
| Python 3.13 | Language |
| FastAPI | Web framework (HTTP API, auto-generated docs) |
| PostgreSQL 18 | Database |
| SQLAlchemy 2 (async) + asyncpg | Talking to the database from Python |
| Alembic | Database migrations (versioned schema changes) |
| Pydantic / pydantic-settings | Data validation and configuration |
| uv | Package and virtualenv manager (replaces pip/poetry) |
| ruff | Linter and code formatter |
| mypy | Static type checker (strict mode) |
| pre-commit | Runs checks automatically before each commit |
| pytest | Tests |
| Docker / Docker Compose | Running the app and database the same way everywhere |

---

## Quick start (Docker)

You need: [Docker](https://docs.docker.com/engine/install/) with the Compose plugin.

```bash
# 1. Create your local settings file and CHANGE the password inside
cp .env.example .env

# 2. Build and start everything (database + migrations + app)
docker compose up -d --build

# 3. Check that it works
curl http://localhost:8000/api/v1/health
```

Then open:

- Swagger UI (interactive docs): <http://localhost:8000/docs>
- OpenAPI schema: <http://localhost:8000/api/v1/openapi.json>

Useful commands:

```bash
docker compose logs -f app     # follow app logs
docker compose ps              # status of containers
docker compose down            # stop everything (data is kept)
docker compose down -v         # stop AND delete the database data
```

---

## Local development

Use this when you want to run the app on your machine (e.g. from PyCharm) with hot reload, while the database runs in Docker.

Requirements: [uv](https://docs.astral.sh/uv/getting-started/installation/) and Docker.

```bash
# 1. Install dependencies into .venv (uv also installs the right Python)
uv sync

# 2. Create the settings file (POSTGRES_HOST=localhost is already set in the example)
cp .env.example .env

# 3. Start only the database
docker compose up -d db

# 4. Apply migrations
uv run alembic upgrade head

# 5. Run the app with auto-reload
uv run uvicorn app.main:app --reload
```

The app is now at <http://localhost:8000>.

**PyCharm:** set the project interpreter to `.venv/bin/python` (Settings → Python Interpreter → Add → Existing).

**Install the git hooks once** (see [Code quality](#code-quality)):

```bash
uv run pre-commit install
```

> Never use `pip install`. Add packages with `uv add <package>` (or `uv add --dev <package>` for dev tools). This updates `pyproject.toml` and `uv.lock`; commit both.

---

## Project structure

```
.
├── app/                        # All application code
│   ├── main.py                 # Creates the FastAPI app (entry point)
│   ├── core/
│   │   └── config.py           # Settings read from environment variables
│   ├── db/
│   │   ├── base.py             # SQLAlchemy Base class, naming rules, TimestampMixin
│   │   └── session.py          # Database engine and session factory
│   ├── models/                 # SQLAlchemy models = database tables
│   │   └── user.py
│   ├── schemas/                # Pydantic models = request/response shapes
│   │   ├── user.py
│   │   └── health.py
│   ├── services/               # Business logic and database queries
│   │   └── user.py
│   └── api/
│       ├── deps.py             # Shared FastAPI dependencies (e.g. DB session)
│       └── v1/
│           ├── router.py       # Collects all v1 endpoints
│           └── endpoints/      # HTTP handlers (thin layer)
│               ├── health.py
│               └── users.py
├── alembic/                    # Migrations
│   ├── env.py                  # Connects Alembic to our settings and models
│   └── versions/               # One file per migration
├── tests/                      # pytest tests
├── Dockerfile                  # How to build the app image
├── docker-compose.yml          # How to run app + database together
├── pyproject.toml              # Dependencies and tool settings (ruff, mypy, pytest)
├── uv.lock                     # Exact dependency versions (always commit it)
├── .env.example                # Template for your local .env
├── .pre-commit-config.yaml     # Checks that run before each commit
└── .github/workflows/ci.yml    # Checks that run on every Pull Request
```

### Models vs schemas — an important difference

| | `models/` (SQLAlchemy) | `schemas/` (Pydantic) |
|---|---|---|
| Describes | A **database table** | The **JSON** the API accepts/returns |
| Example | `User` with `id`, `email`, ... | `UserCreate` (input), `UserRead` (output) |

They are kept separate on purpose: you never want to expose every database column (e.g. a password hash) just because it exists in the table.

---

## How a request flows through the code

Example: `POST /api/v1/users` with `{"email": "a@example.com"}`.

```
HTTP request
   │
   ▼
app/api/v1/endpoints/users.py     ← 1. endpoint: validates JSON into UserCreate (schema),
   │                                   gets a DB session via dependency injection
   ▼
app/services/user.py              ← 2. service: business logic + database queries
   │
   ▼
app/models/user.py + PostgreSQL   ← 3. model: row is inserted into the "users" table
   │
   ▼
app/schemas/user.py (UserRead)    ← 4. response is converted to JSON using the schema
```

Rules of thumb:

- **Endpoints stay thin.** They translate HTTP ⇄ Python (status codes, errors). No SQL in endpoints.
- **Services contain the logic.** They know nothing about HTTP; they raise Python exceptions (e.g. `UserAlreadyExistsError`), and the endpoint turns them into HTTP errors (`409 Conflict`).
- **Dependencies** (`app/api/deps.py`): `SessionDep` gives each request its own database session, closed automatically afterwards.

### Available endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness: the app is running |
| GET | `/api/v1/health/db` | Readiness: the app can reach the database (503 if not) |
| POST | `/api/v1/users` | Create a user (409 if the email exists) |
| GET | `/api/v1/users` | List users (`offset`, `limit` query params) |
| GET | `/api/v1/users/{id}` | Get one user (404 if not found) |

---

## Configuration

All settings come from **environment variables**, defined in [`app/core/config.py`](app/core/config.py). Locally they are loaded from the `.env` file.

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | `local`, `staging` or `production` (Swagger UI is disabled in `production`) | `local` |
| `DEBUG` | Enables SQL logging | `false` |
| `BACKEND_CORS_ORIGINS` | JSON list of allowed browser origins | `[]` |
| `POSTGRES_HOST` | Database host | `localhost` |
| `POSTGRES_PORT` | Database port | `5432` |
| `POSTGRES_USER` | Database user | **required** |
| `POSTGRES_PASSWORD` | Database password | **required** |
| `POSTGRES_DB` | Database name | **required** |

Important:

- `.env` is in `.gitignore`. **Never commit secrets.** Only `.env.example` (with fake values) is committed.
- Inside Docker Compose, `POSTGRES_HOST` is automatically set to `db` (the name of the database container), so you do not need to change it.
- If a required variable is missing, the app (and `docker compose`) refuses to start. This is intentional.

---

## Database and migrations

The database schema is **not** created automatically from models. Every schema change goes through an **Alembic migration**: a versioned file that says how to upgrade (and downgrade) the schema. This keeps all environments in sync.

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Roll back the last migration
uv run alembic downgrade -1

# Show current version / history
uv run alembic current
uv run alembic history
```

### Changing the schema (step by step)

1. Edit or add a model in `app/models/`.
2. If it is a new model, import it in `app/models/__init__.py` (so Alembic sees it).
3. Generate a migration:
   ```bash
   uv run alembic revision --autogenerate -m "add documents table"
   ```
4. **Open the generated file in `alembic/versions/` and review it.** Autogenerate is good but not perfect.
5. Apply it: `uv run alembic upgrade head`.
6. Commit the model change **and** the migration file together.

With Docker Compose, migrations run automatically: the `migrate` container applies them before the app starts.

---

## Code quality

Three tools keep the code consistent. All are configured in [`pyproject.toml`](pyproject.toml).

```bash
uv run ruff format .      # auto-format code
uv run ruff check . --fix # lint and auto-fix what is possible
uv run mypy               # type-check (strict)
```

- **ruff** — formatter + linter (style, bugs, security hints, import order).
- **mypy (strict)** — checks type hints. In this project **every function must have type annotations**.

### pre-commit

After `uv run pre-commit install`, these checks run automatically on `git commit`. If a hook fails or modifies files, the commit is aborted: review the changes, `git add` them again and commit again.

Run all hooks manually on the whole project:

```bash
uv run pre-commit run --all-files
```

> Do not skip hooks with `--no-verify`. CI runs the same checks and will fail your PR anyway.

---

## Tests

```bash
docker compose up -d db   # integration tests need PostgreSQL
uv run pytest             # everything, with coverage (threshold: 80%)
```

Layout:

| Path | What | Needs |
|------|------|-------|
| `tests/unit/` | pure logic, routes with stubbed dependencies | nothing (a route asking for a DB session fails loudly) |
| `tests/integration/` | real HTTP stack + real PostgreSQL | running PostgreSQL |
| `tests/fakes/` | in-memory `FakeStorage` (the AI service uses the deterministic `StubAIService`) | - |

- Markers `unit` / `integration` are applied automatically by folder: `pytest -m unit --no-cov`, `pytest tests/integration`.
- Integration tests create a throw-away database `<POSTGRES_DB>_test`, build the schema with the real Alembic migrations and drop it afterwards. Each test runs in a transaction that is rolled back, so tests never see each other's data. The dev database is never touched.
- Safety: the test DB is only created on local hosts (`localhost`, `127.0.0.1`, `db`, `postgres`) and never with `ENVIRONMENT=production`. For a remote server you must opt in with `ALLOW_REMOTE_TEST_DB=1`.
- Storage and the AI/RAG service are replaced in every test automatically (`tests/conftest.py`): the `fake_storage` and `fake_ai` fixtures give each test a fresh `FakeStorage` and `StubAIService`, so no test can reach real files or an LLM. To test error paths set `fake_ai.failure = LLMTimeoutError()`.

### Coverage

`uv run pytest` always measures coverage of `app/`, fails below **80%** and prints a link to the HTML report (`htmlcov/index.html`) at the very end of the output. The link is shown only when the report was regenerated by that run.

### Running a single test / debugging: use `--no-cov`

Coverage is enabled in `addopts`, so a partial run (one file, `-k ...`) covers only a small part of the code and fails the 80% threshold. Add `--no-cov` for such runs:

```bash
uv run pytest tests/unit/test_security.py --no-cov
uv run pytest -k login --no-cov
```

**PyCharm:** `Run | Edit Configurations... | Edit configuration templates... | Python tests | pytest` -> *Additional arguments*: `--no-cov`. Existing run configurations created before that need the same argument (or delete and recreate them). PyCharm's *Run with Coverage* conflicts with `pytest-cov`, so keep `--no-cov` there too and use `uv run pytest` in a terminal for the coverage report. Debugging with coverage enabled also breaks breakpoints, another reason for `--no-cov`.

### Troubleshooting

- `PostgreSQL is not reachable ...` -> `docker compose up -d db` and check `POSTGRES_*` in `.env`. Without a database run `uv run pytest tests/unit --no-cov`.
- `Refusing to create/drop a test database on remote host` -> your `.env` points to a non-local server; see Safety above.

---

## AI/RAG service

Package [`app/services/ai/`](app/services/ai/). Routes depend on the `AIService` protocol through `AIServiceDep` (`app/api/deps.py`) and never on a concrete implementation.

| Module | Content |
|--------|---------|
| `interface.py` | `AIService` protocol: `ingest_document(document_id, workspace_id, file_path, filename) -> IngestionResult` and `ask_workspace(workspace_id, query, chat_history) -> WorkspaceAnswer` |
| `schemas.py` | Pydantic models: `ChatMessage`, `SourceCitation` (`document_id`, `filename`, `chunk_index`, `page_number`, `snippet`, `score`), `IngestionResult`, `WorkspaceAnswer` |
| `exceptions.py` | `DocumentParsingError` (422), `EmbeddingServiceError` (502), `LLMGenerationError` (502), `LLMTimeoutError` (504); all inherit from `AIServiceError` |
| `stub.py` | `StubAIService`: deterministic, in-memory, no network. This is the implementation that runs now |

The errors are handled by the global exception handler, so routes need no `try/except`:

```python
@router.post("/workspaces/{workspace_id}/chat", response_model=WorkspaceAnswer)
async def chat(workspace_id: UUID, body: ChatRequest, ai: AIServiceDep) -> WorkspaceAnswer:
    return await ai.ask_workspace(
        workspace_id=workspace_id, query=body.query, chat_history=body.chat_history
    )
```

How the stub behaves:

- `ingest_document` accepts `.txt`, `.md` (UTF-8) and `.pdf`. It reads the file at `file_path` and splits it into word-boundary chunks of about 800 characters. A PDF is only checked for the `%PDF-` header and indexed as one placeholder chunk on page 1. A missing, empty, undecodable or unsupported file raises `DocumentParsingError`. Re-ingesting the same `document_id` replaces its chunks.
- `ask_workspace` ranks the chunks of that workspace only, by word overlap with the query, and returns up to 3 `SourceCitation`s plus a deterministic summary answer. The same input always gives the same output.
- The index lives in process memory: it is lost on restart and is not shared between workers.

When the real implementation is ready, make `get_ai_service()` in `app/api/deps.py` return it. Routes and tests stay unchanged.

---

## CI (GitHub Actions)

File: [`.github/workflows/ci.yml`](.github/workflows/ci.yml). It runs on **every Pull Request** (and on pushes to `main` and `develop`):

1. Install `uv` (with cache) and the locked dependencies
2. `ruff format --check`
3. `ruff check`
4. `mypy`
5. `pytest` against a PostgreSQL service container (with the 80% coverage gate)
6. Coverage summary table and a link to the downloadable HTML report (`coverage-html` artifact) on the run's *Summary* page; this runs even when tests or the coverage gate fail

A PR can only be merged when all steps are green. To avoid surprises, run the same commands locally (or just rely on pre-commit).

> If CI fails with a lock-file error, you changed `pyproject.toml` but did not update `uv.lock`. Run `uv lock` and commit it.

---

## Docker setup explained

### `Dockerfile` (multi-stage)

1. **builder** stage: installs `uv` and all production dependencies. Dependencies are installed in a separate layer first, so Docker re-uses the cache when only your code changes (fast rebuilds).
2. **runtime** stage: a clean, small Python image that only receives the ready `.venv` and the code. It has no `uv` or build tools and runs as a **non-root user** (safer).

### `docker-compose.yml` (three services)

```
db  ──(healthy)──▶  migrate  ──(finished OK)──▶  app
```

| Service | Role |
|---------|------|
| `db` | PostgreSQL. Has a healthcheck (`pg_isready`), data is stored in the `pgdata` volume |
| `migrate` | Runs `alembic upgrade head` once, then exits |
| `app` | The FastAPI server. Starts only after the DB is healthy **and** migrations succeeded |

So the app never starts against an unready or outdated database.

The database port is published only on `127.0.0.1`, so it is reachable from your machine but not from the network.


---

## How to add a new feature

Example: a `Document` resource. Follow the same order as the request flow:

1. **Model** — `app/models/document.py`; import it in `app/models/__init__.py`.
2. **Migration** — `uv run alembic revision --autogenerate -m "add documents"`, review, `upgrade head`.
3. **Schemas** — `app/schemas/document.py` (`DocumentCreate`, `DocumentRead`).
4. **Service** — `app/services/document.py` (queries and logic).
5. **Endpoints** — `app/api/v1/endpoints/documents.py` with an `APIRouter`.
6. **Register the router** — add `api_router.include_router(documents.router)` in `app/api/v1/router.py`.
7. **Tests** — add tests in `tests/`.
8. Run `uv run pre-commit run --all-files` and `uv run pytest`, then open a PR.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `docker compose` says `POSTGRES_USER is not set` | You forgot `cp .env.example .env` |
| `Connection refused` when running locally | The DB is not running: `docker compose up -d db`. Check `POSTGRES_HOST=localhost` in `.env` |
| `port is already allocated` (5432 or 8000) | Another service uses it. Stop it or change `POSTGRES_PORT` in `.env` |
| `relation "users" does not exist` | Migrations were not applied: `uv run alembic upgrade head` |
| Changed `.env` password but DB still uses the old one | The volume keeps the old data: `docker compose down -v` (**deletes DB data**) and start again |
| mypy errors after adding a library | Add the library's type stubs (`uv add --dev types-<name>`) or check its docs |
| Commit rejected by pre-commit | Read the output, fix the issue (or `git add` the auto-fixed files) and commit again |
| `uv: command not found` | Install uv: <https://docs.astral.sh/uv/getting-started/installation/> |
