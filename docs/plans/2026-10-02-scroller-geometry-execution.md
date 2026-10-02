# Scroller geometry execution evidence

## Scope and status

Production Tasks 1–3 initially finish at `e61c475b`.
Final review corrections finish at `55dacc1d`. Code review is **Approved** with no open Critical or Important finding.
Browser geometry and loading checks run. Native bottom-following and retirement acceptance remain **inconclusive**.
Baseline cleanup completes. Controller FULL and BUILD results are recorded below.
No backend change, server operation, package installation, public share, or share-token operation occurs.
`frontend/tests/browser/invisibleStreaming.js` remains unchanged.

## Review record

| Gate | Reviewed commits | Result |
| --- | --- | --- |
| Task 1, immutable cache | `ccac57e7..3fd3d6eb` | Spec compliant; Approved |
| Task 2, composable integration | `3fd3d6eb..384f9406` | Approved; String/Number key-contract documentation added in `afa449e3` |
| Task 3, events and loading | `384f9406..fd9821ce` | Important: successful fill followed by same-key content removal did not retry |
| Task 3, correction | `fd9821ce..e61c475b` | Addressed; Approved; regressions cover both conversation loaders |
| Task 4, validation artifacts | `e61c475b..afa449e3` | Approved; optional historical-value browser assertion remains deferred |
| Task 4, first browser correction | `afa449e3..1185eead` | Route correction addressed; timeout helper works; imported controls still bypass the page lock |
| Task 4, second browser correction | `1185eead..bbf1f7fc` | Imported controls quarantined; scoped review Approved; public content substitution and stage diagnostics added |
| Final production review | `ccac57e7..afa449e3` | Three Important loading findings; all receive correction and scoped review |
| Final correction wave | `bbf1f7fc..e2d341e9` | I1/I2 corrected in `8917b5b6`; measured readiness corrected in `e2d341e9` |
| Final scoped review | `bbf1f7fc..e2d341e9` | I2/I3 addressed; I1 remains open because the neutral callout creates layout feedback |
| Narrow feedback correction | `e2d341e9..55dacc1d` | Addressed; Approved; 20 pagination tests pass |

The final review distinguishes successful no-progress pagination from actual fetch failure.
It also detects filtered membership changes without canonical ID, cursor, query, or range changes.
Both findings require actual-SFC regression tests and scoped re-review.
Native browser diagnostics reveal a third finding: cached zero-height readiness prevents missing-content demand.
The viewport later measures 421px, but the computed readiness remains false.
Focused actual-source probes confirm this in `SessionItemsList` and `ShareItemsList`.
The delayed-measurement `SessionList` probe recovers; no pagination starvation trace is established for this finding.
The first I1 correction introduces an in-flow no-progress callout.
Its appearance changes the viewport, which supplies another range opportunity and removes the callout.
A controlled ResizeObserver trace produces three requests without user action.
The final correction uses an absolute overlay. It preserves the scroller's flex footprint.
Its regression retains one request without user action and permits explicit retry and a real external resize.
The optional browser assertion does not block production approval.
Utility and composable tests already verify unchanged historical snapshot values.

### Review boundaries accepted by the controller

| Behavior set aside by final review | Controller decision and reason |
| --- | --- |
| Vue-proxied object keys | Existing supported keys are String/Number; the documented contract matches the component. |
| Total suffix-only runtime | The approved specification permits O(N) reference copying and topology reconciliation. |
| Full height-stability scans | This priority preserves their existing contract; counts do not claim to remove their cost. |
| Physical-phone responsiveness and the global freeze | Synthetic geometry and viewport overrides do not prove either result. |
| Dedicated share-app native browser behavior | Deterministic ShareItemsList tests and its standalone build provide separate evidence. Native share-app acceptance remains absent. |
| Earlier priorities on this reused branch | Their completed reviews remain the record; this review covers their interaction with priority 7. |

## Deterministic checks

All commands run from `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`.

| Check | Exact command after `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows &&` | Result |
| --- | --- | --- |
| Preparation RED | `node --test frontend/tests/browser/prepareScrollerGeometryBaseline.test.js` | 7 failures; absent preparation module |
| Fixture RED | `node --test frontend/tests/browser/scrollerGeometryFixture.test.js` | 2 failures; absent harness and entry |
| FIXTURE final | `node --test frontend/tests/browser/scrollerGeometryFixture.test.js frontend/tests/browser/prepareScrollerGeometryBaseline.test.js` | 19 passed; exit 0; after both fixture corrections |
| GEOMETRY | `node --test frontend/src/utils/virtualScrollGeometry.test.js frontend/src/composables/useVirtualScrollGeometry.test.js` | 16 passed; exit 0 |
| SCROLL | `node --test frontend/src/composables/useVirtualScroll.test.js frontend/src/composables/useVirtualScrollSuspension.test.js` | 71 passed; exit 0 |
| EVENTS_LOADING, including compatibility | `node --test frontend/src/utils/virtualScrollRangeUpdates.test.js frontend/src/utils/scrollerLoadWindow.test.js frontend/src/components/virtual-scroller/VirtualScroller.update.test.js frontend/src/components/session/detail/SessionItemsList.loading.test.js frontend/src/components/session/list/SessionList.loading.test.js frontend/src/share-session/ShareItemsList.loading.test.js` | 39 passed; exit 0 |
| FULL, controller final | `npm --prefix frontend test` | 1,743 passed; 0 failed; exit 0 at `55dacc1d` |
| BUILD, controller final | `npm --prefix frontend run build` | All five builds pass; exit 0 at `55dacc1d` |
| Syntax | `node --check frontend/tests/browser/scrollerGeometryConversation.js` | exit 0 |
| WHITESPACE | `git diff --check` | exit 0 |

The controller independently runs FULL and BUILD after all final code corrections.
The final FULL run includes fixture, loading, pagination, and measured-readiness regressions.
Pagination corrections report 20 focused tests passing.
The measured-readiness correction reports 39 focused loading tests passing.
The final build emits mixed static/dynamic import warnings and the existing 500kB chunk-size warning.
These warnings do not change its successful exit status. This priority does not change bundle splitting.

## Controlled-container geometry evidence

These measurements use the actual current composable with real Vue effect scopes and controlled Node containers.
Each case attempts and accepts exactly **60** height changes, from 25px through 84px.
An independent full oracle verifies every final `{ index, key, top, height }` entry and the total height.
The source has 500, 2,000, or 10,000 rows. The viewport is 640×240px with initial scrollTop 0.

| Rows | Position | Index | Key reads across 60 changes | Rebuilt entries across 60 changes | Sum of measured intervals, ms |
| --- | --- | --- | --- | --- | --- |
| 500 | head | 0 | 0 | 30,000 | 12.265866 |
| 500 | middle | 250 | 0 | 15,000 | 6.173977 |
| 500 | tail | 499 | 0 | 60 | 3.911458 |
| 2,000 | head | 0 | 0 | 120,000 | 17.911548 |
| 2,000 | middle | 1,000 | 0 | 60,000 | 13.441997 |
| 2,000 | tail | 1,999 | 0 | 60 | 9.159197 |
| 10,000 | head | 0 | 0 | 600,000 | 85.454310 |
| 10,000 | middle | 5,000 | 0 | 300,000 | 62.510316 |
| 10,000 | tail | 9,999 | 0 | 60 | 37.614138 |

Per-change elapsed values, acceptance, totals, spacers, range tuples, and range identities enter the temporary review record.
The retained aggregate results appear above. Plan-owned scratch is removed after final review.
This record is controlled-container evidence. It is not desktop/mobile browser evidence.

The measured interval covers synchronous height writing and demanded geometry, total, and spacer evaluation.
Acceptance checks, reference inspections, and full oracles run outside this interval.
Post-flush height-stability watchers also run outside this interval.
Array reference copying can remain **O(N)**. Height-stability scans remain a separate cost.
Rebuilt-entry counts do not establish total runtime complexity or physical-device responsiveness.
The pinned baseline test also passes the full oracle for 500 tail rows and reports full reconstruction.

## Browser execution interface

### Completed browser geometry comparison

Chrome runs the current and pinned geometry matrix in desktop and mobile viewport overrides.
Each matrix passes all nine full-oracle cases. Each case attempts and accepts 60 changes.
The actual desktop viewport is 1164×727px. The actual mobile viewport is 355×767px.
Requested overrides are 1280×800px and 390×844px. Existing browser zoom remains unchanged.
The controlled geometry container is 640×240px in both viewport modes.
The document is visible. Modules are warm. The synchronous feed has no frame pacing.

| Rows | Position | Desktop current, ms | Desktop pinned, ms | Mobile current, ms | Mobile pinned, ms |
| --- | --- | --- | --- | --- | --- |
| 500 | head | 10.8 | 17.2 | 18.3 | 28.9 |
| 500 | middle | 4.9 | 20.1 | 5.9 | 19.4 |
| 500 | tail | 3.2 | 18.0 | 3.8 | 15.2 |
| 2,000 | head | 19.5 | 52.6 | 18.9 | 44.5 |
| 2,000 | middle | 12.4 | 45.0 | 15.9 | 49.2 |
| 2,000 | tail | 10.8 | 49.5 | 8.1 | 45.7 |
| 10,000 | head | 74.5 | 253.2 | 74.4 | 269.9 |
| 10,000 | middle | 62.5 | 280.9 | 58.4 | 229.2 |
| 10,000 | tail | 37.6 | 307.7 | 42.0 | 226.1 |

Each elapsed value sums 60 synchronous measured intervals. It is one run, not a statistical comparison.
The interval excludes post-flush height-stability work, oracles, and identity inspection.
For 10,000 tail rows, current geometry rebuilds 60 entries; pinned geometry rebuilds 600,000 entries.
Current height-only cases read zero item keys; pinned cases read every item key on every change.
Both versions retain the render-range identity on all 60 changes.
Current geometry also retains the visible-range identity on all 60 changes; pinned geometry retains it on none.
Final current totals are 12,060px, 48,060px, and 240,060px for the three source sizes.
Final render tuples are `{start:0,end:21}`. Final visible tuples are `{start:0,end:1}`.
The before spacer is 0px. The after spacer is total minus 564px for head changes.
The after spacer is total minus 504px for middle and tail changes.
These tuples describe the controlled harness; they do not establish native conversation visibility.
These measurements do not prove physical-phone responsiveness or removal of the full-application freeze.

| Page | URL | Ready state | DOM JSON report |
| --- | --- | --- | --- |
| Current geometry | `http://localhost:5175/tests/browser/scrollerGeometry.html` | `#scroller-geometry-status`: `Scroller geometry ready` | `#scroller-geometry-report` |
| Pinned geometry | `http://localhost:5175/tests/browser/scrollerGeometry.html?baseline=1` | Same state after preparation | Same ID |
| Claude conversation | `http://localhost:5175/tests/browser/scrollerGeometryConversation.html?provider=claude_code` | `#scroller-conversation-status`: `Scroller conversation ready` | `#scroller-conversation-report` |
| Codex conversation | `http://localhost:5175/tests/browser/scrollerGeometryConversation.html?provider=codex` | Same state | Same ID |

Accessible geometry action: **Run geometry matrix**.
Accessible conversation actions:

- **Initial reveal**: require successful actual `scrollToKey`, row presence, and component content separately.
- **Near-bottom growth**: run the existing bounded production streaming scenario and assert its actual bottom gap.
- **Reading-above anchor**: use the existing real anchor and scroll-position checks with 2px tolerance.
- **Group expand/collapse**: use the actual component toggle handler in simplified display mode; restore normal mode afterward.
- **Streaming retirement**: run real final reconciliation, assert synthetic retirement, and reveal final component content.
- **Missing content recovery**: remove same-index fixture content, provide one failed GET response, then trigger real KeepAlive recovery.
- **Filtered session pagination**: mount actual SessionList with the same Pinia/router; serve two filtered short pages, then one matching page.

Run filtered session pagination once per fresh conversation page. Its response bound is three requests.
Reports contain actual viewport/container dimensions, module/feed conditions, requests, and explicit failures.
The controller applies viewport overrides after navigation and reads DOM reports without mutable browser evaluation.
A fresh page loads one geometry implementation. Baseline source remains unchanged and keeps same-directory imports.
The production conversation entry rejects inherited `baseline` and `publicationRateBaseline` query flags.
All mutation fetches remain blocked.

## Baseline ownership commands

The controller prepares the owned baseline from `56a1600a`; preparation exits 0.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node frontend/tests/browser/prepareScrollerGeometryBaseline.mjs
```

The script exclusively creates only:

- `frontend/src/composables/useVirtualScrollGeometryBaseline.js`
- `frontend/tests/browser/.scroller-geometry-generation.json`

It pins `56a1600a:frontend/src/composables/useVirtualScroll.js`.
The manifest stores the UUID, source, exact created path, and SHA-256 digest.
Failed manifest creation validates the adapter before rollback.
Failed rollback attempts an exclusive recovery manifest and reports every failure.
Racing manifests and modified files remain untouched.

After closing owned fixture tabs and resetting the viewport:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node frontend/tests/browser/prepareScrollerGeometryBaseline.mjs --remove --tabs-closed
```

Cleanup validates schema, pinned source, allowed path, and actual digest, then deletes the manifest last.
Failed cleanup retains ownership and supports a guarded retry.
Never replace this cleanup command with broad deletion or `git clean`.
The controller closes only tab `487180164`, resets the viewport override, and runs the guarded cleanup command.
Cleanup exits 0: `Owned scroller baseline removed`. Existing user tabs remain untouched.

## Conversation browser results

| Action | Claude mobile, 355×767px | Codex desktop, 1164×727px |
| --- | --- | --- |
| Initial reveal | Passed | Passed |
| Missing content recovery | Passed | Passed |
| Group expand/collapse | Passed | Passed |
| Filtered session pagination | Passed | Passed |
| Reading-above anchor | Passed | Passed |
| Near-bottom growth | Inconclusive: 20-second deadline | Not run in the final pass |
| Streaming retirement | Not run in the final pass | Inconclusive: 20-second deadline |

Initial reveal verifies `scrollToKey=true`, actual row presence, and the component marker separately.
Missing-content recovery verifies one failed GET, no retry loop, and one successful GET after real session recovery.
Both recovery results verify the final component marker and actual successful scroll.
Both pagination results use three pages, canonical IDs `geometry-page-0/1/2`, cursor 70, and `hasMore=false`.
The pagination viewport is 319×260px on mobile and 320×260px on desktop.
Group expansion adds one child, renders its marker, and returns to the prior count after collapse.
Reading-above checks preserve the anchor and scroll position within the existing 2px tolerance.
Claude retains anchor `2` and scrollTop 200px. Codex retains anchor `3` and scrollTop 231.818176px.
The conversation viewport heights are 421px and 397px respectively.

Conversation checks run with warm modules, visible documents, and seeded synthetic read-only data.
The successful gap checks run after `e2d341e9`; Codex pagination also runs after `55dacc1d`.
The final feedback correction changes only pagination layout and its regression tests.
The imported controls report 20 hidden and disabled actions, with an inert control root.
Timeout results require reload, retain locked controls, and never become passes after late settlement.

The browser records `ResizeObserver loop completed with undelivered notifications.`
Claude records this warning twice; Codex records it once.
The tests do not establish its cause or prove that it causes the native timeout.
The timeout also does not establish a production bottom-following or retirement regression.

## Remaining acceptance limits

- Native bottom-following and streaming retirement remain inconclusive in the bounded scenarios above.
- The final conversation pass covers Claude/mobile and Codex/desktop, not every provider/viewport combination.
- Dedicated share-app browser evidence, separately from passing deterministic ShareItemsList tests.
- Physical-phone responsiveness and the full-application freeze require independent user testing.

Sparse RAF or unavailable native behavior remains failed/inconclusive. It never establishes a pass.

## Rulings I made

1. Include `ShareItemsList` loading compatibility. It also depends on scroller update events. If wrong, its additional edits need review or rollback.
2. Review the full priority 7 range from `ccac57e7`. Earlier priorities have completed review gates. If wrong, older interactions need broader review.
3. Reuse the completed final-review seat. A fresh reviewer cannot start because the agent thread limit is reached. If wrong, retained context reduces independence.
4. Include measured-readiness finding I3 in the active final correction wave. The browser confirms real loading starvation. If wrong, readiness edits need review or rollback.
5. Permit one narrow correction after the final wave. Scoped review confirms a new self-induced request loop. Leaving it violates the specification and requested review loop. If wrong, the exception adds review time and UI edits needing rollback.

All ledger rulings appear above in their original order.
The worktree remains available for user testing. No merge, push, or worktree removal occurs.
