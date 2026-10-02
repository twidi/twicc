# Effective scroller suspension execution

## Scope and state

Tasks 1–3 are committed as `70d22083`, `9bf5b1bd`, and `383f8ca3`.
Task 4 extends the production SessionView fixture and its contract tests.
Deterministic checks pass. Browser acceptance and independent review remain pending.
The reported application freeze has not been validated as fixed.

## Fixture coverage

`frontend/tests/browser/invisibleStreaming.js` retains its synthetic sessions, read-only API responder, actual SessionView, RouterView/KeepAlive, dock tabs, and priority 2 controls.
It now exposes these named controls on `window.invisibleStreamingFixture`:

| Control | Scenario |
| --- | --- |
| `runLargeHistorySuspension({ mode, replacements, expectedViewport })` | Seed 2,000 main-session history rows. Keep a second main Chat scroller visible. Hide the target with KeepAlive, a subagent tab, or the Tasks dock. Replace line 1,000 60 times by default. Record frozen and resumed geometry. |
| `runPendingRevealHide({ expectedViewport })` | Start a real `scrollToKey()` reveal, switch sessions immediately, record its result, and return. |
| `runReconcileHideReturn({ expectedViewport })` | Render and measure a tall streamed block. Read inside it above the bottom. Reconcile it into a final item, then hide and return before the parent's eight-frame restore completes. Compare the returned anchor and final row before any explicit scroller navigation. |

`expectedViewport` accepts `desktop` or `mobile` and checks the current browser width.
Each scenario records the actual viewport width and height.
The mobile fixture skips only its desktop Tasks-dock preflight.
The target's scroller diagnostics include published positions, visible range, anchor, raw scroll state, and rendered anchor.
The fixture uses Vue `unref()` because exposed component refs can be unwrapped by the public proxy.
These diagnostics do not add a product API or count geometry builds.
The reconciliation control asserts that the streamed height exists and is seeded onto the final row.
It counts `setScrollTop()` calls matching the saved position during the route transitions.
The browser run must confirm the anchor tolerance and final rendered text.

## Deterministic verification

All commands ran from `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`.
The worktree has no `frontend/node_modules` directory.
The read-only ESM resolver is `.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs`.
It resolves bare imports from the main checkout without writing to either dependency tree.

| Check | Command and result |
| --- | --- |
| RED fixture contract | `node --loader ./.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs --test frontend/src/utils/invisibleStreamingFixture.test.js` — 2 pass, 4 expected fail before fixture changes. |
| Final targeted set | `node --loader ./.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs --test frontend/src/composables/useVirtualScroll.test.js frontend/src/composables/useVirtualScrollSuspension.test.js frontend/src/stores/streamingRows.test.js frontend/src/utils/invisibleStreamingFixture.test.js` — **96 pass, 0 fail**. Raw output: `.superpowers/sdd/2026-10-02-effective-scroller-suspension-implementation-plan/task-4-targeted-test-final.log`. |
| Syntax and whitespace | `node --check frontend/tests/browser/invisibleStreaming.js`, `node --check frontend/src/utils/invisibleStreamingFixture.test.js`, and `git diff --check` — all pass. No SFC changed. |

The first full-suite run used `NODE_OPTIONS='--loader /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows/.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs' npm --prefix frontend test`.
It passed 1,522 of 1,525 tests.
Three tests failed because CommonJS `createRequire().resolve('@awesome.me/webawesome/package.json')` bypassed the ESM loader.
Raw output: `.superpowers/sdd/2026-10-02-effective-scroller-suspension-implementation-plan/task-4-full-frontend-test.log`.

The corrected full-suite command was:

```bash
NODE_PATH=/home/twidi/dev/twicc-poc/frontend/node_modules NODE_OPTIONS='--loader /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows/.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs' npm --prefix frontend test
```

It passed **1,529 of 1,531 tests**.
The two failures match priority 2's recorded direct worktree file reads:

- `Lit guard: createRenderRoot adopts this.constructor.elementStyles`: missing `frontend/node_modules/@lit/reactive-element/reactive-element.js`.
- `Notivue guard: version 2.4.5`: missing `frontend/node_modules/notivue/package.json`.

Raw output: `.superpowers/sdd/2026-10-02-effective-scroller-suspension-implementation-plan/task-4-full-frontend-test-corrected.log`.
The three previously failing test files are unchanged from baseline commit `e56858ce` (`git diff e56858ce -- frontend/src/styles/glide.test.js frontend/src/utils/detailsMotion.test.js frontend/src/utils/waMotionStyles.test.js` is empty).
A focused `NODE_PATH` probe of these files passed 85 of 87 tests and reproduced only the two direct-file failures.
Raw output: `.superpowers/sdd/2026-10-02-effective-scroller-suspension-implementation-plan/task-4-dependency-probe.log`.
The corrected environment uses existing dependencies. It is not an installed worktree build.

## Browser acceptance pending

Server startup and a baseline checkout are not authorized.
No server started, no browser scenario ran, and no browser geometry or work counts were collected.
The fixture's `?baseline=1` option selects the priority 2 data-store baseline.
It is not the priority 3 comparison baseline `e56858ce`.

When authorized, compare identical seeded scenarios on `e56858ce` and this branch.
Hold priority 2 constant and use the same desktop and mobile viewport sizes.
Verify saved reading anchors, latest rows and text, bottom following, scroll-up stability, visible streams, pending reveal cancellation, and rapid reconciliation hide/return.
Repeat hide/show cycles.
For each pass, set debugger logpoints once before iteration.
Count baseline `positions` mapping, inline watcher key-set creation, and zero-height invalidation at their specified boundaries.
Count new `livePositions`, `cleanupHeightCache(currentItems)`, and the actual zero-height pass at their corresponding boundaries.
Use separate counters. Remove the logpoints after counting.
Do not use rendered-window `itemKey()` calls or logpoint timing as geometry-build measurements.

## Review

Task 4 self-review checked actual scroller access and corrected exposed-ref reads with `unref()`.
The first independent review found an offscreen reconciliation setup.
The fixture now measures the visible streamed row and checks its returned anchor before any explicit reveal.
The final whole-priority review found one parent integration defect.
`SessionItemsList.vue` read the exposed `suspended` boolean through `.value`.
That read missed deferred resume and consumed the near-bottom override while Chat remained hidden.
The controller extended the file map only for `SessionItemsList.vue` and its focused resume test.
The fix uses `unref()` in the watcher and the post-resume guard.
The focused test runs the actual parent watcher and handler against a real Vue-exposed scroller proxy.
It verifies that hidden activation retains saved state, actual resume applies the bottom override once, and a no-new-row return clears state without scrolling.
This extension adds one component test file and three production-line edits; it does not change parent stream-swap cancellation.

The new test failed before the fix: the hidden return attempted one bottom scroll, and the no-new-row return cleared state early.
After the fix, the focused test passed 2/2.
The scoped set passed **99/99** across the new test, both scroller test files, streaming rows, and the browser fixture contract.
`SessionItemsList.vue` compiled with the main checkout's installed Vue SFC compiler through the existing read-only resolver.
`node --check` for the new test and `git diff --check` passed.
The resolver emitted Node's experimental-loader warning.
The full frontend suite was not rerun for this scoped correction; its earlier 1,529/1,531 result and two direct dependency-file failures remain recorded above.
Final scoped review and browser acceptance remain pending.
No browser acceptance claim is made.
