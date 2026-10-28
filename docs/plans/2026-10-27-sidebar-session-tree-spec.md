# Sidebar session tree — spec

**Status:** draft for review. Spec level: behavior and rules, not implementation.

## 1. Goal

Add an option **"Show orchestrated sessions as tree"** to the sessions sidebar controls (next to "Show archived sessions", "Compact view", …). Off by default. When on, a session spawned by another visible session is shown nested under it instead of at the first level.

The hard part is not the nesting. It is the placement: a root can be idle while its descendants are active, pinned or recent, and the sidebar is split into sections (Pinned, Active, Last 24 hours, …) and filtered by project or workspace. This spec fixes those rules.

## 2. Vocabulary

- **Parent edge:** `Session.spawned_by`, already in the session payload. It is the only edge the tree uses. `parent_session_id` (provider sub-agents) is out of scope: those sessions never appear in the sidebar.
- **Candidate set:** the sessions of the `natural` block that `computeSidebarSessionBlocks` already returns for the current scope (single project with its worktrees, workspace, or all projects), after the archived filters. Hidden sessions are not in it, since the sidebar never lists them. In tree mode the set also includes every loaded in-scope session linked to a member by a `spawned_by` chain (ancestor or descendant), even when it lies beyond the pagination window of the flat list (§11). Such a session is not also shown in a cross-filter block, nor as "Selected": a session added by this extension always nests (ephemeral sessions and drafts excepted, §5.6).
- **Node, root, child:** a session of the candidate set, placed in the tree. A root has no parent in the tree.

## 3. Non-goals

- Changing what `spawned_by` means or how it is set.
- Showing hidden sessions, or sub-agents, in the sidebar.
- A tree in the command palette's "Go to Session…" picker, in the Orchestration tab, or in any other list. The one exception is the icon rail's active-session buttons (§14).
- Drag and drop re-parenting. The edge is immutable.
- Turning the option on by default.

## 4. Anti-goals

The feature fails, even if it technically works, when:

- A session disappears from the sidebar that a flat list would show.
- An active or pinned descendant is buried in an old section, so the user must expand things to learn it is running. (Pinned descendants and recent descendant activity are not signalled on a collapsed node, see §12. Unread descendants are handled by expansion and the cumulative indicator of a collapsed node only, as unread never drives section placement within the flat list's natural block either; the existing "Active elsewhere" block, which includes unread sessions, is unchanged.)
- Rows reorder continuously while sessions are writing.
- The rules of the flat mode (option off) change in any way.

## 5. Tree construction

1. Start from the candidate set (scope and archived filters already applied).
2. The **parent of a node** is its **nearest ancestor present in the candidate set**, following `spawned_by`. If the direct parent is filtered out but a grandparent is present, the grandparent adopts the node. The chain is followed only through ancestors the client can obtain: a hidden or sub-agent ancestor ends it (§11), and the node becomes a root.
3. A node with **no present ancestor is a root**. It carries a small hint "↰ from *<parent title>*" when its direct parent exists but is outside the candidate set (other project or workspace, archived, not loaded). When the parent is unknown, no hint is shown.
4. Consequence: a session never disappears because of the tree. Filters apply first, the tree is built on what is left.
5. Cycles cannot occur (`spawned_by` is immutable and set at creation). A defensive implementation treats a detected cycle as roots.
6. **Ephemeral sessions and drafts** stay flat roots, placed by their own state exactly as today (launched ephemeral sessions in the Ephemeral section, drafts in the date buckets). They are never nested, never lift an ancestor, and never act as a parent. *(Author's call: they have no meaningful spawn history in the sidebar.)*

## 6. Effective state

Every node has an **effective state**, computed from itself and all its descendants in the tree:

| Aspect | Effective value |
|---|---|
| Pinned | true if the node or any descendant is pinned |
| Active | true if the node or any descendant has a running process |
| Active key | the **highest `started_at`** among the active nodes of the subtree |
| Date key | the **highest `mtime`** of the subtree |

"Active" and "pinned" keep their current meaning in the flat list, including the exclusion rules that exist today (for example a muted session still counts as active through its process).

## 7. Placement of roots in sections

A root is classified by its **effective state**, with the same section order as today: Ephemeral, Pinned, Active, then date buckets (Last 24 hours, older cutoffs).

- Idle root with an active child → section **Active**.
- Old root with a child modified yesterday → section **Last 24 hours**.
- Root with a pinned descendant → section **Pinned**.
- A root appears **once**, in one section. Its idle descendants stay under it, whatever section the root is in.

The section separators keep their meaning: one separator before the first visible root of each section.

## 8. Ordering

- **Roots within a section** and **children under the same parent** use the existing comparator (`sessionSortComparator`): pinned first, then active, then date. Only the inputs change: the effective values of §6.
- Among active nodes the key is the `started_at` of the most recently started active node of the subtree. It never changes while processes run. A row moves only when a process starts or stops in the subtree (a discrete event), as in the flat list today. Writing sessions do not make rows move.
- Among inactive nodes the key is the subtree's highest `mtime`.
- The existing entrance animation and the archive-exit animation apply to tree rows. Rows moving between positions are not animated, as in the flat list. Expanding or collapsing a node needs no animation.

## 9. Expanded and collapsed state

- A node with children shows a **toggle arrow** at the start of its row (`▸` collapsed, `▾` expanded). Default: collapsed. The arrow exists **only on rows that have children**: a row without children shows no tree control and **reserves no space**, so it renders exactly like a flat row. Root rows are flush left. The toggle is operable by keyboard, has an accessible label, and clicking it never selects the session.
- **Automatic expansion:** a node is expanded while its subtree **has** an active, unread or pinned descendant. "Unread" is the rule of the unread indicator on the session row, **including its exception for the session currently being viewed** (that row never shows the indicator, so it never counts). The expansion and the cumulative indicator then match what the user sees on the child row. This is evaluated at first render and every time data changes or loads (live updates, pagination, ancestor fetch). The path to each such descendant expands. This is what satisfies the first anti-goal. A node whose subtree has none of these starts collapsed.
- **Automatic expansion is sticky.** A node that expanded automatically stays expanded until the user collapses it or the page reloads. A run ending never folds rows under the user's pointer.
- **A manual collapse or expand is final:** nothing but the user changes it. A collapsed node stays collapsed whatever happens in its subtree (a worker starts, a session becomes unread or pinned): the cumulative indicator of the collapsed node is how the user learns about it. A user expand is kept until the user collapses it. Only a node the user never touched follows the automatic rule above.
- **Persistence:** the user's explicit collapses and expands are stored in the browser and survive a reload; they win over automatic expansion. Automatic expansion is recomputed at each load. So a node the user collapsed stays collapsed after a reload, even with an active descendant. *(Author's call on storage.)*
- **A collapsed node shows the cumulative activity indicator instead of its own.** The cumulative indicator is the one the project and workspace badges show, computed by the same function (the priority cascade: pending request, unread, assistant turn, background shells, crons, active process) over **the node itself and every loaded descendant at any depth that the tree shows** (not the backend's spawn chain: the indicator matches the list, so a session outside the scope or hidden by the archived filter does not count). Synthetic, ephemeral and hidden sessions never count. Unread is the unread indicator rule of the session row, including its exception: the session currently being viewed never counts. When the node is **expanded** it keeps its own indicator: its children show theirs. There is no count, no pill and no other summary on the row; the cumulative indicator carries the usual tooltip of that indicator. **Slot:** the cumulative indicator takes the position of the row's activity indicators (the process, pending-request and unread indicators, in compact and normal view), and shows even when the node itself has none. Everything else on the row stays as on any row: the comments icon, the pin icon, the session's state icons, and the memory and timer of the process cell.
- A child row is indented one step per level and joined to its parent by **guide lines**, as in a file explorer: a vertical line from the parent and an elbow into the child. The vertical line of an ancestor continues past a child while that ancestor has more children below, and stops at its last child. The lines use the color of the sidebar's section separators ("Active", "Last 24 hours", …). Indentation is capped at about three levels: deeper levels keep the same indentation and guide width as the third. The guides and the indent exist only on child rows.
- **A child row hides its project marker when it has the same project as its parent** (the colored dot in compact view, the project badge in normal view), and shows it when the project differs: the reference is always the parent's project, so below a session of another project the comparison restarts from that project. Root rows always show it (when it shows at all today: only outside a single-project scope). A git worktree is its own project, so a session in a worktree of its parent's project shows its marker. Nothing is reserved for a hidden marker: the row's content starts where the marker would have been, and the normal view's project line keeps only what it still has to show (comments, unread) and disappears when it has nothing.
- Compact view applies to nested rows like any other row.

## 10. Cross-filter blocks, selection, search

- **"Pinned elsewhere"** and **"Active elsewhere"** stay flat lists. They are outside the scope by definition, and no tree is built across scopes.
- **"Selected"** (the deep-linked session outside every block) stays flat and unchanged.
- A session that is in a cross-filter block is not nested in the natural tree, even if one of its ancestors is in the candidate set.
- **Search** switches the list to flat for the duration of the query. Results are the current flat blocks (Selected, cross-filter blocks, and the candidate set of §2 for the natural block), each session classified and sorted by its **own** state as in flat mode, so a query can match old sessions that flat mode would not load. A search result is never hidden under a collapsed node.
- When the **selection changes** to a session under a collapsed node, or when the selected session is found under a collapsed node at first render (reload, deep link), its path expands once so the row is visible. A later manual collapse sticks, even if it hides the selected row. *(Author's call.)*
- **Multi-selection** (selection bar: archive, pin, stop, …) acts on visible rows only. Selecting a collapsed parent never selects its hidden descendants. Collapsing a node drops its hidden descendants from the selection, so no action ever reaches a row the user cannot see. *(Author's call.)*
- The **session switcher** ("displayed sessions" mode) is a navigation aid: it lists what it lists today (display order, cross-filter blocks, Selected, search filtering) and additionally the sessions under collapsed nodes, each right after its parent's subtree order. *(Author's call.)*
- Keyboard navigation moves over the visible rows only, in display order.

## 11. Data availability

The sidebar loads sessions page by page, newest first, so a child can be loaded without its parent. The tree must not show a child as a wrong root for long:

- When tree mode is on and a node's `spawned_by` target is not loaded, the client fetches it by id (the single-session endpoint exists), following the chain upward.
- A fetched ancestor that is **inside** the scope joins the candidate set (§2), even when it is older than the flat list's pagination window: its children nest under it and it is placed by its effective state (§7). The same holds for an old in-scope descendant of a member. The extension of §2 applies **only to the sidebar's tree rendering**: the command palette picker and the switcher keep the flat-mode candidate set (the switcher only adds the sessions under collapsed nodes, §10).
- A fetched ancestor that is **outside** the scope is not nested into the list. §5.3 applies (hint shown). It stays eligible for the existing flat cross-filter blocks, exactly as in flat mode.
- A hidden or sub-agent parent (the endpoint answers 404) is final: no retry, and no hint (§5.3, parent unknown). A transient failure (network, server error) is not final: the fetch is retried on the next data load. The chain above that parent is unknown to the client, so the node becomes a root even if a grandparent is visible (see §12).
- While an ancestor is being fetched, the child may briefly appear as a root. This is accepted.
- **Turning the option off applies the unchanged flat rules** to whatever the store holds. Sessions the tree loaded are treated like any other loaded session (as one fetched by a deep link is today): the flat list may therefore show a pinned or unread one in a cross-filter block. This is accepted.

## 12. Known limits

- **Bulk archive is unchanged in every mode.** It works on a session's own `mtime`, own pin and own process, on the server. In tree mode the "older than X" separators therefore **no longer mark exactly what the matching archive option selects**: a root can sit in a recent section because of its descendants while its own date is old. Archiving it leaves its child visible as a root with the hint of §5.3. Accepted for v1.
- **A collapsed node does not explain its placement.** It shows no pinned marker and no subtree date, by the owner's choice (no counts, no pill). A collapsed parent can therefore sit in "Pinned" because of a pinned descendant, or in "Last 24 hours" because of a recent child, with its own pin and date unchanged; expanding it shows why. Auto-expansion covers active, unread and pinned descendants unless the user collapsed the node (§9).
- **A hidden middle link cuts the chain.** Example: leader (visible) → manager (hidden) → worker (visible). The worker is a root, not nested under the leader. Skipping hidden links (for example through `spawn_root`) is a possible later refinement.
- A pinned scope (`project`, `workspace`, `all`) applies per session as today. A pinned descendant lifts its root into the Pinned section but does not change any cross-filter rule.
- No persistence across devices for expansion overrides.

## 14. The icon rail

The icon rail lists one button per session that has a live process, globally (no filter), ordered like "All Projects". With the option on, it follows the tree:

- **Order.** Rail buttons follow the tree order of the "All Projects" list: roots by their effective state (§6, §8), each child right after its parent.
- **Collapsed nodes.** A node is collapsed in the rail if and only if the user collapsed it (explicit choice, §9); an untouched node is expanded. The rail keeps no state of its own and has no control to collapse or expand.
- **One button per collapsed node.** A collapsed node, and everything below it, shows as a single button: the node's own session, even when it has no live process itself. Its indicator is the **cumulative indicator** of §9: the node and every loaded descendant, same set and rules. Clicking the button opens the node's own session.
- **Expanded nodes.** Each session with a live process keeps its own button and its own indicators, as today. An idle ancestor of such a session gets no button.
- **Tooltip.** The button's tooltip is unchanged: the preview of the button's session. (Showing the subtree there is a possible later change.)
- **Repeated project mark.** As in the list (§9), a button whose session has the same project as its reference shows a small dot instead of its project mark. The reference is the nearest ancestor of the session that has a button in the rail (an idle ancestor without a button is skipped; with no such ancestor the mark is shown). A button that shows a mark always keeps the mark's place: the dot takes exactly the space of the mark, centred on it, so nothing moves. The dot is a few pixels wide, in the color of the link segments.
- **Link cue.** Buttons are not indented. The buttons of one tree, when there are two or more, are chained by a short 1 px vertical segment centred on each button's project mark, in the separators' color, drawn between a mark and the next one (so a tree of three buttons has two segments, however many levels it spans). A segment stops 2 px before a project mark, but runs right up to the center of a dot, and leaves a dot from its center. The segment is painted above the buttons, also above the open session's highlighted button, and never catches the pointer. A tree with a single button has none.
- With the option off, the rail is unchanged.

## 13. Left to the builder

Data structure and computation of the tree and effective values; how the virtual scroller consumes depth; row visuals (arrow, cumulative indicator, guide lines, hint); exact override storage; how ancestors are fetched and batched; animation details; tests.
