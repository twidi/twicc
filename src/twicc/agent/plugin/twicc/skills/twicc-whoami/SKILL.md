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
$TWICC whoami [--full]    # emit a single JSON object describing the calling session
```

- `--full` — the `session self` payload in full — every field of the session payload. Default: the reduced payload.

### Self-aware shortcuts

Commands that accept `self` resolve the calling session on their own. On `$TWICC sessions` and `$TWICC search` use `--spawned-by self` (direct children), `--descendants self` (every descendant, you excluded), or `--spawn-tree self` (the whole spawn tree that contains you — any session id in the tree resolves to the same tree). `--spawned-by` and `--descendants` also accept `parent` (the session that spawned the current one): `--spawned-by parent` surfaces your siblings (yourself included), and `--descendants parent` surfaces your siblings + their subtrees + your own subtree. Use `$TWICC topology self` when you need the surrounding spawned-session tree.

## Output format

It returns the `session self` payload: the session row (reduced by default, in full with `--full`) with its `process` block inside, exactly what `$TWICC session self` returns with the same flag. Key fields of the row:

- `id`, `title`, `project_id`, `project_directory`, `artifacts_dir`, `scratch_dir` (the session's own working directories, already joined with the session id).
- `orchestration_scratch_dir` — the shared scratch folder; `null` outside an orchestration tree.
- `git_directory` — the working directory resolved from tool_use activity; may differ from `project_directory` when working in a worktree or another repo.
- The effective agent settings, as top-level fields (`selected_model`, `effort`, ...): the stored value, else the current default.
- `--full` adds the fields the reduced projection drops (see `session <ID> --full`).

The `process` block:

- `process.state` — `starting`, `assistant_turn`, `awaiting_user_input` (blocked on a human), `user_turn` or `dead`.
- `process.background_work_in_progress` — what still runs behind the agent, **whatever `state` says**; `null` when nothing does (always on `dead`). Else `{"subagents": N, "shells": N, "monitors": N, "scheduled_wakeup_at": ISO-8601 or null, "goal": bool}`:
  - `subagents` — live subagents. `shells` — shell commands still running, the session's or its subagents'; Claude Code counts only backgrounded ones, Codex every command whose process has not exited (one it is still polling mid-turn included; a subagent's once its first output reports it running). `monitors` — Claude Code `Monitor` tools. `scheduled_wakeup_at` — a pending Claude Code `ScheduleWakeup`. `goal` — a Codex `/goal` continuation.
  - `user_turn` with `shells > 0`: the turn is over, a shell still runs. TwiCC never auto-stops an idle session in that case (stopping it would kill the shell).
  - `assistant_turn` while the agent itself is silent: TwiCC keeps a turn open for live subagents, Monitors or a pending wake-up. The final answer may already be written; the agent may speak again when they finish.

### Exit codes

- `0` — Session resolved.
- `1` — No session found in PID ancestry.

## Examples

```bash
MY_SESSION_ID=$($TWICC whoami | jq -r .id)
MY_MODEL=$($TWICC whoami | jq -r .selected_model)
MY_PID=$($TWICC whoami --full | jq -r .process.pid)
MY_ARTIFACTS_DIR=$($TWICC whoami | jq -r .artifacts_dir)
MY_SCRATCH_DIR=$($TWICC whoami | jq -r .scratch_dir)
```

## Related commands

- `$TWICC sessions --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — children/siblings, every descendant (self/parent excluded), or the full spawn tree. Skill: `twicc-sessions`.
- `$TWICC sessions --spawned-by self --active` — only the ones with a live process. Skill: `twicc-sessions`.
- `$TWICC search '<query>' --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — same for full-text search. Skill: `twicc-search`.
- `$TWICC topology self` — map the spawned-session tree around you. Skill: `twicc-topology`.
