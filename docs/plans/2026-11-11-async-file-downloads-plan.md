# Async File Downloads Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Execute one task at a time, with task review and commits.

**Plan review:** Two independent internal reviewers complete two adversarial rounds. No findings remain after round 2.

**Goal:** Stream existing downloads progressively under ASGI using aiofiles and bounded binary reads.

**Architecture:** Add a shared Django async streaming response over an already-open binary reader. A small ASGI cleanup wrapper owns download preparations for the complete HTTP request. Keep Django's handler, routing, and access middleware. Git retains ownership of its process and exposes its binary reader to the shared response.

**Tech Stack:** Python 3.13+, Django 6.1.2, asyncio, aiofiles, pytest.

**Spec:** `docs/plans/2026-11-11-async-file-downloads-spec.md`.

## Global Constraints

- Use `aiofiles` for asynchronous file reads.
- Use Django's standard `StreamingHttpResponse` async interface.
- Keep the existing routing, middleware, and frontend download protocol.
- Application body buffering must remain bounded independently of source size.
- Once a file is opened for delivery, continue using that file rather than reopening its pathname during iteration.
- Keep streaming responses outside HTTP gzip compression, as they are today.
- Cancelling an await does not necessarily stop the filesystem operation running in a worker thread.
- A failed Git command must not complete a download as successful, including after producing partial output.
- Valid empty patches remain successful downloads.
- ZIP and directory downloads are excluded. Inline previews and assets remain unchanged.
- Follow AGENTS.md and AGENTS.local.md. Work in the current checkout and branch; do not create isolation.
- Preserve unrelated edits. Stage explicit paths or our exact hunks only. Never use broad staging or destructive git commands.
- Every completed task has a commit with a Conventional Commit subject, explanatory body, and `Co-Authored-By: Codex GPT-6.1 <codex@openai.com>`.
- Run Python through `uv run`; tests use the isolated test settings. Never set TWICC_DATA_DIR for pytest.
- Do not restart servers, run migrations, edit CHANGELOG, or install unrelated packages.
- Each implementer works alone and never spawns another agent. The controller owns independent reviews.

After each implementation commit, the controller obtains a task-scoped spec-compliance and code-quality review.
Resolve blocking findings before marking the task complete and starting the next task.
Commit corrections separately when a review requires them.
After Task 3, obtain a whole-change review and resolve its blocking findings.

## Review Focus

- Cancellation while a worker still owns an acquisition or read must retain cleanup ownership: Task 1 and Task 2 tests.
- Cancellation between yielded chunks, while ASGI sends, must close resources: Task 1 and Task 3 tests.
- Failure to send initial HTTP headers must release a prepared source even when iteration never starts: Task 1 and Task 2 tests.
- A file that grows, shrinks, or is replaced must respect its opened source and declared length: Task 2 tests.
- Git EOF is not proof of success; valid diff exit results must stay valid: Task 3 tests.
- Existing inline helper callers must stay synchronous FileResponse callers with unchanged broker behavior: Task 2 tests.

## Verification policy

Use TDD for the new behavior. Read superpowers:test-driven-development before implementation.
Run focused tests during edits and the task's relevant suite before committing.
Do not run the entire repository suite for each task.
After all integrations, run the combined affected suite once.
Use deterministic synchronization for cancellation tests, not arbitrary sleeps as the correctness condition.
Bound scripted test runs with `timeout -k 5 180` or a justified longer limit.

### Task 1: Shared async download response and dependency

**Files:**
- Create: `src/twicc/http_streams.py`.
- Create: `tests/test_http_streams.py`.
- Modify: `pyproject.toml`, `uv.lock`.
- Include in this task's commit: this plan and the reviewed spec.

**Interfaces:**
- Produce `AsyncFileResponse(StreamingHttpResponse)`.
- Constructor: `AsyncFileResponse(file: BinaryIO, *, filename: str, content_type: str, size: int | None = None, head: bool = False, finish: Callable[[], None] | None = None, close: Callable[[], None] | None = None)`.
- `file` is an already-open standard binary reader, positioned at the content start. Adapt it through `aiofiles.threadpool.wrap` when iteration starts on the event loop.
- `size=None` means unknown length. A known size limits the bytes sent. Premature EOF with a known size raises an error.
- `finish` is blocking source-completion validation, called off-loop before successful body completion. It must not run on cancellation or read failure.
- `close` owns cleanup of both reader and source when provided. Otherwise cleanup closes `file`. Cleanup must be idempotent.
- The response provides its normal synchronous `close()` fallback without running blocking cleanup on the event loop.
- Produce `DownloadCleanupMiddleware(application)`, a small ASGI wrapper with a request-scoped cleanup registry shared through a ContextVar. It does not replace or subclass Django's handler, change access policy, or consume response content.
- Produce `async def prepare_download_response(factory: Callable[[], AsyncFileResponse | None]) -> AsyncFileResponse | None`. It runs blocking source preparation off-loop and retains ownership if the caller is cancelled. Register preparation with the active request registry before awaiting its worker, including ownership of a response returned after cancellation. Without a registry, direct test callers retain response-close ownership; acquisition cancellation must still be safe.
- The wrapper's `finally` releases registered resources on every request exit, including `send(http.response.start)` failure before iteration. Pending preparation must retain cleanup ownership until it settles. Response cleanup remains idempotent with this outer fallback. A view's current task is not a sufficient lifetime hook: Django can adapt middleware across tasks before ASGI sends the response.
- Cancellation cleanup has two orders. Default file cleanup first settles its in-flight operation, then closes the file. Custom source cleanup starts before waiting for pending reads or finish callbacks, so it can stop a producer. The custom callback must unblock operations before safely closing their reader.
- Execute custom source cleanup in a dedicated cleanup executor, separate from the default read/finish executor. Cleanup must start even when pending source reads occupy all default workers. Do not submit cleanup behind the reads it must unblock.

- [ ] **Step 1: Add failing behavior tests.** Cover bounded reads, newline-free bytes, exact/empty/unknown lengths, premature EOF, finish success/failure, and stable headers. Assert `response.is_async` and exact bytes.
- [ ] **Step 2: Add lifecycle tests.** Cover explicit close before first iteration, success, read failure, cancellation during read or finish, cancellation while a sender pauses after a yielded block, and repeated close/cancellation. Hold reads with synchronization and verify an unrelated asyncio task progresses. Verify late completion of a cancelled read remains owned. Add a test where custom cleanup alone releases a read while the default executor is saturated. Through DownloadCleanupMiddleware around the real ASGIHandler, fail the initial response-start send and assert source cleanup despite no body iteration or response.close. Also cover acquisition completing after cancellation and verify isolation between simultaneous requests.
- [ ] **Step 3: Verify RED.** Run `timeout -k 5 180 uv run pytest tests/test_http_streams.py -q` and record the expected failure before implementation.
- [ ] **Step 4: Add the dependency, response, preparation helper, and cleanup wrapper.** Run `uv add 'aiofiles~=25.1.0'` in this checkout, as the dependency requested for this implementation. Use 64 KiB blocks. Preserve filename encoding with Django's `content_disposition_header`, nosniff, never-cache headers, and CDN no-store. Never pass an aiofiles iterator directly, because it reads lines. Apply the lifecycle and executor ordering defined above; no blocking reads, close, or waits on the loop. Keep the ownership implementation small and documented. Do not wire the wrapper or change routes yet.
- [ ] **Step 5: Verify GREEN and self-review.** Run `timeout -k 5 180 uv run pytest tests/test_http_streams.py -q`. Review the diff for task-only changes. Run `uvx ruff check src/twicc/http_streams.py tests/test_http_streams.py` if available without changing dependencies.
- [ ] **Step 6: Commit.** Stage only the listed files and commit `feat(downloads): add async file streaming response`, with explanatory body and the required trailer. Leave a full report with RED/GREEN evidence at the controller's report path.

### Task 2: Disk downloads and HTTP compatibility

**Files:**
- Modify: `src/twicc/views.py`, `src/twicc/asgi.py`, `tests/test_file_downloads.py`.
- Create: `tests/test_file_download_streaming.py` for disk-route lifecycle and ASGI tests.

**Interfaces:**
- Consume `AsyncFileResponse` from Task 1 with the exact constructor above.
- Extend `_raw_file_response(normalized_path: str, *, as_attachment: bool = False, head: bool = False)` without changing existing callers' defaults.
- Only `as_attachment=True` selects AsyncFileResponse. The existing inline FileResponse branch remains unchanged.
- `_raw_serve_response` passes HEAD into the download branch. Download validation/opening remains before successful response headers.
- The opened descriptor supplies size and regular-file metadata, with blocking preparation off-loop. Preparation owns an opened file even if its await is cancelled before returning it.
- Consume Task 1's `prepare_download_response` for the entire blocking download-response factory; do not acquire a reader in an unprotected await before this helper.
- Wire `django_asgi_app = DownloadCleanupMiddleware(get_asgi_application())` in `src/twicc/asgi.py`. Keep the existing HTTP router, MCP bypass, origin gates, and auth middleware unchanged.
- For standalone download requests, move the existing realpath-based confinement check off-loop without changing its policy. Preserve the inline branch's behavior. Project/session filesystem validation already runs through sync_to_async; audit the included download preparation for any remaining blocking metadata operations.

- [ ] **Step 1: Add failing compatibility tests.** Update body-reading helpers so request and async consumption happen in the same event loop. Preserve inline consumption where tests cover non-download responses. Assert exact filename/media type/cache headers, Unicode, HTML download bypass, empty content, directory refusal, existing outside-root refusal, project/session/standalone scope, and HEAD with no body reads.
- [ ] **Step 2: Add failing streaming/mutation tests.** Through the cleanup wrapper and Django's real ASGIHandler, verify a first chunk arrives without reading the complete source, slow-client reads stay bounded, and unrelated asyncio work progresses while a read is held. Separately delay standalone realpath metadata validation and verify unrelated asyncio progress before file opening. Test file growth, shrink, path replacement, opening failure, cancellation during acquisition, before first iteration, during reading, and while sending (including initial headers). Verify the production Django ASGI entry point installs the wrapper. Reuse existing ASGI testing patterns from tests/test_uploads_transfer.py without importing another test module.
- [ ] **Step 3: Verify RED.** Run the focused new tests and record why current FileResponse fails progressive delivery. Avoid consuming a huge source just to prove buffering.
- [ ] **Step 4: Integrate disk downloads and request cleanup.** Select the shared response only for the download branch. Install the ASGI wrapper and use protected preparation. Read metadata from the opened file. Move standalone filesystem confinement checks off-loop for downloads. Preserve existing auth/path policy and all inline helper behavior. Ensure acquisition cancellation cannot discard an opened resource. Bound output to Content-Length and treat early EOF as transfer failure. HEAD must release resources without body reads.
- [ ] **Step 5: Verify GREEN and self-review.** Run `timeout -k 5 180 uv run pytest tests/test_http_streams.py tests/test_file_downloads.py tests/test_file_download_streaming.py tests/test_gzip_middleware.py tests/test_artifact_broker_html.py -q`. Run focused lint for changed Python files, without fixing pre-existing unrelated findings.
- [ ] **Step 6: Commit.** Stage only task edits and commit `fix(downloads): stream disk files asynchronously`, with body and required trailer. Report RED/GREEN commands, results, scope, and any interface changes.

### Task 3: Git downloads and combined ASGI verification

**Files:**
- Modify: `src/twicc/git.py`, `src/twicc/views.py`, `tests/test_file_downloads.py`, `tests/test_file_download_streaming.py`.
- Create: `tests/test_git_download_streaming.py` if separate process tests make the existing file clearer.

**Interfaces:**
- Consume Task 1 AsyncFileResponse for Git stdout with `size=None`, `finish=stream.finish`, and `close=stream.close`.
- Consume `prepare_download_response` for a factory that acquires the Git process and constructs its response together. The Task 2 ASGI wrapper owns this preparation through request exit. Do not leave a separate unprotected process-acquisition await.
- Extend GitProcessStream with `reader` exposing its standard binary stdout, and blocking `finish()` validating natural process completion.
- Retain existing streaming helper entry points and path/ref validation. Preserve any existing GitProcessStream.read callers.
- `close()` is idempotent and owns reader closure plus producer termination/reaping. Download Git processes start in a dedicated session/process group. On interrupted delivery, signal that owned group before waiting for an outstanding stdout read or closing its buffered reader. Use bounded termination grace with group kill fallback, including helpers that survive the Git leader, then reap the direct child. Generic GitProcessStream callers without an explicitly owned group terminate only their child. Task 1 invokes this callback on the separate cleanup executor, before waiting for pending read/finish operations. Coordinate concurrent finish/close without serializing termination behind the blocking operation it must interrupt.
- Track acceptable command exit outcomes: normally zero; git diff --no-index also accepts the normal differences outcome (one). A real failed command raises GitError before successful transfer termination.
- `_git_download_response` accepts HEAD state and builds the shared response. Working-tree downloads pass HEAD to Task 2's disk response. HEAD never drains process output.

- [ ] **Step 1: Add failing Git tests.** Cover progressive large blob/patch delivery, absent Content-Length, valid empty patches, untracked-file diff exit one, nonzero failure before or after partial output, and preserved deleted-file fallbacks. Verify HEAD without output reads and all existing validation failures.
- [ ] **Step 2: Add failing process-lifecycle tests.** Drive ASGI disconnect/read failure/send failure while output is pending and while sending is paused. Assert stdout closure and process reaping, including cancellation during acquisition, initial-header send failure, and cancellation during completion validation. Verify cleanup when the default executor is saturated, repeated cleanup, and terminate/kill fallback. Include a real Git external diff helper through production source preparation and ASGI cancellation; verify helper termination, reader closure, worker settlement, and bounded cleanup even when the helper ignores termination after the Git leader exits. Test exact normal content with real small Git repos; use controlled subprocess sources for failure and blocking cases.
- [ ] **Step 3: Verify RED.** Run focused new Git streaming tests before source changes and record the expected failures.
- [ ] **Step 4: Integrate Git streaming.** Expose stdout to aiofiles and keep process ownership in GitProcessStream. Validate completion before ending a successful body. Avoid waiting for process exit before draining its output on successful GET. Coordinate interrupted reads and cleanup without blocking the event loop. Preserve current MIME/filename/cache semantics and no temporary blob/patch files.
- [ ] **Step 5: Verify GREEN and combined regressions.** Run `timeout -k 5 240 uv run pytest tests/test_http_streams.py tests/test_file_downloads.py tests/test_file_download_streaming.py tests/test_git_download_streaming.py tests/test_gzip_middleware.py tests/test_artifact_broker_html.py tests/test_composer_attachments_api.py -q` (omit the optional test filename if not created). Add any specifically affected regression module discovered during implementation. Run focused lint for the changed Python files. Confirm no synchronous-iterator adaptation warnings on included routes.
- [ ] **Step 6: Commit.** Stage only task edits and commit `fix(downloads): stream git output asynchronously`, with body and required trailer. Report actual commands/results and spec acceptance coverage. The controller then runs independent whole-change review.

## Adversarial plan review outcome

Round 1 identifies three distinct Important gaps: initial-header send cleanup, producer cleanup ordering/executor starvation, and standalone metadata work on the event loop.
The plan adds request-scoped ASGI cleanup ownership, protected source preparation, explicit cleanup ordering, a separate cleanup executor, and off-loop download metadata validation.
Each correction has focused regression tests assigned to its owning task.
Both independent reviewers re-read the revised plan in round 2 and report no remaining findings.
Implementation resumes under the user's existing execution mandate.
