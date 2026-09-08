---
name: twicc-processes
description: List live TwiCC processes, batch-look up states by session_id, batch-stop agents, or batch-wait until sessions reach a given state. Use when you or the user want to see what's running, track spawned sessions, or gate a script on multiple sessions finishing.
---

# TwiCC Processes

Four modes: list all live processes, batch-look up states (`get`), batch-stop agents (`stop`), or batch-wait on states (`wait`).

## When to use

- You or the user want to see what's currently running in TwiCC.
- You need to check which sessions are busy, idle, or blocked on user input.
- You have a set of session_ids and want to batch-fetch their process state — use `get`.
- You want to stop multiple agents in one call — use `stop` (idempotent, tolerant of unknown/subagent/stale ids).
- You want to block a script until several sessions finish or one needs attention — use `wait`.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### List (default)

```bash
$TWICC processes [OPTIONS]
```

- `--provider claude_code|codex` — filter by provider.
- `--state STATE` — filter by state: `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`. (`dead` is rejected — never returned.)
- `--limit N` — max results (default: 20).
- `--offset N` — skip first N for pagination (default: 0).
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`, so you know whether another page follows instead of guessing from the page size. Without it the output shape is unchanged.
- `--include-hidden` — include processes for hidden sessions (excluded by default).
- `--only-hidden` — only processes for hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self|parent>` — filter to direct child sessions spawned by the given session ID. `self` is the current session (= my children); `parent` is the session that spawned the current one (= my siblings, myself included). Implies `--include-hidden`. Mutually exclusive with `--spawn-tree` and `--descendants`.
- `--spawn-tree <ID|self>` — filter to processes of every session in the spawn tree that contains the given session ID. Any id in the tree works — root, middle, or leaf: the CLI looks it up and resolves to the tree it belongs to. A standalone session (no children, never spawned) queried by its own id is returned as a single-node tree. `self` resolves to the tree that contains the current session. Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--descendants`.
- `--descendants <ID|self|parent>` — filter to processes of the proper descendants of the given session (every session transitively spawned by it, target excluded). `self` is the current session; `parent` is the current session's spawner (= my siblings, their subtrees, and my own subtree). Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--spawn-tree`. Use this when you want "everything under X" but not X itself.
- `--siblings <ID|self>` — filter to processes of the siblings of the given session: the *other* sessions spawned by the same parent, **target always excluded**. `self` is the current session's peers. `parent` is **not** supported. Implies `--include-hidden`. Mutually exclusive with `--spawned-by`, `--spawn-tree` and `--descendants`. Use this to watch what your peers are running; `--spawned-by parent` is the same set but includes yourself. (Listing only — `processes stop` / `processes wait` do **not** take `--siblings`: you observe peers, you don't control them.)
- `--annotation KEY[OP]VALUE` — filter to processes whose session's `annotations` object matches the expression. Requires one filiation scope (`--spawned-by`, `--spawn-tree`, `--descendants`, or `--siblings`) so annotations cannot accidentally select sessions from another orchestration. Repeatable; multiple flags are AND-combined. Does **not** imply `--include-hidden` (orthogonal). Five operators:
  - `KEY=VALUE` — annotation key equals VALUE.
  - `KEY!=VALUE` — annotation key differs from VALUE (or key absent).
  - `KEY:exists` — annotation key is present (any value).
  - `KEY:not-exists` — annotation key is absent.
  - `KEY:in:V1,V2,...` — annotation key equals one of the listed values; escape a literal comma with `\,`.
  - Values are inferred as typed: `true`/`false` → boolean, `null` → null, integers and floats parsed numerically, everything else → string (same rules as `create-session --annotation`).
  - Example: `--spawn-tree self --annotation role=implementer`

### Batch lookup (`get`)

```bash
$TWICC processes get <SESSION_ID> [<SESSION_ID>...]
```

Returns one entry per id in input order (duplicates collapsed). No filter flags.

### Batch stop (`stop`)

```bash
$TWICC processes stop [SESSION_ID...] [--timeout N] [--force] [--spawned-by X|--descendants X] [--annotation KEY[OP]VALUE]...
```

Idempotent. Explicit IDs and filtered IDs are merged (explicit IDs first, duplicates collapsed). IDs that fail pre-check (unknown, subagent, stale, no directory, unknown provider) get a `skipped_*` status — they don't consume timeout budget. Valid drops are processed in parallel, so `--timeout` (default 30 s) is a wall-clock budget for the whole batch. `--force` hard-kills every selected process (SIGKILL the tree, no grace window) — for wedged agents.

Filter flags for process control:

- `--spawned-by <ID|self>` — select direct children.
- `--descendants <ID|self>` — select every proper descendant.
- `--annotation KEY[OP]VALUE` — narrow selected sessions by annotations; requires `--spawned-by` or `--descendants`.

The two filiation flags are mutually exclusive. If no explicit IDs are passed, `--spawned-by` or `--descendants` is required. `--annotation` cannot be used by itself, even with explicit IDs. An empty filtered set returns `[]` and exits 0.

### Batch wait (`wait`)

```bash
$TWICC processes wait [SESSION_ID...] <STATUS>... --timeout N [--all|--first] [--transition] [--spawned-by X|--descendants X] [--annotation KEY[OP]VALUE]...
```

Session_ids and statuses are a **single positional list, auto-discriminated by value** — anything matching a valid state (`starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`) is a status; everything else is a session_id. Pass session_ids first, statuses last. Explicit IDs and filtered IDs are merged (explicit IDs first, duplicates collapsed). You may omit explicit session IDs when selecting the wait pool with filters.

- `--all` (default) — wait until every active session has matched.
- `--first` — stop as soon as one session matches.
- `--transition` — only match after at least one state change per session since the initial snapshot.
- `--timeout N` — **required**. Wall-clock budget for the whole batch. Exits 5 on timeout.
- `--spawned-by <ID|self>` / `--descendants <ID|self>` — select the wait pool by filiation. Mutually exclusive.
- `--annotation KEY[OP]VALUE` — narrow the wait pool by annotations; requires `--spawned-by` or `--descendants`.

Unknown explicit session_ids are dropped from the wait pool (`wait_status="skipped_unknown"`) and don't participate in `--all`/`--first`. An empty pool exits 0 immediately.

## Output format

### Listing

```json
[
  {
    "id": 42,
    "provider": "claude_code",
    "session_id": "abc123-def456",
    "session_title": "Implement user authentication",
    "project_id": "-home-twidi-dev-myproject",
    "state": "awaiting_user_input",
    "started_at": "2026-05-29T15:30:00+00:00",
    "last_state_change_at": "2026-05-29T15:45:12+00:00",
    "pid": 81287
  }
]
```

### Fields (listing)

- `state` — `starting`, `assistant_turn`, `awaiting_user_input`, or `user_turn`.
- `session_title` — `null` for brand-new sessions not yet picked up by the watcher.
- `pid` — `null` briefly after process start.

### Batch lookup (`get`)

Same shape per entry plus `session_known`. Three possible shapes:

- Live process: all fields filled, `session_known: true`.
- Session known, no live process: `state: "dead"`, process fields `null`, `session_known: true`.
- Unknown session_id: `state: "dead"`, `session_known: false`, most fields `null`.

```json
[
  {"id": 42, "session_id": "abc123", "state": "user_turn", "session_known": true, ...},
  {"id": null, "session_id": "def456", "state": "dead", "session_known": true, ...},
  {"id": null, "session_id": "typo", "state": "dead", "session_known": false, ...}
]
```

### Batch stop (`stop`)

```json
[
  {"session_id": "abc123", "session_known": true, "status": "stopped", "request_uuid": "...", "provider": "claude_code", "session_title": "...", "project_id": "...", "error": null},
  {"session_id": "typo", "session_known": false, "status": "skipped_unknown", "request_uuid": null, "provider": null, "session_title": null, "project_id": null, "error": "..."}
]
```

Possible `status` values:

- `stopped` — killed (or already not running — idempotent).
- `rejected` — server refused; see `error`.
- `failed` — unexpected server error; see `error`.
- `timeout` — no final status within `--timeout`.
- `skipped_unknown` — session_id not found.
- `skipped_subagent` — subagents cannot be stopped directly.
- `skipped_stale` — JSONL file gone.
- `skipped_no_directory` — project has no directory.
- `skipped_unknown_provider` — provider no longer recognized.

#### Exit codes (stop)

- `0` — ran to completion (check each entry's `status`)
- `1` — local validation error (e.g. `--timeout <= 0`)
- `2` — TwiCC server not running
- `64` — bad CLI usage

### Batch wait (`wait`)

```json
[
  {"session_id": "abc123", "session_known": true, "wait_status": "matched", "matched_state": "user_turn", "current_state": "user_turn", "provider": "claude_code", "session_title": "...", "project_id": "..."},
  {"session_id": "def456", "session_known": true, "wait_status": "pending", "matched_state": null, "current_state": "assistant_turn", ...},
  {"session_id": "typo", "session_known": false, "wait_status": "skipped_unknown", ...}
]
```

Possible `wait_status` values:

- `matched` — reached a requested state; see `matched_state`.
- `pending` — did not match before the wait ended (timeout or `--first` triggered elsewhere).
- `skipped_unknown` — session_id not found.

#### Exit codes (wait)

- `0` — condition satisfied (or wait pool was empty)
- `1` — local validation error
- `2` — TwiCC server not running
- `5` — timeout
- `64` — bad CLI usage

## Examples

```bash
$TWICC processes
$TWICC processes --state awaiting_user_input
$TWICC processes --spawned-by self
$TWICC processes --spawned-by parent
$TWICC processes --spawn-tree self
$TWICC processes --descendants self
$TWICC processes --descendants parent
$TWICC processes --siblings self
$TWICC processes --spawn-tree self --annotation role=implementer
$TWICC processes --spawn-tree self --annotation status:exists --annotation priority:in:high,critical
$TWICC processes get abc123 def456 ghi789
$TWICC processes stop abc123 def456
$TWICC processes stop abc123 def456 --timeout 60
$TWICC processes stop --spawned-by self --annotation status=failed
$TWICC processes wait abc123 def456 user_turn dead --timeout 300
$TWICC processes wait abc123 def456 ghi789 awaiting_user_input --first --timeout 120
$TWICC processes wait abc123 def456 user_turn --transition --timeout 600
$TWICC processes wait --spawned-by self user_turn dead --timeout 300
```

## Related commands

- `$TWICC process <session_id>` — inspect/stop/wait for a single session. Skill: `twicc-process`.
- `$TWICC update-sessions <op> [SESSION_ID...]` — batch-update session rows (hide, archive, pin, annotations…) with the same selection model. Skill: `twicc-update-sessions`.
- `$TWICC session <session_id>` — session metadata. Skill: `twicc-session`.
- `$TWICC sessions` — browse all sessions (including stopped). Skill: `twicc-sessions`.
- `$TWICC topology <ID|self>` — show process states in tree context. Skill: `twicc-topology`.

## How to present results

1. For listing: group by provider; show title (or session_id), state, and time since `last_state_change_at`. Map states: `starting` → "spinning up", `assistant_turn` → "working", `awaiting_user_input` → "blocked — pending dialog", `user_turn` → "idle". If empty, say "no processes currently running".
2. For `get`: flag `session_known: false` as unknown; `state: "dead"` + `session_known: true` as "no live process".
3. For `stop`: bucket by `status` and show counts (e.g. "3 stopped, 1 skipped"). Include `error` for non-`stopped` entries.
4. For `wait`: surface `matched` entries with `matched_state`, `pending` with `current_state`, `skipped_unknown` separately. Call out timeout explicitly on exit 5.
5. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
