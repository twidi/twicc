---
name: twicc-update-sessions
description: Apply the same update to several TwiCC sessions — archive/unarchive, pin/unpin, hide/unhide, mute/notify, annotations, or settings. Use when you or the user want to change many sessions in one call (e.g. mute every child, tag every worker, or bump model/effort).
argument-hint: '{archive|unarchive|pin|unpin|hide|unhide|mute|notify|annotations|settings} [SESSION_ID...] [--spawned-by X|--descendants X] [--annotation ...]'
---

# TwiCC Update Sessions

Batch sibling of `update-session`: applies the SAME change to every targeted session in one call. Ten sub-commands: `archive`, `unarchive`, `pin`, `unpin`, `hide`, `unhide`, `mute`, `notify`, `annotations`, `settings`. For a single session, prefer `update-session` (skill: `twicc-update-session`).

`title` is intentionally not batchable here (setting the same title on several sessions is meaningless) — use `update-session <ID> title` per session.

## When to use

- Hide or unhide several sessions at once → `hide` / `unhide`.
- Suppress or restore finished-working notifications for several sessions → `mute` / `notify`.
- Archive / unarchive, or pin / unpin a set of sessions → `archive` / `pin` / etc.
- Tag every session in an orchestration with the same annotation → `annotations` with `--spawned-by self` or `--descendants self`.
- Change model / effort / permission mode of a whole batch → `settings` (applied per session against its own provider).
- You have a list of session_ids and one change to apply to all of them.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### Session selection (all sub-commands)

Every sub-command takes a positional `SESSION_ID...` list plus optional scope filters. Explicit ids and scope-selected ids are **merged** (union, explicit ids first, duplicates collapsed). `self` means the current session.

- `SESSION_ID...` — sessions to update; optional if a scope filter is given.
- `--spawned-by <ID|self>` — also target the direct children of the given session. `parent` is not supported. Mutually exclusive with `--descendants`.
- `--descendants <ID|self>` — also target every proper descendant of the given session (target excluded). Mutually exclusive with `--spawned-by`.
- `--annotation KEY[OP]VALUE` — narrow the `--spawned-by` / `--descendants` scope by annotation; repeatable, AND-combined. Requires a filiation scope and does **not** filter explicit ids. Same operators as `twicc sessions --annotation` (skill: `twicc-sessions`).
- `--timeout SECONDS` — wall-clock budget for the whole batch (default 30; drops run in parallel server-side).

If neither ids nor a filiation scope is given, the command errors (exit 1). An empty resolved set is not an error: `results` is `{}` and the command exits 0.

### `archive` / `unarchive`

```bash
$TWICC update-sessions archive [SESSION_ID...] [--spawned-by X|--descendants X]
$TWICC update-sessions unarchive [SESSION_ID...] [--spawned-by X|--descendants X]
```

`archive` kills each session's live agent and auto-unpins if `autoUnpinOnArchive` is on. `unarchive` flips the flag back; no agent restart.

### `pin` / `unpin`

```bash
$TWICC update-sessions pin [SESSION_ID...] --mode <project|workspace|all> [--spawned-by X|--descendants X]
$TWICC update-sessions unpin [SESSION_ID...] [--spawned-by X|--descendants X]
```

`--mode` is required for `pin` and applies the same scope to every session.

### `hide` / `unhide`

```bash
$TWICC update-sessions hide [SESSION_ID...] [--spawned-by X|--descendants X]
$TWICC update-sessions unhide [SESSION_ID...] [--spawned-by X|--descendants X]
```

`hide` requires each session to have a non-interactive `permission_mode` and `question_widget` disabled — both providers (see `twicc-update-session`); a session that doesn't qualify is `rejected` individually while the rest are hidden. `unhide` has no preconditions.

### `mute` / `notify`

```bash
$TWICC update-sessions mute [SESSION_ID...] [--spawned-by X|--descendants X]
$TWICC update-sessions notify [SESSION_ID...] [--spawned-by X|--descendants X]
```

`mute` suppresses only finished-working notifications for each targeted session. Questions and approvals still notify. `notify` restores each per-session notification path, but global notification settings still apply. Both commands are independent of hidden visibility and do not restart agents.

### `annotations`

```bash
$TWICC update-sessions annotations [SESSION_ID...] --op <OPERATION> [--op <OPERATION>...] [--spawned-by X|--descendants X]
```

Two distinct annotation flags here: `--op` is the **mutation** applied to each session; `--annotation` is a read-only **filter** on the scope. At least one `--op` is required; operations apply left-to-right (`clear`, `replace-file:PATH`, `merge-file:PATH`, `set:KEY=VALUE`, `unset:KEY` — same syntax as `update-session annotations`).

### `settings`

```bash
$TWICC update-sessions settings [SESSION_ID...] [FLAGS] [--spawned-by X|--descendants X]
```

Same flags as `update-session settings` (skill: `twicc-update-session`): `--preset`, `--model`, `--effort`, `--permission-mode`, `--thinking`, `--claude-in-chrome`, `--fast-mode`, `--question-widget`, `--context-max`, `--unset <field>`. Patch by default; `--preset` switches to replace mode. At least one flag or `--unset` is required.

Resolution is **per session against its own provider**, which makes a mixed-provider batch trivial. Provider-agnostic aliases resolve to each provider's concrete value — `--model max`, `--effort max`, `--permission-mode open`, `--context-max max` (and `min`, `strict`, `auto`, ...; see `twicc-create-session` for the full list) each land on the right value per session. A flag a session's provider doesn't support (e.g. `--thinking` on Codex) is silently ignored for that session — no error. A genuinely invalid value on a supported field (e.g. `--model opus` on a Codex session) yields a per-id `validation_error` (`invalid_choice` / `invalid_format` / `invalid_preset`) while the other sessions proceed.

Claude Code startup settings (`effort`, `thinking`, `claude-in-chrome`, `fast-mode`, `question-widget`) are applied on the next restart; Codex Fast mode applies on its next turn. No agent is interrupted mid-turn. Codex `question-widget` is a startup setting with **no automatic restart** — run `$TWICC sessions stop <ids>` (skill: `twicc-sessions`), then `$TWICC send-messages <ids>` (skill: `twicc-send-messages`) to apply the new value.

## Errors

Argument-level problems fail the whole command (exit 1, plain-text on stderr): bad `--timeout`, `--spawned-by`/`--descendants` together, `parent` scope, `--annotation` without a filiation scope, neither ids nor scope, invalid `--mode`, malformed `--op`, and (settings) a malformed flag shared by all ids (`unknown_unset_field`, `invalid_format`, `unset_conflict`, `no_op`).

Per-session problems never fail the batch — they are reported in `results[<id>]` with `status` `validation_error` (local lookup failure: `session_not_found`, `is_subagent`, `session_stale`, `project_no_directory`, `unknown_provider`; settings not applicable to the session's provider: `provider_disabled`, `invalid_choice`, `invalid_format`, `invalid_preset`) or `rejected` (server business rule, e.g. hidden constraints). Same code vocabulary as `twicc-update-session`.

## Output format

A single object keyed by session_id, plus a summary:

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

Per-id `status`: `updated`, `noop` (nothing to apply for that session's provider — e.g. a Claude-only flag like `--thinking` on a Codex session), `rejected`, `failed`, `timeout`, or `validation_error`. `succeeded` counts `updated` and `noop`; `failed` is everything else.

### Exit codes

- `0` — batch ran and at least one session was updated or skipped as a no-op (or the resolved set was empty)
- `1` — local argument error
- `2` — TwiCC server not running
- `6` — resolved set was non-empty but no session was updated or skipped
- `64` — bad CLI usage

## Examples

```bash
$TWICC update-sessions hide abc123 def456 ghi789
$TWICC update-sessions hide --descendants self
$TWICC update-sessions unhide --spawned-by self --annotation role=worker
$TWICC update-sessions mute --spawned-by self
$TWICC update-sessions notify abc123 def456
$TWICC update-sessions archive abc123 def456 --timeout 60
$TWICC update-sessions pin abc123 def456 --mode workspace
$TWICC update-sessions unpin --spawned-by self
$TWICC update-sessions annotations --spawned-by self --op set:reviewed=true
$TWICC update-sessions annotations abc123 def456 --op set:phase=done --op unset:wip
$TWICC update-sessions settings --spawned-by self --effort high
$TWICC update-sessions settings abc123 def456 --model opus
$TWICC update-sessions settings --descendants self --preset 'deep think'
```

## Related commands

- `$TWICC update-session <id|self> <op>` — update one session (and the only place for `title`). Skill: `twicc-update-session`.
- `$TWICC send-messages [SESSION_ID...] --message <text>` — send the same message to several sessions (same selection model, plus a `--siblings self` peer broadcast this command does not have). Skill: `twicc-send-messages`.
- `$TWICC sessions stop [SESSION_ID...]` — batch-stop live agents; its selection is wider than this command's (bare call, `parent`, `--spawn-tree`, `--siblings`, `--annotation` alone), and can stop you too. Skill: `twicc-sessions`.
- `$TWICC sessions` — browse / filter sessions to pick the ids to update. Skill: `twicc-sessions`.
- `$TWICC topology <id|self>` — see the spawn tree before targeting `--descendants` / `--spawned-by`. Skill: `twicc-topology`.

## How to present results

1. Bucket by per-id `status` and show counts (e.g. "2 updated, 1 rejected").
2. Surface `rejected` / `validation_error` entries with their `code` so the user knows which sessions need attention and why.
3. Call out a total failure (exit 6) explicitly.
4. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
