---
name: twicc-status
description: Report the live TwiCC backend's status. Use when you or the user want to know whether TwiCC is up, diagnose a failing CLI command, or grab the PID/port.
---

# TwiCC Status

Check whether the TwiCC backend is running and classify it into one of five states. Always outputs JSON, even on failure.

## When to use

- You or the user want to know whether TwiCC is running.
- A `twicc` subcommand failed with "TwiCC is not running" and you want to confirm before suggesting a restart.
- You need the live PID or port.
- A shell script needs to gate work on TwiCC being up (exit 0 = fully running).

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC status
```

No options. Shell gating:

```bash
$TWICC status >/dev/null && echo "TwiCC is up" || echo "TwiCC is down"
```

## Output format

```json
{
  "status": "running",
  "data_dir": "/home/twidi/.twicc",
  "pid": 12345,
  "port": 3500,
  "started_at": "2026-05-29T15:30:00+00:00",
  "heartbeat_age_seconds": 2.3,
  "heartbeat_stale_after_seconds": 15
}
```

### Fields

- `status` — one of `running`, `starting`, `stale`, `dead_pid`, `not_running`.
- `pid` — backend PID, or `null` when not running.
- `port` — HTTP port, or `null` when unknown.
- `started_at` — when the lock was acquired, or `null`.
- `heartbeat_age_seconds` — seconds since last heartbeat, or `null` if missing.

### Status values

- `running` — Heartbeat fresh, backend fully ready.
- `starting` — PID alive but no heartbeat yet — mid-boot.
- `stale` — Heartbeat too old — process alive but hung.
- `dead_pid` — Info file exists but PID is dead — crashed without cleanup.
- `not_running` — Info file missing — never started or clean shutdown.

### Exit codes

- `0` — `running`.
- `1` — any other status.

## Related commands

- `$TWICC sessions --active` — what's running inside the backend (only meaningful when `running`). Skill: `twicc-sessions`.
- `$TWICC session <session_id>` — a specific session, its live `process` block included. Skill: `twicc-session`.
- `$TWICC create-session` — fails fast when not `running`. Skill: `twicc-create-session`.

## How to present results

1. Lead with the key fact: "TwiCC is running" / "TwiCC is starting up" / "TwiCC is not running".
2. `running` — mention port + PID + uptime (from `started_at`).
3. `starting` — tell the user to wait a few seconds and re-check.
4. `stale` — backend looks hung; suggest checking `<data_dir>/logs/backend.log`. Surface `heartbeat_age_seconds`.
5. `dead_pid` — backend crashed; suggest removing the orphaned info file (path in output) and restarting.
6. `not_running` — tell the user to run `twicc` in another terminal.
