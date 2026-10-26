# Timeline CLI, RPC, and MCP specification

Date: 2026-10-26
Status: Adversarial specification review complete; accepted for later planning

## 1. Purpose

Expose the recorded timeline through one read-only command.
Support CLI users, RPC clients, and internal and external MCP clients.
Let callers select events through the same session filters as `sessions`.

This document specifies observable behavior. It does not prescribe query construction, modules, indexes, or implementation tasks.

## 2. Decisions and boundaries

The discussion selects one global `timeline` command with optional scope arguments.
Nested `project timeline` and `workspace timeline` commands are not part of this feature.
They would expose several entry points for the same read operation.

Use cursor pagination. New events at the front must not shift older-page boundaries.
Keep the CLI default page size of 20 events.

Exclude hidden sessions by default. Include archived sessions by default.
Filiation filters implicitly include hidden sessions, matching `sessions`.

The response details and edge-case policies below are specification proposals, not additional Human decisions.
They make the contract reviewable before implementation.

### Out of scope

- Changing the Timeline UI or its REST contract.
- Adding new timeline event kinds or backfilling missing history.
- Historical reconstruction of annotations, visibility, workspace membership, or process state.
- Event-family, author, and view filters from the separate Timeline UI filter work.
- Live subscriptions, polling cursors, watch mode, or mutation commands.
- An implementation plan or product implementation.

## 3. Public entry points

| Surface | Entry point |
|---|---|
| CLI | `twicc timeline` |
| RPC | `POST /rpc/timeline` |
| Internal MCP | Tool `timeline` |
| External MCP | Tool `timeline` |

All surfaces expose the same selection, ordering, pagination, and output contract.
RPC and MCP use the existing argument encoding and error conventions.
The command is read-only and eligible for existing read-only batch operations.

## 4. Scope

| Argument | Selected scope |
|---|---|
| None | All projects |
| `--project PROJECT` | The project and its own worktrees |
| `--project WORKTREE` | Only that worktree |
| `--workspace WORKSPACE_ID` | Workspace members and each member's worktrees |
| `--session SESSION_ID` | Only events whose `session_id` equals that session |

`--project`, `--workspace`, and `--session` are mutually exclusive.
The default scope never depends on the current working directory.
Project arguments accept the same IDs and paths as existing project filters.
Existing remote restrictions on relative paths still apply.

Project expansion is downward only. A worktree never selects its parent or sibling worktrees.
Workspace expansion deduplicates members and worktrees.
Archived projects and worktrees remain in the scope.

Unknown explicit project, workspace, or session scopes produce an error.
A known empty workspace returns an empty page, never the global timeline.
A known session without recorded events returns an empty page.

`--session` selects the target session, not its descendants or events it causes on other sessions.
It accepts a known subagent session ID as well as a root session ID.
The existing session-reference conventions apply, including contextual `self` and `parent` where supported.
Unresolvable contextual references fail; they never broaden the scope.

## 5. Session filters

Filter semantics use current session metadata, not metadata at the event timestamp.
Resolve the scope and filters before ordering and paginating events.
Different filter categories intersect.

| Argument | Meaning |
|---|---|
| `--provider PROVIDER` | Select sessions of that provider |
| `--annotation EXPRESSION` | Existing typed annotation matching; repeatable and AND-combined |
| `--spawned-by SESSION` | Direct children of the referenced session |
| `--spawn-tree SESSION` | The entire spawn tree containing the referenced session, including its root |
| `--descendants SESSION` | All proper descendants; exclude the referenced session |
| `--siblings SESSION` | Other sessions with the same spawner; exclude the target and sibling descendants |
| `--state STATE` | Current process state; repeatable and OR-combined |
| `--active` | Any live process state, including idle `user_turn` |
| `--include-hidden` | Include visible and hidden sessions |
| `--only-hidden` | Select only hidden sessions |
| `--include-archived` | Accepted for filter compatibility; archived sessions are already included |

The filiation arguments are mutually exclusive, as in `sessions`.
`--state` and `--active` are mutually exclusive.
`--include-hidden` and `--only-hidden` are mutually exclusive.
Hidden visibility matches `sessions`: hidden sessions are excluded without filiation or visibility flags.
Every filiation filter includes hidden sessions automatically; `--only-hidden` narrows that selection to hidden sessions.
An exact-session scope alone still requires `--include-hidden` or `--only-hidden` for a hidden target.

Annotation operators, typed values, dotted paths, and missing-key behavior match `sessions`.
Supported process states and contextual filiation references also match `sessions`.
Do not introduce a second annotation language or a second definition of a spawn tree.
Reuse existing session-filter logic, including filiation resolvers, annotation matching, and process-state selection.
Extract shared logic where necessary instead of duplicating it for the timeline.
Select matching sessions before filtering their events; never paginate sessions before paginating events.
Unknown filiation targets retain existing filter behavior: no matching sessions, rather than a global fallback.

Session scope can intersect with filiation or metadata filters.
A conflict returns an empty page. It does not override either selection.

### Timeline-specific differences from `sessions`

- Archived sessions remain eligible without a flag.
- Timeline eligibility does not require a first user message or a completed content-indexing step.
- Global, project, and workspace scopes do not impose a session-type restriction on recorded events.
- Explicit subagent scope remains supported; this does not expand filiation to provider-native subagent trees.

The existing `sessions` command keeps its behavior.
Output projections such as `--full` and legacy pagination switches are not session filters and are not copied.

### Current-state consequences

`--active` returns the full recorded history of sessions active at request time.
It does not select events that occurred during active periods.
If no backend manages a session, process-state matching follows existing `sessions` behavior.

Changing annotations, hidden state, or workspace membership can change later pages.
Cursor pagination does not create a metadata snapshot.

## 6. Instance events and unavailable session metadata

Instance events have no target session.
Include them in global, project, and workspace timelines without explicit session-selection filters.
This preserves the contextual instance events in the current Timeline UI.

Exclude instance events whenever any of these is supplied:

- `--session`;
- annotation or filiation filters;
- provider, state, or active filters;
- `--only-hidden`.

`--include-hidden` and the compatibility flag `--include-archived` do not remove instance events.
An empty workspace returns no events, including no instance events.
An explicit session-selection filter matching no sessions returns no events, including no instance events.
Without such filters, instance events remain eligible even when no session events match.
This includes a known project with no eligible session history, or an instance with only instance events.

For retained events whose target session metadata is absent, preserve recorded history in unfiltered global/project/workspace reads.
Use recorded `project_id` for those scope matches.
Such events cannot satisfy explicit session metadata filters and are excluded when those filters require the missing metadata.
Their visibility is unknown; the hidden-session default does not erase them solely because metadata is absent.

Visibility filtering concerns the event's target session, not its actor.
An event on a visible session remains eligible when a hidden session caused it.
Preserve recorded actor references in the event; visibility is a display selection, not an authorization boundary.

## 7. Ordering and pagination

Order events by `occurred_at DESC`, then `id DESC`.
The ID breaks timestamp ties.

| Argument | Contract |
|---|---|
| `--limit N` | Positive page size; default 20; maximum 100 |
| `--before CURSOR` | Return events strictly older than the cursor boundary |

Reject invalid limits and malformed cursors with normal argument errors.
Do not support `--offset` for this command.
Do not copy the REST live-update argument `after_id` into this command.

The cursor preserves the exact timestamp and ID of the last returned event.
Treat it as an opaque value supplied by the response.
Clients keep the same selection arguments when requesting the next page.
Changing selection arguments starts a new traversal without `--before`.
The cursor is a boundary, not a stored selection or a frozen snapshot.

New events newer than the boundary do not shift subsequent pages.
Late-recorded events with older occurrence times may appear on subsequent pages.
Late-recorded events newer than an already passed boundary require a new traversal.
No exactly-once snapshot guarantee is promised across metadata changes or late history publication.

## 8. Response

Use the CLI listing name `items` for serialized timeline events.
Preserve the existing timeline event fields and timestamp precision.
Do not expose internal deduplication or write-control fields.

```json
{
  "items": [],
  "pagination": {
    "limit": 20,
    "has_more": false,
    "next_cursor": null
  },
  "sessions": {}
}
```

`has_more` describes matching events older than this page at request time.
When `has_more` is true, `next_cursor` advances past the last returned event.
Otherwise, `next_cursor` is null.
An empty page always has `has_more: false` and `next_cursor: null`.

There is no `offset` or `total` field. This is an explicit cursor-pagination contract, not an offset envelope.
The `sessions` side table provides the existing compact metadata for sessions cited by returned events.
It does not contain unrelated sessions. Missing session metadata is omitted without dropping eligible events.
Actor sessions may appear in that side table even when they are hidden; this does not select their events.

### Current labels

Each item also carries these nullable fields:

- `session_title`: the target session's current title, including a pending title.
- `project_name`: the current name of the event's recorded project, when assigned.
- `project_directory`: the recorded project's current directory, when known.

These fields do not change the recorded event payload in `data`.
Missing session metadata leaves `session_title` null without dropping eligible history.
Instance events have null session and project labels.
The `sessions` table also carries `project_name` and `project_directory` for each cited session's current project.
If a session moved, its current project can differ from an older event's recorded project.

With workspace scope, the response adds `workspace: {id, name}`, even on an empty page.
Other scopes omit `workspace`. Reuse the workspace definition read during scope resolution.

Enrichment uses only the returned page and its cited sessions.
Group session and project metadata reads; never issue a metadata query per event or per session.
Reuse metadata already loaded for explicit project and session scopes.

## 9. Examples

```bash
twicc timeline
twicc timeline --project /path/to/project --include-hidden
twicc timeline --workspace WORKSPACE_ID --annotation 'team=backend'
twicc timeline --project /path/to/project --spawn-tree SESSION_ID
twicc timeline --session SESSION_ID
twicc timeline --descendants SESSION_ID --only-hidden
twicc timeline --state assistant_turn --state awaiting_user_input
twicc timeline --limit 20 --before 'CURSOR_FROM_PREVIOUS_RESPONSE'
```

Equivalent RPC/MCP arguments use the existing snake_case convention:

```json
{
  "workspace": "WORKSPACE_ID",
  "annotation": ["team=backend"],
  "include_hidden": true,
  "limit": 20
}
```

## 10. Acceptance criteria

1. CLI, RPC, and both MCP contexts return equivalent results for equivalent explicit arguments.
2. Project/worktree/workspace scopes follow the expansion rules, including archived members and deduplication.
3. Empty scopes and empty filter intersections return empty pages without broadening the request.
4. Hidden-session events are absent by default, including exact-session reads. Filiation filters include them as in `sessions`.
5. `--include-hidden` and `--only-hidden` produce their specified visibility selections.
6. Archived-session history remains available by default.
7. All session selection filters listed above work, with documented timeline-specific defaults.
8. Session filters use current metadata and apply before the pagination boundary and page size.
9. Session scope selects only the target's events, including a known subagent when explicitly selected.
10. Instance-event inclusion follows section 6 for every scope and filter combination.
11. Equal timestamps and newly inserted recent events cause no cursor-boundary duplicates or skipped older existing matches.
12. Invalid arguments produce ordinary errors, never broader results.
13. Every page provides the specified event payload, pagination fields, and compact cited-session metadata.
14. Existing CLI commands and the UI/REST timeline retain their existing contracts.

## 11. Specification review record

Two independent internal reviewers examine the specification, followed by a correction and re-review round.
Their mandate excludes implementation mechanisms and task-level details.

| Finding | Resolution |
|---|---|
| Broad scopes incorrectly imply root-session-only eligibility | Remove the implicit type restriction; preserve matching recorded events |
| Empty session selection conflicts with baseline instance-event inclusion | Distinguish explicit empty filters from absent eligible session history |

Both reviewers report no remaining substantive specification findings after correction.
The Human requests committing the specification and defers implementation planning.
Product implementation remains outside the authorized work.
