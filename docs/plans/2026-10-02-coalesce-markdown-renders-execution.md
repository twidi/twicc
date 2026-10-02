# Real Markdown browser validation

**Status:** Implemented and reviewed at `6c7f5c33`. Final controller checks pass 1,656 tests and five bundle builds.
Current output passes seven scenarios on desktop and mobile viewports. Claude and Codex pass five conversation checks each.
Native document visibility, physical-phone latency, and standalone share-app acceptance remain explicit limits.

## Scope

These fixtures separate three forms of evidence:

- Deterministic coordinator/component tests prove scheduling, cancellation, cache staging, and tool ownership.
- Isolated real MarkdownContent runs prove parser/highlighter/Mermaid/DOM compatibility and count actual operations.
- Production conversation checks use SessionView, SessionItemsList, and KeepAlive from the existing conversation fixture.

The validation fixtures change no production component, Vite configuration, backend API, or streaming policy.
The implementation changes Markdown rendering and its view eligibility.
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
This initial record precedes the final controller evidence below.

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
The controller subsequently completes the final checks recorded below.
The earlier full-suite/build counts above describe the pre-correction verification.


## Controller checks before the final correction

Revision: `15e88249`.
The controller independently runs `npm --prefix frontend test`: 1,649 passed, 0 failed, 0 skipped.
The controller runs `npm --prefix frontend run build`: all five bundles complete successfully.
Existing Node MockTimers and Vite chunk-size warnings remain.
Logs are in this plan's retained `.superpowers/sdd/` workspace: `controller-full-tests.log` and `controller-build.log`.
These checks precede the final correction. They do not replace its fresh verification.

Normal production output passes compatibility and the actual nested Markdown tool.
The desktop viewport is 1273 × 921. No isolated run errors or console errors occur.

Claude conversation checks pass initial reveal, KeepAlive return, open thinking, and theme/tool restoration.
KeepAlive waits for observable restoration before its return reveal.
Theme/tool restoration requires a replacement wrapper and restores the nested rendered view.
The composer control fails twice because it assumes CodeMirror. The actual editor is a Web Awesome shadow textarea.
Manual browser input succeeds in that textarea. Send remains disabled; no Send action occurs.
A startup ResizeObserver notification remains in the fixture diagnostics.
The document-visibility check remains inconclusive: the browser API does not produce an actual hidden transition.
No synthetic visibility event supplies acceptance.

The isolated baseline page uses one renderer and a fresh component for each scenario.
The first Mermaid run loads its libraries cold in that page. Code follows with the page's libraries loaded.
The viewport is 1273 × 870. Source deadlines are identical; actual feed lateness remains recorded.

| Baseline scenario | Final output | Document starts | Peak active | Block starts | Mermaid starts | Maximum feed lateness |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Mermaid | Passed, three diagrams | 89 | 74 | 352 | 264 | 76.9 ms |
| Code | Passed, highlighted final source and tools | 89 | 3 | 176 | 0 | 86.8 ms |
| References | Failed: stale final href/title | 96 | 1 | 96 | 0 | 80.1 ms |

All baseline operations settle before the page closes.
These operation counts do not measure complete application input latency.
The guarded removal command succeeds after the owned fixture pages close.

## Review closure

Task 1 and Task 2 scoped reviews approve their changes.
Task 3 review corrects application error routing; its scoped re-review approves the correction.
Task 4 review corrects tuple attribution, restore observation, scenario event isolation, and matching emit acceptance.
Its scoped re-review marks all four findings addressed and finds no new breakage.
The whole-change review covers `f9102d3c..15e88249` and returns two Important findings.
The final correction must stop obsolete HTML allocation/parsing after highlighting and support the actual textarea composer.
The following final correction section records closure and fresh controller evidence.

## Controller decisions

| Decision | Reason | Cost if wrong |
| --- | --- | --- |
| Accept the documented Task 3 test chronology | Independent review and behavioral mutation proof validate current behavior; retrospective RED would be false | Add missed regression tests and correct the pipeline |
| Add two conversation fixture entry files | Reuse existing production seeding without duplicating provider contracts | Rework fixture composition and controls; production remains unchanged |
| Permit an exclusive recovery generation manifest | Preserve exact ownership after preparation rollback fails | Rework cleanup or manually remove verified test artifacts |
| Keep fixture helpers/tests under `tests/browser` | They have fixture-specific responsibilities and normal test discovery includes them | Move fixture files and adjust imports |

## Behaviors outside final review acceptance

The controller assesses every behavior that the whole-change reviewer declines to judge.
No item supplies hidden acceptance or cancels a required check.

| Behavior | Controller disposition |
| --- | --- |
| Cross-component scheduling and Mermaid contention | Explicit non-goal; only component-local serialization is implemented |
| Interrupting synchronous work already running | Not possible within this design; subsequent stages still require ownership checks |
| Unfinished fences and incremental parsing | Deferred to later priorities |
| TOC parsing cost and temporary outline differences | Unchanged; no optimization claim |
| Shared nested-render Promise deduplication and simultaneous tool toggles | Deferred; stale ownership remains covered |
| Existing clipboard notifications after clipboard awaits | Unchanged; no new clipboard behavior claim |
| Existing occurrence hashes and nested-code key collisions | Unchanged contracts; no new collision evidence |
| Priorities 1–4 backend/scroller/animation algorithms | Integration gates checked; no broad historical audit claim |
| Standalone share application | Injected component hooks pass; standalone acceptance remains pending |
| Complete application freeze resolution | No complete application performance claim |
| Codex conversation and document visibility | Actual browser checks required; unavailable real visibility stays inconclusive |
| ResizeObserver notification | Preserve diagnostics; attribution to this change is unproven |
| Hostile concurrent filesystem replacement during cleanup | Outside the local developer fixture ownership model |
| Installations, migrations, and server restarts | No such change or operation occurs |


## Final correction and verification

Final production revision: `6c7f5c33`.
The single final fix wave corrects both Important findings.
Highlighting returns into a local string. Current ownership is checked before detached-root allocation and HTML assignment.
Both document blocks and nested Markdown use this order.
Six deferred tests cover supersession, visibility loss, and disposal.
The composer control waits for the actual `wa-textarea` and edits its shadow textarea's selection and value.
Its behavioral test also rejects an induced mutation request.

Corrected RED: 33 tests, 26 passed, 7 failed.
An initial draft used an invalid ownership predicate; the corrected RED establishes valid initial ownership.
GREEN covering component/coordinator/fixture/preparation checks: 69 passed, 0 failed.
Both adapters compile and strip back to the exact original executable source.
The scoped final review marks both findings addressed and reports no new breakage.

| Final controller check | Result | Retained log |
| --- | --- | --- |
| `npm --prefix frontend test` | Exit 0; 1,656 passed, 0 failed, 0 skipped; 1,644 top-level tests | Plan workspace `final-full-tests.log` |
| `npm --prefix frontend run build` | Exit 0; all five bundles complete | Plan workspace `final-build.log` |

The existing Node MockTimers and Vite chunk-size warnings remain.

## Final isolated browser output

The reviewed current adapter uses `6c7f5c33`. Each scenario remounts a fresh component.
Each page contains one renderer. Mermaid runs first; code follows; references, slash, empty, paragraphs, and compatibility follow.
Page module instances are fresh at navigation. The browser HTTP cache is not controlled.
Desktop and mobile are browser viewports on this computer, not measurements on a physical phone.
Actual feed deadlines can be late. Counts are work evidence, not controlled latency benchmarks.
Initial empty-source bootstrap can contribute one document start.

| Current scenario | Desktop starts / blocks / Mermaid | Desktop maximum lateness | Mobile starts / blocks / Mermaid | Mobile maximum lateness |
| --- | --- | ---: | --- | ---: |
| Mermaid | 89 / 159 / 99 | 83.0 ms | 90 / 166 / 93 | 51.3 ms |
| Code | 87 / 172 / 0 | 125.7 ms | 87 / 159 / 0 | 68.9 ms |
| References | 101 / 200 / 0 | 9.8 ms | 98 / 194 / 0 | 27.2 ms |
| Slash | 101 / 101 / 0 | 16.5 ms | 101 / 101 / 0 | 9.6 ms |
| Empty | 101 / 99 / 0 | 10.5 ms | 101 / 99 / 0 | 8.5 ms |
| Paragraphs | 95 / 194 / 0 | 132.9 ms | 87 / 186 / 0 | 235.1 ms |
| Compatibility | 87 / 114 / 0 | 244.4 ms | 79 / 106 / 0 | 383.2 ms |

All fourteen current runs pass final DOM checks, matching commit/emit checks, and settlement checks.
Peak active document operations equals one in every current run.
Desktop viewport: 1273 × 921. Mobile viewport: 355 × 767, from a 390 × 844 override at the browser's current zoom.
No isolated run errors or console errors appear.
Current Mermaid renders all three final diagrams, with fewer Mermaid calls than the recorded desktop baseline.
Current references produce the latest href and title; the desktop baseline retains stale values.
These findings do not prove that every application freeze is resolved.


## Final conversation checks

Codex conversation at 355 × 767 passes all five checks: initial reveal, KeepAlive return, open thinking, composer typing, and theme/tool restoration.
The return records observable restoration, no pending restore, and a successful `scrollToKey` result.
The corrected composer check records the exact textarea value and zero mutation requests.
The theme/tool check replaces the wrapper, restores nested Markdown, preserves wrapping, and retains Mermaid SVG after changing theme.
No console errors occur. Two ResizeObserver notifications remain in production-fixture diagnostics.
These fixture controls verify product behavior. They do not measure input latency under a production workload.

The second baseline page unexpectedly returns to 1273 × 870 after a tab change.
Its 279 Mermaid calls and peak 75 are a second desktop sample, not mobile evidence.
The controller preserves that sample and rejects the mobile label.
The mobile baseline is repeated after applying the override to the new tab and checking its actual DOM width.


## Verified mobile baseline comparison

The baseline viewport is checked after navigation: 355 × 767.
The order matches the current page: Mermaid first, then code and references.
Each scenario uses a fresh component; page library instances remain loaded after the first scenario.

| Mobile baseline scenario | Final output | Document starts | Peak active | Block starts | Mermaid calls | Maximum feed lateness |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Mermaid | Passed, three diagrams | 92 | 78 | 364 | 273 | 73.9 ms |
| Code | Passed | 74 | 3 | 146 | 0 | 114.9 ms |
| References | Failed: stale href/title | 85 | 1 | 85 | 0 | 169.0 ms |

All operations settle. No isolated console errors occur.
The current mobile Mermaid run has peak one and 93 Mermaid calls, with the same three final diagrams.
The current desktop Mermaid run has peak one and 99 Mermaid calls, compared with baseline peak 74 and 264 calls.
Actual feed lateness and library conditions differ. These counts establish bounded concurrency and reduced obsolete Mermaid work in these samples.
They do not establish a controlled speedup ratio or physical mobile responsiveness.
Baseline comparison covers three scenarios per viewport. Current final-output acceptance covers all seven scenarios per viewport.


## Completion and remaining limits

Claude final conversation at 1273 × 870 passes the same five checks as Codex.
Its corrected composer records zero mutation requests.
Its theme/tool check replaces the wrapper and restores nested Markdown and wrapping after the theme change.
No console errors occur. One ResizeObserver notification remains in its fixture diagnostics.
The two earlier composer-control failures remain recorded above; the corrected final checks pass for both providers.

Owned fixture tabs close. The viewport override resets.
Guarded removal succeeds for both final-generation adapters and their ownership manifest.
Existing worktree servers remain running on frontend 5175 and backend 3502.
No install, restart, merge, main edit, or production mutation request occurs.
The branch and review workspace remain available for independent user testing.

The controller resolves the scoped final review's remaining exclusions as follows:

- Fresh full-suite and build checks pass, as recorded above.
- Corrected real-browser composer checks pass for Claude and Codex.
- Comparative work counts pass with the stated timing limits. Real document visibility remains inconclusive.
- Other production behavior retains the original whole-change review disposition.
- This execution report and the plan record final controller evidence and explicit incomplete acceptance.

Real document visibility cannot be exercised with the available browser-tab controls.
Deterministic lifecycle tests cover hide/show and ownership, but they do not replace an actual native browser transition.
Standalone share-app acceptance remains untested; actual component share hooks pass compatibility checks.
Physical-phone input latency and complete production freeze resolution require product observation.
Synchronous work already running remains non-interruptible. Different Markdown components can still render concurrently.
Unfinished-fence deferral, incremental parsing, and cross-component scheduling remain outside this implementation.
