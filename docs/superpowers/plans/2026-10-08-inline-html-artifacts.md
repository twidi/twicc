# Inline HTML Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display live HTML artifacts inside main-session private and shared conversations while retaining their iframe state within the owning view's lifetime.

**Architecture:** Provider compute builds a publication catalog and exposes only the latest private descriptors. Session-owned runtimes keep frames and brokers outside virtualized rows. Public shares serve filtered manifests and confined copies through the existing share authorization and broker infrastructure.

**Tech Stack:** Python >=3.13, Django 6, Channels, SQLite, Vue 3, Pinia, markdown-it, Penpal, Vite 7, pytest, node:test.

**Spec:** [Inline HTML artifacts design](../specs/2026-10-08-inline-html-artifacts-design.md). Read the complete spec before executing this plan.

**Status:** Adversarial review complete. Both reviewers give READY. Application implementation has not started.

## Global Constraints

- HTML pages only; entry files use `.html` or `.htm` directly under `inline-artifacts/<id>/`.
- Artifact IDs match `[a-z][a-z0-9_-]{0,63}`. Identity includes the source session.
- One live private folder per artifact. No private source history, immutable publication copies, or state serialization.
- Only finalized, user-facing assistant text publishes. Each latest valid occurrence replaces the previous widget placement.
- Only the main agent of a regular session creates or publishes inline artifacts. Native subagents are excluded.
- The shared addendum forbids native subagent generation/publication and main-agent delegation of this work.
- No inline parsing, errors, placeholders, frames, brokers, catalogs, or exports in native subagent conversations.
- Keep actual iframe DOM nodes alive until owning view teardown or session cache eviction. Never reparent retained iframes.
- Keep `window.twicc.data` optional and separate from iframe memory state. Do not require input autosave.
- Initial height is 360 CSS pixels; requested and automatic heights stay within 160–900 CSS pixels.
- Title is plain text, at most 200 characters; default is the artifact ID.
- Provide Full screen and Reload only. No selection, responsive inspector, editing, or Submit to discussion.
- Include inline artifacts defaults to `true`, including existing shares without that key. Explicit `false` denies access.
- Export selected folders, including `data/`, through confined copies. Public saved data stays read-only.
- Private data limits remain 10 MiB per file and 100 MiB per tree. Aggregate inline exports stay within 200 MiB per share.
- Reject nested symlinks and special files in exports. Preserve configured artifacts-root symlinks.
- Keep existing network broker rules, share host gating, passwords, expiration, agent-share gates, and standalone artifact shares.
- Write all product strings, comments, code, tests, and docs in English. Do not update CHANGELOG.
- Stay in the current checkout unless the user explicitly asks for a branch or worktree. Preserve unrelated changes.
- Do not restart servers, run migrations against the user's instance, or install packages during this planning task.

## Review Focus

1. Emoji, CRLF, trimmed text, and split proposed-plan messages must preserve the same source offsets in Python and JavaScript. Tasks 1 and 7 test this.
2. A correction arrives while its old row is unloaded or its session is inactive. Tasks 2 and 5 test exactly one reload and no fallback.
3. A retained iframe requests network consent while its row disappears. Tasks 5 and 6 test broker retention and prompt visibility.
4. A folder changes into a symlink during copying, or a share is disabled during a slow export. Tasks 3, 9, and 10 test confinement and stale-work rejection.
5. Native subagent text contains a tag or copied parent publication. Tasks 2, 7, 8, and 11 prove it remains ordinary transcript text.

## Execution and verification rules

Each task contains a failing-test step, a verification step, an implementation step, and a passing-test step.
Tests below specify assertions; their fixtures are created in the named test files, following existing repository fixtures.
Use observable outputs and lifecycle counters. Do not test an implementation by copying its algorithm into assertions.

Run Python tests from the target checkout with `TWICC_DATA_DIR=$PWD uv run pytest ...`.
This also protects the main data directory when execution occurs in a user-requested worktree.
Run frontend tests with `cd frontend && node --test <paths>`.
Run focused tests after each task. Run broader checks in Task 12 after integration.

Every task ends with a commit restricted to that task's files.
Use its stated Conventional Commit subject and a body explaining behavior and validation.
Get the actual model at commit time for the `Co-Authored-By: Codex MODEL <codex@openai.com>` trailer.
Do not commit failing intermediate steps or unrelated files.

## File structure and shared contracts

Keep one implementation plan because publication, runtime ownership, and share copies form one feature.
The tasks provide separate review gates without creating separately shipped partial features.

### Backend files

| File | Responsibility |
| --- | --- |
| Create `src/twicc/inline_artifacts/publications.py` | Tag grammar, descriptor validation, source identities, catalog operations |
| Create `src/twicc/inline_artifacts/__init__.py` | Package marker |
| Modify `src/twicc/providers/compute_base.py` | Shared extraction and full/live catalog computation |
| Modify `src/twicc/providers/claude_code/compute.py`, `src/twicc/providers/codex/compute.py` | Canonical finalized assistant-text extraction |
| Modify `src/twicc/settings.py` | `CLAUDE_CODE_COMPUTE_VERSION` and `CODEX_COMPUTE_VERSION` increments |
| Modify `src/twicc/core/models.py`, new generated migration | Private catalog and server-owned export metadata |
| Modify `src/twicc/core/serializers.py` | Latest private descriptors and sanitized public metadata |
| Create `src/twicc/inline_artifacts/files.py` | Confined source reads, race-safe export copying, export read leases |
| Create `src/twicc/inline_artifacts/views.py` | Authenticated owner document, asset, and data routes |
| Create `src/twicc/inline_artifacts/share_selection.py` | Current eligibility and captured snapshot selection |
| Create `src/twicc/inline_artifacts/share_exports.py` | Prepared copies, publication transitions, lifecycle coordinator |
| Create `src/twicc/share/inline_artifact_views.py` | Token manifest, retry, file/data, and proxy routes |
| Modify `src/twicc/core/services/share_mutation.py`, `share_agent_gate.py` | Option validation and atomic share mutations |
| Modify `src/twicc/providers/sessions_watcher.py`, `db_writer.py` | Notify coordinator after committed publication changes |
| Modify `src/twicc/artifacts_watcher.py`, `src/twicc/cli/run.py` | Data events and coordinator start/stop |
| Modify `src/twicc/share/session_views.py`, `consumer.py`, `src/twicc/urls.py` | Public manifests, filtered live events, explicit routes |
| Modify `src/twicc/agent/system_prompt.py`, `src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md` | Agent authoring and share option guidance |
| Modify `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` | Minor version bump with the new documented sharing option |
| Modify `pyproject.toml`, `uv.lock` | Declare `markdown-it-py~=4.0` directly; version 4.0.0 already exists transitively in the lock |

### Frontend files

| File | Responsibility |
| --- | --- |
| Create `frontend/src/inline-artifacts/publications.js` | Equivalent tag grammar and Unicode offsets |
| Create `frontend/src/inline-artifacts/runtime.js` | View-owned registry, publication reconciliation, frame attachment |
| Create `frontend/src/inline-artifacts/context.js` | Shared injection key and adapter contracts |
| Create `frontend/src/inline-artifacts/geometry.js` | Scheduled visible-placeholder geometry and focus handling |
| Create `frontend/src/inline-artifacts/InlineArtifactBlock.vue` | Row-owned placeholder and publication state display |
| Create `frontend/src/inline-artifacts/InlineArtifactRuntimeHost.vue` | Stable broker owners, prompt presentation, fullscreen controls |
| Create `frontend/src/inline-artifacts/InlineArtifactFrameOwner.vue` | One mounted broker/height connection for each loaded runtime entry |
| Create `frontend/src/inline-artifacts/ownerAdapter.js` | Private session descriptors, owner URLs, bookmark grants |
| Modify `frontend/src/stores/framePool.js`, `components/frames/FrameHost.vue` | Idempotent retained registration and public host z-tier configuration |
| Modify `frontend/src/utils/markdown.js`, `components/ui/MarkdownContent.vue` | Typed artifact blocks without enabling raw HTML |
| Modify `frontend/src/components/session/detail/items/TextContent.vue` | Exact source context without trimming artifact offsets |
| Modify `frontend/src/components/session/detail/items/claude_code/ContentList.vue`, `Message.vue` | Text-block indices and finalized/synthetic context |
| Modify `frontend/src/components/session/detail/items/codex/Message.vue`, `AssistantMessage.vue`, `frontend/src/providers/codex/canonical.js`, `proposedPlan.js` | Original Text-block mapping and proposed-plan segmentation |
| Modify `frontend/src/components/session/detail/SessionItem.vue`, `SessionItemsList.vue` | Original source occurrence context and scroller geometry |
| Modify `frontend/src/views/SessionView.vue` | Runtime lifetime bound to the cached view |
| Modify `frontend/src/components/files/FilesPanel.vue` | Reload relevance within selected inline folder |
| Modify `frontend/src/artifact-broker/host.js`, `shim.js`, `composables/useArtifactBroker.js` | Optional bound height RPC and correct configuration rebinding |
| Modify `frontend/src/components/share/ShareDialog.vue` | Default-on switch and exact scope copy |
| Create `frontend/src/share-session/inlineAdapter.js` | Public manifest and token URLs, without private dependencies |
| Modify `frontend/src/share-session/ShareSessionApp.vue`, `SharedSubagentView.vue`, `ShareItemsList.vue` | Main-session runtime, drawer suppression, and disabled inline context for subagent text |
| Modify `frontend/src/share-session/shims/shareApi.js`, `shareLive.js`, `dataStoreShim.js` | Public manifest fetch/retry/reconnect adapters |
| Modify `frontend/vite.config.share.js` only if needed | Preserve existing private-store/router cuts |

### Data contracts

Choose a compact `Session.inline_artifacts` JSONField, default `{}`.
Its stored value is `{schema: 1, publications: Publication[]}`; `{}` means no records.
Full recompute replaces the catalog only when its existing revision guard accepts the result.
Live ingestion merges records by source identity. It does not append duplicates.

`Publication` has these JSON keys:

```text
artifact_id: string
line_num: integer
text_block_index: integer
tag_offset: integer
src: string
title: string
height: integer
```

Offsets count Unicode code points in the canonical persisted assistant text block, after provider normalization but before renderer transformations.
Screenshot rewrites belong to provider normalization; compute and rendering both read the resulting persisted text.
JavaScript converts string positions with `Array.from(prefix).length`; Python uses character indices.
Preserve original CRLF offsets even when the tokenizer normalizes line endings.
The frontend key is JSON serialization of `[session_id, line_num, text_block_index, tag_offset]`.
Artifact keys serialize `[source_session_id, artifact_id]`.

Private `serialize_session()` exposes `inline_artifacts: {artifact_id: Publication}` containing only latest publications.
Reduced list projections need not carry that blob. Session detail and updates must carry it.
Public session metadata never inherits this private field through generic session serialization.
Add the safe public boolean `inline_artifacts_supported`, true only when the shared root is a regular session.
Direct shares of native subagent transcripts keep existing sharing behavior but have no inline runtime or exports.

`RuntimeDescriptor` uses `{sourceSessionId, artifactId, publication, publicationKey, status, title, height, codeRevision}`.
Private adapters set `codeRevision` to null. Public adapters use the published code revision.
`RuntimeManifest` uses `{revision, descriptors: RuntimeDescriptor[]}`; private adapters use their local reconciliation sequence.
Adapters convert wire snake_case fields to these runtime names in one place.
All runtime entries, URLs, and bookmark lookup keys include the owning regular `sourceSessionId`.
That identity must equal the private view's main session or the public share's root session.
Native subagent records are never reconciled into this runtime.

Choose a `Share.inline_artifact_exports` JSONField, default `{}`; it is never writable through share options.
Store schema version, mutation revision, initialization state, captured publications, ready/error state, and current copy identifiers.
Index entries by the serialized artifact key. Keep source paths and copy identifiers server-side.
Keep excluded captured identities as selection tombstones until explicit recapture.
Visibility relaxation cannot reinterpret a previously captured identity as a newly introduced artifact.

Public `InlineManifest` is `{enabled: boolean, revision: integer, artifacts: PublicDescriptor[]}`.
Each public descriptor uses `source_session_id`, `artifact_id`, `publication`, `title`, `height`, `entry_filename`, `status`, and `code_revision`.
Every inline selection, readiness, inline-relevant option, or code-replacement transition increments public manifest revision monotonically.
Transcript-only include_subagents changes leave inline manifest revision and code revisions unchanged.
The adapter rejects older revisions across HTTP and WebSocket delivery. Equal revisions do not repeat lifecycle actions.
Statuses are `pending`, `ready`, `error`, or `not_included`. Error codes are stable and omit absolute paths.
`code_revision` changes on code-export replacement, including Push update without a new tag.
Data-only refreshes never change `code_revision`.
Disabled manifests still identify eligible placements for the **Inline artifact not included** placeholder.
Disabled descriptors use `not_included`, with null `code_revision` and no runnable URLs or ready exports.

Internal export directories use opaque replacement IDs beneath the existing per-share inline export root.
The public URL remains `/share/<token>/inline-artifacts/<source-session-id>/<artifact-id>/<asset-path>`.
Server metadata points to one current copy per selected artifact; internal replacement IDs never appear in route parameters.
This pointer permits an atomic DB manifest commit after all required folders are ready.
Retired copies exist only until current read leases finish, then cleanup removes them.

---

### Task 1: Define one publication grammar with shared fixtures

**Files:** Create backend/frontend `publications` modules and `fixtures/inline-artifacts/publications.json`. Create `tests/test_inline_artifact_publications.py` and `frontend/src/inline-artifacts/publications.test.js`. Modify `pyproject.toml` and `uv.lock`.

**Interfaces:**
- Python `parse_inline_artifact_blocks(text: str) -> list[ArtifactBlock]`, immutable NamedTuple records with `start`, `end`, `descriptor: dict | None`, `error: str | None`.
- Python `merge_publications(catalog: dict, records: list[dict]) -> dict`; `latest_publications(catalog: dict) -> dict[str, dict]`.
- JavaScript `parseInlineArtifactBlocks(text) -> ArtifactBlock[]`; `publicationKey(sessionId, publication) -> string`.
- Export `parseMarkdownTokens(source)` from the existing Markdown module for its syntax tokenizer. The publication module consumes it; the Markdown module receives recognized spans and never imports the publication module back.
- Valid and invalid standalone artifact-shaped blocks are returned. Ordinary content and excluded contexts are absent.

- [ ] **Step 1: Write the shared fixture assertions.** Each case has original text and expected code-point spans plus descriptor or error.

```python
def test_shared_publication_grammar(case):
    assert normalize(parse_inline_artifact_blocks(case['text'])) == case['expected']

def test_catalog_orders_source_occurrences_not_arrival():
    catalog = merge_publications({}, [publication(87), publication(42), publication(87)])
    assert len(catalog['publications']) == 2
    assert latest_publications(catalog)['preferences']['line_num'] == 87
```

Include one/multiline syntax, reordered/unknown/duplicate attributes, missing required values, invalid IDs, and quoted titles.
Include paths with traversal, URL/query/fragment/backslash/percent sequences and both HTML suffixes.
Pin default height 360, clamps 160/900, invalid nonintegers, title maximum 200, and plain-text escaping.
Pin fenced/indented/inline code, comments spanning lines, quotes, nested lists, lazy continuations, and existing `:::` containers.
Include emoji before tags, CRLF, three-space top-level indentation, four-space code indentation, and unclosed code fences/comments.

- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_publications.py -q` and `cd frontend && node --test src/inline-artifacts/publications.test.js`.** Expect missing-module failures initially.
- [ ] **Step 3: Implement the grammar and catalog signatures.** Use markdown-it-py block tokens on the backend and the existing Markdown tokenizer on the frontend. Declare the already-locked parser as a direct dependency during implementation. Add only the comment/container recognition needed for parity with TwiCC Markdown. Maintain an original-source offset map. Do not parse unrestricted substrings or access files. Require standalone top-level blocks and double quotes. Treat `src` as an unencoded filesystem path; build serving URLs later.
- [ ] **Step 4: Run both fixture suites.** Expect all cases and source-order assertions to pass.
- [ ] **Step 5: Commit `feat(artifacts): parse inline publication blocks`.** Include grammar rationale and both fixture results in the body.

### Task 2: Persist canonical publications through both compute paths

**Files:** Modify the model, serializers, both provider compute modules, `src/twicc/settings.py`, and `compute_base.py`. Generate a migration for `Session.inline_artifacts` and `Share.inline_artifact_exports`. Create `tests/test_inline_artifact_compute.py`. Extend `tests/test_compute_apply_signals.py` and `tests/test_codex_recompute_persistence.py`.

**Interfaces:**
- Base/provider hook `extract_inline_artifact_texts(parsed_json: dict) -> list[tuple[int, str]]`: original content-block index and canonical finalized assistant text, called only for regular sessions.
- `build_inline_artifact_publications(session_id: str, line_num: int, parsed_json: dict) -> list[dict]` uses Task 1 grammar.
- Full/live orchestration skips that hook for `Session.type == 'subagent'` and stores an empty catalog regardless of existing tag-like text.
- Session detail payload latest descriptors follow Data contracts. Existing `session_updated` messages carry descriptor changes.

- [ ] **Step 1: Write provider parity and lifecycle tests.** Feed realistic Claude assistant text blocks and canonical Codex assistant events through full and sliced live compute.

```python
def test_live_and_full_compute_publish_identical_catalog(provider, rollout):
    live = ingest_in_slices(provider, rollout, slice_bytes=256)
    rebuilt = recompute(provider, rollout)
    assert live.inline_artifacts == rebuilt.inline_artifacts
    assert len(rebuilt.inline_artifacts['publications']) == 2

def test_stale_recompute_does_not_erase_live_correction(compute_case):
    result = compute_case.capture_rebuild()
    compute_case.append_tag(line=87)
    compute_case.apply(result)
    assert compute_case.latest()['preferences']['line_num'] == 87
```

Also assert user/tool/reasoning/synthetic messages do not publish; provider mirrors do not duplicate publications.
Test native child tags and copied parent history produce empty catalogs, including recompute clearing any stale subagent catalog.
Test a finalized main-session text publishes before the complete turn ends.
Missing HTML files still produce valid catalog records. Invalid tags do not supersede valid ones.
Re-ingestion and background recompute emit no artificial new publication or duplicate export trigger.
Assert private session details expose latest only and public metadata exposes no catalog or owner paths.

- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_compute.py tests/test_compute_apply_signals.py tests/test_codex_recompute_persistence.py -q`.** Expect new field/hook assertions to fail.
- [ ] **Step 3: Implement the extraction hook after existing provider normalization.** Preserve canonical representation rules and original block indices. Carry catalog data in `session_complete.session_fields`; merge in `_sync_session_slice`. Reuse `guard_compute_revision` and the serialized writer. Bump both current compute versions. Generate, but do not apply, the migration to the user's instance. Cover migration empty defaults in the isolated pytest database.
- [ ] **Step 4: Run the focused suites.** Expect full/live parity, revision rejection, deduplication, and serializer boundary tests to pass.
- [ ] **Step 5: Commit `feat(artifacts): compute inline publication catalogs`.** Record the generated migration and remind the user to migrate their running instance after implementation.

### Task 3: Provide confined private document and asset URLs

**Files:** Create `inline_artifacts/files.py`, `views.py`, and `tests/test_inline_artifact_files.py`, `tests/test_inline_artifact_owner_routes.py`. Modify `src/twicc/urls.py`. Reuse `artifacts/broker_html.py`, existing content-type/header helpers, and existing artifact data helpers without broad refactoring.

**Interfaces:**
- `open_source_asset(session_id: str, artifact_id: str, asset_path: str) -> BinaryIO` returns a confined regular-file handle or raises a stable unavailable error.
- `copy_source_artifact(session_id: str, publication: dict, destination: Path, remaining_bytes: int) -> int` returns actual copied bytes and rejects unsafe entries.
- `open_export_asset(export_root: Path, asset_path: str) -> BinaryIO` uses the same handle-based boundary.
- `inline_asset_response(file: BinaryIO, content_type: str, *, as_document: bool, head: bool, release: Callable[[], None] | None = None) -> HttpResponse` consumes the validated handle and owns closing/releasing it.
- Read HTML bytes from that handle for `artifact_html_response()`. Stream other assets from the same handle with FileResponse. Never reopen a checked path through `_serve_artifact_file()` or `_raw_file_response()`.
- Owner route `/api/sessions/<session_id>/inline-artifacts/<artifact_id>/<path:asset_path>` supports documents, assets, and existing data GET/PUT/DELETE semantics.
- Require a regular owning Session on private inline routes too. Subagent HTML remains accessible only through existing ordinary file/artifact capabilities.

- [ ] **Step 1: Write confinement and data behavior tests.** Use temporary session roots and copy destinations, never production artifacts.

```python
def test_copy_rejects_nested_symlink(root, outside_file, publication):
    (root / 'app.js').symlink_to(outside_file)
    with pytest.raises(InlineArtifactUnavailable):
        copy_source_artifact('session', publication, root.parent / 'copy', 200 * 1024 * 1024)

def test_configured_artifacts_root_symlink_is_supported(linked_artifacts_root):
    with open_source_asset('session', 'preferences', 'index.html') as file:
        assert file.read() == b'<p>preferences</p>'
```

Also swap a directory for a symlink between traversal and file open using a synchronization barrier.
Verify it never reads outside bytes; reject FIFO/socket/device entries and decoded traversal.
Repeat the replacement race against the final document and asset HTTP responses, not only copy helpers.
Test sibling artifact/session traversal, missing files, bounded copying when source files grow, and exact 200 MiB aggregate accounting.
Owner HTML receives existing shim/CSP. Relative JS/CSS load. Ordinary unauthenticated routes keep existing auth behavior.
Native subagent IDs are unavailable on private inline routes, regardless of files manually present in their artifacts folder.
Private `data/` get/set/list/remove retains 10 MiB/file and 100 MiB/tree limits without touching sibling data.

- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_files.py tests/test_inline_artifact_owner_routes.py tests/test_artifact_data_writes.py -q`.** Expect missing helper and route failures.
- [ ] **Step 3: Implement source/copy/export helpers and routes.** Resolve the configured root once, then traverse and open relative to trusted directory handles with no-follow checks. Use regular-file `fstat` checks and counted streaming copies. Verify containment during actual reads; a preflight realpath check is insufficient. Use equivalent safe handle primitives on supported platforms; fail closed if unavailable. Keep private saved-data handling on the existing server gate and broker headers. Run copying through `asyncio.to_thread`, outside DB locks.
- [ ] **Step 4: Run focused route and file tests.** Expect no outside reads, matching data semantics, and standard HTML wrapping.
- [ ] **Step 5: Commit `feat(artifacts): serve confined inline documents`.** Document any platform-specific safe-handle limitation with its test result.

### Task 4: Separate retained frame ownership from row attachment

**Files:** Modify `framePool.js`, `FrameHost.vue`, and `framePool.test.js`. Create `inline-artifacts/runtime.js`, `context.js`, and `runtime.test.js`.

**Interfaces:**
- Add `pool.ensureRegistered(id, descriptor) -> frame`; existing registration API stays compatible with pane frames.
- `createInlineArtifactRuntime({viewId, pool, adapter}) -> runtime` is framework-light and dependency-injected.
- Runtime methods: `reconcile(manifest)`, `attach(artifactKey, publicationKey, attachment) -> detach`, `setVisible(artifactKey, visible)`, `setActive(active)`, `reload(artifactKey)`, `openFullscreen(artifactKey)`, `closeFullscreen()`, `dispose()`.
- `attachment` supplies placeholder element, clip element, suppression getter, and conversation-focus callback.
- Runtime exposes reactive append-ordered loaded entries and one fullscreen artifact key.
- Adapter methods: `documentUrl(descriptor)`, `brokerConfig(descriptor)`, `probe(descriptor, {signal})`, `retry(descriptor)`, and `dispose()`. Runtime disposal invokes adapter disposal once.
- `probe` resolves `{available: boolean, error: string | null}` from a visibility-gated HEAD request to the exact document route.
- Runtime tracks `loadState: idle | probing | loading | ready | error` separately from manifest status. Only the current reload generation can apply a probe result.
- After navigation, the bound shim reports document readiness. An iframe `load` event alone never proves an HTTP-success document.
- FrameHost accepts optional z-tier values; private defaults stay unchanged. Public main-session frames stay suppressed behind subagent drawers.

- [ ] **Step 1: Write runtime transition tests with a fake frame pool and load counter.** No DOM assertions in node:test.

```js
test('detaching a row retains its loaded frame', () => {
    const { runtime, pool, artifact, publication, attachment } = runtimeFixture()
    runtime.reconcile(manifest(artifact, publication))
    const detach = runtime.attach(artifact, publication, attachment)
    runtime.setVisible(artifact, true)
    const frame = pool.frames[frameKey(artifact)]
    detach()
    assert.equal(pool.frames[frameKey(artifact)], frame)
    assert.equal(frame.visible, false)
    runtime.dispose()
    assert.equal(pool.frames[frameKey(artifact)], undefined)
})
```

Assert no frame before actual visibility, registration idempotence, append-only keys, and independent source-session IDs.
Assert a failed old HEAD probe cannot replace a newer successful correction. Missing documents show an error; restoring the file and Reload retries its current URL.
Attach duplicate presentations without registering two frames; deterministic currently visible attachment owns geometry.
Verify a new publication or public code revision reloads exactly once, while equal reconciliation and data changes do not.
Verify loaded entries survive inactivity; never-loaded corrections stay unloaded; disposal removes only this view's frames.
Assert `ready(11)` then delayed `pending(10)` stays ready, and `disabled(12)` then delayed `ready(11)` stays disabled.

- [ ] **Step 2: Run `cd frontend && node --test src/stores/framePool.test.js src/inline-artifacts/runtime.test.js`.** Expect missing runtime/registration failures.
- [ ] **Step 3: Implement the runtime and frame-pool extension.** Keep immutable iframe identity separate from publication/reload identity. Patch descriptors without replacing DOM-order registry objects. A newer publication clears stale attachment authority and closes fullscreen. Hide before attaching the new placement; retain loaded frame ownership. Keep pane-owned PersistentFrame behavior intact.
- [ ] **Step 4: Run both suites.** Expect existing pane pool tests and new retained-runtime tests to pass.
- [ ] **Step 5: Commit `feat(artifacts): retain inline frames by owning view`.

### Task 5: Bind private runtimes, brokers, and prompt lifetime

**Files:** Create `ownerAdapter.js`, `InlineArtifactRuntimeHost.vue`, `InlineArtifactFrameOwner.vue`. Modify `SessionView.vue`, `SessionItemsList.vue`, and `useArtifactBroker.js`. Create `ownerAdapter.test.js`; extend `runtime.test.js`. Extend existing broker tests or create `frontend/src/inline-artifacts/brokerLifecycle.test.js`.

**Interfaces:**
- `makeOwnerInlineAdapter({sessionId, store, api}) -> adapter` produces Task 4 methods for the frozen main session ID. Reject mismatching source IDs.
- SessionView provides `{runtime, sourceSessionId: sessionId}` through the Task 4 context key. Subagent SessionItemsList passes no inline text context, even with this ancestor injection.
- Observe only main-session descriptors. Switching to a child panel hides main-session attachments without unloading their frames.
- Stable `InlineArtifactFrameOwner` receives one loaded entry, uses `pool.frameEl(frameId)`, and mounts `useArtifactBroker` there.
- Extend `useArtifactBroker` config with `bindingKey` and optional `onInlineHeight(height)`; include bound document/config identity in rebinding decisions.
- Also support `onInlineReady()` from the current bound shim. A current-generation readiness timeout uses the existing 30000 ms broker handshake budget and becomes a retryable load error.
- RuntimeHost renders loaded owners outside row components and KeepAlive-movable pane content. The component itself contains no iframe DOM.

- [ ] **Step 1: Write adapter and broker lifecycle assertions.** Record connections and settled prompts with injected fake broker factories.

```js
test('hidden owners retain broker and pending consent until eviction', () => {
    const f = brokerFixture()
    f.requestConsent()
    f.runtime.setActive(false)
    assert.equal(f.connection.destroyCount, 0)
    assert.equal(f.promptSettled, false)
    assert.equal(f.promptVisible, false)
    f.runtime.setActive(true)
    f.runtime.setVisible(f.artifact, true)
    assert.equal(f.promptVisible, true)
    f.runtime.dispose()
    assert.equal(f.connection.destroyCount, 1)
    assert.equal(f.promptDecision, 'deny')
})
```

  - A row detaches or session deactivates: zero broker destroys; pending consent stays pending and its dialog hides.
  - Session/widget becomes visible: same connection and pending prompt return.
  - Entry filename or public copy identity changes: old connection destroys and new config binds exactly once.
  - View evicts: each connection destroys, prompt settles deny, and listeners stop.
  - Reload keeps saved data and loses only document memory state.
  - Owner data writes use `inArtifactsRoot: true`; unbookmarked network requests retain the existing owner consent behavior.
  - A private child uses the same artifact ID as its parent: no child frame/broker/probe exists. Main-session frames hide while the child panel opens and return unchanged.
  - Successful HEAD followed by a failed document GET does not become ready merely because iframe load fires. Only the bound shim-ready acknowledgement sets ready.
- [ ] **Step 2: Run `cd frontend && node --test src/inline-artifacts/ownerAdapter.test.js src/inline-artifacts/brokerLifecycle.test.js src/inline-artifacts/runtime.test.js`.** Expect missing adapter/config lifecycle failures.
- [ ] **Step 3: Implement adapter, stable owners, and cached-view wiring.** Create runtime only for a regular SessionView, using its frozen main session ID. Observe only its descriptors, including while inactive. Use SessionItemsList viewActive state to hide attachments when switching to subagent panels. Dispose on view unmount, never row unmount. Keep broker watchers alive while cached. Render consent dialogs only for active visible widget owners; use the host overlay and existing ArtifactBrokerPrompt. Correct the current same-window shortcut so a changed document configuration cannot keep an old broker. Probe document availability before first navigation and each Reload/correction; abort stale probes and gate completion by reload generation. After navigation, require shim readiness within the existing handshake budget; dispose stale timers. Do not rely on iframe load/error events to infer HTTP status.
- [ ] **Step 4: Run the new suites and existing artifact broker tests.** Expect preserved connections and correct entry-point rebinds.
- [ ] **Step 5: Commit `feat(artifacts): own inline brokers in cached sessions`.

### Task 6: Add clipping, bounded height, and fullscreen without moving frames

**Files:** Create `geometry.js`, `geometry.test.js`, `InlineArtifactBlock.vue` skeleton. Modify `runtime.js`, RuntimeHost/FrameOwner, `artifact-broker/host.js`, `shim.js`, and `useArtifactBroker.js`. Extend frame-pool tests and create `frontend/src/inline-artifacts/heightBridge.test.js`.

**Interfaces:**
- `createInlineGeometryScheduler({pool, requestFrame, cancelFrame}) -> {attach, detach, schedule, dispose}` batches measurements of visible attachments only.
- Add optional broker RPC `reportInlineHeight({height, mode})`; `mode` is `inline` or `fullscreen` from host-bound configuration.
- Add optional broker RPC `requestInlineEscape()`. The shim forwards Escape keydown from the iframe; the runtime closes only fullscreen owned by that bound frame.
- Add broker RPC `reportInlineReady()` for Task 5 load acknowledgement. The injected shim announces even when the page performs no fetch. Ordinary pane/shell brokers accept it as a no-op.
- Accept the RPC only on the Penpal connection bound to this iframe's `contentWindow`. Existing shell/pane brokers ignore unsupported optional height reporting.
- Runtime remembers `inlineHeight` per artifact, clamps to 160–900, and ignores fullscreen measurements.

- [ ] **Step 1: Write geometry and RPC tests.** Assert viewport intersection, complete clipping, suppression, focus return, and no measurement work for detached background frames. Assert many scroll events produce one scheduled pass. Height tests reject NaN/nonfinite/forged reports, clamp 159 to 160 and 901 to 900, remember the last inline value, and ignore fullscreen reports. A width-dependent page that alternates reports must settle without an endless ResizeObserver loop. Test that an unrelated bound frame's Escape cannot close another frame's fullscreen, and disposal removes shim/host key listeners.

```js
test('fullscreen reports do not replace bounded inline height', () => {
    const f = heightFixture({requestedHeight: 360})
    f.boundReport({height: 901, mode: 'inline'})
    assert.equal(f.entry.inlineHeight, 900)
    f.runtime.openFullscreen(f.artifact)
    f.boundReport({height: 1800, mode: 'fullscreen'})
    f.runtime.closeFullscreen()
    assert.equal(f.entry.inlineHeight, 900)
    f.unboundReport({height: 160, mode: 'inline'})
    assert.equal(f.entry.inlineHeight, 900)
})
```

- [ ] **Step 2: Run `cd frontend && node --test src/inline-artifacts/geometry.test.js src/inline-artifacts/heightBridge.test.js src/inline-artifacts/runtime.test.js src/stores/framePool.test.js`.** Expect geometry and bridge failures.
- [ ] **Step 3: Implement scheduled geometry and optional height/Escape RPCs.** Measure intrinsic content rather than viewport-sized document scrollHeight. Coalesce shim observations, skip equal heights, and disable unstable feedback for that document in favor of requested height/internal scrolling. Keep one scheduler per owning view. Hook scroller scroll/resize, visible placeholders, pool geometry epochs, and overlays. Hidden/focused frames return focus before hiding. Fullscreen patches the same host cell geometry/z-tier and uses `expandPreviewHost` to remove the private containing block; no iframe Teleport. Controls live outside the virtual row. Both parent keydown and bound iframe Escape close fullscreen. Preserve inline height when expanded and when the row disappears.
- [ ] **Step 4: Run focused suites.** Expect bounded geometry, stable focus, and no background polling or resize feedback.
- [ ] **Step 5: Commit `feat(artifacts): add inline geometry and fullscreen controls`.

### Task 7: Render typed blocks with original occurrence context

**Files:** Modify Markdown utilities/component, TextContent, Claude Message/ContentList, Codex Message/AssistantMessage, `frontend/src/providers/codex/canonical.js`, proposedPlan, SessionItem, and SessionItemsList. Complete InlineArtifactBlock. Create `frontend/src/inline-artifacts/rendering.test.js`; extend MarkdownContent render tests. Modify FilesPanel and add `frontend/src/inline-artifacts/fileRelevance.test.js`.

**Interfaces:**
- `InlineTextContext = {sessionId, lineNum, sourceOffset, finalized, publicationAllowed, recognizedSpans}` accompanies only eligible assistant text.
- `assistantTextBlocks(data) -> [{textBlockIndex, text}]` exposes original canonical Codex Text entries without changing `agentMessageText()` for other consumers.
- `recognizedSpans` contains complete-block grammar results mapped into the rendered joined source, with each original `textBlockIndex` and `tag_offset` retained.
- Parse each complete canonical Text block before joining, trimming, or proposed-plan segmentation. A tag split across Text blocks cannot publish or become an artifact error block.
- `splitMarkdownBlocks(source, {inlineArtifacts, sourceOffset, recognizedSpans} = {})` produces typed blocks only from supplied original recognized spans, without reparsing sliced text for tag eligibility.
- MarkdownContent accepts optional inline context and injected runtime; without both, no widget executes.
- Extend `splitProposedPlan()` output with original code-point start offsets for before/plan/after segments.
- Invalid blocks show compact errors. Superseded valid blocks render nothing. Disabled public blocks show **Inline artifact not included**.

- [ ] **Step 1: Write source-context and rendering tests.** Assert original offsets survive TextContent trimming, command display transformations, emoji, CRLF, multiple Claude blocks, and Codex proposed-plan segmentation. Test two canonical Codex Text entries with a tag in the second, a tag split across entries, and screenshot normalization before a tag. Cover tags inside a proposed-plan body according to complete-source eligible grammar, not a separate publication identity. Include proposed-plan wrappers inside fences, comments, and colon containers; slicing must not promote their tags or suppress their example text. Assert user/tool/reasoning/example rendering cannot execute. Native subagent messages receive no inline context and preserve tag-like text as ordinary Markdown without artifact errors or pending placeholders. Synthetic main-session streaming candidates remain noninteractive and never supersede.

```js
test('unloaded latest publication suppresses the loaded older widget', () => {
    const f = renderingFixture({loadedLines: [42], latestLine: 87})
    assert.equal(f.widgetState({lineNum: 42}), 'superseded')
    assert.equal(f.createdFrames.length, 0)
    f.loadLine(87)
    assert.equal(f.widgetState({lineNum: 87}), 'active')
})
```

  - Latest descriptor beyond loaded ranges removes the old widget without a fallback banner.
  - Invalid newer tags leave the old valid placement active; valid missing documents show errors at the new placement.
  - Sanitized HTML rendering remains unchanged; titles never enter `v-html`.
  - Own inline code/assets changes can reload an existing Artifacts-tab preview; sibling folders and any `data/` changes cannot.
- [ ] **Step 2: Run `cd frontend && node --test src/inline-artifacts/rendering.test.js src/inline-artifacts/fileRelevance.test.js src/components/ui/MarkdownContent.render.test.js src/inline-artifacts/publications.test.js`.** Expect new typed/context assertions to fail.
- [ ] **Step 3: Implement typed rendering and context propagation.** Build complete-source recognized spans and the Codex joined-text mapping before formatting or segmentation. Pass original content-block identities and real SessionItem line numbers; use getParsedContent helpers. Intersect recognized spans with each displayed segment, including the proposed-plan body, without resetting grammar context. Gate finalized status from persisted canonical items, never merely a closed-looking tag. Render Vue artifact blocks separately from sanitized HTML. Include context in render-cache keys; keep ordinary Markdown caches stable. Attach the placeholder to runtime, scroller clip element, row height reporting, and existing scroll-anchor correction. Use async component import if a static component/runtime cycle appears. Limit FilesPanel reload relevance to the selected inline folder.
- [ ] **Step 4: Run focused suites.** Expect parity, correct supersession, and unchanged ordinary Markdown behavior.
- [ ] **Step 5: Commit `feat(artifacts): render inline widgets in conversations`.

### Task 8: Derive share selection without exposing private metadata

**Files:** Create `share_selection.py`, `tests/test_inline_artifact_share_selection.py`. Modify `share_mutation.py`, `share_agent_gate.py`, serializers, ShareDialog, bundled twicc-share SKILL.md, and its `.claude-plugin/plugin.json`. Extend `tests/test_share_agent_gate.py`, `tests/test_share_cli_payloads.py`, `tests/test_twicc_share_skill.py`; create `frontend/src/inline-artifacts/shareOptions.test.js`.

**Interfaces:**
- `include_inline_artifacts(options: dict) -> bool`: missing means true, explicit false means false.
- `select_share_publications(share, *, captured: dict | None = None, recapture: bool = False) -> dict[str, dict]` returns publications only from a regular `share.session_id`. A native subagent root returns an empty selection without initialization metadata.
- `public_inline_manifest(share) -> dict` serializes Data contracts without paths or copy IDs.
- Only server-owned export metadata captures selections. Caller options cannot contain manifest fields.
- `SelectionNotReady(session_ids: tuple[str, ...])` prevents capture when the shared regular session fails existing `session_compute_ready()`. Native child readiness never delays inline exports.
- Public manifest requests return retriable 409 `session_not_ready` without writing initialization/capture metadata. Owner mutations return the same typed error without changing published state.

- [ ] **Step 1: Write selection and option tests.** Pin missing/new default true, explicit false, literal-boolean agent validation, and rejection of catalog/path/readiness fields in caller payloads. Use actual main-session display ceiling and frozen boundary.

```python
def test_snapshot_subagent_option_does_not_change_inline_selection(snapshot_case):
    snapshot_case.capture(main_tag(line=10, src='inline-artifacts/preferences/index.html'))
    before = snapshot_case.inline_state()
    snapshot_case.append_child_tag(line=20, src='inline-artifacts/preferences/widget.html')
    snapshot_case.change_options(include_subagents=False)
    assert snapshot_case.inline_state() == before
    selected = snapshot_case.change_options(show_timestamps=False)
    assert selected['["main","preferences"]']['line_num'] == 10
```

Also test root frozen boundary, child-tag exclusion even with include_subagents true, excluded captured tags with no fallback, newly included main artifacts, and title-only changes. Public disabled manifests preserve placeholder positions without executable exports. No private latest catalog or absolute paths appear in public metadata or WS.
`inline_state()` includes selected publications, public manifest revision, code revisions, and current copy identities.
Direct sharing of a native subagent remains a usable transcript share, with `inline_artifacts_supported: false`, no captured inline metadata, and no runnable inline descriptors.
Request legacy/new snapshot capture before historical recompute, then after compute. The first attempt stores no empty capture; the second selects real main-session publications. An unready child cannot block it.
Tighten then relax the display ceiling after the main session changes its entry filename. An excluded captured identity cannot be recaptured as newly introduced until Push update.
- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_share_selection.py tests/test_share_agent_gate.py tests/test_share_cli_payloads.py -q` and `cd frontend && node --test src/inline-artifacts/shareOptions.test.js`.** Expect missing selection/option failures.
- [ ] **Step 3: Implement selection, safe serialization, and switch copy.** Check shared main-session type and compute readiness before capture, and recheck them before committing a prepared selection. Reuse existing filtered item helpers only for that session. Never traverse descendants for inline selection. Captured snapshots preserve still-permitted publications; visibility tightening excludes without replacement and keeps identity tombstones. Only genuinely newly introduced main identities capture current eligible publications. Ignore include_subagents changes for inline export decisions. Set the switch with `?? true` and exact spec label/explanation. Thread the option through owner REST, CLI options, and agent shape validation. Read the plugin README and existing skills, document the option in twicc-share, and bump plugin.json minor version from its value at execution time in the same commit. Leave sharing kill switches unchanged.
- [ ] **Step 4: Run focused suites.** Expect consistent defaults, main-session selection, and subagent exclusion without standalone artifact-share changes.
- [ ] **Step 5: Commit `feat(sharing): select eligible inline artifact publications`.

### Task 9: Prepare and publish atomic export replacements

**Files:** Create `share_exports.py`, `tests/test_inline_artifact_share_exports.py`. Modify `share_mutation.py`; extend `tests/test_share_mutation.py`.

**Interfaces:**
- `prepare_inline_exports(share_id: str, selection: dict, *, retain: dict | None = None) -> PreparedInlineExports` performs counted copies off-thread.
- PreparedInlineExports is an immutable NamedTuple containing ready metadata, new copy IDs, retained copy IDs, and copied byte totals.
- `commit_inline_exports(share_id: str, expected_revision: int, prepared, options: dict) -> bool` publishes options, manifest pointers, and root boundary under the existing DB writer.
- `retire_inline_exports(share_id: str, copy_ids: list[str]) -> None` removes unreferenced copies after read leases finish.
- `ensure_inline_exports(share_id: str) -> None` initializes legacy default-on links with captured metadata before scheduling copies.
- `retry_inline_export(share_id: str, artifact_key: str) -> None` retries only failed initial snapshot exports or current live errors.
- `recover_snapshot_initialization(share_id: str) -> None` changes interrupted initial pending entries to `error: export_interrupted`, preserving captured publications and successful copies.
- `remove_inline_share_exports(share_id: str) -> None` denies new leases and cleans all copies after existing leases complete, including roots with no remaining Share row.

- [ ] **Step 1: Write failure and concurrency tests with copy barriers and temporary export directories.** Assert initial creation fails without a share row if a requested copy fails. Push update failure preserves options, root boundary, selected manifest, code revisions, and old files. Mode/visibility changes that need new snapshot copies also preserve all old state on failure. Aggregate retained plus new exports must stay at or below 200 MiB.

```python
async def test_failed_push_preserves_complete_snapshot(snapshot_case):
    before = await snapshot_case.published_state()
    snapshot_case.append_tag_and_break_source()
    result = await propagate_share(snapshot_case.share)
    assert result.success is False
    assert await snapshot_case.published_state() == before
```

  - Successful Push update replaces code revision even when the selected tag stays equal.
  - Snapshot retains matching captured selection/copy through unrelated options; newly included identity gets a current copy.
  - Legacy initialization captures selection once, exposes pending then error, and retries only failed copies.
  - Restart never recaptures successful snapshot copies from changed owner sources.
  - Crash with one ready and one pending legacy snapshot entry. Recovery turns only pending into a retriable error; retry uses its captured publication despite newer main-session tags.
  - Slow work loses its revision race after disable, revoke, deletion, or a newer publication and cannot restore access.
  - An in-flight file response completes against its leased copy; retirement cannot unlink it prematurely.
  - Delete a session share during a leased response: new routes fail, the response finishes, then its entire inline export root disappears. Startup also removes orphan roots left after a committed deletion.
- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_share_exports.py tests/test_share_mutation.py -q`.** Expect export/revision failures.
- [ ] **Step 3: Implement staged copying and durable metadata transitions.** Reserve a share mutation revision before preparing work; serialize owner mutations per share and background updates per artifact under that share. Do not hold a SQLite lock across copy I/O. Put complete replacement folders under opaque internal IDs before committing DB pointers. Re-read activity, current option, eligibility, compute readiness, and expected publication/revision inside the commit gate. If rejected, discard only this work's unused copies. Use response-lifetime read leases, including FileResponse close cleanup. Preserve currently referenced trees across crashes; clean unreferenced staging/retired trees during reconciliation. For legacy links, capture metadata first and allow per-artifact failures. Recover interrupted initial snapshot entries as retriable errors without recapturing. Extend delete_share cleanup to session inline exports, and reconcile orphan roots without a Share row. Explicit snapshot mutations remain all-or-nothing.
- [ ] **Step 4: Run focused suites.** Expect rollback, lease cleanup, aggregate size accounting, and stale-work rejection.
- [ ] **Step 5: Commit `feat(sharing): publish confined inline export copies`.

### Task 10: Coordinate live publications and data-only exports

**Files:** Extend share_exports, `src/twicc/providers/sessions_watcher.py`, `src/twicc/providers/db_writer.py`, ArtifactsWatcher, `cli/run.py`, and `share/consumer.py`. Create `tests/test_inline_artifact_share_coordinator.py`; extend `tests/test_share_consumer.py`.

**Interfaces:**
- `InlineExportCoordinator.start()`, `stop()`, `publication_changed(session_id: str)`, `files_changed(session_id: str, paths: list[str])`, `reconcile(share_id: str)`.
- Enqueue notifications only after successful committed compute. Worker compute processes never copy exports or write share rows.
- `share_inline_artifacts` WS event contains only share ID and sanitized manifest, sent through ShareConsumer after current authorization rechecks.
- `refresh_export_data(share_id: str, artifact_key: str, paths: list[str]) -> None` updates only the selected ready copy's data tree.

- [ ] **Step 1: Write coordinator integration tests.** A new eligible finalized tag moves placement to pending, copies once, then publishes ready; duplicate compute does not recopy. Intermediate code writes do not change exports. Data PUT/delete updates copied data only and preserves HTML bytes and code revision. Writes during a replacement cannot lose the latest data change or modify a retired copy. Enforce aggregate size limits on later data growth too; failures preserve the last complete published data and become owner-visible errors without reloading code.

```python
async def test_live_data_refresh_keeps_published_code(live_export_case):
    before = await live_export_case.read_html_and_code_revision()
    live_export_case.change_unpublished_html()
    live_export_case.save_data({'density': 'compact'})
    await live_export_case.drain()
    assert await live_export_case.read_html_and_code_revision() == before
    assert await live_export_case.read_data() == {'density': 'compact'}
```

  - Disable/ineligibility/revoke during queued data or code work wins immediately.
  - An enabled live share with no viewer still updates copies.
  - Restart/reconnect reconciles missed publications and data state but not unpublished code or successful frozen snapshots.
  - Startup performs snapshot-initialization recovery and orphan cleanup separately from live export reconciliation.
  - Owner artifact watcher behavior and monotonic Artifacts-tab availability remain intact.
  - ShareConsumer never forwards raw watcher paths, private catalogs, child inline manifests, or revoked-share updates. Existing child transcript events remain unchanged.
- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_share_coordinator.py tests/test_share_consumer.py tests/test_compute_apply_signals.py -q`.** Expect notification/coordinator failures.
- [ ] **Step 3: Implement one process-local coordinator with bounded, coalesced work.** Start it with backend lifecycle and stop/drain it on shutdown. Extend the watcher with an enqueue callback; keep its filesystem probe independent of ORM readiness. Notify after live commit and accepted full-compute apply. Reconcile active live share metadata and ready data trees on startup, without a separate transcript scanner. Also recover interrupted snapshot initialization and deleted-share orphan roots through Task 9 helpers; never refresh successful frozen copies. Retry live errors on relevant source changes. Copy data through safe counted helpers and atomic replacement; perform whole-subtree reconciliation when events are missed. Track a dirty revision during copy so later changes queue another pass. Clear source sessions and artifact paths before public broadcasts.
- [ ] **Step 4: Run focused suites.** Expect data-only behavior, startup recovery, stale-work rejection, and filtered broadcasts.
- [ ] **Step 5: Commit `feat(sharing): coordinate live inline exports and data`.

### Task 11: Serve and render public inline artifacts

**Files:** Create `share/inline_artifact_views.py`, `tests/test_inline_artifact_public_routes.py`, `tests/test_inline_artifact_public_proxy.py`, and `share-session/inlineAdapter.js`, `inlineAdapter.test.js`. Modify public share components/shims, urls, consumer, and frame host configuration. Extend `tests/test_share_host_gate.py`, `tests/test_share_public_routes.py`, `frontend/src/share-session/shims/shareApi.test.js`.

**Interfaces:**
- `GET /share/<token>/api/inline-artifacts/` returns the filtered manifest and schedules authorized legacy initialization/reconciliation.
- `POST /share/<token>/api/inline-artifacts/<source-session-id>/<artifact-id>/retry/` retries current allowed failures without modifying owner data or successful snapshots.
- `GET|HEAD /share/<token>/inline-artifacts/<source-session-id>/<artifact-id>/<asset-path>` serves ready confined copied files, with read-only data directory listing.
- `POST /share/<token>/api/inline-artifacts/<source-session-id>/<artifact-id>/proxy/` uses existing artifact_proxy with server-enforced bookmark grants or an empty allowlist.
- `makeShareInlineAdapter({api, tokenPath, store}) -> adapter` uses Task 4 contract, `mode: 'share'`, and token-only URLs.
- ShareSessionApp creates/provides this adapter and runtime only when public metadata `inline_artifacts_supported` is true. Native-root shares skip manifest fetch/initialization and render ordinary Markdown.
- Its `probe()` uses HEAD on that token-scoped document route and preserves authorization errors; loading then uses the same bound shim-ready acknowledgement as private frames.
- Extend shareApi with `fetchInlineManifest()` and `retryInlineArtifact(sourceSessionId, artifactId)`; connectShareLive accepts `onInlineArtifacts(manifest)`.

- [ ] **Step 1: Write the route authorization matrix and public adapter tests.** Check valid/password/missing grant/expired/revoked/deleted/wrong-host/wrong-kind/disabled/excluded-session/excluded-publication/pending/error states across manifest, HTML, assets, listing, retry, and proxy. Verify traversal and sibling routes cannot reach another exported folder. HTML gets CSP/shim; CSS/JS/data keep content types and no-cache. HEAD has no body. PUT/DELETE and public data writes fail.

```python
async def test_disabled_share_refuses_old_export_routes(public_case):
    await public_case.disable_inclusion()
    for route in public_case.file_listing_retry_proxy_routes():
        response = await public_case.request(route)
        assert response.status_code in (403, 404)
    manifest = await public_case.manifest()
    assert manifest['enabled'] is False
    assert not public_case.has_runnable_descriptor(manifest)
```

  - Unbookmarked proxy requests have no grants; bookmarked ones read current persisted host grants server-side.
  - Retry cannot refresh successful snapshot artifacts, recapture a newer tag, or restore excluded access. Child IDs fail file/listing/retry/proxy requests even with include_subagents true.
  - Public metadata/WS never exposes absolute paths, bookmark-private details, copy IDs, or private artifact URLs.
  - Public runtime retains the main-session iframe through row virtualization and child drawer changes. Child text receives no inline context and creates no frame or broker.
  - Changed code revision reloads once even with unchanged tag; data revision never reloads.
  - Deliver ready revision 11 before delayed pending revision 10, then disabled revision 12 before delayed ready revision 11. HTTP/WS interleaving cannot restore older readiness or access.
  - Live disable closes on its delivered update; snapshot disable closes on metadata reconciliation/reload.
- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_public_routes.py tests/test_inline_artifact_public_proxy.py tests/test_share_host_gate.py tests/test_share_public_routes.py -q` and `cd frontend && node --test src/share-session/inlineAdapter.test.js src/share-session/shims/shareApi.test.js src/inline-artifacts/runtime.test.js`.** Expect missing endpoint/adapter behavior failures.
- [ ] **Step 3: Implement explicit token routes before the artifact-share catch-all.** Reuse resolve_or_404 on every request, including proxy and retry. Require source_session_id equals share.session_id and its type is regular session; descendant permission never grants inline access. Determine export from current server metadata only. Resolve any bookmark from the selected source entry, never a caller bookmark ID. Configure public broker document URLs and data listing using existing request headers. Mount one stable FrameHost and InlineArtifactRuntimeHost in ShareSessionApp outside its virtualized root list. Suppress main-session frames behind child drawers; do not provide inline context to SharedSubagentView text. Reconcile manifests at boot, live reconnect, snapshot window focus, and explicit widget Reload; abort old fetches on view teardown. Keep share shims free of private WebSocket/router/auth imports and dispose live connections on unmount. Do not add a snapshot streaming channel.
- [ ] **Step 4: Run focused tests and `cd frontend && npm run build`.** Expect all bundles to build, including standalone share and shim bundles. Inspect the share bundle module graph for private application imports.
- [ ] **Step 5: Commit `feat(sharing): render inline artifacts in public conversations`.

### Task 12: Update agent instructions and validate the integrated feature

**Files:** Modify `src/twicc/agent/system_prompt.py`, bundled twicc-share SKILL.md if not already updated. Create `tests/test_inline_artifact_system_prompt.py` and `docs/superpowers/plans/2026-10-08-inline-html-artifacts-validation.md`. Add focused regressions to owning task test files when integration reveals a gap.

**Interfaces:** Both provider addenda contain the exact tag syntax, folder identity/correction behavior, examples, optional data guidance, and accepted memory-state loss. No new publishing tool or persistence callback is introduced.

- [ ] **Step 1: Write addendum tests.** Assert single-file and folder-with-assets examples, double-quoted tag syntax, same-ID correction/reinsertion, distinct IDs for independent widgets, height limits, optional data API, main-session-only authoring, no delegation to native subagents, and no required autosave/Submit to discussion. Test Claude Code and Codex addendum generation for newly created sessions. Preserve existing per-session frozen addenda and Codex resume behavior; do not retrofit old rollouts or replace user-owned instructions. Existing main sessions can publish when given the tag instructions in discussion.

```python
def test_both_provider_addenda_document_inline_publication(provider_addendum):
    text = provider_addendum()
    assert '<twicc:inline-artifact' in text
    assert 'inline-artifacts/' in text
    assert 'window.twicc.data' in text
    assert 'Use the same ID and folder for corrections, then insert the tag again.' in text
    assert 'Use window.twicc.data only when saved data serves the widget.' in text
    assert 'Do not save every interface change automatically.' in text
    assert 'Only the main session agent creates and publishes inline artifacts.' in text
    assert 'Do not delegate inline artifact generation or publication to native subagents.' in text
    assert 'If you are a native subagent, do not create or publish inline artifacts.' in text
```

Use these exact guidance sentences in the shared addendum, alongside the complete examples.
- [ ] **Step 2: Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact_system_prompt.py tests/test_system_prompt_spawned_by.py -q`.** Expect missing authoring guidance failures.
- [ ] **Step 3: Write the addendum and validation checklist.** Keep shared instructions in the existing artifact section. The checklist records browser/OS, actual URLs, commands, provider, observed load counters, screenshots where useful, and pass/fail per scenario. Do not record production share tokens or passwords in the repository.
- [ ] **Step 4: Run backend and frontend integration checks.**

```bash
TWICC_DATA_DIR=$PWD uv run pytest tests/test_inline_artifact*.py tests/test_share_*.py tests/test_artifact_data*.py tests/test_artifact_broker_html.py tests/test_compute_apply_signals.py tests/test_codex_recompute_persistence.py -q
cd frontend && npm test
cd frontend && npm run build
uvx ruff check src/twicc/inline_artifacts src/twicc/share src/twicc/core/services/share_mutation.py src/twicc/core/services/share_agent_gate.py src/twicc/agent/system_prompt.py tests/test_inline_artifact*.py
git diff --check
```

Execute each command from its stated directory, not by copying this block as a persistent shell sequence.
For a worktree, prefix commands with its explicit `cd` and keep `TWICC_DATA_DIR=$PWD` for Python.
Expect passing suites, successful standalone bundles, no new lint findings in changed code, and a clean diff check.
If unrelated baseline failures exist, reproduce them against unchanged code and report them; do not label them passing.

- [ ] **Step 5: Perform real-browser lifecycle checks on an authorized test instance.** Use purpose-built available browser tools. Request server startup only if no authorized instance exists; use devctl only after user authorization. Do not manually install dependencies or apply migrations to bring servers up.

| Scenario | Expected observation |
| --- | --- |
| Publish single file and folder page with both providers | Exact inline placement; JS/CSS/assets/data load |
| Type text, adjust range, switch widget tab, then scroll beyond unload buffer | Same input values, page position, and document load counter after return, without data API |
| Switch cached sessions, hide chat pane, move/resize docks | Same iframe node and browsing state |
| Fullscreen, focus an iframe input, remove source row, press Escape | Bound iframe RPC closes the same frame; returns to retained inline height or hides if row absent |
| Focused widget scrolls out; composer, header, and overlay interaction | Focus returns safely; no iframe intercepts hidden regions |
| Consent arrives while row/session hidden | Prompt waits; returns with same broker when widget becomes visible |
| Correct same ID while old row is absent, then load the old range | Only latest widget; one document reload; scroll anchor preserved |
| Modify code without tag; save data; open independent Artifacts viewer | No inline reload; relevant tab reload only; independent memory and shared data |
| Reload, browser refresh, cache eviction, archive teardown | Accepted memory loss; saved data survives; listeners/prompts cleaned |
| Autoheight and viewport-dependent layout, narrow/mobile pane | Height stays 160–900; no loop or composer overlap |
| Live share new tag, data-only update, no-viewer interval, reconnect | Filtered pending/ready; code reload once; data refresh without reload |
| Snapshot Push update without new tag | Old open widget reloads once on metadata reconciliation |
| Child emits a tag, then Include subagents changes | Tag remains ordinary text; main inline manifest/copy/revision stays unchanged |
| Failed legacy initial export, then fix source and Reload | Only failed copy retries; successful widgets and placements stay frozen |
| Disable inclusion, change password, revoke, expire | Route access denied; live/snapshot UI follows specified notification timing |
| Private/public child view opens/closes with artifact-like text | Main frames hide/return unchanged; no child placeholder, frame, probe, or broker |
| Direct shared HTML/assets/data/proxy on production share bundle | Host/password gates apply; public data writes fail; no private paths |

Browser checks are required because node:test cannot prove iframe survival through DOM movement or actual focus behavior.
Do not substitute string/template assertions for these lifecycle observations.

- [ ] **Step 6: Commit `feat(artifacts): document and validate inline artifact authoring`.** Include validation results and limitations. Remind the user to install the declared Python dependency, migrate, and restart their running backend through devctl. Implementation does not authorize deployment, remote push, or CHANGELOG edits.

## Spec coverage map

| Spec sections | Owning tasks |
| --- | --- |
| 4–6: identity, tag, canonical index | 1–3 |
| 7: typed rendering and unloaded-range supersession | 2, 7 |
| 8–10: retention, geometry, height, fullscreen, correction reload | 4–7 |
| 11: optional private saved data | 3, 5, 7, 12 |
| 12: sharing option and filtered/captured selection | 8 |
| 13: copied export confinement and limits | 3, 9 |
| 14: snapshot/live timing, failure, recovery, data refresh | 9–11 |
| 15: token routes, broker, public runtime | 10–11 |
| 16: shared system-prompt addendum | 12 |
| 17–18: non-goals and acceptance checks | Global Constraints and 12 |

## Adversarial review record

Two independent internal reviewers assess this plan across three rounds and a final correction verification.
One reviews provider grammar, source mapping, retained frames, brokers, geometry, and load errors.
The other reviews confined exports, selection, atomic mutations, authorization, recovery, and public adapters.
Both final verdicts are **READY** for the plan and the main-session-only spec amendment.

The review resolves these material gaps:

- Preserve canonical Codex Text-block identities and complete-source grammar through joined and segmented rendering.
- Consume confined file handles directly when serving responses, without reopening paths.
- Forward Escape from the focused iframe through its bound broker connection.
- Detect document load failures through HEAD probes and bound shim readiness, with stale-generation guards.
- Gate snapshot capture on main-session compute readiness without capturing empty historical catalogs.
- Recover interrupted legacy initialization while preserving successful copies and captured placements.
- Remove deleted-share copies after read leases finish, including orphan roots after a crash.
- Reject delayed HTTP or WebSocket manifests with older revisions.
- Bump the bundled plugin version with its new share-option guidance.

During review, the user excludes native subagents from inline authoring and runtime support.
The spec and plan now apply inline behavior only to regular main sessions.
Native subagent tags remain ordinary Markdown; Include subagents changes only transcript visibility.
Direct native subagent shares stay usable without inline initialization, runtime, or export access.

Document checks validate task structure, links, fences, integration paths, and the main-session-only contract.
No application code, runtime checks, or implementation tests are executed during plan writing.

## Handoff

This plan defines implementation work only. Its review does not authorize implementation.
After user review, choose subagent-driven execution or native execution before changing application code.
Subagent-driven execution is recommended because the twelve tasks span provider normalization, retained DOM ownership, and public file access.
