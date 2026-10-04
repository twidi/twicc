# Composer Attachments for Any File Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept every file in the web composer, then send eligible files natively and preserve other files as session artifacts.

**Architecture:** Upload files immediately through tus into a backend staging store. Plan native eligibility at send time. Commit files after the session ID exists, then render ordered provider content and extract its manifest during ingestion.

**Tech Stack:** Django 6 ASGI, Python ≥ 3.13, Pillow, orjson, Vue 3, Pinia, IndexedDB, tus-js-client, pytest, node:test.

**Spec:** [Composer attachments design](2026-10-03-composer-attachments-any-file-design.md). Read the whole spec before execution.

## Global Constraints

- Accept **any file of any size** from the web composer. Add no file-count, file-size, or draft-volume refusal.
- Preserve strict add order. Unsupported files and surplus native candidates become artifacts; later candidates can still become native.
- No user control over "native vs file". No toggle, no per-chip indicator, no file-mode reason in the manifest.
- Claude long edge: **2000 px**. Codex long edge: **2576 px**.
- PDF native threshold: **512 KiB**. Text native threshold: **50 KiB**.
- Native volume budget: **16 MiB**; Claude `third_party`: **12 MiB**.
- Claude native count: **580** with `context_1m`, otherwise **80**. Codex native count: **1,500**.
- Claude per-image base64 limits: **10,485,760** first party; **5,242,880** third party. Codex has no per-image byte limit.
- Kind detection reads at most the **first 64 KiB**. Specialized image-header probes follow spec §6.4.
- Image decode limit: **100 megapixels**. Animated images become files. Claude CMYK JPEGs become files; Codex accepts them.
- No weight-driven compression. Re-encode only after resizing; preserve the family and exact encoding settings in spec §6.4.
- Claude hybrid sends only eligible images natively. PDF and text always become files.
- Ephemeral sends accept only native entries. Reject the whole plan before promotion when any entry needs artifacts.
- Send stays disabled while any chip is not `ready`. Do not queue a browser send behind uploads.
- Keep legacy `images` / `documents`, CLI `--attach`, MCP, and backend peer sends unchanged in phase 1.
- Preserve legacy failed-send Retry for **7 days**. Web peer delivery uses the new composer pipeline.
- Manifest numbering restarts at 1 per message. Native blocks follow manifest order. Claude user text stays last.
- No system-prompt addendum change. No PDF parsing, page count, or encryption detection.
- No new package dependency is planned. Use the existing Pillow and tus implementations.
- All code, UI copy, comments, and documents use English. Use `NamedTuple` and `orjson` in backend code.
- Stay on the current branch. Create no branch or worktree without an explicit user request.
- Preserve existing uncommitted files. Stage only files changed by each task.
- Do not install packages, apply migrations, or restart servers without a user request.

## Review Focus

1. Retry overlaps finalization or release: stale uploads never recreate or overwrite a removed/reset staging entry. Tests: Tasks 2–3.
2. A disconnect or lost ack follows delivery: server cleanup still runs, while Edit shows unavailable entries. Tests: Tasks 13 and 15.
3. A pasted manifest resembles protocol content: extraction preserves user text, tool content, and invalid blocks. Tests: Tasks 6 and 8.
4. Codex binds a draft while more sends arrive: all sends and soft controls keep their order; force kill remains immediate. Tests: Tasks 9 and 11.
5. Reload, two tabs, or migration races change upload ownership: the current attempt alone controls chip readiness and removal stays permanent. Tests: Tasks 14 and 17.

## Scope and Interpretation

This is one connected delivery pipeline. Its tasks share staging identity, ordered content, and delivery semantics.
Separate subsystem plans would duplicate these contracts. Keep one plan, with separately reviewable tasks.

- Spec §10.1 governs malformed blocks: **leave them untouched and set no metadata**. The conflicting §15 phrase is not implemented.
- Preserve the existing new-session text requirement. Attachment-only sends remain supported for existing sessions.
- Do not alter existing matching semantics for equal text. Attachment-only matching uses the new total count.
- Exact protocol-shaped pasted manifests are indistinguishable from generated manifests when all §10.1 checks pass.
  Preserve invalid or misplaced pasted blocks; do not promise provenance authentication or add a new token.
- Force kill bypasses the lane and immediately targets the current process. It does not cancel planning or revoke an admitted send.
  A pending send can later start a process. A send-cancellation protocol requires a separate design decision.
- Binding preserves unsent records added after the first send. Move them before forgetting the old draft ID.
  This refines §6.1.4 cleanup: the sent refs are already forgotten; binding must not discard the next composer message.
- Validate the hybrid `!` behavior during Task 12. Reject it only if the TUI enters bash mode.
- Check Claude document titles at 200 characters during final authorized provider validation. Do not update the SDK or runtime.

## File Structure and Contracts

Existing file locations and function names below are checked against the current checkout.
Line numbers are omitted because preceding tasks change them. Use the named symbols as edit anchors.

| New file | Responsibility |
|---|---|
| `src/twicc/core/services/attachments/types.py` | Immutable staging, plan, manifest, prepared-content contracts |
| `src/twicc/core/services/attachments/staging.py` | Validated paths, markers, ready loading, content location, promotion |
| `src/twicc/core/services/attachments/lifecycle.py` | Shared creation/upload locking, settle, release, status, touch |
| `src/twicc/core/services/attachments/views.py` | Password-protected staging REST routes |
| `src/twicc/core/services/attachments/images.py` | Bounded image probes and native normalization |
| `src/twicc/core/services/attachments/planner.py` | Kind detection, ordered first-fit decisions, ranks, business errors |
| `src/twicc/core/services/attachments/target.py` | Effective settings, live context, platform and pending-hybrid resolution |
| `src/twicc/core/services/attachments/manifest.py` | Exact shared builder and parser |
| `src/twicc/core/services/attachments/committer.py` | Off-loop preparation, bounded finish, cleanup of pre-copies |
| `src/twicc/composer_attachments_cleanup_task.py` | Daily staging and pre-copy retention |
| `src/twicc/agent/send_lanes.py` | Refcounted send locks and draft-to-canonical aliases |
| `frontend/src/utils/composerAttachments.js` | Ref conversion, display kind, state mapping, status/touch HTTP helpers |
| `frontend/src/utils/attachmentMigration.js` | Legacy media decoding and idempotent migration decisions |
| `frontend/src/utils/attachmentStrip.js` | Pure history-strip construction and attachment-count matching |
| `frontend/src/components/media/AttachmentStrip.vue` | Ordered history chips and image thumbnails, including share mode |

Create `attachments/__init__.py` with the first backend task. Keep it free of imports that create upload/view cycles.
Keep the existing media components compatible with legacy callers. Add optional attachment fields rather than replacing their contract.

### Shared backend types

Define these `NamedTuple` contracts in Task 1. Import them instead of duplicating dictionaries between modules.
Use `Path` for filesystem paths. Use literal strings for `kind`, `mode`, `provider`, and `platform`.

```python
AttachmentRef(bucket: str, id: str)
PlanTarget(provider: str, hybrid: bool, ephemeral: bool, model: str,
           context_1m: bool, platform: str)
PromotedEntry(session_id: str, final_path: Path, final_name: str,
              kind: str, original_name: str, size: int)
StagedEntry(ref: AttachmentRef, filename: str, size: int,
            path: Path | None, promoted: PromotedEntry | None)
NativePart(kind: str, media_type: str, data: bytes | str)
PlannedEntry(ref: AttachmentRef, n: int, name: str, kind: str,
             rank: int, of: int, mode: str, source: StagedEntry,
             native: NativePart | None)
AttachmentPlan(target: PlanTarget, entries: tuple[PlannedEntry, ...])
ManifestEntry(n: int, name: str, kind: str, rank: int, of: int,
              mode: str, artifact_name: str | None)
AttachmentManifest(owner: str, directory: Path | None,
                   entries: tuple[ManifestEntry, ...])
PreparedEntry(entry: PlannedEntry, precopy: Path | None)
PreparedAttachments(plan: AttachmentPlan, entries: tuple[PreparedEntry, ...])
AttachmentContent(native_parts: tuple[NativePart, ...],
                  manifest: AttachmentManifest, user_text: str)
ParsedManifest(entries: tuple[ManifestEntry, ...], directory: str | None,
               hybrid_paths: tuple[str | None, ...], hybrid: bool)
UserTextSlot(parent: dict, key: str, format: str)
```

`mode` is `inline` or `file`. `kind` is `image`, `PDF`, `text`, `video`, `audio`, or `other`.
`NativePart.data` is normalized binary data for images/PDFs, or decoded text for text documents.
`native_parts` includes only inline entries, in entry order. Manifest file names are final names; inline names are staging names.
`directory` is absent when no file entry exists. Stored ingestion metadata contains no absolute path.
`UserTextSlot.format` is `claude`, `codex_response`, `codex_canonical`, or `hybrid`.

### Verification and commits

- Backend commands below run from the implementation checkout with `TWICC_DATA_DIR=$PWD`.
- Tests use `tmp_path`, isolated upload metadata, fake agents, and mocked provider clients. Never use real provider homes in unit tests.
- Frontend tests use `node:test`. Inject storage, fetch, clock, UUID, and upload dependencies where browser APIs are unavailable.
- Every task first runs its new focused test and verifies an assertion failure or missing symbol.
- Every task then runs that focused suite and verifies exit code 0.
- Each commit uses the specified Conventional Commit subject, a descriptive body, and the required current-model co-author trailer.
- Resolve the exact model at commit time. Do not hardcode a model name from this plan.
- Do not commit this planning document unless the user requests a commit.

---

### Task 1: Define staging identity, names, and durable markers

**Files:** Create `src/twicc/core/services/attachments/__init__.py`, `types.py`, and `staging.py` in that package.
Modify `src/twicc/paths.py`, `.gitignore`, `AGENTS.md`, and `CLAUDE.md`.
Create `tests/test_composer_attachments_staging.py`.

**Interfaces:** Produces the shared types above; `get_composer_attachments_dir() -> Path`;
`validate_ref(raw: object) -> AttachmentRef`; `normalize_filename(name: str, max_bytes: int) -> str`;
`load_entry(ref: AttachmentRef) -> StagedEntry`; `on_upload_completed(meta: dict, final_path: str | Path) -> None`.

- [ ] **Step 1: Write failing staging tests.** Create ready fixtures with one file and `ready.json`, using `orjson`.

```python
assert normalize_filename("../a\n@.txt", 255) == ".._a_@.txt"
assert normalize_filename(" . ", 255) == "attachment"
assert normalize_filename(".twicc-upload-x", 255) == "_.twicc-upload-x"
assert load_entry(ref).size == source.stat().st_size
```

Parameterize `/`, `\\`, NUL, U+0000–U+001F, U+007F, U+0085, U+2028, and U+2029.
Check UTF-8 byte truncation before the extension, whitespace, empty names, and `..`.
Reject invalid buckets and noncanonical UUID attachment IDs. Ignore temporary and placeholder files.
Verify size mismatch means not ready; missing promoted targets mean `attachment_missing`.
Verify completion uses the final renamed basename, remains idempotent, and never creates a removed entry.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachments_staging.py -q`; verify failure.
- [ ] **Step 3: Implement staging contracts and atomic marker writes.** Fsync durable markers; validate real paths before content access.
  Add `/composer-attachments` beside the existing runtime directories in `.gitignore`.
  Add `composer-attachments/` to both instruction files' Data Directory contents lists.
- [ ] **Step 4: Run the same suite**; verify all assertions pass.
- [ ] **Step 5: Commit** `feat(attachments): add composer staging identity and markers`.

### Task 2: Add the composer tus origin and shared settle operation

**Files:** Modify `src/twicc/uploads/views.py`, `store.py`; create `attachments/lifecycle.py`.
Modify `tests/test_uploads_create.py`, `tests/test_uploads_finalize.py`; create `tests/test_composer_attachments_uploads.py`.

**Interfaces:** Consumes Task 1. Produces `settle_entry_uploads(ref: AttachmentRef) -> None` as an async function.
The caller holds the shared upload creation lock. Each attempt settles under its existing `_run_locked` upload lock.
Expose one `composer_creation_guard()` async context manager from `lifecycle.py`, backed by that existing creation lock.

- [ ] **Step 1: Write failing creation/finalization tests.** Define local fixtures before endpoint assertions; do not rely on fixtures local to another test module.
Use `AsyncClient`, password disabled through `settings`, an isolated data-dir fixture, captured broadcasts,
and cleared loop-scoped upload locks. Copy these fixture behaviors from `tests/test_uploads_create.py` without importing that test module.
Provide a transaction-enabled Django test module and use one `asyncio.run` per complete async scenario.
Define async helper `post_composer(client, ref, *, filename="x.bin", size=0, client_id="attempt", url="/api/uploads/")`.
It calls `client.post(url, data=orjson.dumps(body), content_type="application/json")` with body
`{filename, size, client_id, fingerprint: "test:0", origin: {panel: "composer", key: f"{ref.bucket}/{ref.id}"}}`.
For scoped-route refusal, create the matching project fixture and pass its uploads URL.
For release rejection in this task, directly create `.released/<id>` in the isolated data dir; the DELETE API comes in Task 3.
Use injected recovery operations for finalization/cancellation assertions.

```python
async def assert_creation_contract(client, ref, project_upload_url, release_tombstone):
    first = await post_composer(client, ref, client_id="same-attempt")
    assert first.status_code == 201
    repeat = await post_composer(client, ref, client_id="same-attempt")
    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    scoped = await post_composer(client, ref, url=project_upload_url)
    assert scoped.status_code == 400
    release_tombstone.parent.mkdir(parents=True, exist_ok=True)
    release_tombstone.touch()
    refused = await post_composer(client, ref, client_id="different-attempt")
    assert refused.status_code == 410
```

Call that coroutine from `test_composer_creation_contract` with the local fixtures and `asyncio.run`.
`release_tombstone` is `<isolated data>/composer-attachments/<bucket>/.released/<id>`.

Cover creation without `target_dir`/`root`, composer scope revalidation, and unchanged Files/Artifacts filename refusal.
Assert the exact creation order from spec §6.1.1, including the idempotence lookup before the release-tombstone check.
Test waited-for finalization, crashed `finalizing` recovery, recovery returning `active`, and cancel-write failure.
On cancel-write failure, creation returns 500 and retains all entry files and markers.
Verify every completion path writes `ready.json` before persisting/broadcasting `completed`.
Inject completion-hook failure: retain `finalizing`, then recover successfully using the explicitly found final path.
Inject disk-full creation: return 507 with no live upload. Test zero-byte completion.
Verify upload progress refreshes the existing staging entry mtime without creating absent directories.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachments_uploads.py -q`; verify failure.
- [ ] **Step 3: Implement the composer branch in `_parse_creation_body`, `_create`, and `_revalidate_target`.**
  Add `composer` to `ORIGIN_PANELS`; calculate the target server-side.
  Settle other attempts, reset markers, then run directory-dependent target checks.
  Preserve target checks, client idempotence, and resume behavior for existing origins.
  Call the hook in `finalize_files`, `_recover_committed`, and `_recover_uncommitted` before the completed write.
  Refresh staging entry mtime on creation and accepted transfer progress without changing committed ownership.
  Correct `cancel_upload`'s docstring to include all non-terminal states.
  Avoid static `staging → uploads.views → staging` imports; put coroutine coordination in `lifecycle.py` with local imports where needed.
- [ ] **Step 4: Run** the new suite plus `tests/test_uploads_create.py` and `tests/test_uploads_finalize.py`; verify pass.
- [ ] **Step 5: Commit** `feat(uploads): support composer staging uploads`.

### Task 3: Add staging endpoints, release, heartbeat, and retention

**Files:** Create `attachments/views.py`, `src/twicc/composer_attachments_cleanup_task.py`.
Modify `attachments/lifecycle.py`, `src/twicc/urls.py`, `src/twicc/cli/run.py`.
Create `tests/test_composer_attachments_api.py`, `tests/test_composer_attachments_cleanup.py`.

**Interfaces:** Produces async `release_refs(refs: tuple[AttachmentRef, ...]) -> None`;
`status_refs(refs: tuple[AttachmentRef, ...]) -> list[dict]`; `touch_refs(refs, *, holder: str) -> None`;
`start_composer_attachments_cleanup_task(stop_event: asyncio.Event) -> None`.
Routes and request bodies match spec §6.1.2, including `GET <bucket>/<id>/content` without an added trailing slash.
Define the status response as `{statuses: [{bucket, id, state, client_id?, offset?}]}`, preserving requested order.
`client_id` and `offset` appear only for uploading entries. Touch returns 204; invalid body/ref/holder returns 400.
Frontend `statusRefs(refs)` consumes that exact response and returns its `statuses` array.

- [ ] **Step 1: Write failing API and clock-controlled cleanup tests.**

```python
assert status(ref_with_promotion)["state"] == "promoted"
assert delete_entry(ref).status_code == 204
assert delete_entry(ref).status_code == 204
assert promoted_artifact.read_bytes() == original_bytes
assert create_after_delete(ref).status_code == 410
```

Status precedence is promoted-existing, ready, live-uploading, missing; a missing promoted target wins over old `ready.json`.
Return uploading `client_id` and offset. Reject invalid refs and holder values.
Test nosniff; inline raster/PDF/UTF-8 plain text; attachment/octet-stream for SVG, HTML, JavaScript, and other active content.
For a large sparse fixture, assert `response.is_async` and that the first chunk arrives before the file is fully read.
Instrument the source reads: each read is at most 64 KiB; response close/disconnect closes the file.
Refuse symlink escapes from staging and from the tombstone owner's attachments directory. Reuse normal `/api/` password protection.
Release settles every attempt under creation/upload locks and writes `.released/<id>` even when the entry is absent.
A failed cancellation returns 500 without removal. Barrier-controlled creation/release tests prove neither order recreates a released entry.
Touch `draft` removes `committed.json`; touch `snapshot` preserves it; absent entries stay absent.
Test exact 7-day committed and 30-day draft expiry boundaries, recent touch, and active/finalizing uploads retained without cancellation.
Expire release tombstones after 24 h, then remove empty directories. Remove only top-level artifacts pre-copies older than 24 h.
Respect `SESSION_DIRS_CLEANUP_ENABLED` for the artifacts sweep, including shared worktree symlinks.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachments_api.py tests/test_composer_attachments_cleanup.py -q`; verify failure.
- [ ] **Step 3: Implement the endpoints and daily cleanup task.** Use `StreamingHttpResponse` with an async iterator, 64 KiB reads through `asyncio.to_thread`, and `finally` file closure.
  Do not use a synchronous `FileResponse` iterator under ASGI; Django materializes that iterator before delivery.
  Use bounded kind detection from Task 4 when available.
  Initially isolate content-type detection behind `content_media_type(entry: StagedEntry) -> tuple[str, bool]` in staging.
  Task 4 supplies its bounded detector; never infer safe inline content solely from the filename.
  Register startup and shutdown beside `start_upload_cleanup_task`; acquire locks before re-reading cleanup candidates.
- [ ] **Step 4: Run the same suites** plus `tests/test_upload_cleanup_task.py`; verify pass.
- [ ] **Step 5: Commit** `feat(attachments): add staging lifecycle endpoints and retention`.

### Task 4: Implement bounded kind detection and image normalization

**Files:** Create `attachments/images.py`; start `attachments/planner.py` with detection functions.
Modify `attachments/staging.py`'s `content_media_type` integration.
Create `tests/test_composer_attachment_images.py`, `tests/test_composer_attachment_detection.py`.

**Interfaces:** Produces `detect_kind(path: Path, name: str, size: int) -> str` in `planner.py`;
`normalize_image(path: Path, *, target: PlanTarget, remaining_budget: int) -> NativePart | None` in `images.py`.
`None` means file fallback. Size, header, count, and budget checks precede full reads/decode.

- [ ] **Step 1: Write failing detection and normalization tests.** Use small real image fixtures and tracked reads/probes.

```python
assert detect_kind(empty_path, "empty.txt", 0) == "other"
assert detect_kind(cut_utf8_path, "notes", 65537) == "text"
assert normalize_image(small_png, target=claude, remaining_budget=16 * 1024**2).data == small_png.read_bytes()
assert normalize_image(animated_gif, target=claude, remaining_budget=16 * 1024**2) is None
```

Cover NUL, invalid UTF-8 away from a cut boundary, misleading extensions, BMP, unsupported HEIC, and TIFF headers beyond 64 KiB.
Probe PNG IHDR and up to 1,000 chunk headers, acTL before IDAT, no IDAT, and oversized chunk lengths.
Probe VP8X/VP8/VP8L WebP without Pillow opening the whole file at the header stage.
Cover large ICC JPEG markers, MPO primary frame, GIF animation, CMYK by provider, and >100 MP before decode.
Within-dimension over-budget images must not decode or run the GIF animation probe.
Above-dimension images resize after EXIF transpose with Lanczos: PNG→PNG, WebP→lossless WebP, JPEG/MPO→JPEG quality 92, still GIF→PNG.
Verify 2000/2576 edges, aspect ratio, unchanged within-target bytes, and no byte-saving quality loop.
Verify Pillow open/verify/decode/encode errors become file fallback; post-resize byte limit remains enforced.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachment_images.py tests/test_composer_attachment_detection.py -q`; verify failure.
- [ ] **Step 3: Implement the detectors and normalizer.** Permit incomplete UTF-8 only at the bounded-head boundary.
  Detect standard raster types by magic bytes before Pillow fallback. Treat MPO as JPEG, not animation.
  Keep decoded/resized native bytes in memory; never read large PDF/text/other files to classify them.
- [ ] **Step 4: Run the same suites** and API content-header tests; verify pass.
- [ ] **Step 5: Commit** `feat(attachments): detect kinds and normalize native images`.

### Task 5: Resolve targets and build deterministic ordered plans

**Files:** Create `attachments/target.py`; complete `attachments/planner.py`.
Modify `src/twicc/providers/claude_code/helpers.py`, `src/twicc/providers/codex/helpers.py`.
Create `tests/test_composer_attachment_planner.py`, `tests/test_composer_attachment_target.py`.

**Interfaces:** Produces provider `ATTACHMENT_POLICY` constants, retaining `ATTACHMENT_SUPPORT`.
`plan_attachments(refs: tuple[AttachmentRef, ...], target: PlanTarget, *, text: str) -> AttachmentPlan` is synchronous.
`resolve_plan_target(*, provider: str, effective_settings: AgentSettings, directory: str, hybrid: bool,
ephemeral: bool, live_agent: BaseAgent | None) -> PlanTarget` is async. Call the planner through `asyncio.to_thread`.

- [ ] **Step 1: Write failing policy, first-fit, and target tests.** Stage mixed kinds through Task 1 fixtures.

```python
assert [e.mode for e in plan.entries] == ["inline", "file", "inline"]
assert [e.n for e in plan.entries] == [1, 2, 3]
assert [(e.rank, e.of) for e in plan.entries if e.kind == "image"] == [(1, 2), (2, 2)]
assert promoted_retry.entries[0].mode == "file"
```

Parameterize every target and all exact limits in Global Constraints; test threshold equality and one byte/item above.
Budget measures are `4 * ((n + 2) // 3)` for binary and UTF-8 byte length for text.
File entries consume no quota. Ranks include inline and file entries together. Manifest/user text consume no native budget.
Count exhaustion and oversized PDF/text candidates must not read beyond classification; native text must decode fully.
Missing directory → `attachment_missing`; existing unready entry → `attachment_not_ready`; missing promoted artifact → `attachment_missing`.
Ephemeral mixed plans raise `attachment_requires_artifacts`, listing names, with no markers or promotion.
Hybrid leading whitespace plus `/`, and Codex hardcoded commands, raise `attachments_with_command` before commit.
Target tests require both requested and live effective Claude settings to be 1M-capable and set to 1M.
Cover user settings and trusted project/local settings, resolved provider homes, and all six platform flags from spec §6.2.
Accept only trimmed case-insensitive `1`, `true`, `yes`, `on`; ignore inherited environment and untrusted project files.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachment_planner.py tests/test_composer_attachment_target.py -q`; verify failure.
- [ ] **Step 3: Implement target resolution and first-fit planning.** Preserve input order and deterministic ranks.
  Apply resolved/enforced settings before target construction; do not consume pending context or write committed markers here.
- [ ] **Step 4: Run the same suites**; verify pass.
- [ ] **Step 5: Commit** `feat(attachments): plan native delivery with provider quotas`.

### Task 6: Build and parse the exact attachment manifest

**Files:** Create `attachments/manifest.py`, `tests/test_composer_attachment_manifest.py`.

**Interfaces:** Produces `build_manifest(manifest: AttachmentManifest, *, hybrid_paths: tuple[str | None, ...] | None = None) -> str`;
`parse_manifest(text: str) -> ParsedManifest | None`. Hybrid paths align with every manifest entry; file positions contain `None`.

- [ ] **Step 1: Write failing literal-output and round-trip tests.** Use the exact intro and headers from spec §7.4.

```python
assert block.startswith("<twicc:attachments>\nFiles the user attached to this message, in the order they attached them.\n")
assert "1. a&amp;#64;&#64;&lt;&gt;.png (image 1 of 1, inline)" in block
assert parse_manifest(block).entries[0].name == "a&#64;@<>.png"
assert parse_manifest("<twicc:attachments>\nbad\n</twicc:attachments>") is None
```

Snapshot exact all-inline, all-file, mixed, and hybrid output, with conditional inline/file headers and one closing tag.
Use the exact SDK header and hybrid `@` header. No Markdown links, labels, or mode reasons.
Test greedy right-anchored names containing ` (`, `)`, `): @`, `&`, `<`, `>`, `@`, and literal `&#64;`.
Unescape only `amp`, `lt`, `gt`, `#64` in one pass. Reject wrong headers, empty bodies, extra lines, and invalid entry grammar.
Hybrid inline lines carry references; SDK/Codex lines do not. File entry names use the final artifact name.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachment_manifest.py -q`; verify failure.
- [ ] **Step 3: Implement one builder and one parser.** Keep protocol literals in this module; providers use these functions.
- [ ] **Step 4: Run the same suite**; verify pass.
- [ ] **Step 5: Commit** `feat(attachments): add shared manifest protocol`.

### Task 7: Prepare and commit artifacts without overwrite

**Files:** Create `attachments/committer.py`; modify `attachments/staging.py`.
Create `tests/test_composer_attachment_commit.py`.

**Interfaces:** Produces `prepare_attachments(plan: AttachmentPlan, *, session_id: str | None) -> PreparedAttachments`;
`finish_attachments(prepared: PreparedAttachments, *, session_id: str, text: str) -> AttachmentContent`;
`discard_prepared(prepared: PreparedAttachments) -> None` in `committer.py`.
`staging.promote_entry(entry: PreparedEntry, session_id: str) -> PromotedEntry` uses a prepared source.
Prepare and finish are synchronous; execute prepare off-loop before manager locks.

- [ ] **Step 1: Write failing promotion and crash-order tests.** Inject link/copy/marker failures and concurrent destination claims.

```python
assert content.manifest.entries[0].artifact_name == "notes (1).txt"
assert occupied_destination.read_bytes() == occupied_bytes
assert promoted_marker["original_name"] == "notes.txt"
assert other_session_file.stat().st_ino != original_session_file.stat().st_ino
```

Assert `committed.json={at}` for every native/file entry at commit, before promotion.
Staged files link to fsynced top-level `.twicc-upload-<uuid>.tmp` pre-copies; EXDEV/no-link errors fall back to copy.
Never rely only on `st_dev`. Tombstone copies to other or unknown sessions always use independent bytes/inodes.
Finish uses `candidate_names` and exclusive links, or exclusive create then replace only for accepted no-link errnos.
Never overwrite another writer. Write/fsync promotion tombstone before removing staged source and remaining pre-copy.
Crash before the tombstone keeps ready source; Retry may create a duplicate artifact under the next free name.
Same-session tombstones reuse existing files; other-session retries claim new names and update the tombstone.
Missing promoted sources raise `attachment_missing`; all other commit I/O failures raise `attachment_commit_failed`.
Every failure path removes pre-copies and preserves retryable staging. Native entries get no permanent artifact.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachment_commit.py -q`; verify failure.
- [ ] **Step 3: Implement prepare, finish, and promotion.** Finish performs only same-filesystem claims and small marker operations.
  Use `try/finally` ownership of pre-copies at every manager call site added later.
  Return raw user text plus structured manifest and ordered native parts; never fold context here.
- [ ] **Step 4: Run the same suite** plus manifest tests; verify pass.
- [ ] **Step 5: Commit** `feat(attachments): commit files with retry-safe promotion`.

### Task 8: Extract manifests only from validated user-message slots

**Files:** Modify `src/twicc/context_injection.py`, `src/twicc/providers/compute_base.py`,
`src/twicc/providers/claude_code/compute.py`, `src/twicc/providers/codex/compute.py`,
`src/twicc/providers/codex/canonical.py`, and `src/twicc/settings.py`.
Create `tests/test_attachment_manifest_ingestion.py`; extend `tests/test_attachment_only_messages.py` and `tests/test_codex_canonical.py`.

**Interfaces:** Produces provider hooks `user_text_slots(parsed: dict) -> tuple[UserTextSlot, ...]` and
`attachment_owners(parsed: dict, *, session_id: str) -> set[str]` on the compute classes.
`extract_attachments_block(slots: tuple[UserTextSlot, ...], accepted_owners: set[str], *, session_id: str) -> dict | None`
mutates only valid slots and returns `{owner, entries}`. `transform_inline` stores it as `twicc_attachments`.

- [ ] **Step 1: Write failing extraction/visibility tests.** Build manifests through Task 6, then wrap them in real provider record shapes.

```python
assert parsed["twicc_attachments"]["entries"][0]["artifact_name"] == "movie.mp4"
assert "<twicc:attachments>" not in cleaned_user_text
assert recomputed["twicc_attachments"] == parsed["twicc_attachments"]
assert invalid_record == original_invalid_record
```

Cover Claude SDK arrays, hybrid strings with/without text, and `queued_command` prompt arrays with `commandMode: "prompt"`.
Cover Codex `response_item` user content and `item_completed` canonical `UserMessage` entries.
Locate SDK/Codex blocks immediately after native slots; count images, documents, and `[Image could not be processed:` placeholders.
Count hybrid inline paths by accepted `/hybrid/<owner>/att_[0-9a-f]{12}(\.[A-Za-z0-9]+)?` suffixes.
Check file-directory suffix `/artifacts/<owner>/attachments/`; accept the Codex `forked_from_id` parent.
Test full recompute, live ingestion from stored line 1, and a live batch containing session_meta plus copied user records before flush.
Include a `parent_session_id` different from `forked_from_id`; only the actual fork owner is accepted.
Test a missing DB seed followed by session_meta and a copied user; the later metadata must replace the negative cache.
Use one compute instance for two IDs; neither session can accept the other's fork owner.
Owner precedence is file-directory owner, hybrid-path owner, then record session ID. Preserve a previously extracted key.
Reject count mismatch, invalid/missing headers, wrong owner, wrong reference suffix, misplaced block, and extra blocks without modifying content.
Also pin the accepted protocol limit: an exact all-file pasted block with a valid current owner passes the same checks.
Do not write a test that claims to distinguish it from generated content without additional protocol information.
Leave tool results, `Write` inputs, assistant, sidechain/meta, compacted, and `last-prompt` records unchanged.
Assert file-only/no-text records remain user messages in both providers; remove empty content entries after extraction.
Check title input, search input, first user message, and `/user-messages/` with actual transformed Claude/Codex/hybrid fixtures.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_attachment_manifest_ingestion.py -q`; verify failure.
- [ ] **Step 3: Implement extraction before `strip_context_blocks_in_place`.** Treat a slot as its whole string or array, not a recursive walk.
  At the start of Codex `transform_inline`, latch `agent_runs.fork_fields(parsed_json)` for every `session_meta` record.
  Latch before selecting user-text slots, including metadata in a live batch that has not reached the database.
  Reuse the per-session `_fork_fields` cache; `begin_session_compute` initializes it and `end_session_compute` clears it.
  `attachment_owners` returns the record session ID plus its cached `forked_from_id`, never `Session.parent_session_id`.
  For an uncached live session, inspect earlier line-1 metadata in `in_memory_items` before using `_live_fork_fields(session_id)`.
  An absent/negative live seed must not block a later metadata latch. A later session_meta replaces that session's cached value.
  Keep full-compute and live session IDs isolated; never reuse another session's fork owner.
  Serialize when extraction alone changes content. Never remove an existing `twicc_attachments` key during recompute.
  Update Claude `_has_visible_content` and Codex `user_message_is_visible` / `user_message_attachment_count`.
  Keep Codex `user_message_text` unchanged. Bump both compute versions from their current checkout values by one.
  Current inspection values are Claude **112** and Codex **53**; re-read before editing to preserve intervening changes.
- [ ] **Step 4: Run** ingestion, attachment-only, and canonical suites; verify pass.
- [ ] **Step 5: Commit** `feat(attachments): extract manifests into user-message metadata`.

### Task 9: Add refcounted send lanes and nonblocking control barriers

**Files:** Create `src/twicc/agent/send_lanes.py`; modify `src/twicc/asgi.py`, `src/twicc/agent/ephemeral.py`.
Create `tests/test_send_lanes.py`, `tests/test_attachment_ws_ordering.py`.

**Interfaces:** Produces `send_lane(session_id: str)` as an async context manager;
`bind_send_lane(draft_id: str, canonical_id: str) -> None`; `wait_for_send_barrier(session_id: str) -> None` as async.
All sends and barriers acquire a registry reference before waiting. Bind aliases the live entry until its total reference count reaches zero.
`send_lane` acquires/releases the lock and its reference; `wait_for_send_barrier` uses that same context then returns immediately.

- [ ] **Step 1: Write failing event-controlled concurrency tests.** Block a fake planner with events, not timing sleeps.

```python
assert delivered_order == ["first", "second", "third"]
assert unrelated_frame_handled.is_set()
assert outgoing_broadcast_sent.is_set()
assert "agent_starting" not in error_codes
assert force_kill_called.is_set()
```

Run sends from two consumer connections to one ID. Mix refs-based and legacy sends.
Bind draft X to canonical Y while X holds the lane; queue sends and barriers through both IDs.
Verify aliases stay until every waiter finishes; clear the registry after success, failure, and cancellation.
Soft stop and interrupt wait behind earlier sends without holding the lane during execution.
Force kill bypasses the lane and kills the current process during a soft-stop grace window or blocked planning.
Test that its process call does not wait for the lane; do not assert cancellation of the admitted send.
After planning resumes, a send may still start an agent under the existing process-only kill contract.
Test three rapid sends to a resumed session while the first plan waits; the consumer continues dispatching other frames.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_send_lanes.py tests/test_attachment_ws_ordering.py -q`; verify failure.
- [ ] **Step 3: Implement module-level shared lanes and detached send dispatch.**
  `_handle_send_message` performs shape validation inline, then uses `_spawn_detached` for the whole admitted send.
  Inside the lane preserve `check_readonly`, `reserve`, admitted body, and `finish` in `finally`.
  Add lane alias binding at Codex `ephemeral.bind`, before exposing the canonical binding to subsequent sends.
  Detached interrupt/soft-kill tasks first await the barrier; force kill takes no lane.
  Correct the outdated receive-loop ping comment; transport heartbeat behavior stays unchanged.
- [ ] **Step 4: Run** new concurrency suites plus `tests/test_ephemeral_ws.py` and `tests/test_ephemeral_lifecycle.py`; verify pass.
- [ ] **Step 5: Commit** `refactor(ws): serialize sends outside the receive loop`.

### Task 10: Carry structured content through Claude SDK delivery

**Files:** Modify `src/twicc/agent/base_agent.py`, `src/twicc/agent/base_manager.py`,
`src/twicc/providers/claude_code/agent/manager.py`, and `src/twicc/providers/claude_code/agent/agent.py`.
Create `tests/test_claude_attachment_delivery.py`; extend `tests/test_attachment_only_messages.py`.

**Interfaces:** Add optional keyword `content: AttachmentContent | None = None` to applicable start/send methods and forwarding helpers.
Add `attachment_plan: AttachmentPlan | None = None` to Claude manager `create_session` and `send_to_session`.
Keep all legacy positional parameters and attachment keywords compatible.
`ClaudeCodeAgent.start` additionally accepts `on_delivered=None` for hybrid-compatible manager forwarding; SDK start never invokes it.

- [ ] **Step 1: Write failing manager and prompt tests.** Use fake manager locks and mock SDK query calls.

```python
assert [part["type"] for part in prompt] == ["image", "document", "image", "text", "text"]
assert prompt[-1]["text"] == folded_user_text
assert prompt[-2]["text"].startswith("<twicc:attachments>\n")
assert document["title"] == original_name[:200]
```

Cover image/PDF/text mixed ordering, all-file/no-text follow-ups, and SDK slash commands with unchanged final user text.
Text native entries use Claude document text sources; PDFs use base64 sources; both get the filename title.
Assert `_reconcile_context` → `apply_pending_context` → `apply_goal_instruction` run only on the distinguished user-text part.
If the fold returns empty, omit that part. Never consume pending context during plan/commit/parking.
Assert prepare/finish happen before manager `_lock` on existing, resumed, and new-session paths.
A commit error propagates as `SendDeliveryError`, bypassing generic agent error swallowing.
Cover every content gate, parked legacy/new shapes, cron-restart and startup-settings branches without changing legacy behavior.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_claude_attachment_delivery.py -q`; verify failure.
- [ ] **Step 3: Thread structured content through managers and `_build_query_prompt`.**
  Commit before manager locks; store structured content for parked sends and update all `has_content` checks.
  Forward `content` and start kwargs through base manager admission helpers without changing default callers.
  Keep legacy SDK startup/send error behavior unless handling the new synchronous attachment delivery error.
- [ ] **Step 4: Run** the new suite plus attachment-only and Claude ephemeral-provider tests; verify pass.
- [ ] **Step 5: Commit** `feat(claude): deliver ordered composer attachment content`.

### Task 11: Commit Codex attachments before canonical session binding

**Files:** Modify `src/twicc/providers/codex/agent/manager.py`, `agent.py`, and base-manager forwarding from Task 10.
Create `tests/test_codex_attachment_delivery.py`.

**Interfaces:** Add `attachment_plan: AttachmentPlan | None = None` to Codex manager creation/send APIs.
Add optional keyword `prepared_attachments: PreparedAttachments | None = None` to `_start_agent_with_admission` and Codex `_create_agent`.
Add optional `initial_text: str = ""` to Codex `_create_agent`.
When prepared attachments exist, `_start_agent_with_admission` forwards `initial_text=text` explicitly to that factory.
Forward both preparation and text as factory kwargs, not through provider `agent.start` kwargs.
Set `agent._initial_content: AttachmentContent | None` after successful finish; `start` consumes it before fixed legacy start kwargs.
Extend `_build_turn_input(text, images, *, content=None)` and turn/steer forwarding to preserve ordered content.
Call `finish_attachments(prepared_attachments, session_id=canonical_id, text=initial_text)` inside `_create_agent`.
`start` consumes and clears `_initial_content` once; resumed/steered sends cannot replay that first content.
After starting, assert `_initial_content is None`; verify a later steer contains only its own new user text.

- [ ] **Step 1: Write failing Codex creation, resume, and steer tests.** Mock `thread_start`, work directories, and binding callbacks.

```python
assert events.index("prepare") < events.index("manager_lock")
assert events.index("thread_start") < events.index("finish") < events.index("notify_session_bound")
assert [type(item).__name__ for item in turn_input] == ["ImageInput", "ImageInput", "TextInput", "TextInput"]
assert closed_client_on_commit_failure is True
assert agent._initial_content.user_text == original_text
```

Existing-session commit precedes migration `gate_for(session_id)`, manager `_lock`, and hardcoded dispatch.
New-session prepare precedes locks; finish follows `thread_start` and work-dir creation inside `_create_agent`'s existing try.
A finish error closes the client, removes pre-copies, emits no binding, and leaves the browser draft ID unchanged.
Test Retry onto another canonical ID with a promoted tombstone: independent copied artifact, no overwrite.
Use data-URL `ImageInput`, never `LocalImageInput`. Send manifest then folded user text; preserve slash-command refusals.
Update all three `if not text and not images` gates. File-only follow-ups and mid-turn steers carry content.
Codex folding remains `_reconcile_context` → `apply_pending_context`, with no goal instruction.
Legacy document warnings remain only on legacy inputs. Couple binding with Task 9 lane aliases before notification.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_codex_attachment_delivery.py -q`; verify failure.
- [ ] **Step 3: Implement preparation and canonical finish at the specified lifecycle points.**
  Copy large files only outside locks; finish does no cross-filesystem copy.
  Preserve cleanup inside `_create_agent`'s exception path and cleanup if startup fails before that method finishes.
- [ ] **Step 4: Run** the new suite plus attachment-only and Codex ephemeral suites; verify pass.
- [ ] **Step 5: Commit** `feat(codex): commit attachments before session binding`.

### Task 12: Render hybrid manifests and refuse commands synchronously

**Files:** Modify `src/twicc/providers/claude_code/agent/hybrid/agent.py`, `src/twicc/asgi.py`, and target integration.
Create `tests/test_hybrid_attachment_delivery.py`.

**Interfaces:** Hybrid `start` and `send` accept optional `content`; `start` accepts optional `on_delivered`.
Add async `_materialize_content(content: AttachmentContent) -> str` beside the unchanged legacy materializer.
Store module-level pending-hybrid IDs in `asgi.py`; target resolution consumes their membership.

- [ ] **Step 1: Write failing hybrid rendering, callback, and command tests.** Mock paste and task creation.

```python
assert paste.startswith("user text\n\n<twicc:attachments>\n")
assert "inline): @" in paste
assert materialized_text_file.read_text() == native_text
assert first_paste_callback_calls == 1
```

Native files use current `att_<12 hex><ext>` naming. Materialize image, PDF, and text parts from an SDK-target race.
With no user text, paste only the block. Use hybrid header, real names, escaped `@`, and inline reference suffixes.
Do not add context folding to hybrid. Preserve the legacy hybrid text bug on legacy sends.
Assert leading-whitespace `/` with content raises `attachments_with_command` in both `start` and `send`, before scheduling or paste.
Register pending switch membership synchronously before spawning the switch; clear it in `finally` on success/failure.
Invoke `on_delivered` only after successful first paste; never on timeout, paste failure, adoption, or cancellation.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_hybrid_attachment_delivery.py -q`; verify failure.
- [ ] **Step 3: Implement the structured hybrid renderer and synchronous guards.**
  Use the shared builder after materialization; preserve the legacy `_materialize_attachments` path.
  Inspect bundled TUI input handling for `!`; add the same guard/test if it invokes bash mode.
  Pass pending-hybrid membership into existing-session targets before detached switch work can run.
- [ ] **Step 4: Run** the new suite and `tests/test_hybrid_pending_tool_input.py`; verify pass.
- [ ] **Step 5: Commit** `feat(hybrid): render composer manifests with file references`.

### Task 13: Integrate planning, admission errors, and delivery cleanup

**Files:** Modify `src/twicc/asgi.py`, `src/twicc/core/services/session_creation.py`,
`src/twicc/core/services/send_message.py`, `src/twicc/providers/claude_code/agent/manager.py`.
Create `tests/test_composer_attachment_send.py`; extend attachment-only and ephemeral creation tests.

**Interfaces:** Add `allow_attachments: bool = False` to public/private session creation services.
WS passes `True`; drop-request callers retain `False`. Add `validate_attachment_frame(payload: dict) -> tuple[AttachmentRef, ...]`
in `attachments/planner.py`. Add detached release callback factory `delivery_release(refs) -> Callable[[], None]` in lifecycle.
Parked sends carry `{text, images, documents, content, refs, on_delivered}` with optional new fields.

- [ ] **Step 1: Write failing full-send tests.** Fake planner, committer, manager, admission, and socket operations.

```python
assert error_frame["code"] == "invalid_attachments"
assert error_frame["request_id"] == request_id
assert ack_count_on_commit_failure == 0
assert released_refs_after_lost_ack == refs
assert pending_stashes_after_plan_error == {}
```

Reject malformed refs, noncanonical IDs, duplicates, and simultaneous legacy field/ref payloads inline.
Allow empty refs as no attachment content. Reject drop-request `attachments` with `invalid_attachments`.
Resolve/enforce settings before planning; plan off-loop. Existing `has_content` includes refs.
New-session planning precedes every `set_pending_*` stash; preserve the existing required-text validation.
Propagate all six new error codes with request ID, including ephemeral names and commit errors.
For direct delivered sends, attempt ack first, then schedule best-effort release despite a failed socket send.
No request ID means no ack but still release. Failure/parked/not-delivered sends do not release.
Closed sockets never prevent error logging, admission finish, or delivered cleanup.
Ensure the detached lane task survives disconnect; connection cleanup never cancels admitted send tasks.
Test all three parked delivery sites: startup-change `send_to_session`, `_restart_crons_for_session`, and `_apply_pending_settings`.
For `_start_agent` sites, look up the **new** agent after return: absent/DEAD keeps staging; SDK live releases.
Hybrid start defers release to successful first paste callback. Parked overwrite/drop releases nothing.
Release callbacks schedule outside manager `_lock`; release failure logs and leaves cleanup to retention.

- [ ] **Step 2: Run** `TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachment_send.py -q`; verify failure.
- [ ] **Step 3: Wire all send entry points and parked delivery callbacks.**
  Return creation planning failures as `SessionCreationResult` errors; existing-send failures use `SendDeliveryError`.
  Put release in delivered-path cleanup after the ack attempt, independently of socket state.
  Reject phase-1 refs on CLI/drop services rather than silently ignoring them.
- [ ] **Step 4: Run** all attachment delivery suites plus ephemeral WS/creation/lifecycle suites; verify pass.
- [ ] **Step 5: Commit** `feat(ws): admit composer refs and release delivered staging`.

### Task 14: Persist draft refs and map upload attempts to chip state

**Files:** Modify `frontend/src/utils/draftStorage.js`, `frontend/src/stores/data.js`, `frontend/src/main.js`,
`frontend/src/stores/uploads.js`, `frontend/src/utils/uploads/controller.js`.
Create `frontend/src/utils/composerAttachments.js`, `composerAttachments.test.js`;
extend `frontend/src/utils/uploads/controller.test.js`; create `frontend/src/utils/draftStorage.test.js`.

**Interfaces:** IndexedDB `draftAttachments` has keyPath `id` and nonunique `sessionId` index; bump **8 → 9** after re-reading current schema.
CRUD exports: `saveDraftAttachment(record)`, `getAllDraftAttachments()`, `getDraftAttachmentsBySession(sessionId)`, `deleteDraftAttachment(id)`.
Storage export `subscribeDraftStorageBlocked(callback) -> unsubscribe` reports blocked-upgrade state for bootstrap UI.
Extend `startUploads` with `onRejected?: ({client_id, filename, code}) => void`; composer fingerprint failures use code `file_unreadable`.
Existing callers omit it and retain their current toast behavior.
Record: `{id, sessionId, bucket, position, name, size, mimeType, kind}`.
Store APIs: `addAttachment(sessionId, file)`, `forgetAttachments(sessionId)`, `releaseAttachments(refs)`,
`retryAttachment(id)`, `reconcileAttachmentStatuses()`, `touchHeldAttachments()`.
Controller `startUploads` accepts optional caller `client_id`; composer defaults to standalone `/api` without target directory.
Runtime `File`, object URLs, display state, progress, current client ID, and upload key remain memory-only.

- [ ] **Step 1: Write failing pure state/controller tests with injected store/storage dependencies.**

```javascript
assert.equal(mapAttachmentStatus({ state: 'promoted' }).state, 'ready')
assert.equal(mapAttachmentStatus({ state: 'missing' }).state, 'missing')
assert.equal(shouldAcceptCompletion('attempt-new', 'attempt-old'), false)
assert.deepEqual(toAttachmentRefs(records), [{ bucket: oldBucket, id: firstId }, { bucket: oldBucket, id: secondId }])
```

Define these pure exports in `composerAttachments.js`: `mapAttachmentStatus(status, previousState?)`,
`shouldAcceptCompletion(currentClientId, eventClientId)`, `toAttachmentRefs(records)`, and `getDisplayKind(file)`.
`mapAttachmentStatus` returns `{state}`; previous `failed` remains failed on reconnect missing status.
Sort refs by `position`; never derive bucket from current session ID. Keep local live-upload state above status responses.
Cover all §9.2 transitions: own completed, old completed, terminal failure, removed upload entry before/after completion,
creation refused 4xx/507, exhausted unanswered-creation retries, network pause, transfer 500/507, and stalled foreign upload.
Controller Retry on a paused transfer retains the client ID; chip Retry cancels the old local attempt and allocates a new client ID.
Reject a File fingerprint read before any entry exists: the callback sets the persisted composer chip to `failed`, with Retry/Remove.
Allocate the caller client ID before fingerprinting, so this rejection maps to its exact chip. Ignore callbacks for removed or superseded chips.
Test blocked IndexedDB upgrade with an old open connection; test `versionchange` closes a current connection and clears `dbPromise`.
Test that blocked status reaches bootstrap before any awaited draft hydration and clears after the upgrade completes.
Use controlled IndexedDB request/connection event fakes in `draftStorage.test.js`; add no test dependency.
Build IDs with `makeClientId(tabId, …)`; test tab ownership after reload.
Suppress composer completion/remote-cancellation toasts; keep Files/Artifacts toasts and generic composer labels.
Verify record persistence has no File/base64/progress state. Ref metadata survives aliases/provider switches.

- [ ] **Step 2: Run** `cd frontend && node --test src/utils/composerAttachments.test.js src/utils/uploads/controller.test.js src/utils/draftStorage.test.js`; verify failure.
- [ ] **Step 3: Implement ref storage, runtime attachment state, and composer upload controller integration.**
  Serialize composer creation without `target_dir` or `root`. Persist a record before starting upload; keep the File for Retry.
  In `getDb`, install `db.onversionchange` to close the connection and reset the cached promise.
  Report `request.onblocked` through the storage subscriber; clear blocked status on successful upgrade/open.
  In `main.js`, subscribe before hydrate and render an accessible startup notice without depending on Vue mounting.
  Exact copy: `Draft storage upgrade blocked. Close other TwiCC tabs, then keep this page open.`
  Keep the pending open request alive; old version-8 tabs lack the new close handler and must be closed by the user.
  Unsubscribe and remove the notice before normal app mount. No data reset, package, or schema workaround.
  On reconnect/hydrate query only records without this tab's live local upload, then apply explicit state precedence.
  `releaseAttachments` cancels the local controller attempt first, deletes matching local/legacy rows, then DELETEs explicit refs.
  `forgetAttachments` deletes only local/legacy rows; it never cancels uploads or calls the release endpoint.
- [ ] **Step 4: Run the same suites** plus existing upload rule/ID tests; verify pass.
- [ ] **Step 5: Commit** `feat(composer): persist attachment refs and upload states`.

### Task 15: Preserve Retry/Edit refs and audit every cleanup caller

**Files:** Modify `frontend/src/utils/inflightStorage.js`, `frontend/src/stores/data.js`, `frontend/src/utils/ephemeralSessions.js`,
`frontend/src/utils/ephemeralSessions.test.js`,
`frontend/src/components/session/detail/items/FailedSendBanner.vue`,
`frontend/src/components/session/detail/items/codex/PlanImplementationBody.vue`,
`frontend/src/components/session/list/SessionListItem.vue`, `SessionSelectionBar.vue`,
`frontend/src/views/SessionView.vue`, and existing composer Reset/delete and peer rollback call sites. Task 16 changes their send payloads.
Create `frontend/src/utils/attachmentRecovery.test.js`; start `frontend/src/utils/attachmentStrip.js` with count/key exports.
The strip renderer itself remains Task 18.

**Interfaces:** `registerOutgoingSend` accepts `attachments` metadata alongside optional legacy `medias`.
New snapshot entries: `{bucket, id, name, size, mimeType, kind}`; snapshot lifetime remains 7 days.
`deleteDraftSession(sessionId, {keepInStore=false, releaseAttachments=false}={})` defaults to local forget.
`restoreDraftAttachmentRefs(sessionId, records)` appends positions and immediately touches refs with `holder: 'draft'`.
Define `attachmentCountForMessage(parsed, legacyCount)` and `attachmentMatchKey(text, count)` here, before enabling new sends.
Add async `rebindDraftAttachments(oldSessionId, newSessionId)` to persist unsent record ownership and move matching runtime state.
Extend `createEphemeralActions` with `collectAttachmentRefs(ids)` and `releaseAttachmentRefs(refs)` dependencies.
`purgeEphemeralContent(ids, {releaseAttachments=false}={})` collects refs before any maps/snapshots are removed when release is requested.

- [ ] **Step 1: Write failing recovery and release-call tests.** Inject fetch/storage into attachment recovery actions.

```javascript
assert.equal(saved.attachments.length, 1)
assert.equal(Object.hasOwn(saved, 'mediasDropped'), false)
assert.equal(restored[0].bucket, originalBucket)
assert.equal(restored[0].position, previousLastPosition + 1)
assert.deepEqual(retryPayload.attachments, originalRefs)
```

Keep attachments-only snapshots during hydrate. New snapshots have no 8 MB cap and no encoded bytes.
Legacy snapshots keep `resizeMediasForSend` plus `images`/`documents`; legacy Edit uses Task 17 migration.
Optimistic bubbles carry `attachmentCount`, names/icons, and local object URLs only; never use staging preview URLs there.
Test alias and recovered ephemeral IDs without changing the bucket; Edit after delivered/lost-ack restores `missing` chips.
Test server failure after composer forget: snapshot refs remain available for Retry/Edit.
Test total counts for metadata, snapshots, and optimistic bubbles before Task 16 activates new sends.
During delayed Codex binding, add a new attachment after the first send; binding moves it to the canonical composer and IndexedDB.
Preserve its bucket, id, order, File, object URL, upload attempt, and state. If the canonical composer already has records, append moved records.
Collect ephemeral draft, canonical, failed-send, and in-flight refs before user discard purges them; deduplicate by bucket/id.
Verify internal ephemeral purges/recovery only forget locally, while persisted discard intentions repeat explicit release safely.
Dismiss releases snapshot refs. Edit, snapshot resolution, and expiration only forget locally.
Spy on release calls for the complete ownership matrix below.

| Caller/event | Required operation |
|---|---|
| Chip Remove / Remove all / composer Reset or Clear | Release explicit refs |
| User Discard, Cancel, ordinary draft list/bulk delete | `deleteDraftSession(..., {releaseAttachments: true})` |
| Ephemeral Discard, including list/bulk callers | `discardEphemeralSession` collects all held refs, then purges with release enabled |
| Web peer rollback | Release exactly imported refs; release its whole draft only if newly created |
| Failed-send Dismiss | Release that snapshot's refs |
| Post-send composer clear | Forget only |
| Send-path deletion with `keepInStore` | Default deletion, forget only |
| `bindDraftSession` / ephemeral canonical binding | Move remaining unsent records/runtime first; default old-ID deletion only after that move |
| Both `PlanImplementationBody.vue` deletions | Default deletion, forget only |
| `cleanupOrphanDraftSessions`, `_dropOrphanAttachments` | Forget only |
| Snapshot resolve/expiry, failed-send Edit | Forget only |
| Provider switch | Keep attachments |

- [ ] **Step 2: Run** `cd frontend && node --test src/utils/attachmentRecovery.test.js`; verify failure.
- [ ] **Step 3: Implement snapshots, Retry/Edit/Dismiss, count matching, and the caller audit before new composer sends are enabled.**
  `bindDraftSession` and ephemeral binding rehome surviving unsent attachments before old-draft deletion.
  Use an IndexedDB readwrite transaction for the ownership change; publish matching local state without an intermediate empty composer.
  Treat additions during an awaited bind as canonical-owned through the draft alias, while preserving the attachment's original bucket.
  In `discardEphemeralSession`, collect refs before `_clearEphemeralInflight` and local-map deletion.
  Clear new draftAttachments rows in the ephemeral clearContent dependency; explicit discard alone enables server release.
  Apply the same explicit release rule when hydrating a persisted user discard, without changing automatic process cleanup.
  Extract testable actions into `composerAttachments.js` if Pinia cannot load directly under node:test; exercise actual used actions.
  At hydrate and every 2 h, touch draft refs with `holder: 'draft'` and in-flight/failed refs with `holder: 'snapshot'` separately.
  Keep this heartbeat independent of orphan cleanup removal. Detached server release remains the only post-delivery release.
- [ ] **Step 4: Run** recovery and composer suites; verify pass. Search all attachment cleanup callers and compare with the table.
- [ ] **Step 5: Commit** `feat(composer): preserve staged refs across failed sends`.

### Task 16: Accept all web files and send refs from the composer

**Files:** Modify `frontend/src/components/message/MessageInput.vue`, `AgentSettingsPopover.vue`,
`frontend/src/components/session/detail/SessionItemsList.vue`, `frontend/src/components/browser/BrowserPane.vue`,
`frontend/src/components/files/FilePane.vue`, `frontend/src/components/peer/PeerMessageReviewDialog.vue`,
`frontend/src/components/media/MediaThumbnailGroup.vue`, `MediaPreviewDialog.vue`,
`frontend/src/utils/fileUtils.js`, `frontend/src/utils/ephemeralSessions.js`, and its test file.
Extend `frontend/src/utils/composerAttachments.test.js`.

**Interfaces:** New composer consumers use Task 14 records plus runtime state.
MediaThumbnailGroup keeps its legacy props; attachment items add `id`, `name`, `size`, `kind`, `state`, `progress`, `retryable`.
Add `retry` event keyed by attachment ID; keep old index-based `remove` behavior for legacy callers.
Export `canSendAttachments(records, runtimeStates) -> boolean` from `composerAttachments.js`.

- [ ] **Step 1: Write failing send-state and entry-point contract tests.**

```javascript
assert.equal(canSendAttachments(records, { [id]: { state: 'uploading' } }), false)
assert.equal(canSendAttachments(records, { [id]: { state: 'failed' } }), false)
assert.equal(canSendAttachments(records, { [id]: { state: 'ready' } }), true)
assert.equal(canSendAttachments([], {}), true)
```

Test all six display kinds and metadata-based ephemeral summaries. Keep legacy summary inputs where required by old snapshots.
Assert outgoing payload contains ordered refs and no `images`/`documents` for new attachments.
Keep ordinary socket-send failure drafts intact; clear locally only after successful `sendWsMessage` and snapshot registration.
Web peer import converts all blocks to Files without provider rejection; rollback releases only imported refs or its newly created draft.
Provider selection keeps PDF/text/other attachments. Screenshots continue calling the same `addAttachment` entry point.

- [ ] **Step 2: Run** `cd frontend && node --test src/utils/composerAttachments.test.js src/utils/ephemeralSessions.test.js`; verify failure.
- [ ] **Step 3: Implement all composer UI paths.** Remove type/size/count/total-byte restrictions from the new composer path.
  Remove picker `accept`, paste/drop provider gates, provider-switch `removeNonImageAttachments`, and client resizing on new sends.
  Keep legacy helpers needed by old snapshots/other consumers; remove constants only after auditing references.
  Show ordered thumbnail/icon chips, name, size, progress, state, Retry, and Remove in the current badge/popover.
  Use object URLs for local image/text previews, else staging content endpoints; show an icon for other kinds.
  Show no native/file indicator. Revoke object URLs only when their composer/optimistic users release ownership.
  Always show the paperclip. Disable Send for every non-ready chip; do not pre-check ephemeral native eligibility.
- [ ] **Step 4: Run focused tests and** `cd frontend && npm run build`; verify pass.
  Manually check picker, clipboard files, drop, both screenshot sources, peer import, and mobile chip layout.
- [ ] **Step 5: Commit** `feat(composer): accept all files through staged uploads`.

### Task 17: Migrate legacy draft media without duplicates or ownership loss

**Files:** Create `frontend/src/utils/attachmentMigration.js`, `attachmentMigration.test.js`.
Modify draft hydration in `frontend/src/stores/data.js` and legacy Edit in `FailedSendBanner.vue`.

**Interfaces:** Produces `legacyMediaToFile(media) -> File`;
`migrateLegacyAttachments({sessionId, medias, mediaIds, dependencies}) -> Promise<void>`.
Dependencies provide storage lookup/write/delete, `statusRefs`, `startUpload`, current tab ID, and live-local-upload lookup.
New record ID equals `media.id`; bucket equals its original migration session ID and never changes afterward.

- [ ] **Step 1: Write failing migration tests with in-memory storage and controlled upload completion.**

```javascript
assert.deepEqual(await legacyMediaToFile(txtMedia).text(), originalText)
assert.equal(migratedRecord.id, legacyMedia.id)
assert.equal(uploadCallsForReadyEntry, 0)
assert.equal(recordsAfterTwoHydrations.length, 1)
assert.equal(legacyRowsAfterUploadFailure.length, 1)
```

Decode image/PDF base64 and `txt` plain text correctly; preserve name/MIME.
Order rows by draft `mediaIds`, then remaining `createdAt` order; reuse existing records and positions.
`ready`/`promoted` avoids upload; foreign-tab `uploading` defers migration until next start.
Own-tab stalled uploading without live local ownership starts a fresh attempt; local live ownership starts nothing.
Missing entries start a new client ID. Delete legacy rows only after readiness.
Test removal while migration upload waits: forget/release also deletes the legacy row and `mediaIds` reference, so it never returns.
Test legacy failed-send Edit with current draft attachments: migrated records append without changing existing order.

- [ ] **Step 2: Run** `cd frontend && node --test src/utils/attachmentMigration.test.js`; verify failure.
- [ ] **Step 3: Implement migration using Task 14 upload/state APIs.**
  Keep legacy rows after failures; register readiness cleanup for migration-owned rows.
  Preserve legacy snapshot Retry separately. Do not encode new composer files into IndexedDB.
- [ ] **Step 4: Run** migration, recovery, and composer suites; verify pass.
- [ ] **Step 5: Commit** `feat(composer): migrate legacy draft media to staged refs`.

### Task 18: Render one ordered history strip and match total counts

**Files:** Complete `frontend/src/utils/attachmentStrip.js`; create `attachmentStrip.test.js`,
`frontend/src/components/media/AttachmentStrip.vue`.
Modify `frontend/src/stores/data.js`, `frontend/src/providers/codex/canonical.js`,
`frontend/src/components/session/detail/items/claude_code/Message.vue`, `ContentList.vue`,
`frontend/src/components/session/detail/items/codex/Message.vue`, `UserMessage.vue`,
`frontend/src/share-session/ShareItemsList.vue`.
Modify `frontend/src/components/session/detail/items/UnknownEntry.vue` for queued prompt rendering.
Its current generic `JsonHumanView` fallback displays attachment records; keep that fallback for other unknown entries.

**Interfaces:** Produces `buildAttachmentStrip(metadata, nativeBlocks, {hybrid=false, share=false}={}) -> StripItem[]`;
consume Task 15 `attachmentCountForMessage(parsed, legacyCount) -> number` and
`attachmentMatchKey(text, count) -> string | null`;
`queuedAttachmentDisplay(parsed, options) -> {items: StripItem[], text: string} | null` for extracted Claude queued prompts.
Strip item: `{id, name, kind, mode, src?, artifactOwner?, artifactName?, canOpenArtifact}`.
`AttachmentStrip` emits `open-artifact` with `{owner, relativePath}`; parent uses the existing Artifacts tab navigation.

- [ ] **Step 1: Write failing pure strip/count tests.**

```javascript
assert.deepEqual(strip.map(item => item.name), ['one.png', 'movie.mp4', 'two.png'])
assert.equal(strip[2].src, secondNativeImage)
assert.equal(shareStrip[1].canOpenArtifact, false)
assert.equal(attachmentMatchKey('', attachmentCountForMessage(parsed, 1)), 'a:3')
```

Map inline images by media-slot order, including documents and failed-image placeholders between images.
Inline documents are chips. Hybrid inline entries are chips with no native thumbnail dependency.
File chips use metadata owner and `attachments/<artifact_name>`, including a fork-parent owner.
All-file/no-text renders only the strip; suppress duplicate image groups and document placeholders when metadata exists.
Metadata-free legacy messages keep current rendering. Shares show identical order and file names with no artifact links.
Test new snapshot `attachments.length`, optimistic `attachmentCount`, legacy media/mediaCount fallback, and unchanged text keys.
Use `getParsedContent` for session items; no direct `item.content` parsing or `_parsedContent` access.

- [ ] **Step 2: Run** `cd frontend && node --test src/utils/attachmentStrip.test.js`; verify failure.
- [ ] **Step 3: Implement the shared strip, provider wrappers, and match-key integrations.**
  Pass top-level metadata through Claude/Codex components and hybrid string renderers.
  In `UnknownEntry.vue`, when `queuedAttachmentDisplay` returns a display, render the strip and cleaned text inside existing details.
  For other records keep `JsonHumanView`; do not add provider-specific behavior to that generic component.
  `queuedAttachmentDisplay` recognizes `attachment.type === 'queued_command'`, `commandMode === 'prompt'`, and top-level attachment metadata.
  Test this branch with a native image, document, and file chip. Propagate share mode through this fallback too.
  Derive artifact locations in the current instance; store no original absolute filesystem path in frontend metadata.
  Use existing artifact selection/navigation, with lazy router access where necessary to avoid HMR cycles.
  Propagate explicit share mode into reused renderers; never enable owner artifact links inside the share viewer.
- [ ] **Step 4: Run** strip, composer, recovery, and Codex canonical frontend tests, then `cd frontend && npm run build`.
  The build must include the standalone share viewer; it is not HMR'd.
- [ ] **Step 5: Commit** `feat(messages): render attachment manifests as ordered strips`.

### Task 19: Validate the full feature and record the accepted limits

**Files:** Modify `CHANGELOG.md` only in freshly checked `## [Unreleased]`.
Extend existing attachment test files only for gaps found during this task.
Create `docs/plans/2026-10-04-composer-attachments-any-file-validation.md` with observed results and outstanding user-run checks.

**Interfaces:** No new runtime interface. Validate Tasks 1–18 as one pipeline.
The validation record distinguishes automated results, actual UI observations, and provider checks requiring authorization.

- [ ] **Step 1: Add failing cross-layer regressions for uncovered contracts.** Prefer existing targeted suites over redundant tests.
  At minimum round-trip staged mixed files → plan → commit → provider input → transformed record → history strip.
  Verify all-file/no-text follow-up visibility and matching, plus new-session text requirement.
  Verify a large file send never embeds its bytes in the WebSocket frame or snapshot.
- [ ] **Step 2: Run those focused tests** and verify their failure before any correction.
- [ ] **Step 3: Correct only the demonstrated integration gaps.** Update Unreleased with web any-file support and server-side selection.
  Document retention, reload-interrupted uploads, ephemeral staging traces, lost-ack Edit, promotion duplicates, and legacy phase-1 paths.
- [ ] **Step 4: Run the final automated checks.**

```bash
TWICC_DATA_DIR=$PWD uv run pytest
cd frontend && npm test
cd frontend && npm run build
```

Run these as separate commands from the correct checkout. Expected: exit code 0 for each command.
If broader suites fail for unrelated existing changes, record exact failing tests without changing user-owned code.
No hermetic LLM diagnostic is required unless execution changes a model, runtime, or SDK covered by AGENTS.md.

- [ ] **Step 5: Complete the manual matrix on a user-authorized running instance.** Do not start/restart servers on your own.

| Scenario | Expected observation |
|---|---|
| 400 MB video + images + large PDF, Claude existing/new | Chips upload; eligible images inline; other files in artifacts; order preserved |
| Same files, Codex existing/new | Images inline; PDF/video artifacts; canonical binding only after finish |
| Hybrid `/model …` with attachments | Synchronous `attachments_with_command`; no paste, ack, or release |
| Hybrid ordinary mixed send | Real names, image references, file chips; no raw manifest in history |
| Ephemeral video | `attachment_requires_artifacts`; no promotion; Retry/Edit records intact |
| Forced promotion/send failure | Failed-send banner; Retry reuses refs/tombstones; Edit appends refs |
| Lost ack after actual delivery | Server releases entries; restored Edit chips show missing |
| Reload during upload | Status-driven chip; no File resume from chip; interrupted upload offers Remove |
| Two tabs sharing draft | Current attempt controls readiness; removal never recreates an entry |
| Picker/paste/drop/screenshots/provider switch | Every file accepted; no client resizing; attachments survive provider switch |
| Web peer PDF into Codex composer | PDF accepted and becomes file; rollback releases imported entries |
| Shared history | Same ordered strip; file chips have no owner artifact link |
| Long plan + soft stop + force stop | Other frames/broadcasts continue; soft waits; force kills the current process immediately |

A real Claude document-title request checks **200 characters** only when provider execution is authorized.
If the API rejects that length, record its actual accepted limit and reconcile the spec before changing the contract.
Preserve the accepted dense/encrypted-PDF limitation, within-target EXIF behavior, settings-only platform detection, and CLI media eviction behavior.
Do not claim manual/provider checks pass when they are not run.

- [ ] **Step 6: Commit** `docs(attachments): document any-file composer delivery and validation`.
  Report automated results, manual gaps, and the required user backend restart via `devctl.py`.
  No database migration or package installation is expected.

## Dependencies and Implementation Order

Execute Tasks 1–19 in order. This sequence keeps contracts available before their consumers need them.
Tasks 3–5 share bounded detection; Task 3's content classifier becomes complete in Task 4 before the feature ships.
Provider tasks use the new optional API without changing legacy calls. Task 13 enables the WS refs path after provider integration.
Task 15 completes snapshot recovery and matching before Task 16 enables refs-based composer sends.
Frontend acceptance activates only after upload-state, backend send, and failed-send contracts exist.

## Spec Coverage Map

| Spec requirement | Owning tasks |
|---|---|
| §1–2 goals, decisions, any file, phase-1 scope | Global Constraints; 5, 13–17 |
| §3 existing APIs and legacy behavior | 2, 9–18 |
| §4 provider limits and accepted CLI behavior | 4–6, 10–12, 19 |
| §5 entry-point-agnostic backend foundation | 1–7 |
| §6.1 staging, filenames, readiness, composer tus | 1–2 |
| §6.1.2 REST, content headers, status, touch | 3–4 |
| §6.1.3–4 promotion, settle, release, retention | 2–3, 7, 12–13, 16 |
| §6.2 target, live 1M context, trusted platform, pending hybrid | 5, 12–13 |
| §6.3–6 policy, bounded reads, budgets, first-fit, errors | 4–5, 13 |
| §6.6 detached lanes, aliases, barriers, force kill | 9, 11, 13 |
| §7.1–2 lock discipline, prepare/finish, canonical binding | 7, 10–13 |
| §7.3–5 ranks, escaped manifest, provider order, folding | 5–8, 10–12 |
| §8 WS refs, mutual exclusion, legacy protocol, errors | 9, 13, 15–16 |
| §9.1–3 records, upload states, chips, previews | 14, 16 |
| §9.4–5 refs send, optimistic bubble, snapshots, Retry/Edit | 15–16, 18 |
| §9.6–7 migration and web peer import/rollback | 15–17 |
| §10 ingestion, visibility, user-text readers, history/share | 8, 18–19 |
| §11–12 errors, path confinement, headers, decode bound | 1–5, 7–8, 12–14 |
| §13 unchanged system prompt | Global Constraints; 10–12 |
| §14 deferred CLI/MCP/backend peer replacement | Global Constraints; 13, 16–17 |
| §15 automated/manual matrix | Per-task checks; 19 |
| §16 accepted limitations | Scope and Interpretation; 19 validation record |

## Planning Self-Review

- Spec coverage: every numbered section has an owning task in the coverage map.
- Interfaces: shared names, fields, return types, and raw-text ownership remain consistent across planner, committer, providers, and ingestion.
- Test scope: all five Review Focus conditions have explicit regression cases in their owning tasks.
- Lock order: creation lock precedes upload locks; large planning/copy work never holds provider manager locks.
- Delivery: direct ack attempts precede detached release; parked hybrid release waits for successful paste.
- Compatibility: legacy snapshots, CLI/MCP/backend peer fields, and new-session text validation remain intact.
- Proportion: steps specify contracts, assertions, and checks; they do not prescribe implementation bodies.


## Adversarial Review Record

Three internal reviewers inspect backend delivery, frontend ownership, and executable plan contracts.
The first pass finds nine actionable P2 issues. The plan corrects all nine.

| Finding | Correction |
|---|---|
| Codex finish lacks the original text | Explicit `initial_text` factory keyword and one-shot content assertions |
| Fork parent provenance is unavailable on user records | Per-session metadata latch, live seed, reset, and negative-cache replacement tests |
| ASGI preview can materialize a synchronous file stream | Async streaming iterator, bounded off-loop reads, and early-chunk/closure assertions |
| Refs sends activate before snapshots/recovery exist | Recovery and matching precede composer refs activation |
| Upload tests rely on undefined/local-only helpers | Local fixture contracts and an exact AsyncClient helper/body |
| IndexedDB upgrade waits indefinitely behind an old tab | Connection close/reset and accessible blocked-upgrade bootstrap notice |
| Ephemeral Discard bypasses ordinary draft deletion | Collect refs before the actual purge path; explicit release only for user discard |
| Canonical binding drops the next message's attachments | Move unsent records and runtime before forgetting the old draft ID |
| Fingerprint failure leaves no upload entry/state event | Caller-correlated rejection callback changes the chip to failed |

The second pass clears frontend and execution-order findings. Backend review requires the fork-cache instructions to be explicit.
Those instructions now cover full compute, live pre-flush metadata, negative-cache replacement, and cross-session isolation.
A final targeted backend counter-review validates that correction. No P1/P2 remains in the three reviewed scopes.
The review also defines the status response envelope and records two protocol limits: pasted valid manifests and process-only force kill.
No implementation, package installation, provider call, server restart, or test execution occurs during this plan review.
