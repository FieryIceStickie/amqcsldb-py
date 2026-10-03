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

### Object model

`objects/_db_types.py` defines the attrs-based Python objects and their
conversion methods. This file may be edited.

`objects/_json_types.py` and `objects/_obj_consts.py` define the external
database schemas and constants. Do not modify these files; treat the
database contract as fixed.

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

### Maintainer's coding preferences

- Prefer `idx` over `i` for loop indices.
- Use `[*items]` instead of `list(items)` and `{*items}` instead of
  `set(items)`. Use `set()` for an empty set. Named constructors are fine
  where a callable is needed, such as an attrs field factory.
- Prefer `(*inferred,) = dict.fromkeys(...)` over
  `tuple(dict.fromkeys(...))` when deduplicating values.
- Add trailing commas to longer function signatures so Ruff wraps them
  onto multiple lines.
- Prefer `match`/`case` over chains of `isinstance` checks where practical,
  especially in client stream processing. Keep the match statements used
  to convert JSON into objects.
- Add useful docstrings to private methods too; an underscore prefix does
  not mean a method should be undocumented.
- Keep intentional formatting comments. If Ruff rejects an empty comment,
  use `# :)` rather than deleting it.
- Share sync/async workflow logic through bundles and keep compatibility
  wrappers small. Keep bundles out of ordinary user-facing interfaces.
- Keep async network requests outside locks used to protect shared writes;
  recheck cached state before updating it.
- Prefer static type checking over runtime type validators. Keep checks
  for meaningful value constraints.
- Keep logging useful and sparse, matching the existing workflow messages.

### Documentation preferences

- Match the maintainer's existing informal, direct writing style.
- User documentation should explain how to use the library without
  introducing bundle internals.
- Keep `docs/source/reference/` as API reference directives; put explanatory
  prose in the guides.
- Write concise changelog entries for readers who know the codebase.
  Describe the changes without explaining the implementation, and link
  relevant APIs or documentation wherever possible.
- Add changes to the appropriate release section of the changelog.

**Tests:** pytest + pytest-asyncio + respx (mocks HTTP calls — don't make
real network requests in tests). Fixtures/resources live in
`tests/resources/`.

## Definition of done

1. `uv run pytest -vv` passes.
2. `uv run ruff check` and `uv run ruff format --check` are clean.
3. New or changed behavior has a corresponding test.
4. `objects/_json_types.py` and `objects/_obj_consts.py` are untouched.
5. No new dependencies were added without asking first.
6. Changes are on a branch, not committed directly to `main`, and ready for
   the maintainer to review in the current session.

## Git and PR conventions

- Always work on a branch, never commit directly to `main`.
- PRs are optional. The maintainer can review changes in the current session;
  only open a PR when explicitly asked.
- Check `git log` and follow the existing commit conventions: a concise
  conventional title (such as `feat: ...` or `fix: ...`) and a meaningful,
  short description of the change using bullet points.
- Do not include validation summaries or a `Validation` section in commit messages.
- Include `Implemented with Codex.` in commit descriptions for changes
  made by Codex.
- Split unrelated changes into reasonable commits when useful; a single
  coherent change can use one commit.

## Boundaries

**Always**
- Read the existing client/workflow/bundle code before adding to it, and
  match its existing patterns (underscore-private + public re-export, attrs
  for schemas, sync and async client parity).
- Work on a branch and make changes ready for review in the current session.

**Ask first**
- Adding any new dependency.
- Any change that would affect the public API surface exported from a
  non-underscore module.

**Never**
- Modify `objects/_json_types.py` or `objects/_obj_consts.py` without permission — the database
  contract is not under our control.
- Commit directly to `main`.
- Add new dependencies without asking.
- Edit generated/build output: `docs/build/`, `__pycache__/`, `.pyc` files.
- Make real network requests in tests — use respx mocking, following the
  existing fixtures in `tests/resources/`.
