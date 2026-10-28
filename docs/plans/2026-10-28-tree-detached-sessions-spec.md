# Tree-detached sessions — spec

Status: draft for review. Builds on `2026-10-27-sidebar-session-tree-spec.md`.

## 1. Problem

"Show orchestrated sessions as tree" nests a session under the session that spawned it (`spawned_by`). That is right when the child serves the parent's work. It is wrong when the parent is only a coordinator that spawns unrelated sessions (other projects, other subjects). The tree then groups sessions with nothing in common except the filiation.

## 2. Decision

Add a per-session boolean, **`tree_detached`**. It is display-only.

- `spawned_by` and `spawn_root` are unchanged: filiation, Orchestration tab, topology, spawn ancestors and every orchestration CLI selector (`--spawned-by`, `--descendants`, `--siblings`, `--spawn-tree`) ignore the flag.
- In the sidebar tree (list and icon rail) a detached session has **no parent**: it is a root. Its own children (attached or not) stay under it.
- Default `false` (attached). Backfill: none.
- The flag is mutable, in both directions (detach and reattach), at any time.
- The flag has no effect when `spawned_by` is null. It is stored as given; nothing validates it against `spawned_by`.

Non-goals: no visual cue that a session is detached or where it came from; no bulk action; no per-parent default; no use of the flag outside the sidebar tree (it must stay readable for later use).

## 3. Backend

### 3.1 Model
`Session.tree_detached = BooleanField(default=False)`, one migration (next number after the latest). Comment it next to `mute_on_user_turn` / `spawned_by` in `src/twicc/core/models.py`: TwiCC UI behaviour, not an `AgentSettings` field, not trust-related.

### 3.2 Serialization
- Add `"tree_detached": session.tree_detached` to the session serializer (`src/twicc/core/serializers.py`, next to `mute_on_user_turn`). The frontend store receives it through the existing session payloads and websocket updates.
- Add `"tree_detached"` to `SESSION_LISTING_FIELDS`, so the CLI session payload carries it **without `--full`** (same group as `hidden`, `pinned`, `mute_on_user_turn`).
- `twicc-sessions/list.md` shows the field in its example and field list.

### 3.3 Creation
- `twicc create-session` gets `--tree-detached` (boolean flag, default off; no negative form needed, same shape as `--mute-on-user-turn`).
- Path, following `mute_on_user_turn` end to end: typer option next to `cli/create_session/command.py:177` and payload key near `:538`; `payload.get(...) is True` in `core/services/session_creation.py` (~`:198`) and the `set_pending_session_attributes` call (~`:495`); the field and setter of `PendingSessionAttributes` in `pending_session_attributes.py` (new field with default `tree_detached: bool = False` in `set_pending_session_attributes`, and its log line; `rekey_pending_session_attributes` moves the whole entry, so Codex needs no change); and the row write in `providers/sessions_watcher.py` (~`:712-714`, where `pop_pending_session_attributes` fills `hidden` / `mute_on_user_turn`). Without the last step the flag is lost.
- Not touched: `read_session_visibility_flags` and the `asgi.py` process-state broadcast (they serve notifications only).
- A session created from the UI never has the flag. From a plain terminal (no spawner found by PID ancestry) the flag is stored but inert: the help text says it matters only when the caller is itself a session.

### 3.4 Mutation
Same blueprint as `mute` / `notify` (commit `386303ac0`):

- Service: `apply_session_tree_detached_change(session, value)` and `update_session_tree_detached_from_payload` in `core/services/session_update.py`, kind `session:update_tree_detached`, handled by `drop_requests_watcher.py`. The change broadcasts `session_updated` so every client re-groups live, only when the session is not hidden (same gate as mute and as the REST handler: hidden sessions never reach the frontend, and the tree ignores them). The timeline part follows §3.5.
- Singular CLI: `twicc update-session <ID> detach-tree` and `attach-tree`. Plural: `twicc update-sessions detach-tree` / `attach-tree`, with the same selection rules as `mute` / `notify`. The RPC routes and MCP tools are derived automatically.
- REST: `PATCH` on the session accepts `tree_detached` (boolean, else 400), like `mute_on_user_turn` in `views.py`: the handler calls `apply_session_tree_detached_change` and sets `needs_broadcast = True`, so the existing combined broadcast fires and the UI shares the helper with the CLI.
- Wording of CLI/skill help: "detach" / "attach" refer to the sidebar tree only.

### 3.5 Timeline
The flag is a state interval like `archived`, `hidden` and `muted`: two paired kinds, recorded only through `record_event` / `record_event_sync` (never a direct insert).

- `TimelineKind.TREE_DETACHED_STARTED = "tree_detached.started"` ("Tree detached started") and `TREE_DETACHED_ENDED = "tree_detached.ended"` ("Tree detached ended") in `core/models.py`. The `kind` field `choices` change, so the same migration as the column carries the `AlterField`.
- `core/timeline.py`: both kinds in `PAYLOAD_TYPES` with `None` (no payload); not in `STARTED_OF`; update the comment that lists the kinds without a started payload.
- `core/services/timeline_filters.py`: both kinds in the `organization` family (a test pins that every kind is in exactly one family).
- Recording on change: `apply_session_tree_detached_change` records `started` / `ended` only when the value changes, as `apply_session_mute_on_user_turn_change` does; actor and origin come from the existing mechanism.
- Recording at creation: `record_session_created` in `core/services/session_timeline.py` writes a `started` event, `caused_by` the `session.created` event, when the session is created with the flag, as for `hidden` and `muted`. The event is written whenever the row is created with the flag, even without `spawned_by`.
- Frontend: `utils/timeline/describeEvent.js` maps `tree_detached.started` to "Detached from parent" and `tree_detached.ended` to "Reattached to parent", both with the icon `sitemap` (Font Awesome Free, checked on the CDN; `link-slash` is taken by the stale-session icon). `sitemap` already marks the Workflows tab, the Orchestration command and the "Show orchestrated sessions as tree" toggle (`staticCommands.js`); the tree-toggle meaning is the intended one. Add `detached` to the `organization` hint in `utils/timeline/filters.js` (~`:24`) and in `filters.test.js` (~`:25`).
- Docs: no doc lists the timeline kinds today (checked: `twicc-timeline/SKILL.md`, `SKILLS-AND-CLI.md`, `views.py` family names); only update a doc that names the contents of the `organization` family, if one is found.
- Docstrings to update: `STARTED_OF` in `core/timeline.py` and `record_session_created` in `core/services/session_timeline.py` (they name hidden and muted).
- No backfill: no existing session is detached.

## 4. Frontend

### 4.1 One rule, one helper
Add `treeParentId(session)` in `frontend/src/utils/sessionTree.js`: `session.tree_detached ? null : session.spawned_by || null`. **Every** tree-side read of `spawned_by` uses it, both the start of a walk and each step (`current.spawned_by`), so a detached non-candidate ancestor also stops the walk. Reads to convert (line numbers as of this writing):

- `sessionTree.js`: `resolveParent` (`:42`, `:48`), the hint parent (`:92-93`), `childrenOf` in `extendTreeCandidates` (`:270-272`), its `walkUp` (`:284`, `:290`), `findMissingAncestorIds` (`:330`, `:339`).
- `railActiveSessionsTree.js`: `buildChildrenIndex` (`:15`) and the ancestor walk (`:67`, `:72`).
- Comments that say "walking `spawned_by`" are updated to match: `useRailActiveSessions.js:106`, `sidebarSessions.js:64`, `sessionTree.js` (~`:254`, ~`:315`) and `railActiveSessionsTree.js` (~`:11`).

Semantics: a detached session is still a candidate when it qualifies on its own; it is a root and only the walk **above** it stops. The existing behaviour is kept for the rest: a walk passes through loaded, non-blocking, non-candidate ancestors.

Orchestration-side reads keep `spawned_by`: `orchestrationView.js:110`, `spawn_ancestors` (and the comment at `stores/data.js:5314`), timeline.

### 4.2 Consequences (intended)
- A detached session's activity does not count in its spawner's cumulative indicator, tooltip list or collapsed rail node.
- The project-mark rule (same project as parent hides the mark; rail dot) uses the tree parent, so a detached root always shows its mark.
- Expansion state is keyed by session id and is untouched; toggling the flag only moves the node.

### 4.3 Menu
In the sidebar session menu (`SessionListItem.vue`, `handleMenuSelect`), one entry:

- gated by a new boolean prop `canDetachFromTree` (default `false`), which `SessionList.vue` passes as `:can-detach-from-tree="showAsTree"` (the "Show orchestrated sessions as tree" option, not `treeActive`), **independent of the search** (the tree is only suspended during a search, so the entry stays available). Not `treeRow`, which is null during a search. The default keeps the entry out of `PeerMessageReviewDialog.vue` and of the rail preview (`show-menu="false"`);
- shown only when `session.spawned_by` is set and the session is a regular session (not a draft, ephemeral or subagent row);
- label "Detach from parent" (value `detach-tree`) when attached, "Reattach to parent" (value `attach-tree`) when detached, both with the icon `sitemap` (`link-slash` already means a stale session in the sidebar);
- placed in the block after the pin block; `hasItemsAfterPinBlock` (`SessionListItem.vue`, ~`:287`) is already true for every regular non-draft session, so it needs no change;
- the `handleMenuSelect` branches are added after the existing ephemeral guard;
- they call a store action `setSessionTreeDetached(projectId, sessionId, value)`: a simple optimistic action like `setSessionPinMode` (optimistic update, PATCH, `updateSessionFromServer`, rollback on error). A fast double toggle is accepted as is (no request queue as in `utils/sessionMute.js`).

## 5. Agent-facing documentation (required)

The agent that creates a session must be able to decide by itself. Every text states purpose and criterion, not only syntax.

Criterion (same meaning everywhere): use `--tree-detached` when the new session has **no topical link** with you (you are a coordinator spawning independent work in other projects or on other subjects); leave it off when the child serves your own work (a sub-task, a bug found while working on this). Effect: only the sidebar tree display; filiation, orchestration and reporting are unchanged.

Files:
- `twicc-create-session/session-behavior.md` (flag list and a `### --tree-detached` section) and the "Session behavior" row of `twicc-create-session/SKILL.md` (~`:100`).
- `twicc-orchestration/SKILL.md` (~`:116-122`, the session-behaviors decision block, with `--hidden` and `--mute-on-user-turn`): add the rule. Check `twicc-orchestration-leader` and `twicc-orchestration-manager` and add the rule where they decide how to spawn.
- `twicc-update-session/`: `detach-tree.md` and `attach-tree.md` (or one file, following the neighbouring layout) and its `SKILL.md` (`description`, `argument-hint`, the sub-command index and body lines). Same for `twicc-update-sessions/` (the plural text says the flag applies to the selected sessions only).
- `twicc-sessions/list.md`: field in the example and field list.
- `SKILLS-AND-CLI.md`: the `create-session` entry, the `update-session` sub-command list and the `update-sessions` paragraph.
- Typer help of the flag and of the new sub-commands (the MCP tool descriptions derive from it), and the docstrings that list the sub-commands: `cli/update_session/command.py` (module docstring and app help) and `cli/update_sessions/command.py` (~`:87`); also the module docstring of `core/services/session_update.py` (kinds list) and the "change these after creation" line of `twicc-create-session/session-behavior.md`. Check `src/twicc/mcp/descriptions.py` for hand-written text that needs the same sentence.
- `src/twicc/agent/system_prompt.py`, Orchestration section: one short sentence after the `twicc-create-session` sentence (~`:90`), why/when only, no flag syntax. The system prompt is outside the `plugin.json` rule.
- Bump `plugin.json` (minor: new flag and new sub-commands).

## 6. Tests

- Python, modelled on `tests/test_timeline_flag_hooks.py`, `tests/test_timeline_session_created.py` (timeline kinds and creation event), `tests/test_mute_on_user_turn_creation.py`, `tests/test_mute_on_user_turn_api.py`, `tests/test_drop_transport.py`, `tests/test_mcp_tools.py` and `tests/test_cli_listing_envelope.py`: serializer and `slim_session` carry the field; the pending-attribute path persists the flag on the new row; update service and drop-request kind (boolean required, unknown session); `session_updated` broadcast on change and its hidden-session gate; REST PATCH (boolean, else 400); MCP creation property `tree_detached` (boolean, description with the criterion) and the two new update tools; migration default and the `kind` choices; timeline: both kinds recorded on change and not on a no-op, the creation event with `caused_by`, the family pin test, `tests/test_timeline_filters_bench.py` (~`:84`: replace the literal `8` in the `organization` share with `len(FAMILIES["organization"])`), `PAYLOAD_TYPES` validation (a payload is refused).
- Frontend unit: `describeEvent` and the kind lists of `SessionEventNotice.test.js` (~`:35`) and `describeEvent.test.js` for the two kinds; the `organization` hint in `filters.test.js`; `treeParentId`; `buildSessionTree` with a detached child (root, no hint), a detached ancestor in a chain (walk stops), a detached session with attached children; `extendTreeCandidates` / `findMissingAncestorIds` do not walk through a detached session; rail selection and children index; menu entry visibility (each condition) and label; store action.

## 7. Operations left to the user

`migrate` after the migration is created; dev server restart after backend changes.

## 8. Known limits

- Existing children stay attached until the user detaches them one by one.
- A coordinator must pass the flag at each `create-session` call (no default per parent).
- When the spawner is deleted (`spawned_by` becomes null) the flag is kept but inert, and the menu hides the entry.
