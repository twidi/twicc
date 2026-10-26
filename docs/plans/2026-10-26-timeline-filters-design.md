# Timeline Filters — Design

**Status:** reviewed by independent adversarial subagents until PASS (5 rounds). Written with the user 2026-10-26.
**Date:** 2026-10-26
**Scope:** filtering the events of the Timeline tab (global, workspace, project and worktree pages). It adds filter controls to the tab's sticky band, filter parameters to `GET /api/timeline/`, and a per-timeline memory of the filters. It changes nothing in what is recorded, nor in how a row is worded.
**Baseline:** `main` at `24c6f06e1`. The earlier timeline designs (`2026-10-13-session-timeline-design.md`, `2026-10-14-timeline-actor-origin-design.md`, `2026-10-15-timeline-display-design.md`, `2026-10-18-timeline-compute-integration-design.md`) are not amended.

---

## 1. Why

The Timeline tab shows every event of its scope, newest first. Turns dominate a live day. The user cannot yet say which filter will serve most. So the design gives:

- a few ready-made **views** for the probable needs;
- a **panel** of detailed filters for the rest;
- a quiet band that can take more filters later without a redesign.

The user chose this layout ("option B") from a mockup of three proposals (chips in the band; views plus a Filters panel; a token search field).

## 2. Goals, non-goals, anti-goals

**Goals.**

- Narrow a timeline to the events the user is looking for, in one or two clicks.
- Every filter is visible when active and removable in one click.
- Each timeline keeps its own filters while the app lives in the browser tab.
- Filtering stays correct with pagination and with live updates.

**Non-goals.**

- No persistence across a page reload: no `localStorage`, no URL parameter, no synced setting (user decision).
- No saved or user-defined views.
- No counts per filter value.
- No search by session title, no date range, no project filter inside a workspace or global page.
- No timeline in the CLI or the MCP (open subject of the timeline follow-ups).

**Anti-goals.**

- A band that grows a line per filter, or a wall of controls.
- A filter that silently hides rows the user did not ask to hide.
- A filtered list that shows empty or short pages while more matching events exist.

## 3. What the user sees

### 3.1 The band

The sticky band of the Timeline tab (`components/ui/StickyBand.vue`, today a centered "Timeline" label) holds, on one line on desktop:

- the "Timeline" title, at the start;
- the **views**: a segmented control of three exclusive choices, `All`, `Without turns`, `Waits & problems` (section 4.1). Exactly one is selected; `All` by default;
- a **Filters** button. When panel values are checked, it shows their number.

On a narrow screen the title takes its own line; the views and the button share the next one. When that line is still too wide (a phone), the Filters button shows only its icon and its count, and the view labels shorten (`All`, `No turns`, `Waits`, with the full label as their accessible name); if it still does not fit, the button wraps under the views. Nothing scrolls sideways.

The band and everything in it (views, Filters button, open panel, pills) stay in place while the list reloads under them: a filter change never closes the panel, never moves the keyboard focus away from a control that still exists, and never swaps the band for another one. Loading, error and empty states appear below the band, in the list area.

When the focused control disappears because of its own action, the focus goes to a fixed place:

- a pill's remove control: the next pill's remove control, else the previous one, else the Filters button;
- "Clear all" (under the pills or in the no-match state): the Filters button;
- "Only this session" in a session header (the list reloads): when it sets the filter, the remove control of the session pill; when it releases it, the Filters button.

### 3.2 The Filters panel

The button opens a panel anchored to it. Three groups of checkboxes:

| Group | Values |
|---|---|
| Events | the seven families of section 4.2, each with a short hint of what it holds |
| Provider | Claude Code, Codex |
| Done by | the six authors of section 4.3 |

And a **Reset** action that clears the panel's checkboxes. A change applies at once (no Apply button). It is reachable and usable with the keyboard alone.

The panel closes on Escape, on its button, and on a click outside the band. A click on another control of the band (a view, a pill) changes the filter and keeps the panel open; the reload under it never closes it (section 3.1). A click in the list (for example "Only this session") is outside the band and closes it.

### 3.3 Active filter pills

Under the band's line, at most four pills: one per panel group with checked values, listing them ("Events: Processes, TwiCC", "Provider: Codex", "Done by: You"), and one for the session filter ("Session: <title>"). Each pill has a remove control that clears its group (or the session filter). A pill whose values do not fit shows the first ones and a "+N" (the full list in its tooltip); the pills wrap, so the band grows by one line at most on desktop and by two on a phone. A "Clear all" action follows the pills; it clears the panel and the session filter, and sets the view back to `All`. No pill when nothing is checked and no session is filtered. The selected view is not a pill: the segmented control shows it.

### 3.4 Only one session

Every session block header gets a control "Only this session". It restricts the timeline to that session and adds the "Session: <title>" pill. Pressed again (or the pill removed), the timeline shows every session again. Only one session at a time. On a screen without hover the control is always visible; with hover it may appear on hover and on keyboard focus.

The pill shows the session's title as it was when the filter was set (a session with no title shows its short id). It does not depend on the session still being cited by the loaded events.

### 3.5 No match

When a filter is active (a view other than `All`, a panel value or a session) and the first page came back with zero events, the list area says that nothing matches and offers "Clear all", the same action as in section 3.3 (view back to `All` included). This message is distinct from the "no event yet" message of an unfiltered empty timeline. Loading and error states win over it: it only shows after a successful first page.

Some combinations can never match (`Without turns` with only Turns checked; `Waits & problems` with only Sessions checked). They are allowed and lead to this state; the message names the selected view so the cause is visible.

## 4. What matches

### 4.1 Views

A view is a base; the detailed filters and the session filter refine it (logical AND).

| View | Rows |
|---|---|
| `All` | every row |
| `Without turns` | every row except `turn.started` and `turn.ended` |
| `Waits & problems` | `awaiting_input.started`, and every **problem** row (section 4.4) |

`Waits & problems` is a history like the rest of the timeline: it lists past waits too, handled or not. Its label and hint say "waits", never "pending".

### 4.2 Event families

| Family | Kinds |
|---|---|
| Sessions | `session.created`, `title.changed` |
| Processes | `process.started`, `process.ended` |
| Turns | `turn.started`, `turn.ended` |
| Waiting for you | `awaiting_input.started`, `awaiting_input.ended` |
| Settings | `settings.changed` |
| Organization | `archived.*`, `hidden.*`, `muted.*`, `pinned.*` |
| TwiCC | `backend.started`, `backend.ended` |

Every kind of `TimelineKind` belongs to exactly one family. A kind added later must be given a family (a test enforces it).

### 4.3 Done by

"Done by" sorts every row into exactly one author. The rules apply in this order; the first that fits wins:

| # | Value | Rows |
|---|---|---|
| 1 | Unknown | rows dated afterwards (`precision` `derived` or `estimated`): the creation rows of sessions TwiCC did not see being created, the process ends found lost at boot, the turn and wait rows closed at boot, "TwiCC stopped unexpectedly"; `session.created` written by the system (a session created in a terminal, or found by the sync); and every `actor`/`via` pair that is not a known pair (see below) |
| 2 | Automatic | `title.changed` with `data.source = auto` (a title TwiCC generated), and every other `actor = system` row: TwiCC starts and stops, process starts by a cron or at boot, every process end TwiCC detected or caused (shutdown, timeouts, crashes, failed starts, login required, a hybrid CLI that exited), settings TwiCC changed (retired model, hybrid switch) |
| 3 | You | `actor = user` (UI, CLI, API, MCP with a token) |
| 4 | Agent via CLI/MCP | `actor = agent` with `via` `cli` or `mcp_internal`: an agent acting on a session through the TwiCC CLI or MCP |
| 5 | External MCP | `actor = external` with `via = mcp_external` |
| 6 | The session's agent | `actor = agent` without a `via`: turns, waits, and the titles the provider wrote ("from Claude Code", "from Codex") |

The known pairs are: `user` with `ui`, `cli`, `rpc`, `mcp_internal` or no `via`; `agent` with `cli`, `mcp_internal` or no `via`; `external` with `mcp_external`; `system` with no `via`. Any other pair (possible from a forged drop file) is Unknown: the row cannot label it either and shows the raw actor in its tooltip.

Every row has an author, so checking every value is the same as checking none.

What the row shows agrees with this: rows 2 to 5 carry the author label or the "automatic" tag, except where the wording already says it (TwiCC rows, a known process end reason) and except your own UI actions, which carry no label (the common case). Rows 1 and 6 carry no author.

A turn or wait row closed by a process end takes the origin of that end (`twicc/agent/timeline_hooks.py`): "You" when you stopped the process, "Agent via CLI/MCP" when an agent did, "Automatic" when TwiCC did.

### 4.4 Problems

A **problem** is a row that says something went wrong:

- `process.ended` with a reason whose severity is warning or danger in the row wording (`PROCESS_ENDED_REASONS` in `frontend/src/utils/timeline/describeEvent.js`): start timed out, no activity, cron restart timed out, lost, login required, failed to start, crashed;
- `backend.ended` with `data.clean = false` (TwiCC stopped unexpectedly).

`awaiting_input.started` is shown with the warning colour but is not a problem: it is a wait. A `settings.changed` that opens the permission mode is shown with the warning colour but is not a problem either. The server decides which rows are problems; a test pins that the server's list and the frontend's severity agree for every known reason.

### 4.5 Combining

- Inside one panel group: any checked value matches (OR).
- Between groups, the view and the session filter: all must match (AND).
- A group with nothing checked does not filter.
- Each group sorts every row into one value or keeps it: Events and Done by give every row exactly one value; Provider keeps every row without a provider (instance rows, a session row whose provider no source could name) whatever is checked, and only hides rows of an unchecked provider. So checking more values never hides a row, and checking every value of a group is the same as checking none. The UI shows what is checked as it is (pills, count); the server applies the same rule, so an API caller asking for every provider gets the unfiltered answer.
- The keeping of rows without a provider extends today's rule for instance rows to session rows with no provider (today a provider filter drops those).
- The session filter hides the instance rows.

## 5. Memory

- Each timeline has its own filters: the global page, each workspace page, each project page, each worktree page (a worktree has its own page, so its own filters, distinct from its main project's).
- The memory is the app's own state: it lives while the app lives in the browser tab and is lost on a reload. It does not depend on a component staying mounted: leaving a timeline for a session, another tab or another scope, then coming back, shows the same view, the same checked panel values and the same session filter. The panel itself is closed when the user leaves the timeline, and stays closed on return.
- A session filter stays until removed, whatever happens to the session. Its rows keep the project they were written with, so a session moved to another project still shows its old rows in this timeline. A deleted session has no rows left: from its next first page (a filter change, a scope change, a reload), the timeline shows the no-match state. Rows already loaded stay on screen until then, as for any deleted session today.

## 6. Pagination and live updates

- The server applies the filters. The first page, every older page (`before`) and every live fetch (`after_id`) carry the same filter.
- A page holds up to `limit` matching events; `has_more` speaks of matching events.
- Changing any filter (view, panel, session) restarts the list from the first page, like a scope change does today, and keeps the scroll at the top.
- A live event that does not match is not shown and does not move the view. The live cursor still advances (the `timeline_changed` signal carries the table's highest id, not the scope's).
- The `truncated` reload of a live fetch keeps the filters.

## 7. Server contract

- `GET /api/timeline/` accepts the filters of sections 4.1 to 4.4 as query parameters, next to the existing `project_ids`, `provider`, `limit`, `before` and `after_id`. Several providers can be asked at once.
- An unknown value of any filter parameter is a 400, like an unknown provider today. No filter parameter means no filter: an old client keeps its behaviour.
- The session filter matches `session_id` exactly.
- Speed, measured by the builder on a synthetic table of one million events spread over 200 projects, in three scopes: global; a workspace of 20 projects holding about 30 % of the events; one project holding about 10 %. The mix: 45 % turns, 15 % waits, 20 % session and title rows, 10 % process rows, 10 % the rest; authors 60 % the session's agent, 25 % automatic, 8 % unknown, 6 % you, 1 % agent via CLI/MCP, and no external MCP row at all; providers 70 % Claude Code, 30 % Codex, 0.1 % none; problems 0.5 %.
  - A first page and a `before` page answer in under 100 ms for a view alone, a single panel value alone, and the session filter alone, including a value with no row at all.
  - Under 500 ms for a view combined with one value per panel group and the session filter.
  - Deeper combinations are best effort.
  - The builder chooses the indexes; an index added for a filter is justified by that measure.

## 8. Decisions (who made them)

| Decision | By |
|---|---|
| Layout B: views + Filters panel + pills | user |
| Filters kept per timeline, in memory only, lost on reload | user |
| "Agent via CLI/MCP" (an agent acting through the TwiCC CLI or MCP) is a value apart from "The session's agent" | my call (user delegated) |
| The three views and their content; `Waits & problems` rather than "Needs attention" (it lists past waits) | my call |
| "Done by" sorts every row into one of six authors, "Unknown" included, so the group never hides a row by accident | my call |
| A provider filter keeps the rows without a provider | my call |
| One pill per group, not per value | my call |
| No "Problems only" switch in the panel: `Waits & problems` with the Processes and TwiCC families checked gives the problems only | my call |
| Combinations that can never match are allowed and lead to the no-match state | my call |
| Filters applied by the server | my call (forced by pagination) |
| A change applies at once, no Apply button | my call |
| One session at a time in the session filter | my call |

## 9. Left to the builder

- The parameter names and encodings of section 7.
- The Web Awesome components of the panel and the views (`wa-dropdown`, `wa-popover`, `wa-radio-group`…), checked against `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`.
- Where the per-timeline memory lives (a Pinia store keyed by the page id is the obvious place), and how the feed controller (`frontend/src/utils/timeline/feed.js`) receives the filter.
- The exact hints of the families, the wording of the empty state.
- Indexes, after measuring (section 7).

## 10. Later

Ideas left out on purpose, to reconsider once the filters are used: a view without the rows dated afterwards (the creation rows of old sessions; today "Done by" without "Unknown" hides them), a search by session title, a date jump, a project filter inside a workspace page, user-defined views, counts per family.
