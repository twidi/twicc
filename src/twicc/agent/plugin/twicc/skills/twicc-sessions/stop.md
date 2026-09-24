# `sessions stop` — stop the agents behind several sessions

Stop the agents behind the selected sessions, never the calling one. MCP tool: `mcp__twicc__sessions_stop`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC sessions stop [SESSION_ID...] [--force] [--timeout N] [FILTERS]
```

- Only sessions that **currently have a process** are targeted: a stopped one has nothing to stop.
- **A bare call is refused** (exit `1`): at least one id or one filter, since a bare call would stop every running session. "Stop everything running" stays possible, on purpose: `--state starting --state assistant_turn --state awaiting_user_input --state user_turn`.
- `--force` — SIGKILL immediately instead of letting the turn finalize.
- `--timeout` (default 30 s) — a wall-clock budget for the whole batch, not per session.
- Idempotent.

### Selection

Filters `--project`, `--workspace`, `--provider`, `--state`, `--spawned-by`, `--spawn-tree`, `--descendants`, `--siblings`, `--annotation` select with the listing's rules (file: `list.md`, section "Filters"). E.g. `--spawned-by self` stops your children; `--state awaiting_user_input` stops what is blocked on a human. Differences:

- Named ids are added to the filters' selection (unioned): `$TWICC sessions stop <MANAGER_ID> --descendants <MANAGER_ID>` stops a manager and its subtree.
- **Hidden sessions are included**: orchestration workers are hidden by convention, and sparing them would leave them running.
- Archived ones never appear: archiving already kills the agent.
- `--state dead` is refused: there is nothing to stop.
- **Never stops the calling session.** Named (`self` included) or selected by a filter, it is reported `skipped_self`, counted in `failed`, and nothing is sent for it. Stop yourself with `session self stop`.
- `parent`, `--spawn-tree` and `--siblings` still reach beyond your children (`--spawn-tree self` selects you, reported `skipped_self`). `--annotation` alone selects across every tree: pair it with a filiation scope.

## Output format

It never fails as a whole: exit 0, `{summary, results}`.

- `results` — one entry per target, keyed by id in selection order: `session_id`, `status` (`stopped` / `rejected` / `failed` / `timeout` / `skipped_*`, `skipped_self` included), `session_known`, `request_uuid`, `provider`, `session_title`, `project_id`, `error`.
- `summary` — `total`, `succeeded` (`status: stopped`), `failed` (every other status), `all_succeeded` (`true` on an empty selection: nothing asked, nothing failed).

```json
{
  "summary": {"total": 2, "succeeded": 1, "failed": 1, "all_succeeded": false},
  "results": {
    "abc123-def456": {"session_id": "abc123-def456", "session_known": true, "status": "stopped", "request_uuid": "…", "provider": "claude_code", "session_title": "Worker A", "project_id": "-home-twidi-dev-myproject", "error": null},
    "me-session-id": {"session_id": "me-session-id", "session_known": true, "status": "skipped_self", "request_uuid": null, "provider": "claude_code", "session_title": "Leader", "project_id": "-home-twidi-dev-myproject", "error": "The calling session is never stopped by `sessions stop`; use `session self stop`."}
  }
}
```

### Exit codes

- `0` — the batch ran, whatever each status.
- `1` — a local refusal, before anything is stopped. A bare call, `--state dead`, `--timeout` ≤ 0 or conflicting scopes are refused even with no backend. An unknown `--workspace` / `--provider` / `--state` value, a malformed `--annotation` or a `self` / `parent` that cannot be resolved are refused too, but checked after the backend check.
- `2` — no backend runs (so the second group of refusals above exits `2` with no backend).

## Examples

```bash
$TWICC sessions stop --spawned-by self
$TWICC sessions stop --state awaiting_user_input
$TWICC sessions stop <MANAGER_ID> --descendants <MANAGER_ID>
$TWICC sessions stop --state starting --state assistant_turn --state awaiting_user_input --state user_turn
```

## Related commands

- `$TWICC session <ID> stop` — one session; the way to stop your own (`session self stop`). Skill: `twicc-session`.
- `$TWICC sessions --spawned-by self` — preview what a filter selects. File: `list.md`.

## How to present results

1. Report `summary`, then each entry whose `status` is not `stopped`, with its `error`.
