# Timeline Display — Design

**Status:** draft, review round 3 fixes applied, not committed. Decisions taken with the owner 2026-10-15.
**Date:** 2026-10-15
**Scope:** a "Timeline" page that shows the recorded `TimelineEvent`s, newest first, one line per event, updated in real time. This is a delta on the implemented timeline. It does not edit the earlier timeline documents.
**Baseline:** the system as implemented on branch `feature/session-timeline` (migrations up to `0158`). Nothing reads `TimelineEvent` today: no view, serializer, WebSocket frame, CLI, MCP tool or frontend code.

---

## 1. Goal and scope

**Goal.** The owner sees what happened, when, to which session and by whom, across the whole instance, a workspace, a project or a worktree.

**In scope.**

- A Timeline tab, first tab of `ProjectDetailPanel`, and the default tab of every scope page. Stats moves to its own route.
- A read endpoint `GET /api/timeline/` and a light real-time signal.
- A `provider` column on `TimelineEvent`, filled by every writer and by the backfill.
- The wording, icon and severity of the 19 event kinds.

**Out of scope.** Section 13.

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Scope page** | The detail page of the global scope ("All Projects"), a workspace, a project, or a worktree. All use `ProjectDetailPanel`. |
| **Band** | The sticky strip under the page header. It follows the provider band of Stats. |
| **Row** | One line of the list: an event, or a day separator. |
| **Group** | Consecutive event rows of the same session, in the displayed order. |
| **Group head** | The first event row of a group. It carries the session title. |
| **Instance event** | A `backend.started` or `backend.ended` event. It has no session and no project. |
| **Side table** | The map of sessions cited by a response (section 7.3). |
| **Signal** | The light WebSocket message that tells clients a new event exists (section 9). |
| **Estimated time** | An `occurred_at` whose `precision` is `estimated` or `derived`. |

## 3. The page

### 3.1 Tab and routes

- **Timeline** is a new tab, first position, of `ProjectDetailPanel`. It is the default tab of the global, workspace, project and worktree scopes.
- Stats keeps its content and moves to a `/stats` route per scope. Project and worktree: `/project/:projectId/stats`. Global and workspace: the `projects` family, with `?workspace=` for a workspace, as the other `projects-*` tab routes do today.
- The bare routes (`/project/:projectId`, `/projects`, `/projects?workspace=`) now land on Timeline. Old links to the bare form therefore open Timeline, not Stats. Accepted.
- The tab strip becomes: Timeline, Stats, Files, Git (only with a git root), Terminal. The active tab is derived from the route name, as today.
- Tab switching stays router-driven. Each scope page keeps its own list state while it is kept alive (`ProjectDetailPanel` is kept alive per scope).

### 3.2 Header

The page header is the one of Stats, unchanged: the navigation list of workspaces and projects, with its existing max-height rule. In compact-height mode (viewport at most 60rem high) the page header omits the navigation list and a copy of it sits at the top of the tab content, as in Stats. The Timeline tab does the same.

### 3.3 Band

- A sticky band in the style of the provider band of Stats (`.provider-filter glass-sticky` look, including the reduced-transparency and reduce-effects fallbacks).
- It carries the label "Timeline" and nothing else. No filter control exists in this version. The band is the place where filters come later, including the provider filter.

### 3.4 Scroll

The Timeline tab reproduces what the user sees in Stats, mode by mode.

- **Normal mode:** the page header stays fixed. The band stays fixed above the list. Only the list scrolls.
- **Compact-height mode:** the copy of the navigation list scrolls away with the content. The band stays at the top while the list scrolls.
- The list uses the existing simple virtual scroller (`VirtualScroller.vue`), not the chat's complex usage. It is newest first, so it must not pin to the bottom.
- If the scroller needs its own scroll container, the observable behaviour above must still hold. The plan decides how.

- **Page size:** about 50 events per request (section 7).
- **Row height:** the scroller gets a default height estimate per row type, then measures.
- **Loading:** automatic when the visible range nears the end of the loaded list. No "load more" button. A failed load shows an inline error with a Retry action. All list fetches go through one queue (9.2); loading is not re-entrant.
- **States:** a loading state on first load, an empty state ("No events yet"), and no end-of-list marker.

## 4. Scope rules

The scope content follows the same project grouping as Stats.

| Page | Events shown | `project_ids` sent |
|---|---|---|
| Global | Every event, including events whose project is NULL (orphan rows, instance events) | none |
| Workspace | Events of every project of the workspace and of their worktrees, archived projects included, as Stats does | the workspace's full project list (`getAllProjectIds`); never empty (see below) |
| Project | Events of the project and of its worktrees | the project's scope (`getProjectScopeIds`) |
| Worktree | Events of that worktree project only (a worktree is a project) | that project id |

- **Empty or unresolved scope.** An empty `project_ids` means the global scope on the server. A workspace or project scope therefore never sends an empty list. A workspace with no project shows the empty state and sends no request. While the projects or the workspace are not loaded yet, the page shows its loading state, sends no request, and loads when the list resolves. The same holds for a project page whose project is unknown.
- **Membership change.** The resolved set of project ids can change while the page is open: a project or worktree is added, removed or archived, or the workspace changes. The client compares the set, not the scope name. On any change it discards the loaded list, the live cursor and the pending fetch queue, and reloads the first page for the new set. Every `after_id` fetch uses the current set. A response whose scope set no longer matches the current one is ignored.
- **Unknown project.** A project page whose project id does not exist shows a "Project not found" state with a link back to All Projects, and sends no request.
- Archived projects: Stats uses the archived-blind list for a workspace. The Timeline uses the same list. The "show archived projects" setting does not apply to it.
- Instance events appear on every scope page, at their time position (section 7.4). Which query returns them depends on scope and provider (table in 7.4).
- Rows with a session but a NULL project (a `lost` process without a Session row) appear on the global page only. They never match a project list.
- Project name display is in section 5.3.

## 5. Row model

### 5.1 Event row anatomy

From left to right:

1. An icon with the severity colour of the kind (section 6).
2. The wording, with the author label (section 6.3) in a muted style after it.
3. The time, on the right: clock time in the browser locale (hour and minute), with the full date and time with seconds in a tooltip. An estimated time carries a leading "~" and a lighter style (section 6.4).

The group head also carries, before the wording (same line, left side), the session identity: the provider icon, then the session title (section 5.2), then on workspace and global pages the project name (section 5.3).

### 5.2 Grouping, separators, indentation

- Rows are ordered `occurred_at DESC, id DESC`. Never by `recorded_at`.
- The group head shows the session identity once. The following rows of the same session are slightly indented and show no identity.
- A **day separator** labels the day of the rows **below** it. The list starts with a separator for the newest displayed day. Another separator sits between the last row of day D and the first row of day D-1. A day is the user's local calendar day (browser time zone), computed from `occurred_at`. It uses `DaySeparator.vue` with `formatDaySeparatorLabel` and `formatDayKey` of `utils/date.js`, the same component and format as the session chat. A separator **breaks** the group: the row after it is a group head again.
- An instance event has no session. It is never indented and never a group head with an identity. It breaks the group of the surrounding rows (a session row after it is a group head).
- Grouping, indentation and separators are **derived** from the ordered loaded list at render time. They are never stored. A live insertion or a page append recomputes them, so a row's head status can change when a row is inserted before it.
- Separator and group-head computation never mutate the rows. The scroller sees stable keys (`event-<id>`, `daysep-<dayKey>`).

### 5.3 Session identity and project name

- **Provider icon:** `ProviderIcon.vue` before the title, as in the session header. The provider comes from the side table (section 7.3). Fallback order: the event's own `provider` column, then `data.provider`, then no icon.
- **Title:** the side-table title (it already carries the pending-title override). Fallback: an untitled session, or an id with no side-table entry, shows the session id. Draft sessions write no timeline row, so no draft fallback is needed.
- **Links:** the session title links to the session and keeps the current frame (the `projects-*` prefix and `?workspace=`), through `sessionRouteLocation`. The project name links to the project. A session with no Session row (purged, or not created yet) shows plain text, not a link. So does a hidden session (5.4). **The link rule applies to every mention of a session**: the group head, the "by <session>" author label, and the `spawned_by` parent in `session.created`. A subagent session, cited only by an author label, is plain text too (it has no openable route as a top-level session).
- **Project name** (shown with the group head):
  - Global and workspace pages: shown, from the project stores, with the worktree marker when the project is a worktree (`ProjectBadge.vue`).
  - Project page: not shown for rows of the project itself. Rows that come from one of its worktrees carry the worktree marker.
  - Worktree page: never shown.
- The project of a row is the event's `project_id`; the side-table project is the fallback when the event has none.

### 5.4 Hidden and archived sessions

Their events are shown (D5). The markers are on group heads only, next to the title, and reflect the state of the session in the latest response received, not its state at event time. A later response (first page, `before`, `after_id`) overwrites the client's side-table entry for a session (title, provider, flags), so markers always follow the latest response.

- **Archived:** the title keeps its link. The archived marker of the session list is shown (`box-archive`, tooltip "Archived").
- **Hidden:** the title is plain text, not a link, because a hidden session cannot be opened from a link: the session REST lookup returns 404 for it and the client drops hidden sessions from its store. A small hidden marker (`eye-slash`, tooltip "Hidden") sits beside the title. The session list has no hidden marker, so this one is new.
- A session both hidden and archived shows both markers and no link.
- Subagent sessions write no timeline rows, so none appears as a group head. A subagent could still be cited in an agent author label; it then shows as plain text with its title or short id.

### 5.5 Click behaviour

Only the session title, the project name and the session names inside author labels are links. The row itself is not clickable. Tooltips carry the technical details (section 6.4).

## 6. Event rendering catalogue

### 6.1 The 19 kinds

Wording uses plain English. `<…>` is data. Icons are Font Awesome Free solid names, all verified to exist in the free set. Severity maps to the existing colour tokens: neutral (default text), success, warning, danger.

| Kind | Wording | Icon | Severity |
|---|---|---|---|
| `session.created` | "Session created". `origin=external`: "Session created from a terminal". `origin=sync`: "Session imported". `spawned_by` set and no agent author label (6.3): "Session created by <parent session>" with the parent as a link. When the author label already names the parent session, the wording stays "Session created" and the parent is not repeated | `plus` | neutral |
| `title.changed` | `Renamed "<old>" -> "<new>"`; `Titled "<new>"` when old is null. Source tag after the text, small and muted: `user` none, `auto` "automatic", `agent` "by the agent" | `pencil` | neutral |
| `settings.changed` | "Model: Sonnet -> Opus, Effort: High -> Max" (rules in 6.2) | `sliders` | neutral; warning when the new permission mode is `bypassPermissions` or `yolo` |
| `process.started` | "Process started" or "Process resumed" (`cause=resume`); suffix "(hybrid CLI)" when `hybrid` | `play` | success |
| `process.ended` | per the reason table in 6.1.1 | `power-off` | per 6.1.1 |
| `turn.started` | "Turn started" | `robot` (neutral colour; the blue process-state accent is not used in the timeline) | neutral |
| `turn.ended` | "Turn ended" + background-work badge (6.5) | `circle-check` (success) | success |
| `awaiting_input.started` | by `request_type`, which wins over `tool_name` (table 6.1.2) | `hand` | warning |
| `awaiting_input.ended` | same precedence, past tense (table 6.1.2) | `circle-check` | neutral |
| `archived.started` / `.ended` | "Archived" / "Unarchived" | `box-archive` / `box-open` | neutral |
| `hidden.started` / `.ended` | "Hidden" / "Unhidden" | `eye-slash` / `eye` | neutral |
| `muted.started` / `.ended` | "Muted" / "Unmuted" | `bell-slash` / `bell` | neutral |
| `pinned.started` | "Pinned (project / workspace / all projects)" by `scope`; null or unknown scope: "Pinned" | `thumbtack` | neutral |
| `pinned.ended` | "Unpinned (was <scope>)"; null or unknown scope: "Unpinned" | `thumbtack-slash` | neutral |
| `backend.started` | "TwiCC started v<version>"; null version: "TwiCC started"; "after a machine restart" when `machine_restarted` is true (null or false: nothing) | `server` | neutral |
| `backend.ended` | `clean=true`: "TwiCC stopped". `clean=false`: "TwiCC stopped unexpectedly" (estimated) | `power-off` | neutral; danger when unclean |

An unknown kind (a future kind on an older frontend) renders as its raw kind string with a neutral generic icon (`circle-info`) and no detail. The page never fails on unknown data.

#### 6.1.3 Fallbacks for missing or unknown fields

Every field has a fallback, so a malformed or future payload never breaks a row.

| Field | Fallback |
|---|---|
| `session.created` `origin` unknown | "Session created" |
| `process.started` `cause` unknown | "Process started" |
| `process.ended` `error` null on reason `error` | "Process crashed" without detail |
| `settings.changed` unknown reason | no prefix |
| `settings.changed` empty or unknown `changes` | "Settings changed" |
| `settings.changed` unknown field name | the raw name; raw values |
| `title.changed` `new` null or empty | "Title cleared" |
| `title.changed` unknown `source` | no tag |
| `turn.ended` unknown `background_work` shape | plain "Turn ended" |
| `backend.ended` `clean` null | "TwiCC stopped" |
| `data` missing or not an object | treated as empty |
| `title.changed` `old` equal to the empty string | treated as null ("Titled ...") |
| `precision` unknown | treated as `exact`: no "~", normal author-label rules |

#### 6.1.2 `awaiting_input` wording

`request_type` decides the wording. `tool_name` is shown, truncated near 40 characters, only where the table says so.

| `request_type` | started | ended |
|---|---|---|
| `tool_approval` | "Waiting for approval (<tool_name>)"; without a tool name "Waiting for approval" | "Approval handled (<tool_name>)" / "Approval handled" |
| `ask_user_question` (also the hybrid AskUserQuestion) | "Question for you" (`tool_name` ignored) | "Question answered" |
| `hybrid_terminal` | "Waiting in the terminal (<tool_name>)" / "Waiting in the terminal" | "Terminal input handled" |
| null or unknown | "Waiting for input (<tool_name>)" / "Waiting for input" | "Input handled" |

#### 6.1.1 `process.ended` reasons

The reason set is open. An unknown reason reads "Process stopped" with the raw reason in the tooltip, neutral.

| Reason | Wording | Severity |
|---|---|---|
| `manual` | "Process stopped" | neutral |
| `force` | "Process killed" | neutral |
| `archived` | "Process stopped (session archived)" | neutral |
| `shutdown` | "Process stopped (TwiCC shutdown)" | neutral |
| `apply-settings` | "Process restarted to apply settings" | neutral |
| `switch-hybrid` | "Process stopped (switch to hybrid)" | neutral |
| `timeout_user_turn` | "Process stopped (idle timeout)" | neutral |
| `cli-exit` | "Hybrid CLI exited" | neutral |
| `timeout_starting` | "Process stopped: start timed out" | warning |
| `timeout_assistant_turn` | "Process stopped: no activity" | warning |
| `cron_restart_timeout` | "Cron restart timed out" | warning |
| `lost` | "Process lost (found dead at boot)", estimated | warning |
| `auth_required` | "Process stopped: login required" | danger |
| `startup-failed` | "Process failed to start" | danger |
| `error` | "Process crashed" + the first line of `data.error` | danger |

### 6.2 `settings.changed` diff

- Format: `<Field>: <old> -> <new>`, joined by ", ". Labels come from the session's provider (`getFieldLabel`, `getChoiceLabel`, `getModelLabel` in `providers/baseHelpers.js`). When the provider is unknown, raw values are shown.
- Booleans read "on" / "off". A null value reads "default".
- At most 3 changes are shown, then "+N more". The tooltip lists all.
- Reason prefixes: `model_retired` "Model upgraded (retired): …", `set_mode` "Permission mode set: …", `switch-hybrid` "Hybrid switch: …".

### 6.3 Author label

The label follows the wording, muted, as "by …". The session named in an agent label uses the provider icon and follows the link rules of 5.3 (link, or plain text for hidden, missing or subagent sessions); its title comes from the side table through `actor_ref`, with the first 8 characters of the id as fallback.

| `actor` | `via` | Label |
|---|---|---|
| `user` | `ui`, or NULL | none |
| `user` | `cli` | "by you (CLI)" |
| `user` | `mcp_internal` | "by you (MCP)" |
| `user` | `rpc` | "by you (API)" |
| `agent` | `cli` | "by <session> (CLI)" |
| `agent` | `mcp_internal` | "by <session> (MCP)" |
| `external` | `mcp_external` | "by <actor_name> (external MCP)"; fallback "by an external MCP client" |
| `system` | NULL | "automatic", only on rows whose `precision` is `exact`, and not on `backend.*` or `session.created`, whose wording already says it |
| `agent` | NULL | none (the wording says it: "Turn started") |
| any other combination | | no label; `actor` and `via` raw in the tooltip |

**Precision rule.** No author label is shown on a row whose `precision` is not `exact`. A backfilled flag row or a `lost` row has actor `system`, but a user set the flag or the process, so "automatic" would be false. This applies to every actor.

NULL `via` is not an origin: it means "unknown" for old rows or "automatic". The label is chosen from `actor` and `via` together and never inferred from NULL alone.

### 6.4 Estimated marker, tooltips, content rules

- **Estimated or derived time:** a "~" before the clock time and a lighter style. Tooltip for `estimated`: "Estimated: it happened at or before this time". Tooltip for `derived`: "Derived from the session's creation time". For `process.ended` `lost`, `data.upper_bound` adds "detected at boot HH:MM".
- **Tooltips** carry: the full timestamp with seconds; `precision`; `reason` when set; `via`, `actor_ref` and `actor_name` when set; for process events `twicc_pid`; for `backend.*` `pid`, `version`, `boot_id`; for `process.ended` the raw reason. IDs and technical values appear only in tooltips.
- **Escaping:** every string from data (`title.changed` old and new, `actor_name`, `tool_name`, `error`, `version`) is rendered as text, never as HTML or Markdown.
- **Truncation:** titles in `title.changed` and `actor_name` are cut near 80 characters, the full text in the tooltip. `tool_name` is cut near 40. `data.error` shows its first line in monospace, with an expand control for the rest.
- **Unmatched intervals:** a started row without an ended row, and the reverse, are rendered as they are. Each row reads alone.

### 6.5 Background-work badge

`turn.ended` shows a warning-outline badge when `data.background_work` is not null. It lists the non-zero keys in this order: "N subagents", "N shells", "N monitors", "wake-up at HH:MM", "goal". It reuses `pluralize` and `formatClockTime` of `utils/backgroundWork.js`. The tooltip shows the exact dict. A null dict reads plain "Turn ended". Re-emissions of `turn.ended` are not collapsed.

## 7. API contract

### 7.1 Request

`GET /api/timeline/`, owner-only (the global API auth applies).

| Parameter | Meaning |
|---|---|
| `project_ids` | Comma-separated project ids. Empty or missing means global. Empty items are dropped. Same convention as `daily_activity`. |
| `provider` | Optional provider key. Unknown value: 400. The UI has no control for it yet. |
| `limit` | Page size, an integer from 1 to 100. Default 50. A larger value is capped to 100. Zero, a negative or a non-integer value returns 400. |
| `before` | Keyset cursor `<occurred_at>,<id>`: return events strictly older than `(occurred_at, id)`. The timestamp is the exact string the server returned (7.2), unchanged, URL-encoded by the client (no raw `+`). Mutually exclusive with `after_id`. |
| `after_id` | Return events with `id` greater than this value (live fetch, 7.4). `limit` does not apply: the cap is 500 rows. |

Bad parameters (unparseable `before`, non-integer `after_id`, `limit`, both `before` and `after_id`, unknown provider) return 400 `{"error": "..."}`.

### 7.2 Response

```
{ "events": [...], "has_more": bool, "truncated": bool, "latest_id": int|null, "sessions": { "<session_id>": {...} } }
```

- `events`: ordered `occurred_at DESC, id DESC` (an `after_id` response is the exception: `id` ascending, see 7.4). Each event: `id`, `kind`, `occurred_at` (UTC ISO-8601 with microseconds and an explicit `Z`), `precision`, `actor`, `via`, `actor_ref`, `actor_name`, `reason`, `data`, `session_id`, `project_id`, `provider`. `recorded_at`, `dedupe_key` and `caused_by` are not sent.
- `has_more`: true when more events exist older than the last returned one. Computed with one extra row (limit + 1), as the sessions page does. It is not used for `after_id` responses: the client keeps the list's own `has_more`.
- `truncated`: only for `after_id` (7.4).
- **Timestamps:** `occurred_at` is returned in UTC with full microsecond precision, in one fixed format with a `Z`. The default JSON encoder of the existing views truncates to milliseconds, so the endpoint produces this format itself. The cursor round-trips without loss: the row-value comparison runs on the stored microsecond value, so equal-millisecond rows are neither skipped nor repeated.
- `latest_id`: the highest `TimelineEvent.id` in the table, regardless of scope, read **first** in the request, **before** the event queries. A row committed meanwhile may be in `events` with an id above `latest_id`, which is harmless; a row can never be below `latest_id` and missing from a result that claims it.
- An empty feed gives `latest_id` null. The client treats a null live cursor as 0 for `after_id`, so the first event ever recorded is fetched.
- **The live cursor** is the highest id the client is sure to have received from the head of the feed. Only a first-page response (no cursor) and an `after_id` response may advance it, to the response's `latest_id` when the response is not `truncated`. A `before` response never advances it.

### 7.3 Side table

`sessions` holds one entry per session id cited by the events, by `actor_ref` when the actor is an agent, and by `data.spawned_by`. Entry: `title`, `project_id`, `provider`, `hidden`, `archived`. No `type`: subagent sessions write no rows, so the field has no use.

- It is built by one query on `Session` for at most the ids of the page. It uses a slim serializer, not `serialize_session`.
- `title` applies the pending-title override (`get_pending_title`), as `serialize_session` does. An untitled session gives `null`.
- A session id with no Session row (purged, or not created yet) has **no entry**. The client falls back to the id and to the event's own provider.
- Project names and worktree information come from the frontend stores, not from the response.

### 7.4 Ordering, keyset, live fetch

- **Keyset comparison:** a row-value comparison `(occurred_at, id) < (cursor)`. It is **not** the expanded `OR` form, and the query has no `OR project_id IS NULL`. Both forms were measured: the expanded form with `IN` on busy projects is up to 100 times slower.
- **Equal timestamps:** the id breaks ties, so no event is skipped or repeated between pages.
- **Instance events.** The main query returns rows by scope and provider. Instance events (`session_id IS NULL`) have a NULL provider and NULL project, so the main query returns them only on the global scope without a provider. Every other combination runs a second query for them (same keyset, no provider filter). The two result sets are merged by `(occurred_at, id)` descending, deduplicated by id, and cut at the limit.

| Scope | `provider` | Main query | Second (instance) query |
|---|---|---|---|
| Global | none | all rows, instance events included | no |
| Global | set | rows of that provider (excludes `session_id IS NULL`) | yes |
| Scoped | none | rows of the project ids | yes |
| Scoped | set | rows of the project ids and provider | yes |

  Instance events concern every provider, so they are included in all four cases, at their time position on the page that covers it.
- **`after_id`:** returns events newer than the id, ordered by id ascending, ignoring `limit`. The same query table applies. The cap of 500 applies to the merged set (main plus instance). Over the cap, the response sets `truncated: true` and `events` holds the first 500 rows after `after_id`; the client does not use those rows. It discards its whole list and reloads the first page.
- The page result for `before` and for the first page (no cursor) has the same shape.
- **`after_id` response shape.** `events` are ordered by `id` ascending, not by `occurred_at`. `has_more` is ignored. `latest_id` is read first, as for every response. The client sorts the rows into its list by the insertion rules (9.2).

### 7.5 Errors

Auth failures follow the global middleware. A database error returns 500 and the client shows the Retry state. The view never writes.

## 8. Data model changes

- **Column:** `TimelineEvent.provider`, nullable `CharField(50)`. NULL for instance events, and for the rare row where no source gives a provider. It stores the `Provider` value (`claude_code`, `codex`).
- **Indexes:** `(occurred_at, id)` for the global feed, and `(provider, occurred_at, id)` for the provider filter. Existing indexes stay.
- **Migration:** a new migration after `0158`, adding the column and the two indexes only. No data migration: the table is empty in production, and the owner will squash the whole timeline migration set later. The owner applies migrations himself.
- **Who fills it:** a single central resolution in the recording service, so no writer needs to know the rule. The order is: an explicit value from the caller (free sources are preferred: the agent's provider, the loaded session, `ProcessRun.provider`); then the event payload when it carries `provider` (`session.created`, `process.started`; `process.ended` inherits it); then a bounded in-memory cache of session id to provider (a session's provider never changes and ids are never reused); then one primary-key query on `Session`. A session with no row and no hint gets NULL. Recording still never raises: a failed resolution leaves NULL.
- **Every write path** carries the column: the single-event path, the bulk path (including the bulk archive), the batch fallback, the boot reconciliation, and the backfill. The shared batch insert reads the model metadata and picks the column up; the paths that build rows with explicit fields must be extended.
- **Backfill:** writes `provider` from the loaded session. Rows written before this migration, if any, stay NULL; no re-fill is planned.
- **Guard:** a test fails when a row with a `session_id` has a NULL `provider` after any writer ran (the session existing).

## 9. Live updates

### 9.1 Server signal

- One light WebSocket message of a new type, `timeline_changed`, carrying `latest_id`. It is sent to the existing `updates` group like other broadcasts. It carries no session id, no project, no content, so it leaks nothing and is not filtered by project.
- **Throttled:** at most about 3 signals per second, trailing. A burst produces one signal after the burst. A sustained stream of events produces signals at the throttle rate.
- **After commit:** the signal is requested through the commit hook of the writing transaction, never before the row is committed, and and the emission itself (the read of the latest id and the send) never runs under the DB write lock.
- **When:** after a successful single write (not on a dedupe hit or a failure) and after a non-empty bulk insert, including the bulk archive.
- **Backfill:** silent per session. At most one signal at the end of a run that wrote rows.
- **No loop, no problem:** with no running event loop (tests, CLI, boot before startup) the request is a no-op. A closed loop at shutdown is caught. A failed send is logged, never raised.
- The signal never blocks or fails the writer. All timeline writes happen in the main process.

### 9.2 Client rules

- **One queue.** A single queue serializes all list fetches: the first page, `before` pages and `after_id` fetches. A live fetch waits for a pending first-page or `before` load, then applies the insertion rules below against the **updated** tail and `has_more`. Signals arriving meanwhile are coalesced into one `after_id` fetch after the load. This closes a race: a row committed after a `before` query read, but older than the new tail, would otherwise be returned by a concurrent `after_id` fetch, dropped as "older than the tail", and lost.
- **Null cursor.** With no live cursor yet, a signal that arrives while the first page is in flight is deferred and replayed after it. After the first page of an empty feed the cursor is null and is treated as 0. The same applies after a reconnect.
- On a signal whose `latest_id` is not greater than the live cursor (7.2), the client does nothing.
- Otherwise it queues an `after_id` fetch with the live cursor and the current scope set. A scope that resolves to nothing sends no request (section 4). A response for a scope set that no longer matches is ignored (section 4).
- **Pagination cursor.** The `before` cursor is always the `(occurred_at, id)` of the last row of the last fetched page. It is never derived from the tail of the list after live insertions.
- **Insertion rules.** Fetched rows are inserted by `(occurred_at, id)` descending, not appended. A row already present (same id) is ignored. While `has_more` is true, a live row that sorts after (older than) the loaded tail is **not** inserted: the page that reaches it brings it. While `has_more` is false, it is inserted at its sorted place. A back-dated row (a `lost` end, a backfilled row) that sorts inside the loaded range is inserted where its time puts it.
- `truncated: true`: discard the list and the cursor, reload the first page.
- **First connect and tab return.** `twicc:ws-reconnected` fires only on a reconnection, not on the first connect. The client therefore also queues an `after_id` fetch when the socket first reaches OPEN after the page has loaded, and whenever the Timeline tab becomes active again (kept-alive page). These fetches go through the same queue and coalesce.
- **Reconnect:** on the existing `twicc:ws-reconnected` event the client queues an `after_id` fetch from its live cursor (null counts as 0), or reloads the first page if it has no list. `resync_required` is not handled here: the existing logic reloads the whole page (`utils/resync.js`).
- A kept-alive page that is not visible updates its data the same way and renders nothing.
- **Scroll:** when the user is at the top, new rows appear at the top. When the user scrolled down, the visible content must not move: rows inserted above the viewport must not shift what is on screen. No "new events" banner in this version.
- Day separators and group heads are recomputed from the new list (section 5.2).

### 9.3 Tests' implications

Backend tests need the commit-hook helper to run `on_commit` callbacks. A test must show: one signal per burst and signals at the throttle rate under a sustained stream, none from the backfill rows of a session, one at the end of a backfill run that wrote rows, none on a dedupe hit.

## 10. Interaction with existing behaviour

| Area | Change |
|---|---|
| Routing | New `/stats` routes per scope. Bare routes show Timeline. |
| Scope memory | The scope-memory sets (`HOME_NAMES`, `PROJECT_NAMES`, `ALL_PROJECTS_NAMES` in `scopeMemory.js`) and the redirect guard that sends a bare home to the last remembered place must know the new route names. A remembered Stats location is restored as Stats. |
| Granular routes, tab routes | `granularRoutes.js` and `tabRoutes.js` learn the stats route. `railScopeNavigation` keeps pointing to the bare route, which now is Timeline. |
| Shortcuts | The fixed map of Alt+Shift+digit becomes: 1 Timeline, 2 Stats, 3 Files, 4 Git, 5 Terminal. Without a git root, key 4 does nothing and key 5 is still Terminal. The settings-panel text ("Project home tabs") and the command palette are updated to match. |
| Command palette | The "Switch to Stats Tab" command stays and targets `/stats`. A "Switch to Timeline Tab" command is added with the same gating. |
| Old links | Skills, external notifications and the provider helpers emit bare project URLs. They open Timeline. No backend change is needed (SPA catch-all). |
| Existing frontend tests | The tests that pin the Stats layout must be reviewed and updated where the markup moved: tab bar markup (`main-content-height.test.js`), the provider band (`panel-veil.test.js`, `glass.test.js`), compact header (`compact-header-panel.test.js`, `compactHeight.test.js`), `home-motion.test.js`, `stats-motion.test.js`, `projectActivityUi.test.js`, `railScopeNavigation.test.js`. |
| Back end | A new view and URL, a new WebSocket message type, a model column and a migration. Existing views and the `daily_activity` endpoint are unchanged. |

**Unchanged guarantees.**

- Recording never raises: a failure in the new column resolution or in the signal leaves the event written (or logged), never the caller broken.
- Ordering semantics are unchanged: `(occurred_at, id)`. A back-dated row can have a higher id than rows after it in time.
- Nothing in the display path writes: the endpoint and the page only read. The signal is the only new write-side effect and carries no data.
- The `ProjectDetailPanel` keep-alive and the existing circular-import rules hold: the new code imports the router lazily.

## 11. Decisions

| # | Decision |
|---|---|
| D1 | Timeline is a new first tab of `ProjectDetailPanel` and the default tab of global, workspace, project and worktree. Stats moves to `/stats` per scope. Old bare links land on Timeline. Shortcuts, palette, scope memory, settings text updated. |
| D2 | Same page header as Stats. A sticky band in the style of the provider band, label "Timeline" only, no filter control. Scroll behaviour is Stats' per mode (D27). |
| D3 | Newest first, one line per event, time on the right. The group head carries the provider icon, the session title and the project name per 5.3. A day separator (same component and format as the chat) breaks the group. Following rows of a group are slightly indented. |
| D4 | Project name on workspace and global pages; not on project and worktree pages. On a project page, rows from its worktrees carry the worktree marker. Scope content equals Stats' grouping. |
| D5 | Everything is shown: all 19 kinds, hidden and archived sessions (lightly flagged), instance events on every scope page. Filters and default hiding come later. |
| D6 | Existing simple virtual scroller, about 50 events per page, a default row-height estimate, automatic loading, no "load more" button. |
| D7 | Real time by a light signal, then a client fetch of `after_id`. Rows inserted by `(occurred_at, id)`, not appended. The view does not jump. |
| D8 | A nullable `provider` column, filled by every writer and the backfill through one central resolution; indexes `(occurred_at, id)` and `(provider, occurred_at, id)`; no data migration. The API accepts `provider`; the UI has no control. The session provider icon comes from the side table. |
| D9 | Any Font Awesome Free icon. Strings are hardcoded English. |
| D10 | (ruling) `GET /api/timeline/` with `project_ids`, `provider`, `limit` (50, max 100), `before=<iso>,<id>`, `after_id` (capped at 500 with `truncated`). Response: events, `has_more`, side table of sessions cited by events, `actor_ref` and `spawned_by`. |
| D11 | (ruling) Row-value keyset comparison, no `OR project_id IS NULL`. Instance events by a second query merged by `(occurred_at, id)`. |
| D12 | (ruling) A session id with no Session row has no side-table entry; the UI falls back to the id and the event's provider. |
| D13 | (ruling) The endpoint is owner-only. Exposure in public shares is out of scope. |
| D14 | (ruling) Backfill is silent per session, with at most one signal at the end of a run that wrote rows. |
| D15 | (ruling; made precise by D31) With a provider selected, instance events are still included. |
| D16 | (ruling) Estimated and derived times show a "~" and a lighter style. Tooltips carry via, actor_ref, boot_id, pid, version, upper_bound. |
| D17 | (ruling) `turn.ended` re-emissions are not collapsed. The background-work badge is shown. |
| D18 | (D4 refined) The Timeline workspace scope uses the archived-blind project list, as Stats does. The "show archived projects" setting does not apply. This corrects the earlier analysis, which said it did (`ProjectDetailPanel.vue`, `statsProjectIds`: "No archived filter"). |
| D19 | (amended by D22) The response carries `latest_id` so a client advances its live cursor even when its scope has no new row. |
| D20 | The author label is chosen from `actor` and `via` together (6.3). It is omitted where the wording already says it. |
| D21 | (after review round 1) A non-global scope that resolves to zero project ids, or not yet loaded, sends no request: empty state or loading state. The client never sends an empty `project_ids` for a workspace or project scope. |
| D22 | (after review round 1; amends D19) `latest_id` is read before the event queries. Only a first-page or `after_id` response advances the live cursor. A `before` response never does. |
| D23 | (after review round 1) The `before` cursor is the last row of the last fetched page. A live row older than the loaded tail is not inserted while `has_more` is true. |
| D24 | (after review round 1; refined by D31: also runs on the global scope with a provider) The instance-events query runs only for scoped requests; the merge deduplicates by id. |
| D25 | (after review round 1) `occurred_at` is returned in UTC with microseconds and a `Z`; the cursor carries it unchanged. |
| D26 | (after review round 1) A day separator labels the rows below it, on the user's local calendar days. The list starts with one. |
| D27 | (after review round 1; corrects D2's description) Scroll behaviour is Stats' per mode: normal mode, header and band fixed, list scrolls; compact mode, the navigation copy scrolls away, the band stays. |
| D28 | (after review round 1) A hidden session's title is plain text with a hidden marker; an archived one keeps its link and the archived marker. The side table drops `type`. |
| D29 | (after review round 1) `request_type` wins over `tool_name` for `awaiting_input` wording. The parent link of `session.created` is shown only when no agent author label applies. |
| D30 | (after review round 1) `resync_required` is left to the existing page reload. The `after_id` refetch hooks on `twicc:ws-reconnected` only. `derived` times have their own tooltip. |
| D31 | (after review round 2; refines D15, D24) Instance events: no second query on the global scope without a provider; a second query in the other three cases; merge and deduplication by id always. |
| D32 | (after review round 2) One queue serializes all list fetches; a live fetch applies the insertion rules against the updated tail and `has_more`; signals during a load coalesce. |
| D33 | (after review round 2; amends D22) An empty feed gives `latest_id` null; a null live cursor counts as 0 for `after_id`; signals during the first page are deferred and replayed. |
| D34 | (after review round 2) A change of the resolved project-id set discards list, cursor and queue and reloads the first page; mismatched responses are ignored. |
| D35 | (after review round 2) Fixed shortcut map 1 Timeline, 2 Stats, 3 Files, 4 Git, 5 Terminal; key 4 is a no-op without a git root. |
| D36 | (after review round 2) Every field of every kind has a fallback (6.1.3). |
| D37 | (after review round 2; refines D20) No author label on a row whose precision is not `exact`. |
| D38 | (after review round 2) The link/plain-text rules of 5.3 apply to every session mention. A later response overwrites the client's side-table entry. |
| D39 | (after review round 2) The 500 cap and `truncated` apply to the merged set, ordered by id ascending; on `truncated` the rows are not used. `limit` is an integer 1..100, else 400. |
| D40 | (after review round 3) An `after_id` fetch is also queued at the first OPEN of the socket after load and at each return to the tab. |
| D41 | (after review round 3) `after_id` responses are ordered by id ascending; the client ignores their `has_more`. |
| D42 | (after review round 3) `turn.started` uses the neutral icon colour. Unknown `precision` counts as `exact`. An empty `old` title counts as null. An unknown project shows "Project not found". |

## 12. Known limits

- **First boot after the backfill.** The backfill writes thousands of derived `session.created` rows, all dated at the sessions' creation. They flood the global timeline, and large workspaces, until filters exist. Accepted.
- **Unmatched intervals.** A started row without an ended row, or the reverse, appears as it is. Nothing pairs rows in this version.
- **Hybrid adoption.** At every boot the adoption of a hybrid CLI reads as "Process stopped" then "Process started (hybrid)" although the CLI never died.
- **Back-dated rows.** A `lost` end and backfilled rows sit before rows with a higher id. A client sees them inserted into the middle of the list, possibly above rows it already shows.
- **Group heads can change.** A live insertion may turn a head into a non-head row, or the reverse, and a back-dated row can split a group.
- **Orphan rows.** A purged session's rows are deleted with it, but a page loaded earlier may still cite it. Rows may also exist for a session whose Session row does not exist yet. The UI falls back to the id.
- **Estimated times.** They are lower bounds or approximations, not facts.
- **Volume.** The global timeline shows volume: `turn.*` rows are tens to hundreds per day per active session. Accepted until filters exist.
- **NULL provider.** A row whose provider could not be resolved appears with the id fallback and, with a provider filter, not at all.
- **Multi-project cost.** A multi-project scope (a workspace, a project with worktrees) plans as an index search plus a temporary sort. Each page reads every older event of those projects. Single-project, global and provider-only queries are fully index-ordered. The "uses an index" check does not bound this cost. Accepted for now.
- **Throttle.** A signal can arrive up to about 0.3 s after the commit.

## 13. Out of scope

- The filter UI and default hiding of kinds (the band is its place).
- Any exposure in public shares, including the new fields (`via`, `actor_ref`, `actor_name`, `error`, `boot_id`).
- CLI and MCP read access to the timeline.
- i18n.
- A per-session timeline tab.
- Retention and pruning.
- Pairing of open intervals, folding of `pinned` scope-change pairs, collapsing re-emissions.
- A "new events" banner.
- Tier 2 events.
- Showing the token name of an API token (the names live in a file store with no listing).

## 14. Verification expectations

The future plan's tests must demonstrate these behaviours.

**Backend**

- The endpoint returns events newest first, with `id` breaking ties for equal `occurred_at`. Paging by `before` on a set with many equal timestamps returns every row exactly once.
- The global scope includes rows with a NULL project; a `project_ids` scope excludes them. A project with worktrees returns both when the ids are sent.
- Instance events: present exactly once in all four cases of the 7.4 table (global without provider; global with provider; scoped without; scoped with), at their time position, merged correctly across a page boundary, never duplicated.
- `provider` filters correctly. An unknown provider, a bad `before`, a bad `limit`, `before` with `after_id` return 400. `limit` over 100 is capped.
- `after_id` returns only newer ids in ascending id order, includes instance events as the table says, and is `truncated` past 500 rows of the merged set, with the first 500 rows in `events`. An empty feed gives `latest_id` null. `limit` of 0, a negative or a non-integer value returns 400. `latest_id` is correct.
- The side table contains every session cited by events, by `actor_ref` and by `spawned_by`; has the pending-title override; omits an id with no Session row.
- `EXPLAIN QUERY PLAN` shows the main query uses an index (project or global) with the row-value form and no multi-index OR.
- The `provider` column is filled by every writer: single path, bulk path, bulk archive, boot reconciliation, the title and settings recorders, process hooks, `session.created`, and the backfill. A guard fails on a NULL provider with an existing session. A session with no row and no hint gets NULL without an error. The central resolution uses the explicit value, the payload, the cache, then one query, in that order.
- The migration adds the column and the two indexes only. Existing rows are unchanged.
- The signal: one message per burst, sent after commit and not before, nothing on a dedupe hit or a failed insert, none per backfilled session, one at the end of a backfill run that wrote rows, a no-op without a loop, and no exception on a failed send. A writer succeeds when the signal fails.

**Frontend**

- Routing: the bare scope routes show Timeline; the `/stats` routes show Stats on each scope; the tab strip and active tab match the route; scope memory restores a remembered Stats location; the command palette and shortcuts target the new numbering; the settings panel text matches.
- The list shows events newest first with the time on the right; the group head shows the identity once; following rows are indented; a day separator breaks the group; the project name rules hold per scope (workspace, global, project, worktree, worktree rows on a project page).
- Rendering: every kind and every `process.ended` reason has its wording, icon and severity; an unknown kind and an unknown reason do not break the page; author labels match section 6.3 for every actor and via combination; estimated times show "~"; strings from data are escaped and truncated.
- Virtual scrolling: the list loads the next page near the end, is not re-entrant, shows Retry on failure, and stops at `has_more=false`. Bottom pinning is off.
- Live updates: a signal with a known latest id does nothing; a new id triggers one fetch; concurrent signals cause one follow-up fetch; fetched rows are inserted by `(occurred_at, id)`; a duplicate id is ignored; the pagination cursor stays the last fetched row after live insertions; a back-dated row lands in the right place; `truncated` reloads the first page; a `twicc:ws-reconnected` event triggers an `after_id` refetch (and `resync_required` triggers nothing timeline-specific); the view does not jump when the user is scrolled down.
- Day separators follow local-time day boundaries, label the rows below them, and start the list.
- Hidden and archived rules: link or plain text, markers on group heads only. Fallbacks for a purged or missing session and for an untitled session.
- A live insertion splits or merges groups correctly. A live row older than the loaded tail is not inserted while `has_more` is true.
- An empty or unloaded workspace shows the empty or loading state and sends no request.
- The queue race: a row committed after a `before` query read but older than the new tail is not lost when an `after_id` fetch runs at the same time.
- A signal during the first-page request is replayed after it. A null cursor on an empty feed fetches the first event ever.
- A change of the resolved project-id set (project added, removed or archived) reloads the first page; a response for the old set is ignored.
- The tab shortcuts match the fixed map, key 4 is a no-op without a git root, and the settings-panel text and the palette match.
- Author labels: none on rows whose precision is not `exact` (backfilled flag rows, `lost` rows). The link rules hold for the group head, the author label and `spawned_by`. A later response overwrites a side-table entry. Every fallback in 6.1.3 renders.
- The `latest_id` and live-cursor rule: a `before` response never advances it; a row committed between the two reads is not lost.
- The cursor round-trips microseconds, URL encoding included, with rows equal to the millisecond.
- Instance events are not duplicated on the global scope.
- The `awaiting_input` wording for every `request_type` and `tool_name` combination, and the single appearance of the parent session in `session.created`.
- Compact-height mode: the navigation copy scrolls away and the band stays at the top; in normal mode header and band stay fixed.
- The loading state, the empty state and the "Project not found" state render and send no request where required.
- The `settings.changed` diff: at most 3 changes then "+N more", booleans on/off, null as "default", reason prefixes.
- The background-work badge: key order, pluralisation, wake-up time, tooltip with the exact dict.
- The `derived` and `estimated` tooltips differ.
- Local-day boundaries hold at midnight and across a DST change.
- The first connect (socket reaching OPEN after load) and a return to the tab each trigger one coalesced `after_id` fetch.
- An `after_id` response has `events` in ascending id order, and the client ignores its `has_more`.
- Existing tests that pin the Stats markup are updated and pass.
- No component imports the router statically (circular-import rule).
