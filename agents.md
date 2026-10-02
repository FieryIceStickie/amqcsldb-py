# AGENTS.md

## Project overview

`amqcsldb-py` (AMQCSLdb-py) is a Python API wrapper for mshift's AMQ (Anime
Music Quiz) Custom Song List database, plus extra tooling for managing it.
It's used to automate database management tasks like adding character
metadata or editing track names. Niche userbase — mostly just the maintainer.

This is an existing codebase. The current focus is cleanup and adding new
functionality (not a rewrite).

- **Language:** Python 3.13.3 (repo declares `requires-python = ">=3.12"`)
- **Package manager:** uv
- **HTTP client:** httpx (sync and async clients both exist)
- **Schemas/data model:** attrs
- **CLI:** Typer + Rich (entry point: `amqcsl = "amqcsl.cli:app"`)
- **Build backend:** hatchling
- **Docs:** Sphinx (Furo theme), hosted on Read the Docs
- **Fully type hinted** — checked with pyright in strict mode

## Repository layout

```
src/amqcsl/
├── cli.py                CLI (Typer app)
├── clients/
│   ├── _sync_client.py   Private sync client implementation
│   ├── _async_client.py  Private async client implementation
│   ├── _client_consts.py Private shared client constants
│   ├── sync_client.py    Public re-export
│   ├── async_client.py   Public re-export
│   └── bundles/          Grouped endpoint/request bundles
│       ├── _core.py
│       ├── _misc.py
│       └── _pages.py
├── objects/               attrs-based DB/JSON object model — see "Object model" below
│   ├── _db_types.py
│   ├── _json_types.py
│   └── _obj_consts.py
├── workflows/              Higher-level multi-step operations built on the clients
│   ├── _workflow_utils.py
│   └── character.py
├── exceptions.py
├── _templates/             Script/log scaffolding used by the CLI's project setup
└── py.typed

tests/
├── conftest.py
├── helpers.py
├── resources/              JSON fixtures for mocked API responses
├── test_api.py
├── test_async_api.py
├── test_login.py
└── workflows/
    └── test_character.py

docs/source/                Sphinx docs source — handwritten, editable
docs/build/                 Generated Sphinx output — off-limits, do not edit
```

### Naming convention

Underscore-prefixed modules (`_sync_client.py`, `_db_types.py`, `_core.py`,
etc.) are **private implementation**. Public, non-underscore modules exist to
re-export from them, grouped by submodule for organizational purposes (e.g.
`clients/sync_client.py` re-exports from `clients/_sync_client.py`).

When adding new files, follow this same pattern: put the implementation in an
underscore-prefixed module and re-export the public surface from the
appropriately-grouped non-underscore module.

### Object model — do not modify

`objects/_db_types.py`, `objects/_json_types.py`, and `objects/_obj_consts.py`
define the attrs-based mapping between the database's data and Python
objects. **Do not change anything about the object model** — its shape is
dictated by the external database, not by this project, and isn't under our
control. Treat it as a fixed contract; build new functionality on top of it
rather than editing it.

## Setup and commands

| Task              | Command                                           |
| ------------------ | -------------------------------------------------- |
| Install deps        | `uv sync`                                         |
| Run CLI             | `uv run amqcsl`                                   |
| Run tests           | `uv run pytest -vv`                               |
| Lint / format        | `uv run ruff check --fix` / `uv run ruff format`  |
| Type check          | `uv run basedpyright`                                |
| Build docs (optional) | `uv run sphinx-build docs/source docs/build/html` |

**Type checking:** the project uses basedpyright in strict mode
(`tool.basedpyright` in `pyproject.toml`).

**Formatting details:** ruff, line length 120, single-quote strings.

**Tests:** pytest + pytest-asyncio + respx (mocks HTTP calls — don't make
real network requests in tests). Fixtures/resources live in
`tests/resources/`.

## Definition of done

1. `uv run pytest -vv` passes.
2. `uv run ruff check` and `uv run ruff format --check` are clean.
3. New or changed behavior has a corresponding test.
4. The object model (`objects/_db_types.py`, `_json_types.py`,
   `_obj_consts.py`) is untouched.
5. No new dependencies were added without asking first.
6. Changes are on a branch, not committed directly to `main`, with a PR
   opened.

## Git and PR conventions

- Always work on a branch, never commit directly to `main`.
- Open a PR when changes are ready, even though this is a solo project.

## Boundaries

**Always**
- Read the existing client/workflow/bundle code before adding to it, and
  match its existing patterns (underscore-private + public re-export, attrs
  for schemas, sync and async client parity).
- Work on a branch and open a PR for any change.

**Ask first**
- Adding any new dependency.
- Any change that would affect the public API surface exported from a
  non-underscore module.

**Never**
- Modify `objects/_db_types.py`, `objects/_json_types.py`, or
  `objects/_obj_consts.py` (the object model) — not under our control.
- Commit directly to `main`.
- Add new dependencies without asking.
- Edit generated/build output: `docs/build/`, `__pycache__/`, `.pyc` files.
- Make real network requests in tests — use respx mocking, following the
  existing fixtures in `tests/resources/`.
