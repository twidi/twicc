# Async file downloads

**Date:** 2026-11-11
**Status:** Adversarial specification review complete. User authorizes implementation on 2026-11-11.
**Scope:** Existing file and patch downloads through Django under ASGI.

## 1. Purpose

Send downloads progressively without loading the complete response body into backend memory.
Keep filesystem reads and process waits outside the asyncio event loop.
Use `aiofiles` for asynchronous file reads.

The user selects this work before directory downloads.
ZIP creation and directory downloads belong to a separate specification.

This document defines behavior, constraints, and acceptance criteria.
The implementation plan selects classes, function signatures, modules, and cleanup mechanisms.

## 2. Current behavior

TwiCC uses Django 6.1.2 under ASGI.
The relevant download endpoints return `FileResponse` over a synchronous reader.
Django adapts this iterator by collecting its complete content in a thread before sending the body.
The thread avoids direct filesystem reads on the event loop, but the complete body occupies memory.

The frontend starts downloads with a same-origin anchor through `triggerDownload()`.
The response supplies the filename through `Content-Disposition`.
Files and Artifacts downloads use the raw-file routes with `download=1` or `download=true`.
The download branch bypasses HTML broker injection.

Git downloads have two source types:

- A regular file in the working tree.
- A Git process producing a commit blob or patch through stdout.

Composer attachment content already uses an async iterator with bounded reads.
It does not need this correction.
TwiCC's gzip middleware already excludes streaming responses.

## 3. Scope

### Included downloads

| Surface | Source | Route family |
| --- | --- | --- |
| Files | A regular file on disk | Project and session `file-raw`, with the download flag |
| Artifacts and standalone file browsing | A regular file on disk | Standalone `file-raw`, with the download flag |
| Git working tree | A regular file on disk | `git-file-download`, with `ref=index` |
| Git revision | Commit blob, including the existing deleted-file fallback | `git-file-download` |
| Git patch | Index or commit patch | `git-diff-download` |

Both project-scoped and session-scoped Git routes are included.
Any existing caller using these download routes receives the same correction.

### Excluded work

- ZIP files generated from directories and all new directory-download actions.
- Changes to menus, browser download triggers, or native Android download code.
- Migration of inline previews, artifact assets, project icons, public media, or static bundles.
- Migration of composer attachment content, which already streams asynchronously.
- New HTTP range support, download resumption, retries, or progress interfaces.
- A new ASGI handler, external file server, or reverse-proxy requirement.
- New path permissions, symlink policies, or changes to existing access checks.

Some included and excluded responses share helpers today.
Changing an included download must not implicitly migrate excluded responses.

## 4. Approach

| Approach | Trade-off | Decision |
| --- | --- | --- |
| Keep synchronous `FileResponse` | Django buffers the complete body under ASGI | Rejected |
| Use `aiofiles` with Django's async streaming response interface | Small application integration for headers and resource ownership | Selected direction |
| Adopt another ASGI response stack or replace Django's handler | Broader integration and compatibility work | Rejected for this scope |

Use a compatible published `aiofiles` version with Python 3.13 support.
Declare it as a direct backend dependency and update the lockfile.
The implementation plan selects the version constraint.

Use Django's standard `StreamingHttpResponse` async interface.
Keep the existing routing, middleware, and frontend download protocol.

`aiofiles` provides async wrappers around file operations executed in a thread pool.
It does not provide the complete Django response or resource-lifecycle contract.
It can adapt standard opened binary files, including a Git process's stdout reader.
The application remains responsible for the Git process itself.

## 5. Streaming contract

### S1. Progressive delivery

Begin sending content before reading or generating the complete source.
Do not collect the source into a complete bytes object, list, or temporary file before delivery.
Use an async body iterator, so Django does not invoke its synchronous-iterator buffering fallback.

### S2. Bounded reads and buffering

Read binary content in bounded blocks.
The proposed default block size is 64 KiB, matching the existing attachment streaming behavior.
The exact block size remains an implementation choice.

Do not iterate files by lines or use an unbounded read for their bodies.
A large file containing no newline must still use bounded blocks.

Application body buffering must remain bounded independently of source size.
Concurrent downloads may consume one bounded streaming state each.
This requirement excludes operating-system caches and buffering outside TwiCC's control.

### S3. Slow clients

Source consumption must follow response consumption.
A slow client must not cause TwiCC to read the complete source ahead into memory.
The same requirement applies to Git process output.
Bounded library, process-pipe, and transport buffers are allowed.

### S4. Event-loop availability

Filesystem reads, blocking filesystem metadata operations, and process waits must not execute on the event loop.
Downloading a large file must allow unrelated requests and asyncio tasks to progress.
This is not a hard latency guarantee for the operating system or storage device.

## 6. HTTP and content compatibility

### H1. Existing request semantics

Preserve routes, query parameters, authentication, project/session ownership, and path validation.
Preserve allowed request methods and existing not-found or access-denied behavior.
Continue to reject directories as file downloads.
This change does not expand the set of accessible files or Git refs.

### H2. Content and filename

For a source that does not change during delivery, send exactly its current download bytes.
Preserve Git revision selection, deleted-file fallbacks, and patch generation semantics.
Keep downloaded HTML unchanged, without broker scripts or preview CSP injection.

Preserve the existing media type and attachment filename behavior.
Support Unicode filenames through Django's normal `Content-Disposition` encoding.
Preserve existing cache directives and `X-Content-Type-Options: nosniff`.

### H3. Known and unknown lengths

For a disk file, derive `Content-Length` from the opened file being served.
Do not calculate it from a later lookup of the pathname.
Do not send more bytes than the announced length if the file grows during delivery.

Git process output may omit `Content-Length` when its size is unknown.
Do not pre-read a process's output merely to calculate its length.

Empty files remain valid downloads with an empty body and the correct filename.
Keep streaming responses outside HTTP gzip compression, as they are today.

### H4. HEAD

Where a route accepts `HEAD`, return the corresponding response metadata without a response body.
For a disk file, include the same length and filename as `GET` for the same unchanged source.
Do not read the file body or drain Git output to answer `HEAD`.
Any resource acquired while preparing `HEAD` must be released.

### H5. Mutable sources and failures

This change does not create filesystem snapshots or lock files against agent edits.
In-place edits can affect bytes that the server has not read yet.

Once a file is opened for delivery, continue using that file rather than reopening its pathname during iteration.
A replacement at the same pathname must not switch the download to a different file midway.

Complete source validation and opening before committing a successful download response.
For Git, preserve the existing source/ref validation before delivery.
Preparation failures must retain normal error responses rather than become empty successful downloads.

A source that shrinks or fails during delivery can produce an incomplete download.
After response headers are sent, a failure cannot become a new HTTP error response.
Terminate the failed transfer and clean up its resources.
Do not append an error message to the downloaded bytes or advertise missing bytes as successfully delivered.

For Git output, EOF alone does not prove that source generation succeeds.
A failed Git command must not complete a download as successful, including after producing partial output.
Preserve valid command outcomes, including the differences result from `git diff --no-index`.
Valid empty patches remain successful downloads.

## 7. Resource lifecycle

### L1. Ownership

Give every acquired file and process a defined owner until cleanup completes.
Release resources after successful completion, preparation failure, read failure, send failure, or client disconnect.
Also cover a response closed before its body iterator starts.
Cleanup must tolerate multiple close paths without closing an unrelated resource or repeating process ownership actions.

### L2. Cancellation

Account for cancellation during source acquisition, reading, and network sending.
Cancelling an await does not necessarily stop the filesystem operation running in a worker thread.
If acquisition completes after cancellation, the acquired resource still needs cleanup.

Coordinate outstanding reads with cleanup.
Do not leave files open or block the event loop while waiting for an operation to settle.
Release resources when outstanding operations permit safe cleanup.
This specification does not require forcibly interrupting an operating-system filesystem call.

### L3. Git processes

For Git-backed downloads, release both the stdout reader and the child process.
An interrupted download must not leave Git blocked on a full pipe or leave an unreaped child process.
Cleanup must handle interruption during a pending read and during a pending network send.

The implementation plan selects process termination, waiting, and escalation mechanisms.
No new temporary disk materialization of blobs or patches is needed.

## 8. Acceptance criteria

Validation must exercise the real ASGI response path, not only concatenate a response iterator in a unit test.
Small integration tests can cover bytes, headers, routing, and existing errors.
Controlled large-source tests cover progressive reads and cleanup.

| Criterion | Required evidence |
| --- | --- |
| A1. Progressive file download | ASGI emits a first body block while the rest of the source remains unread |
| A2. Bounded file reads | Large binary and newline-free files never require an unbounded body read |
| A3. Slow-client behavior | Delaying delivery does not cause complete source consumption or growing application body buffering |
| A4. Event-loop availability | An unrelated asyncio task or request progresses while a source read is delayed |
| A5. Complete content | Stable disk files, Git blobs, and patches arrive with their expected bytes |
| A6. HTTP compatibility | Filename, Unicode encoding, media type, cache headers, HTML download bytes, and access errors remain correct |
| A7. Known and unknown lengths | Disk-file lengths are correct; process output streams without a pre-read for length |
| A8. HEAD and empty files | HEAD has no body reads; empty GET downloads complete correctly |
| A9. Source mutation | Path replacement does not switch the opened source; file growth does not exceed the announced length |
| A10. Cleanup | Completion, early response close, preparation/read/send failure, and disconnect release owned resources |
| A11. Cancellation races | Cancellation during acquisition or reading does not lose ownership of a later-completing resource |
| A12. Git delivery | A large blob or patch starts delivery before its output is fully consumed; interrupted delivery releases its process |
| A13. Shared-helper isolation | Inline previews and other excluded helper callers keep their current behavior |
| A14. Source failure | A truncated disk source or failed Git command does not complete as successful; valid empty patches and difference results remain successful |

Use representative project, session, and standalone routes for scope coverage.
Exercise disconnects during both source reads and response sends.
Verify that Django's synchronous-iterator adaptation warning does not occur for included downloads.

The frontend keeps its existing same-origin anchor behavior.
The HTTP contract remains usable by desktop browsers and the current Android download handling.

## 9. Code references

These references describe the current code. They do not prescribe the final module layout.

- `src/twicc/views.py`: `_raw_file_response`, `_raw_serve_response`, `file_raw`, and `standalone_file_raw`.
- `src/twicc/views.py`: `_git_download_response`, `git_file_download`, and `git_diff_download`.
- `src/twicc/git.py`: `GitProcessStream` and Git download source creation.
- `src/twicc/core/services/attachments/views.py`: the existing async `FileStream` and its ownership behavior.
- `src/twicc/gzip_middleware.py`: streaming responses retain their length and avoid HTTP gzip compression.
- `frontend/src/utils/download.js`: `triggerDownload` and the existing download URLs.
- `tests/test_file_downloads.py`: current file and patch download coverage.
- `tests/test_composer_attachments_api.py`: bounded reads and disconnect tests for the existing attachment stream.
- `tests/test_uploads_transfer.py`: examples of tests through Django's real `ASGIHandler`.

External references:

- [Django 6.1 responses and ASGI file serving](https://docs.djangoproject.com/en/6.1/ref/request-response/#fileresponse-objects).
- [aiofiles documentation](https://github.com/Tinche/aiofiles).
- [aiofiles opened-file adapters](https://github.com/Tinche/aiofiles/blob/v25.1.0/src/aiofiles/threadpool/__init__.py).
- [aiofiles iteration uses readline](https://github.com/Tinche/aiofiles/blob/v25.1.0/src/aiofiles/base.py).

## 10. Specification review boundary

Review this document as a specification, not as an implementation plan or code change.
Challenge missing requirements, contradictions, scope mistakes, unsupported compatibility claims, and untestable acceptance criteria.

Do not require class names, function signatures, lock choices, executor settings, exact block sizes, or test-fixture designs.
Those decisions belong to implementation planning.
Only request more detail when different choices change the required behavior or make this specification infeasible.

Review completion does not authorize implementation.

## 11. Review outcome

Two independent internal reviewers complete two rounds of adversarial specification review.
Their mandate excludes implementation details and focuses on requirements, compatibility, scope, and acceptance criteria.

Round 1 identifies one substantive acceptance gap: Git can fail after emitting partial output without a read exception.
H5 now defines this failure outcome explicitly.
A14 verifies source failures while preserving valid empty patches and `git diff --no-index` difference results.

Both reviewers re-read the revised specification in round 2.
Both report no remaining substantive specification findings.
No product code changes or runtime validation are part of this specification work.
