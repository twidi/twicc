# Directory ZIP Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download a real Files or Artifacts directory as a progressively generated ZIP, without blocking the asyncio event loop.

**Architecture:** A standard-library ZIP generator writes to a non-seekable sink. A small blocking reader adapts its output to the existing `AsyncFileResponse` and request cleanup. Dedicated directory-download routes preserve raw serving, artifact data-store dispatch, and Git behavior.

**Tech Stack:** Existing Python >= 3.13, `zipfile`, `zlib`, `threading`, Django ASGI, `aiofiles`, Vue 3, Vue I18n, pytest, and Node's test runner.

**Spec:** [2026-11-11-directory-zip-downloads-spec.md](2026-11-11-directory-zip-downloads-spec.md). Read the specification and this plan before implementation.

## Global Constraints

- Use standard-library `zipfile`, standard Deflate, and standard ZIP64. Add no package dependency or external executable.
- Include the selected directory as one top-level directory. Use `root` when its normalized basename is empty.
- Include all regular files, directories, and symlinks. Apply no visibility, ignore, extension, size, depth, or tree-budget filter.
- Preserve symlink type and target text. Never traverse directory symlinks or read their targets.
- Preserve executable permission information and ZIP-compatible modification times. ACLs, ownership, and extended attributes are excluded.
- Fail on unsupported FIFO, socket, or device entries. Never open them as regular files or silently omit entries.
- Use `application/zip`, attachment filename encoding, existing cache/nosniff headers, no HTTP gzip, and no `Content-Length`.
- Keep traversal, metadata, reads, compression, and cleanup outside the event loop. Use asynchronous response iteration.
- Keep aggregate retained content bounded independently of file sizes. ZIP metadata may grow with entry count.
- Keep existing authentication and source confinement. UI root exclusions do not introduce a backend access policy.
- Keep ordinary file/Git downloads, inline previews, artifact broker/data-store operations, composer attachments, and native Android protocols unchanged.
- No snapshot, forced interruption of filesystem calls, range/resume support, progress UI, archive cache, subprocess, job service, or new executor.
- Use i18n for new interface text. Keep code, documentation, tests, and commit messages in English.
- Stay in this shared checkout. Preserve unrelated changes. Commit only each task's files, with a body and the current model trailer.
- Maintain the visible task list during implementation. Complete review and verification before each task commit.
- Package installation, migrations, and server restart remain user operations. This plan requires no dependency installation or migration.

## Review Focus

1. Highly compressible input can produce empty intermediate output. Task 1 proves this is not EOF and cancellation stops further source reads.
2. Broken, cyclic, and external symlinks must remain links. Task 1 proves their targets are neither opened nor traversed.
3. Unreadable descendants and special entries can fail after headers. Tasks 1 and 2 prove failure without silent omission or successful HTTP completion.
4. Cancellation can arrive during acquisition, reads, sends, or finalization. Tasks 1 and 2 prove ownership and serialized iterator closure.
5. Small blocks alone do not bound memory. Tasks 1 and 2 account for retained payload, scaling, and paused-send read-ahead separately from ZIP metadata.

## File and task boundaries

| Task | Files | Responsibility |
| --- | --- | --- |
| 1 | New `src/twicc/directory_archives.py`, `tests/test_directory_archives.py` | ZIP source, bounded reader, symlink representation, source lifecycle |
| 2 | `src/twicc/views.py`, `src/twicc/urls.py`; new `tests/test_directory_downloads.py`, `tests/test_directory_download_streaming.py` | Authorized routes, HTTP adapter, real ASGI acceptance |
| 3 | `frontend/src/utils/download.js`; `frontend/src/components/files/FileTreeContextMenu.vue`, `FileTreePanel.vue`; five locale catalogs; new frontend tests | URL construction, directory action, actual panel dispatch, translations |

Do not change `src/twicc/http_streams.py` or `src/twicc/asgi.py` unless a demonstrated integration defect requires revising this plan.
Reuse their protected preparation, worker reads, cleanup executor, and request registry.
Do not extract unrelated view or component code.

---

### Task 1: Build the standard-library streaming archive source

**Files:**
- Create: `src/twicc/directory_archives.py`.
- Test: `tests/test_directory_archives.py`.

**Interfaces:**
- Produce `DirectoryZipReader(io.BufferedIOBase)` with `__init__(self, directory: str)`, `read(self, size: int = -1) -> bytes`, and `close(self) -> None`.
- Produce `directory_archive_name(directory: str) -> str`, returning `<basename>.zip`, or `root.zip` for `/`.
- Constructor accepts a normalized absolute source. It validates a real directory with `lstat` and opens/closes its root `scandir` to detect access failure.
- Constructor raises normal filesystem exceptions for missing, non-directory, root-symlink, or unreadable sources. It neither creates directories nor walks descendants.
- `read(0)` returns `b''` without advancing. Negative sizes raise `ValueError`; the HTTP consumer always requests positive bounded reads.
- `read(size)` returns at most `min(size, 65536)` bytes. An empty return means actual EOF or stopped/closed source, never an intermediate empty compression result.
- `close()` is idempotent. It sets the stop event before waiting for an active `read`, then closes the iterator and all owned sources.
- Private sink and generator stay within this module. They are not interfaces for views or frontend code.

- [ ] **Step 1: Write archive-content tests.**

Use `zipfile.ZipFile(io.BytesIO(collected_bytes))` as an independent consumer of the streaming writer.
Name tests and assertions explicitly:

| Test | Assertions |
| --- | --- |
| `test_complete_tree_and_layout` | Exact bytes and CRC; `reports/` top level; relative names; Unicode; hidden `.env`; `.git`; ignored-style paths; deep lazy-tree-absent content; nested empty directory |
| `test_empty_directory_and_root_name` | Valid empty-directory ZIP; `directory_archive_name('/') == 'root.zip'`; Unicode attachment basename |
| `test_metadata` | Unix directory/file type, executable bit, representable mtime, and normalization outside ZIP date range |
| `test_symlinks_preserved_without_target_access` | Relative, broken, directory, cyclic, and external links retain `S_IFLNK` and exact `os.fsencode(os.readlink(path))` bytes; no target file open or target-directory scan |
| `test_invalid_root` | Missing path, regular file, and root symlink fail before returning a usable source; root access failure opens no descendant |
| `test_unsupported_entries_fail_before_open` | FIFO and socket fail; simulated device metadata fails; a trap proves none is opened as a regular source |
| `test_source_failures_propagate` | Injected `scandir`, `lstat`, file-open, read, compression, and ZIP-finalization failures raise; discovered-then-removed entry is not omitted |

Inject permission errors rather than trusting `chmod` under privileged test users.
Use platform skips only for unavailable filesystem entry types. Keep metadata simulation coverage.

- [ ] **Step 2: Run RED.**

Run `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_directory_archives.py -q`.
Expected: collection fails because `twicc.directory_archives` does not exist.

- [ ] **Step 3: Implement the generator, sink, and metadata rules.**

Use a non-seekable `io.RawIOBase` sink with `write`, `tell`, and a drainable byte buffer.
Open `ZipFile(sink, 'w', compression=ZIP_DEFLATED, compresslevel=6, allowZip64=True)`.
Use `64 * 1024` bytes for source reads and maximum reader output, matching the existing HTTP block size.

Emit the selected directory entry and drain its header before descending.
Walk lazily with `os.walk(..., followlinks=False, onerror=raise_error)` or equivalent explicit `scandir` traversal.
Use `lstat` to classify every entry; directory symlinks are emitted once and never descended into.
No ordering guarantee or full-tree pre-scan is required.

Create `ZipInfo` names relative to the source parent, beneath its normalized basename or `root`.
Set Unix type/permission metadata and ZIP-compatible timestamps, clamped to 1980–2107.
Use `writestr(info, b'')` for directory entries, with a trailing slash and directory attributes.
Use stored entries for symlink target bytes and standard Deflate level 6 for regular files.
Set a regular member's known `file_size` before `archive.open(info, 'w')`, so ZIP64 local-header selection occurs before writing.
Never call a convenience API that reads a whole regular file.

For each regular file, open one source, read bounded blocks, write each block into the ZIP member, and yield drained output.
Yield intermediate empty drains internally; the reader skips them.
Check the stop event before each entry and each source read, and after returning from an outstanding source operation.
Keep source and member contexts around yields, so generator closure releases them.

On normal exhaustion, close the ZIP writer, then expose the final central directory and EOF.
On failure or `GeneratorExit`, close the writer and sources without yielding cleanup output.
Discard sink contents after failure/cancellation. Do not replace a source failure with successful EOF.
ZIP finalization can temporarily buffer metadata proportional to entry count; classify that buffer separately from payload.

- [ ] **Step 4: Write reader, cancellation, memory, and ZIP64 tests.**

| Test | Assertions |
| --- | --- |
| `test_reader_sizes_and_empty_compression_output` | `read(0)` does not advance; negative size fails; positive reads stay <= 65536; large zero-filled input round-trips despite empty intermediate drains |
| `test_first_output_precedes_traversal_and_content` | First nonempty read precedes descendant scan and source-content reads |
| `test_close_before_iteration_and_repeated_close` | No compression or content read; iterator/source released; repeated close safe |
| `test_close_during_pending_read` | Event-controlled source read blocks; another thread calls close; stop becomes visible before lock acquisition; release permits cleanup; no concurrent generator close and no subsequent source read |
| `test_stop_during_compressible_input` | Repeated empty output does not prevent stop checks between source blocks |
| `test_payload_memory_does_not_scale_with_file_size` | Same entry count, 8 MiB then 64 MiB generated compressible/incompressible sources; discard delivered bytes; instrument sink, reader remainder, and current chunks; retained payload stays within a fixed bound |
| `test_zip64_large_member_and_many_entries` | Simulated large-member declaration selects ZIP64 local structures; temporarily lowered public `zipfile.ZIP64_LIMIT`/`ZIP_FILECOUNT_LIMIT` exercises valid complete ZIP64 archives without giant fixtures |

Use a lock to serialize `read` and iterator closure. Never call `generator.close()` while its `next()` executes.
Implement `close` with an independently settable `threading.Event`, then acquire the read lock, close the iterator, clear buffers, and close the reader base.
Use `io.BufferedIOBase` so `aiofiles.threadpool.wrap` accepts the reader without registration or response changes.

For memory evidence, account for source block, sink payload, pending output, returned chunk, and fixed compressor buffers.
Assert a conservative fixed 1 MiB retained-payload budget for this adapter; exclude fixture data and entry-count-dependent ZIP metadata.
Add `tracemalloc` peak comparison with fixed entry count as supporting evidence; it does not measure native zlib allocations.
Keep the architectural accounting of zlib's fixed working memory explicit. Do not claim constant total archive memory.
Document simulated ZIP64 boundaries separately from real multi-GiB or 65536-entry transfer evidence.

- [ ] **Step 5: Run GREEN and review the deliverable.**

Run `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_directory_archives.py tests/test_http_streams.py -q`.
Expected: all pass; no changed shared response behavior.
Review source ownership, bounded payload accounting, and symlink handling before committing.

- [ ] **Step 6: Commit only Task 1 files.**

Subject: `feat(downloads): add streaming directory zip source`.
Body: explain the stdlib non-seekable writer, bounded reader, symlink representation, and cancellation ownership.
Stage only the two listed files. Use a body file and `git commit --only --file <body-file> -- <task-paths>`.
Include `Co-Authored-By: Codex <current model> <codex@openai.com>` with the actual current model name.

### Task 2: Add authorized directory-download routes and ASGI evidence

**Files:**
- Modify: `src/twicc/views.py` near `file_raw` and `standalone_file_raw`.
- Modify: `src/twicc/urls.py` beside the corresponding directory-tree/file-content routes.
- Create: `tests/test_directory_downloads.py`.
- Create: `tests/test_directory_download_streaming.py`.
- Reuse without modification: `AsgiCall` from `tests/test_file_download_streaming.py`.

**Interfaces:**
- Consume `DirectoryZipReader(directory: str)` and `directory_archive_name(directory: str)` from Task 1.
- Consume `prepare_download_response(factory)` and `AsyncFileResponse(reader, filename=..., content_type=..., size=None, head=..., close=...)`.
- Produce async views `directory_download(request, project_id, session_id=None)` and `standalone_directory_download(request)`.
- Produce synchronous helper `_directory_download_response(directory: str, *, head: bool, root: str | None = None) -> AsyncFileResponse` in `views.py`.
- Routes accept GET/HEAD only:
  - `/api/projects/<str:project_id>/directory-download/?path=<absolute-path>`.
  - `/api/projects/<str:project_id>/sessions/<str:session_id>/directory-download/?path=<absolute-path>`.
  - `/api/directory-download/?path=<absolute-path>&root=<optional-absolute-root>`.
- No `download=1` parameter is needed. These routes always mean an explicit ZIP attachment.

- [ ] **Step 1: Write route and HTTP-contract tests.**

Use temporary project/session/standalone directories and existing `AsyncClient` streaming-consumption patterns.
Cover project root itself, nested directories, and a session-only allowed directory.
Assert source content with an independent ZIP reader, including a directory readable without write permission.
Keep root exclusion out of backend authorization tests: Artifacts root requests can succeed when otherwise authorized.

Add named cases:

- `test_directory_download_route_matrix`: all three routes, Unicode filename, `application/zip`, attachment, `nosniff`, existing cache/CDN headers, omitted length, async iterator.
- `test_directory_download_access`: real password middleware, unknown project, mismatched session, project allowed-base rejection, standalone lexical-root rejection, and standalone resolved-root escape.
- `test_directory_download_invalid_source`: missing query gives 400 with `backend.file_tree.path_required`; relative/NUL path, missing directory, file, or root symlink gives 404 with `backend.file_tree.directory_missing`.
- `test_directory_download_preparation_permission_error`: root `PermissionError` gives 403 using existing `backend.file_content.permission_denied` semantic metadata.
- `test_directory_download_methods`: POST/PUT/DELETE return 405 and do not create, modify, or remove paths.
- `test_directory_download_head`: GET-equivalent attachment metadata, no body/length, no descendant walk/content reads/compression, resources released.
- `test_directory_download_not_http_gzipped`: `Accept-Encoding: gzip` still has no HTTP `Content-Encoding`; decoded ZIP bytes start with a ZIP header.

Use existing `ui_error` conventions for application-owned JSON errors. Other preparation `OSError` failures remain normal server errors.
Do not add new translated error keys when existing messages describe these cases.

- [ ] **Step 2: Run RED.**

Run `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_directory_downloads.py -q`.
Expected: the new route assertions fail with unmatched URLs.

- [ ] **Step 3: Implement the routes and protected response preparation.**

Reject invalid methods first. Read `path` and normalize only after checking an absolute, NUL-free path.
For project/session routes, call `validate_path(project_id, directory, session_id=session_id)` through `sync_to_async`.
Validate the requested directory itself, not its parent; the latter incorrectly rejects a project-root download.
Preserve returned ownership/allowed-base errors. Do not add a different project confinement model.

For standalone, keep empty root unrestricted and apply existing `validate_standalone_root` lexical behavior.
Perform nonempty-root realpath confinement off-loop inside the protected factory, matching standalone raw-download behavior.
Do not call `ensure_session_artifacts_root`; downloading never creates the source.

Call `prepare_download_response(lambda: _directory_download_response(...))`.
The helper checks confinement where applicable, constructs the reader, then constructs `AsyncFileResponse` with:

```python
filename=directory_archive_name(directory)
content_type="application/zip"
size=None
head=head
close=reader.close
```

Passing `close=reader.close` is mandatory: the existing cleanup executor must set stop before awaiting a pending worker read.
If response construction fails after reader acquisition, the factory closes the reader before re-raising.
Translate preparation errors in the view, before returning the response. Never translate an iterator error into ZIP bytes.
Leave raw endpoints, broker wrapping, data-store dispatch, and Git response helpers untouched.

- [ ] **Step 4: Write real ASGI lifecycle and backpressure tests.**

Use `DownloadCleanupMiddleware(ASGIHandler())`, actual routes, and event-controlled `AsgiCall.send_hook` failures/pauses.
Patch narrow source operations to expose worker events; do not replace the ZIP reader with a plain byte iterator.
Never depend on arbitrary sleeps for races. Release every held worker event in `finally`.

`AsgiCall.send` appends each message to `sent` before calling the hook.
For the memory-only test, the hook counts/hashes each body incrementally, then replaces `message['body']` with `b''` before pausing or returning.
This prevents the harness from retaining a complete 8/64 MiB response. Do not call `AsgiCall.body` for this test.
Account for the currently executing send chunk, and keep normal message recording for separate content/failure tests.
The shared harness requires no modification.

| Test | Assertions |
| --- | --- |
| `test_asgi_progressive_zip` | First ZIP body precedes full traversal and complete file consumption; successful final body only after a valid archive footer |
| `test_asgi_payload_bounds_and_paused_send` | Parameterize compressible/incompressible input and 8/64 MiB sizes; count source reads and retained payload; pause first body send, await a synchronization barrier, prove no continued consumption; account for the in-flight operation and <= 1 MiB adapter payload |
| `test_asgi_loop_progress_during_source_work` | Event-controlled read and compression delays execute on workers; an unrelated coroutine progresses before release; traversal/metadata/close thread IDs also differ from loop thread |
| `test_asgi_late_source_failures_abort` | Traversal, metadata/open/read/compression/finalization errors and unsupported entries after initial body send do not append an error payload or emit successful `more_body=False` completion |
| `test_asgi_cleanup_matrix` | Normal completion, prepared response closed before iteration, pending-read disconnect, paused-body-send disconnect, initial-header-send failure, and body-send failure all release reader/writer/open source |
| `test_asgi_late_acquisition_and_repeat_cleanup` | Cancel during worker preparation; release late acquisition; response remains owned until closed; repeated response close is safe; no concurrent generator closure |
| `test_asgi_finalization_cancel` | Block writer finalization, disconnect, then release; discard cleanup output and close; do not send a success body |

Assert no synchronous-iterator adaptation warnings for representative project, session, and standalone requests.
For streaming failures, inspect ASGI messages, not only the raised exception or response status.
Keep the fixed-entry-count memory fixture separate from a many-entry metadata fixture.
The paused-send test must account for all payload owners; bounded individual `read` calls alone do not pass A7.

- [ ] **Step 5: Run GREEN and affected backend regressions.**

Run:

```bash
cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_directory_archives.py tests/test_directory_downloads.py tests/test_directory_download_streaming.py tests/test_http_streams.py tests/test_file_download_streaming.py tests/test_file_downloads.py tests/test_artifact_data_writes.py tests/test_artifact_data_store.py tests/test_artifact_broker_html.py -q
```

Expected: all pass, without synchronous-iterator warnings.
Confirm unchanged raw directory/data-store handling and ordinary file/Git downloads.
Review the route access matrix and every resource owner before committing.

- [ ] **Step 6: Commit only Task 2 files.**

Subject: `feat(downloads): stream directory archives over authorized routes`.
Body: explain the dedicated endpoints, existing async lifecycle reuse, HEAD behavior, and ASGI validation.
Stage only the four listed files. Commit with `--only`, a body file, and the current model trailer.

### Task 3: Expose directory ZIP downloads in Files and Artifacts

**Files:**
- Modify: `frontend/src/utils/download.js`.
- Modify: `frontend/src/components/files/FileTreeContextMenu.vue` and `FileTreePanel.vue`.
- Modify: `frontend/src/i18n/locales/en.json`, `fr.json`, `de.json`, `es.json`, `pt-BR.json`.
- Create: `frontend/src/utils/download.test.js`.
- Create: `frontend/src/components/files/fileTreeDownloads.test.js`.

**Interfaces:**
- Consume Task 2's three routes.
- Produce `buildDirectoryDownloadUrl({ directoryPath, projectId, apiPrefix, root }) -> string | null` in `download.js`.
- Keep `buildFileDownloadUrl`, `triggerDownload`, and context-menu `download` event signatures unchanged.
- Keep existing Git file/diff event dispatch unchanged.

- [ ] **Step 1: Write URL, real-menu, and panel-dispatch tests.**

Use Node's test runner. Use the existing Vue SFC compiler/custom renderer harness in `frontend/tests/helpers/scrollerComponentHarness.js`.
Execute the real SFC computed/action code with narrow dependency stubs; avoid tests that only match source text or duplicate the availability expression.
Do not mount unrelated editors, frames, or the whole session view.

| Test | Assertions |
| --- | --- |
| `directory download URLs preserve scope and encode paths` | Project/session prefixes; standalone root; Unicode, spaces, `#`, `?`, `%`, and `&`; empty source returns null |
| `real directory action is independent of write access` | Files directory with concrete `fullPath` and `createOnly=false` shows ZIP action with both writable states and during writable-loading |
| `excluded directory nodes have no ZIP action` | Both Git modes, lazy Artifacts root whether present or missing (`createOnly=true`), synthetic/group/non-path node, empty path; existing file and Git diff actions unchanged |
| `panel dispatch selects file or directory download` | Invoke real menu selection and panel handler; Files directory uses new URL; Files file uses existing raw URL; Git file emits existing parent event; no browser fetch/blob enumeration |
| `ZIP action translates in every supported locale` | Render the actual menu in all five languages; assert translated ZIP label and emitted existing `download` event |

Check actual tree node classification when preparing fixtures.
Synthetic/root-selector nodes must not acquire directory eligibility just because their display label looks like a directory.
Existing Artifacts root exclusion uses `lazyRoot && data.isRoot` through `createOnly`.
Do not hide ordinary Files roots that represent downloadable real directories.

- [ ] **Step 2: Run RED.**

Run `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/download.test.js src/components/files/fileTreeDownloads.test.js`.
Expected: missing builder and absent directory action/dispatch cause failures.

- [ ] **Step 3: Implement URL construction and action dispatch.**

Use `URLSearchParams` for `path` and standalone `root` values.
With `projectId`, return `${apiPrefix}/directory-download/?${query}`; otherwise use `/api/directory-download/?${query}`.
Keep null return for an absent source, matching the existing file builder.

In `FileTreeContextMenu.vue`, extend `canDownload` only for Files-mode directory nodes with a concrete nonempty `fullPath`.
Keep the existing `!createOnly` render guard. Do not gate downloads on write permission.
Use the new ZIP label for directories and existing labels for files.
Keep all Git directory actions excluded and existing Git file/diff rules intact.

In `FileTreePanel.vue`, keep the early Git dispatch branch.
In the Files branch, select the directory builder when `contextMenu.value.type === 'directory'`; otherwise use the existing file builder.
Pass the current path, project/session API prefix, and `rootRestriction || rootPath`.
Continue calling `triggerDownload` synchronously from the user gesture. Add no fetch/blob or frontend tree enumeration.

Add `files.actions.downloadFolderZip` in all five catalogs:

| Locale | Exact label |
| --- | --- |
| en | Download folder (.zip) |
| fr | Télécharger le dossier (.zip) |
| de | Ordner herunterladen (.zip) |
| es | Descargar carpeta (.zip) |
| pt-BR | Baixar pasta (.zip) |

- [ ] **Step 4: Run GREEN and validate product behavior.**

Run the focused Node command from Step 2, then `cd /home/twidi/dev/twicc-poc/frontend && npm test`.
Run targeted i18n lint with `npx --no-install eslint --config eslint.i18n.config.js src/utils/download.js src/components/files/FileTreeContextMenu.vue src/components/files/FileTreePanel.vue src/i18n/locales/en.json src/i18n/locales/fr.json src/i18n/locales/de.json src/i18n/locales/es.json src/i18n/locales/pt-BR.json` from `frontend/`.
Expected: tests pass and no new translation violation. Identify unrelated shared-checkout failures separately; never rewrite unrelated code to make this task green.

After the user restarts the backend, check a project directory, a nested Artifacts directory, a read-only readable directory, and an empty directory.
Extract each ZIP and compare names/content. Confirm Artifacts root and Git directories have no ZIP action.
Check one ordinary file and one Git download. Record whether browser smoke validation runs or awaits the user restart.
Do not restart a server autonomously or claim unrun smoke checks pass.

- [ ] **Step 5: Commit only Task 3 files.**

Subject: `feat(files): add directory zip download actions`.
Body: explain Files/Artifacts eligibility, dedicated URL dispatch, unchanged Git behavior, translations, and validation performed.
Stage only the ten listed frontend files. Commit with `--only`, a body file, and the current model trailer.

## Acceptance and final handoff

| Spec criteria | Owning evidence |
| --- | --- |
| A1 | Task 3 actual-menu and panel-dispatch tests; browser smoke |
| A2 | Task 1 invalid root; Task 2 access/source/method matrix |
| A3–A5 | Task 1 independent ZIP content/metadata/symlink tests |
| A6–A8 | Tasks 1–2 progressive, memory, paused-send, and loop-progress tests |
| A9–A10 | Task 2 header/gzip/HEAD tests; Tasks 1–2 early closure |
| A11–A13 | Tasks 1–2 source failure, cleanup, finalization, and late-acquisition matrix |
| A14 | Task 1 complete small ZIP64 archives and declared-large-member header evidence |
| A15 | Task 2 backend regression command; Task 3 file/Git/menu regression tests |

At handoff, report task commits, executed checks, ZIP64 simulation limits, and any browser check awaiting user restart.
Do not report a real multi-GiB transfer unless one actually runs.
No release, CHANGELOG, package installation, migration, or server restart belongs to this plan.

## Adversarial review outcome

Round 1 uses two independent internal reviewers with architecture/lifecycle and coverage/integration lenses.
Both review this implementation plan against the approved specification and current code.
The architecture reviewer reports no substantive finding.
The coverage reviewer identifies one P2 test-recipe defect: the existing ASGI harness retains every response body.

Task 2 now requires its memory-only send hook to count/hash bytes, then discard recorded body bytes before pausing or returning.
Other content/failure tests retain normal ASGI messages. This correction needs no shared harness or product-code change.

Both reviewers re-read the revised plan in round 2 and approve it.
They confirm the harness correction and report no remaining substantive architecture, lifecycle, coverage, or integration finding.
No feature implementation occurs during planning.
