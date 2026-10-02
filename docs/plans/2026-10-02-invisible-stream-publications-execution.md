# Invisible stream publications: implementation evidence

## Status

Tasks 1–4 have implementation commits. Task 5 has a prepared browser fixture.
**Task 5 is incomplete.** Controlled browser and baseline scroll acceptance remain unexecuted.
No worktree server startup authorization is received. The implementation does not use the main instance.
The parent agent owns independent implementation review. This record contains no review acceptance claim.

## Commits

| Task | Commit | Change |
|---|---|---|
| 1 | `1570ba7b` | Safe suspension, retained text, catch-up, scheduler request ownership |
| 2 | `82b1f067` | Consumer aggregation, document gate, shared bootstrap reservation, managed buffer bindings |
| 3 | `beee0e5d` | Suspended production defaults, canonical structural snapshots, immutable generation guards |
| 4 | `3be48e85` | Explicit view context, scroller intersection and height gate, scoped text/raw/thinking ownership |

Base specification: `aeaf1838`. Reviewed implementation plan: `1ceb230f`.
All changes stay in `.worktrees/bugfix-stable-streaming-rows`, branch `bugfix/stable-streaming-rows`.

## Deterministic verification

- Task 1 RED: inactive feeds schedule a frame; flush returns no retained text; active/snapshot operations are missing.
- Task 2 RED: registry module is missing.
- Task 3 RED: production start feeds schedule one frame with no consumer.
- Task 4 RED: observer and ownership modules are missing.
- Same-element observer retarget RED: a queued old outside event changes the new registration.
- GREEN targeted suite: **38/38 pass**.
- Changed SFC verification: **9/9 script and template pairs compile** with the existing Vue compiler.
- Fixture and baseline helper pass `node --check`.
- Baseline helper creates modules from `aeaf1838`, the copied store passes syntax checking, and helper removal succeeds.
- `git diff --check` passes.

Worktree `frontend/node_modules` is absent. A scratch ESM resolver reads main frontend dependencies without writes or symlinks.
It redirects only failed bare ESM imports, preserving import conditions. `NODE_PATH` supplies existing CJS dependencies.
The resolver remains in this plan's ignored `.superpowers/sdd/` workspace.

Targeted command, from the worktree root:

```bash
node --loader ./.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs --test frontend/src/utils/streamingBuffer.test.js frontend/src/utils/streamPublicationRegistry.test.js frontend/src/utils/rowVisibilityObserver.test.js frontend/src/composables/useStreamingPublication.test.js frontend/src/stores/streamingRows.test.js
```

Full frontend suite command, from the worktree root:

```bash
NODE_PATH=/home/twidi/dev/twicc-poc/frontend/node_modules NODE_OPTIONS='--loader /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows/.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs' npm --prefix frontend test
```

Latest full run: **1499/1501 pass**, with two environment failures in `waMotionStyles.test.js`:

- `Lit guard: createRenderRoot adopts this.constructor.elementStyles` reads absent worktree `node_modules/@lit/reactive-element/reactive-element.js`.
- `Notivue guard: version 2.4.5` reads absent worktree `node_modules/notivue/package.json`.

One later source-extracted SessionItem identity regression test passes independently and in the final targeted 38-test run.
The full suite is not claimed green. No dependency installation is performed.
An earlier loader run has four dependency failures. Import-condition and CJS resolution corrections remove two of them.
No Python tests apply to these frontend-only changes.

## Fixture and substitutions

`frontend/tests/browser/invisibleStreaming.html` mounts the production `SessionView` through a memory router and `KeepAlive`.
It uses actual `SessionLayout`, `useSessionLayout`, `SessionContent`, `SessionItemsList`, scroller, and provider SFCs.
It loads production CSS, Web Awesome components, and production detail-motion installers.
It does not load `main.js` or connect a WebSocket.

The fixture seeds synthetic project/session IDs, fetched metadata, real content envelopes, and process state.
Sessions use `draft: true` to prevent layout persistence from creating backend mutations. This bypasses first-load HTTP flows.
History remains seeded and rendered through production recomputation. Initial-load fidelity is **unvalidated**.
The fixture manually requests production recomputation after the settings display-mode action because `main.js` installs that watcher.
This adapter changes no filtering algorithm.

The fixture-local fetch responder accepts only GET requests for declared synthetic session subagent/tool/workflow links.
It records each substitute and rejects all mutations and unexpected reads.
These routes are prepared; their runtime sufficiency remains unvalidated.

Diagnostics count buffer RAF scheduling separately from independent scroller/motion RAF.
Store drain wrappers count drains and actual current-cache parsed-envelope replacements.
Registry wrappers count aggregate transitions and bootstrap requests.
Mixin diagnostics record outer row component UIDs. Geometry reads record physical scroll position and anchor offsets.

Prepared controls cover closed thinking, visible text/thinking/proposed plans, final `addSessionItems` reconciliation, tasks-route ownership,
actual layout maximize/restore, subagent selection, KeepAlive session switching, display-mode changes, and same-field reconnect.
The console API is `window.invisibleStreamingFixture`.
Required hidden delta counters reset after observer/reveal settling.
Visible scenarios use real exposed scroller navigation. They never force `scrollTop = scrollHeight` to hide baseline failures.
Bottom acceptance uses the production **150 px** threshold. Reading-above tolerance is **2 px**.
No physical geometry measurements or browser pass results exist yet.

## Baseline procedure

After worktree startup authorization and normal devctl setup:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node frontend/tests/browser/prepareInvisibleStreamingBaseline.mjs
```

Open fresh pages for each provider and mode:

- `/tests/browser/invisibleStreaming.html?provider=claude_code`
- `/tests/browser/invisibleStreaming.html?provider=codex`
- Add `&baseline=1` for `aeaf1838`.

The helper creates exactly two temporary modules beside their originals.
It changes only the baseline store's buffer import.
The baseline factory creates `defineStore('data')` first in a fresh Pinia.
The fixture asserts `useDataStore(pinia)` returns that same instance before mounting production components.
A fresh page separates baseline and implementation state.

Remove adapters before committing:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node frontend/tests/browser/prepareInvisibleStreamingBaseline.mjs --remove
```

## Unexecuted browser acceptance

Every case below remains **unexecuted** for both providers. Unit evidence does not replace these cases.

| Required case | Browser status |
|---|---|
| Visible text/thinking and stable outer row instances | Unexecuted |
| Codex proposed-plan dispatch changes | Unexecuted |
| Closed thinking: zero drains and correct spinner/stop state | Unexecuted |
| Close/reopen during the detail fold | Unexecuted |
| Actual group close/reopen with retained closing content | Unexecuted |
| Display-mode filter/restore | Unexecuted |
| Cache rebuild after hidden bootstrap | Unexecuted |
| Empty mount before first delta | Unexecuted |
| Zero-height scroller and fresh observation on recovery | Unexecuted |
| Offscreen outside 200 px and return-to-row catch-up | Unexecuted |
| Inactive center Chat tab and switch back | Unexecuted |
| Tool dock route with shown center Chat | Unexecuted |
| Tool dock maximize/restore | Unexecuted |
| Selected subagent versus hidden main Chat | Unexecuted |
| KeepAlive switch and current-generation acquisition on return | Unexecuted |
| Two simultaneous views sharing one block | Unexecuted; secondary fixture not yet prepared |
| Actual document hide/show | Unexecuted |
| Provider final-item replacement and detail-state transfer | Unexecuted |
| Next message reuses the synthetic index | Unexecuted |
| Reconnect with identical message fields | Unexecuted |
| Visible raw JSON ownership and formatted-body release | Unexecuted |
| Baseline/implementation bottom following and reading-above anchors | Unexecuted |

## Decisions and limits

Same-target IO registration reuse renews the shared observation epoch.
This rejects old queued target entries without geometry polling or per-delta work.
Recovery from zero height or suspension resets retained rows to unknown before fresh observation.
Missing IntersectionObserver degrades mounted rows to inside, with view/body/document/height gates retained.
Offscreen savings are unavailable in that degraded mode.

The registry has no store import. Owners carry the immutable stamp from their rendered row.
An old exit-only row cannot derive the replacement generation from its reused line number.
Canonical text, inactivity timers, stopped flags, and structural lifecycle handling remain independent of body eligibility.

No package install, server start/restart, main-checkout change, merge, push, provider message, or user message is performed.
Browser fixture fidelity, provider visual behavior, scroll behavior, and complete lifecycle acceptance remain material verification gaps.
