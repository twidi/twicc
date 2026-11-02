# Contributing to TwiCC

Thanks for your interest in TwiCC. This guide covers how to set up a local development environment, run the project from source, and build releases. For deeper conventions and patterns, see [`AGENTS.md`](AGENTS.md) and [`CLAUDE.md`](CLAUDE.md).

> **Heads-up:** TwiCC is a personal project. Issues and pull requests are welcome, but there is no commitment to address every one of them. Drive-by fixes and small focused PRs have the best odds.

## Tech stack

- **Backend:** Django 6, Uvicorn, Django Channels (WebSocket), Python 3.13+
- **Frontend:** Vue 3 (SFC, Composition API), Vite 7, Pinia, VueUse
- **UI:** Web Awesome (`wa-*` components)
- **Storage:** SQLite (WAL mode), Tantivy full-text index
- **Process management:** Claude Agent SDK and Codex SDK/CLI
- **Package managers:** `uv` (Python), `npm` (frontend)

## Setup

```bash
git clone https://github.com/twidi/twicc.git
cd twicc
cd frontend && npm install && cd ..
```

Optionally, install dev tools (`django-extensions`, `ipython`):

```bash
uv sync --group dev
```

## Running the dev servers

Two options:

### `devctl.py` (recommended)

Runs the Vite dev server with hot-reload alongside the Uvicorn backend. No frontend build step required.

```bash
./devctl.py start         # start both backend and frontend
./devctl.py stop          # stop both
./devctl.py restart       # restart both
./devctl.py status        # show running state and ports
./devctl.py logs back     # tail backend log
./devctl.py logs front    # tail frontend log
```

Run `./devctl.py --help` for all subcommands and options.

`devctl.py` also handles git worktrees: each worktree gets its own ports, database, logs, and `.env`. See `CLAUDE.md` for the worktree section.

### `run.py` (no hot-reload)

Runs the backend only. The frontend must be built first.

```bash
cd frontend && npm run build && cd ..
uv run run.py
```

## Codex SDK — vendored

The Codex CLI binary is a regular PyPI dependency (`openai-codex-cli-bin`, manylinux/macOS/Windows wheels). The SDK source (`openai_codex`) is vendored from the `openai/codex` repository.

See [`docs/codex-vendoring.md`](docs/codex-vendoring.md) for the layout, update procedure, and exit condition.

## Building and publishing

```bash
./scripts/build-release.sh     # builds sdist + one py3-none-any wheel → dist/
uv publish dist/*              # publishes both the sdist and the wheel to PyPI
```

The release script runs `npm ci` + `npm run build` in `frontend/`, then produces a single platform-agnostic `py3-none-any` wheel plus the matching sdist. The Codex CLI binary comes from `openai-codex-cli-bin` on PyPI (manylinux/macOS/Windows wheels), so TwiCC itself does not need to ship per-platform wheels. The sdist embeds the pre-built frontend assets, so `pip install` from source needs neither npm nor an extra fetch.

## Release process

See the "Release Process" section in [`CLAUDE.md`](CLAUDE.md) for the full ordered checklist (version bumps in `pyproject.toml` and `uv.lock`, `CHANGELOG.md` update, build, user-side wheel test, commit, annotated tag, GitHub Release, PyPI publish).

## Code conventions

The full set of conventions, patterns, and gotchas (avoiding circular imports, JSONL sync model, Web Awesome usage, dialog forms, data directory and worktrees) lives in:

- [`AGENTS.md`](AGENTS.md) — short, language-agnostic agent guide
- [`CLAUDE.md`](CLAUDE.md) — extended guide with architecture notes, frontend/backend patterns, and operational rules

Quick reminders:

- Code, comments, identifiers, and source UI messages are in **English**. Translations live in the locale catalogs; see [Interface languages](docs/internationalization.md) for the workflow. Documentation files (`*.md`) may be in French.
- The project aims for high quality. The only accepted shortcuts are no mandatory tests and no mandatory linting — but if you can add tests for a tricky bit, please do.
- Use `orjson` instead of the stdlib `json` module in the backend.
- Prefer `NamedTuple` over `@dataclass` for immutable containers.
- Every Web Awesome component used in the frontend must be explicitly imported in `frontend/src/main.js`.
