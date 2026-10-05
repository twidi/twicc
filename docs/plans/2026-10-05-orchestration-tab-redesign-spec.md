# Orchestration tab redesign

Date: 2026-10-05 — Status: design (spec), not implemented.

The visual reference is a static mock-up, outside the repo: the session artifact `orchestration-redesign/v2.html`
(Dashboard variant, light/dark, width slider). It is a reference for the look only. This document is the contract. Where
the two differ, this document wins (section 12 lists the known differences).

## 1. Problem and goal

The Orchestration tab (`frontend/src/components/orchestration/OrchestrationPanel.vue`) shows two trees: the spawned
sessions (`sessions`) and the subagents of the session (`agents`). After the visual refresh (floating panels, glass,
glide) it is the one page that still looks unfinished:

- The view switch sits at the right of a text toolbar, not at the top of the page.
- Each node is a loose run of text fragments (`OrchestrationNode.vue`, `AgentTreeNode.vue`).
- The Live/Stopped indicator, the "stopped" icon and the `background` badge add noise.
- The sessions tree shows the whole spawn tree from its top-level ancestor. The current session is only highlighted.

**Goal.** One redesign of the tab: a header with the switch, a summary of tiles, and one card per node. Plus three logic
changes: the sessions tree is rooted at the current session, a "Spawned by" line links to the parent, and a subagent
shows its model.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | Visual direction: "Dashboard" (summary tiles, one card per node, a time bar per node). The other explored directions (plain cards, dense rows) are dropped. |
| D2 | Sessions tree rooted at the **current session**. It shows only the current session and its descendants. No backend change. |
| D3 | A "Spawned by" line above the tree when the current session has a parent. The parent's title is the only link. It opens the parent's Orchestration tab. |
| D4 | The Live/Stopped indicator is removed. The note "tree stopped" is removed. Auto-refresh itself does not change (section 7). |
| D5 | The "stopped" icon is removed from both trees. A stopped node shows no process-state icon. |
| D6 | The `background` badge is removed from subagent nodes. The "current" badge is removed from session nodes. |
| D7 | A subagent node shows its model (the model recorded on the subagent's own session). |
| D8 | The agent-settings summary of a session node does not change. It reuses `AgentSettingsSummaryView` and the existing `summaryParts` logic as they are. |
| D9 | Everything cost-related is shown only when the global "Show costs" setting is on (`settingsStore.areCostsShown`), as today. |
| D10 | A session that has a parent but no children still gets the Orchestration tab and the sessions view. It shows its own card and the "Spawned by" line. This is already the behaviour of `hasSpawnRoot` (`SessionView.vue:485`); the redesign must keep it. |
| D11 | No code-comment indicator and no cron indicator in this version (section 13). |

## 3. Page structure

```
┌ header ─────────────────────────────────────────────┐   fixed, does not scroll
│ [Sessions | Subagents]                    [Refresh] │
│ summary tiles                                       │
├ body (scrolls) ─────────────────────────────────────┤
│ Spawned by ↩ <parent title>          (sessions only)│
│ note: hidden sessions                (conditional)  │
│ tree of cards                                       │
└─────────────────────────────────────────────────────┘
```

The header spans the full width of the pane. It keeps the bottom divider the toolbar has today. At pane widths below
480 px the tiles lay out as two columns, and the header is no longer fixed: it scrolls away
with the body, so a short pane keeps room for the cards.

### 3.1 Header, top row

- Left: the view switch. It is the existing `SegmentedControl` (icons `diagram-project` and `robot`, labels "Sessions" and
  "Subagents"). Its behaviour is unchanged: shown only when both views exist (`canSwitchView`). The inactive segment keeps
  its activity indicator slot (`OrchestrationTabActivity`).
- When only one view exists, the switch is replaced by a static label: the same icon and the name of the view. There is
  no title text such as "Orchestration tree" any more.
- Right: the Refresh button (`wa-button`, plain, small, `arrow-rotate-right`). It keeps its `loading`/`disabled` state and
  the existing `refresh()` logic. Below 420 px of pane width, the button and the switch labels show the icon only (the label stays as the accessible name
and tooltip). The shared `SegmentedControl` renders its label as a bare text node today, so it needs a small change to
allow this.

### 3.2 Header, summary tiles

A row of tiles (`grid`, `repeat(auto-fit, minmax(7.5rem, 1fr))`). Each tile is a small bordered card with a small caps
label and a value at normal text size.

| Tile | Sessions view | Subagents view |
|---|---|---|
| 1. Count | Number of spawned sessions (descendants of the current session, current session excluded). A donut of the state buckets. A caption "N running". | Number of subagents (all depths). Donut of working / stopped. Caption "N running". |
| 2. Working / Stopped | Number of nodes in `starting` or `assistant_turn`. Caption "N awaiting" (`awaiting_user_input`). | Number of stopped subagents (label "Stopped"). No caption. |
| 3. Total cost | See 3.2.1. Hidden when costs are hidden (D9). | `agentForestCost` (unchanged). Hidden when costs are hidden. |
| 4. Span, Cumulative duration | Span: duration of the time range of section 6, formatted with `formatDuration`; a dash when no node has a start date. Under it, "Cumulative": the sum of the durations of every descendant session (current session excluded). | Same, the cumulative line summing every subagent. |

**Cumulative duration.** A node's duration is its end minus its start, with the rules of the time bars (section 6):
nodes with no start are skipped; a working node ends at the range end; otherwise the end is the node's end, raised to
its start. Overlapping nodes are simply summed, so the total can exceed the Span. A dash when no node qualifies.

**Node set.** Tiles 1 and 2 and the donut count the **descendants** of the current session (current session excluded),
at any depth. The cost tile (3.2.1) and the Span tile (section 6) include the current session. Subagents: every agent of the tree.

"Running" means the process is not `dead`, as `hasLiveNode` does today. For subagents it means `store.getProcessState(id)`
is truthy, as today.

**Buckets.** Every node falls in exactly one bucket. The bucket gives the donut arc colour and the card's left border
colour (5.1).

| Bucket | States | Colour |
|---|---|---|
| working | `starting`, `assistant_turn` | blue |
| awaiting | `awaiting_user_input` | warning |
| idle | `user_turn`, with or without a background shell | success |
| stopped | `dead` | neutral |

`starting` is in the working bucket and so has the blue border, although its icon (5.2) is warning-coloured. This is the
one place where the border does not take the icon colour. Only non-empty buckets draw an arc.

**Empty tree** (the current session has no children). The tiles still show. The count, "Working" and "N running" are 0
and the donut is one neutral ring. The cost tile shows the current session's own cost (3.2.1). The span tile shows the
current session's own range (section 6).

#### 3.2.1 Total cost, sessions view

The value is the `subtree_total_cost` of the current session's topology node. It includes the current session's own cost.
It therefore equals the Σ shown on the root card (and the root card's own cost when it has no children). This is deliberate and differs from the count, which excludes the
current session. The tile label is "Total cost".

### 3.3 Spawned-by line

Shown in the sessions view when the current session has a parent: `nodesById[sessionId]?.session.spawned_by` is set and
that parent exists in `nodesById`. Otherwise the line is not shown (no placeholder).

- Content: an arrow-up-left icon, the text "Spawned by", the parent's title.
- The title is a `router-link` to the **Orchestration tab** of the parent. It is the only link on the line: no buttons.
- The route is built from `sessionRouteLocation()` (`utils/sessionRoute.js`), with the route name replaced by the tab
  route name (`buildTabRouteName({ isAllProjectsMode, isSessionRoute: true, tab: 'orchestration' })`,
  `utils/granularRoutes.js:70`). `sessionRouteLocation` returns the base session route name only; the implementation must
  either extend it with a `tab` option or override the name at the call site. Params and the `workspace` query stay as
  `sessionRouteLocation` builds them.
- Title fallback: the first 8 characters of the id, as `OrchestrationNode` does today.
- A hidden parent cannot be opened (same rule as hidden nodes): show the title as plain text with the crossed-out eye, no
  link.
- Not shown when the session has no parent, and never in the subagents view.

Only the direct parent is shown. Ancestors above it are not shown.

### 3.4 Hidden-sessions note

The text of the existing note: "Sessions marked [eye-slash] were created hidden by their parent and can't be opened."

- Placed in the body, between the "Spawned by" line and the tree. It is no longer in the header.
- Shown if and only if at least one hidden session is displayed in the sessions view: the parent on the "Spawned by"
  line, or a descendant card. The current session counts like any other displayed session (it renders with the same dimming and eye if it is hidden). Sessions view only.

## 4. The tree

- Rooted at the current session. The frontend finds the current session's node in `topology.tree` (a depth-first search
  for `id === sessionId`) and renders that subtree. The current session is the first card. Its descendants are nested.
- **Fallback.** The tree can lack the current session: `build_topology` builds `nodes` from the tree walk, so a session cut
  out of the tree (parent not loaded, self-parent) is absent from both `tree` and `nodes`. Then the view shows
  "No orchestration data." (no card, no "Spawned by" line, tiles hidden). This is accepted: it only happens on corrupt
  spawn data. `topology.cycle_detected` is not otherwise used.
- Connector geometry (vertical line, elbows, collapse) comes from `treeNode.css`, adapted to cards. The collapse button
  leaves the chevron rail, so the rail column no longer anchors anything. The new anchors: the vertical line runs down
  a fixed inset from the parent card's left edge (about 0.8 rem); each child card has an elbow from that line to its left
  edge, at the middle of its title line; the last child's line stops at its elbow. `OrchestrationNode` and
  `AgentTreeNode` keep sharing the same stylesheet.
- Every node with children has a collapse button at the top right of its card. Expanded by default. The
  button always shows the number of descendants, expanded or collapsed.
- The topology payload is still the whole spawn tree (`GET .../topology/`, unchanged). The re-rooting is frontend-only.
  `topology.total_cost` and `topology.node_count` (whole tree) are no longer used by this tab.
- Empty states keep their current texts ("No orchestration data.", "No subagent."). A current session with no
  children shows its own card, then the line "This session has not spawned any session."

## 5. Node card

One card per node, in both trees. Same component structure and the same CSS, so the two trees do not drift.

### 5.1 Common to both trees

Rows, top to bottom:

1. **Title row.** The title (link, semibold, wraps). After the title, in this order: the crossed-out eye if the session is
   hidden, then the process-state icon (5.2). The collapse button is at the far right.
2. **Settings row** (5.3 for sessions, 5.4 for subagents).
3. **Time bar** (section 6).
4. **Facts row.** At the left: one calendar icon, the start date, an arrow icon, the end date; then the duration with the
   `clock` icon, the turn count with the `comment` icon, the context ring and its percentage. At the right: the cost
   (5.5). The facts wrap onto several lines on a narrow card. The cost stays at the right, on the last line of the facts,
   at every width. It never drops under them.
5. **Annotations row** (5.6), sessions only.

**Left border.** Each card has a 4 px left border. Its colour is the bucket colour of 3.2 (the colour of the node's
process-state icon, except `starting`, see 3.2), so the state reads at a glance. A stopped node has a neutral border at
reduced opacity. The border is the only coloured
decoration: the time bar is not coloured by state.

**Current session card** (first card, sessions view). Same card with a 1 px brand-coloured outline. No "current" badge.

**Dates.** Same `formatDate(..., { smart: true })` as today. One calendar icon replaces the former "Created" /
"Started" and "Finished" words. The end is shown as the text `now` when the node is working (5.2). A node with no end
date shows only the start date. A node with no start date shows no date segment (calendar, dates, duration); its turns,
context ring and cost still show when they have values.

**Duration.** `formatDuration` over the start–end span, as today. Rule change for sessions: it is no longer shown for a
working node (subagents already behave so). It is not shown either when no end date is known or the span is not positive.

**Context ring.** Unchanged (same data, same thresholds, same tooltip). Only the layout changes.

### 5.2 Process-state icon

Sessions keep the icon set of `PROCESS_STATUS` in `OrchestrationNode.vue`, unchanged except that `dead` loses its icon (D5):

| State | Icon | Colour | Animation |
|---|---|---|---|
| `starting` | hourglass-start | warning | none |
| `assistant_turn` | robot | blue | `robot-working` |
| `awaiting_user_input` | hand | warning | pending pulse |
| `user_turn` | check | success | none |
| `user_turn` with a background shell | terminal | success | shell breathing |
| `dead` | **none** (D5) | neutral | none |

No text label is shown next to the icon. The label stays as the tooltip and the accessible name. The existing background
shell tooltip (`backgroundShellsRunningPhrase`) stays.

Subagents keep the working robot (blue, `robot-working`) while running. A stopped subagent shows no icon (D5).

The left border colour (5.1) follows the buckets of 3.2.

### 5.3 Settings row, sessions

`AgentSettingsSummaryView` with the existing `summaryParts` (model with version, effort, thinking, permission, flags) and
`:mark-forced="false"`. Unchanged. The `ProjectBadge` (`:use-directory-for-unnamed="true"`) sits next to it, at the right of
the last line of the row, like the cost in the facts row. The summary takes the remaining width and may wrap onto
several lines. A very long project name is cut, it never pushes the summary away.

### 5.4 Settings row, subagents (D7)

A subagent has no agent settings of its own. The row shows the provider icon and the model, as the model is shown in the
session header (`frontend/src/components/session/detail/SessionHeader.vue:193`: `${family} ${version}` from `session.model`, tooltip "Last used model" there).
The source is the subagent's own `Session.model`: the last model used by that subagent. It can differ from its launcher's
model.

Where the value comes from:

1. The subagent's `Session` row when it is loaded in the store (`store.getSession(id)?.model`), live.
2. Else the `model` field of the agent's entry in the `/subagents/` snapshot. **This needs a backend change** (section 9.1): the snapshot
   does not carry the model today.

No value (a subagent with no model yet, or with no family or version, as `SessionHeader` treats it): the row is omitted.

### 5.5 Cost

Gated by D9. Own cost in regular weight. For a node with children, the cumulative cost follows it, prefixed by `Σ`, in a
quieter style, with the existing tooltip. Same values as today:

- a `null` cost shows the dash `CostDisplay` renders today, on the cards. On the "Total cost" tile this is a deliberate change:
  the toolbar hides a `null` total today, the tile shows the dash. The serializer
  returns `null` for a zero cost too, so a free node shows the dash;
- sessions: `session.total_cost` and the node's `subtree_total_cost`;
- subagents: `agentCost` and `agentSubtreeCost` (`utils/agentTreeMetrics.js`).

### 5.6 Annotations (sessions only)

Annotations are a free-form key/value object. Keys can contain dots.

- Order: entries sorted by key (plain string order), in the tags and in the popover, so the display is stable.
- **One line of tags** under the facts row, one tag per entry: `key value`, key in a quieter weight. A dotted key is shown
  whole (`team.name`). A long value is cut with an ellipsis. Value text: strings as is; numbers and booleans as text;
  `null` as `null`; an object or an array as compact JSON, cut with an ellipsis. The line never wraps: tags that do not
  fit are hidden.
- **A chevron button always follows the tags** as soon as the session has at least one annotation, whether or not some tags
  are hidden. It opens a `wa-popover` (already imported in `main.js`) anchored to it. Light dismiss and Escape close it.
  One popover is open at a time. When tags are hidden, the button also shows how many.
  It sits right after the last displayed tag, and reaches the right edge only when the tags are cut. It is styled like
  the collapse button (plain, quiet colour, hover background).
- The popover lists **all** annotations as a tree. Keys are split on `.`. Entries that share a prefix share a parent
  level. Example: `team.name`, `team.lead`, `run.limits.tokens` give a `team` level with `name` and `lead`, and a `run`
  level, then `limits`, then `tokens`. A level shows its name with a chevron. A leaf shows `key: value`. Same connector
  style as the node tree, thinner.
- Tree order: at each level, siblings are sorted by segment name (plain string order), levels and leaves mixed.
- A key with an empty segment (`a..b`, `.a`, `a.`) is not split: it shows whole as a leaf at the top level (an object value there shows as compact JSON).
- A value that is itself an object expands like extra dotted levels. An array is shown as compact JSON text on its leaf.
- Collision: when both `a` and `a.b` exist, or `a` is an object value that has a `b` and `a.b` also exists, they merge
  into one level `a`. A scalar `a` shows its value next to the name (`a: value`). Two leaves with the same path are both
  listed.
- Session with no annotations: no row and no button.

## 6. Time bar

A thin horizontal bar under the settings row. It shows when the node ran, relative to the whole tree.

- Reference range: from the earliest start to the latest end over the rendered nodes (for sessions: the current session
  and its descendants; for subagents: all agents). Start of a session: `created_at`. Start of a subagent:
  `entry.startedAt`.
- End of a node: the range end (below) for a working node (`starting`, `assistant_turn`); otherwise `last_new_content_at` for a session
  and `stoppedAt ?? agentStoppedAt` for a subagent (the rules `OrchestrationNode` and `AgentTreeNode` use today). With no
  usable end, the end is the start. An end earlier than the start is raised to the start.
- A node with no start date has no bar and is left out of the range. Only its date segment is hidden (5.1).
- **Working** (both trees): a session in `starting` or `assistant_turn`; a subagent that is running (as `isRunning` today).
  A session awaiting input is alive but not working: its end is `last_new_content_at`.
- Range end = the latest of: the ends of the non-working nodes, the starts of all ranged nodes, and `now` when at least one
  node is working. The Span tile uses this same range, so a fully stopped tree shows its real duration.
- A working node's end **is** the range end. This keeps its bar at the right end of the track even when
  a server timestamp is later than the client clock. The text `now` in the dates row (5.1) is only a label.
- Fill: left offset = `(start − rangeStart) / range`, width = `(end − start) / range`. Minimum width 1.5 %. Clamped to the
  track. If `range` is 0, every fill is the full track.
- Colour: one neutral brand colour for every node. It does **not** depend on the state.
- A working node's fill ends at the right end of the track (its end is the range end) and fades out
  toward the right (gradient from the brand colour to a faint tint). The fade runs to the end of the track.
- The track has the page's neutral low-contrast fill.
- Tooltip on the bar: `start → end` text.
- `now` is a reactive timestamp. It is updated at each topology load and by a 30 s timer that runs only while the tab is
  active and at least one **displayed** node is working (the rendered subtree, the current session included). The agent
  view uses the same timer.

The "Span" tile (3.2) uses the same range.

**"Displayed" / "rendered subtree"** (3.4, this section, checks 4 and 14) means every node of the sessions or agents
tree being shown, whatever the collapse state of its branches. Collapse state is local to each card and does not change
the range, the note, the tiles or the timer.

## 7. Behaviour kept unchanged

- Topology fetch, 15 s polling while a node is live and the tab is active, forced fetch on every tab activation, abort of
  a superseded request, silent ticks keeping the last good snapshot. The poll gate (`hasLiveNode`) stays on the **whole**
  payload, not on the re-rooted subtree: a live ancestor or sibling keeps the parent line and the payload fresh. The code
  comments that describe the Live/Stopped indicator are updated; the polling logic is not.
- The Refresh button re-reads the topology (sessions view) or the `/subagents/` snapshot (agents view).
- The agent tree stays live over the WebSocket (agent-link cache), no polling.
- Selected view logic: the user's choice is honoured only when both views exist.
- Loading and error states: spinner "Loading topology…", the danger callout. They move into the body, under the header.
- Hidden nodes cannot be opened: their title is not a link.

## 8. Visual rules

- Cards, tiles, and the popover use the glass/panel tokens of the refreshed site (`--glass-*`, `--panel-*`, `--depth-*`,
  `--glow-*`, `wa-*` palette). No new colour. The brand colour comes from `--wa-color-brand-*`.
- A hidden session's card is rendered at reduced opacity (about 60 %), back to about 90 % on hover. Same content, same
  colours. The crossed-out eye stays.
- Narrow panes: the cost keeps its right place (5.1). The switch and Refresh go icon-only below 420 px. Tiles reflow
  with the grid. Test widths: 320, 480, 760 and 1200 px of pane width. Use a container query on the pane, not a viewport
  media query: the pane width depends on the dock layout.
- Light and dark themes, both.
- Motion: no new motion except what exists (`robot-working`, pending pulse, shell breathing). The time-bar gradient is
  static. Reduced-motion handling stays as in `motion.css`.
- Firefox is the reference browser: no CSS feature without Firefox support.

## 9. Backend changes

### 9.1 Subagent model in the agent links (D7)

`serialize_agent_links` (`src/twicc/core/session_queries.py:304`) already reads each agent's `Session` row, for all agents in
one query (`values_list("id", "slug", "last_stopped_at", "total_cost", "user_message_count", "context_usage")`). Add the
agent's `model` to that query and to every agent entry it returns, as a plain field, not tied to `include_metrics`. It is
a new available datum, like `agent_slug`.

- The value is the serialized form `{raw, family, version}` of the provider helper's `serialize_model`
  (`providers/helpers.py:948`), so the frontend formats it as `SessionHeader.vue` does. The root session's provider helper
  is already held by `build_subagents_state`.
- The share view calls the same function and so receives the field too. The share viewer is not changed and does not
  display it.
- Frontend: `utils/agentLinkIndex.js` (around line 190) carries the field onto the agent entry.
- An agent link created live over the WebSocket may have no `Session` row loaded yet: its model row is omitted until the next
  Refresh. This is accepted.

No other backend change. The topology endpoint is unchanged.

## 10. Files

| File | Change |
|---|---|
| `frontend/src/components/orchestration/OrchestrationPanel.vue` | Header (switch, Refresh, tiles), re-rooting, spawned-by line, hidden note, `now` timer. Remove the toolbar title/meta, Live/Stopped indicator, note in header. |
| `frontend/src/components/orchestration/OrchestrationNode.vue` | Card layout. Remove "current" badge, stopped icon, text dates. |
| `frontend/src/components/orchestration/AgentTreeNode.vue` | Card layout. Remove the `background` badge, stopped icon. Add model row. |
| `frontend/src/components/orchestration/treeNode.css` | Connector geometry adapted to cards. Card, time bar, tile styles may live in a new shared stylesheet next to it. |
| `frontend/src/components/ui/SegmentedControl.vue` | Icon-only mode for narrow widths (label kept as accessible name). |
| `frontend/src/components/orchestration/` (new) | Small shared components to avoid duplicating the two nodes: a summary-tiles component, a time-bar component, an annotations tags + popover component. Names fixed in the implementation plan. |
| `frontend/src/utils/sessionRoute.js` or call site | Route to a session's Orchestration tab (3.3). |
| `frontend/src/utils/agentLinkIndex.js` | `model` on the agent entry. |
| `src/twicc/core/session_queries.py` | `model` in every agent entry (9.1). |
| Tests | Frontend tests for the pure logic (re-rooting, range and fill computation, annotation tree, state buckets). Backend test for 9.1. |

No `CHANGELOG.md` entry is written unless the user asks for it (project rule).

## 11. Acceptance checks

1. Sessions view of a root session: the first card is that session, no "Spawned by" line, descendants nested, no
   ancestor.
2. Sessions view of a child session: "Spawned by <parent title>" above its card. Clicking the title opens the parent's
   Orchestration tab, keeping the `project`/`projects` frame and the workspace. A hidden parent is plain text.
3. Child session with no children: tab and view present, its card and the empty line. The count, Working and "running"
   tiles show 0 with a neutral donut. The cost tile shows the session's own cost.
4. A hidden descendant is dimmed. The note is shown if and only if a hidden session is displayed (a hidden parent on the
   "Spawned by" line counts), and it sits under "Spawned by".
5. The header switch is the first thing on the page. Refresh is at its right. Live/Stopped, the "tree stopped" note,
   "current" and `background` are gone.
6. A stopped node shows no state icon in either tree. Working, awaiting, idle, idle-with-shell show the table 5.2 icons
   with no text. The card's left border has the bucket colour of 3.2: blue for `starting` too (its icon stays
   warning), neutral at reduced opacity for a stopped node.
7. A subagent card shows its model, for a loaded and for a historical (no row) subagent.
8. Costs off: no cost on any card, no "Total cost" tile, no Σ.
9. At 320 px the cost is still at the right of the facts row.
10. A session with annotations shows one line of tags and a chevron button, even when everything fits. Tags that do not fit
    are hidden and counted on the button. The popover shows dotted keys as a tree.
11. A working node's bar has the fade and reaches the right end of the track. A node's bar colour is the same whatever
    its state.
12. Both themes, four widths, both trees.
13. A current session missing from `nodesById` (cut edge) shows "No orchestration data." with no tiles.
14. While only an ancestor or a sibling (not displayed) is live, polling continues. While no displayed node is working, the
    30 s `now` timer does not run.
15. A node with no `created_at` has no bar and no date segment, but still shows its turns, ring and cost. A node whose end is before its start shows the minimum-width
    bar. A node with `null` cost shows the dash.
16. Each process-state icon has a tooltip and an accessible name with the state label, and no visible text.
17. In a fully stopped tree the Span tile equals the latest end minus the earliest start and does not grow over time.
18. Collapsing a branch changes none of the tiles, the range or the hidden note.

## 12. Differences from the mock-up

- The mock-up caps the tags at 2 and shows a `+N` button. The spec shows one line of tags that never wraps, with a chevron
  button that is always present when there are annotations (5.6).
- The mock-up draws a code-comment bubble and a cron clock. Neither is part of this version (section 13).
- Mock-up colours and the stand-in agent-settings summary are illustrative. The real components are reused.

## 13. Out of scope, noted for later

- **Code-comment indicator.** The app shows an icon for code comments before the process-state icon in several places
  (`frontend/src/components/ui/CodeCommentsIndicator.vue`). The orchestration tab does not show it today. It is not added
  here. It applies to sessions only, never to subagents. It is not known whether the topology payload could carry the
  count, or whether it comes from a separate store (`stores/codeComments.js`). To study in a later version.
- **Cron indicator** (clock for a pending cron on a finished turn). Not in the tab today. Not added.
- Ancestors above the direct parent (breadcrumb): explored and not retained.
