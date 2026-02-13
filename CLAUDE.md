# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TwiCC (Twi for Twidi, CC for Claude Code)  - A standalone, self-contained web application to replace the Claude Code CLI. Single process, zero external services, one command to launch.

**Status:** Active development. Original (POC) implementation details are documented in `docs/2026-01-23-architecture-decisions.md`.

**Quality approach:** We aim to implement everything to the best standards possible. The only shortcuts we allow: no tests and no linting.

**IMPORTANT - Development workflow:** Never start implementing code without being explicitly invited to do so. When the user explains requirements or shares thoughts, wait for them to finish and confirm before writing any code. Ask clarifying questions if needed, but do not assume that an explanation is an invitation to implement.

**IMPORTANT - Git rebase:** Never rebase on remote branches (e.g., `origin/main`) unless explicitly requested. Always rebase on local branches. If the local branch exists, use it.


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
uv run ./devctl.py status                   # Check running status and port config
uv run ./devctl.py logs [front|back]        # Show recent logs (--lines=N)
```

**Default ports:** Frontend on 5173, Backend on 3500. The script verifies correct port binding after start.

**Log files:** `.devctl/logs/frontend.log` and `.devctl/logs/backend.log` - read these to debug issues.

### Worktree Support

The application is able to run in multiple worktrees, each worktree has its own:
- instances of the backend and frontend servers
- `.env` file with port configuration
- `.devctl/` directory (logs, PIDs)
- `data.sqlite` database (3 files: `data.sqlite`, `data.sqlite-shm`, `data.sqlite-wal`)
 
If you are in a worktree and asked to run the dev servers, you MUST:
- copy the database file from the main worktree to the worktree root: `cp /path/to/main/worktree/data.sqlite* ./` (ONLY IF IT DOESN'T EXIST IN THE WORKTREE ROOT YET)
- configure ports to use to run the servers, ONLY IF THEY ARE NOT ALREADY CONFIGURED IN THE WORKTREE ROOT `.env` FILE:
  - find available ports on the system that we'll use for the frontend and backend servers
  - configure those ports in a `.env` file in the worktree root like in this example:

```env
# Backend port (Uvicorn server)
TWICC_PORT=3600

# Frontend port (Vite dev server)
VITE_PORT=5273
```

Always check your current working directory before starting the servers so you'll know if you are in a worktree or not. 
When the user asks to start the servers, if you are in a worktree, you MUST proceed as described above. And give them the localhost urls for the frontend and backend servers based on the ports configured in the worktree `.env` file (e.g., `Frontend: http://localhost:5273`, `Backend: http://localhost:3600`).
When the user asks you to exit/kill/delete (etc...) a worktree, you MUST run the "stop all" command to kill the processes, even if you didn't start them yourself.

## Operations Reserved to User

Claude never runs these operations on its own initiative. If the user explicitly asks you to run one of these operations, do it without asking for confirmation. Otherwise, notify the user at the end of a task or, if absolutely necessary during your work, pause the task and ask them the permission to do it or to do them manually:

- **Django migrations:** After modifying models (and having created the migration yourself), remind the user to run and `migrate`
- **Dev server restart:** After backend changes, remind the user to restart via `devctl.py`
- **Package installation:** After adding dependencies, remind the user to run `npm install` or `uv add`

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
- **Language:** All code content (UI strings, comments, variable names) must be in English. Only documentation files (*.md) may contain French. **Important:** Even when the user speaks French, always write UI text and code in English.

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

### Avoiding Circular Imports (HMR)

**CRITICAL:** Circular imports between frontend modules cause Vite HMR to fall back to full page reloads instead of hot updates. This has been a recurring issue.

**Rules to follow:**
- **Never** import `router.js` directly from utility files, composables, or stores. Use lazy `await import('../router')` if router access is needed (e.g., for redirects).
- **Never** create mutual static imports between stores (e.g., `settings.js ↔ data.js`) or between stores and composables (e.g., `data.js ↔ useWebSocket.js`). Use lazy `await import()` in the less-frequently-called direction.
- **Never** import Vue components statically from composables if those components import stores/composables that create a cycle. Use `defineAsyncComponent(() => import(...))` instead.
- **Common cycle patterns to avoid:**
  - `main.js → ... → someFile → main.js` (extract shared code to a utility file)
  - `router.js → views → components → composable/util → router.js` (lazy import router)
  - `store ↔ store` (lazy import in one direction)
  - `store ↔ composable` (lazy import in one direction)
  - `composable → component → store → composable` (use defineAsyncComponent)

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
Full documentation is also at `./frontend/node_modules/@awesome.me/webawesome/dist/skills/` (`references/components/`, `references/usage.md` and `references/frameworks/vue.md`)


## Dialog Forms Pattern

When creating a form inside a `wa-dialog`, refer to `frontend/src/components/ProjectEditDialog.vue` as the reference implementation. Key patterns:

- **Form element:** Wrap content in a `<form>` with `@submit.prevent="handleSave"` and a unique `id`
- **Submit button outside form:** Use `type="submit"` and set the `form` attribute via `setAttribute()` in a sync function (wa-button doesn't expose `form` as a property)
- **Focus management:** Use `@wa-after-show` event (not `autofocus` attribute) to focus the first input after the dialog animation completes, and use `setSelectionRange(len, len)` to position cursor at end
- **Input validation:** Apply `trim()` on text inputs before validation and submission
- **Uniqueness checks:** Validate client-side first (from store data), backend enforces with unique constraint
- **Error display:** Use `wa-callout variant="danger"` for validation and API errors
- **Dialog width:** Use `--width: min(Xpx, calc(100vw - 2rem))` to be responsive
