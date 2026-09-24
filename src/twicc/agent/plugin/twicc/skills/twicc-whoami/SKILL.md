---
name: twicc-whoami
description: Return the details of the session that owns the calling process. Use to discover your own TwiCC session_id from inside a Bash tool, to reference your own session.
---

# TwiCC Whoami

Identify the TwiCC session you are running in. Exits 1 if not running inside a TwiCC agent.

## When to use

- You need your own `session_id` and don't already have it in context.

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
$TWICC whoami [--slim | --full]    # emit a single JSON object describing the calling session
```

- `--slim` — the `session self` payload, reduced (the default from 2026-10-01).
- `--full` — the `session self` payload in full — every field of the session payload.
- Neither flag — the legacy object until 2026-10-01 (see Output format), then the reduced payload. **Pass `--slim` or `--full` now**, so a script reads the same keys on both sides of the date.

### Self-aware shortcuts

Commands that accept `self` resolve the calling session on their own. On `$TWICC sessions` and `$TWICC search` use `--spawned-by self` (direct children), `--descendants self` (every descendant, you excluded), or `--spawn-tree self` (the whole spawn tree that contains you — any session id in the tree resolves to the same tree). `--spawned-by` and `--descendants` also accept `parent` (the session that spawned the current one): `--spawned-by parent` surfaces your siblings (yourself included), and `--descendants parent` surfaces your siblings + their subtrees + your own subtree. Use `$TWICC topology self` when you need the surrounding spawned-session tree.

## Output format

With `--slim` or `--full` — usable now — and from 2026-10-01 without a flag, it returns the `session self` payload: the session row (reduced, or in full with `--full`) with its `process` block inside, exactly what `$TWICC session self` returns with the same flag. **Until 2026-10-01 a call with neither flag keeps its current object**: `session_id`, `title`, `project_id`, `project_directory`, `current_working_directory` (resolved from tool_use activity — may differ from `project_directory` when working in a worktree or another repo), `artifacts_dir` and `scratch_dir` (the session's own working directories, already joined with the session id), `orchestration_scratch_dir` (the shared scratch folder, present only when the session is part of an orchestration tree), the resolved `agent_settings`, the `session` sub-object (the serializer payload of the session), and the matching `process` row — nine fields (`id`, `provider`, `session_id`, `session_title`, `project_id`, `state`, `started_at`, `last_state_change_at`, `pid`), not that block's compact five. That flagless call prints a one-line notice on stderr in a terminal (never on MCP).

Where the old keys are read from in the new shape:

- `session_id` → `id`
- `title`, `project_id`, `project_directory`, `artifacts_dir`, `scratch_dir` → same names
- `orchestration_scratch_dir` → same name, `null` instead of absent outside an orchestration
- `current_working_directory` → `git_directory`
- `agent_settings.<field>` → `<field>` (effective: the stored value, else the current default)
- `session.<field>` → `<field>` (with `--full` for the fields the reduced projection drops)
- `process` (nine fields) → `process`: `{state}`, or five fields (`id`, `state`, `started_at`, `last_state_change_at`, `pid`) with `--full`; `provider`, `session_id`, `session_title`, `project_id` are the row's own `provider`, `id`, `title`, `project_id`

### Exit codes

- `0` — Session resolved.
- `1` — No session found in PID ancestry.
- `2` — `--slim` and `--full` passed together (checked before the lookup, so also outside a session).

## Examples

```bash
MY_SESSION_ID=$($TWICC whoami --slim | jq -r .id)
MY_MODEL=$($TWICC whoami --slim | jq -r .selected_model)
MY_PID=$($TWICC whoami --full | jq -r .process.pid)
MY_ARTIFACTS_DIR=$($TWICC whoami | jq -r .artifacts_dir)
MY_SCRATCH_DIR=$($TWICC whoami | jq -r .scratch_dir)
```

## Related commands

- `$TWICC sessions --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — children/siblings, every descendant (self/parent excluded), or the full spawn tree. Skill: `twicc-sessions`.
- `$TWICC sessions --spawned-by self --active` — only the ones with a live process. Skill: `twicc-sessions`.
- `$TWICC search '<query>' --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — same for full-text search. Skill: `twicc-search`.
- `$TWICC topology self` — map the spawned-session tree around you. Skill: `twicc-topology`.
