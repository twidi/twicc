# Inline HTML artifacts: integrated validation

## Status

**Integrated acceptance closes with the stated tool limits. Independent final scoped review remains pending.**
The record distinguishes observed PASS from unverified subcases. Independent review owns the completion verdict.
The browser matrix contains **16 PASS rows and one PASS with limits row** after the final pending follow-up.
The limits do not assert an in-viewport pending interval, suppressed-ready browser recovery, or exact cross-call DOM identity.

Actual parent-window-focus reconciliation passes. Historical R4 A3 outage/reconnection **FAILS on `836715ea`**.
The later actual R5 A3 retest **PASSES on `e0b91199`**, including copied saved data and data-only RAM retention.
The separate old-R4 viewer stalls in Loading after serialized pending65→ready66. Its root cause remains unproved.

Normal pending copy at750bae99 and retained-viewer automatic loading when visible **PASS with the limits below**.
Final product source: `2238acd5fb06eab1c1c67547b0de774aa0e56ccd`. Backend source stays at `750bae99`.
FR-I1 affected checks, fresh full frontend, and the rebuilt public bundle pass.
Both actual mode-adoption paths pass on750bae99; ready=true mode behavior remains unchanged at2238acd5.
The first full backend run has one SQLite lock failure. Its isolated owning file and qualified broad retry pass.
No unfiltered backend pass or actual artificially suppressed-ready recovery is claimed.
Read-only browser tools cannot compare retained DOM object identity across calls.
Attached iframe observations, document nonce/counter, inputs, and scroll establish retained browsing state instead.

## Environment and authorization

| Field | Actual value |
| --- | --- |
| Date | 2026-10-08 |
| Task 12 implementation base | `e497c83aefa73f886c13538c50f46f2d00f94622` |
| Initial implementation commit | `db2324aed04a7cbb14915d5c16a2e4ef5a38dc59` |
| Browser integration fix I1 | `2e4defdb0b7416cacc06b4cde9616b228136f105` |
| Full-suite fixture compatibility fix | `8daaa2020431df4daedeae60e859128576033098` |
| Snapshot transcript boundary fix B1 | `427adc8cce9bdf59eac926f784f4e13ab6fbbe2a` |
| R3-I1 pending reactivation correction base | `4805dc0e50c03de4d35d8e9c063f7f81c1cee1d8` |
| A3-I1 reconnect correction base / actual failing browser state | `836715ea6b58ea5f4c28f658c09b52154dc38dee` |
| Actual R5 A3 PASS / final fix base | `e0b911997699ab88d46351ff1cde57c2e6cc63bb` |
| Initial final-wave source correction / actual pending test | `750bae9932bad34ab7d6ab4363335e8a9dcb5fc4` |
| FR-I1 final product source | `2238acd5fb06eab1c1c67547b0de774aa0e56ccd` |
| Host | Linux `7.0.0-31-generic`, Ubuntu SMP PREEMPT_DYNAMIC |
| Python / Django / pytest | `3.13.14` / `6.0.4` / `9.0.3` |
| Node | `v22.22.1` |
| Browser | Chrome extension browser on Linux |
| Exact browser version | NOT AVAILABLE; browser policy rejects `chrome://version`. No workaround occurs. |
| Owner app / backend | `http://localhost:5174` / `http://localhost:3501` |
| Dedicated loopback share host | `http://127.0.0.1:3501` |
| Providers | Actual regular Claude Code and Codex sessions; native Claude child |
| Browser evidence | Controller DOM reads, UI actions, document diagnostics, and browser-tool screenshots |
| Detailed evidence | Ignored `.superpowers/sdd/2026-10-08-inline-html-artifacts/` reports and logs |

The user authorizes isolated startup and the later A3 backend outage/restart.
The controller starts the instance with `devctl.py start --fresh-providers` and performs the authorized outage/restart.
Devctl owns dependency preparation and startup migrations. The implementer runs no server, installation, or manual instance migration command.
Database, artifacts, scratch, and provider homes resolve inside the authorized worktree before fixture writes.
Native provider JSONL passes through the normal watcher and canonical compute. No LLM launches to prepare fixtures.
Normal owner services create and mutate test-only loopback shares. No tunnel or public deployment occurs.

Actual session IDs, tokens, passwords, and API secrets stay outside committed documentation.
Sanitized owner route: `/project/<fixture-project>/session/<regular-session>`.
Sanitized share route: `http://127.0.0.1:3501/share/<test-token>/`.
Public checks use the built standalone share-session bundle served by the backend.

## Automated evidence by code state

| Check | Actual result |
| --- | --- |
| Addendum RED | 22 failed, 5 passed; approved inline guidance absent |
| Addendum GREEN | 27 passed |
| Focused addendum/source/Codex resume checks | 70 passed |
| Persisted screenshot-to-renderer integration | 4 passed, 26 deselected |
| Selected backend before I1, initial Task 12 implementation | 1004 passed, 1 device-node skip; 45.20s |
| Full frontend before I1 | 2947 passed, 0 failed/skipped; 26.995s |
| Corrected gap-loading harness | 18 passed |
| Backend checks affected by mechanical Ruff cleanup | 172 passed |
| I1 real serializer/context RED | 4 private cases fail; 4 public cases pass |
| I1 real serializer/context GREEN | 8 passed |
| I1 covering backend, including serializer/projection callers | 161 passed; 6 established baseline CLI cases deselected; 16.09s |
| I1 focused frontend | 250 passed, 0 failed; 7.449s |
| Selected backend after I1 | **1012 passed, 1 device-node skip; 31.37s; exit 0** |
| Full backend after I1, before fixture compatibility fix | 9810 passed, 26 skipped, 10 failed, 75 warnings; 403.93s; exit 1. Six established CLI baseline failures and four branch-owned fixture regressions. |
| Fixture compatibility RED / GREEN | Four cases fail alone at the pre-fix HEAD; unchanged original sources pass them. After correction, both covering files pass: **20 passed; 4.19s; exit 0**. |
| Full backend after fixture compatibility fix | **9814 passed, 26 skipped, 6 established CLI baseline cases deselected, 75 warnings; 378.03s; exit 0**. This is not an unfiltered full-suite pass. |
| Fresh full frontend after I1, before B1 | **2947 passed, 0 failed/skipped; 24.1046s; exit 0** |
| B1 mounted public-component RED / GREEN | Three initial cases fail before the fix. Four final cases pass after the fix; 1.017s. |
| B1 covering frontend | **269 passed, 0 failed/skipped; 5.173s; exit 0** |
| Fresh full frontend after B1, before R2-I1 | **2951 passed, 0 failed/skipped; 22.0428s; exit 0** |
| R2-I1 authoritative exclusion RED / GREEN | Mounted snapshot exclusion fails before correction: active runtime survives metadata503. After correction, all five owning component tests pass; 1.031s. |
| R2-I1 covering public component/adapter/completion/runtime | **42 passed, 0 failed/skipped; 1.184s; exit 0** |
| R3-I1 pending reactivation RED / GREEN | Mounted completion accepts publication267 while the successful store boundary stays263. The owning RED fails at that runtime assertion. After correction, all seven owning tests pass; 5.660s. |
| R3-I1 meta503 preservation RED / GREEN | An intermediate gate stops pending completion after fetchMeta503. A second owning RED fails. The final gate preserves polling at the reconciled boundary. |
| R3-I1 covering public component/adapter/completion/runtime | **44 passed, 0 failed/skipped; 5.817s; exit 0** |
| A3-I1 mounted reconnect RED | Two failures reproduce premature WS placement and an unchanged successful transcript boundary. |
| A3-I1 aborted-manifest ownership RED | A newer reconnect waits for the canceled request instead of accepting its current manifest. |
| A3-I1 affected component/adapter/completion/runtime/store/API/live-WS checks | **77 passed, 0 failed/skipped; 24.451s; exit 0**. Includes all 18 owning mounted tests. |
| Required Ruff scope, plus I1 serializer/projection files | All checks passed |
| Fixture compatibility Ruff scope | Five unchanged findings reproduce on original sources: four F811 fixture redefinitions and one PIE807 lambda. No added-line finding. |
| SPA, broker shim, artifact shell, browser companion, share-session build | All five initial bundles build before I1. All five rebuild after B1; the actual B1 browser retest uses the new production share bundle. |
| R2-I1 standalone public share-session build | Production bundle builds successfully; 17.13s; exit 0. Browser validation has not yet loaded this correction. |
| R3-I1 standalone public share-session build | Final production bundle builds successfully; 17.01s; exit 0. No browser check loads this correction. |
| A3-I1 standalone public share-session build | Production bundle builds successfully; 17.01s; exit 0. Later actual R5 A3 retest passes on this source. |
| Fullscreen runtime/geometry investigation | 3 targeted tests pass; no production change |

The post-I1 selected backend command is:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts && TWICC_DATA_DIR=$PWD uv run --no-sync pytest tests/test_inline_artifact*.py tests/test_share_*.py tests/test_artifact_data*.py tests/test_artifact_broker_html.py tests/test_compute_apply_signals.py tests/test_codex_recompute_persistence.py -q
```

Log: `task-12-backend-after-i1.log`. The six known CLI cases are outside this selected command; no cases are excluded here.
The controller separately runs the full backend and fresh full frontend suites.
Logs: `branch-full-backend.log`, `branch-full-backend-after-fixture-fix.log`, `branch-full-frontend.log`, and `task-12-round-2-tests.log`.
The four corrected fixtures use canonical provider compute readiness and isolated coordinator start/stop dependencies.
No production gate or service lifecycle changes in that correction.
The post-fixture backend run excludes only the six established baseline CLI cases.
B1 changes only frontend source and its regression. No additional backend suite is justified for that correction.
B1 logs: `task-12-b1-red.log`, `task-12-b1-green.log`, `task-12-b1-covering-frontend.log`, and `task-12-b1-build.log`.
The controller's fresh full frontend result is in `branch-full-frontend-after-b1.log`.
The later R2-I1 correction uses only affected frontend tests and a standalone production share-session rebuild.
No backend or full frontend suite repeats for that correction.
R2-I1 logs: `task-12-r3-red.log`, `task-12-r3-green.log`, `task-12-r3-covering-frontend.log`, and `task-12-r3-share-build.log`.
The later R3-I1 correction also uses only affected frontend tests and the standalone share-session build.
R3-I1 logs: `task-12-r4-red.log`, `task-12-r4-meta-red.log`, `task-12-r4-green.log`, and `task-12-r4-covering-frontend.log`.
Final standalone build log: `task-12-r4-share-build.log`. It contains no warning; the covering log also contains no warning.
No backend or full frontend suite repeats. Earlier full-suite results precede both focus-handler corrections.
The A3-I1 correction runs only affected frontend checks and the standalone share-session build.
Logs: `task-12-r5-red.log`, `task-12-r5-aborted-manifest-red.log`, and `task-12-r5-covering-frontend.log`.
Build log: `task-12-r5-share-build.log`. It contains no warning.
The covering log contains one existing Node MockTimers ExperimentalWarning. All 77 assertions pass.

The initial full frontend log contains **five Node ExperimentalWarning messages for the MockTimers API**.
Those warnings are not failed assertions. Existing SPA mixed-import and large-chunk warnings also remain recorded.
The B1 covering frontend log contains one further Node MockTimers ExperimentalWarning.
No whole-repository pytest pass is claimed.

## Source, authoring, and integration contracts

Both provider addenda contain the approved main-session authoring, correction, native exclusion, and optional data guidance.
Single-file and relative-asset examples parse through the real backend parser.
New sessions freeze the addendum. Existing stored addenda remain unchanged.
Codex receives frozen developer instructions on thread start and omits them on resume.
The bundled sharing skill already documents inline inclusion. Plugin version remains `0.109.0`.

Persisted-source tests ingest actual tool-image/finalized-assistant JSONL, save screenshot bytes, and read the persisted SessionItem.
Node consumes that record through actual canonical extraction, text contexts, trimming, Markdown splitting, and publication-key comparison.
Full recompute preserves the publication, normalized text, and frozen addendum.

The source boundary is **persisted canonical text after provider normalization**, before renderer transformations.
Claude preserves canonical block index 1. Existing Codex screenshot normalization coalesces text into canonical block index 0.
The test includes emoji, CRLF, whitespace, a saved screenshot, and sliced/batched ingestion.
No legacy screenshot normalization changes to preserve upstream pre-normalization indices.

The first actual owner browser publication fails because full `serialize_session` omits authoritative Session.type.
Valid tags and canonical catalogs still exist. Strict frontend regular-session gates reject the missing type.
I1 adds the authoritative type field to full payloads and preserves slim payload shape and strict native exclusion.
Real provider ingestion and serializers feed actual Vue context excerpts, owner adapter/runtime, and typed publication placement.
The public path already derives its session identity from authoritative inline_artifacts_supported metadata.
After I1, actual owner Claude and Codex pages render successfully.

Actual browser finding B1 exposes another integration boundary after snapshot Push.
Public metadata and inline placement advance to a new line, but the mounted transcript initially keeps only its old rows.
B1 refreshes authorized root item metadata before reconciling changed-boundary inline placement.
It retains the mounted transcript, loaded rows, scroll position, and unrelated frame.
Generation/disposal checks reject stale async responses; failed metadata can retry on the next focus.
Mounted SFC/store regressions and an actual production-browser retest close B1.

Independent round2 review finds R2-I1 in the new focus handler.
Fresh exclusion previously waits behind a changed-boundary item-metadata request.
When that request fails, retained inline frames remain active despite the successful exclusion response.
Backend routes still deny excluded artifacts. This finding concerns stale visible runtime state.
The correction applies fresh share state immediately while retaining the last successful transcript boundary.
Only successful root metadata loading advances that boundary and permits the explicit inline refresh.
Mounted regression evidence covers pending metadata, repeated HTTP503 failures, hidden retained frames, and recovery with exclusion preserved.
Existing retry, disposal, newer-focus, cache, and latest typed-placement tests also pass.
No actual-browser metadata503 test occurs.

Independent round3 review finds R3-I1 in the early inclusion assignment.
Retained pending completion resumes before changed-boundary transcript metadata arrives.
The final correction pauses completion when fresh metadata reveals a boundary different from the successful store boundary.
It sets that pause before applying fresh inclusion. Authoritative exclusion still suppresses the runtime immediately.
Failed transcript metadata preserves the pause and cached boundary. A successful later focus retries and resumes completion.
Failed fetchMeta does not stop completion at a still-reconciled boundary.
Generation and disposal guards preserve ownership before pause changes and row mutations.
Mounted evidence covers disable/manifest503, slow and failed reactivation metadata, recovery, automatic pending completion, and latest typed placement.
Existing B1, immediate exclusion, failed-boundary retry, disposal, and newer-focus cases remain passing.
No actual-browser R3-I1 check occurs. The round4 scoped review passes.
Actual A3 now reveals a separate reconnect defect. Task12 acceptance stays incomplete.

## Historical R4 A3 outage and reconnect correction

The controller stops and starts only the isolated backend through `devctl.py`.
While stopped, the guarded fixture updates Calculator code, optional saved data, and finalized Codex publication268→269.
The browser keeps Calculator version1, nonce `10540cba`, counter3, and unsaved text `a3-unsaved-before-outage` during the outage.

No page refresh or widget Reload occurs.

Normal startup compute reaches source269 before the WebSocket reconnects.
The socket initially receives403 while compute remains unready. It later opens without browser intervention.
The automatic inline manifest GET returns200, revision35, Calculator publication269/code revision2.

Public meta and filtered item metadata also contain269.

The retained transcript still ends at268. GoLast cannot reach269.
The old Calculator block disappears, and the retained frame hides. The replacement remains inaccessible.

This historical R4 result is **FAIL**, captured on `836715ea`. That checkpoint contains no saved-data read after reconnect.

The root callback previously fetches only the manifest. The consumer does not replay offline-computed transcript rows.
The correction fetches current root metadata and advances its successful boundary before releasing reconnect placement.

The adapter buffers HTTP and WS manifests during that work. It releases only the newest revision after source rows exist.
Concurrent publications beyond the HTTP snapshot trigger another reconciliation attempt.

Transient metadata/manifest failures retry automatically. New connections, disposal, and access loss invalidate older work.
Delayed HTTP metadata cannot undo a newer channel exclusion. Disabled manifests still close access immediately.

An aborted manifest request cannot block the next connection's fresh request.

Mounted checks preserve cached transcript rows, scroll position, unchanged frame memory, and all earlier snapshot regressions.
Changed publication/code navigates the same retained frame once. Duplicate and saved-data-only revisions do not reload it.

These checks use the real app, list, Pinia store, API, live transport, adapter, completion, and runtime.
HTTP responses, WebSocket endpoints, row DOM, and scroller DOM remain controlled test boundaries.
These automated tests alone do not establish actual iframe retention or corrected production-browser A3 acceptance.
The later actual R5 observations below establish that separate A3 acceptance.
The later current-build pending observation below establishes normal copy and visible automatic loading.

### Later actual R5 A3 retest

The controller intentionally reloads the page before the second outage to load the R5 production bundle.
No page or widget Reload occurs during or after that outage.
Calculator HTML2/publication269 retains its nonce, counter4, unsaved input, range25, and tabOne while the backend stops.
A normal native publication advances source271→272 and Calculator HTML2→3 while stopped.
After startup, the actual socket reconnects and requests meta, root metadata, inline manifest, and row272 content.
The new row becomes reachable through GoLast. Exactly one Calculator placement shows HTML3 and counter5.
The correction loads its document once. Saved-data Load reads `a3-retest-offline-choice`/81/Two.
A later normal owner data PUT and explicit Load retain the nonce, counter5, HTML3, and unsaved input/range/tab.

**Actual A3 PASS on `e0b911997699ab88d46351ff1cde57c2e6cc63bb`.**
Evidence: `task-12-a3-retest-observations.md` and `task-12-acceptance-final-checkpoint.md`.
The earlier R4 FAIL remains historical evidence. This A3 PASS does not close live pending completion.

### Separate old-R4 pending observation

A normal pending-copy HTML5→6 publication270→271 reaches serialized pending65 then ready66.
The retained old-R4 viewer shows Loading after durable ready and remains stalled without new HEAD/GET.
No delivered or accepted ready66 revision is recorded. Loading alone cannot identify the failing boundary.
The read-only serializer proves public descriptor state, rather than actual socket delivery.
The actual-block control passes pending→ready under its controlled observer/renderer boundary.
The final review proves a separate skipped-terminal-delivery defect; it does not prove this stall uses that path.
The final-fix worker executes no browser test. The controller later records current-build automatic loading below.
Evidence: `task-12-pending-browser-observations.md` and `task-12-pending-diagnosis.md`.

### Actual current-build normal pending copy and visible automatic completion

Tested product source: `750bae9932bad34ab7d6ab4363335e8a9dcb5fc4`.
The controller restarts only the isolated backend and intentionally reloads the public page before the test.
The existing regular Codex live viewer stays mounted. No page or widget Reload occurs after the test starts.

Initial pending-copy HTML6 has load counter 8 and nonce `0127a04d`.
The observer starts first and the producer starts second in one tool setup. Both exit0.

The producer's ten-second driver delay precedes publication. The exporter has no added delay.
Only pending HTML6→7 and finalized native source272→273 change; publication273 uses block0/offset38.
Existing 10000×8192-byte padding remains unchanged. No new fixture or share is created.

The observer uses fresh ORM reads and pure public serialization under its own SQLite `PRAGMA query_only = ON`.
It performs no HTTP polling, reconciliation scheduling, or DB/catalog/export write.
Actual public descriptors show publication271/ready/revision99, then273/pending/revision100, then273/ready/revision103.
Pending appears at21:00:16.062046 UTC; ready appears at21:00:17.321916 UTC.

The actual Loading witness appears later, at21:00:26.620 UTC. The replacement block is outside the viewport at top928.9375.
This is ready+idle lazy loading after durable readiness; it does not prove an in-viewport pending interval.
Ordinary GoLast makes publication273 visible. Automatic HEAD/document loading produces HTML7 and loads its assets.
Nonce `ab377a07` and counter 9 establish one correction load, from counter 8→9. Exactly one pending-copy block remains.
No manual retry, widget Reload, or page Reload supplies that completion.

**PASS: normal actual pending export and retained-viewer automatic loading when eligible and visible.**
No in-viewport pending interval or browser recovery from an artificially suppressed ready event is claimed.
The owning consumer regression covers the exact skipped-terminal-delivery condition separately.
The earlier R4 Loading stall keeps its unproved root cause; this result does not retroactively explain it.
These conclusions remain in this committed record even if the ignored workspace evidence is removed.
Detailed controller evidence: `final-pending-browser-observations.md`.

## Fixture diagnostics and browser limits

Fixtures display document nonce, load counter, input/range/tab state, local/page scroll, and operation results in the DOM.
The load counter uses sessionStorage with a caught fallback when storage is unavailable.
It measures document loads; it does not persist interface inputs.
Pages provide explicit optional Save/Load controls and ordinary fetch network diagnostics. They do not autosave.
Both providers have a single HTML page and a folder page with relative JavaScript, CSS, SVG, and JSON data.
History filler permits actual virtualization. A separate cached session and native child exercise view boundaries.

A separate legacy fixture deliberately has one available entry and one missing entry.
Normal service creates its snapshot with inclusion false.
The controller explicitly authorizes one test-only database simulation: remove that inclusion option and reset export metadata to `{}`.
The frozen line and other service-created options remain unchanged.
Normal first-view initialization creates successful/error copies. No publication, descriptor, capture, or error metadata is handwritten.

Two browser-tool limitations require careful interpretation:

- Some locator clicks target controls outside the visible conversation overlay clip. Fullscreen exposes those controls for actual interaction.
- Snapshots/role locators omit some overlay controls. Actual DOM CSS locators reach them; ancestors have no hidden/inert/aria-hidden exclusion.

Fullscreen Reload retains toolbar top 0, height 43.99, zIndex 1002, and both actual buttons.
CSS Exit full screen works. The legacy error Reload also works through its observed CSS locator.
These observations establish a tool scoping limitation, not a user-visible accessibility defect.
Temporary viewport overrides are reset. The original cache limit 20 is restored through the UI.
All agent-created browser tabs close after evidence capture. The authorized server remains running.
Additional focus and anchor observations use native accessibility focus state and actual keyboard/scroll actions.
The tools do not provide network-offline or OS window-focus controls.
An actual Ctrl+N action does not create a separate browser window through the available tool.
Actual iframe input focus followed by bound Escape returns parent-window focus and provides the successful A4 observation.
No OS window-switch claim follows from this supported focus path.

## Browser acceptance record

PASS means the listed actual observations succeed. PARTIAL identifies a required subcase without actual browser evidence.
Nonce prefixes below identify diagnostic document instances, not session IDs or share capabilities.

| Scenario | Status | Actual observation and limit |
| --- | --- | --- |
| Both providers: single HTML and relative-asset folder | **PASS** | Owner and production public pages render. JavaScript/CSS/SVG load. Explicit data reads work. |
| Input/range/tab/local scroll through virtualization | **PASS** | Public calculator nonce 87c6b80f/counter 1 retains text/range 100/tab Two through GoLast/GoFirst. Private preferences nonce 92aa4af9/counter 1 retains local scroll 643.636 and inputs. No Save occurs. |
| Cached session switch, hidden chat, dock moves/resizes | **PASS** | Private nonce 92aa4af9/counter 1 survives cache switch, Files Right top/Bottom right, dock/sidebar resizing, and viewport changes. Other frames stay hidden. DOM object identity remains unobservable. |
| Fullscreen with source row absent; iframe-focused Escape | **PASS** | Public preferences nonce 87d49092/counter 3 retains text while keyboard Enter activates parent GoFirst to marker 055. Frame stays fixed top 44/width 769/height 735. Escape hides it with source block count 0. GoLast restores the same document/state. append-history remains unexecuted. |
| Focused widget scroll-out; composer/header/overlay interaction | **PASS** | Native accessibility identifies the focused iframe input with text focus-retained-focused. Actual parent gutter scroll moves scrollTop 30866.363→27556.363 and transfers focus to the conversation at marker 220. Space moves parent scrollTop to 27920.908. Composer fill/clear works while the retained frame stays hidden. GoLast returns the same nonce d5baaba1/counter 1/text. Native accessibility supplies focus evidence; shadow-DOM activeElement inspection does not. Header and child/dock overlays remain interactive. |
| Network consent while chat hides | **PASS** | Delayed network shows no prompt under Files. Returning Chat shows consent; deny resolves on the same document without reload. |
| Same-ID correction while old placement is absent | **PASS** | With Preferences source absent, parent scrollTop stays 3310 across finalized version 5 publication. Marker 010 stays top 217.1164703369141/bottom 255.2130584716797; marker 011 stays top 447.1448669433594/bottom 485.241455078125. Both retained frames stay hidden. GoLast renders exactly one latest Preferences block and changes nonce d5baaba1/counter 1 to c3d7571b/counter 2 once, with defaults. Old correction prose has no widget. Earlier live correction reloads once; snapshot retains its captured version. |
| Unpublished code, saved data, independent Artifacts viewer | **PASS** | Unpublished version 2 leaves private/live version 1 and counters unchanged. Artifacts preview alone reloads version 2/counter 2. Its memory is independent; Explicit Load reads shared saved choices. Live data PUT refreshes copied data without reloading version 1/counter 1. |
| Reload, refresh, eviction, and conditional archive teardown | **PASS** | Codex Reload resets RAM at counter 2; Explicit Load restores saved codex-before-reload. Browser refresh resets RAM. Cache limit 1 removes Codex frames; return recreates nonce 4e132960/counter 4/defaults. Archive/unarchive succeeds; active/cached views may survive archive. Older cached frames can linger during limit changes; no exact global cache count is claimed. |
| Height, viewport-dependent content, narrow/mobile | **PASS** | Narrow 271.8px and mobile 390×844 viewport clamp height 900. Tall content grows 170→1100; exit fullscreen clamps 900. Header/composer clipping remains correct. No ResizeObserver warning/error is observed. |
| Live tag/data changes, no-viewer interval, reconnect | **PASS with limits; R5 A3 and normal pending completion pass** | Real-time correction reloads once. Data changes preserve nonce/counter/RAM. Fresh viewer after no-viewer changes loads published version 3 rather than unpublished version 4 and reads after-no-viewer data. A real 78.125 MiB fixture reaches ready versions 1→4, once per finalized publication. Earlier browser sampling misses the transient pending interval because copy completion beats its tool observation window. The test driver delays only publication; the exporter has no artificial delay. Actual authorized backend outage preserves nonce10540cba/counter3/unsaved input while stopped. After accepted WS reconnect, ready Calculator269 replaces its manifest binding while retained rows still end268. The old block disappears; the new block remains inaccessible. This historical failure occurs on836715ea. Actual R5 retest on e0b91199 restores row272 before placement, loads Calculator HTML3 once with counter4→5, reads copied saved data, and preserves RAM/counter during a later data-only update. The separate old-R4 pending-copy viewer stalls after serialized ready66. Current product750bae99 observes public pending100→ready103; ordinary GoLast makes the offscreen replacement visible and automatic HEAD/document loading produces HTML7/counter 8→9. No Reload occurs during the test. A visible in-viewport pending interval remains unobserved; no suppressed-ready browser recovery is claimed. |
| Snapshot Push without new tag | **PASS** | Actual iframe input focus retains version 5/counter 2 while normal same-publication Push copies version 6. Bound Escape returns parent-window focus and automatically loads version 6/counter 3 without Reload. The counter stays 3 after several minutes. B1 retest also advances frozen boundary 267→268: parent focus adds Correction version 7 and exactly one latest widget, nonce 26869aeb/counter 4→7b5036af/counter 5. No page refresh, Reload, or GoLast occurs during that retest. |
| Native tag and Include subagents changes | **PASS** | Child literal tag remains ordinary text. Actual related-child HTTP checks keep root publication/code/manifest revisions unchanged for inclusion false/true. Child transcript access follows the option. |
| Legacy failed initial export; repair and error Reload | **PASS** | Initial calculator ready/preferences error at revision 3/frozenline 3. Creating only missing entry leaves manifest unchanged. Error Reload recovers preferences/counter 1 at revision 4. Calculator copy ID/code revision/captured/selected placement remain unchanged. |
| Disable inclusion, password, revoke, expiry | **PASS** | Live disable hides frames and shows not-included state. Re-enable explicitly recaptures version 4/defaults. Expiry/revoke show unavailable and hide both frames. Password gate has no iframe. Actual HTTP confirms snapshot route denial and password grant rotation. |
| Private/public native child opens and closes | **PASS** | Main frames hide during child views. Child tag renders ordinary text; no child runtime appears. Return restores public nonce 75d1ed06/counter 1/text child-return-memory. Direct child artifact/retry/proxy access returns 404. |
| Production share HTML/assets/data/proxy gates | **PASS** | Actual HTTP **88/88 PASS** covers host/password/source/traversal gates, HTML shim/CSP, relative assets, provenance listing, readonly PUT/DELETE, and denied ungranted proxy. Fullscreen Save reports read-only refusal with unchanged nonce/counter. Successful granted upstream proxy fetch is outside these observed checks and is not attempted. |

## Independent actual HTTP evidence

An independent probe uses real urllib requests, owner services, a no-redirect handler, and returned password cookies.
The initial matrix passes 77 checks; related native-child follow-up passes 11. Total: **88 PASS, 0 FAIL**.
All three probe-created shares finish revoked. Controller fixture files and shares stay unchanged by this probe.

| Boundary | Actual HTTP evidence |
| --- | --- |
| Serving | Manifest/HTML/HEAD/CSS/JavaScript/SVG/data return 200; HEAD bodies are empty. HTML includes shim and connect-src none/base-uri none. |
| Saved data | Bound data listing returns path/size/mtime. Missing document provenance returns 404. PUT/DELETE return 405; bytes remain unchanged. |
| Source/host confinement | Wrong owner host, foreign/unknown root, native source, and encoded traversal return 404. |
| Retry | Ready snapshot retry preserves manifest, publication/code revisions, and served bytes. |
| Inclusion | Disabled manifest reports not_included; asset/listing/retry/proxy return 404. Include subagents changes preserve root revisions. |
| Password | Missing grant returns 401 or HTML redirect. Login grants access. Rotation invalidates the prior grant. Returned cookie is Secure. |
| Revoke/expiry | Manifest/asset/listing/retry/proxy return 404. |
| Native identity | Public support false/empty manifest; literal tag remains in transcript; native owner and public artifact routes deny execution. |
| Proxy | Missing persistent grant reports blocked/not_allowed. Preflight returns 400; GET returns 405. No successful upstream request is claimed. |

The corrected HTTP probe explicitly sends the returned Secure cookie over local HTTP for grant verification.
Python's default cookie jar does not send that cookie over HTTP loopback.
No filesystem symlink creation or deliberate pending-copy delay occurs in this probe.
Detailed reports: `browser-observations.md`, `task-12-browser-followup-observations.md`, `task-12-http-observations.md`, and the closed fullscreen Reload investigation.

## Remaining review risks and user prerequisites

- Final source fixes I1/I2/I3/M1 have affected automated coverage at750bae9932bad34ab7d6ab4363335e8a9dcb5fc4.
- Integrated acceptance closes with the stated visibility, delivery, and browser-tool limits.
- Independent final scoped review remains pending.
- Historical R4 A3 FAIL and later actual R5 A3 PASS remain separate evidence.
- Actual R5 copied saved-data reads and data-only RAM retention pass.
- The old-R4 pending viewer stalls after serialized ready66. Its observed root cause remains unproved.
- Normal pending copy at750bae99 and visible automatic completion pass on750bae99 without Reload during the test.
- The actual Loading witness occurs after serialized ready while the replacement is outside the viewport.
- No visible in-viewport pending interval or artificially suppressed-ready browser recovery is claimed.
- Final full frontend at2238acd5 passes2970 cases, with five existing MockTimers warnings.
- The first full backend run has one MCP SQLite lock failure; its isolated owning file passes19 cases.
- Backend source stays at `750bae99`. Broad retry passes9817 cases,26 skips,6 established CLI exclusions,75 warnings.
- Both actual mode-adoption paths pass at750bae99, with unchanged ready=true behavior at2238acd5.
- FR-I1 retains the authorized live channel through temporary compute readiness while execution stays suspended.
- Exact cross-call retained DOM object identity is unobservable. Nonce/counter/state and attached-frame observations prove browsing-context retention.
- Five Node MockTimers ExperimentalWarning messages and existing SPA build warnings remain recorded.
- Six CLI slim/full projection failures reproduce against unchanged baseline. They are not labeled passing.
- Four additional full-backend fixture regressions pass in both covering files and the subsequent broad backend run.
- That backend run deselects six established baseline cases. No unfiltered full-suite pass is claimed.
- Five Ruff findings in the affected title-runner test reproduce unchanged against the original branch base.
- One confinement test skips because the host cannot create a device node.
- The inherited main-checkout VIRTUAL_ENV warning confirms uv selects the worktree environment.

The target checkout requires the declared Python dependency and migration `0154_inline_artifact_catalogs`.
The user runs `uv sync` as needed and restarts their target instance through `devctl.py`.
Devctl applies pending migrations at startup. The authorized isolated validation instance already completes that startup.
Validation does not authorize deployment, merge, remote push, or CHANGELOG edits.
Integrated acceptance closes with the recorded visibility, delivery, and browser-tool limits. Independent final scoped review remains pending.
The controller restores the browser menu, cache, and viewport settings and leaves the isolated backend/frontend running.
No merge, push, deployment, or worktree cleanup follows from this acceptance record.


## Final review correction: exact source and affected verification

Final tested product source: **`2238acd5fb06eab1c1c67547b0de774aa0e56ccd`**.
Initial final-wave source and unchanged backend: `750bae9932bad34ab7d6ab4363335e8a9dcb5fc4`.
Base: `e0b911997699ab88d46351ff1cde57c2e6cc63bb`.
The table labels initial-wave evidence separately from final FR-I1 evidence. No product/test edit follows the final FR-I1 checks.
This validation update changes documentation only.
Initial review package: `e0b911997699ab88d46351ff1cde57c2e6cc63bb..7735b74c64e54896b09bfc9d64b5071d7399db49`.
FR-I1 extends the same review through `2238acd5fb06eab1c1c67547b0de774aa0e56ccd`.
The final documentation-only commit extends those bounds without changing tested product source.

| Finding / check | Result and qualification |
| --- | --- |
| I1 terminal delivery | Producer preserves an obligation when serialization is unready. Consumer reloads fresh authorization and a sanitized manifest on root readiness. One boolean coalesces delivery; success clears it. No live completion polling is added. |
| I2 completion / retry | Focus, pending completion, mode adoption, and missing-row retry responses use owned transcript reconciliation. Source rows and the successful boundary precede publication acceptance. |
| I3 both mode transitions | Reactive transport starts one live connection or stops it for snapshot adoption. Active snapshot metadata remains readable; revoke/expire still close access immediately. |
| M1 failed startup | Outer ownership includes partial coordinator startup. Stop-and-drain executes on provider failure, adoption cancellation, and coordinator failure. Normal order stays coordinator-before-DB-writer. |
| Final owning backend RED | 5 failed, 1 passed, 66 deselected; 5.16s; exit1. Missing delivery times out; mode and lifecycle assertions fail. No compile/import error occurs. |
| Final owning backend GREEN | 6 passed, 66 deselected; 3.86s; exit0. |
| Initial final-wave affected frontend at750bae99 | **81 passed, 0 failed/skipped; 29.053s; exit0**. Includes22 mounted cases. One existing MockTimers ExperimentalWarning. |
| Final affected backend / coordinator | **109 passed; 12.50s; exit0**. |
| Additional mutation / public-route checks | **106 passed; 9.90s; exit0**. |
| Reviewer reproductions / actual-block control | **3 passed; 2.437s; exit0**. Controlled HTTP, observer, and renderer. No actual iframe-browser claim. |
| Reviewer relay reproduction | Exit0; skipped terminal inline delivery recovers after root readiness. Controlled reads; no ORM/fixture/socket claim for this script. The shipping regression uses actual Django/Channels test boundaries. |
| Initial final-wave standalone public build at750bae99 | Vite7.3.1;4662 modules;18.24s;exit0. CSS294.02kB/gzip45.06kB;JS16190.29kB/gzip3334.09kB. No build warning. |
| FR-I1 shipping readiness RED / GREEN | RED fails the explicit retained-channel assertion:0pass/1fail;853.337ms;exit1. GREEN delivers terminal readiness through the same socket and starts the probe:1pass;2015.006ms;exit0. |
| Final FR-I1 affected frontend at2238acd5 | **82 passed,0 failed/skipped;30.130915007s;exit0**. Includes23 mounted cases and every earlier owning case. One existing MockTimers warning. |
| Final FR-I1 reviewer control | Original readiness reproduction:1pass;2186.814ms;exit0. Controlled socket/HTTP/renderer; no actual suppressed-ready browser recovery claim. |
| Final rebuilt public bundle at2238acd5 | Vite7.3.1;4662 modules;17.57s;exit0;no warning. CSS294.02kB/gzip45.06kB;JS16190.27kB/gzip3334.09kB. |
| Final affected Python Ruff | Five files pass:`src/twicc/cli/run.py`, `src/twicc/core/services/share_mutation.py`, `src/twicc/share/consumer.py`, `tests/test_inline_artifact_share_selection.py`, `tests/test_share_consumer.py`. Including `tests/test_title_auto_task.py` reports five established findings; original `7b51b01e252f505018e4dda7ed711d82f7b6324c` sources reproduce the same four F811 fixture redefinitions and one PIE807 lambda. No entire six-file clean-lint claim. |
| Final whitespace check | `git diff --check` exits0. |
| Final full frontend at2238acd5 | **2970 passed,0 failed/skipped;39.630508975s;exit0**. Controller log: `branch-final-frontend-readiness.log`. Five existing MockTimers warnings. |
| Full frontend at750bae99 before FR-I1 | **2969 passed,0 failed/skipped;39.868020303s;exit0**. Controller log: `branch-final-frontend.log`. |
| Earlier full frontend at e0b91199 | 2965 passed,0 failed/skipped;33.832s;exit0. This predates final source changes. |
| First current broad backend at750bae99 | **1 failed,9816 passed,26 skipped,6 established CLI cases deselected,75 warnings;393.99s;exit1**. `test_mcp_events_external.py::test_registered_handlers_follow_fresh_runtime_across_lifespans` fails during OAuth INSERT with SQLite `core_mcpconnection` table locked. The owning file and unchanged broad retry later pass; this first failure remains recorded. |
| MCP owning file after first lock failure | **19 passed;4.31s;exit0**. No source/test change or added exclusion precedes this check. The unchanged broad retry later passes. |
| Final qualified broad backend retry | **9817 passed,26 skipped,6 established CLI cases deselected,75 warnings;373.69s;exit0**. Log:`branch-final-backend-recheck.log`. Backend source750bae99 remains unchanged at final product2238acd5. No added exclusion or source change follows the first lock failure. |
| Earlier qualified broad backend | 9814 passed,26 skipped,6 established baseline CLI cases deselected,75 warnings;378.03s;exit0. This predates final backend changes. |

The six excluded baseline cases remain `tests/test_session_projection.py::test_slim_and_full_are_mutually_exclusive[argv0]` through `[argv5]`.
No unfiltered backend pass is claimed. The750bae99 full frontend result predates FR-I1.
The fresh full frontend at2238acd5 passes2970 cases. The qualified broad backend retry passes9817 cases.
The first lock failure,19-pass isolated file, and373.69-second unchanged broad retry remain separate results.
No backend change follows750bae99, so that qualified backend result remains applicable to the final product.
Existing device-node, Ruff, SPA-build, MockTimers, and inherited VIRTUAL_ENV qualifications remain recorded above.

Commands, from the explicit worktree:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts && node --test frontend/src/share-session/ShareSessionApp.snapshot.test.js frontend/src/share-session/inlineAdapter.test.js frontend/src/share-session/inlineCompletion.test.js frontend/src/inline-artifacts/runtime.test.js frontend/src/share-session/ShareItemsList.loading.test.js frontend/src/share-session/shims/shareApi.test.js frontend/src/share-session/shims/shareLive.test.js frontend/src/share-session/shims/shareLiveNested.test.js frontend/src/share-session/shims/noPrivateApi.test.js
```

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts && TWICC_DATA_DIR=$PWD uv run --no-sync pytest tests/test_inline_artifact_share_selection.py tests/test_share_consumer.py tests/test_title_auto_task.py tests/test_inline_artifact_share_coordinator.py -q
```

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts && TWICC_DATA_DIR=$PWD uv run --no-sync pytest tests/test_share_mutation.py tests/test_inline_artifact_public_routes.py -q
```

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts/frontend && ./node_modules/.bin/vite build --config vite.config.share.js
```

The final-fix worker runs no server, browser, fixture action, package installation, or manual instance migration.
Bounded ignored tooling prepares only pending-copy6→7 after source272 with exact source/page/data/share guards.
The producer uses a ten-second driver delay. The query-only ORM observer stops within25seconds.
The worker executes neither action. The controller later executes both successfully; existing padding remains unchanged.
The controller loads the new bundle before this normal retained-viewer pending test, without an outage.
Actual normal copy and visible automatic completion pass with the recorded limits. No fresh page after copying supplies that evidence.


### Current full-suite controller commands

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts/frontend && npm test
```

Final controller result at2238acd5:2970passed/0failed/0skipped;39.630508975s;exit0.
Log: `branch-final-frontend-readiness.log`. It records five existing Node MockTimers ExperimentalWarning messages.
The earlier750bae99 result passes2969 cases in39.868020303s before FR-I1; it does not establish the final source alone.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts && TWICC_DATA_DIR=$PWD uv run --no-sync pytest -q -k 'not slim_and_full_are_mutually_exclusive'
```

First backend run at750bae99:1failed/9816passed/26skipped/6knownCLIexcluded/75warnings;393.99s;exit1.
The failure is `tests/test_mcp_events_external.py::test_registered_handlers_follow_fresh_runtime_across_lifespans`.
Its OAuth INSERT raises SQLite `database table is locked: core_mcpconnection` within direct MCP lifespan ownership.
M1 run_server cleanup is absent from that path; changed title lifecycle tests execute later in this run.
The MCP reader thread's joined connection query can contend with authorization, but the actual lock holder is unproved.
Fresh controller owning-file verification passes19 cases in4.31s, exit0, without a source/test change.
The controller runs the same broad backend command again, with the same six established CLI exclusions.
The unchanged broad retry passes9817 cases,26 skips,6 established CLI exclusions,75 warnings in373.69s, exit0.
Log:`branch-final-backend-recheck.log`. No additional exclusion or product/test change follows the first failure.
No actual lock holder is confirmed. The first failure is not reclassified as a passing test or a proved baseline defect.
The worker repeats neither broad suite. Final controller results enter this documentation-only commit.
Controller final affected Ruff covers five source/test files successfully.
Including `tests/test_title_auto_task.py` reports four F811 fixture import redefinitions and one PIE807 lambda.
The controller's direct `git show` of original7b51 piped into `uvx ruff` reproduces exactly those five findings.
No source change follows this lint control. No entire six-file lint-clean claim is made.


### FR-I1: explicit correction of temporary readiness transport ownership

The final scoped reviewer proves an Important regression introduced by reactive transport ownership at750bae99.
A live share_meta ready=false closes the only socket. Same-mode metadata has no owned recovery after that closure.
The consumer's terminal-delivery obligation therefore cannot reach the viewer when root compute returns ready.

The controller explicitly extends the still-open final correction before delivery, despite its round cap.
This is a recorded ledger deviation. The same reviewer remains responsible; no unrelated fix wave or review seat is added.

FR-I1 source `2238acd5` removes compute readiness from the transport watch and stop condition.
The existing runtime gate still suspends execution while unready. The authorized live channel retains readiness recovery.
Actual revoke/expire or snapshot adoption stops transport. Inline exclusion still suppresses execution immediately.

The shipping mounted test delivers ready metadata and the terminal manifest through that same retained socket.
It asserts automatic probe/frame registration, no live polling, immediate exclusion, and actual access closure.
Controlled endpoints and renderer prove the integration boundary rather than actual browser suppressed-event recovery.
No backend, runtime, exporter, helper, fixture, or source-data change occurs.
The earlier normal pending browser test at750bae99 remains distinct from this exact controlled-readiness test.

Final evidence files: `final-fix-readiness-red.log`, `final-fix-readiness-green.log`, `final-fix-readiness-affected.log`,
`final-fix-readiness-review-control.log`, and `final-fix-readiness-share-build.log`.
Their essential results and limits remain in this committed record after ignored-workspace cleanup.


### Actual public mode transitions and source applicability

The actual viewer loads public bundle750bae99 and uses backend750bae99 on the isolated instance.
The same included regular Codex share uses source273 and pending-copy HTML7.
Normal owner GET/PATCH changes only the existing share's mode; no new share, token, password, or fixture write occurs.

Live→snapshot returns an active snapshot. The open viewer stays usable and does not display the unavailable state.
The replacement export loads once:HTML7, nonce `5ed4b310`, counter 9→10, with assets ready.
No page or widget Reload occurs.

The owner returns mode to live. The initial locator Escape does not establish parent focus or mode adoption.
An unchanged counter 10 after that action supplies no defect evidence.
Actual View options click moves focus to the parent, and normal focus metadata reconciliation adopts live mode.

Backend local timestamps:23:06:03.800 public meta200;23:06:03.981 new WebSocket accepted;
23:06:03.998 on-open meta200;23:06:04.118 inline manifest200.
The actual iframe shows HTML7, nonce `9d44ca13`, and counter 10→11, without page or widget Reload.
The controller closes the menu without changing settings. Final share options remain active, included, and live.

**PASS: both actual mode-adoption paths.**
No separate new publication is generated after this mode flip to test future streaming in the actual browser.
The shipping mounted test separately proves later live-channel publications and reverse frozen transport ownership.
The later2238acd5 change removes only temporary ready=false transport ownership.
It preserves the actual ready=true mode paths tested at750bae99; source review establishes that applicability.
No browser readiness suppression or current-bundle mode rerun is implied by this source applicability statement.
Essential results and limits remain here after ignored-workspace cleanup; detailed evidence is `final-mode-browser-observations.md`.


### Final acceptance disposition

Integrated acceptance closes for the approved scope, with the recorded tool limits.
Normal actual pending-copy status transitions and retained-viewer visible automatic loading pass at750bae99.
The actual Loading witness occurs after durable readiness while the replacement is outside the viewport.
Ordinary GoLast makes it eligible; it loads HTML7 once without Reload during the test.
No in-viewport pending interval or actual deliberately suppressed-ready recovery is claimed.
Real skipped-ready delivery is covered by the owning consumer and mounted transport regressions.

Both actual mode transitions pass at750bae99. Final source2238acd5 preserves those ready=true paths.
Final full frontend passes2970 cases. Unchanged final backend retry passes9817 with26 skips and6 established CLI exclusions.
The initial SQLite MCP lock failure remains recorded alongside isolated and broad recovery, without a confirmed lock holder.
Five changed Python source/test files pass Ruff; five existing title-test findings reproduce at the original branch base.

Browser cross-call DOM object equality and successful real external upstream forwarding remain outside actual tool evidence.
Nonce/counter/RAM/attachment observations and owning proxy/header tests provide their separately stated evidence.
Essential acceptance and verification conclusions remain interpretable here after ignored-workspace cleanup.
Independent final scoped review still owns its verdict over the complete final commit range.


## UI follow-up recovery after the OOM — 2026-10-09

This follow-up replaces the separate inline fullscreen toolbar with the shared Files preview floating menu.
FilePane and inline artifacts use `FloatingPreviewTools.vue` for buttons, drag placement, and resize clamping.
The same fullscreen action changes between Full screen and Exit full screen.
Inline artifacts offer Reload and fullscreen only, including Reload on failed loads.
The existing runtime and FrameHost retain iframe ownership; fullscreen now uses the whole viewport.

Superseded finalized publications show the requested replacement sentence in `<blockquote><p><em>`.
The notice uses existing Markdown quote styles and normal inherited assistant text size.
No notice appears for missing catalog/runtime entries, native subagents, nonfinalized messages, or snapshots without inline context.
Current ready, pending, unavailable, invalid, and excluded public placements retain their existing rendering.

### OOM cause and recovery evidence

The kernel journal records Node PID1062312 at 2026-10-09 06:59:26 with12593908kB anonymous RSS.
The original unbounded multi-file run produces only `TAP version 13` before the incident.
The controller isolates the new floating-menu test under a64MiB V8 heap and256MiB service memory limit.
The first test alone passes at69.1M peak; the whole file reaches the heap cap at131.8M peak.
The folded/expanded test alone reaches the heap cap at135M peak.
A diagnostic assertion wrapper preserves the failure and limits formatting; that run reports line163 at68M peak.

The failed assertion compares a cyclic renderer host div with `undefined`.
Node's `internal/assert/assertion_error` formats values with `depth:1000`, `getters:true`, `sorted:true`,
`maxArrayLength:Infinity`, and `customInspect:false`.
The fixture has parent/children cycles, offsetParent/parentElement getters, and Vue references.
Formatting traverses this graph; a custom inspection method cannot bypass the disabled `customInspect` option.
The recovery changes all host-node assertions to Boolean or `Object.is` Boolean comparisons.
It also makes fixture unmount cleanup idempotent.

With only assertion/cleanup changes, the file reports an ordinary Boolean failure:5/6 pass,70M peak.
The failed upward-menu expectation remains a real test failure.
Pointermove updates reactive position; pointercancel reads geometry before Vue patches the DOM.
The resize callback likewise measures before the clamped position reaches the DOM.
The shared geometry function now uses the current explicit position, keeping DOM measurements for the default corner.
Coordinates, clamp bounds, drag threshold, upward-menu rule, tooltip rule, and zero-size-parent guard stay unchanged.
Coverage checks final drag events, resize tooltip direction, folded/expanded reachability, fullscreen transitions,
zero-size cached parents, mode/link actions, click suppression, balanced hooks, and the retained fullscreen button.

### Resource-bounded frontend verification

Each test file runs alone and serially with a unique systemd unit:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts && systemd-run --user --wait --pipe --collect --unit=<unique-unit> -p MemoryMax=256M -p MemorySwapMax=0 -p RuntimeMaxSec=30s -p LimitCORE=0 --working-directory=/home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts node --max-old-space-size=64 --max-semi-space-size=4 --test --test-concurrency=1 <single-test-file>
```

| Single test file | Unit suffix | Pass | Memory peak |
| --- | --- | ---: | ---: |
| `frontend/src/components/frames/FloatingPreviewTools.test.js` | `tools-final-20261009a` |6|69.8M|
| `frontend/src/components/ui/MarkdownContent.render.test.js` | `markdown-fixed-20261009a` |33|71.2M|
| `frontend/src/inline-artifacts/brokerLifecycle.test.js` | `suite-20261009a-1` |11|76.5M|
| `frontend/src/inline-artifacts/fileRelevance.test.js` | `suite-20261009a-2` |2|32.2M|
| `frontend/src/inline-artifacts/geometry.test.js` | `suite-20261009a-3` |6|57.7M|
| `frontend/src/inline-artifacts/heightBridge.test.js` | `suite-20261009a-4` |5|53.5M|
| `frontend/src/inline-artifacts/ownerAdapter.test.js` | `suite-20261009a-5` |4|57.2M|
| `frontend/src/inline-artifacts/publications.test.js` | `suite-20261009a-6` |118|63.4M|
| `frontend/src/inline-artifacts/rendering.test.js` | `suite-20261009a-7` |30|58.5M|
| `frontend/src/inline-artifacts/runtime.test.js` | `suite-20261009a-8` |19|49.2M|
| `frontend/src/inline-artifacts/shareOptions.test.js` | `suite-20261009a-9` |2|32.4M|
| `frontend/src/stores/framePool.test.js` | `integration-20261009a-1` |7|56.7M|
| `frontend/src/share-session/inlineAdapter.test.js` | `integration-20261009a-2` |7|50M|
| `frontend/src/share-session/inlineCompletion.test.js` | `integration-20261009a-3` |11|52.2M|

Every unit name has the prefix `inline-ui-`. All14 final test files exit0:261 passed,0 failed,0 skipped.
No recovered test reaches its heap, memory, swap, or time cap.
The assertions-only RED uses `inline-ui-safe-assert-20261009a` with the same resource flags.
An unchanged repeated RED uses `inline-ui-tools-fixed-20261009a`; an earlier failed read prevents the planned edit.
After the edit runs, `inline-ui-tools-fixed-20261009b` passes6/6 at65.6M peak.
The final tools rerun above follows the final Boolean assertion cleanup.

A separate capped compiler check parses and compiles all five changed Vue SFC scripts and templates.
The unit `inline-ui-sfc-final-20261009a` exits0 at63.1M peak with the same service/V8 resource limits.
It uses the repository's `@vue/compiler-sfc`, `compileScript(..., {inlineTemplate:true})`, and existing `wa-*` custom-element handling.
`git diff --check` passes.
This SFC check is not a SPA/share production build or browser product acceptance.
The recovery does not rerun the forbidden unbounded command, build bundles, or restart servers.
The controller owns the remaining bounded SPA/share builds and independent review after this commit.
