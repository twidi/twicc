# Keep Streaming Rows Stable Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use subagents only when the user authorizes that execution method.

**Goal:** Publish streaming content without replacing its visual row, scanning the conversation, or invalidating unchanged geometry.

**Architecture:** `_onBufferDrain()` reads the current visual-item cache and updates its reactive row through `setParsedContent()`. Structural rebuilding and stream lifecycle remain unchanged. Tests execute the actual action source and exercise Vue/Pinia notification.

**Tech Stack:** Vue 3, Pinia, JavaScript ESM, Node `node:test`, existing adaptive streaming buffer.

**Spec:** [Keep streaming rows stable](2026-10-02-keep-streaming-rows-stable-spec.md), committed as `9aef4021`.

## Global Constraints

- Keep `_onBufferDrain(sessionId, blockIndex, displayedText)` unchanged as an interface.
- Use `localState.visualItemCache[sessionId]` and `setParsedContent()`.
- Keep the existing provider-independent text and thinking envelope shapes.
- Keep structural changes on the existing `recomputeVisualItems()` path.
- Do not change buffers, visibility, publication rate, backend events, Markdown, geometry algorithms, or animations.
- Do not add dependencies, another row registry, or a product helper solely for testability.
- Preserve current retirement ordering, stream identity, detail state, and scroll behavior.
- Keep all code, comments, test names, and documentation in English.
- Work in the current checkout. Do not create a branch or worktree without explicit user instruction.
- Preserve all user-owned changes, including already staged files.
- Do not restart servers, install packages, or run migrations without explicit user instruction.

## Review Focus

1. Structural replacement during streaming: the next publication updates the current cached row, never its predecessor. Task 1 tests this.
2. Filtering and exit retention: hidden rows retain displayed text; exit-only rows receive no patch. Tasks 1 and 2 test this.
3. Stream retirement with undrained text: flush still publishes before block removal. Task 2 tests this.
4. Synthetic key reuse: a new message retains buffer ownership at the reused index. Task 2 tests this.
5. Content-dependent branches: Codex proposed plans may replace inner content, while outer instances remain mounted. Task 3 validates this.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `frontend/src/stores/data.js` | Modify `_onBufferDrain()` and cache comment | Production publication path |
| `frontend/src/stores/streamingRows.test.js` | Create | Action behavior, reactive identity, geometry, and buffer lifecycle tests |
| `frontend/tests/browser/streamingRows.html` | Create | Test-only Vite browser entry for reproducible SFC validation |
| `frontend/tests/browser/streamingRows.js` | Create | Isolated fixtures, real SFC mounting, publication controls, and lifecycle counters |
| This plan | Update checkboxes and validation record | Execution tracking and evidence |

Read these existing references before execution:

- `frontend/src/stores/nestedAgentState.test.js`: tests extract actual store actions because Node cannot import the full store graph.
- `frontend/src/utils/parsedContent.js`: sanctioned parsed-content access and reactive property replacement.
- `frontend/src/composables/useVirtualScroll.test.js`: geometry and fake-container patterns.
- `frontend/src/utils/streamingBuffer.js`: RAF scheduling, flush, replacement, and destruction.
- `frontend/src/composables/useListExit.js`: temporary ownership of removed rows.
- `frontend/src/components/session/detail/items/codex/AssistantMessage.vue`: proposed-plan subtree transition.

## Task 1: Change the publication action with behavior tests

**Files:**

- Modify: `frontend/src/stores/data.js`, `_onBufferDrain()` and the `visualItemCache` state comment.
- Create: `frontend/src/stores/streamingRows.test.js`.

**Interfaces:**

- Consumes: `getParsedContent(item)`, `setParsedContent(item, parsed)`, `SYNTHETIC_ITEM.STREAMING_BLOCK`.
- Produces: unchanged `_onBufferDrain(sessionId: string, blockIndex: number, displayedText: string): void`.
- Test fixture: `makeFixture({ blockType = 'text', stopped = false, rowCount = 2 } = {})`.
- Fixture returns `{ store, sessionId, blockIndex, lineNum, block, row, rows, cache }`.
- `store._onBufferDrain` must execute the extracted production action, not a copied implementation.
- Fixture rows and Map use shared objects inside reactive Pinia state.
- Use a unique test-store ID per fixture or a fresh Pinia instance to avoid state leakage.

- [x] **Step 1: Write failing behavior tests.**

Extract `_onBufferDrain()` from `data.js`, using the existing action-extraction pattern. Assert the action boundary exists.

Supply `SYNTHETIC_ITEM` and the actual parsed-content helpers as dependencies. Inject no alternative publication logic.

Create a streaming block and matching visual row using the existing negative-line formula. Put the same object in the array and Map.

Add these named tests:

| Test name | Assertions |
|---|---|
| `text publication preserves row list and cache identity` | Text changes; row, list, Map, and untouched rows retain identity; envelope changes |
| `thinking publication preserves shape and stopped flag` | `thinking` changes; `streaming === !block.stopped` for both stopped values |
| `publication does not scan the visual list` | Define a throwing `findIndex` on the fixture visual array; publication still succeeds |
| `publication does not rebuild the visual list` | A throwing `recomputeVisualItems` stub is never invoked |
| `missing stream or block produces no patch` | No exception; existing row envelope stays unchanged |
| `missing cache or row retains latest displayed text` | `block.displayedText` changes; no row creation or list mutation |
| `another synthetic kind receives no patch` | Wrong-kind cache entry retains its previous envelope |
| `publication uses the cache after structural replacement` | Replace cache and list together; next publication updates the new row only |
| `filtered row returns with latest displayed text` | Remove the row from cache/list; publish; reintroduce a row from the current block prefix |

Core identity assertion sketch:

```js
const f = makeFixture()
const before = getParsedContent(f.row)
f.store._onBufferDrain(f.sessionId, f.blockIndex, 'latest text')
assert.strictEqual(f.store.localState.sessionVisualItems[f.sessionId], f.rows)
assert.strictEqual(f.rows.at(-1), f.row)
assert.strictEqual(f.cache.get(f.lineNum), f.row)
assert.notStrictEqual(getParsedContent(f.row), before)
assert.equal(getParsedContent(f.row).message.content[0].text, 'latest text')
```

Fixtures model structural replacement explicitly. They do not claim to execute the full `recomputeVisualItems()` action.

- [x] **Step 2: Run the tests and confirm the regression.**

Run from the main checkout:

```bash
cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/streamingRows.test.js
```

Expected before implementation: identity, no-scan, and wrong-kind tests fail against the current action.

A missing test file, import error, or extraction failure is not the required behavioral failure.

- [x] **Step 3: Implement the specified action.**

Keep the existing streaming-state check, block lookup, and `block.displayedText` assignment.

Compute the synthetic line number. Get the row from the current cache with `Map.get()`.

Return for a missing row or a mismatched `syntheticKind`. Build the existing envelope and call `setParsedContent(row, newParsed)`.

Remove the visual-array scan, row copy, array assignment, and `cache.set()`.

Correct the cache comment: it is internal Pinia state; access returns reactive row proxies shared with the visual list.

Do not retain a cache or row reference in the buffer callback. Do not change structural methods.

- [x] **Step 4: Run the focused tests.**

Run the command from Step 2. Expected: every behavior test passes.

Inspect the diff: production edits stay within `_onBufferDrain()` and the cache comment.

- [x] **Step 5: Commit this independently testable change.**

Use subject `fix(chat): preserve streaming visual row identity`.

Stage only `frontend/src/stores/data.js` and `frontend/src/stores/streamingRows.test.js`.

Commit with `git commit --only ... -- <the two paths>` so unrelated staged changes remain excluded.

Add a descriptive body and the current runtime model's `Co-Authored-By: Codex MODEL <codex@openai.com>` trailer.

## Task 2: Verify reactive rendering, geometry, and lifecycle boundaries

**Files:**

- Extend: `frontend/src/stores/streamingRows.test.js`.
- Read only: `data.js`, `streamingBuffer.js`, `useVirtualScroll.js`, and `useListExit.js`.

**Interfaces:**

- Consumes: Task 1's fixture and production `_onBufferDrain()` action.
- Additional actual actions: `streamBlockStart()` and `_retireStreamingBlocks()`.
- Buffer APIs: `initBuffer`, `feedDelta`, `flushBuffer`, `destroySessionBuffers`, `destroyAllBuffers`.
- Geometry API: `useVirtualScroll({ items, itemKey, containerRef })`, `positions`, and `batchUpdateItemHeights()`.
- Exit API: `useListExit({ items, getKey, isEligible, scopeKey, getVisibleRange, getHeight, env })`.
- Produces: executable evidence for identity, notification, and lifecycle acceptance criteria.

- [x] **Step 1: Add a reactive content and geometry regression.**

Use Vue's `createRenderer()` with an in-memory host. Mount a component that creates the actual scroller composable in `setup()`.

Use Pinia fixture state. A scoped row slot passes `getParsedContent(row)` to a content component, matching the application prop flow.

Do not assign `_parsedContent` directly in tests. Use the existing helpers.

Initialize 2,000 history rows and one streaming row. Count `itemKey` calls, component mounts, and observed text values.

After the initial Vue flush, reset counters. Run 60 production-action publications, each followed by `nextTick()`.

Assert:

```js
assert.equal(keyReadsAfterInitialSetup, 0)
assert.equal(contentMountCount, 1)
assert.equal(observedTexts.at(-1), 'update-59')
assert.equal(observedTexts.length, 60)
```

Capture initial observed text separately, so the 60-count assertion measures publications only.

Assert the position-array reference stays unchanged throughout content-only updates.

Then apply a real height change through `batchUpdateItemHeights()`. Assert positions reflect the new height and total geometry remains correct.

Name the tests `content publications notify Vue without geometry invalidation` and `actual height changes still update geometry`.

- [x] **Step 2: Add exit-retention and cache-replacement tests.**

Run the actual `useListExit()` in a component scope with controlled timers and a visible range.

Collapse a group containing the synthetic row. Remove that row from the store list and cache through the fixture structural operation.

Flush Vue so `displayItems` retains the leaving row. Publish new text through the production action.

Assert the exit-only row envelope remains unchanged and `block.displayedText` contains the latest text.

Reopen before the exit timer ends. Build the current row from the latest block prefix, and replace cache/list together.

Assert no duplicate key remains in `displayItems`. The next publication updates the current row, not the retained predecessor.

Name the test `exit-only streaming rows stay unchanged and reopening uses current text`.

- [x] **Step 3: Add buffer flush and retirement tests.**

Use the actual buffer with a deterministic RAF queue. Restore global RAF functions after each test and destroy all buffers.

Extract the production `_retireStreamingBlocks()` action. Inject actual buffer functions and parsed-content helpers.

Supply detail-state methods as fixture storage operations. Provide the timer cleanup dependency without changing production lifecycle behavior.

Exercise both matching paths:

- Claude: final item's parsed `uuid` and `message.id` match the block and stream.
- Codex: final item's `stream_uuid` matches the block UUID.

Feed text without draining all frames, then retire the block. Observe the publication during `flushBuffer()`.

Assert complete text is published before block removal, the correct retired pair is returned, and the buffer has no pending RAF afterward.

For thinking, assert persisted detail-open state transfers to the real item's existing target key.

Name tests `retirement flushes the latest text before removing a Claude block` and `retirement flushes Codex stream_uuid and transfers thinking state`.

The fixture does not execute final list rebuilding. Browser validation in Task 3 verifies the final item appears once.

- [x] **Step 4: Add synthetic-index reuse coverage.**

Extract and execute production `streamBlockStart()` with the actual buffer APIs.

Its recompute dependency builds only fixture rows/cache from current block state. Label that operation as a structural fixture, not production recomputation.

Start message A at block index 0 and feed pending text. Start message B at the same index before A finishes.

Assert A's RAF handle is cancelled. Feed B, drain frames, and verify only B's text owns the current row.

Name the test `a new message replaces the buffer at a reused synthetic index`.

- [x] **Step 5: Run focused and related existing tests.**

```bash
cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/streamingRows.test.js src/utils/visualItems.test.js src/composables/useVirtualScroll.test.js src/utils/listExit.test.js src/composables/useChatEntrance.test.js
```

Expected: all tests pass, no leaked buffer/timer handles, and no Vue lifecycle warnings from the mounted harness.

- [x] **Step 6: Commit the regression coverage.**

Use subject `test(chat): cover stable streaming rows and lifecycle boundaries`.

Commit only `frontend/src/stores/streamingRows.test.js`, with a descriptive body and the current model trailer.

## Task 3: Validate actual provider components and product behavior

**Files:**

- Modify: this plan's validation record only.
- Create: `frontend/tests/browser/streamingRows.html` and `frontend/tests/browser/streamingRows.js`, test-only files.
- Read only: the actual session, provider message, and Markdown SFCs.

**Interfaces:**

- Consumes: the implemented action and passing Task 1–2 tests.
- Produces: evidence from the actual browser rendering paths, with clear limits if a case cannot be observed.

- [x] **Step 1: Check browser prerequisites before making product changes.**

Run this prerequisite check before Task 1 execution. Record an existing Vite frontend URL and browser automation availability.

The isolated harness needs the running Vite server to compile actual SFCs. It does not need active provider sessions.

If no Vite server is available, server start remains a user-authorized operation. Do not start or restart it automatically.

Automated Node tests can proceed without browser access. Mark browser validation pending and implementation only partially validated until it runs.

- [x] **Step 2: Build the isolated browser harness.**

The HTML entry loads `./streamingRows.js` through a relative module script. Open `/tests/browser/streamingRows.html` on the existing Vite origin.

Create an independent Vue app and Pinia instance. Do not import or run `src/main.js`, initialize WebSockets, or launch a provider.

Install a harness-local `createRouter()` with `createMemoryHistory()` from `vue-router`, using one `/` route and an inert route component.

Push `/` and await `router.isReady()` before mounting. This supplies `MarkdownContent.useRouter()` without importing the application's `src/router.js`.

Import the actual `useDataStore`, `VirtualScroller`, `SessionItem`, and parsed-content helpers through Vite.

Mount the actual `VirtualScroller` with a scoped slot that passes `getParsedContent(item)` to the actual `SessionItem`.

Select Claude or Codex by the fixture session's provider. Both must use their real message SFCs, not component stubs.

Initialize only fixture session, block, row, and cache state. Give the fixture a synthetic project/session ID used only inside this app.

Keep structural fixture updates explicit. Do not label manually rebuilt fixture rows as production `recomputeVisualItems()` behavior.

Import the existing styles and Web Awesome modules needed by the real rendered components. Keep real Markdown and details transitions enabled.

Use controlled publication buttons and finite sequences. Each publication invokes the real store `_onBufferDrain()` action.

Provide these reproducible scenarios:

| Scenario | Publication sequence and observation |
|---|---|
| Claude text | Empty prefix, short text, longer wrapping text; latest text remains visible |
| Claude thinking | Growing thinking prefixes; open details while publishing |
| Codex text | Same text sequence through the actual Codex message SFC |
| Codex thinking | Same thinking sequence through the actual Reasoning SFC |
| Codex proposed plan | Plain text, opening `<proposed_plan>` line, growing plan, closing tag; existing subtree transitions stay valid |
| Geometry | Enough history rows to scroll; growing text changes measured height; test bottom following and reading above |

Do not send fixture data to backend write endpoints. Check browser requests and avoid mounting unrelated components that require backend data.

This harness validates real SFC notification and geometry. It does not replace natural-session validation of final JSONL reconciliation.

- [x] **Step 3: Instrument component lifetime outside product code.**

Install a mixin only on the harness app before mounting. Record instance `uid`, mount count, and unmount count.

Identify components by their development `__file` path. Track these separately:

- `VirtualScrollerItem.vue`
- `SessionItem.vue`
- `items/claude_code/Message.vue`
- `items/codex/Message.vue`

Display the observations in a harness diagnostics panel. Keep counters keyed by component file and instance UID.

Tag observations with the target row identity: `itemKey` for `VirtualScrollerItem`, and `sessionId` plus `lineNum` for the others.

Only assert lifecycle stability for the target streaming row. Historical rows can legitimately mount or unmount when the virtual range moves.

Reset the baseline between scenarios, provider changes, and structural rebuilds. Keep structural transitions separate from content-only publication assertions.

Take the baseline after initial mount. Publish multiple prefixes while the target remains inside the rendered range.

Assert each tracked outer component of the target row keeps the same UID and receives zero additional mounts or unmounts.

Check the changed text independently. Stable DOM identity alone is not evidence of stable component identity.

Allow inner TextContent/Markdown subtree replacement for proposed-plan transitions. Do not apply outer-component assertions to those children.

Keep all instrumentation in the test harness. Do not expose diagnostics or component internals in product code.

- [x] **Step 4: Run the complete frontend suite.**

```bash
cd /home/twidi/dev/twicc-poc/frontend && npm test
```

Expected: all tests pass. If another session changes unrelated files, identify that failure before changing scope.

- [ ] **Step 5: Run the reproducible browser scenarios.**

Run all harness scenarios from Step 2. Record provider, final text, outer-instance counters, measured height change, and scroll behavior.

The proposed-plan case is required in this controlled harness. It must not depend on a naturally active plan-mode session.

Do not mark a scenario passing when only its synthetic Node counterpart runs.

- [ ] **Step 6: Observe natural streaming and final replacement when available.**

Use an existing running instance and naturally active sessions. Confirm the edited action is loaded through HMR or the relevant bundle.

Do not restart a server or send artificial user messages without authorization.

Observe live text for both providers. Open thinking and confirm subsequent publications update its body.

Use the harness lifecycle observations for instance-stability evidence. Natural-session DOM inspection supplies supplementary rendering evidence only.

The controlled harness already checks the actual provider SFC path, including proposed plans. Natural sessions need not produce that case.

- [ ] **Step 7: Validate natural-session scroll, filtering, and final replacement.**

Check near-bottom following as text wraps. Then scroll upward and verify new text does not move the reader unexpectedly.

Collapse and reopen a streaming thinking group. Check the reopened text is current and no duplicate row appears.

Change display mode while a stream is active. Check the next visible publication targets the current row.

Observe completion: one final item replaces its synthetic row, with thinking open state retained where applicable.

If a required natural lifecycle case is unavailable, record it as unverified and report partial product validation.

Do not wait indefinitely for natural sessions. Keep Task 3 incomplete for that case, without blocking completed code and Node-test reporting.

- [x] **Step 8: Review the final scoped diff and record evidence.**

Confirm production changes remain limited to the action and cache comment. Confirm no changes to timing, protocol, or geometry algorithms.

Run `git diff --check`. Inspect the two implementation commits, not unrelated checkout changes.

Record exact test commands, pass/fail results, observed provider cases, and any remaining browser validation gaps below.

Commit the two harness files with subject `test(chat): add isolated streaming row browser validation`, using a targeted commit and model trailer.

Commit this plan's completed evidence separately. Keep incomplete validations explicit in that record.

Do not claim every freeze is resolved. Report removal of the verified content-only lookup and geometry invalidation.

## Validation record

Implementation runs on current `main`, starting from `f2696fe1`, on 2026-10-02.

Product code and Node regression validation complete. Browser product validation remains **partial**.

### Scope and commits

| Commit | Owned change |
|---|---|
| `edd30da5` | Production action, cache comment, nine behavior tests |
| `89eaf621` | Reactive rendering, geometry, exit, retirement, and buffer-reuse tests |
| `dc0345f2` | Explicit scoped-slot prop flow in the Vue renderer test |
| `ef5a6d6f` | Isolated real-SFC browser harness |

Production edits stay inside `_onBufferDrain()` and the cache comment in `frontend/src/stores/data.js`.

No timing, protocol, Markdown, structural rebuild, or geometry algorithm changes occur.

The checkout also contains concurrent changes in `usage.js`, `ProjectView.vue`, and `QuotaTooltipContent.vue`.
Those files and two untracked July design documents remain outside these commits.

### Commands and results

All commands run from the main checkout. No server restart, installation, migration, or provider launch occurs.

| Exact command | Result |
|---|---|
| `cd /home/twidi/dev/twicc-poc && curl -I --max-time 4 http://localhost:5173/` | HTTP 200 before production edits |
| `cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/streamingRows.test.js` | Before fix: 9 tests, 4 pass, 5 behavioral failures; after fix: 9/9 pass |
| `cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/streamingRows.test.js src/utils/visualItems.test.js src/composables/useVirtualScroll.test.js src/utils/listExit.test.js src/composables/useChatEntrance.test.js` | 97/97 pass |
| `cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/streamingRows.test.js` | After scoped-slot follow-up: 15/15 pass |
| `cd /home/twidi/dev/twicc-poc/frontend && npm test` | Final suite: 1,478/1,478 pass, zero failures, skips, or cancellations |
| `cd /home/twidi/dev/twicc-poc && node --check frontend/tests/browser/streamingRows.js` | Exit 0 |
| `cd /home/twidi/dev/twicc-poc && git diff --check` | Exit 0 |

The observed RED failures cover identity, thinking publication, visual-list scans, missing-cache handling, and wrong synthetic kind.
Action extraction and imports succeed before those failures.

The final reactive test executes 60 publications over 2,000 history rows and one streaming row.
It observes 60 text updates, one content mount, zero additional geometry key reads, and unchanged position-array identity.
Actual height changes still update positions and total height.

Exit retention, reopened current text, Claude UUID/message matching, Codex `stream_uuid` matching, thinking-state transfer, and buffer cancellation pass.
These structural operations use labeled fixtures. They do not execute final production list rebuilding.

### Controlled browser evidence

Chrome automation uses `cua_repl` on the existing Vite origin.
Harness URL: `http://localhost:5173/tests/browser/streamingRows.html`.

The app uses independent Vue/Pinia state and a memory router.
It mounts actual `VirtualScroller`, `SessionItem`, provider message, Markdown, and thinking-details SFCs.
It does not bootstrap `src/main.js` or initialize a WebSocket.

Each scenario records the target row only. The baseline starts after structural fixture replacement and initial mount.
Each listed outer instance has one initial mount, zero additional mounts, and zero unmounts during content publications.

| Scenario | Outer UIDs: scroller item / session item / provider message | Rendered result | Measured row height |
|---|---|---|---|
| Claude text | `10 / 11 / 13` | Empty, short prefix, then latest wrapping text | 23.97 → 359.84 px |
| Claude thinking | `10 / 11 / 13` in a separate run | Real details open; growing thinking body renders | 122.13 → 465.31 px |
| Codex text | `18 / 19 / 21` | Empty, short prefix, then latest wrapping text | 23.97 → 359.84 px |
| Codex thinking | `26 / 27 / 29` | Real reasoning details open; growing body renders | 121.31 → 465.31 px |
| Codex proposed plan | `33 / 34 / 36` | Plain introduction, opening tag, growing plan, closing tag | 47.97 → 150.77 → 241.25 px |
| Claude geometry | `49 / 50 / 52` | 100 history rows; 20 incremental publications render | 23.97 → 359.84 px |
| Codex geometry | `339 / 340 / 342` | 100 history rows; 20 incremental publications render | 23.97 → 359.84 px |

UID values belong to separate fixture runs and page reloads. They do not identify product sessions.

Final text contains `Latest wrapping text stays visible.` for text and thinking cases.
The thinking body also contains `Thinking grows.`.
The proposed-plan body contains `Growing plan details.` after both opening and closing tags.
Inner content transitions occur while all three tracked outer instances stay mounted.

Harness diagnostics report no Vue warnings and no `/api/` resource requests.
Web Awesome requests its normal Font Awesome icons from `ka-f.fontawesome.com`.
No fixture write endpoint or provider message is invoked.

### Scroll result and remaining validation

Both geometry scenarios preserve the reader at `scrollTop=200` during subsequent publication.
The target row is outside the rendered virtual range for that reading-above phase.

**Controlled bottom following fails.** The failure also occurs with 20 incremental publications.
After placing the actual scrollbar at its physical bottom, initial geometry is:
`scrollTop=3786.36`, `scrollHeight=4205`, `clientHeight=418`.
Final geometry is `scrollTop=3791.82`, `scrollHeight=4523`, `clientHeight=418`.

Content renders and heights update, but the scrollbar does not remain at the bottom.
This result does not establish a regression from the old publication action.
No before/after browser comparison runs against the old action.
The existing native scroll-anchor and geometry algorithms remain unchanged.

Natural-session final JSONL reconciliation, collapse/reopen, display-mode changes, and thinking-state retention at completion remain unverified.
The existing open Claude view is idle. Two naturally active session routes also show no mounted synthetic streaming row at inspection.
No final replacement or filtering lifecycle event is observed.
No artificial provider user message is sent. Validation does not wait indefinitely for those lifecycle events.
Task 3 Steps 5–7 remain incomplete for these gaps.

### Execution decisions and deviations

- Stay on current `main`, without a branch or worktree, as explicitly instructed.
- Task 2 covers existing lifecycle behavior. Task 1 supplies the observed RED regression; lifecycle code requires no production edit.
- Add a separate scoped-slot follow-up commit. The first minimal renderer slot returns a fragment and fails host teardown. A single wrapper element fixes the test host.
- Remove completed prior-fixture unmounts before each browser baseline. They belong to structural changes, not content publications.
- Pass `externallyGrouped=true` for the browser fixture. This represents an expanded external group and keeps real thinking details available.
- Use settled navigation, then set the physical scrollbar to its bottom before geometry publication. Fixture history estimates leave navigation short of the end.
- Preserve the bottom-following failure in diagnostics. Do not change product geometry outside the approved scope.
- Correct initial model trailers after reading the root rollout `turn_context.model=gpt-6.1-sol`. The internal child inherits that model. Only owned commit messages change; all trees, worktree files, and index content stay unchanged.
- Keep author ledger artifacts under the plan's ignored `.superpowers/sdd/` workspace until independent review completes.

The implemented change removes the verified conversation-length lookup and content-only geometry invalidation.
It does not claim to fix every freeze or complete all product lifecycle validation.

## Adversarial review record

An internal subagent reviews this plan against the specification and source on 2026-10-02. The loop completes in three rounds.

The review identifies four validation gaps:

- Natural streaming alone does not guarantee reproducible provider and proposed-plan cases.
- DOM stability does not prove Vue component-instance stability.
- Actual Markdown requires a harness-local router.
- Lifecycle assertions must target the streaming row, allowing legitimate virtualization changes in historical rows.

The revised plan adds an isolated browser harness, instance counters, a memory router, and target-specific assertions.

The final round reports no remaining actionable findings. No blocking issue is found in the proposed production change.

This review is static. Implementation tests and browser scenarios remain unexecuted.

## Execution handoff

Review this plan before product implementation.

Native execution is recommended: one small production change and closely related tests share the same ownership contract.

An internal subagent review can follow the completed change when that execution method is approved.

The earlier subagent authorization covers specification review. It does not select implementation delegation automatically.
