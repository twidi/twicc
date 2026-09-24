---
name: twicc-update-sessions
description: Apply the same update to several TwiCC sessions — archive/unarchive, pin/unpin, hide/unhide, mute/notify, annotations, or settings. Use when you or the user want to change many sessions in one call (e.g. mute every child, tag every worker, or bump model/effort).
argument-hint: '{archive|unarchive|pin|unpin|hide|unhide|mute|notify|annotations|settings} [SESSION_ID...] [--spawned-by X|--descendants X] [--annotation ...]'
---

# TwiCC Update Sessions

Apply the SAME change to every targeted session in one call: the batch sibling of `update-session`. This file is the index; each sub-command has its own file next to it.

## When to use

- Hide or unhide several sessions at once → `hide` / `unhide`.
- Suppress or restore finished-working notifications for several sessions → `mute` / `notify`.
- Archive / unarchive, or pin / unpin a set of sessions → `archive` / `pin` / etc.
- Tag every session in an orchestration with the same annotation → `annotations` with `--spawned-by self` or `--descendants self`.
- Change model / effort / permission mode of a whole batch → `settings` (applied per session against its own provider).
- You have a list of session_ids and one change to apply to all of them.
- No `title` sub-command: the same title on several sessions is meaningless. For one session, or a title, use `update-session` (skill: `twicc-update-session`).

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Common to all sub-commands

### Session selection

Every sub-command takes a positional `SESSION_ID...` list plus optional scope filters. Explicit ids and scope-selected ids are **merged** (union, explicit ids first, duplicates collapsed). `self` means the current session.

- `SESSION_ID...` — sessions to update; optional if a scope filter is given.
- `--spawned-by <ID|self>` — also target the direct children of the given session. `parent` is not supported. Mutually exclusive with `--descendants`.
- `--descendants <ID|self>` — also target every proper descendant of the given session (target excluded). Mutually exclusive with `--spawned-by`.
- `--annotation KEY[OP]VALUE` — narrow the `--spawned-by` / `--descendants` scope by annotation; repeatable, AND-combined. Requires a filiation scope and does **not** filter explicit ids. Same operators as `twicc sessions --annotation` (skill: `twicc-sessions`).
- `--timeout SECONDS` — wall-clock budget for the whole batch (default 30; the updates run in parallel).
- Neither ids nor a filiation scope: error (exit 1). An empty resolved set is not an error: `results` is `{}`, exit 0.

### Errors

- Argument-level problems fail the whole command (exit 1, plain text on stderr): bad `--timeout`, `--spawned-by`/`--descendants` together, `parent` scope, `--annotation` without a filiation scope, neither ids nor scope. Each file lists its own.
- Per-session problems never fail the batch. They land in `results[<id>]`:
  - `validation_error` — local lookup failure: `session_not_found`, `is_subagent`, `session_stale`, `project_no_directory`, `unknown_provider`. `settings` adds its own codes.
  - `rejected` — server business rule (e.g. hidden constraints).
- Same code vocabulary as `twicc-update-session`.

### Output format

One object keyed by session_id, plus a summary:

```json
{
  "summary": {"total": 3, "succeeded": 2, "failed": 1, "all_succeeded": false},
  "results": {
    "abc123": {"status": "updated", "session_id": "abc123", "provider": "claude_code", "project_id": "...", "request_uuid": "..."},
    "def456": {"status": "rejected", "errors": [{"field": "...", "code": "...", "message": "..."}], "request_uuid": "..."},
    "typo":   {"status": "validation_error", "errors": [{"field": "SESSION_ID", "code": "session_not_found", "message": "..."}]}
  }
}
```

- Per-id `status`: `updated`, `noop` (nothing to apply for that session's provider), `rejected`, `failed`, `timeout`, or `validation_error`.
- `succeeded` counts `updated` and `noop`; `failed` is everything else.

### Exit codes

- `0` — the batch ran and at least one session was updated or skipped as a no-op (or the resolved set was empty).
- `1` — local argument error.
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart).
- `6` — the resolved set was non-empty but no session was updated or skipped.

### How to present results

1. Bucket by per-id `status` and show counts (e.g. "2 updated, 1 rejected").
2. Surface `rejected` / `validation_error` entries with their `code`: the user sees which sessions need attention and why.
3. Call out a total failure (exit 6) explicitly.
4. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.

## Sub-commands

**ALWAYS READ THE SUB-COMMAND'S FILE BEFORE YOU CALL IT.** It holds the options, the sub-command's own errors, the pitfalls and the examples; the common selection, output, errors and presentation rules are in this index. The files sit next to this `SKILL.md`.

| Sub-command | Purpose | File |
|---|---|---|
| `settings` | Change agent settings (model, effort, permission mode...), per session against its own provider. | `settings.md` |
| `annotations` | Apply the same annotation operations to every session. | `annotations.md` |
| `archive` / `unarchive` | Archive (stops the agent) or unarchive. | `archive.md` |
| `pin` / `unpin` | Pin with one scope, or unpin. | `pin.md` |
| `hide` / `unhide` | Hide (per-session preconditions), or unhide. | `hide.md` |
| `mute` / `notify` | Suppress or restore finished-working notifications. | `mute.md` |

## Related commands

- `$TWICC update-session <id|self> <op>` — update one session (and the only place for `title`). Skill: `twicc-update-session`.
- `$TWICC send-messages [SESSION_ID...] --message <text>` — send the same message to several sessions (same selection model, plus a `--siblings self` peer broadcast this command does not have). Skill: `twicc-send-messages`.
- `$TWICC sessions stop [SESSION_ID...]` — batch-stop live agents; its selection is wider than this command's (`parent`, `--spawn-tree`, `--siblings`, `--annotation` alone), though it refuses a bare call and never stops you (`skipped_self`). Skill: `twicc-sessions`.
- `$TWICC sessions` — browse / filter sessions to pick the ids to update. Skill: `twicc-sessions`.
- `$TWICC topology <id|self>` — see the spawn tree before targeting `--descendants` / `--spawned-by`. Skill: `twicc-topology`.
