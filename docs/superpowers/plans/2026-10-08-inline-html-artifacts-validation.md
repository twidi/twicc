# Inline HTML artifacts: integrated validation

## Status

**Browser validation is NOT RUN.** Task 12 remains incomplete.
No authorized test instance exists. The controller must obtain startup authorization before browser checks.
No dev server, ad-hoc product server, package installation, or instance migration runs during this validation.
Pytest uses its isolated database. Node executes source modules without a product server.

## Environment and evidence

| Field | Recorded value |
| --- | --- |
| Date | 2026-10-08 |
| Worktree | `/home/twidi/dev/twicc-poc/.worktrees/feature-inline-html-artifacts` |
| Task 12 base | `e497c83aefa73f886c13538c50f46f2d00f94622` |
| Host | Linux `7.0.0-31-generic`, Ubuntu SMP PREEMPT_DYNAMIC |
| Python / Django / pytest | `3.13.14` / `6.0.4` / `9.0.3` |
| Node | `v22.22.1` |
| Browser / browser OS | NOT RUN; record actual versions after startup authorization |
| Owner URL / share host | NOT AVAILABLE; record actual test URLs after startup |
| Claude Code / Codex | Automated coverage for both; browser checks NOT RUN |
| Observed document load counters / frame nodes | NOT RUN |
| Browser screenshots | None; browser checks NOT RUN |
| Detailed logs | `.superpowers/sdd/2026-10-08-inline-html-artifacts/task-12-*.log` |
| Detailed commands and results | `.superpowers/sdd/2026-10-08-inline-html-artifacts/task-12-report.md` |

Never record production share tokens or passwords in this repository.
Record sanitized test URLs such as `/share/<test-share-token>/`.
Keep actual test tokens outside the repository.

## Automated coverage

| Check | Actual result |
| --- | --- |
| Addendum RED before implementation | 22 failed, 5 passed; missing inline guidance |
| Addendum GREEN | 27 passed |
| Focused prompt/source/Codex resume tests | 70 passed |
| Actual persisted screenshot-to-renderer integration | 4 passed, 26 deselected |
| Selected backend integration, final tree | 1004 passed, 1 device-node skip; 45.20 seconds |
| Full frontend integration, final tree | 2947 passed; zero failed/skipped; 26.995 seconds |
| Corrected gap-loading harness | 18 passed |
| Backend suites affected by mechanical lint cleanup | 172 passed |
| Exact required Ruff scope | All checks passed |
| SPA + broker shim + artifact shell + browser companion + share session | All five bundles build successfully |
| Browser acceptance | NOT RUN; startup authorization required |

Both provider addenda include the approved authoring sentences and valid single-file and asset-folder examples.
New session creation freezes that guidance. Existing stored addenda remain unchanged.
Codex receives its frozen addendum on thread start and omits developer instructions on resume.
The bundled sharing skill already documents default-on inline inclusion. Plugin version remains `0.109.0`.

The persisted-source regression crosses these actual integration boundaries:

1. Ingest each provider's tool image and finalized assistant record through `sync_session_slice`.
2. Normalize the screenshot tag and save its bytes under an isolated artifacts root.
3. Read canonical content from the persisted `SessionItem` and verify the catalog publication.
4. Send that persisted record to Node through stdin.
5. Execute canonical Codex text extraction, `createInlineTextContext`, `displayInlineText`, and `splitMarkdownBlocks`.
6. Compare the typed widget's block index, Unicode offset, and `publicationKey` with the backend descriptor.
7. Run full recompute and verify unchanged persisted content, publication identity, and frozen addendum.

The source boundary is **persisted canonical text after provider normalization**.
Claude retains block index `1`; existing Codex screenshot normalization coalesces text into canonical block index `0`.
Renderer trim, joining, and Markdown splitting preserve those persisted identities.
The test includes emoji, CRLF, leading whitespace, a real saved screenshot, and sliced/batched ingestion.
It does not change legacy screenshot normalization or substitute for browser lifecycle checks.

The complete frontend run exposes two extraction-harness errors after Task 7 adds inline geometry scheduling.
The corrected harness supplies `inlineContext`; its added test checks scheduling against the current geometry owner.
Mechanical Ruff cleanup preserves fixture objects through module assignments and removes unused imports.
It replaces `dict(...)` with literals and imports `Callable` from `collections.abc`.

## Browser fixture and procedure

After authorization, start the isolated worktree through `devctl.py` and record its actual URLs.
Use both providers to create regular main sessions. Give existing sessions the tag guidance directly in discussion.
Create two independent IDs, a single HTML file, and a page with relative JS/CSS/assets.
Use text input, range input, local tabs, and scrollable content. Keep their state in memory.
Add an explicit optional Save action for a separate `window.twicc.data` case.
Create native child conversations containing the same tag as ordinary text.

Instrument each test document with this diagnostic counter:

```javascript
const key = `inline-validation:${location.pathname}`;
const loadCount = Number(sessionStorage.getItem(key) || 0) + 1;
sessionStorage.setItem(key, String(loadCount));
window.inlineValidation = { loadCount, instance: crypto.randomUUID() };
document.documentElement.dataset.loadCount = String(loadCount);
```

This counter measures document loads. It does not save widget inputs.
Record the actual iframe node identity, counter, instance, input values, tab, page scroll, and parent scroll anchor.
Capture these values before and after each action. Record screenshots when geometry or focus needs visual evidence.
Use the production standalone share bundle for public route and adapter checks.

## Browser acceptance record

Every row remains **NOT RUN** because instance startup lacks authorization.
For each executed row, record provider, browser/OS, sanitized URL, counters, observed result, and screenshot path.

| Scenario | Expected observation | Status / observed evidence |
| --- | --- | --- |
| Both providers publish a single file and an asset-folder page | Exact inline placement; scripts, CSS, assets, and optional data load | NOT RUN; counters unavailable |
| Edit text/range/tab, then scroll beyond unload buffer and return | Same node, load counter, values, tab, and document scroll without data API | NOT RUN; counters unavailable |
| Switch cached sessions, hide chat, move/resize docks | Same iframe node and browsing state | NOT RUN; counters unavailable |
| Fullscreen, focus input, remove source row, press Escape | Bound iframe closes fullscreen; retained height returns or absent row hides frame | NOT RUN; counters unavailable |
| Focused widget scrolls out; interact with composer/header/overlay | Focus returns safely; hidden frame intercepts no input | NOT RUN; focus unavailable |
| Network consent arrives while row/session hides | Prompt waits; same broker returns when widget becomes visible | NOT RUN; broker observation unavailable |
| Correct same ID while old row unloads, then load old range | Latest placement only; load counter increases exactly once; parent scroll anchor stays fixed | NOT RUN; counters unavailable |
| Edit code without tag; save data; open Artifacts viewer | Inline counter stays fixed; relevant viewer reload only; independent memory and shared saved data | NOT RUN; counters unavailable |
| Reload, browser refresh, cache eviction, archive teardown | Accepted memory loss; saved data survives; listeners/prompts clean up | NOT RUN; counters unavailable |
| Autoheight, viewport-dependent layout, narrow/mobile pane | Height stays 160–900 CSS pixels; no loop or composer overlap | NOT RUN; geometry unavailable |
| Live share: new tag, data update, no viewers, reconnect | Filtered pending/ready manifests; code reload once; data refresh leaves load counter fixed | NOT RUN; counters unavailable |
| Snapshot Push update without a new tag | Metadata reconciliation reloads old open widget exactly once | NOT RUN; counters unavailable |
| Native child emits tag; toggle Include subagents | Ordinary child text; main manifest, copy, and revisions stay unchanged | NOT RUN; revisions unavailable |
| Legacy initial export fails; fix source and Reload | Failed copy alone retries; successful copies and captured placements stay fixed | NOT RUN; counters unavailable |
| Disable inclusion, change password, revoke, expire | Routes deny access; live/snapshot UI follows specified notification timing | NOT RUN; authorization observation unavailable |
| Private/public child view opens and closes | Main frames hide/return unchanged; no child placeholder, frame, probe, or broker | NOT RUN; counters unavailable |
| Shared HTML/assets/data/proxy with production bundle | Host/password gates apply; public writes fail; no private paths escape | NOT RUN; served production bundle unavailable |

Node and template tests cannot prove iframe survival through DOM movement or actual browser focus behavior.
These browser rows are required before overall Task 12 completion.

## Final-review risks and deployment prerequisites

- Task 10 M1: failed startup can bypass coordinator stop-and-drain before the serving cleanup block.
- Metadata mode-transition handling remains a named final-review risk.
- Existing SPA mixed static/dynamic import and chunk-size warnings require final triage.
- Six CLI slim/full projection failures reproduce against unchanged branch baseline. They remain outside this feature.
- The host cannot create the device node required by one confinement test; that test skips.
- The main checkout's inherited `VIRTUAL_ENV` mismatch warning confirms uv selects the worktree environment.

The user must install the declared Python dependency in their target checkout with `uv sync`.
The feature includes migration `0154_inline_artifact_catalogs`; the user's instance must apply it.
The user must restart their running backend through `devctl.py`, which applies pending migrations at startup.
Implementation and automated validation do not authorize deployment, remote push, or server startup.
