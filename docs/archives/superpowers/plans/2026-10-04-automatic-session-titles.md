# Automatic Session Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate the first session title in the backend, then update automatic titles without overwriting validated titles.

**Architecture:** A backend runner coalesces requests and runs at most two checks concurrently. Shared suggestion routing preserves the WebSocket behavior. Conditional database writes, an expiring provider echo record, and a post-push check protect validated titles within the specification's limits.

**Tech Stack:** Python ≥ 3.13, Django 6, asyncio, Channels, SQLite, existing hermetic Claude/Codex calls, Vue 3, Pinia, pytest, node:test.

**Spec:** `docs/plans/2026-10-04-automatic-session-titles-design.md`, revision 11.

## Global Constraints

- `title_origin`: `''` (legacy or unknown, **frozen**), `'auto'` (written by the automation, revisable), `'user'` (validated by the user, **frozen**).
- First check: **one** relevant message.
- Following checks: **at least 6** new relevant messages **and at least 15 minutes** since the last attempt.
- Closing check: **at least 3** new relevant messages since the last successful check; **time ignored**.
- Failed attempts update only `title_checked_at`; gathered messages remain available.
- Maximum global concurrency: **2 checks**. A session never runs two checks concurrently.
- Relevant input: `ItemKind.USER_MESSAGE`, `has_title_content`, excluding bare slash commands. Commands with arguments remain relevant.
- Source limits remain `EDGE_MESSAGES = 15` and `MAX_MESSAGE_CHARS = 2000`.
- Output limits remain 100 characters model-side, 200 helper-side, and 250 in the title column.
- Both `titleGenerationEnabled` and `titleAutoApply` must be true. Keep the existing keys and defaults.
- Use the exact V3b block from specification §6.2. Inject it only after a successful model check.
- No backfill, data migration, new setting, provider, model, dependency, lexical similarity rule, or automatic revalidation action.
- Generation stays outside the database write lock and outside background compute processes.
- Explicit title writes always set `'user'`, including confirmation of unchanged text.
- Provider changes preserve the existing origin, except the conditional `NULL` → value stamp to `'auto'`.
- Preserve the existing hermetic configuration and suggestion retries/fallback. Attribute no title-generation cost to sessions.
- No startup sweep and no delayed cadence timer. Requests evaluate at their execution wall clock.
- Keep ephemeral frontend suggestions. Keep manual dialog suggestions free of the current-title block.
- Quiet updates only: sidebar and header, no toast.
- Preserve all existing user changes. Stay on the current branch unless the user explicitly requests isolation.
- Write code, UI copy, comments, tests, documentation, and commit messages in English.
- The user manages migrations and dev server restarts. Do not run `migrate` or restart servers without an explicit request.

## Review Focus

1. A user restores the original title during generation: origin comparison must still discard the result. Task 6.
2. A pending title masks an automatic database title: the dialog must hide the automatic hint. Tasks 1 and 9.
3. A batch contains an automatic echo followed by another provider title: the unseen echo record must expire. Task 4.
4. Shutdown interrupts provider work: workers must stop, retain no loop-bound state, and never consume success counters. Task 7.
5. A malformed stored message accompanies valid messages: counting and prompt construction must share the same parsed list. Tasks 2 and 6.

---

## Execution Notes and Decisions

### Repository state

The following files already exist as user-owned, untracked files:

- `src/twicc/title_cadence.py`
- `tests/test_title_cadence.py`

Adopt them; do not replace them. Preserve the unrelated untracked design documents.

The inspected migration leaf is `0149_remove_redundant_message_index`.
The repository also contains its replacement squash, `0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index`.
Confirm the loader's leaf when implementing Task 1. Do not modify either existing migration.

### Additional wire metadata

The frontend currently cannot identify a server-side pending title.
Add a transient serialized Boolean, `has_pending_title`, from `get_pending_title(session.id)`.
Keep `title_origin` equal to the database origin; do not disguise a pending title by changing the serialized origin.
This adds no database field or user setting.

Add `title_origin` to `SESSION_LISTING_FIELDS` too. CLI/MCP slim output otherwise removes the new field.
The dialog receives the full payload; `has_pending_title` need not enter slim output.

### Commands and generation attempts

Claude's `extract_indexable_text_from_parsed` unwraps `<command-name>` and `<command-args>`.
`/rename` is absent from `_SYSTEM_SLASH_COMMANDS`; `/rename <title>` therefore counts under the specification's current definition.
Keep that behavior and test it. Do not reclassify commands or bump compute versions for this feature.
The live regression must confirm what the bundled CLI actually writes.

Both title providers currently allow two attempts, then routing may try the other provider.
Preserve those contracts. One logical check can therefore contain multiple hermetic calls on failure.
Specification §7.3's cost estimate describes the normal successful path, not a new single-attempt API.

### Tests and commits

Use `pytest.mark.django_db(transaction=True)` for scenarios that cross async ORM boundaries.
Use `asyncio.run` or `async_to_sync`, following existing tests; add no async test dependency.
Use events and controlled clocks for races. Do not use real sleeps or real providers in the normal suite.
Patch `read_synced_settings`, provider availability, generation, rename, broadcasts, and reindex requests in automation tests.
Reset runner state and echo records between tests. Use `provider_home` for JSONL fixtures.

Every task has a failing test, implementation, passing test, and commit step.
Use a descriptive commit body and the required `Co-Authored-By` trailer.
Resolve the exact executing Codex model from the environment at commit time. Do not copy this session's model into future commits.
Stage only the task's files. Tasks that adopt the two user-owned cadence files include them intentionally.
Do not commit the plan during the planning phase unless the user requests it.

## File Map

| File | Responsibility |
|---|---|
| `src/twicc/core/models.py` | Store origin and cadence state beside `title`. |
| `src/twicc/core/migrations/0150_session_automatic_titles.py` | Add three fields; adjust number if the leaf changes. |
| `src/twicc/core/serializers.py` | Expose origin and pending status; preserve origin in slim output. |
| `src/twicc/title_cadence.py` | Existing pure cadence rules; update clock documentation. |
| `src/twicc/title_transcript.py` | Exact stability block and prompt assembly. |
| `src/twicc/providers/helpers.py` | Ordered user-message extraction and extended generation interface. |
| `src/twicc/core/services/title_suggestion.py` | Shared generation routing and result contract. |
| `src/twicc/title_echo.py` | Main-process, expiring automatic push records. |
| `src/twicc/core/services/title_automation.py` | One check: gates, count, generation, conditional apply, side effects. |
| `src/twicc/title_auto_task.py` | Request coalescing, two workers, shutdown. |
| `src/twicc/providers/compute_base.py` | Split placeholder/provider maps in live and full compute. |
| `src/twicc/providers/live_sync.py` | Carry and merge IDs changed by live title application. |
| `src/twicc/providers/db_writer.py` | Broadcast other title targets after full compute apply. |
| `src/twicc/providers/claude_code/compute.py` | Provider echo guard before existing title protection. |
| `src/twicc/providers/claude_code/helpers.py` | Live-only protection registration; remove dead protection. |
| `src/twicc/providers/codex/titles.py` | Conditional origin stamp during boot title import. |
| `src/twicc/providers/sessions_watcher.py` | Discovery origin and post-sync requests. |
| `src/twicc/asgi.py` | WebSocket adapter for the shared suggestion service. |
| `src/twicc/agent/base_manager.py` | Pending user origin, broadcast, deliberate stop hook. |
| `src/twicc/core/services/session_update.py` | Explicit user origin and archive hook. |
| `src/twicc/views.py` | REST title broadcast and bulk archive hook. |
| `src/twicc/cli/run.py` | Runner startup and shutdown. |
| Both providers' `helpers.py` and `title_suggest.py` | Pass `current_title` through existing hermetic calls. |
| `frontend/src/utils/sessionTitle.js` | Pure dialog and automatic-request decisions. |
| `frontend/src/utils/sessionTitle.test.js` | Test decisions with node:test. |
| `frontend/src/composables/useAutoApplyTitle.js` | Keep only ephemeral automatic title writes. |
| `frontend/src/views/SessionView.vue` | Register/request ephemeral suggestions only. |
| `frontend/src/stores/data.js` | Keep ephemeral pending intent state; remove obsolete real-session assumptions. |
| `frontend/src/components/session/detail/SessionRenameDialog.vue` | Validation hint and unchanged-title Save. |
| `frontend/src/components/app/SettingsPopover.vue` | Rename switch to `Automatic titles`. |
| CLI descriptions, plugin skills, tips, `SKILLS-AND-CLI.md`, `CHANGELOG.md` | Describe the revised behavior and origin output. |
| `scripts/diagnose_automatic_titles.py` | Explicit, read-only, live Haiku/Luna regression. |

## Task 1: Store and Serialize Title State

**Files:**
- Modify: `src/twicc/core/models.py` near `Session.title`.
- Modify: `src/twicc/core/serializers.py` near `SESSION_LISTING_FIELDS` and `serialize_session`.
- Create: `src/twicc/core/migrations/0150_session_automatic_titles.py`.
- Create: `tests/test_title_state.py`.
- Modify: `tests/test_cli_session_payload.py`.

**Interfaces:**
- Produces: `Session.title_origin: str`, `Session.title_check_count: int | None`, `Session.title_checked_at: datetime | None`.
- Produces: full payload keys `title_origin: str`, `has_pending_title: bool`; slim payload key `title_origin: str`.
- Consumes: `get_pending_title(session_id: str) -> str | None` unchanged.

- [ ] **Step 1: Add schema and payload tests.**

```python
def test_new_title_state_defaults(db):
    project = Project.objects.create(id="title-state-project")
    session = Session.objects.create(
        id="title-defaults", project=project, provider=Provider.CODEX,
        file_path="title-defaults.jsonl",
    )
    assert (session.title_origin, session.title_check_count, session.title_checked_at) == ("", None, None)

def test_serializer_keeps_database_origin_when_pending(db):
    project = Project.objects.create(id="title-pending-project")
    session = Session.objects.create(
        id="title-pending", project=project, provider=Provider.CODEX,
        file_path="title-pending.jsonl", title="Automatic", title_origin="auto",
    )
    set_pending_title(session.id, "Chosen")
    payload = serialize_session(session)
    assert payload["title"] == "Chosen"
    assert payload["title_origin"] == "auto"
    assert payload["has_pending_title"] is True
    assert "title_check_count" not in payload
    assert "title_checked_at" not in payload
    assert slim_session(payload)["title_origin"] == "auto"
```

Clean pending state in fixture teardown. Add a migration test that creates a titled row in the predecessor schema.
Apply the new migration in the isolated test database; assert the old title remains and state reads `('', None, None)`.
Assert exactly three `AddField` operations and no `RunPython` operation.

- [ ] **Step 2: Run `uv run pytest tests/test_title_state.py tests/test_cli_session_payload.py -q`.** Expected: missing-field failures.
- [ ] **Step 3: Add the exact fields from §4 and one migration.**
Use `CharField(max_length=8, default='', blank=True)`, `PositiveIntegerField(null=True)`, and `DateTimeField(null=True)`.
Use the project's nullable-field conventions without changing defaults. Resolve the migration leaf through Django's loader.
- [ ] **Step 4: Serialize the new wire keys and extend the slim allowlist.** Keep counters private.
- [ ] **Step 5: Run the Step 2 command.** Expected: PASS; legacy titles remain frozen.
- [ ] **Step 6: Commit as `feat(titles): add automatic title state`.** Explain migration defaults and wire metadata in the body.

## Task 2: Adopt Cadence and Share Prompt/Input Construction

**Files:**
- Modify/adopt: `src/twicc/title_cadence.py`, `tests/test_title_cadence.py`.
- Modify: `src/twicc/title_transcript.py`, `tests/test_title_transcript.py`.
- Modify: `src/twicc/providers/helpers.py`.
- Modify: `src/twicc/providers/claude_code/helpers.py`, `src/twicc/providers/codex/helpers.py`.
- Modify: both providers' `title_suggest.py`.
- Modify: `tests/test_title_suggestion_client_failures.py`, `tests/test_title_output_validation.py`.

**Interfaces:**
- Produces: `build_title_prompt(system_prompt: str, source: str, current_title: str | None = None) -> str`.
- Produces: `BaseProviderHelpers.get_title_messages(self, session_id: str) -> list[str]`, synchronous, ordered by `line_num`.
- Preserves: `get_title_source(self, session_id: str) -> str | None`, now delegates to `build_title_source(get_title_messages(...))`.
- Extends: `generate_title(self, prompt: str, system_prompt: str, *, current_title: str | None = None) -> str | None`.
- Extends provider module functions and `_call_haiku`/`_call_codex` with the same keyword, preserving existing positional parameters.
- Consumes unchanged cadence exports: `TitleCheckState`, `rebased`, `title_check_due`, `closing_check_due`, `after_check`, `is_relevant_message`.

- [ ] **Step 1: Add prompt, forwarding, and extraction tests.**

```python
def test_prompt_without_current_title_is_byte_identical():
    assert build_title_prompt("Summarize: {text}", "Input") == "Summarize: Input"

def test_prompt_appends_exact_v3b_block():
    result = build_title_prompt("Summarize: {text}", "Input", "Session titles")
    assert result == "Summarize: Input\n\n" + EXPECTED_V3B_BLOCK.format(title="Session titles")

def test_bare_commands_and_commands_with_arguments():
    assert not is_relevant_message(" /compact ")
    assert is_relevant_message("/rename Session titles")
    assert is_relevant_message("/custom implement title updates")
    assert not is_relevant_message("... 🐈")
```

Copy `EXPECTED_V3B_BLOCK` verbatim from §6.2, independent of the implementation constant.
Test both providers' final hermetic prompt, with and without `current_title`.
Test valid Unicode text, empty input, malformed stored JSON, and Claude command XML normalization.
Assert one malformed row does not discard other valid messages or change their order.
Keep existing cadence boundary, failure, rebase, closing, and simulation tests intact.

- [ ] **Step 2: Run `uv run pytest tests/test_title_cadence.py tests/test_title_transcript.py tests/test_title_suggestion_client_failures.py tests/test_title_output_validation.py -q`.** Expected: new prompt/input tests fail.
- [ ] **Step 3: Add the V3b constant and `build_title_prompt`.** Replace only the duplicated prompt `.replace` operations.
- [ ] **Step 4: Add `get_title_messages` and propagate the optional keyword through both providers.**
Reuse the existing `SessionItem` query and `get_user_messages` parser. Do not filter slash commands in manual `get_title_source`.
Leave models, timeout bounds, retries, validation, and hermetic configuration unchanged.
- [ ] **Step 5: Update cadence docstrings to use the check's wall clock.** Mention closing and catch-up checks without message timestamps.
- [ ] **Step 6: Run the Step 2 command.** Expected: PASS with exact prompt compatibility and original cadence tests.
- [ ] **Step 7: Commit as `feat(titles): add stable automatic title prompts`.** Include the adopted cadence files explicitly.

## Task 3: Extract Suggestion Routing Without Changing WebSocket Replies

**Files:**
- Create: `src/twicc/core/services/title_suggestion.py`, `tests/test_title_suggestion_service.py`.
- Modify: `src/twicc/asgi.py`, `tests/test_title_suggestion_routing.py`.

**Interfaces:**
- Produces: `TitleSuggestionResult(NamedTuple)` with `suggestion: str | None`, `requested_provider: Provider`, `title_provider: Provider | None`, `error: str | None`.
- Produces: `async suggest_title(source: str | None, system_prompt: str, session_provider: Provider, *, title_model: str | None = None, no_fallback: bool = False, current_title: str | None = None) -> TitleSuggestionResult`.
- Moves unchanged constants: `TITLE_SUGGESTION_MODEL_PROVIDERS`, `TITLE_CAPABLE_PROVIDERS` from `asgi.py` into this service.
- Consumes: extended provider `generate_title` interface from Task 2.

- [ ] **Step 1: Add direct routing tests and retain all WebSocket payload assertions.**
Assert no source yields `no_prompt` without provider work.
Assert no running provider yields `no_provider_available`.
Assert `None`, empty output, and exceptions permit fallback; complete failure yields `generation_failed`.
Assert only explicit `noFallback: true` disables fallback in the WebSocket adapter.
Assert `haiku` routes Claude, `luna` routes Codex, and missing/unknown model follows the session provider.
Assert source extraction still uses the session provider, even when another provider generates.

```python
assert result.requested_provider == Provider.CODEX
assert result.title_provider == Provider.CLAUDE_CODE  # Codex failed; Claude succeeded.
assert result.error is None
assert generated_current_titles == ["Current", "Current"]
assert websocket_generated_current_titles == [None]  # Manual suggestion remains unbiased.
```

- [ ] **Step 2: Run `uv run pytest tests/test_title_suggestion_service.py tests/test_title_suggestion_routing.py -q`.** Expected: missing-service failure.
- [ ] **Step 3: Extract selection, availability, fallback, and exception handling into `suggest_title`.**
Keep input validation, source lookup, `sourcePrompt` behavior, and the single reply in `_handle_suggest_title`.
Call the shared service without `current_title` from the WebSocket path.
- [ ] **Step 4: Move generation/availability monkeypatch targets into the service tests and existing WebSocket harness.**
Keep `twicc.asgi.get_provider_helpers` patched for source reads. Extend fake generator signatures for the optional keyword.
- [ ] **Step 5: Run the Step 2 command.** Expected: all existing reply shapes and fallback tests PASS.
- [ ] **Step 6: Commit as `refactor(titles): share suggestion provider routing`.**

## Task 4: Add Bounded Automatic Echo Records and Fix Dead Protection

**Files:**
- Create: `src/twicc/title_echo.py`, `tests/test_title_echo.py`.
- Modify: `src/twicc/providers/claude_code/helpers.py`.
- Create: `tests/test_title_protection_lifecycle.py`.
- Reference only: `src/twicc/providers/claude_code/titles.py`, `src/twicc/providers/claude_code/agent/manager.py`.

**Interfaces:**
- Produces: `record_automatic_title_push(session_id: str, title: str) -> None`.
- Produces: `should_skip_automatic_title_echo(session_id: str, provider_title: str, *, title: str | None, title_origin: str) -> bool`.
- Record type: private immutable `NamedTuple(title: str, recorded_at: float)`; timestamp from `time.monotonic()`.
- Expiry: `AUTOMATIC_TITLE_ECHO_TTL_SECONDS = 600`.
- Existing `rename_session(session_id, title)` signature stays unchanged.

- [ ] **Step 1: Add record lifecycle tests with a fake monotonic clock.**

```python
record_automatic_title_push("s", "A")
assert should_skip_automatic_title_echo("s", "U", title="U", title_origin="user") is False
assert should_skip_automatic_title_echo("s", "A", title="U", title_origin="user") is True
assert should_skip_automatic_title_echo("s", "A", title="U", title_origin="user") is False
```

Test matching `'auto'` and equal-text `'user'` rows consume the record without skipping.
Test replacement `A` → `B`, expiry at 600 seconds, and unmatched `[A, U]` batching followed by a later legitimate `A`.
Test lazy pruning of expired records when recording and consulting, so inactive-session records do not accumulate indefinitely.
Test user, CLI, and pending writes never clear the record. Integration assertions follow in Tasks 5 and 6.

- [ ] **Step 2: Add protection lifecycle tests.**
Fake a manager `_agents` entry in `STARTING`, `ASSISTANT_TURN`, `USER_TURN`, and `DEAD`, then no entry.
Assert only non-DEAD entries register protection, including failed provider writes.
Assert a dead rename removes an older protected entry, including before the DEAD handler reads it.
Assert a later external differing `custom-title` can apply after a dead rename.

- [ ] **Step 3: Run `uv run pytest tests/test_title_echo.py tests/test_title_protection_lifecycle.py -q`.** Expected: missing-record and dead-protection failures.
- [ ] **Step 4: Implement the record API with matching consumption before the origin decision.**
Return true only for a matching, unexpired record when origin is `'user'` and database title differs.
Do not expose this state to background workers or serialize it.
- [ ] **Step 5: Change Claude `rename_session`'s `finally` block.**
Read the manager's current `_agents` entry at that point. Register protection only for a live entry; otherwise clear existing protection.
Preserve the existing hybrid paste/direct append behavior. Do not change the accepted read-to-rewrite death-window race.
- [ ] **Step 6: Run the Step 3 command.** Expected: PASS.
- [ ] **Step 7: Commit as `fix(titles): bound automatic echoes and dead protection`.**

## Task 5: Make Every Title Writer Respect Origin and Compute Races

**Files:**
- Modify: `src/twicc/providers/compute_base.py` around full maps/result/apply and live maps/apply.
- Modify: `src/twicc/providers/live_sync.py` near `LiveSyncUpdates`, `empty`, and `merge_live_updates`.
- Modify: `src/twicc/providers/db_writer.py` near `_process_compute_message` and its applied-result broadcasts.
- Modify: `src/twicc/providers/claude_code/compute.py` near `apply_session_title`.
- Modify: `src/twicc/providers/codex/titles.py` near `_apply_sync_session_titles_job`.
- Modify: `src/twicc/providers/sessions_watcher.py` near `create_session_sync` and live-result broadcasts.
- Modify: `src/twicc/core/services/session_update.py`, `src/twicc/views.py`, `src/twicc/agent/base_manager.py`.
- Create: `tests/test_title_origin_writers.py`, `tests/test_title_compute_apply.py`.
- Modify: `tests/test_compute_apply_signals.py` if its fixtures carry the old title-map shape.

**Interfaces:**
- Produces full compute keys: `placeholder_titles: dict[str, str]`, `provider_titles: dict[str, str]`; removes `titles`.
- Produces live maps with the same separate responsibilities.
- Extends `LiveSyncUpdates` and `ComputeApplyResult` with `title_updated_session_ids: tuple[str, ...] = ()` as the last field.
- Both result types carry only IDs whose database `title` or `title_origin` actually changes during the committed apply.
- `LiveSyncUpdates.empty()` and `merge_live_updates(left, right)` preserve the new field; merge deduplicates IDs in encounter order.
- Produces: `apply_placeholder_title(target_session_id: str, title: str) -> bool` on `BaseSessionCompute`.
- Preserves: `apply_session_title(target_session_id: str, title: str) -> bool` for provider-channel titles.
- Consumes: Task 4 echo API, consulted only in the main apply process.
- Preserves existing title validation, rename, serialization, and broadcast transport interfaces.

- [ ] **Step 1: Add writer tests for specification §4.1, including equal-text writes.**

| Test | Action | Database assertions |
|---|---|---|
| `test_rest_same_text_validates_title` | PATCH the session detail endpoint with `{title: 'Same'}`. | `title == 'Same'`, `title_origin == 'user'`. |
| `test_cli_same_text_validates_title` | Call `update_session_title_from_payload({'session_id': id, 'title': 'Same'})`. | `title == 'Same'`, `title_origin == 'user'`. |
| `test_pending_flush_validates_title` | Call `_try_flush_pending_title(agent, 'Chosen')` with mocked writeback. | `title == 'Chosen'`, `title_origin == 'user'`. |
| `test_discovery_stamps_automatic_origin` | Create a watcher row with `parsed.title == 'Provider'`. | `title == 'Provider'`, `title_origin == 'auto'`. |
| `test_placeholder_stamps_automatic_origin` | Apply a first-message placeholder to a NULL title. | `title == 'First message'`, `title_origin == 'auto'`. |

Read each result back from the database; do not assert only the caller's in-memory object.
Parameterize provider changes across `''`, `'auto'`, and `'user'`, with differing and equal values.
Assert existing origins remain unchanged. Test Codex boot import and watcher row creation separately.
Assert REST title-only PATCH broadcasts `session_updated`, including unchanged text that validates an automatic title.
Assert CLI and pending flush broadcasts contain the new origin, even if provider writeback fails after the database update.

- [ ] **Step 2: Add conditional compute and echo integration tests.**
Compute a placeholder while title is `NULL`; write a user title before applying the compute result.
Assert the placeholder changes neither title nor origin.
Test both live and full paths; assert full compute ships both maps and no merged `titles` key.
Test each of base hook, Claude override, and full provider-map apply against an automatic echo on a differing `'user'` row.
Assert the record is consumed before `check_protected_title`; a skipped echo must append no correction.
After consumption, assert an external `/rename` to that same text applies and origin remains `'user'`.
Test a provider title in session A targeting session B in both live and full compute.
Assert B appears in `title_updated_session_ids` and receives one `session_updated` with its new title and unchanged origin.
Assert source A retains its existing broadcast. No extra source broadcast is added through the new IDs.
Assert ignored echoes, blocked placeholders, missing targets, and unchanged title/origin pairs produce no new target IDs or broadcasts.
Test `LiveSyncUpdates.empty()` and merged slices; repeated B IDs produce one target broadcast for that merged result.

- [ ] **Step 3: Run `uv run pytest tests/test_title_origin_writers.py tests/test_title_compute_apply.py tests/test_compute_apply_signals.py tests/test_title_echo.py tests/test_title_protection_lifecycle.py -q`.** Expected: origin, map, and echo failures.
- [ ] **Step 4: Split both compute maps and implement conditional placeholder application.**
Use `filter(id=..., title__isnull=True).update(title=..., title_origin='auto')` as one statement.
Return whether a row changed. Apply provider titles after placeholders, preserving provider precedence.
Refresh the live in-memory title/origin after successful application; do not let later aggregate saves restore stale values.
- [ ] **Step 5: Guard provider applies, then preserve origin on existing titled rows.**
Consult the echo record before Claude protection and before the full provider-map update.
For `NULL` provider applies, use the same conditional stamp before the existing-title update branch.
Keep full compute's current behavior otherwise; do not introduce new full-path protection corrections.
Collect changed target IDs while applying titles; compare pre-apply and post-apply title/origin under the existing write serialization.
Return the IDs through `LiveSyncUpdates` and `ComputeApplyResult`; do not perform async broadcasts in synchronous compute hooks.
Keep existing source/ancestor broadcasts in their consumers. Add broadcasts for other changed targets after the committed apply.
In the watcher, handle these IDs outside the source session's visibility gate; hidden source A can rename visible target B.
In the database writer, handle these IDs only for an `'applied'` result, outside the apply lock.
Deduplicate source and `folded_ancestor_id` against target IDs; retain the existing visibility rules for each target's broadcast.
- [ ] **Step 6: Stamp discovery and Codex import only on `NULL` → value transitions.**
Set `kwargs['title_origin'] = 'auto'` with a parsed title during row creation.
Use conditional NULL stamping in the Codex apply job; never bulk-write a guessed origin over a titled row.
- [ ] **Step 7: Set `'user'` in REST, CLI/MCP service, and pending-title database writes.**
Update both fields under the existing write lock. Set REST `needs_broadcast` before its title branch so later initialization cannot erase it.
Broadcast after pending database persistence even when provider work subsequently fails.
Never clear the echo record from these paths. Preserve pending retry semantics listed as accepted limits in §4.3.
- [ ] **Step 8: Run the Step 3 command and `uv run pytest tests/test_codex_recompute_persistence.py tests/test_watcher_catch_up.py -q`.** Expected: PASS.
- [ ] **Step 9: Commit as `fix(titles): preserve title origin across all writers`.**

## Task 6: Implement One Automatic Check With Conditional Apply

**Files:**
- Create: `src/twicc/core/services/title_automation.py`, `tests/test_title_automation.py`.
- Consumes files from Tasks 1–5 without new public write APIs.

**Interfaces:**
- Produces: `async check_session_title(session_id: str, *, closing: bool = False) -> None`.
- Private predicates: `_is_eligible(session: Session, *, closing: bool) -> bool`, `_passes_pre_gate(session: Session, now: datetime, *, closing: bool) -> bool`.
- Private apply: `async _apply_check(snapshot: Session, state: TitleCheckState, *, title: str | None, now: datetime, closing: bool, succeeded: bool) -> str`.
- Apply result literals: `'discarded'`, `'failed'`, `'kept'`, `'changed'`.
- Private rebase write: `async _apply_rebase(snapshot: Session, state: TitleCheckState, *, closing: bool) -> bool`.
- Rebase returns whether the conditional count-only write succeeds; it does not mark an attempt or trigger side effects.
- Consumes `suggest_title` from Task 3, `get_title_messages` from Task 2, and `broadcast_session_updated`/`request_session_reindex`.
- The check coroutine includes all post-push work; callers must not detach those effects.

- [ ] **Step 1: Add eligibility and cheap pre-gate tests.**
Parameterize subagent/workflow agent, archived, hidden, stale, legacy titled row, validated row, pending title, missing row, and live hybrid.
Assert ineligible requests perform no message read, generation, counter write, or provider push.
Allow `spawned_by` sessions and legacy `NULL` titles. Missing providers are failures, not eligibility skips.
Test first live hybrid title allowed; later live hybrid updates skipped without consuming state; dead hybrids allowed.
Test both settings off independently. Ordinary requests enforce 6 messages and 15 minutes, first requests require one message and failure retry interval.
Closing requests require three messages, ignore time, and allow archived rows.
Test count shrink passes the pre-gate even with a negative difference; persist the rebased count without generating.
At exact count, slash-only and non-text rows must never trigger generation.

- [ ] **Step 2: Add successful, failed, and discarded checks with fake providers.**

```python
assert kept.title == "Current"
assert kept.title_check_count == 7
assert kept.title_checked_at == now
assert (broadcasts, reindexes, pushes) == ([], [], [])

assert changed.title_origin == "auto"
assert changed.title == "Session titles and provider sync"
assert (len(broadcasts), len(reindexes), len(pushes)) == (1, 1, 1)

assert failed.title_check_count == previous_count
assert failed.title_checked_at == now
assert failed.title == previous_title
```

Assert first title passes `current_title=None`; a later check passes the current title.
Assert case/punctuation changes count as changes; whitespace-only differences after trimming count as kept.
Test `None`, exceptions, timeout, rejected output, hermetic guard violation, no provider, and missing `{text}` as failures.
Test first failure retries after 15 minutes without losing collected messages.
Use `get_title_messages` once; derive both exact count and `build_title_source` from that same filtered list.

- [ ] **Step 3: Add races and post-push assertions using events.**
Pause generation, then change title, origin alone, automatic `A` → user `B` → user `A`, pending title, archive, or hybrid liveness.
Assert a discarded check writes no fields, not even attempt time.
Also recheck hidden/stale/subagent eligibility before apply; deleting the row safely discards it.
Repeat the race checks for failure and rebase-only writes so neither consumes stale state.
Assert generation occurs outside `run_under_db_write_lock`.
Assert the echo record exists before rename starts and survives rename failure.
If rename succeeds and the database title changes during it, assert one re-push of the database title.
If title is unchanged, assert no re-push. If rename raises, assert no post-push re-push.
Assert re-push never replaces the automatic echo record.
Assert broadcast/reindex failure is logged and does not prevent provider writeback after a committed change.

- [ ] **Step 4: Run `uv run pytest tests/test_title_automation.py -q`.** Expected: missing-service failures.
- [ ] **Step 5: Implement load, settings/eligibility gates, cheap pre-gate, and exact counting.**
Use `django.utils.timezone.now()` at evaluation, not a message timestamp.
For a missing `{text}`, record failure without reading the transcript once other cheap gates pass.
Use the manager for the session's provider and `_agents` to inspect hybrid liveness, including `STARTING`.
After `rebased`, evaluate ordinary/closing rules. A rebase alone changes only the count under the same conditional safeguards.
Use `_apply_rebase` when no check is due. Keep `title_checked_at`, title, origin, and provider state unchanged.
- [ ] **Step 6: Generate through `suggest_title`, then apply under the shared write lock.**
Compare database title and origin with the loaded snapshot. Re-read pending title and eligibility inside the lock.
Use a conditional ORM update for title/origin. Check its affected-row count before accepting the result.
Successful checks save exact relevant count and attempt time; failures save attempt time only.
Use `title_rejection_reasons` and the session provider's `validate_title` before applying fake or future-provider output.
- [ ] **Step 7: Complete changed-title effects outside the lock.**
Record the automatic push after the database commit and before starting provider rename.
Broadcast, request reindex, rename through the session provider, then re-read for one corrective push.
Log individual side-effect failures. Do not roll back the successful database check after provider write failure.
- [ ] **Step 8: Run the Step 4 command plus `uv run pytest tests/test_title_cadence.py tests/test_title_echo.py -q`.** Expected: PASS.
- [ ] **Step 9: Commit as `feat(titles): implement backend automatic checks`.**

## Task 7: Run Coalesced Checks With Two Workers

**Files:**
- Create: `src/twicc/title_auto_task.py`, `tests/test_title_auto_task.py`.
- Modify: `src/twicc/cli/run.py` near other task startup/shutdown.

**Interfaces:**
- Produces: `request_title_check(session_id: str, *, closing: bool = False) -> None`.
- Produces: `async start_title_auto_task(shutdown_event: asyncio.Event) -> None`.
- Consumes: `check_session_title(session_id, closing=...)` from Task 6.
- Private state: FIFO queue of IDs, queued/running membership, pending request flags merged with Boolean OR.
- Each running session can accumulate exactly one subsequent request; `closing=True` wins that merge.

- [ ] **Step 1: Add deterministic coalescing and concurrency tests.**
Block fake checks on events. Submit 100 repeated requests for one queued ID; assert one check.
Submit ordinary then closing while running; assert exactly one later closing check.
Submit multiple IDs; assert peak active count is 2 and per-ID peak is 1.
Keep the first check blocked during its provider/post-push phase; assert that ID cannot run again yet.
Raise from one check; assert worker continues to the next ID and membership is cleaned up.
Cancel at generation and post-push phases; assert no orphan worker remains.
Generation cancellation writes no success counters. Post-push cancellation preserves the already committed successful check without another write.
Start a fresh runner in another event loop; assert no old event, queue, membership, or shutdown flag prevents new requests.
Assert startup makes no requests and no session scan.

- [ ] **Step 2: Run `uv run pytest tests/test_title_auto_task.py -q`.** Expected: missing-runner failures.
- [ ] **Step 3: Implement two owned workers and queue merging.**
Requests only enqueue; they never create one task per session.
Initialize loop-bound primitives in `start_title_auto_task`, retain any pre-start requests, and reset state on shutdown.
Do not erase a merged closing request when a running ordinary check finishes.
Propagate cancellation and gather canceled workers in `finally`. Log ordinary worker exceptions.
- [ ] **Step 4: Wire startup and shutdown into `cli/run.py`.**
Start after database infrastructure is available and before live watcher triggers can need the runner.
Cancel the title runner before tearing down providers/database infrastructure; use existing `_cancel_task` conventions.
Do not add checks to the boot initial sync or search coordinator.
- [ ] **Step 5: Run the Step 2 command and `uv run pytest tests/test_title_automation.py -q`.** Expected: PASS.
- [ ] **Step 6: Commit as `feat(titles): schedule coalesced title checks`.**

## Task 8: Connect Live Sync and Deliberate Closing Events

**Files:**
- Modify: `src/twicc/providers/sessions_watcher.py`, `src/twicc/agent/base_manager.py`.
- Modify: `src/twicc/core/services/session_update.py`, `src/twicc/views.py`.
- Create: `tests/test_title_check_triggers.py`.
- Modify: `tests/test_watcher_catch_up.py` if shared watcher fixtures need request stubs.

**Interfaces:**
- Consumes: `request_title_check(session_id, closing=False)` from Task 7.
- Keeps all current HTTP, CLI, MCP, watcher, and stop APIs unchanged.

- [ ] **Step 1: Add hook tests with the request API replaced by a recorder.**
Both providers' incremental sync with indexed new lines requests an ordinary check for top-level sessions.
Assert request happens outside the write lock and after compute-readiness gating.
No request for deletion, no new lines, subagents, missing indexed session, or compute-not-ready early returns.
Test manual/force DEAD transitions for real rows; exclude ephemeral and every other kill reason.
Explicit exclusions: `archived`, `shutdown`, `startup-failed`, `apply-settings`, `switch-hybrid`, `cron_restart_timeout`, idle timeout, `cli-exit`.
Test `USER_TURN`/finished state alone triggers nothing.
Archive false → true requests closing only after awaited teardown, including already-dead sessions.
Repeated archive true → true and unarchive request nothing.
Bulk archive requests closing for each actually selected ID; dry run and excluded active IDs request nothing.
Startup initial sync requests nothing.

- [ ] **Step 2: Run `uv run pytest tests/test_title_check_triggers.py tests/test_watcher_catch_up.py -q`.** Expected: hook assertions fail.
- [ ] **Step 3: Add watcher requests after the indexed session passes compute readiness.**
Require `indexing.new_line_nums` and `indexed_session.type != SessionType.SUBAGENT`.
Do not make requests depend on Tantivy initialization or successful search indexing.
- [ ] **Step 4: Add manual/force DEAD requests after base manager cleanup.**
Use only `{'manual', 'force'}`; the broader `DELIBERATE_STOP_REASONS` includes `archived` and would duplicate the archive hook.
Check ephemeral before enqueueing. Queue synchronously; never await generation in manager callbacks.
- [ ] **Step 5: Add archive-transition and bulk-archive requests.**
Capture prior archived state before assigning it. Enqueue after existing agent/tmux teardown finishes.
For bulk archive, retain the existing raw update and enqueue each final ID outside the lock.
Keep cheap per-session closing gates in Task 6; bulk archive does not read transcripts itself.
- [ ] **Step 6: Run the Step 2 command and `uv run pytest tests/test_title_auto_task.py -q`.** Expected: PASS.
- [ ] **Step 7: Commit as `feat(titles): trigger checks on sync and deliberate close`.**

## Task 9: Transfer Real-Session Automation to the Backend

**Files:**
- Create: `frontend/src/utils/sessionTitle.js`, `frontend/src/utils/sessionTitle.test.js`.
- Modify: `frontend/src/composables/useAutoApplyTitle.js`, `frontend/src/views/SessionView.vue`.
- Modify: `frontend/src/stores/data.js` near pending auto-apply state and draft binding.
- Modify: `frontend/src/components/session/detail/SessionRenameDialog.vue`.
- Modify: `frontend/src/components/app/SettingsPopover.vue`.
- Modify: `tests/test_title_suggestion_client_failures.py` only if source fixtures assert removed real-session behavior.

**Interfaces:**
- Produces: `shouldRequestAutomaticTitle(session, { titleAutoApply, titleGenerationEnabled }) -> boolean` (ephemeral only).
- Produces: `showAutomaticTitleHint(session) -> boolean` (`title_origin === 'auto'` and no `has_pending_title`).
- Produces: `buildSessionTitlePatch(title) -> { title: string }` (trimmed; called after existing validation).
- Keeps `registerPendingTitleAutoApply`, `clearPendingTitleAutoApply`, `setDraftTitle`, and `renameSession` APIs.

- [ ] **Step 1: Add pure decision and source wiring tests using node:test.**

```javascript
const enabled = { titleAutoApply: true, titleGenerationEnabled: true }
assert.equal(showAutomaticTitleHint({ title_origin: 'auto' }), true)
assert.equal(showAutomaticTitleHint({ title_origin: 'auto', has_pending_title: true }), false)
assert.equal(showAutomaticTitleHint({ title_origin: 'user' }), false)
assert.equal(showAutomaticTitleHint({ title_origin: '' }), false)
assert.deepEqual(buildSessionTitlePatch(' Current '), { title: 'Current' })
assert.equal(shouldRequestAutomaticTitle({ ephemeral: true }, enabled), true)
assert.equal(shouldRequestAutomaticTitle({ draft: true }, enabled), false)
assert.equal(shouldRequestAutomaticTitle({ draft: false }, enabled), false)
```

Test either setting off, suggestion failure cleanup, and a manually titled ephemeral session retaining its title.
Use existing source-reading test conventions to assert `handleNeedsTitle` uses the helper and retains its disabled-settings dialog branch.
Assert the automatic watcher contains no `renameSession` call and ignores/clears accidental non-ephemeral pending entries before mutation.
Assert real Save uses PATCH even for unchanged text; draft/ephemeral Save retains local persistence.
Assert the dialog does not add a deep/title-property watch that resets typed input on server updates.

- [ ] **Step 2: Run `cd frontend && node --test src/utils/sessionTitle.test.js src/utils/titleSuggestion.test.js`.** Expected: new helper assertions fail.
- [ ] **Step 3: Add the pure helpers and wire them into the dialog and `SessionView`.**
When both settings are on, real sessions do nothing in `handleNeedsTitle`; only ephemeral sessions request/register suggestions.
When automation is off, retain `openRenameDialog({ showHint: true })` exactly.
Use the patch helper in the real Save path without changing `renameSession`'s existing signature.
- [ ] **Step 4: Remove the real-session branch from `useAutoApplyTitle`.**
Apply locally and call `setDraftTitle` only for ephemeral sessions. Keep failure/manual-title handling and pending cleanup.
Remove unused imports/metadata reads. Update store comments and avoid transferring ephemeral auto-apply intents to canonical real sessions.
Do not remove the manual draft-title bridge; it still becomes a pending `'user'` title in the backend.
- [ ] **Step 5: Add the automatic hint and change the setting label.**
Hint copy: `This title is automatic. Save to validate it and stop automatic updates.`
Place it in the existing hint area. Hide it when `has_pending_title` is true.
Save always validates a real title; Cancel changes no state. Preserve current shallow session-reference watch behavior.
Label copy: `Automatic titles`. Keep `titleAutoApply` as the bound setting.
- [ ] **Step 6: Run `cd frontend && npm test` and `uv run pytest tests/test_title_suggestion_client_failures.py -q` from the repository root.** Expected: PASS.
- [ ] **Step 7: Commit as `feat(titles): use backend automation for real sessions`.**

## Task 10: Document and Validate the Delivered Behavior

**Files:**
- Modify: `src/twicc/cli/settings/_keys.py`.
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-update-session/title.md`.
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md`.
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`.
- Modify: `SKILLS-AND-CLI.md`, `frontend/public/tips/title-suggestions.md`, `frontend/public/tips/rename-sessions.md`.
- Modify: `CHANGELOG.md`, **Unreleased only**.
- Create: `scripts/diagnose_automatic_titles.py`, `tests/test_automatic_title_diagnostic.py`.

**Interfaces:**
- Diagnostic CLI: `--provider haiku|luna|all`, `--input PATH`, `--output PATH`, `--live`, `--yes`.
- Input JSON: `{cases: [{id, messages: [str], check_counts: [int], expected_subjects: [str]}]}`.
- Output JSON: per-check provider/title/kept/error/latency and aggregate call count, keep fraction, subject coverage notes.
- Diagnostic uses `build_title_source`, `build_title_prompt`, and existing hermetic calls; it never writes Session rows or provider names.

- [ ] **Step 1: Add diagnostic tests with fake model responses.**
Assert absent `--live --yes` never calls a provider.
Assert each session chains the previous accepted title and source prefixes at `check_counts`.
Assert empty/invalid results are counted as failures; successful same-text results count as kept.
Assert input/output use orjson and require no new package.
- [ ] **Step 2: Run `uv run pytest tests/test_automatic_title_diagnostic.py -q`.** Expected: missing-script failures.
- [ ] **Step 3: Implement the diagnostic with existing hermetic calls and output validation.**
Use a read-only fixture of about 100 logical Haiku checks over short, stable, and pivoting conversations.
Keep fixtures and raw results in the executing session's scratch folder; place the review report in its artifacts folder.
Use the same fixture for Luna comparison. Do not reuse live session IDs as write targets.
- [ ] **Step 4: Update documentation and plugin version together.**
Describe origin values in session output and explain that explicit CLI/MCP renames freeze automatic updates.
Keep the create-session skill's existing requirement to supply a concise title, and explain that supplied titles are validated.
Describe omitted-title behavior for human/other clients: backend automation when enabled, including agent-created eligible sessions.
Replace stale first-message-only wording for `titleGenerationEnabled` and explain first-title/update behavior for `titleAutoApply`.
Document cadence, closing checks, legacy freeze, live hybrid restrictions, ephemeral behavior, and Save validation in the tips.
At inspection, plugin version is `0.107.2`; increment the current patch at implementation time, normally to `0.107.3`.
Re-read the top of `CHANGELOG.md` before adding an Unreleased entry; never edit a dated release.
- [ ] **Step 5: Run the normal verification set.**

```bash
uv run pytest tests/test_title_state.py tests/test_title_cadence.py tests/test_title_transcript.py tests/test_title_suggestion_service.py tests/test_title_suggestion_routing.py tests/test_title_suggestion_client_failures.py tests/test_title_output_validation.py tests/test_title_echo.py tests/test_title_protection_lifecycle.py tests/test_title_origin_writers.py tests/test_title_compute_apply.py tests/test_title_automation.py tests/test_title_auto_task.py tests/test_title_check_triggers.py tests/test_automatic_title_diagnostic.py tests/test_cli_session_payload.py tests/test_compute_apply_signals.py tests/test_codex_recompute_persistence.py tests/test_watcher_catch_up.py -q
cd frontend && npm test
```

Run each command from its required directory. Expected: PASS without real provider calls.
Check `git diff --check`. Verify imports, map keys, request hooks, and documentation references with `rg`.
No mandatory full-suite or lint pass; expand testing only for observed failures or changed adjacent behavior.
No frontend standalone bundles change, so the special standalone-bundle rebuild rule does not apply.

- [ ] **Step 6: Run the Haiku regression before shipping.**

```bash
uv run python scripts/diagnose_automatic_titles.py --provider all --input <scratch-fixture.json> --output <scratch-results.json> --live --yes
```

Compare approximately 100 checks per provider on the same chained fixtures.
Review keep fraction against Luna's measured 72% reference without treating noisy rates as an exact pass threshold.
Inspect stable-conversation rewrites and main-subject coverage at pivots; the original main subject must remain represented.
Do not ship if Haiku shows repeated needless changes or misses substantive pivots.
If tuning is necessary, document the provider-specific block, repeat the paired regression, and pin both prompts in tests.
No tuning should silently change the default Luna V3b block.
If the model constants, Codex runtime, vendored SDK, or Claude SDK change during this work, also run:
`uv run python scripts/diagnose_hermetic_llm.py --provider all --live --yes`.
Do not ship on `FAIL` or `INCONCLUSIVE` from that required diagnostic.

- [ ] **Step 7: Perform the manual product checks after the user restarts the running instance.**
Tell the user that `devctl.py` restart applies pending migrations at startup; do not apply migrations yourself.
Use fresh test sessions; never modify existing legacy sessions for validation.
Confirm a first backend title appears with no session tab open.
Confirm no check at only five new messages or under 15 minutes; confirm a check with six messages and elapsed interval.
Confirm same-text Save freezes an automatic title across later messages and updates a second tab.
Confirm three new messages cause a closing check on manual stop/archive, but `cli-exit` does not.
Confirm a live hybrid gets its first title but no update paste; confirm dead hybrid updates work.
Inspect the bundled Claude `/rename` JSONL shape; confirm it counts only if classified `USER_MESSAGE` and has arguments.
Confirm ephemeral suggestions, pending-title hint suppression, Cancel, and typed dialog input preservation.
Record any unperformed live check as an explicit delivery limitation, not a passing result.
- [ ] **Step 8: Commit as `docs(titles): document and validate automatic titles`.** Include regression evidence in the body or linked report.

## Specification Coverage and Delivery Gates

| Specification area | Tasks |
|---|---|
| Data fields, defaults, serialization, no backfill (§4) | 1 |
| Explicit writers and discovery origins (§4.1) | 5 |
| Conditional live/full placeholder maps (§4.1) | 5 |
| Echo record, expiry, consumption order (§4.2) | 4, 5, 6 |
| Post-push correction, dead protection (§4.2) | 4, 6, 7 |
| Existing writeback limits (§4.3) | Constraints and Tasks 4–6; deliberately unchanged |
| Cadence, cheap gate, rebase (§5) | 2, 6 |
| Exact stability prompt, first/manual exclusions (§6) | 2, 3, 6 |
| Shared routing and check pipeline (§7) | 3, 6 |
| Coalescing, concurrency, backend lifecycle (§7.2) | 7 |
| Watcher, stop, archive, bulk archive hooks (§8) | 8 |
| Eligibility, hybrid, spawned/legacy/ephemeral sessions (§9) | 5, 6, 8, 9 |
| Real frontend removal, dialog, master switch (§10) | 1, 5, 9, 10 |
| Edge cases and normal test requirements (§11–12) | 1–9 |
| CLI/MCP documentation, Haiku, `/rename`, migration leaf (§13) | 1, 2, 9, 10 |

Tasks depend on earlier interfaces; execute in order.
One plan is appropriate because the database writers, backend runner, and frontend transfer form one title lifecycle.
Do not deliver an intermediate state where frontend automatic PATCHes classify new backend titles as `'user'`.
The planning phase writes this document only. Implementation requires the user's separate invitation.

## Adversarial Review Record

Date: 2026-10-04. Four internal subagents perform two review rounds.
Each reviewer receives this plan, the specification path, and access to the current source.
Each reviewer knows that specification revision 11 already passes through ten adversarial design review rounds.
Reviewers treat the specification's accepted limits as authoritative. They review the implementation plan without reopening settled design decisions.

| Round | Independent scope | Verdict | Verified result |
|---|---|---|---|
| 1 | Backend contracts, races, runner, compute, tests | NEEDS_CHANGES | Cross-session title targets lack a defined broadcast transport contract. |
| 1 | Full specification coverage, frontend, serialization, migration, diagnostics | NEEDS_CHANGES | The plan names nonexistent `SLIM_SESSION_FIELDS` instead of `SESSION_LISTING_FIELDS`. |
| 2 | Fresh backend review of the corrected plan against source and specification | PASS | No actionable finding. |
| 2 | Fresh complete review of the corrected plan against source and specification | PASS | No actionable finding. |

### Applied Corrections

- Task 1 names the existing `SESSION_LISTING_FIELDS` projection contract.
- Task 5 carries changed title-target IDs through `LiveSyncUpdates` and `ComputeApplyResult`.
- Task 5 defines merge/deduplication, consumer broadcasts, visibility handling, and live/full target tests.
- Self-review corrects the watcher method name to `create_session_sync` and defines the count-only `_apply_rebase` interface.

The second round confirms the corrected plan's contracts, task order, tests, and specification coverage.
These are document reviews and source inspections. Product code, runtime tests, and live provider calls are not executed during this review loop.
