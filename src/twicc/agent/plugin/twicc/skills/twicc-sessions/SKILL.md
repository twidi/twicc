---
name: twicc-sessions
description: List sessions tracked by TwiCC with each one's live process state, batch-look them up by id, wait until several of them conclude, or stop the agents behind them. Use when you or the user want to browse sessions, find a session ID, filter by project, see which are still running, block until several of them conclude, or batch-stop them.
---

# TwiCC Sessions

List sessions, or batch-look up specific ones with `sessions get`. Only returns valid sessions (with a creation date and at least one user message), unless you use `get` (which returns anything you name explicitly).

## When to use

- You or the user want to list or browse sessions.
- You need to find a session ID.
- You want to block until several sessions conclude — one wall-clock wait instead of one per session.
- You have a list of known session_ids and want to batch-fetch their metadata — use `sessions get <ID>...` (returns subagents, archived, and hidden too since you named them explicitly).


## Waiting on several at once

```bash
$TWICC sessions wait-reply [SESSION_ID...] [--since INSTANT] [--wait-first|--wait-all] [--wait-timeout N] [--no-reply-text] [FILTERS]
```

The plural of `session <ID> wait-reply`, on sessions **nobody just messaged**: spawned earlier, steered from the UI, messaged by someone else. When you sent the messages yourself, use `send-messages --wait-reply` instead — it reads each cursor server-side and needs nothing from you.

One loop polls them all and **one budget covers the batch**, so it costs a wall-clock wait, not N of them. Each concludes the same two ways the singular does — an answer, or a **pending request** only a human can clear — and an answer arriving in the same poll wins. `--wait-all` (default) waits for every one; `--wait-first` stops at the first to conclude, leaving the rest `outcome: pending`.

**A bare call is refused** — at least one id or one filter. The listing with no filter shows a page; a wait with no filter would poll every session TwiCC has indexed until the deadline.

Filters: `--project`, `--workspace`, `--provider`, `--state`, `--active`, `--only-hidden`, `--spawned-by`, `--spawn-tree`, `--descendants`, `--siblings` and `--annotation`, with named ids **unioned** on top (never replacing them). `--include-hidden` and `--include-archived` do not apply — hidden is always on, since orchestration workers are hidden by convention, and archived always off, since archiving kills the agent; `--only-hidden` still narrows. `--state dead` is refused: a session with no process will never speak. `--active` is the shorthand for "wait on everything alive". Filters see indexed sessions only: a child spawned seconds ago matches none yet, so **name its id** (from its `create-session` result). A barrier on a subset (one phase, one annotation) names only that subset's ids: a named id outside the subset would widen the barrier, since filters never narrow ids.

**Each session starts above its own last line** — "tell me the next thing each of them says". `--since` names that cursor as an ISO 8601 instant instead, translated per session. There is no `--from`: line 42 is a different place in every transcript, which is why an instant is what addresses a batch at all.

Returns `summary` + `results`, one `reply` block per id — the block the singular returns. A named id with a live process but no indexed transcript yet (just spawned) is waited on from line 0; `--since` does not apply to it. A named id with neither a session nor a live process comes back as `outcome: unknown_session` carrying only that and `session_id` — none of the four keys every other block has, since there was nothing to wait on — rather than being dropped. `summary` carries `total` (every id asked for, unknown ones included), `replied`, `awaiting_user_input`, `concluded` and `all_replied`. `replied` counts `outcome: replied` alone; `concluded` also counts the ones that ended on a pending request, so `all_replied` can be `false` with nothing left to wait for. Exit 0 whatever the outcomes — a per-id verdict does not fit in one code, so branch on `summary.all_replied` or on each `outcome`; `1` on a local refusal, before anything is waited on.

**Resuming a timed-out batch:** pass `--since` the instant the batch started and each cursor is placed from that instant, so nothing stamped after it is missed. That is what `--since` is for. Without it, re-running re-reads each session's current last line and silently skips an answer that arrived in between; the per-session alternative is each block's `since_line_num` handed to `$TWICC session <ID> wait-reply --from`.

## Stopping what is running

```bash
$TWICC sessions stop [SESSION_ID...] [--force] [--timeout N] [FILTERS]
```

Stops the agents behind the selected sessions. Only sessions that **currently have a process** are targeted — a stopped one has nothing to stop — so a bare `$TWICC sessions stop` stops everything running, bounded by what is alive rather than by how many sessions exist. Filters narrow it, with the same selection as the listing: `$TWICC sessions stop --spawned-by self` stops your children, `--state awaiting_user_input` stops what is blocked on a human. Named ids are added to the filters' selection (unioned), so `$TWICC sessions stop <MANAGER_ID> --descendants <MANAGER_ID>` stops a manager and its subtree. **No guardrail spares the caller:** a bare call stops you too; `parent`, `--spawn-tree` and `--siblings` reach beyond your children (`--spawn-tree self` includes you); `--annotation` alone selects across every tree — pair it with a filiation scope.

**Hidden sessions are included.** They are hidden when *listing*, but orchestration workers are hidden by convention and sparing them would leave them running. Archived ones never appear: archiving already kills the agent. `--state dead` is refused — there is nothing to stop.

Idempotent, and it never fails as a whole: exit 0, one entry per target carrying `status` (`stopped` / `rejected` / `failed` / `timeout` / `skipped_*`), `session_known` and `error`. `--force` SIGKILLs immediately instead of letting the turn finalize; `--timeout` (default 30 s) is a wall-clock budget for the whole batch, not per session.

For a single session, `$TWICC session <ID> stop` does the same thing.


## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### List

```bash
$TWICC sessions [OPTIONS]
```

Results are ordered by most recently active.

- `--project <PROJECT>` — filter by project (path or id; **drop the leading dash** on ids). A normal project also returns its git worktrees' sessions (a worktree's sessions belong to its main repository); a worktree project returns only its own. Non-existent project returns empty, no error.
- `--workspace ID` — filter to sessions of projects in the given workspace, including each member project's git worktrees. Mutually exclusive with `--project`.
- `--limit N` — max results (default: 20; 50 with `--paginated`).
- `--offset N` — skip first N for pagination (default: 0).
- `--slim` / `--full` — the projection of each row. The reduced one: identity, state, cost, and the `has_*` flags (`has_tasks`, `has_goals`, `has_plan`, `has_artifacts`, `has_workflows`) saying what else there is to fetch; it drops the per-session payloads (`tasks`, `plan_paths`, `goals`, `layout`), the redundant timestamps and paths, and the agent-settings bundle — **about 80% lighter**. `--full` returns the full payload, the one `session <ID>` returns. **Before 2026-10-01 the full payload is the default and `--slim` opts in; from that date the reduced projection is the default and `--slim` is an accepted no-op** — `--full` keeps working on both sides. Until then a call with neither flag prints a one-line notice on stderr (in the RPC envelope's `warnings` key; never on MCP), next to the `--paginated` one. Mutually exclusive (exit `2`). `sessions get` takes them too, placeholders included, so a batch lookup keeps one shape.
- **Live process state:** every row carries a `process` block. `{state}` in the reduced projection, plus `id`, `started_at`, `last_state_change_at` and `pid` with `--full`. `state` is one of `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead` — note `awaiting_user_input`, the non-obvious stop: the agent is blocked on a human, not working. It answers **what TwiCC is running**, so `dead` means no TwiCC-managed process, which is also the answer for the many sessions TwiCC indexed but never started (a `claude` run straight from a terminal keeps generating and still reads `dead`). `dead` also covers a TwiCC that is not running at all: an agent does not outlive its backend, so "TwiCC is down" and "TwiCC runs nothing for this session" are the same fact. The block is `null` for one case only — a subagent, which runs inside its parent's process and never has one of its own; `parent_session_id` identifies those.
- **Filtering on it:** `--state <VALUE>` keeps one bucket — **repeatable and OR-combined**, since a session holds a single state, so `--state assistant_turn --state awaiting_user_input` means "busy or blocked" and naming all five is the unfiltered listing. `dead` is accepted and means "no TwiCC-managed process"; `--active` keeps every state but `dead` — `user_turn` included, the agent is loaded and idle, not gone. The two are mutually exclusive. `--provider claude_code|codex` is an ordinary column filter, unrelated to state. The state filter runs **before** the page is cut, so `total` and `has_more` count what matches. Hidden sessions stay hidden unless you ask (`--include-hidden`, or any filiation scope, which lifts it for you).
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`, so you know whether another page follows instead of guessing from the page size. Without an explicit `--limit` the page size becomes **50**. **Before 2026-10-01 the flag is opt-in and a call without it is unchanged; from that date the envelope is the only shape and the flag is an accepted no-op.** Passing it works on both sides.
- `--include-archived` — include archived sessions (excluded by default).
- `--include-hidden` — include hidden sessions (excluded by default).
- `--only-hidden` — only hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self|parent>` — filter to direct child sessions spawned by the given session ID. `self` is the current session (= my children); `parent` is the session that spawned the current one (= my siblings, myself included). Implies `--include-hidden`. Mutually exclusive with `--spawn-tree` and `--descendants`.
- `--spawn-tree <ID|self>` — filter to every session in the spawn tree that contains the given session ID. Any id in the tree works — root, middle, or leaf: the CLI looks it up and resolves to the tree it belongs to. A standalone session (no children, never spawned) queried by its own id is returned as a single-node tree. `self` resolves to the tree that contains the current session. Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--descendants`.
- `--descendants <ID|self|parent>` — filter to the proper descendants of the given session (every session transitively spawned by it, target excluded). `self` is the current session; `parent` is the current session's spawner (= my siblings, their subtrees, and my own subtree). Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--spawn-tree`. Use this when you want "everything under X" but not X itself — `--spawn-tree` gives the whole tree containing X (root included), `--spawned-by` only direct children.
- `--siblings <ID|self>` — filter to the siblings of the given session: the *other* sessions spawned by the same parent, **target always excluded**. `self` is the current session's peers. `parent` is **not** supported (siblings are relative to a node's own parent). Implies `--include-hidden`. Mutually exclusive with `--spawned-by`, `--spawn-tree` and `--descendants`. This is the direct way to list your peers; `--spawned-by parent` selects the same set but includes yourself.
- `--annotation KEY[OP]VALUE` — filter to sessions whose `annotations` object matches the expression. Repeatable; multiple flags are AND-combined. Does **not** imply `--include-hidden` (orthogonal). Five operators:
  - `KEY=VALUE` — annotation key equals VALUE.
  - `KEY!=VALUE` — annotation key differs from VALUE (or key absent).
  - `KEY:exists` — annotation key is present (any value).
  - `KEY:not-exists` — annotation key is absent.
  - `KEY:in:V1,V2,...` — annotation key equals one of the listed values; escape a literal comma with `\,`.
  - Values are inferred as typed: `true`/`false` → boolean, `null` → null, integers and floats parsed numerically, everything else → string (same rules as `create-session --annotation`).
  - Example: `--spawn-tree self --annotation role=implementer`

### Batch lookup

```bash
$TWICC sessions get <SESSION_ID> [<SESSION_ID>...] [--slim | --full]
```

Returns one entry per id in input order (duplicates collapsed). No filter flags — all session types returned. Same projection as the listing (`--slim` / `--full`, same default and same date).

## Output format

### Listing

The reduced projection — the default from 2026-10-01, `--slim` before:

```json
[
  {
    "id": "abc123-def456",
    "project_id": "-home-twidi-dev-myproject",
    "provider": "claude_code",
    "title": "Implement user authentication",
    "annotations": {"role": "reviewer"},
    "parent_session_id": null,
    "spawned_by": null,
    "spawn_root": null,
    "created_at": "2025-03-10T14:30:00+00:00",
    "last_new_content_at": "2025-03-10T15:45:00+00:00",
    "context_usage": 85000,
    "context_max": 200000,
    "total_cost": 1.801,
    "user_message_count": 12,
    "model": {"raw": "claude-opus-4-20250514", "family": "opus", "version": "4"},
    "git_branch": "feature/auth",
    "archived": false,
    "hidden": false,
    "pinned": null,
    "stale": false,
    "unavailable_reason": null,
    "mute_on_user_turn": false,
    "has_artifacts": false,
    "has_plan": true,
    "has_workflows": false,
    "has_tasks": true,
    "has_goals": false,
    "process": {"state": "user_turn"}
  }
]
```

With `--full` (and by default before 2026-10-01), each row is the full payload `session <ID>` returns: the fields above plus `last_line`, `mtime`, the `last_started_at` / `last_updated_at` / `last_stopped_at` / `last_viewed_at` timestamps, `slug`, `compute_version_up_to_date`, `self_cost` / `subagents_cost`, `cwd`, `git_directory`, the agent-settings bundle (`permission_mode`, `selected_model`, `effort`, `thinking_enabled`, `claude_in_chrome`, `fast_mode`), `compacted`, `layout`, `browser_url`, `tasks`, `plan_paths`, `goals`, `hybrid`, `artifacts_dir` — and a five-field `process` block.

### Key fields

- `provider` — `"claude_code"` or `"codex"`. Determines item schema and supported settings.
- `slug` — provider short id (e.g. Codex subagent nickname), or `null`. `--full` only.
- `parent_session_id` — `null` for regular sessions, set for subagents.
- `model` — `{"raw": "...", "family": "...", "version": "..."}`.
- `context_max` / `context_usage` — max context window and current usage in tokens.
- `compacted` — whether the session has been compacted at least once. `--full` only.
- `last_new_content_at` — most recent item appended.
- `last_viewed_at` — when the user last opened the session in TwiCC. `--full` only.
- `hidden` — whether the session is hidden from all listings and broadcasts.
- `spawned_by` — session ID that spawned this session, or `null`.
- `spawn_root` — root session ID for the spawned-session tree, or `null` before a session joins one.
- `annotations` — free-form JSON object attached at session creation.

### Batch lookup (`get`)

Same shape per entry, plus `known` boolean. When `known: false`, the session fields are `null` — but `process` is not: a `ProcessRun` row exists before the watcher writes the `Session` row, so a live block on an unknown id is a session that just started.

```json
[
  {"id": "abc123-def456", "title": "Implement user authentication", ..., "known": true},
  {"id": "typo-or-unknown", "title": null, ..., "known": false}
]
```

## Examples

```bash
$TWICC sessions
$TWICC sessions --project .
$TWICC sessions --project /home/twidi/dev/myproj
$TWICC sessions --workspace backend
$TWICC sessions --include-archived
$TWICC sessions --spawned-by self
$TWICC sessions --spawned-by parent
$TWICC sessions --spawn-tree self
$TWICC sessions --descendants self
$TWICC sessions --descendants parent
$TWICC sessions --siblings self
$TWICC sessions --limit 50 --offset 20
$TWICC sessions --annotation role=implementer
$TWICC sessions --spawn-tree self --annotation role=implementer
$TWICC sessions --annotation status:exists --annotation priority:in:high,critical
$TWICC sessions get abc123-def456
$TWICC sessions get abc123 def456 ghi789
```

## Related commands

- `$TWICC session <session_id>` — full metadata for one session (exit 1 if missing). Skill: `twicc-session`.
- `$TWICC topology <ID|self>` — map a spawned-session tree. Skill: `twicc-topology`.
- `$TWICC project <PROJECT>` / `$TWICC projects` — project details or listing. Skill: `twicc-project` / `twicc-projects`.
- `$TWICC search "<query>"` — full-text search across sessions. Skill: `twicc-search`.

## How to present results

1. Show session title, date, and message count.
2. If `--paginated` reports `has_more: true`, offer to fetch the next page with `--offset`.
3. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
4. Only include cost and model info if explicitly asked.
5. For `get` output: flag `known: false` entries as unknown (typo or already cleaned up).
