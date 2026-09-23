---
name: twicc-whoami
description: Return the details of the session that owns the calling process. Use to discover your own TwiCC session_id from inside a Bash tool, to reference your own session.
---

# TwiCC Whoami

Identify the TwiCC session you are running in. Returns a JSON object with `session_id`, `title`, `project_id`, `project_directory`, `current_working_directory` (resolved from tool_use activity — may differ from `project_directory` when working in a worktree or another repo), `artifacts_dir` and `scratch_dir` (the session's own working directories, already joined with the session id), `orchestration_scratch_dir` (the shared scratch folder, present only when the session is part of an orchestration tree), the resolved `agent_settings`, the full `session` payload (what `$TWICC session <ID>` returns, minus its `process` block), and the matching `process` row — nine fields (`id`, `provider`, `session_id`, `session_title`, `project_id`, `state`, `started_at`, `last_state_change_at`, `pid`), not that block's compact five. Exits 1 if not running inside a TwiCC agent.

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
$TWICC whoami    # emit a single JSON object describing the calling session
```

### Self-aware shortcuts

Commands that accept `self` resolve the calling session on their own. On `$TWICC sessions` and `$TWICC search` use `--spawned-by self` (direct children), `--descendants self` (every descendant, you excluded), or `--spawn-tree self` (the whole spawn tree that contains you — any session id in the tree resolves to the same tree). `--spawned-by` and `--descendants` also accept `parent` (the session that spawned the current one): `--spawned-by parent` surfaces your siblings (yourself included), and `--descendants parent` surfaces your siblings + their subtrees + your own subtree. Use `$TWICC topology self` when you need the surrounding spawned-session tree.

### Exit codes

- `0` — Session resolved.
- `1` — No session found in PID ancestry.

## Examples

```bash
MY_SESSION_ID=$($TWICC whoami | jq -r .session_id)
MY_MODEL=$($TWICC whoami | jq -r .agent_settings.selected_model)
MY_PID=$($TWICC whoami | jq -r .process.pid)
MY_ARTIFACTS_DIR=$($TWICC whoami | jq -r .artifacts_dir)
MY_SCRATCH_DIR=$($TWICC whoami | jq -r .scratch_dir)
```

## Related commands

- `$TWICC sessions --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — children/siblings, every descendant (self/parent excluded), or the full spawn tree. Skill: `twicc-sessions`.
- `$TWICC sessions --spawned-by self --active` — only the ones with a live process. Skill: `twicc-sessions`.
- `$TWICC search '<query>' --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — same for full-text search. Skill: `twicc-search`.
- `$TWICC topology self` — map the spawned-session tree around you. Skill: `twicc-topology`.
