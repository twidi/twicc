# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Code Web UI - A standalone, self-contained web application to replace the Claude Code CLI. Single process, zero external services, one command to launch.

**Status:** Proof-of-concept in architecture/planning phase. Implementation details are documented in `architecture-decisions.md`.

**Quality approach:** Although this is a proof-of-concept, we aim to implement everything to the best standards possible. The goal is to stress-test the system and learn as much as we can for the eventual production implementation. The only shortcuts we allow: no tests and no linting.

**IMPORTANT - Development workflow:** Never start implementing code without being explicitly invited to do so. When the user explains requirements or shares thoughts, wait for them to finish and confirm before writing any code. Ask clarifying questions if needed, but do not assume that an explanation is an invitation to implement.


## Stack

| Layer | Technology |
|-------|------------|
| Package Manager | uv |
| Backend | Django (ASGI) + Uvicorn |
| WebSocket | Django Channels + InMemoryChannelLayer |
| Database | SQLite |
| File Watching | watchfiles |
| Frontend | Vue.js 3 (SFC) + Vite |
| State Management | Vue reactive + VueUse |
| UI Components | Web Awesome (wa-* elements) |


## Development Process Controller (devctl.py)

Script to manage frontend and backend dev servers as background processes. Use this when the user asks to start, stop, or restart the dev servers.

```bash
uv run ./devctl.py start [front|back|all]   # Start process(es)
uv run ./devctl.py stop [front|back|all]    # Stop process(es)
uv run ./devctl.py restart [front|back|all] # Restart process(es)
uv run ./devctl.py status                   # Check running status
uv run ./devctl.py logs [front|back]        # Show recent logs (--lines=N)
```

**Expected ports:** Frontend on 5173, Backend on 3500. The script verifies correct port binding after start.

**Log files:** `.devctl/logs/frontend.log` and `.devctl/logs/backend.log` - read these to debug issues.

## Architecture

```
uv run ./run.py
├── Django ASGI (Uvicorn)
│   ├── HTTP (pages, API)
│   └── Channels WebSocket ← InMemoryChannelLayer
├── watchfiles (asyncio task)
│   └── JSONL file changed → parse → save to DB → signal post_save → broadcast WS
├── SQLite
└── Frontend (Vue.js)
    └── WebSocket JSON → reactive store → auto-update UI
```

**Data flow:** watchfiles monitors JSONL files → parses changes → saves to Django models → post_save signal triggers → Django Channels broadcasts via WebSocket → Vue store updates reactively → UI re-renders automatically.

## Database Schema

Two tables for append-only JSONL sync:

- `files`: tracks file read state (`path`, `last_offset`, `last_line`, `mtime`)
- `lines`: stores JSONL content (`path`, `line_num`, `content` as TEXT)

**Sync strategy:** On file change, compare `mtime` → `seek()` to `last_offset` → read new lines → insert to DB → update offset. Files are append-only.

**JSON querying:** SQLite 3.38+ native JSON support with `->>` operator. Index frequently queried JSON paths with expression indexes or generated columns.

## Project Structure (Planned)

```
.
├── run.py                      # Entry point
├── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── main.js
│   │   ├── store.js            # Vue reactive state + WebSocket handling
│   │   ├── async.js            # Async component definitions
│   │   ├── wa.js               # Web Awesome setup
│   │   ├── App.vue
│   │   └── components/
│   │       └── messages/       # Dynamic message type components
│   └── dist/
└── src/claude_code_web/
    ├── __init__.py
    ├── bootstrap.py            # Auto-setup (npm, build, migrations)
    ├── settings.py
    ├── asgi.py                 # WebSocket consumer + routing
    ├── watcher.py              # JSONL file watcher
    └── core/
        └── signals.py          # Django signals for broadcasts
```

## Code Quality

- Python: ruff (line-length=120), mypy (strict, Python 3.13)
- Tests: pytest with pytest-django
- **Language:** All code content (UI strings, comments, variable names) must be in English. Only documentation files (*.md) may contain French.

## Python Patterns

- **Immutable data containers:** Always use `NamedTuple` for simple immutable data structures (return values, decisions, configs). Works with all field types including lists. Prefer over `@dataclass` when mutability is not needed.
- **JSON parsing:** Use `orjson` instead of the standard `json` module for all JSON operations in the backend. It's ~6x faster and handles the high-volume JSONL file parsing efficiently.

## Key Implementation Notes

- Bootstrap auto-handles: npm install (if needed), frontend build (if outdated), Django migrations
- Vue components use Composition API with `<script setup>`
- Web Awesome custom elements use `wa-` prefix (configured in Vite)
- KeepAlive caches up to 5 conversations (preserves scroll, collapsed states)
- UI state persisted to localStorage via VueUse
- No authentication layer (current design)

## Web Awesome Components

**Version:** Web Awesome 3 (using next, which is > 3). Since version 3, **native** browser events are no longer prefixed with `wa-` (e.g., `@click`, `@focus`, `@input`). However, **custom** Web Awesome events still use the `wa-` prefix (e.g., `@wa-show`, `@wa-hide`, `@wa-after-show`).

**IMPORTANT:** Each Web Awesome component used must be explicitly imported in `frontend/src/main.js`. Imports load both the component JS and its styles (via shadow DOM).

```javascript
// Example in main.js
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
```

If a `wa-*` component appears unstyled in production (but works in dev), it's likely missing its import in `main.js`.

## Web Awesome Documentation

A nearly complete "one file" version of the docs is available at `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`
Full documentation is also at `/home/twidi/dev/webawesome/packages/webawesome/docs/docs/` (`usage.md` and `frameworks/vue.md`) 
