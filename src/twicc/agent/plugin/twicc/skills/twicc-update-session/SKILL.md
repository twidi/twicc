---
name: twicc-update-session
description: Update an existing TwiCC session — change settings, title, annotations, archive/unarchive, pin/unpin, hide/unhide, or mute/notify. Use when you or the user want to change a session without sending a message.
argument-hint: <session_id|self> {settings|title|annotations|archive|unarchive|pin|unpin|hide|unhide|mute|notify} [ARGS / OPTIONS]
---

# TwiCC Update Session

Change one existing session without sending a message. This file is the index; each sub-command has its own file next to it.

## When to use

- Change model, effort, permission mode, or other settings → `settings`.
- Rename a session → `title`.
- Edit free-form session annotations → `annotations`.
- Archive (also stops the agent) / unarchive → `archive` / `unarchive`.
- Pin to project / workspace / globally, or unpin → `pin <MODE>` / `unpin`.
- Hide from all listings and broadcasts / unhide → `hide` / `unhide`.
- Suppress or restore finished-working notifications → `mute` / `notify`.
- Several sessions at once (every sub-command except `title`) → `$TWICC update-sessions` (skill: `twicc-update-sessions`).

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Common to all sub-commands

```bash
$TWICC update-session <SESSION_ID|self> <SUB-COMMAND> [ARGS] [--timeout SECONDS]
```

- The first argument is `SESSION_ID` (the session to update) or `self` (the current TwiCC session).
- `--timeout SECONDS` — seconds to wait for the server's response (default 30). The request is not cancelled: the update may still apply after it.
- You are in TwiCC — on success, link to the session: `[link text](/project/{project_id}/session/{session_id})`.

### Output format

```json
{"status":"updated","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"noop","session_id":"..."}
{"status":"validation_error","errors":[{"field":"--unset model","code":"unset_conflict","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

`noop` means every field you touched is a no-op for this session's provider (e.g. `--thinking` on Codex): nothing was written, and it exits `0`.

### Exit codes

- `0` — Update applied (or a no-op for this provider)
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout

### Errors

Local (exit 1), on every sub-command:

- `is_subagent` — subagents cannot be updated directly; target the parent session.
- `session_not_found`, `session_stale`, `project_no_directory`, `provider_disabled`.

Server (exit 3): every local code — the common ones above and each sub-command file's own — re-checked server-side, plus the server-only codes each file lists.

## Sub-commands

**ALWAYS READ THE SUB-COMMAND'S FILE BEFORE YOU CALL IT.** It holds the arguments, the sub-command's own errors, the pitfalls, and any result presentation beyond the common rules above. The files sit next to this `SKILL.md`.

| Sub-command | Purpose | File |
|---|---|---|
| `settings` | Change model, effort, permission mode and other agent settings (patch or preset). | `settings.md` |
| `title` | Rename the session. | `title.md` |
| `annotations` | Edit the free-form annotations with ordered operations. | `annotations.md` |
| `archive` / `unarchive` | Archive (stops the agent) or unarchive. | `archive.md` |
| `pin` / `unpin` | Pin to project / workspace / all, or unpin. | `pin.md` |
| `hide` / `unhide` | Hide from all listings and broadcasts, or unhide. | `hide.md` |
| `mute` / `notify` | Suppress or restore finished-working notifications. | `mute.md` |

## Related commands

- `$TWICC update-sessions <op> [SESSION_ID...]` — apply the same update, including `mute` or `notify`, to several sessions at once (no `title`). Skill: `twicc-update-sessions`.
- `$TWICC info [models|agent-settings|presets]` — discover providers, models, agent-settings values and presets before editing a session. Skill: `twicc-info`.
- `$TWICC session <session_id> stop` — stop the agent without touching the row. Skill: `twicc-session`.
- `$TWICC send-message <session_id>` — send a message (settings unchanged). Skill: `twicc-send-message`.
- `$TWICC session <session_id>` — one session's row (reduced from 2026-10-01; `--full` for every field). Skill: `twicc-session`.
