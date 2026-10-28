# Tree-Detached Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-session boolean `tree_detached` so a spawned session shows as a root in the sidebar tree, while `spawned_by`, orchestration and topology stay intact.

**Architecture:** One `Session` column, exposed in the serializer and the slim CLI payload, settable at creation (CLI) and later (CLI, REST PATCH, sidebar menu), recorded as paired timeline kinds. The frontend routes every tree-side read of `spawned_by` through one helper, `treeParentId`. Agent-facing docs explain when to use the flag.

**Tech Stack:** Django 6 / Python 3.13 (pytest via `uv run pytest`), Typer CLI, Vue 3 + Pinia, `node --test` frontend tests.

**Spec:** `docs/plans/2026-10-28-tree-detached-sessions-spec.md` (read it first; section numbers below refer to it).

## Global Constraints

- Code, comments, UI strings and docs are in English. Commit subject: `type(scope): lowercase summary`, with a body, and the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (use the exact model name you run as).
- One commit per task. Stage files explicitly (`git add <paths>`), never a directory. The checkout holds uncommitted files that are not ours (`docs/plans/2026-07-18-*`, `2026-10-26-session-timeline-followups.md`, `2026-10-27-test-suite-speedup-handoff.md`): never stage them. The spec is already committed; the plan file is committed only when the owner asks, never inside a task commit.
- Python: `orjson`, never stdlib `json`. Timeline rows only through `record_event` / `record_event_sync`. No Django signals.
- Do not run `migrate`, `npm install`, `uv add`, and do not restart dev servers. `makemigrations` is allowed.
- Python tests: `cd /home/twidi/dev/twicc-poc && uv run pytest <path>`. Frontend tests: `cd frontend && node --test <file>` (whole suite: `npm test`). Lint: `uvx ruff check <files>`. Never `uv pip` or `--active`.
- Names fixed by the spec: column `tree_detached`; CLI flag `--tree-detached`; sub-commands `detach-tree` / `attach-tree`; drop-request kind `session:update_tree_detached`; timeline kinds `tree_detached.started` / `tree_detached.ended`; menu values `detach-tree` / `attach-tree`; icon `sitemap`; labels "Detach from parent" / "Reattach to parent" (menu), "Detached from parent" / "Reattached to parent" (timeline).
- Every `SKILL.md` change needs a `plugin.json` version bump (this plan: one minor bump, Task 8).
- Hidden sessions never reach the frontend: the update broadcast keeps the `not session.hidden` gate.

## Review Focus

Input classes the spec implies; each has a test in the owning task.

1. Detached session whose spawner is not in the loaded list: no ancestor fetch, no hint (Task 6).
2. Chain A → B (detached) → C: C stays under B, B is a root, the walk from C stops at B (Task 6).
3. Detached session with attached children: the children stay under it (Task 6).
4. No-op update (value unchanged): no timeline row, no error (Task 4).
5. Update on a hidden session: row saved, timeline written, no broadcast (Task 4).
6. Created with the flag from a context with no spawner: the creation event is written (Task 2) and the row stores the flag through the pending path (Task 3).
7. Menu entry absent during no-tree mode, for drafts, for sessions without `spawned_by`; present during a search (Task 7).

---

## File Structure

| File | Role |
|---|---|
| `src/twicc/core/models.py`, `src/twicc/core/migrations/0165_*.py` | column + timeline kind choices and registration |
| `src/twicc/core/serializers.py` | serializer field + `SESSION_LISTING_FIELDS` |
| `src/twicc/core/timeline.py`, `core/services/timeline_filters.py`, `core/services/session_timeline.py` | timeline registration and creation event |
| `src/twicc/cli/create_session/command.py`, `core/services/session_creation.py`, `pending_session_attributes.py`, `providers/sessions_watcher.py` | creation path |
| `src/twicc/core/services/session_update.py`, `drop_requests_watcher.py`, `views.py` | update service, drop-request kind, REST |
| `src/twicc/cli/update_session/tree_detached_command.py` (new), `cli/update_session/command.py`, `cli/update_sessions/command.py` | CLI |
| `frontend/src/utils/sessionTree.js`, `utils/railActiveSessionsTree.js` | `treeParentId` and tree-side walks |
| `frontend/src/stores/data.js`, `components/session/list/SessionListItem.vue`, `SessionList.vue` | store action, menu |
| `frontend/src/utils/timeline/describeEvent.js`, `filters.js` | timeline labels |
| skills under `src/twicc/agent/plugin/twicc/skills/`, `SKILLS-AND-CLI.md`, `agent/system_prompt.py`, `plugin.json` | agent-facing docs |

---

### Task 1: Column, timeline kind choices, migration

**Files:**
- Modify: `src/twicc/core/models.py` (Session near `mute_on_user_turn` ~`:527`; `TimelineKind` ~`:2290`), `src/twicc/core/timeline.py` (`PAYLOAD_TYPES`), `src/twicc/core/services/timeline_filters.py` (`FAMILIES["organization"]`)
- Create: `src/twicc/core/migrations/0165_*.py` (generated)
- Test: `tests/test_tree_detached_model.py`, `tests/test_timeline_filters_bench.py` (~`:84`)

**Interfaces:**
- Produces: `Session.tree_detached: bool` (default `False`); `TimelineKind.TREE_DETACHED_STARTED = "tree_detached.started"`, `TimelineKind.TREE_DETACHED_ENDED = "tree_detached.ended"`.

- [ ] **Step 1: Write the failing test** in `tests/test_tree_detached_model.py` (model the fixtures on `tests/test_mute_on_user_turn_api.py`): `test_session_defaults_to_attached` (a created `Session` has `tree_detached is False`); `test_timeline_kind_values` (the two kinds have the exact strings above and fit `max_length=40`); `test_kinds_take_no_payload` (`validate_data("tree_detached.started", {"x": 1})` raises `ValueError`, `validate_data("tree_detached.started", None)` returns an empty payload).
- [ ] **Step 2: Run** `uv run pytest tests/test_tree_detached_model.py -v`. Expected: FAIL (attribute missing).
- [ ] **Step 3: Implement** the field `tree_detached = models.BooleanField(default=False)` with a comment next to `mute_on_user_turn` (TwiCC UI behaviour, display only, not an `AgentSettings` field), and the two `TimelineKind` members with labels "Tree detached started" / "Tree detached ended", placed after `MUTED_ENDED`; register both kinds with payload `None` in `PAYLOAD_TYPES` and in the `organization` family, so the enum-sync and family tests stay green at this commit. In `tests/test_timeline_filters_bench.py` (~`:84`), replace the literal `8` of the `organization` share with `len(FAMILIES["organization"])`.
- [ ] **Step 4: Generate the migration**: `uv run python -m django makemigrations core -n session_tree_detached --settings=twicc.settings_test`. Expected: one file `0165_session_tree_detached.py` with `AddField` and an `AlterField` on `TimelineEvent.kind` choices. Open it and check nothing else changed. `twicc.settings_test` isolates the data dir and uses an in-memory DB: do not use `--settings=twicc.settings` here.
- [ ] **Step 5: Run** `uv run pytest tests/test_tree_detached_model.py tests/test_timeline_enums_sync.py -v`. Also run `uv run pytest tests/test_timeline_filters.py`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(sessions): add the tree_detached column and its timeline kinds` (stage `models.py`, the migration, the test).

### Task 2: Timeline creation event and frontend labels

**Files:**
- Modify: `src/twicc/core/timeline.py` (`STARTED_OF` docstring), `src/twicc/core/services/session_timeline.py` (`record_session_created` + docstring), `frontend/src/utils/timeline/describeEvent.js` (`FLAG_KINDS` ~`:47`), `frontend/src/utils/timeline/filters.js:~24`
- Test: `tests/test_timeline_session_created.py`, `frontend/src/utils/timeline/describeEvent.test.js`, `filters.test.js`, `frontend/src/components/session/detail/SessionEventNotice.test.js`

**Interfaces:**
- Consumes: Task 1 kinds, `Session.tree_detached`.
- Produces: `record_session_created` writes `TREE_DETACHED_STARTED` (with `caused_by` = the `session.created` event) when `session.tree_detached` is true.

- [ ] **Step 1: Write the failing tests.** Backend, in `tests/test_timeline_session_created.py` (model on the existing hidden/muted cases at `:65-71`): `test_created_detached_session_records_tree_detached_started` and `test_created_detached_session_without_spawner_records_event`: create the row directly with `Session.objects.create(..., tree_detached=True)` (the pending path arrives in Task 3), call `record_session_created(session, origin=...)` as the neighbouring cases do, and assert the row kind, `caused_by` = the `session.created` event, and, for the second test, `spawned_by` null. Frontend: extend the per-kind table of `describeEvent.test.js`, and add the two kinds to the flat kind list of `SessionEventNotice.test.js` (~`:35`), for `tree_detached.started` → "Detached from parent" and `tree_detached.ended` → "Reattached to parent", icon `sitemap`; update the `organization` hint assertion in `filters.test.js` (~`:25`) to the string with `detached` added.
- [ ] **Step 2: Run** the tests of Step 1 and `tests/test_timeline_enums_sync.py tests/test_timeline_filters.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** `record_session_created`: after the muted block, `if session.tree_detached: record_event_sync(TimelineKind.TREE_DETACHED_STARTED, caused_by=created, **who, **common)`. Update the `STARTED_OF` comment and the `record_session_created` docstring to name the new flag. Frontend: add both entries to `FLAG_KINDS` and `detached` to the `organization` hint.
- [ ] **Step 4: Run** `uv run pytest tests/test_timeline_session_created.py tests/test_timeline_filters.py tests/test_timeline_enums_sync.py tests/test_tree_detached_model.py -v` and, in `frontend`, `node --test src/utils/timeline/describeEvent.test.js src/utils/timeline/filters.test.js src/components/session/detail/SessionEventNotice.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(timeline): record tree_detached state intervals` (stage the files above).

### Task 3: Serializer, listing fields, creation path

**Files:**
- Modify: `src/twicc/core/serializers.py` (`SESSION_LISTING_FIELDS` ~`:144`, `serialize_session` ~`:239`), `src/twicc/cli/create_session/command.py` (option near `:177`, payload near `:538`), `src/twicc/core/services/session_creation.py` (~`:198`, ~`:495`), `src/twicc/pending_session_attributes.py`, `src/twicc/providers/sessions_watcher.py` (~`:712-714`)
- Test: `tests/test_tree_detached_creation.py`, `tests/test_cli_listing_envelope.py` (existing)

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: `serialize_session(session)["tree_detached"]`; the field in `SESSION_LISTING_FIELDS`; `PendingSessionAttributes.tree_detached: bool = False` (last field, after `origin`); `set_pending_session_attributes(..., tree_detached: bool = False)`; CLI payload key `tree_detached`.

- [ ] **Step 1: Write the failing tests** in `tests/test_tree_detached_creation.py`, modelled on `tests/test_mute_on_user_turn_creation.py`: `test_serializer_exposes_tree_detached`; `test_slim_session_keeps_tree_detached`; `test_creation_payload_sets_pending_attribute` (payload `{"tree_detached": True}` → `pop_pending_session_attributes(...).tree_detached is True`; absent → `False`); `test_watcher_persists_pending_flag_on_the_row` (the row created through the watcher path carries `tree_detached=True`, and the `tree_detached.started` event follows `session.created`).
- [ ] **Step 2: Run** `uv run pytest tests/test_tree_detached_creation.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in this order: serializer key and `SESSION_LISTING_FIELDS` (group with `mute_on_user_turn`); `PendingSessionAttributes` field, `set_pending_session_attributes` parameter, and its debug log line; `session_creation.py` parsing with `payload.get("tree_detached") is True` and the argument passed to `set_pending_session_attributes`; typer option `--tree-detached` (`bool`, default `False`; its help text carries the spec §5 criterion sentence from the start, including the word "topical" that Task 5 asserts, and says the flag matters only when the caller is itself a session) and payload key; `sessions_watcher.py`: `kwargs["tree_detached"] = pending.tree_detached`. Do not touch `read_session_visibility_flags` or `asgi.py`. Check `rekey_pending_session_attributes` moves the whole tuple (no change expected).
- [ ] **Step 4: Run** `uv run pytest tests/test_tree_detached_creation.py tests/test_cli_listing_envelope.py tests/test_mute_on_user_turn_creation.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(sessions): expose tree_detached and accept it at creation`.

### Task 4: Update service, drop-request kind, REST PATCH

**Files:**
- Modify: `src/twicc/core/services/session_update.py` (new functions near `apply_session_mute_on_user_turn_change` `:758` and `update_session_mute_on_user_turn_from_payload` `:960`; module docstring kinds list `:28`), `src/twicc/drop_requests_watcher.py` (registry `:76`), `src/twicc/views.py` (PATCH branch near `:1246`)
- Test: `tests/test_tree_detached_update.py`, `tests/test_timeline_origin_writers.py` and `tests/test_drop_transport.py` (extend)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `async def apply_session_tree_detached_change(session, tree_detached: bool) -> None`; `async def update_session_tree_detached_from_payload(payload: dict) -> UpdateSessionResult` (payload `{"session_id": str, "tree_detached": bool}`); drop-request kind `"session:update_tree_detached"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_tree_detached_update.py` (model on `tests/test_mute_on_user_turn_api.py` and `tests/test_timeline_flag_hooks.py`): `test_change_saves_and_records_started`; `test_change_back_records_ended`; `test_noop_change_records_nothing` (Review Focus 4); `test_payload_requires_boolean` (missing → `missing`, non-bool → `invalid_tree_detached`, missing `session_id`); `test_unknown_session_is_rejected`; `test_broadcast_skipped_for_hidden_session` and `test_broadcast_sent_for_visible_session` (Review Focus 5; capture `group_send`); `test_origin_of_the_flip_rows` (extend `tests/test_timeline_origin_writers.py` next to its existing mute case: the rows carry the request origin); `test_rest_patch_accepts_boolean` and `test_rest_patch_rejects_non_boolean_with_400`. Extend `tests/test_drop_transport.py` with the new kind's registry entry.
- [ ] **Step 2: Run** `uv run pytest tests/test_tree_detached_update.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** by copying the mute functions: `apply_session_tree_detached_change` records `TREE_DETACHED_STARTED` / `TREE_DETACHED_ENDED` through `record_event_sync` only when the value differs, under `run_under_db_write_lock`; `update_session_tree_detached_from_payload` validates like the mute function, then broadcasts `session_updated` only when `not session.hidden`; register the kind in `drop_requests_watcher.py`; keep the inner function named `_save_and_record` (`tests/test_timeline_origin_guard.py` allowlists writers by file and function name); the PATCH branch validates `isinstance(value, bool)` (else 400 `"tree_detached must be a boolean"`), calls the apply function and sets `needs_broadcast = True`. Update the `session_update.py` module docstring.
- [ ] **Step 4: Run** `uv run pytest tests/test_tree_detached_update.py tests/test_timeline_origin_guard.py tests/test_timeline_origin_writers.py tests/test_drop_transport.py tests/test_mute_on_user_turn_api.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(sessions): update the tree_detached flag from the service, drop requests and REST`.

### Task 5: CLI sub-commands and MCP tools

**Files:**
- Create: `src/twicc/cli/update_session/tree_detached_command.py`
- Modify: `src/twicc/cli/update_session/command.py` (docstring, app help, registration like `mute`), `src/twicc/cli/update_sessions/command.py` (new `detach-tree` / `attach-tree` commands after `notify`; help/docstring ~`:87`)
- Test: `tests/test_mcp_tools.py` (extend), `tests/test_tree_detached_cli.py` (new)

**Interfaces:**
- Consumes: Task 4 kind `session:update_tree_detached`.
- Produces: `twicc update-session <ID> detach-tree|attach-tree`; `twicc update-sessions detach-tree|attach-tree`; MCP tools derived automatically (`update_session_detach_tree`, `update_session_attach_tree`, `update_sessions_detach_tree`, `update_sessions_attach_tree`).

- [ ] **Step 1: Write the failing test.** In `tests/test_mcp_tools.py`, add the four tool names to the expected list (see how `update_session_mute` appears at `:46`, `:61-62`) and assert the `create_session` tool has a boolean property `tree_detached` whose description contains "topical" (the criterion word). Create `tests/test_tree_detached_cli.py`: use `typer.testing.CliRunner` on `update_session_app` and `update_sessions_app` and define in the new file its own copy of the module-local fixtures `data_dir`, `sessions` and `submitted` of `tests/test_cli_attach.py` (~`:20-80`: real `Session` rows, `transport.ensure_server_available` / `submit` / `wait` monkeypatched, a fake submission exposing `poll`, `was_received`, `cleanup` and `outcome`). In the copy, the submission status must be `"updated"` for kind `session:update_tree_detached` (the original answers `"sent"`; `update-session` exits 5 and `run_batch` counts a failure on any other status than `"updated"`); the plural command goes through `run_batch`, which never calls `transport.wait`. Assert that `detach-tree` submits payload `{"session_id": <id>, "tree_detached": True}` and `attach-tree` `False`, both under kind `session:update_tree_detached`, for the singular and the plural command.
- [ ] **Step 2: Run** `uv run pytest tests/test_mcp_tools.py tests/test_tree_detached_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** `tree_detached_command.py`: copy `mute_command.py`, with `_run_tree_detached_update(session_id, *, tree_detached, timeout)` and `update_detach_tree_cmd` / `update_attach_tree_cmd`. Their docstrings (which become the MCP descriptions) state that the flag changes only the sidebar tree display, and give the criterion of spec §5. Register them in `update_session/command.py` as `detach-tree` / `attach-tree`, update its module docstring and app help. In `update_sessions/command.py` add the two commands modelled on `_mute` / `_notify` with `kind="session:update_tree_detached"`. Make the `--tree-detached` help text of Task 3 and these docstrings use the same criterion sentence.
- [ ] **Step 4: Run** `uv run pytest tests/test_mcp_tools.py tests/test_tree_detached_cli.py tests/test_drop_transport.py -v` and `uvx ruff check src/twicc/cli`. Expected: PASS, no new lint error.
- [ ] **Step 5: Commit** `feat(cli): add detach-tree and attach-tree to update-session(s)`.

### Task 6: Frontend tree rule

**Files:**
- Modify: `frontend/src/utils/sessionTree.js` (reads at ~`:42`, `:48`, `:92-93`, `:270-272`, `:284`, `:290`, `:330`, `:339`; comments ~`:254`, `:315`), `frontend/src/utils/railActiveSessionsTree.js` (~`:11`, `:15`, `:67`, `:72`), comments in `composables/useRailActiveSessions.js:106` and `utils/sidebarSessions.js:64`
- Test: `frontend/src/utils/sessionTree.test.js`, `railActiveSessionsTree.test.js`, `composables/useRailActiveSessions.test.js`

**Interfaces:**
- Produces: `export function treeParentId(session): string | null` in `sessionTree.js`, returning `session.tree_detached ? null : session.spawned_by || null`.

- [ ] **Step 1: Write the failing tests.** `sessionTree.test.js`: `treeParentId` cases (attached, detached, no spawner); `buildSessionTree` with a detached child: it is a root, has no `hintParent`, and its spawner has no child (Review Focus 1); chain A → B (detached) → C: B is a root, C is B's child, and C does not resolve to A even when B is not a candidate (Review Focus 2); detached B with attached children keeps them (Review Focus 3); `extendTreeCandidates` and `findMissingAncestorIds` do not walk up through a detached session (Review Focus 1) and do not collect a detached session as a descendant of its spawner. `railActiveSessionsTree.test.js`: children index omits detached children; the ancestor walk stops at a detached session; a detached rail row shows its project mark (`showMarkDot` false) even with the same project as its spawner.
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js src/utils/railActiveSessionsTree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.** Add `treeParentId`. Replace every start-of-walk and step read (`session.spawned_by`, `current.spawned_by`, `childrenOf` keys, the `buildChildrenIndex` key) with `treeParentId(...)`. Keep `orchestrationView.js` and `stores/data.js` untouched. Update the five stale comments to say the tree parent. The walk keeps passing through loaded, non-blocking, non-candidate ancestors; a detached ancestor is added as a candidate (root) and the walk stops above it.
- [ ] **Step 4: Run** `node --test src/utils/sessionTree.test.js src/utils/railActiveSessionsTree.test.js src/composables/useRailActiveSessions.test.js src/utils/sidebarSessionsStability.test.js` then `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(sidebar): treat detached sessions as roots of the session tree`.

### Task 7: Store action and sidebar menu entry

**Files:**
- Modify: `frontend/src/stores/data.js` (new action after `setSessionPinMode` ~`:6239`), `frontend/src/components/session/list/SessionListItem.vue` (prop, menu block, `handleMenuSelect` ~`:432`, after the ephemeral guard `:445`), `frontend/src/components/session/list/SessionList.vue` (prop pass)
- Test: `frontend/src/components/session/list/SessionListItem.tree.test.js`, `SessionList.tree.test.js`, (the store action is inline in `data.js`, so its regex assertions on the `data.js` source go in `SessionListItem.tree.test.js`)

**Interfaces:**
- Consumes: Task 4 REST PATCH `{tree_detached: boolean}`.
- Produces: store action `setSessionTreeDetached(projectId, sessionId, value)`; `SessionListItem` prop `canDetachFromTree` (Boolean, default `false`).

- [ ] **Step 1: Write the failing tests.** `SessionList.tree.test.js`: the list passes `:can-detach-from-tree="showAsTree"` (not `treeActive`). `SessionListItem.tree.test.js`: the menu has an entry with value `detach-tree` shown when attached and `attach-tree` when detached, label texts and icon `sitemap` as in Global Constraints; it requires `canDetachFromTree`, `session.spawned_by`, and a regular session (not draft, ephemeral); `handleMenuSelect` branches call `store.setSessionTreeDetached(session.project_id, session.id, true|false)`. Store: optimistic update sets `session.tree_detached`, PATCHes `{tree_detached: value}`, calls `updateSessionFromServer`, and rolls back on error (same shape as `setSessionPinMode`).
- [ ] **Step 2: Run** `node --test src/components/session/list/SessionListItem.tree.test.js src/components/session/list/SessionList.tree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the store action as a copy of `setSessionPinMode` with `tree_detached`; the prop and one `wa-dropdown-item` (value `detach-tree` or `attach-tree` by state) placed after the mark-read / mark-unread items and immediately before the `archive` item (do not change `hasItemsAfterPinBlock`); `SessionList.vue` passes the prop on the row component. Check the icon `sitemap` needs no new import (it is registered already; confirm in `main.js`).
- [ ] **Step 4: Run** the two test files plus `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(sidebar): add detach and reattach to the session menu`.

### Task 8: Agent-facing documentation

**Files:**
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-create-session/session-behavior.md` (flag list, a `### --tree-detached` section, the "change after creation" line ~`:85`) and `twicc-create-session/SKILL.md` (~`:100`); `twicc-orchestration/SKILL.md` (~`:116-122`), and the spawning guidance of `twicc-orchestration-leader` and `twicc-orchestration-manager`; `twicc-update-session/` (`detach-tree.md`, `attach-tree.md`, `SKILL.md` lines for description, argument-hint, index); `twicc-update-sessions/` (same, plural wording); `twicc-sessions/list.md` (example + field list); `SKILLS-AND-CLI.md` (`create-session`, `update-session` list, `update-sessions` paragraph); `src/twicc/agent/system_prompt.py` (one sentence after the `twicc-create-session` sentence ~`:90`); `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (minor bump)
- Test: `tests/` suites that read skills or the system prompt, if any (`uv run pytest -k "skill or system_prompt or plugin" -q` to find and run them)

**Interfaces:**
- Consumes: the final flag and sub-command names (Global Constraints).

- [ ] **Step 1: Check** `src/twicc/mcp/descriptions.py` for hand-written text about mute/hide that needs the same criterion sentence (nothing to do if it has none). Read `src/twicc/agent/plugin/README.md` and the neighbouring `mute.md` / `hide.md` skill files for layout and style; the project memory says skills describe their own fields and avoid speculative examples.
- [ ] **Step 2: Write** the texts. The criterion sentence (same meaning everywhere): use `--tree-detached` when the new session has no topical link with you (you are a coordinator spawning independent work in other projects or on other subjects); leave it off when the child serves your own work. State the effect once: only the sidebar tree display changes; filiation, orchestration and reporting are unchanged. The system-prompt sentence says why/when only, without flag syntax. The `update-session(s)` pages say that detaching an existing child is possible later and that attaching restores the nesting.
- [ ] **Step 3: Bump** `plugin.json` by one minor version.
- [ ] **Step 4: Verify**: `grep -rn "tree-detached\|detach-tree\|attach-tree" src/twicc/agent SKILLS-AND-CLI.md` shows every file of the list; run the test selection of the Files block. Expected: PASS.
- [ ] **Step 5: Commit** `docs(skills): explain the tree-detached flag to agents`.

### Task 9: Full verification

- [ ] **Step 1: Run** `uv run pytest -q` and `uvx ruff check` on every Python file the plan touched. Expected: PASS, no new lint error.
- [ ] **Step 2: Run** `cd frontend && npm test`. Expected: PASS.
- [ ] **Step 3: Check the migration**: `git diff --stat <commit before Task 1>..HEAD -- src/twicc/core/migrations` lists only `0165_*`; read it once more.
- [ ] **Step 4: Report to the owner** the operations left to them: `migrate` and the dev-server restart. Browser check (owner or controller): tree mode on, detach a child from the menu, see it become a root in the list and the rail; reattach; see both rows in the session timeline; restore any browser state (setting off, `twicc.sessionTree.explicit` removed).
