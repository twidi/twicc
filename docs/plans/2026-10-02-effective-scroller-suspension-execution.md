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
| `runReconcileHideReturn({ expectedViewport })` | Reconcile a streamed block into a final session item. Switch sessions before the parent's stream-swap restore completes. Return, then verify the final row. |

`expectedViewport` accepts `desktop` or `mobile` and checks the current browser width.
Each scenario records the actual viewport width and height.
The mobile fixture skips only its desktop Tasks-dock preflight.
The target's scroller diagnostics include published positions, visible range, anchor, raw scroll state, and rendered anchor.
The fixture uses Vue `unref()` because exposed component refs can be unwrapped by the public proxy.
These diagnostics do not add a product API or count geometry builds.

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
Independent adversarial review is pending with the controller.
No browser acceptance claim is made.
