# Coalesce Markdown Renders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for delegated execution, or superpowers:executing-plans for inline execution. Execute each task's test cycle before the next task.

**Goal:** Keep one active Markdown document render and one latest pending snapshot, while preserving correct final output.

**Architecture:** A component-local coordinator controls revision ownership and serial async execution. A lifecycle composable supplies eligibility. The existing block pipeline uses cancellation checkpoints and staged, reference-aware cache entries.

**Tech Stack:** Existing Vue 3, node:test, markdown-it-async, Shiki, Mermaid, and Vite. No new dependency.

**Spec:** `docs/plans/2026-10-02-coalesce-markdown-renders-spec.md`, commit `88dcb87e`.

**Status:** Documents only. The user has not authorized implementation. Stop after plan review and commit.

## Global constraints

- Work in `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`, branch `bugfix/stable-streaming-rows`.
- Prefix every shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows &&`.
- Preserve unrelated user changes. Commit only task-owned files with a descriptive Conventional Commit body and model trailer.
- Write artifacts in English. Do not merge, restart servers, install packages, or change main without user authorization.
- Keep at most one active source-driven document operation and one latest pending snapshot per component.
- Use synchronous invalidation and one microtask drain. Add no timers, RAF handles, global queues, or registry owners.
- A request contains source, Mermaid theme, and slash-command mode. Never revive an invalidated revision.
- A non-cancellable Promise occupies the active slot until it settles, even after invalidation.
- Commit, cache promotion/eviction, successful `rendered`, and tool restoration require current eligible ownership.
- Keep the previous complete DOM during rendering, cancellation, and failure.
- Report only current eligible failures. Consume obsolete failures. Error-sink failure cannot prevent slot release.
- Allow active initial reveal measurement. Never use `STREAMING_VIEW_CONTEXT` as the Markdown view gate.
- Preserve full parsing, sanitization, completed-block reuse, occurrence keys, and all existing Markdown tools and links.
- Do not change priority 1–4 buffering, scroller behavior, streaming visibility, backend events, or animation policies.
- Do not add unfinished-fence deferral, incremental parsing, cross-component caches, or shared in-flight Promise registries.
- Separate deterministic scheduling proof, real output compatibility, and browser product acceptance.

## Review focus

1. A → B → A during an unresolved await must start fresh A after old A settles. Task 1.
2. Initial hidden reveal must still measure rendered canonical Markdown. Task 2 and browser Task 4.
3. Changed reference definitions must update an unchanged link block. Task 3 and browser Task 4.
4. Mermaid rejection after hide or unmount must release ownership without stale errors or cache writes. Tasks 1–3.
5. Nested code-tool restoration must not mutate old wrappers or remembered state after a newer render. Task 3 and browser Task 4.

## File map

| Path | Responsibility |
| --- | --- |
| `frontend/src/utils/markdownRenderCoordinator.js` (new) | Pure latest-only async coordinator and cancelled sentinel |
| `frontend/src/utils/markdownRenderCoordinator.test.js` (new) | Deferred-Promise scheduling and error ownership proofs |
| `frontend/src/composables/useMarkdownRenderEligibility.js` (new) | Optional view/row gates, document visibility, and Vue lifecycle |
| `frontend/src/composables/useMarkdownRenderEligibility.test.js` (new) | Real Vue lifecycle and injected context tests |
| `frontend/src/composables/streamPublicationKeys.js` | Add independent Markdown view key |
| `frontend/src/components/session/detail/SessionItemsList.vue` | Provide Markdown view readiness without reveal restriction |
| `frontend/src/components/ui/MarkdownContent.vue` | Coordinator wiring, checkpoints, staged cache, and tool guards |
| `frontend/src/utils/markdownRenderCache.js` (new) | Exact reference-aware key construction |
| `frontend/src/utils/markdownRenderCache.test.js` (new) | Reference identity and theme/slash key correctness |
| `frontend/src/components/ui/MarkdownContent.render.test.js` (new) | Execute actual component pipeline functions with controlled dependencies |
| `frontend/tests/browser/markdownRendering.html` (new) | Real-component fixture entry |
| `frontend/tests/browser/markdownRendering.js` (new) | Baseline/current comparison and production conversation checks |
| `frontend/tests/browser/prepareMarkdownRenderingBaseline.mjs` (new) | Guarded baseline/current instrumented SFC preparation and removal |
| `frontend/src/utils/markdownRenderingFixture.test.js` (new) | Fixture readiness, adapter safety, source schedule, and report checks |
| `.gitignore` | Ignore generated UI adapters and `frontend/tests/browser/.markdown-rendering-generation.json` only |
| `docs/plans/2026-10-02-coalesce-markdown-renders-execution.md` (new) | Tests, reviews, browser observations, and explicit limits |

`utils/markdown.js` remains the authoritative parser and sanitizer. No modification is planned.
Do not extract unrelated UI or restructure the full Markdown component.

## Interfaces

### Task 1 coordinator

```js
export const MARKDOWN_RENDER_CANCELLED = Symbol('markdownRenderCancelled')
export function createMarkdownRenderCoordinator({ render, commit, onError, onState, schedule = queueMicrotask })
// returns { request(input), setEligible(boolean), dispose() }
// input: { source: string, theme: 'dark' | 'default', slashTag: boolean }
// render(input, { isCurrent }) -> Promise<result | MARKDOWN_RENDER_CANCELLED>
// commit(result, input, { isCurrent }) -> void; synchronous
// onError(error) -> void; guarded and consumed if it throws
// onState({ rendering: boolean }) -> void
```

`request` snapshots the three input fields. Tuple equality compares their exact values.
`request` and `setEligible` return synchronously. `dispose` is idempotent.
The coordinator starts ineligible. Before first eligible drain, requests only update the latest snapshot.
`rendering` means eligible unfinished requested work, including a pending request behind obsolete active work.
It is false while hidden, after disposal, after a successful current commit, and after a current failure.
A superseded operation cannot clear the flag for its successor.
A cancelled result cannot count as a successful committed input.
If the cancelled operation is still current, release its slot and end unfinished state without commit, emit, error, or retry.
Changed input or a later eligibility false-to-true transition can retry. Never create an automatic cancellation microtask loop.
`commit` updates the successful snapshot only for current work. Reentrant input changes during commit callbacks must remain pending.

### Task 2 eligibility

```js
export function useMarkdownRenderEligibility()
// returns { eligible: Readonly<Ref<boolean>> }
```

The composable injects `MARKDOWN_RENDER_VIEW_CONTEXT` and `STREAMING_ROW_CONTEXT` optionally.
Eligibility requires mounted, KeepAlive-attached, visible document, optional true view, and optional active non-outside row.
An absent document uses visible fallback for the non-browser test environment.
Read reactive row fields with `toValue`. `unknown` permits initial rendering.
The composable owns and removes its `visibilitychange` listener. It owns no streaming token.

Add `export const MARKDOWN_RENDER_VIEW_CONTEXT = Symbol('markdownRenderViewContext')` to the existing keys module.
`SessionItemsList` provides a computed value equivalent to:
`props.viewActive && sessionActive.value && !isLoading.value && showVirtualScroller.value`.
Do not change the existing streaming view provider.

### Task 3 cache and pipeline

```js
export function markdownReferenceContextKey(references) // exact deterministic string
export function markdownBlockCacheKey({ source, referenceContext, theme, slashTag }) // exact tuple string
```

Sort reference names. Serialize complete values with deterministic object-key ordering.
Treat absent definitions as an empty reference context. Arrays preserve their order.
Use exact strings, not hash-only equality. Include theme only for Mermaid-source blocks, using the existing fence detection contract.
Use source, context, and slash mode without delimiter ambiguity, for example an exact serialized tuple.

`renderDocument(input, { isCurrent })` inside MarkdownContent replaces the old overlapping `render` entry point.
It returns `{ blocks, cacheEntries }` or `MARKDOWN_RENDER_CANCELLED`.
`cacheEntries` contains only successful completed entries used by this operation.
Repeated blocks reuse operation staging entries. Failed Mermaid fallback can appear in output but does not enter the cache.
`renderOneBlock`, `postProcessIn`, and `renderMermaidIn` receive ownership through an explicit parameter.
Nested user renders capture a component-local document/tool revision when they start.
Increment that revision synchronously on source/theme/slash changes, eligibility loss, and disposal.
Their predicate requires captured revision equality, current eligibility, component lifetime, and ownership by the current root.
Connectivity alone is insufficient because the previous complete DOM stays connected during replacement work.
They do not enter the document coordinator. Automatic restoration also requires the coordinator commit ownership predicate.
`restoreCodeToolsState(isCurrent)` and `applyCodeRendered(wrapper, rendered, isCurrent)` guard after awaits and before state writes.

## Verification commands

Run commands from the worktree root. No dependency installation is part of this plan.

```bash
# COORDINATOR_TESTS
node --test frontend/src/utils/markdownRenderCoordinator.test.js
# ELIGIBILITY_TESTS
node --test frontend/src/composables/useMarkdownRenderEligibility.test.js frontend/src/composables/useStreamingPublication.test.js
# MARKDOWN_TESTS (after Task 3)
node --test frontend/src/utils/markdownRenderCoordinator.test.js frontend/src/composables/useMarkdownRenderEligibility.test.js frontend/src/utils/markdownRenderCache.test.js frontend/src/components/ui/MarkdownContent.render.test.js frontend/src/utils/markdownColonBlocks.test.js
# FULL_TESTS
npm --prefix frontend test
# BUILD
npm --prefix frontend run build
# WHITESPACE
 git diff --check
```

`frontend/package.json` defines node:test auto-discovery and the SPA/standalone-share builds.
The production build checks SFC integration and standalone consumers; mocked function tests do not replace it.
Generated build output is not a task-owned commit artifact. Inspect status and preserve unrelated generated/user files.
Success means exit 0, zero failed tests, and no new build error.
Test harnesses use `t.after` or `try/finally` to restore every fake global, listener, Vue app, and injected sink.
Resolve or reject every owned deferred operation before teardown; dispose coordinators and remove their temporary artifacts.
Run test files without shared mutable globals where possible.
Use an existing installed dependency tree. A missing dependency blocks that command; report it without installing packages.

## Task 1: Implement and prove latest-only coordinator ownership

**Files:** coordinator module and its tests.
**Dependency:** none. This task does not wire the component yet.

- [ ] **Step 1:** Add deferred-Promise tests for one active operation and one replaceable pending input.
  Hold A; submit B, C, D; assert peak active count is 1 and starts equal `[A]` before resolving.
  Resolve A; assert no A commit, starts equal `[A, D]`, and only D commits.
- [ ] **Step 2:** Add RED ownership traces: A → B → A, identical current-active/pending/committed input, and synchronous source/theme/slash changes before one drain microtask.
  A → B → A must start `[A, A]`, with the first revision never committing.
  Drive injected `schedule` callbacks explicitly; assert one queued drain and no timer/RAF use.
- [ ] **Step 3:** Add RED lifecycle/error traces: hide while A awaits, hidden B/C, show before/after A settles, dispose twice, rejecting A, and a throwing error sink.
  Assert no hidden start/commit, latest resume only, all rejections consumed, no obsolete error, and later work proceeds.
  Return the cancellation sentinel while current: one start, no commit/report, no extra drain, and rendering=false. Then changed input progresses.
- [ ] **Step 4:** Add reentrant commit/state callbacks that request another source or hide/dispose.
  Assert current ownership checks prevent stale flag clearing or duplicate drains, and successor input survives.
- [ ] **Step 5:** Run COORDINATOR_TESTS. Confirm failures reflect absent scheduling behavior, not test harness errors.
- [ ] **Step 6:** Implement the documented coordinator signatures and revision state.
  Keep active ownership until the Promise settles. Guard both render and commit failures with guaranteed release.
- [ ] **Step 7:** Run COORDINATOR_TESTS. All traces pass with exact start/commit/error/state counts.
- [ ] **Step 8:** Review and commit owned files: `perf(markdown): serialize document render requests`.

## Task 2: Supply lifecycle eligibility without blocking initial reveal

**Files:** eligibility composable/tests, keys, SessionItemsList provider.
**Dependency:** Task 1 interface. This task supplies the component integration gate for Task 3.

- [ ] **Step 1:** Add a real Vue custom-renderer harness, following `useStreamingPublication.test.js`.
  Provide reactive view, row intersection, and scrollerActive fields. Use a fake document with listener counters.
- [ ] **Step 2:** Add RED tests for mounted default consumers, initial activation, KeepAlive deactivation/reactivation, document hide/show, row outside/inside/unknown, and unmount listener cleanup.
  Missing contexts allow rendering. Unknown row plus positive viewport allows rendering.
  Inactive view or suspended scroller suppresses work. Each transition reports the correct immediate eligibility value.
- [ ] **Step 3:** Execute the actual SessionItemsList Markdown-provider computed expression with reactive inputs.
  Set reveal hidden true and ready active view true: eligibility stays true.
  Set props.viewActive false: eligibility becomes false. Existing streaming provider still includes reveal hidden.
- [ ] **Step 4:** Run ELIGIBILITY_TESTS; confirm RED failures identify missing gate or lifecycle wiring.
- [ ] **Step 5:** Implement the key, independent provider, and lifecycle composable.
  Use existing Vue hooks and optional context values. Add no store/composable import cycle.
- [ ] **Step 6:** Connect a test coordinator to actual eligibility transitions.
  Hold active work through hide/show and KeepAlive transitions. Assert one active operation and latest-only resume.
- [ ] **Step 7:** Run ELIGIBILITY_TESTS and COORDINATOR_TESTS. Existing streaming publication tests remain unchanged and pass.
- [ ] **Step 8:** Review and commit: `perf(markdown): suspend render work outside active views`.

## Task 3: Integrate the block pipeline, cache, and tool ownership

**Files:** MarkdownContent, cache utility/tests, actual component render harness.
**Dependencies:** Tasks 1–2.

- [ ] **Step 1:** Add cache-key RED cases: same source with changed reference href/title, reordered equivalent definitions, theme change for Mermaid/non-Mermaid, first-block slash change, and delimiter-like source.
  Equivalent contexts compare equal. Different rendering contexts never compare equal.
- [ ] **Step 2:** Extract actual component functions into a controlled node:test harness, following existing source-extracted SFC tests.
  Fail clearly if extraction boundaries disappear. Inject the real coordinator, Vue refs/watch/nextTick, and controllable parser/highlighter/Mermaid/DOM dependencies.
  Do not copy the production algorithm into tests. Count real function entry, cache insertion, block commit, and emit calls.
- [ ] **Step 3:** Add RED checkpoints: supersede while first block highlights; supersede between Mermaid diagrams; hide while Mermaid loads; unmount while a library Promise rejects.
  Assert next block/diagram/post-process never starts, cache never promotes, old blocks stay, and only current failure reports.
- [ ] **Step 4:** Add RED output/cache cases: complete final text after backlog, empty source, repeated identical blocks, unchanged successful block reuse, failed Mermaid fallback, cancelled staging, changed reference definitions, and unique occurrence keys.
  Use authoritative expected output for reference href/title, slash tags, fallback content, and block keys.
- [ ] **Step 5:** Add RED tool cases: nextTick restoration after source change/unmount; nested render await followed by replacement/hide; failed nested render after invalidation.
  Assert no stale DOM attachment, toggle-state deletion, error toast, or codeToolsState write.
  Hold nested work, change source while its replacement remains deferred, and keep the old wrapper connected.
  Resolve nested work: assert no effect. Repeat hide → show before nested completion; invalidated ownership stays invalid.
- [ ] **Step 6:** Run MARKDOWN_TESTS. Verify RED failures exercise production function paths.
- [ ] **Step 7:** Implement exact cache keys, per-operation staging, and cancellation checkpoints in the existing pipeline.
  Snapshot slash mode instead of reading current props during a block loop.
- [ ] **Step 8:** Wire one coordinator in component setup.
  Submit source/theme/slash through one immediate synchronous watcher. Set eligibility through a synchronous watcher.
  Mount/activation hooks must not directly launch a second render. Dispose coordinator on scope destruction.
  Commit staging eviction/promotion, blocks, and guarded restoration only while current.
  Emit `rendered` only for current successful output. Catch error-sink exceptions without stranding the coordinator.
- [ ] **Step 9:** Guard nested tool paths and restoration after every await, including state mutation and failure toast paths.
  Keep user-triggered nested work separate from the document queue.
- [ ] **Step 10:** Run MARKDOWN_TESTS, ELIGIBILITY_TESTS, and FULL_TESTS. Run BUILD and WHITESPACE.
  Inspect build output and any test failures before proceeding.
- [ ] **Step 11:** Review and commit: `perf(markdown): stop obsolete block rendering and stage cache results`.

## Task 4: Validate real output and browser behavior

**Files:** browser fixture, baseline preparation, fixture tests, ignore entry, execution report.
**Dependency:** Task 3. This task supplies product evidence, not a replacement for deterministic tests.

- [ ] **Step 1:** Add RED preparation/fixture tests.
  Pin the baseline component source to `f5d52630`. Generate `MarkdownRenderingBaseline.vue` from the pin and `MarkdownRenderingCurrent.vue` from the reviewed current component, in the same UI directory. Preserve relative imports.
  Apply identical fixture-only operation counters to both copies. Never alter the production source or Vite configuration.
  Require the exact worktree root. Use exclusive creation and roll back only newly created files.
  Persist `frontend/tests/browser/.markdown-rendering-generation.json` with version, random generation ID, pinned baseline commit, and SHA-256 digests for the two exact allowed adapter paths.
  Add the generation ID as a comment in each generated adapter. Create the manifest exclusively after both adapters succeed.
  Refuse preparation if any adapter or manifest already exists. Never overwrite unknown files.
  Removal validates the manifest schema, fixed allowed paths, generation comments, and digests for all present adapters before deleting any file.
  Validate the baseline against its pinned instrumented source as well. Do not recompute the current adapter from mutable production source.
  Remove the owned manifest only after its owned adapters are removed. Missing manifest means unknown ownership: refuse adapter removal.
  Preparation failure removes only files exclusively created by that invocation. Cleanup failure retains the ownership record for a later guarded retry.
  Tests: edit original current source after preparation and remove successfully; edit an adapter and refuse removal without partial deletion; fail halfway and preserve all pre-existing files.
  Use temporary directories for preparation unit tests. Never write a real adapter during node:test.
- [ ] **Step 2:** Create visible fixture controls for isolated current/baseline real MarkdownContent runs.
  Load optional instrumented current/baseline copies through runtime variable paths.
  Normal mode imports the unchanged production component and loads without generated files. Counted comparison requires preparation.
  Disable controls until startup completes. Record explicit startup failures.
  Feed the same 100 source snapshots at fixed 20 ms deadlines, then wait for successful latest-source rendering with a finite 15-second diagnostic timeout.
  A timeout is failed/inconclusive evidence, not permission to flush or inject rendered results.
- [ ] **Step 3:** Add real-render source scenarios: growing paragraphs, fenced code, several Mermaid diagrams, reference definition changes, slash-mode changes, and empty final source.
  Instrument actual source-driven parsing/block/Mermaid operations and active concurrency through the prepared SFC copies.
  Use the same counters for baseline/current. Do not add production debug exports or count watcher calls as render executions.
  The preparation script inserts an optional injected metrics sink and wraps the document operation in start/finally-finish counters.
  It inserts entry counters at split/block/Mermaid call boundaries without changing their control flow.
  Assert each reviewed insertion anchor matches exactly once; fail preparation otherwise.
  Keep generated modules restricted to fixture imports and preserve executable production statements.
  Unit tests compare stripped instrumentation with the original component source and verify release on throw/cancellation.
  Preparation exports `prepareMarkdownRenderingBaseline({ root, operations })` and `removeMarkdownRenderingBaseline({ root, operations })`; CLI defaults root to the validated current worktree.
- [ ] **Step 4:** Add separate current implementation checks through production SessionView/SessionItemsList/KeepAlive routing.
  Reuse the established fixture seeding conventions and mutation-fetch blockade from `invisibleStreaming.js`.
  Exercise initial reveal, session switch, composer typing without send, open thinking, document visibility, theme, and code-tool restoration.
  Do not claim isolated baseline renders are full-conversation comparisons.
- [ ] **Step 5:** Record report fields: actual feed times, started request tuples, peak active document operations, parsed sources, block/Mermaid starts, commits/emits, final DOM source/content checks, errors, visibility, and viewport.
  For cancelled staging, use deterministic tests as proof; do not add production inspection hooks.
  Reference output checks inspect real link href/title. Check diagrams, code tools, sanitization, lists, tables, colon blocks, comments, and share/file-link compatibility using known fixtures.
- [ ] **Step 6:** Run fixture tests, MARKDOWN_TESTS, FULL_TESTS, BUILD, and WHITESPACE.
  Record exact counts and exits in the execution document.
- [ ] **Step 7:** If existing browser tooling and worktree servers are available, compare isolated baseline/current on desktop and mobile.
  Then run production conversation checks for Claude and Codex.
  Observe actual successful `scrollToKey` return, row element presence, and component content separately before final rendering assertions.
  Do not use a fixed wait that competes with the eight-RAF retirement anchor restore.
  Report no available browser, sparse frame opportunities, or timed-out library work as explicit pending/inconclusive acceptance.
- [ ] **Step 8:** Close fixture tabs, reset temporary viewport, and safely remove the adapter.
  Run `node frontend/tests/browser/prepareMarkdownRenderingBaseline.mjs --remove` only after comparison tabs close.
  Preserve existing worktree servers. Do not restart or install packages.
- [ ] **Step 9:** Run whole-change independent adversarial review.
  Fix significant findings through scoped corrections, rerun affected checks, and obtain scoped re-review.
  Record unresolved visual/performance limits without claiming the global freeze is fixed.
- [ ] **Step 10:** Commit owned artifacts: `test(markdown): verify latest-only rendering and final output`.
  Update plan checkboxes according to actual completion. Keep incomplete browser acceptance unchecked.

## Self-review and spec coverage

| Specification area | Owning task and proof |
| --- | --- |
| Latest-only serial ownership and A → B → A | Task 1 deferred traces |
| Failure release, cancellation, state ownership | Task 1 and Task 3 actual pipeline |
| Initial reveal, missing contexts, hide/resume, disposal | Task 2 real Vue hooks; Task 4 real conversation |
| Expensive phase checkpoints | Task 3 deferred blocks/diagrams |
| Reference-aware bounded cache | Task 3 key and staging cases; Task 4 actual links |
| Final complete/empty output and render event | Task 3 and Task 4 |
| Nested tool DOM/state ownership | Task 3 and Task 4 |
| Full parser compatibility and standalone consumers | Task 4 output cases and BUILD |
| Performance limits and evidence quality | Task 4 reports and execution record |

No task changes backend or streaming APIs. Interface names above are authoritative across tasks.
The plan deliberately separates isolated renderer comparison from production conversation validation.

## Adversarial review record

Round 1 finds nested-tool revision/eligibility gaps, mutable adapter cleanup ownership, and current cancellation retry ambiguity.
Corrections define captured tool revisions, a saved generation manifest, and terminal cancellation without automatic retry.
The plan also requires harness teardown.
Scoped re-review approves all three corrections. No significant finding remains open.

## Execution handoff

Stop after this plan's review and commit. Wait for the user's implementation go.
Keep the current worktree and branch. Do not merge or deploy the document commits.
