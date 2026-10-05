# Codex Stream Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace each persisted Codex text or reasoning item without leaving its streaming block beside it.

**Architecture:** Frontend provider helpers match durable identities. Store lifecycle actions perform one shared retirement operation for live and fetched items. Remove the backend FIFO after the frontend no longer needs it.

**Tech Stack:** Vue 3, Pinia, JavaScript ES modules, node:test, Django 6, pytest, Channels, the existing Codex SDK.

**Spec:** [Codex stream retirement through durable item identity](2026-10-05-codex-stream-retirement-spec.md).

**Status:** Draft implementation plan. Product implementation requires explicit user authorization.

## Global Constraints

- Stay on the current branch. Do not create a branch or worktree without an explicit request.
- Preserve every existing user-owned change. Stage only files changed by this task.
- Write code, comments, test names, and documents in English.
- Do not update the changelog.
- No dependency installation, database migration, JSONL rewriting, or SDK/runtime upgrade.
- Do not change buffer scheduling, Markdown rendering, display modes, or connection-reset policies.
- Use `getParsedContent()`, `setParsedContent()`, and `clearParsedContent()` for item content access.
- No FIFO fallback, `stream_uuid` authority, text comparison, timestamp comparison, or replacement timeout.
- Preserve Claude's message-plus-completed-block UUID matching.
- Keep provider-specific payload logic in provider helpers and their pure provider modules.
- Preserve the existing scroller retirement hook and `{ streamingLineNum, realLineNum }` result.
- Do not start or restart servers without explicit user authorization. Existing fixtures need only the frontend server.
- Backend changes require a user restart through `devctl.py` after delivery.

## Review Focus

1. Metadata becomes available after streaming starts: unresolved provider state must not crash or retire another provider's item.
2. Content hydrates after its end event: both full-range loading and content-only loading must retire the block.
3. A completed old-message start arrives during a new message: the current block and buffer must remain intact.
4. REST fails while thinking details are open: the retry must retain and transfer detail and group state.
5. A delayed buffer publication runs after retirement: it must not publish a stale row or alter a newer block.

Tests for these cases belong to Task 2. Task 3 verifies the rendered loading and scroll behavior.

## Repository facts and implementation decisions

- `data.js` already imports `getProviderHelpers()` from the provider registry.
- Provider helpers use extensionless imports. Node cannot directly load their complete module graph.
- Existing store tests execute actual action source through the extraction pattern in `streamingRows.test.js`.
- Use small pure provider modules for matching tests. Provider classes delegate to those exact functions.
- `loadSessionItemsRanges()` calls `addSessionItems()` after a full-range REST response.
- Initial `SessionItemsList.vue::loadSessionData()` also uses `updateSessionItemsContent()` after metadata initialization.
- Therefore, the spec's REST guarantee requires retirement in both store actions, not only `addSessionItems()`.
- Initial loading currently calls `clearEndedStreamingBlocks()` before fetching. Remove that call and obsolete action.
- `invisibleStreaming.js` mounts production conversation components, including the retirement scroll hook.
- Its Codex fixtures currently omit durable IDs and inject `stream_uuid`. Update them before browser acceptance.
- Do not run the historical baseline preparation scripts. They target a different worktree and write baseline source copies.

## File structure

| File | Change |
|---|---|
| `frontend/src/providers/baseHelpers.js` | Add the neutral matching method |
| `frontend/src/providers/codex/streamMatching.js` | New pure Codex matching function |
| `frontend/src/providers/claude_code/streamMatching.js` | New pure Claude matching function |
| Both providers' `helpers.js` | Delegate the matching method to the pure function |
| Both providers' `streamMatching.test.js` | New matching contract tests |
| `frontend/src/stores/data.js` | Shared identity matching, start/end lifecycle, both content-ingestion paths |
| `frontend/src/stores/streamRetirement.test.js` | New production-action lifecycle tests |
| `frontend/src/stores/streamingRows.test.js` | Convert Codex retirement fixtures and inject provider helpers |
| `frontend/src/components/session/detail/SessionItemsList.vue` | Remove deletion before REST loading |
| `frontend/tests/browser/invisibleStreaming.js` | Correct Codex identities in existing fixtures |
| `frontend/tests/browser/streamRetirement.html` | New isolated browser fixture entry point |
| `frontend/tests/browser/streamRetirement.js` | Controlled event sequences with production conversation components |
| `frontend/tests/browser/streamRetirementHarness.js` | Bounded scenario orchestration and acceptance predicates |
| `frontend/tests/browser/streamRetirementFixture.test.js` | Harness failure and sequencing tests |
| `src/twicc/providers/codex/agent/agent.py` | Remove FIFO operations and obsolete explanations |
| `src/twicc/providers/codex/helpers.py` | Remove wire enrichment override |
| `src/twicc/providers/helpers.py` | Remove unused wire enrichment hook |
| `src/twicc/providers/sessions_watcher.py` | Remove wire enrichment call |
| `src/twicc/providers/codex/streaming_registry.py` | Delete |
| `tests/test_codex_stream_retirement.py` | New SDK event and durable-item transport tests |

## Task 1: Define and test provider identity matching

**Files:** Provider matching modules, matching tests, `baseHelpers.js`, and both provider `helpers.js` files above.

**Interfaces:**

- Produce `matchesStreamingBlock(parsed, itemKind, messageId, block): boolean` in each pure provider module.
- Produce the same method on `BaseProviderHelpers`, `CodexHelpers`, and `ClaudeCodeHelpers`.
- `block` supplies `blockType`, `blockIndex`, and `uuid`. The method never mutates these values.
- Consume `completedItem()` from `codex/canonical.js` for canonical message envelope validation.
- Base behavior returns `false`; provider overrides delegate to the pure modules.

- [ ] **Step 1: Write matching tests in both `streamMatching.test.js` files.**

Use `messageId = 'item-A'`. Codex text has `payload.item.id = 'item-A'` in an `AgentMessage` completion.
Codex thinking has `payload.id = 'item-A'` in a `response_item.reasoning` record.
Claude has `message.id = 'message-A'` and `uuid = 'block-A'`.

| Test name | Assertions |
|---|---|
| `codex matches persisted text before and after end` | True with text block UUID `null` or `'item-A'` |
| `codex matches the visible reasoning row` | True only for response-item reasoning and a thinking block |
| `codex rejects canonical reasoning duplicates` | False for canonical `Reasoning`, including a matching ID |
| `codex rejects inconsistent identity and type` | False for wrong kind, block type, envelope, message ID, or populated UUID |
| `codex rejects malformed identifiers without throwing` | False for missing ID, `''`, number, array, object, or malformed envelope |
| `claude waits for a matching completed block` | False with null UUID; true only with equal message ID and block UUID |
| `claude isolates content blocks` | Completing block A does not match block B in the same message |
| `claude preserves eligible item kinds` | Preserve `assistant_message`, `content_items`, and `reasoning` gates from the current store |
| `matching does not mutate its inputs` | Parsed input and block remain equal to their pre-call snapshots |

Example assertion shape:

```js
assert.equal(matchesStreamingBlock(parsedText, 'assistant_message', 'item-A',
    { blockIndex: 0, blockType: 'text', uuid: null }), true)
assert.equal(matchesStreamingBlock(parsedText, 'assistant_message', 'item-A',
    { blockIndex: 0, blockType: 'text', uuid: 'item-B' }), false)
```

- [ ] **Step 2: Run the matching tests and confirm the expected missing-module failure.**

Run: `cd frontend && node --test src/providers/codex/streamMatching.test.js src/providers/claude_code/streamMatching.test.js`.
Expected: FAIL because the production matching functions do not exist yet.

- [ ] **Step 3: Implement the Codex pure matching function.**

Require non-empty string identities, the table's envelope and kind, and the matching text/thinking block type.
Require `messageId` equality; if `block.uuid` is populated, require its equality too.
Never use `stream_uuid` or canonical `Reasoning` debug duplicates.

- [ ] **Step 4: Implement the Claude pure matching function.**

Preserve the current kind gate and message-plus-populated-UUID check. Do not use Codex's early-retirement rule.

- [ ] **Step 5: Add the base method and both provider delegations.**

Pure matching modules must not import stores, composables, routers, or the provider registry.
Keep the production helper adapters thin. Do not add a second store-level payload parser.

- [ ] **Step 6: Run the matching tests and confirm all assertions pass.**

Use the command from Step 2. Check actual helper delegation during Task 3's production-store fixture.

- [ ] **Step 7: Commit this tested provider contract.**

Stage only Task 1 files. Subject: `refactor(streaming): define provider replacement matching`.
Include a body describing durable Codex identity and preserved Claude completion matching.
Use the actual executing model for the required Co-Authored-By trailer.

## Task 2: Apply matching to streaming and both REST ingestion paths

**Files:** `data.js`, `streamRetirement.test.js`, `streamingRows.test.js`, and `SessionItemsList.vue`.

**Interfaces:**

- Consume Task 1's provider matching method.
- Preserve public `streamBlockStart`, `streamBlockDelta`, `streamBlockStop`, and `streamBlockEnd` signatures.
- Preserve `_retireStreamingBlocks(sessionId, items)` and its retired-pairs array.
- Add `_findStreamingReplacement(sessionId, messageId, block): SessionItem | null` for already-loaded-item checks.
- Preserve `addSessionItems(sessionId, newItems, updatedMetadata = null)`.
- Preserve `updateSessionItemsContent(sessionId, items)` and `loadSessionItemsRanges(...)` signatures.
- Remove `clearEndedStreamingBlocks(sessionId)`. Preserve `_dropOrphanedStreamingBlocks(sessionId)`.

- [ ] **Step 1: Add a production-action fixture to `streamRetirement.test.js`.**

Follow the action extraction pattern in `streamingRows.test.js`. Execute actual action bodies from `data.js`.
Extend extraction to recognize both `        name(` and `        async name(` declarations.
Assert that every requested action and its next JSDoc boundary exist before evaluating the extracted source.
Inject Task 1's real pure matchers through `getProviderHelpers`, rather than implementing matching in a test double.
Extract the matching, retirement, start, delta, stop, end, content-update, add-items, and range-loading actions.
Supply real parsed-content helpers and buffer functions. Stub only unrelated metadata, network, and process services.

Create `makeRetirementFixture({ provider = 'codex', fetches = [] } = {})` inside the test file.
Expose its store, session IDs, recorded recomputations, captured retirement results, and deferred fetch controls.
Use a normal Pinia store so `$onAction` observes the real retired-pairs return value.
Record the streaming state at each recomputation; a persisted match must already be removed at that boundary.

- [ ] **Step 2: Write the failing lifecycle cases.**

| Test name | Required assertions |
|---|---|
| `persisted item retires before end` | UUID may be null; retired pairs contain the correct real line; no duplicate at recompute |
| `end waits for persisted replacement` | End alone keeps the block; later receipt removes it |
| `persisted item before start never creates a duplicate` | Late start creates no buffer or streaming row |
| `late old start preserves newer streaming` | Persist A, start B, replay A's start; B identity, text, and buffer stay unchanged |
| `repeated active start is idempotent` | Same message and block index keep one block, one buffer, and the same publication identity |
| `two messages match independently` | A and B retire only through their own durable IDs |
| `sessions cannot retire each other` | Receipt under another session leaves the original block intact |
| `range REST response retires streaming` | Deferred `loadSessionItemsRanges` response retires through actual `addSessionItems` |
| `content-only REST hydration retires streaming` | Metadata placeholder receives content through actual `updateSessionItemsContent` and then retires |
| `missing content becomes available after end` | No match until hydrated; hydration retires without replaying end |
| `failed REST preserves thinking state for retry` | HTTP failure or rejected fetch retains block, details, and group; successful retry transfers them |
| `provider resolves from process state` | Session metadata absent, process provider present: correct matcher retires the block |
| `unresolved provider is harmless` | No helper means no exception or false retirement; a later structural event can match after resolution |
| `late delta stop and end cannot recreate a retired block` | No new state or buffer, including when B is current |
| `retirement releases timers and pending publications` | No live inactivity timer, stale row publication, or buffer after cleanup |
| `thinking replacement preserves expanded state` | Real detail key and group receive the synthetic state; obsolete synthetic keys are cleared |
| `claude retires only the completed matching block` | Multiple blocks under one message remain independently eligible |
| `interrupted unfinished block still gets orphan cleanup` | Existing interrupted-turn cleanup remains effective |

Run the ordering cases for text and thinking. Use two summary parts for the thinking completion case.
Construct network-shaped items without cached parsed content for REST tests. Include no `stream_uuid`.
Retirement must ignore a forged `stream_uuid` when the durable identity does not match.

- [ ] **Step 3: Run lifecycle tests and confirm failures expose current behavior.**

Run: `cd frontend && node --test src/stores/streamRetirement.test.js`.
Expected: FAIL for durable matching, pre-start matching, idempotency, and content-only retirement.
Ensure failures concern behavior rather than an incomplete test fixture.

- [ ] **Step 4: Implement shared matching and retirement in `data.js`.**

Resolve provider as `this.getSession(sessionId)?.provider ?? this.processStates[sessionId]?.provider`.
An absent helper means no match. Use its `matchesStreamingBlock()` decision for each eligible item and block.
Keep the current cleanup and state-transfer body inside `_retireStreamingBlocks()`.
Remove `stream_uuid` checks and the store's Claude-specific identity parsing.

Implement `_findStreamingReplacement()` with the same provider decision against already-loaded session items.
Scan only during start/end structural events. Return an existing matching item or `null`; do not mutate it.

- [ ] **Step 5: Update start and end handling.**

Check an incoming start against loaded replacements before resetting the existing message or allocating a buffer.
If its replacement exists, ignore the start; retire any existing matching block through the shared retirement action.
This preflight must leave B untouched when an old completed A start arrives.

Ignore a repeated active start with the same message and block index. Keep its buffer and publication identity.
A genuinely new message still follows the existing reset policy.

On end, record the block UUID, find any loaded replacement, and retire through `_retireStreamingBlocks()`.
Recompute only after retirement or required structural state changes. Keep late delta/stop/end guards.

- [ ] **Step 6: Retire after both content-ingestion actions have stored content.**

Keep `addSessionItems()` retirement before its recompute.
Call `_retireStreamingBlocks(sessionId, updatedItems)` in `updateSessionItemsContent()` before its recompute.
Use updated stored items after `clearParsedContent()`, not stale parsed response objects.

- [ ] **Step 7: Remove unconditional ended-block deletion before initial REST loading.**

Remove the `clearEndedStreamingBlocks()` call from `loadSessionData()` and delete that obsolete store action.
Keep genuine interrupted-block cleanup and explicit disposal policies.
Update comments to describe durable identity and replacement after content hydration.

- [ ] **Step 8: Update the existing streaming-row retirement tests.**

Provide session/process provider metadata and the real matching helpers to the extracted store fixture.
Replace Codex's Claude-shaped content and wire UUID with actual AgentMessage or response-item reasoning records.
Use equal Codex `messageId` and completion UUID, including the existing pending-buffer tests.
Retain all existing row stability, flush, thinking transfer, and publication-identity assertions.

- [ ] **Step 9: Run focused tests and confirm all pass.**

Run: `cd frontend && node --test src/stores/streamRetirement.test.js src/stores/streamingRows.test.js src/utils/streamingBuffer.test.js src/utils/streamPublicationRegistry.test.js`.
Check every recompute snapshot and retired-pairs assertion, not only final stream absence.

- [ ] **Step 10: Commit the frontend lifecycle change.**

Stage only Task 2 files. Subject: `fix(streaming): retire persisted items through durable identity`.
Describe event ordering, both REST paths, and removal of premature load cleanup in the body.
Use the executing model's required trailer.

## Task 3: Update fixtures and validate rendered replacement

**Files:** Browser fixture files from the file-structure table and `invisibleStreaming.js`.

**Interfaces:**

- Consume the real production store and mounted `SessionItemsList.vue` through `window.invisibleStreamingFixture`.
- Use that fixture's existing router, sessions, `start`, `feed`, `settle`, `geometry`, and `snapshot` controls.
- Produce `window.streamRetirementFixture.run({ blockType, order, position, loading }): Promise<report>`.
- `blockType`: `'text'` or `'thinking'`; `order`: `'item-before-end'`, `'end-before-item'`, or `'item-before-start'`.
- `position`: `'bottom'` or `'reading-above'`; `loading`: `'live'`, `'slow-rest'`, or `'failed-rest-retry'`.
- Reports contain observed row identities, visible content, detail/group state, retired pairs, scroll geometry, and errors.
- Test-only harness function: `runStreamRetirementScenario(adapter, options): Promise<report>`.
- The adapter supplies actual controls and observations. It must not synthesize successful observations.

- [ ] **Step 1: Write fixture harness tests before adding the scenarios.**

In `streamRetirementFixture.test.js`, assert the three event orders invoke their controls in the expected sequence.
Assert a retained synthetic row fails acceptance. Assert wrong persisted text fails acceptance.
Assert a timeout or missing DOM observation reports failure, never success.
Assert a deferred failed-fetch retry does not release the replacement before retry resolution.

Run: `cd frontend && node --test tests/browser/streamRetirementFixture.test.js`.
Expected: missing harness or scenario failures before implementation.

- [ ] **Step 2: Correct existing Codex browser fixture records.**

In `invisibleStreaming.js::finalContent()`, set AgentMessage `payload.item.id` and reasoning `payload.id` to `messageId`.
Use `messageId` as the Codex end UUID. Preserve separate Claude message and block UUIDs.
Remove all fixture-generated `stream_uuid` properties. Keep history identities distinct where needed.
Check all completion call sites, including publication-rate, pending-return, and visible scenarios.

- [ ] **Step 3: Add the isolated wrapper and scenario harness.**

Follow `scrollerGeometryConversation.js` for wrapping the production conversation fixture without `main.js` bootstrap.
Use `installImportedScrollerQuarantine()` to prevent conflicting controls while a scenario runs.
Import the production fixture with Codex selected and historical baseline flags disabled.
Wait for its observable readiness before enabling scenario controls.

Drive actual store actions and actual rendered rows. Do not assign synthetic visual lists or imitate the scroll hook.
For REST scenarios, mark the synthetic main session `draft: false`, set `itemsFetched: false`, and navigate away and back.
Install read interception before changing those flags. Keep the other sessions seeded and fetched.
Serve both `/items/metadata/` and `/items/` for that exact synthetic session.
Require an observed request to each route before accepting the pending-load scenario.
The metadata response includes the completed row's kind, line number, and group metadata.
Defer the content response explicitly; return one controlled failure before a successful retry.
Keep the existing mutation rejection and tool-state/subagent/workflow-link GET responders.
Observe the real Retry control after failure. Do not imitate retry by directly inserting the final item.
All fixture project IDs and session IDs stay synthetic. Never launch a provider or issue backend writes.

- [ ] **Step 4: Implement bounded acceptance observations.**

After replacement, assert no synthetic row exists in the production visual list or final DOM.
Assert the expected persisted row and complete text exist. Allow existing exit animations to settle within a bounded wait.
Check opened thinking details and transferred group state through real store and DOM observations.
Capture `_retireStreamingBlocks` pairs through `$onAction`; verify the mounted scroll hook receives them.
For reading-above, preserve the observed anchor and offset within the existing fixture's 2px tolerance.
For bottom-following, use the existing fixture's 150px bottom-gap acceptance.
During delayed/failed REST, assert retained block text and detail/group state in the production store.
Preserve the existing loading reveal and error-panel policy; those controls can temporarily hide the conversation DOM.
After the real retry and reveal finish, assert final DOM text and open thinking state.

- [ ] **Step 5: Run the harness tests and frontend suite.**

Run: `cd frontend && node --test tests/browser/streamRetirementFixture.test.js`.
Then run: `cd frontend && npm test`.
Expected: all tests pass. Do not interpret Node fixture tests as browser DOM validation.

- [ ] **Step 6: Run browser scenarios when an authorized frontend server is available.**

Open `/tests/browser/streamRetirement.html` on the existing Vite origin.
Run each live event order for text and thinking. Check bottom-following and reading-above replacement.
Run slow REST and failed-REST retry for an ended open thinking block through an actual session switch.
Run persisted-item-before-start and late-old-start scenarios without refreshing the page.
Record the final report, observed rows, detail state, and scroll evidence in the session's artifacts directory.
Report unavailable browser access or an unavailable server separately; do not start a server without user authorization.

- [ ] **Step 7: Commit fixture validation changes.**

Stage only Task 3 files. Subject: `test(streaming): exercise durable replacement in conversation fixtures`.
Describe controlled event ordering and deferred REST validation in the body. Use the executing model's trailer.

## Task 4: Remove the backend FIFO bridge

**Files:** Backend files from the file-structure table and `tests/test_codex_stream_retirement.py`.

**Interfaces:**

- Preserve SDK stream-block event shapes and their session routing.
- Preserve `serialize_session_item(item)` and `get_session_items(session, line_nums)` item content.
- Remove `StreamedItemRegistry`, `get_streamed_item_registry()`, and `enrich_live_items_payload()` entirely.
- Keep the existing parent/subagent filter, hidden-session gate, and ephemeral-agent behavior.

- [ ] **Step 1: Add backend contract tests.**

Use the `CodexAgent.__new__` and mocked broadcast patterns in existing Codex event tests.
Supply normal and ephemeral agent state, active-tool maps, and reasoning summary indices required by the actual handler.
Use `SimpleNamespace` SDK payloads with `thread_id`, item type, and stable item ID.

| Test name | Assertions |
|---|---|
| `completed text preserves stream end identity` | Stop then end carry the same session, message ID, index 0, text type, and UUID |
| `reasoning parts complete as one block` | Several summary parts lead to one stop/end pair for the reasoning ID |
| `child completion does not paint parent streaming` | Other-thread completion produces no parent stream-block broadcast |
| `ephemeral completion remains private` | Final text capture remains; no transcript streaming broadcast |
| `persisted transport preserves durable IDs without an agent` | Serialized assistant/reasoning rows retain their JSONL identity without registry state |

The first four pin behavior through cleanup. They may pass before removal.
For transport coverage, create canonical rows in the test database and use the actual watcher item serializer.
Assert no `stream_uuid` is required or produced by the post-removal watcher broadcast path.
Follow existing watcher test lifecycle cleanup if starting the DB writer or watcher consumer.
Use a failing structural import/reference check only to verify removal; do not present it as behavioral race coverage.

- [ ] **Step 2: Run the backend contract tests before removal.**

Run: `uv run pytest tests/test_codex_stream_retirement.py -q`.
Record the passing compatibility baseline and any expected failure tied to the obsolete enrichment path.

- [ ] **Step 3: Remove agent-side registry operations.**

Remove the registry import, text/reasoning pushes, and all cleanup calls.
Update `_run_turn` and `_handle_stream_event` comments and docstrings.
Preserve stream stop/end broadcasts and subagent filtering. Remove only the obsolete FIFO explanation from that filter.

- [ ] **Step 4: Remove watcher-side enrichment and the registry module.**

Delete Codex's override, the base hook, and the generic watcher's enrichment call.
Delete `streaming_registry.py`. Keep serialization and the surrounding broadcast order unchanged.

- [ ] **Step 5: Run backend verification.**

Run: `uv run pytest tests/test_codex_stream_retirement.py tests/test_ephemeral_providers.py tests/test_agent_hidden_broadcast_gate.py tests/test_watcher_slice_fairness.py tests/test_codex_canonical.py -q`.
Run: `rg -n --glob '!*.test.js' 'get_streamed_item_registry|StreamedItemRegistry|enrich_live_items_payload|stream_uuid' src/twicc frontend/src frontend/tests/browser`.
Expected: no production or current-fixture references; `rg` exits 1 when no match exists.
Inspect test matches separately. A forged `stream_uuid` in a negative test is intentional, not a remaining matching dependency.
Historical documentation may retain descriptions.

- [ ] **Step 6: Commit backend removal.**

Stage only Task 4 files. Subject: `refactor(codex): remove the streaming identity fifo bridge`.
Describe durable frontend matching and preserved wire/content shapes. Use the executing model's trailer.

## Task 5: Complete verification and delivery

**Files:** This plan's completion notes only. Do not update the changelog.

**Interfaces:** Consume passing Task 1–4 verification and browser reports. Produce an honest delivery report with remaining limits.

- [ ] **Step 1: Run final change validation.**

Run `git diff --check` and inspect the complete task diff against the spec.
Run `cd frontend && npm run build` to verify provider imports and Vite compilation.
Inspect matching-module imports for helper/store cycles. Build success alone does not prove HMR behavior.
During available browser validation, edit and restore one task-owned provider matching line; verify HMR does not force a page reload.
Do not change or restore any user-owned line for this check.

- [ ] **Step 2: Review results against the acceptance matrix.**

Confirm Tasks 1–4 cover every spec acceptance row and all five Review Focus cases.
Confirm the browser uses actual `SessionItemsList.vue`, not the simpler `streamingRows.js` structural fixture.
Treat failed identity tests, stale rows, lost thinking state, and broken scroll replacement as release blockers.
If browser access is unavailable, list the unverified DOM/HMR cases explicitly.
Do not repeat already-passing test suites unless a later edit changes their coverage.

- [ ] **Step 3: Record completion evidence.**

Add actual commands, results, browser artifact links, and any limitations to this plan.
Do not claim runtime or browser validation from source inspection or Node-only tests.
Record implementation commits and any reviewed contract adjustment.

- [ ] **Step 4: Deliver the change and reserved-operation reminder.**

Report durable text/reasoning replacement, preserved Claude behavior, checks, and material limits.
Remind the user to restart their running backend through `devctl.py`.
Do not restart, install packages, or run migrations on the user's behalf.

## Plan review record

Two independent internal reviewers complete an adversarial implementation-plan review on 2026-10-05.
The review covers execution details, spec coverage, test validity, and repository compatibility.

- Round 1 identifies three execution blockers and one verification-command contradiction.
- The REST scenario now clears draft status, observes both real loading routes, and uses the real Retry control.
- Pending-load assertions use retained store state. Final DOM assertions wait for the existing reveal policy.
- Production-action extraction explicitly supports synchronous and asynchronous declarations.
- Cleanup searches exclude intentional negative-test references and inspect those references separately.
- Round 2 verifies all four corrections against the repository and finds no blocking defects or optional comments.

The plan is ready for explicitly authorized execution. No product implementation occurs during this review.
