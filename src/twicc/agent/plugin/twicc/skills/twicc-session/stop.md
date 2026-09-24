# `session <SESSION_ID> stop` — stop the live agent

Stop the agent behind one session, your own included. MCP tool: `mcp__twicc__session_stop`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC session <SESSION_ID|self> stop [--timeout N] [--force]
```

- `--force` — SIGKILL now, without the grace window.
- `--timeout` (default 30 s) — the wait for the server's final status; the kill may still apply after it.
- Idempotent: an already-stopped session reports `stopped`.
- The way to stop your own session (`session self stop`): `sessions stop` never does.

## Examples

```bash
$TWICC session abc123 stop
$TWICC session abc123 stop --force
$TWICC session self stop
```

## Related commands

- `$TWICC sessions stop` — several sessions at once (never the calling one). Skill: `twicc-sessions`.
- `$TWICC session <ID>` — `process.state` tells whether an agent still runs. File: `row.md`.

## How to present results

1. Report the `status`; `stopped` also covers a session that was already stopped.
