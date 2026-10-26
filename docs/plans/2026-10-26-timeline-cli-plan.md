# Timeline CLI, RPC, and MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose cursor-paginated timeline reads with shared session filters through CLI, RPC, and both MCP contexts.

**Architecture:** Reuse `cli.sessions.build_filtered_queryset` with explicit eligibility parameters instead of copying filter logic. A dedicated read service selects and paginates events using that unsliced session queryset. A lazy Typer command supplies scope resolution and the existing generated RPC/MCP surfaces.

**Tech Stack:** Python 3.13+, Django 6 ORM, SQLite, Typer, orjson, pytest, pytest-django, existing RPC/MCP generators.

**Spec:** `docs/plans/2026-10-26-timeline-cli-spec.md` (commit `eaa7266ee`). Read the entire spec before execution.

**Metadata follow-up:** Keep event IDs and centralize standard serialized references in `sessions` and `projects`. Sessions match `twicc sessions`, with `--full` for every field. Projects and the selected workspace use their complete serialized forms. Batch metadata reads, reuse resolved scope model instances and process rows, and keep REST response shapes unchanged. Query-budget and transport checks live in `tests/test_timeline_enrichment.py` and `tests/test_timeline_transports.py`.

## Global Constraints

- English for code, documentation, comments, UI strings, and commit messages.
- Preserve unrelated changes. Stay on the current branch unless the Human explicitly requests isolation.
- Reuse existing session-filter logic, including filiation resolvers, annotation matching, and process-state selection.
- Extract shared logic where necessary instead of duplicating it for the timeline.
- Select matching sessions before filtering their events; never paginate sessions before paginating events.
- Exclude hidden sessions by default. Include archived sessions by default.
- Filiation filters implicitly include hidden sessions, matching `sessions`; `--only-hidden` narrows that selection.
- Keep the CLI default page size of 20 events. Maximum page size: 100. Reject limits outside 1–100.
- Order events by `occurred_at DESC`, then `id DESC`. Preserve UTC microseconds in cursors.
- No `offset`, `total`, `after_id`, or legacy `--paginated` argument on this command.
- Do not change existing `sessions`, `search`, REST timeline, or Timeline UI behavior.
- No new dependencies, migrations, event kinds, backfill, watch mode, or instance restart during this work.
- Backend tests: `cd /home/twidi/dev/twicc-poc && uv run pytest <paths>`. Never set `TWICC_DATA_DIR` for pytest.
- Lint changed Python files with `uvx ruff check <paths>`. Never install ruff into the project.
- Prefix shell commands with `cd /home/twidi/dev/twicc-poc &&`. Never use `uv pip` or `--active`.
- Commits use Conventional Commits, descriptive bodies, and the executing Codex model's exact `Co-Authored-By` trailer.
- Stage only files belonging to the task. Do not modify CHANGELOG.

## Review Focus

1. A hidden child in a spawn tree stays included, matching `sessions`; `--only-hidden` excludes visible children: Tasks 1 and 3 policy tests.
2. An empty workspace or unknown descendant target never becomes a global or instance-only result: Tasks 2–3 scope tests.
3. An old recorded event survives missing target metadata; an explicit metadata filter excludes it: Task 2 orphan tests.
4. Equal timestamps, inserted recent rows, and sparse matches cross page boundaries without skipping existing eligible events: Task 2 cursor tests.
5. Internal contextual IDs resolve through caller identity; remote and external callers cannot borrow the server session: Task 4 transport tests.

---

## Repository map and integration boundaries

| File | Responsibility/change |
|---|---|
| `src/twicc/cli/sessions.py` | Extend the existing filter builder with two backward-compatible eligibility parameters |
| `src/twicc/core/services/timeline_feed.py` | Publish existing keyset and cited-session helpers without changing REST behavior |
| `src/twicc/core/services/timeline_listing.py` | New event selection, cursor paging, and CLI response assembly |
| `src/twicc/cli/timeline.py` | New command orchestration, argument checks, scope resolution, and filter-builder call |
| `src/twicc/cli/__init__.py` | Lazy Typer registration; source for generated RPC and MCP schemas |
| `src/twicc/rpc/permissions.py` | Register `timeline` as a read-only route |
| `src/twicc/mcp/descriptions.py` | Concise timeline tool description within the client cap |
| `src/twicc/agent/plugin/twicc/skills/twicc-timeline/SKILL.md` | New agent-facing usage documentation |
| `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md` | Add a related-command link for timeline history |
| `SKILLS-AND-CLI.md` | Add the CLI/skill entry and document the cursor exception in the shared Paging section |

Reuse, without reimplementing: `projects.project_scope_ids`, `projects.expand_project_ids_with_worktrees`, `workspaces.read_workspaces`, `cli._session_selection.resolve_explicit_ids`, `cli._drop_request.whoami` resolvers, `cli._annotation_filters`, and `cli._process_state`.

`search` already reuses the filiation resolvers and annotation parser. Do not rewrite its Tantivy selection path.
Do not use `merge_session_scope_ids`: its explicit IDs are unioned with filters; timeline requires intersection.

The separate Timeline UI filter plan also touches `timeline_feed.py`. Re-read that file at execution time.
Preserve any concurrent filter additions; this plan only promotes existing read helpers there.

### Task 1: Make shared session selection support timeline eligibility

**Files:**
- Modify: `src/twicc/cli/sessions.py::build_filtered_queryset`
- Create: `tests/test_timeline_session_selection.py`
- Regression tests: `tests/test_cli_resolve_filters.py`, `tests/test_cli_annotation_filters.py`, `tests/test_cli_session_process_state.py`, `tests/test_cli_sessions_stop.py`, `tests/test_cli_sessions_wait_reply.py`

**Interfaces:**
- Preserve every existing argument and the `(queryset, process_rows)` return shape.
- Add keyword-only `root_only: bool = True` to `build_filtered_queryset`.
- `root_only=False` removes only the initial `type="session"` restriction.
- Preserve the shared hidden-session policy, including implicit inclusion during filiation queries.
- Existing `require_indexed=False` removes the first-message and creation-date eligibility requirements.
- Existing `archived=True` means include both archived and unarchived sessions.

- [ ] **Step 1: Write policy and regression tests.**

Build a root, a visible spawned child, a hidden spawned child, an archived child, and a provider-native subagent.
Include a root with `created_at=None` and `user_message_count=0`.
Use real ORM rows and existing resolver behavior, not a second fake tree implementation.

```python
def test_timeline_policy_preserves_filiation_visibility(tree):
    qs, _ = build_filtered_queryset(
        spawn_tree=tree.root.id, archived=True, require_indexed=False,
    )
    assert set(qs.values_list("id", flat=True)) == {
        tree.root.id, tree.visible.id, tree.hidden.id, tree.archived.id,
    }
```

Additional tests and exact outcomes:
- `test_default_policy_preserves_existing_sessions_selection`: default tree selection includes hidden children and excludes archived children and native subagents.
- `test_timeline_policy_can_select_unindexed_and_native_sessions`: `root_only=False, require_indexed=False, archived=True` includes visible native subagents and zero-message roots.
- `test_timeline_policy_visibility_flags`: hidden child stays included with `include_hidden=True`; `only_hidden=True` selects only that child.
- `test_timeline_policy_reuses_state_and_annotations`: annotations AND-combine; repeated states OR-combine; `active` includes `user_turn`.

- [ ] **Step 2: Run the new test module and confirm failure on the new keywords.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_session_selection.py -q`.

- [ ] **Step 3: Extend the existing builder with the root-only policy parameter.**

Keep the resolver calls, typed annotation parser, scope expansion, provider validation, and process-state code in their existing shared path.
Do not copy those blocks into the timeline command. Preserve lazy Django setup and return values.

- [ ] **Step 4: Run policy tests and the listed regression modules.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_session_selection.py tests/test_cli_resolve_filters.py tests/test_cli_annotation_filters.py tests/test_cli_session_process_state.py tests/test_cli_sessions_stop.py tests/test_cli_sessions_wait_reply.py -q`.
Expected: all pass, including unchanged existing caller defaults.

- [ ] **Step 5: Commit these files.**

Subject: `refactor(cli): support timeline eligibility in shared session selection`.
Body: explain the two opt-in policies and preservation of existing caller defaults.

### Task 2: Implement event selection and cursor pagination

**Files:**
- Modify: `src/twicc/core/services/timeline_feed.py`
- Create: `src/twicc/core/services/timeline_listing.py`
- Create: `tests/test_timeline_listing.py`
- Regression test: `tests/test_timeline_api.py`

**Interfaces:**
- Rename `_keyset(before)` to `before_condition(before)` and `_cited_session_ids(events)` to `cited_session_ids(events)` in `timeline_feed.py`; update its internal call sites.
- Reuse `parse_cursor(text)`, `format_occurred_at(value)`, and `serialize_event(event)` unchanged. Keep the compact REST `build_sessions_table` unchanged. Use the shared CLI session payload and process helpers for CLI references.
- Produce this sync read function in `timeline_listing.py`:

```python
def fetch_timeline_page(
    *, project_ids: tuple[str, ...] | None, session_id: str | None,
    selected_sessions: QuerySet, require_session_metadata: bool,
    include_instance: bool, limit: int, before: tuple[datetime, int] | None,
    known_projects: dict[str, Project] | None = None, known_sessions: dict[str, Session] | None = None,
    workspace: dict | None = None, full: bool = False, process_rows: dict | None = None,
) -> dict:
    ...
```

`None` project IDs means global. `()` means a strictly empty scope.
`selected_sessions` is an unsliced `Session` queryset from Task 1, with scope restrictions applied by Task 3.
The service returns `items`, `pagination`, `sessions` and `projects`, plus the complete `workspace` for workspace scope, as spec §8. Session payloads reuse the standard CLI projection and process serialization, with `full` controlling the projection.
Reuse known project/session rows from scope resolution. Fetch remaining project models in one grouped query after paging.
The reference tables share those loaded metadata rows; events keep only their recorded fields.

- [ ] **Step 1: Write event-level selection and paging tests.**

Create events directly in the hermetic test database, as `tests/test_timeline_api.py` does.
Use fixed UTC timestamps with microseconds. Never call the real asynchronous writer for read-service tests.

```python
def test_empty_project_scope_never_means_global(selected_sessions):
    page = fetch_timeline_page(
        project_ids=(), session_id=None, selected_sessions=selected_sessions,
        require_session_metadata=False, include_instance=False, limit=20, before=None,
    )
    assert page == {
        "items": [], "pagination": {"limit": 20, "has_more": False, "next_cursor": None},
        "sessions": {}, "projects": {},
    }
```

Add these named scenarios with these outcomes:
- `test_keyset_ties_microseconds_and_insertions`: traverse 12 events with repeated timestamps at limit 3; insert a newer event after page one. Original events appear once in descending `(occurred_at, id)` order. The new recent event does not enter older pages.
- `test_sparse_matches_fill_pages_before_lookahead`: interleave hidden/unselected events; pages contain up to `limit` eligible events, not post-filtered partial pages.
- `test_instance_rows_merge_once_at_their_time_position`: instance and scoped session events interleave across pages without duplicates.
- `test_instance_rows_survive_without_eligible_session_history`: baseline scope with only hidden sessions still returns instance events.
- `test_missing_target_metadata_is_preserved_only_without_metadata_filters`: orphan event survives baseline scope and default visibility; disappears with `require_session_metadata=True`.
- `test_subagent_events_are_not_implicitly_dropped`: a selected visible native subagent's recorded events survive a broad scope.
- `test_target_visibility_does_not_filter_actor_references`: visible target event caused by a hidden actor remains; cited hidden actor metadata is present.
- `test_event_fields_and_side_table_match_existing_serialization`: exact public field set, pending title behavior, absent metadata omission, cited creation-spawner lookup; no internal write fields.
- `test_terminal_and_empty_pages_have_null_cursor`: final and exhausted pages have `has_more=False` and `next_cursor=None`.
- `test_cursor_survives_deleted_boundary_event`: a valid boundary does not require that event row to exist.
- `test_listing_keeps_row_value_keyset`: inspect captured SQL for the new service, not only the REST feed; its boundary uses a row-value comparison, with a final database limit on each branch.

- [ ] **Step 2: Run the new tests and confirm missing-service failure.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_listing.py -q`.

- [ ] **Step 3: Publish the two existing helpers and implement `fetch_timeline_page`.**

Restrict session events by recorded project scope and exact `session_id` when present.
Select matching target IDs through `selected_sessions.order_by().values("id")` as a database subquery.
Do not materialize all matching IDs or expand them into a Python `IN` list.

Without required metadata, also admit events whose non-null target ID is absent from `Session` entirely.
Use absence from all sessions, not absence from the filtered selection. Known targets outside the selection must stay excluded.
With required metadata, admit only selected target IDs.

Read instance events separately only when `include_instance=True` and the project scope is not empty.
Apply `before_condition` and descending order to both branches. Fetch at most `limit + 1` from each.
Merge by event ID, sort by `(occurred_at, id)` descending, and retain the first `limit + 1` candidates.
This reuses the existing feed's bounded merge approach without calling `fetch_feed` or copying its different inclusion policy.

Return the first `limit` events. Derive `has_more` from lookahead.
When another page exists, format its boundary as `<six-digit UTC occurred_at>,<id>` using the last returned event.
Build cited-session metadata only from returned events, not the lookahead event.
Do not call `latest_event_id`, count all events, or introduce an offset envelope.

- [ ] **Step 4: Run the new tests and unchanged REST timeline tests.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_listing.py tests/test_timeline_api.py -q`.
Expected: all pass, including the existing row-value keyset query tests.
Inspect `EXPLAIN QUERY PLAN` for the new service on a multi-project fixture with selected-session and orphan predicates.
Confirm the shared keyset shape remains intact. Record any extra sorting in verification notes rather than claiming REST tests cover it.
Do not add a wall-clock threshold to the tests.

- [ ] **Step 5: Commit these files.**

Subject: `feat(timeline): add filtered cursor-paginated event reads`.
Body: explain metadata eligibility, bounded event paging, orphan history, and unchanged REST behavior.

### Task 3: Add timeline CLI orchestration and registration

**Files:**
- Create: `src/twicc/cli/timeline.py`
- Modify: `src/twicc/cli/__init__.py`
- Create: `tests/test_cli_timeline.py`

**Interfaces:**
- Produce `main(*, project: str | None = None, workspace: str | None = None, session: str | None = None, provider: str | None = None, annotation: list[str] | None = None, spawned_by: str | None = None, spawn_tree: str | None = None, descendants: str | None = None, siblings: str | None = None, state: list[str] | None = None, active: bool = False, include_hidden: bool = False, only_hidden: bool = False, include_archived: bool = False, limit: int | None = None, before: str | None = None, full: bool = False) -> None`.
- Main consumes canonical project IDs; the Typer wrapper uses `derive_project_id` exactly as other commands do.
- Produce `TimelineScope(NamedTuple)` in `cli/timeline.py` with `project_ids: tuple[str, ...] | None`, `session_id: str | None`, loaded `projects` and `sessions` metadata maps, and optional `workspace` metadata.
- Produce `resolve_timeline_scope(*, project: str | None, workspace: str | None, session: str | None) -> TimelineScope`.
- Use `emit_json` for the Task 2 response. Do not modify `emit_list` or existing listing envelopes.

- [ ] **Step 1: Write CLI contract and composition tests.**

Use the existing `rpc.invoker.invoke(argv)` capture seam for command assertions and `typer.testing.CliRunner` for actual stdout/help.
Use `transaction=True` only for tests crossing threads; ordinary sync selection tests can use the normal `db` fixture.

```python
def test_default_page_size_and_cursor_shape(db):
    result = invoke(["timeline"])
    assert result.exit_code == 0
    assert result.result["pagination"] == {
        "limit": 20, "has_more": False, "next_cursor": None,
    }
```

Named tests and assertions:
- `test_project_worktree_and_workspace_expansion`: main includes its worktrees; a worktree excludes parent and siblings; workspace deduplicates archived members and worktrees.
- `test_unknown_explicit_scope_errors_but_known_empty_scope_succeeds`: unknown project/workspace/session fail; empty workspace and zero-event session return an empty successful page.
- `test_exact_session_scope_includes_no_descendants_or_instance_events`: target's events only, including an explicit known subagent.
- `test_scope_and_session_filters_intersect`: project plus tree and session plus annotation/state each narrow; mismatches return empty.
- `test_all_filiation_filters_reuse_existing_membership`: root/middle/leaf spawn-tree, standalone root, proper descendants, direct children, siblings without target, unsupported `parent` forms, unknown target empty.
- `test_visibility_and_archived_defaults`: default excludes hidden, filiation includes them; include/only flags work; archived events remain with and without `--include-archived`.
- `test_annotations_use_existing_typed_operators`: equality, inequality, existence, absence, membership, typed values, and repeated-AND matching.
- `test_process_filters_use_current_state`: all five states, repeated OR, active with idle user_turn, no live backend, and backend PID isolation.
- `test_instance_policy_depends_on_supplied_filters`: baseline scopes include instance rows; each restrictive argument in spec §6 excludes them. Include explicit `--state` containing all five states.
- `test_invalid_arguments_fail_before_empty_scope_shortcuts`: conflicting scope/filiation/visibility/state flags, invalid provider/state/annotation, limits 0/-1/101, malformed cursor, `--offset`, `--after-id`, and `--paginated` fail even with an empty workspace.
- `test_local_session_keywords_resolve_or_fail_closed`: forced caller identity resolves `self` and `parent`; missing caller/parent produces an error.

- [ ] **Step 2: Run tests and confirm the command is not registered yet.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_cli_timeline.py -q`.

- [ ] **Step 3: Implement the command and lazy Typer registration.**

Register `@app.command()` with the exact public option names in spec §§4–7.
Keep the Python parameter `session` to match the documented RPC/MCP shape and existing `HOST_BOUND_PARAMS`.
Add clear per-option help for visibility, current-state semantics, cursor reuse, and compatibility-only `--include-archived`.
Import `cli.timeline.main` only inside the wrapper.

Validate conflicts, limit, and cursor before any empty-result shortcut.
Use the existing shared validation for filiation exclusivity where suitable; preserve ordinary CLI errors.
Unknown provider/state/annotation must still fail for empty scopes.

Bootstrap Django, then validate explicit project/session existence.
Resolve a single session through `resolve_explicit_ids([session])`; require the resulting session row to exist.
Resolve workspace membership through `read_workspaces`; use the shared project expansion helpers once.
Represent global as `None` and empty workspace as `()` throughout.

Call the shared builder with `archived=True` and `require_indexed=False`, preserving shared visibility rules.
Set `root_only=True` when any filiation argument is supplied, preserving existing root-session filiation membership.
Otherwise set `root_only=False`, so recorded native-subagent events remain eligible.
Forward provider, state, active, annotation, filiation, and visibility arguments unchanged.
Apply resolved project IDs and exact session ID to the resulting queryset before passing it to Task 2.
The compatibility flag `include_archived` never changes selection.

Compute `require_session_metadata` from supplied `session`, provider, nonempty annotation/state, any filiation, active, or only_hidden.
Compute `include_instance` as its negation, except that empty workspace forces false.
Use supplied arguments, not optimized filter results: all-five-state input remains an explicit metadata filter.
Catch existing `FeedError` from cursor parsing and emit the normal command error.

- [ ] **Step 4: Run the new CLI tests and existing listing tests.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_cli_timeline.py tests/test_cli_listing_envelope.py tests/test_cli_resolve_filters.py -q`.
Expected: all pass. Existing commands keep their four-key offset envelopes; timeline uses its separate cursor envelope.

- [ ] **Step 5: Commit these files.**

Subject: `feat(cli): expose timeline scopes and shared session filters`.
Body: describe scope validation, filter reuse, timeline defaults, and cursor output.

### Task 4: Verify generated RPC and internal/external MCP contracts

**Files:**
- Modify: `src/twicc/rpc/permissions.py`
- Modify: `src/twicc/mcp/descriptions.py`
- Create: `tests/test_timeline_transports.py`
- Modify: `tests/test_mcp_tools.py`
- Modify: `tests/test_rpc_auth.py`
- Regression tests: `tests/test_mcp_dispatch.py`, `tests/test_mcp_batch_contract.py`

**Interfaces:**
- Add `timeline` to `COOKIE_READONLY_COMMANDS`; existing MCP read-only metadata and batch eligibility derive from it.
- Add `MCP_DESCRIPTIONS["timeline"]` under the existing 1024-character cap including `EXTERNAL_DESCRIPTION_SUFFIX`.
- No custom RPC route, parallel MCP handler, or new transport-specific selection implementation.

- [ ] **Step 1: Write transport, discovery, and identity tests.**

Use actual generated registry/schema/argv paths and command invocation against the hermetic database.
Follow existing MCP tests for caller contexts. Patch external operation audit writes with the existing test writer seam if required.
Do not replace the timeline command with a result stub for parity tests.

```python
def test_timeline_is_discoverable_and_read_only():
    assert "timeline" in build_registry()
    tool = next(t for t in iter_mcp_tools() if t.name == "timeline")
    assert tool.annotations.read_only_hint is True
    assert "session" in tool.input_schema["properties"]
    assert "before" in tool.input_schema["properties"]
    assert "offset" not in tool.input_schema["properties"]
```

Additional exact outcomes:
- `test_explicit_arguments_match_across_transports`: the same filtered event page through local CLI capture, RPC `POST /rpc/timeline`, internal MCP, and external MCP has identical result payloads.
- `test_cursor_round_trip_across_transports`: page one via RPC, page two through MCP with returned cursor; exact older matching events, no duplicates.
- `test_cookie_read_scope_allows_timeline`: authenticated read cookie reaches timeline; write-route rejection remains unchanged.
- `test_batch_read_accepts_timeline`: actual batch validation accepts the new read and preserves its cursor result.
- `test_internal_mcp_resolves_caller_self_and_parent`: identity uses the MCP caller, not the backend process ancestry.
- `test_external_and_remote_require_explicit_ids`: `prepare_tool(..., external=True)` and `reject_host_bound(resolve_command(...))` reject contextual values for session and every filiation option. Explicit IDs work.
- `test_rpc_has_no_session_identity`: HTTP RPC does not bind a session identity. With no matching PID ancestry, contextual references fail through the existing resolver. Do not claim RPC has a host-bound rejection gate; only remote forwarding and external MCP do.
- `test_registry_renders_repeated_and_dash_prefixed_values`: repeated annotations/states, dash-prefixed project IDs, and cursors reach the command intact.

- [ ] **Step 2: Run these tests and confirm read-only eligibility fails before the allowlist change.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_transports.py tests/test_mcp_tools.py tests/test_rpc_auth.py -q`.

- [ ] **Step 3: Add the read-only declaration and concise MCP description.**

Reuse the already audited `session`, `spawned_by`, `spawn_tree`, `descendants`, and `siblings` names in `HOST_BOUND_PARAMS`.
No addition to that set is needed. If a test reveals a real gap, fix the existing common gate, not a timeline-only gate.
Do not add `timeline` to `ALWAYS_LOAD_PATHS`; normal deferred discovery applies.

- [ ] **Step 4: Run transport tests and existing authorization/batch regressions.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_transports.py tests/test_mcp_tools.py tests/test_rpc_auth.py tests/test_mcp_dispatch.py tests/test_mcp_batch_contract.py -q`.
Expected: all pass, with no new authorization exceptions.

- [ ] **Step 5: Commit these files.**

Subject: `feat(api): publish timeline reads through rpc and mcp`.
Body: describe generated transport parity, read-only availability, and caller identity checks.

### Task 5: Document agent usage and complete focused verification

**Files:**
- Create: `src/twicc/agent/plugin/twicc/skills/twicc-timeline/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md`
- Modify: `SKILLS-AND-CLI.md`
- Create: `tests/test_timeline_documentation.py`

**Interfaces:**
- New skill name `twicc-timeline`, with a description covering recorded event history and scope/filter selection.
- Match the sibling skills' MCP-first guidance and exact launch-mode-aware `How to invoke` instructions.
- Document result fields and every public option from Task 3. Bump the plugin version for skill changes: minor for a new skill, patch for documentation updates. No dependency change is needed.

- [ ] **Step 1: Write documentation contract tests.**

Follow `tests/test_sessions_wait_reply_documentation.py` for loading tracked skill files.
Assert command discovery links resolve to the new skill.
Assert the option inventory covers the actual public timeline options, including `--before` and compatibility-only `--include-archived`.
Assert documented output uses cursor fields and does not advertise offset pagination or hidden inclusion without filiation or visibility flags.
In `SKILLS-AND-CLI.md`'s shared Paging section, explicitly assert that timeline is named as the cursor-pagination exception.
The general text must no longer claim every listing accepts `--offset` or shares the four offset-pagination fields.

- [ ] **Step 2: Run the tests and confirm the missing skill failure.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_documentation.py -q`.

- [ ] **Step 3: Write documentation using the existing skill authoring conventions.**

Load the applicable skill authoring instructions before writing the new reusable skill.
Copy the executable-resolution procedure from the sibling TwiCC CLI skills without inventing a new launcher.
Update the shared Paging section of `SKILLS-AND-CLI.md`, not just the new command entry.
Explain `--before`, `has_more`, and `next_cursor` there, alongside the existing offset-based command contract.
Explain all four scopes, filter intersection, current metadata, orphan history, instance-event rules, and hidden actors in the side table.
Include examples for workspace annotation, middle-node spawn tree, exact hidden session, repeated states, and next cursor.
State explicit-ID requirements for external MCP/RPC/remote calls and contextual references for local/internal callers.

- [ ] **Step 4: Run all new modules and focused existing regressions together.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_timeline_session_selection.py tests/test_timeline_listing.py tests/test_cli_timeline.py tests/test_timeline_transports.py tests/test_timeline_documentation.py tests/test_timeline_api.py tests/test_cli_resolve_filters.py tests/test_cli_annotation_filters.py tests/test_cli_session_process_state.py tests/test_cli_sessions_stop.py tests/test_cli_sessions_wait_reply.py tests/test_cli_listing_envelope.py tests/test_mcp_tools.py tests/test_rpc_auth.py tests/test_mcp_dispatch.py tests/test_mcp_batch_contract.py -q`.
Expected: all pass in the same process, with no cache-dependent catalog differences.
Run ruff on changed Python files only. Inspect the final diff for unrelated changes and unchanged REST contracts.
No frontend build, server restart, migration, or hermetic LLM diagnostic is needed for these changes.

- [ ] **Step 5: Commit documentation and its contract tests.**

Subject: `docs(timeline): document cli filters and cursor pagination`.
Body: describe the new skill, discovery entries, and verification performed.

## Self-review and specification coverage

| Spec requirements | Owning tasks |
|---|---|
| §§1–3: shared read surface and boundaries | Tasks 3–5 |
| §4: scope expansion, contextual IDs, empty/unknown scopes | Tasks 2–4 |
| §5: existing filters, timeline defaults, current state | Tasks 1, 3 |
| §6: instance events, orphan metadata, target versus actor | Tasks 2–3 |
| §§7–8: cursor ordering, exact payload, side table | Tasks 2, 4 |
| §9: usable cross-surface examples | Task 5 |
| §10 acceptance criteria 1–14 | Tests assigned across Tasks 1–5 |

Each task has a failing-test step, implementation step, focused verification, and scoped commit.
Later tasks consume the exact earlier signatures. Existing defaults remain backward compatible.
Implementation choices above do not change the approved product scope.

## Adversarial plan review record

Status: two independent internal reviewers report PASS after correction and a second review pass.
Review scope includes feasibility, spec coverage, shared-code reuse, transport contracts, and test sufficiency.
No product implementation occurs during this review.

| Finding | Correction |
|---|---|
| RPC has no existing explicit host-bound rejection gate | Task 4 distinguishes the unbound RPC resolver from remote/external rejection |
| The global CLI pagination documentation claims every listing uses offset | Task 5 updates and tests the shared Paging paragraph, not just the timeline entry |
| The info commands reference describes slash/dollar commands, not CLI discovery | Task 5 uses the sessions skill's related commands and `SKILLS-AND-CLI.md` |
| Existing REST query tests do not verify the new listing service | Task 2 adds a listing SQL assertion and actual query-plan inspection |

Both final passes find no remaining substantive plan defects.
The Human still reviews this plan and selects the execution method before implementation starts.
