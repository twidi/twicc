# TWICC

> **T**he **W**eb **I**nterface for **C**laude **C**ode

A web UI to browse and interact with your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions. View projects, sessions, conversation history, costs, and run Claude agents — all from your browser.

## Quick start

```bash
uvx twicc
```

Then open http://localhost:3500.

> **Note:** On first launch, TWICC synchronizes Claude Code's data files into its own database. This can take a minute or two depending on the number of projects and sessions. Subsequent starts are much faster.

TWICC is designed to run with [uv](https://docs.astral.sh/uv/). If you prefer using `pip install twicc` in your own virtualenv, that works too.

## Features

- Browse all your Claude Code projects and sessions
- View full conversation history with tool use details
- Start and interact with Claude agents from the browser
- Track costs and token usage per session
- Daily activity heatmaps
- Git integration (log, diffs, file browser)
- Optional password protection
- Real-time updates via WebSocket
- Fully mobile-friendly interface

## Remote access

The interface is fully usable on mobile. Combined with a tunnel service like [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) or [ngrok](https://ngrok.com/), you can access TWICC from anywhere and interact with Claude Code on the go.

> **Important:** If you expose TWICC over the internet, make sure to enable password protection (see [Configuration](#configuration) below).

## How it works

TWICC reads the JSONL data files that Claude Code stores in `~/.claude/projects/` and indexes them into a local SQLite database (`~/.twicc/db/data.sqlite`). These data files remain the source of truth for everything displayed in the interface — TWICC never modifies them. Whether you use Claude Code directly from the terminal or interact through TWICC, everything shows up in the same place.

When you start a session or send messages through TWICC, it uses the [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) to run real Claude Code processes under the hood. This means it uses your existing Claude Code credentials and configuration — there is nothing extra to set up. The conversation data written by Claude Code is then picked up by TWICC's file watcher and displayed in real time.

On each startup, TWICC detects changes and updates its database accordingly. While running, it watches the filesystem for new sessions and updates in real time.

## Requirements

- Python 3.13+
- A `~/.claude/projects/` directory (created by Claude Code)

## Configuration

All configuration is via environment variables, set in `~/.twicc/.env`:

| Variable              | Default     | Description                                |
|-----------------------|-------------|--------------------------------------------|
| `TWICC_PORT`          | `3500`      | Server port                                |
| `TWICC_PASSWORD_HASH` | *(empty)*   | SHA-256 hash to enable password protection |
| `TWICC_DATA_DIR`      | `~/.twicc/` | Data directory (database, logs)            |

Generate a password hash:

```bash
python -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"
```

## Development

```bash
git clone https://github.com/twidi/twicc.git
cd twicc
cd frontend && npm install && cd ..
```

Two ways to run in development:

**With `devctl.py`** (recommended) — runs Vite dev server with hot-reload, no build step needed:

```bash
./devctl.py start
```

Run `./devctl.py --help` for all available commands and configuration options.

**With `run.py`** — runs the backend only, requires a frontend build first:

```bash
cd frontend && npm run build && cd ..
uv run run.py
```

Optionally, install dev tools (django-extensions, ipython):

```bash
uv sync --group dev
```

## Tech stack

- **Backend:** Django 6, Uvicorn, Django Channels (WebSocket)
- **Frontend:** Vue.js 3, Vite
- **Database:** SQLite (with WAL mode)
- **Process management:** Claude Agent SDK

## Current limitations

- Claude agents always run with **permission bypass mode** enabled (no tool-use confirmations)
- The built-in **MCP server** (Chrome integration) is always active

Configuration options for these will be added in a future release.

## FAQ

**Can I use TWICC while Claude Code is running?**
Yes. TWICC only reads Claude Code's data files and never modifies them.

**Where is my data stored?**
By default in `~/.twicc/`. This includes the SQLite database and log files. Set `TWICC_DATA_DIR` to change the location.

**Where are the logs?**
In `~/.twicc/logs/backend.log`. This file is useful for troubleshooting startup or runtime issues.

**How do I reset the database?**
Delete `~/.twicc/db/data.sqlite*` and restart TWICC. It will rebuild from Claude Code's source files.

## License

MIT
