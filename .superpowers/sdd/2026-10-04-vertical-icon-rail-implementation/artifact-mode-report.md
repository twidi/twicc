# Artifact mode adjustments

## Result

- The rail displays global session search only in Sessions mode.
- Entry into an unselected Artifacts route opens the sidebar on desktop and mobile.
- Entry into a selected Artifacts route preserves the current checkbox state.
- Initial selected Artifacts mounts use the stored open state on both viewport sides.
- Initial bare Artifacts mounts open the sidebar on both viewport sides.
- Artifact selection and deselection inside Artifacts do not reopen the sidebar.

## Design decisions

The existing search definition adds `requires: state => state.mode === 'sessions'`.
The existing resolver filters the item. No rail duplication or new abstraction is needed.
Palette and keyboard search behavior remain unchanged.

`initialSidebarChecked` supplies the initial value once. The `checked` ref controls the DOM binding thereafter.
The old reactive initializer can patch the checkbox when `sessionId` disappears on mobile.
That patch opens selected artifact restores. Replacing this binding is a necessary user-request-driven design adjustment.

The Artifacts entry watcher observes `isArtifactsMode` with `flush: 'post'`.
It opens only on false-to-true entry without `bookmarkId`.
It sets the checkbox explicitly and calls the existing synchronization/persistence handler.
The checkbox is true for mobile open and false for desktop open.
The route watcher covers rail, command-palette, and external navigation through their shared route state.

Mobile Sessions behavior observes `[sessionId, isArtifactsMode]`.
The Sessions root opens the drawer. Session selection closes it.
Observing mode also covers Artifacts-to-Sessions-root transitions with unchanged absent `sessionId`.
The Artifacts guard preserves selected restore state.

Remembered mode routes, active-mode no-op, saved width, drag/snap handling, and persistence retain their existing paths.
Viewport changes retain the existing inverted checkbox semantics. No mode-entry effect runs on resize.

## Files

- `frontend/src/utils/sidebarRail.js`: search requirement.
- `frontend/src/utils/sidebarRail.test.js`: search visibility in both modes and sidebar states.
- `frontend/src/views/ProjectView.vue`: controlled checkbox and route-entry handling.
- `frontend/src/views/ProjectViewRail.test.js`: production watcher and navigation behavior with Vue reactivity.
- `.superpowers/sdd/2026-10-04-vertical-icon-rail-implementation/artifact-mode-report.md`: this report.

## RED evidence

Command, from the worktree root:

```sh
node --test frontend/src/utils/sidebarRail.test.js frontend/src/views/ProjectViewRail.test.js
```

After correcting an initial test-harness automatic-semicolon-insertion issue, the run has 13 passes and 5 expected failures:

- Search remains visible in Artifacts (`true !== false`).
- Checkbox still binds to the reactive initializer.
- Closed desktop sidebar does not open on unselected Artifacts entry.
- Selected mobile artifact mount opens instead of respecting stored closed state.
- Bare desktop Artifacts mount remains closed.

A second RED run adds the mobile Sessions-root return case:

```sh
node --test frontend/src/views/ProjectViewRail.test.js
```

Result: 10 passes, 1 expected failure. Returning from selected Artifacts to Sessions root leaves the drawer closed.

## GREEN evidence

Focused command:

```sh
node --test frontend/src/utils/sidebarRail.test.js frontend/src/views/ProjectViewRail.test.js frontend/src/components/sidebar/SidebarRail.test.js frontend/src/styles/rail-button.test.js frontend/src/styles/overlay-motion.test.js frontend/src/styles/main-content-height.test.js
```

Result: **44 tests pass**, zero failures, zero skips.
Both ProjectView and SidebarRail compile script, template, and styles.

Behavior coverage includes absent/present bookmark targets, already open/closed states, desktop/mobile, and `nextTick` scheduling.
Tests execute production watchers with Vue's real scheduler and simulate checkbox property patches.
Tests also cover repeated entry, in-mode selection/deselection, viewport-derived inversion, mobile Sessions return, and exact remembered routes.
Existing active-mode no-op and checkbox synchronization tests remain enabled.

Full frontend command:

```sh
cd frontend && npm test
```

Result: **1867 tests pass**, zero failures, zero skips.

Build command:

```sh
cd frontend && npm run build
```

Result: **all five Vite bundles build successfully** (SPA, shim, shell, companion, share).
Vite emits mixed static/dynamic import warnings and a large-chunk warning.
These warnings concern existing imports and bundle sizing; this change introduces no imports.

Mixed-import warning modules:

- `frontend/node_modules/@codemirror/language/dist/index.js`
- `frontend/src/stores/settings.js`
- `frontend/src/composables/useToast.js`
- `frontend/src/composables/useWebSocket.js`
- `frontend/src/providers/claude_code/ws.js`
- `frontend/src/providers/codex/ws.js`
- `frontend/src/stores/layouts.js`
- `frontend/src/stores/data.js`
- `frontend/src/stores/workspaces.js`
- `frontend/src/components/artifacts/ArtifactBookmarkDialog.vue`
- `frontend/src/components/tips/showTipToast.js`
- `frontend/src/router.js`

`git diff --check` passes.

## Self-review and limits

The changes use the existing declarative rail contract and shared route state.
Sidebar opening is idempotent. It does not toggle an already open sidebar closed.
The entry watcher does not depend on bookmark-only changes.
Selected restore does not inherit the mobile no-session initializer patch.
The existing desktop persistence handler records explicit opening with the existing width.
Mobile persistence behavior retains its existing contract.

No dependency, backend, CHANGELOG, spec, or plan files change.
No server restart or dependency install runs.
No browser matrix runs in this worker task. Native checkbox rendering and Web Awesome layout remain browser-validation concerns.
