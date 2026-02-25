# CLAUDE.md

## Project Overview

**TwiCC** — *Twi* for Twidi, *CC* for Claude Code — The Web Interface for Claude Code. A standalone, self-contained web application that provides a rich UI for browsing and interacting with Claude Code sessions. Single process, zero external services, one command to launch.

**Quality approach:** We aim to implement everything to the best standards possible. The only shortcuts we allow: no tests and no linting.

**IMPORTANT — Development workflow:** Never start implementing code without being explicitly invited to do so. When the user explains requirements or shares thoughts, wait for them to finish and confirm before writing any code. Ask clarifying questions if needed, but do not assume that an explanation is an invitation to implement.

**IMPORTANT — Git rebase:** Never rebase on remote branches (e.g., `origin/main`) unless explicitly requested. Always rebase on local branches. If the local branch exists, use it.

## Stack

| Layer            | Technology                                  |
|------------------|---------------------------------------------|
| Package Manager  | uv (Python), npm (frontend)                 |
| Backend          | Django 6 (ASGI) + Uvicorn, Python ≥ 3.13    |
| WebSocket        | Django Channels + InMemoryChannelLayer      |
| Database         | SQLite (WAL mode)                           |
| File Watching    | watchfiles                                  |
| Claude SDK       | claude-agent-sdk (for interactive sessions) |
| Frontend         | Vue.js 3 (SFC, Composition API) + Vite 7    |
| State Management | Pinia + VueUse                              |
| UI Components    | Web Awesome 3+ (wa-* elements)              |
| Code Editor      | Monaco (CDN-loaded via vue-monaco-editor)   |
| Terminal         | xterm.js with PTY backend                   |
| Markdown         | markdown-it + shiki + mermaid               |

## Architecture

```
twicc (entry: run.py → cli.main())
├── Startup
│   ├── Initial sync — scans ~/.claude/projects/, bulk-inserts raw SessionItems (no metadata)
│   └── Background compute (multiprocessing) — computes metadata for all sessions if
│       CURRENT_COMPUTE_VERSION changed (display_level, kind, groups, costs, git info).
│       Runs once at startup for sessions needing it, then exits.
├── Django ASGI (Uvicorn)
│   ├── HTTP — REST API + SPA catch-all serving Vue frontend
│   └── WebSocket
│       ├── /ws/ — UpdatesConsumer (Channels): data sync, process control, title suggestions
│       └── /ws/terminal/<session_id>/ — Raw ASGI PTY terminal (optional tmux)
├── watchfiles (asyncio task)
│   └── JSONL file changed → incremental read → save to DB (with full metadata) → broadcast via WS
├── Periodic tasks
│   ├── Price sync from OpenRouter API (every 24h)
│   └── Usage quota fetch from Anthropic OAuth API (every 5min)
└── ProcessManager (Claude SDK)
    └── Manages interactive Claude sessions → SDK writes JSONL → watcher picks up
```

**Startup flow:** Initial sync bulk-inserts raw JSONL lines (fast, no computation). Then background compute (separate process) fills in metadata for sessions whose `compute_version` is outdated. The watcher computes metadata inline for real-time accuracy during normal operation.

**Data flow:** Claude SDK writes JSONL → watchfiles detects change → incremental read from last offset → save to Django models (with metadata) → WebSocket broadcast → Pinia store updates → Vue UI re-renders.

**Agent flow:** User sends message via WS → ProcessManager creates/resumes ClaudeProcess (SDK) → SDK writes JSONL → watcher picks up → broadcast back to frontend.

## Data Directory

All persistent data (database, logs, configuration) lives in a **data directory**:

| Priority | Condition                 | Data directory                        |
|----------|---------------------------|---------------------------------------|
| 1        | Running in a git worktree | Project/worktree root (always forced) |
| 2        | `$TWICC_DATA_DIR` is set  | `$TWICC_DATA_DIR`                     |
| 3        | Default                   | `~/.twicc/`                           |

```
<data_dir>/
├── .env                              # Configuration (ports, password hash)
├── db/
│   └── data.sqlite (+shm, +wal)     # SQLite database
└── logs/
    ├── backend.log                   # Backend application logs
    ├── frontend.log                  # Frontend (Vite) process output
    └── sdk/
        └── {session_id}.jsonl        # Raw SDK message logs (debug mode)
```

Path resolution is centralized in `src/twicc/paths.py`. The `devctl.py` script has its own equivalent logic (since it doesn't depend on Django).

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

**Log files:** `<data_dir>/logs/backend.log` and `<data_dir>/logs/frontend.log` — read these to debug issues.

**devctl-specific files** (always local to the project/worktree root):
- `.devctl/pids/` — PID files for running processes

When starting the backend, devctl passes `TWICC_DATA_DIR` to the backend process so it uses the correct data directory.

### Worktree Support

devctl automatically detects git worktrees (by comparing `git rev-parse --git-dir` vs `--git-common-dir`). When running in a worktree, it sets `TWICC_DATA_DIR` to the worktree root, so database, logs, and `.env` are all local to that worktree.

Each worktree has its own:
- instances of the backend and frontend servers
- `.env` file with port configuration (in the worktree root)
- `.devctl/` directory (PIDs)
- `db/data.sqlite*` database (in `<worktree>/db/`)
- `logs/` directory (backend.log, frontend.log, sdk/) in `<worktree>/logs/`

When starting dev servers in a worktree, just run `uv run ./devctl.py start`. devctl automatically:
- copies the database from `~/.twicc/db/` (if no local DB exists yet)
- finds available ports (incrementing from default+1: 3501 for backend, 5174 for frontend)
- saves the port configuration to the worktree's `.env` file

If the user explicitly asks to start with an empty/fresh database, use `uv run ./devctl.py start --empty-db`.

Always check your current working directory before starting the servers so you'll know if you are in a worktree or not.
When the user asks to start the servers in a worktree, give them the localhost urls for the frontend and backend servers based on the ports shown in devctl's output (e.g., `Frontend: http://localhost:5274`, `Backend: http://localhost:3501`).
When the user asks you to exit/kill/delete (etc...) a worktree, you MUST run the "stop all" command to kill the processes, even if you didn't start them yourself.

## Operations Reserved to User

Claude never runs these operations on its own initiative. If the user explicitly asks you to run one of these operations, do it without asking for confirmation. Otherwise, notify the user at the end of a task or, if absolutely necessary during your work, pause the task and ask them the permission to do it or to do them manually:

- **Django migrations:** After modifying models (and having created the migration yourself), remind the user to run `migrate`
- **Dev server restart:** After backend changes, remind the user to restart via `devctl.py`
- **Package installation:** After adding dependencies, remind the user to run `npm install` or `uv add`

## Database Models

Key models in `src/twicc/core/models.py`:

| Model | Purpose |
|-------|---------|
| `Project` | Maps to a `~/.claude/projects/{id}/` folder. Has `name`, `color`, `total_cost`, `sessions_count`. |
| `Session` | One JSONL file per session. Tracks `last_offset`/`last_line` for incremental sync. Has `title`, costs (`self_cost`, `subagents_cost`, `total_cost`), `type` (session/subagent), `parent_session` (self FK), `model`, `archived`, `pinned`. |
| `SessionItem` | One row per JSONL line. Has `display_level` (ALWAYS/COLLAPSIBLE/DEBUG_ONLY), `kind` (user_message, assistant_message, etc.), `group_head`/`group_tail` for collapsible groups, `cost`, `timestamp`. |
| `ToolResultLink` | Links tool_use to tool_result items within a session. |
| `AgentLink` | Links Task tool_use to spawned subagent session. |
| `ModelPrice` | Historical model pricing from OpenRouter API. |
| `UsageSnapshot` | Anthropic OAuth usage quota snapshots (5h and 7-day quotas). |
| `WeeklyActivity` / `DailyActivity` | Pre-computed activity stats per project. |

**Sync strategy:** On file change, compare `mtime` → `seek()` to `last_offset` → read new lines → insert to DB → update offset. Files are append-only.

## Code Quality

- **Language:** All code content (UI strings, comments, variable names) must be in English. Only documentation files (*.md) may contain French. **Important:** Even when the user speaks French, always write UI text and code in English.
- Python: ruff (line-length=120)
- Tests: pytest with pytest-django
- Vue components use Composition API with `<script setup>`

## Python Patterns

- **Immutable data containers:** Always use `NamedTuple` for simple immutable data structures (return values, decisions, configs). Works with all field types including lists. Prefer over `@dataclass` when mutability is not needed.
- **JSON parsing:** Use `orjson` instead of the standard `json` module for all JSON operations in the backend. It's ~6x faster and handles the high-volume JSONL file parsing efficiently.

## Frontend Patterns

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

### Draft System

Draft sessions, messages, and media attachments are persisted to IndexedDB (via `frontend/src/utils/draftStorage.js`). Hydrated on startup before Vue app mount.

### Virtual Scrolling

Large session item lists use a custom virtual scroller (`useVirtualScroll.js`, `VirtualScroller.vue`). Items go through a visual pipeline: raw items → `computeVisualItems()` (applies display mode, group expansion) → rendered in the scroller.

## Web Awesome Components

**Version:** Web Awesome 3.1. Since version 3, **native** browser events are no longer prefixed with `wa-` (e.g., `@click`, `@focus`, `@input`). However, **custom** Web Awesome events still use the `wa-` prefix (e.g., `@wa-show`, `@wa-hide`, `@wa-after-show`).

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
