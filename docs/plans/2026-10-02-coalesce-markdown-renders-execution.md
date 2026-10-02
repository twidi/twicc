# Real Markdown browser validation

## Scope

These fixtures separate three forms of evidence:

- Deterministic coordinator/component tests prove scheduling, cancellation, cache staging, and tool ownership.
- Isolated real MarkdownContent runs prove parser/highlighter/Mermaid/DOM compatibility and count actual operations.
- Production conversation checks use SessionView, SessionItemsList, and KeepAlive from the existing conversation fixture.

No production component, Vite configuration, backend API, or streaming policy changes here.
The controller owns browser acceptance and the whole-change adversarial review.
Do not infer a complete application performance fix from these fixtures.

The additional `markdownRenderingConversation.html` and `.js` entries are a controller-approved, bounded filemap extension.
They import `invisibleStreaming.js` unchanged. They reuse its fixture-only store, canonical Codex `Text`, read-route substitutions, and mutation-fetch blockade.
They add Markdown-specific acceptance controls without copying conversation seeding.
They reject baseline query flags. Isolated baseline comparisons never enter the production conversation.

## Start conditions

Use the existing worktree frontend. Do not restart servers or install packages.
The recorded frontend is `http://localhost:5175`; the recorded backend is `http://localhost:3502`.
Verify the actual ports before browser use.

Normal mode needs no generated files:

`http://localhost:5175/tests/browser/markdownRendering.html`

Counted comparison needs exclusive preparation:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node frontend/tests/browser/prepareMarkdownRenderingBaseline.mjs
```

Use one counted renderer per page, sequentially:

- `http://localhost:5175/tests/browser/markdownRendering.html?comparison=1&renderer=baseline`
- `http://localhost:5175/tests/browser/markdownRendering.html?comparison=1&renderer=current`

Both adapters use identical instrumentation and the same 100-snapshot, 20 ms deadline feed.
Separate pages avoid concurrent baseline/current contention in the shared Mermaid library.
Record which run starts cold and which starts warm. Record page load/module cache conditions.
The fixture records actual deadlines, feed times, and absolute feed timestamps. Late feeds remain visible in evidence.
It does not force rendering, flush coordinators, or inject generated HTML.

## Guarded generation and removal

Preparation requires the exact worktree root.
It reads baseline `f5d52630` and the reviewed current production source at preparation time.
Current adapter removal uses its saved SHA-256 digest. Later production edits do not change ownership.
The baseline additionally must match the pinned, instrumented source.

Preparation refuses any existing adapter or generation manifest.
It uses exclusive file creation and rolls back only files created by that invocation.
The normal manifest follows successful creation of both adapters.
A controller-approved recovery exception saves an exclusive manifest if own-file rollback fails.
Its `createdPaths` records only adapters exclusively created by that invocation.
A racing unowned adapter stays untouched during later recovery cleanup.
The original generation ID and expected digests remain unchanged.
If exclusive recovery-record creation also fails, preparation reports the rollback and record failures together.
No unknown record is overwritten.

Removal validates schema, fixed allowed paths, generation comments, digests, and pinned baseline contents before deleting any present owned adapter.
Normal ownership includes both adapters. Recovery ownership can include one adapter.
Missing manifest means unknown ownership and refuses deletion.
A cleanup failure retains the manifest for a guarded retry.
The manifest disappears only after all owned adapters are removed.
Temporary-directory unit tests never create real worktree adapters.

Close all comparison tabs before removal:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node frontend/tests/browser/prepareMarkdownRenderingBaseline.mjs --remove
```

Restore any temporary viewport changes. Preserve existing servers.

## Isolated browser checks

Wait for **Fixture ready**. Startup failures are explicit; controls stay disabled until startup completes.
Run paragraphs, code, Mermaid, references, slash, empty, and compatibility.
Repeat counted baseline/current on desktop and mobile with the same conditions.
Record `window.markdownRenderingFixture.exportEvidence()` after each page's runs.

Each run records:

- Actual feed tuples and deadline timestamps.
- Actual document starts/finishes and peak active document operations.
- Parsed sources, block starts, Mermaid starts, commits, and emit boundaries.
- Actual component `rendered` events and final DOM content/HTML.
- Real reference href/title, diagrams, highlighted code, and tools.
- Errors, visibility transitions, viewport, and unexpected fetches.

Current counted runs fail if document operations overlap.
Counted final evidence waits for all started document operations to settle.
A following scenario refuses to start while earlier counted document work remains active.
Latest-render waits have a finite 15-second diagnostic timeout.
Timeouts and output failures remain **failed/inconclusive**. They never trigger a synthetic completion.
Baseline reference/slash failures can expose old behavior. Preserve their evidence instead of changing expectations.
The slash feed changes only slash mode after the first source assignment.
The empty feed ends with the exact empty source.

Compatibility checks inspect lists, tables, colon blocks, comments, sanitized HTML, file-link annotation, share media rewriting, unavailable media, and nested Markdown tools.
Share checks exercise injected hooks in the actual component. They do not validate the standalone share application.
Cancellation staging remains covered by deterministic tests. These fixtures add no production inspection hooks.

## Production conversation checks

- `http://localhost:5175/tests/browser/markdownRenderingConversation.html?provider=claude_code`
- `http://localhost:5175/tests/browser/markdownRenderingConversation.html?provider=codex`

Wait for **Markdown conversation ready**. Use the Markdown controls at the bottom.
Run initial reveal, KeepAlive switch, open thinking, composer typing without send, and theme/tools.
The theme/tools check replaces a `markdown` fence with an `md` fence containing identical code.
It requires a new wrapper and restoration of remembered wrapping/rendered state.

Each reveal separately records the actual successful `scrollToKey` return, row presence, and component content.
The KeepAlive hide/return routes run directly through the production router.
The control reads the existing live `streamSwapSavedScrollTop` setup getter and scroller suspension state.
It waits for the pending retirement restore and auto-scroll to finish before return `scrollToKey`.
Missing observable state or a 15-second timeout records failed/inconclusive acceptance without return reveal.
No fixed wait or control scroll competes with the eight-RAF retirement restore.
Composer editing uses the real contenteditable editor. It never clicks Send.
The reused fetch responder rejects every mutation and unexpected read.
No settings persistence watcher or live WebSocket bootstrap runs.

For document visibility:

1. Start **Begin document visibility**.
2. Switch to another browser tab. A real hidden transition writes a new fixture-only source.
3. Return to the fixture tab.
4. Run **Finish document visibility**.

The check requires actual hidden/visible evidence. A synthetic visibility event is insufficient.
Export `window.markdownConversationFixture.exportEvidence()`.
Close fixture tabs at completion.

## Verification record

| Check | Command | Result |
| --- | --- | --- |
| Fixture tests | `node --test frontend/tests/browser/markdownRenderingFixture.test.js frontend/tests/browser/prepareMarkdownRenderingBaseline.test.js` | Exit 0; 22 passed, 0 failed |
| MARKDOWN_TESTS | `node --test frontend/src/utils/markdownRenderCoordinator.test.js frontend/src/utils/markdownRenderCache.test.js frontend/src/components/ui/MarkdownContent.render.test.js frontend/src/composables/useMarkdownRenderEligibility.test.js frontend/src/utils/markdownColonBlocks.test.js frontend/src/styles/markdown-colours.test.js` | Exit 0; 70 passed, 0 failed |
| FULL_TESTS | `npm --prefix frontend test` | Exit 0; 1644 passed, 0 failed |
| BUILD | `npm --prefix frontend run build` | Exit 0; SPA, broker shim, shell, companion, and share-session bundles succeed |
| WHITESPACE | `git diff --check` | Exit 0 |

Complete outputs: `/tmp/task4-fixture-final.log`, `/tmp/task4-markdown-final.log`, `/tmp/task4-full-final.log`, `/tmp/task4-build-final.log`.
Existing build chunk-size warnings remain. No build errors occur.
The Task 4 scratch report preserves detailed command chronology and limits.
The committed deterministic tests use temporary filesystem fixtures and compile both instrumented SFCs in memory.
The frontend build verifies the production bundles; Vite serves the test HTML entries directly.
Browser acceptance remains controller-owned until its final review and evidence record.

Task 3 chronology remains explicit: initial cache RED was a module-existence failure.
Component tests initially followed implementation. A temporary stale-tool mutation later produced two behavioral failures.
The application-error-handler review fix added four failing checks before the scoped correction.
Its final coordinator/component run passed 40 checks. Commit `ea377157` contains that correction.
Task 4 does not reinterpret that chronology as a full component RED-before-implementation sequence.

Controller preliminary normal-mode compatibility evidence passes lists/table/colon/comment/escaped script, file annotations, share hooks, code tools, and final marker 99.
Recorded viewport: 1273 × 921. No run errors or console errors appear in that preliminary run.
This is preliminary evidence before final review. It is not comparative performance evidence or standalone share-app acceptance.

## Review correction round 1

Baseline commit/emit counters now use the immutable source/theme/slash tuple captured at document entry.
Current emit counters use the coordinator's captured input.
Per-scenario emitted state resets before remount. Counted completion requires latest commit and latest emit.
The execution document moves to this planned `docs/plans/` path.

Scoped fixture RED: 21 checks; 17 passed, 4 failed. Failures cover stale baseline attribution and missing acceptance/restore helpers.
Scoped fixture GREEN: 22 passed, 0 failed. An additional check executes the actual KeepAlive control with controlled restore completion.
Additional pending-diagnostics RED: 22 checks; 21 passed, 1 failed.
Logs: `/tmp/task4-fix-round1-red.log`, `/tmp/task4-fix-round1-diagnostics-red.log`, `/tmp/task4-fix-round1-green.log`.
The controller owns final full-suite, build, browser acceptance, and scoped review checks after this correction.
The earlier full-suite/build counts above describe the pre-correction verification.
