# Inline HTML artifacts: integrated validation

## Status

**Actual browser and HTTP checks run. Independent acceptance review remains pending.**
The record distinguishes PASS observations from PARTIAL coverage and unverified subcases.
It does not claim overall Task 12 completion or an entirely passing browser matrix.
The browser matrix contains **16 PASS and 1 PARTIAL row** after the focus, scroll-anchor, and B1 follow-up.

Actual parent-window-focus reconciliation passes. Forced WebSocket outage/reconnection and transient pending timing remain unverified.
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

The user authorizes isolated startup. The controller starts the instance with `devctl.py start --fresh-providers`.
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
| Required Ruff scope, plus I1 serializer/projection files | All checks passed |
| Fixture compatibility Ruff scope | Five unchanged findings reproduce on original sources: four F811 fixture redefinitions and one PIE807 lambda. No added-line finding. |
| SPA, broker shim, artifact shell, browser companion, share-session build | All five initial bundles build before I1. All five rebuild after B1; the actual B1 browser retest uses the new production share bundle. |
| R2-I1 standalone public share-session build | Production bundle builds successfully; 17.13s; exit 0. Browser validation has not yet loaded this correction. |
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
No actual-browser metadata503 test occurs. A fresh scoped review remains pending.

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
| Live tag/data changes, no-viewer interval, reconnect | **PARTIAL** | Real-time correction reloads once. Data changes preserve nonce/counter/RAM. Fresh viewer after no-viewer changes loads published version 3 rather than unpublished version 4 and reads after-no-viewer data. A real 78.125 MiB fixture reaches ready versions 1→4, once per finalized publication. Transient pending is NOT OBSERVED because copy completion beats the tool observation window. The test driver delays only publication; the exporter has no artificial delay. Forced WebSocket outage/reconnect is NOT RUN; automated adapter coverage remains. |
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

- Task 10 M1: failed startup can bypass coordinator stop-and-drain before the serving cleanup block.
- Metadata mode-transition handling remains a named final-review risk.
- Forced WebSocket outage/reconnect remains NOT RUN. Transient pending browser timing remains NOT OBSERVED.
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
Overall Task12 remains pending independent acceptance review.
