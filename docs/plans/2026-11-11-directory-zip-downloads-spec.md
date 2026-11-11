# Directory ZIP downloads

**Date:** 2026-11-11
**Status:** Adversarial specification review complete. Implementation has not started.
**Scope:** Download a real filesystem directory as a progressively generated ZIP archive.

## 1. Purpose and context

The user can download files from the Files and Artifacts context menus.
The user also needs to download a directory and its complete contents.

Existing file and Git downloads now use asynchronous HTTP streaming under Django ASGI.
They provide bounded reads, request-scoped source ownership, and cleanup after cancellation or delivery failure.
Directory downloads build on that infrastructure.

Generate the archive during its HTTP delivery.
Keep filesystem work and compression outside the asyncio event loop.
Do not require a system `zip` executable or another Python dependency.

This specification defines observable behavior and constraints.
The implementation plan selects modules, interfaces, adapters, synchronization, and exact buffer sizes.
Specification review does not authorize implementation.

## 2. Scope and action availability

Offer a directory-download action when an existing Files-mode tree node represents a real directory on disk.
This includes directories inside the Artifacts tree and ordinary project/session Files directories.
The action downloads the directory itself, not only the currently visible tree entries.

| Surface or node | Directory-download action |
| --- | --- |
| Real directory node in Files | Available |
| Real directory inside Artifacts | Available |
| Artifacts root, including an existing backing directory | Unavailable |
| Synthetic root, root selector, group, or non-path node | Unavailable |
| Missing lazy root | Unavailable; do not create it for a download |
| Git index or commit tree directory | Unavailable |
| Ordinary file | Keep the existing file-download action |

Use the existing context menu and browser download interaction.
The action must identify that a directory downloads as ZIP.
Read-only directories remain downloadable when their contents are readable.
Write permission is not a download requirement.

Root exclusions in the table define UI action availability, not a new filesystem access-control policy.
An archive request must still identify and authorize a real directory on disk.
Do not add Git directory archives, revision archives, or downloads of synthetic collections.

## 3. Approach and analysis decisions

| Approach | Trade-off | Decision |
| --- | --- | --- |
| System `zip` subprocess | Reuses process streaming, but requires an executable on each installation | Rejected |
| Third-party Python streaming ZIP library | Adds a dependency; investigated convenience APIs also need lifecycle and symlink adaptation | Not required |
| Standard-library `zipfile` with asynchronous delivery | Needs a small streaming integration; works with TwiCC's existing Python runtime | Selected |

Python's `zipfile` supports writing to a non-seekable stream and writing entry contents incrementally.
Use standard Deflate compression for regular file contents and standard ZIP64 support when required.
Do not implement a ZIP encoder or a new response stack.
Do not precompute the compressed archive to obtain its size.

The following choices come from the completed analysis and are made explicit by this specification:

- Include the selected directory as one top-level directory in the archive.
- Preserve symlinks as symlink entries instead of traversing their targets.
- Fail on unreadable or unsupported entries instead of silently omitting them.

The user requires all contents and delegates symlink handling to the archive solution.
These choices preserve links without adding traversal outside the selected tree or special cycle-handling rules.

## 4. Archive contents

### C1. Complete traversal

For a stable readable directory, include every regular file, directory, and symlink below it.
Include the selected directory itself, including when it is empty.
Keep nested empty directories.

Do not apply Files-tree visibility, search, depth, lazy-loading, or node-count limits.
Do not apply `.gitignore`, hidden-file settings, extension filters, or size filters.
For example, `.git`, `.env`, ignored files, and `node_modules` are included when present.

Do not require the frontend to enumerate the contents.
The backend reads the filesystem independently of the displayed tree.

### C2. Names and layout

For a directory named `reports`, send `reports.zip` with member paths under `reports/`.
Preserve Unicode names, relative subdirectory structure, and file bytes.
Archive member paths must be relative and must not include the directory's absolute host path.
Do not flatten directories or replace distinct paths with content deduplication.

Use a stable nonempty name such as `root` for a filesystem root without a final path component.
Member order is not part of the contract.

### C3. Symlinks

Store the link's target text and its symlink entry type in the ZIP.
Do not read the target's file content or recurse through a linked directory.
Preserve relative targets, absolute targets, broken links, and links that form cycles.
Preserve links pointing outside the selected directory as links, without including their external targets.

This requirement concerns archive representation.
It does not guarantee that every extraction client recreates symlinks on every operating system.

### C4. Metadata and special entries

Represent directory and symlink types using standard ZIP metadata.
Preserve ordinary executable permission information and modification times where the format supports them.
ZIP timestamp precision and supported-date normalization are acceptable.
Exact ownership, ACLs, extended attributes, and a complete filesystem backup are outside this feature.

FIFO, socket, and device entries are not ordinary files supported by this download.
An encountered unsupported entry fails the archive request or transfer.
Do not open such an entry as a regular file or silently skip it.

## 5. HTTP and access behavior

### H1. Explicit download and existing checks

Generate a ZIP only for an explicit directory-download request.
Preserve authentication, project/session ownership, allowed roots, and existing confinement checks for the requested source.
Reject a missing source or a source that is not an eligible real directory.
Do not create directories as a side effect of downloading.

Endpoint selection belongs to implementation planning.
Existing raw download routes may be extended if their other semantics remain intact.
In particular, preserve inline directory handling and artifact data-store requests.
No new filesystem permission model or snapshot security mechanism is required.

### H2. Download response

Return a ZIP attachment with media type `application/zip` and the archive filename from C2.
Use Django's normal filename encoding, including Unicode.
Preserve the existing download cache policy and `X-Content-Type-Options: nosniff`.
Keep the archive outside HTTP gzip compression.

Omit `Content-Length` because the compressed size is unknown during generation.
The browser may therefore show received bytes without a total-size percentage.
Keep the existing same-origin browser download mechanism and native Android download protocol.

Do not add range requests, resumable downloads, retries, or a new progress interface.

### H3. HEAD

Where the archive route accepts `HEAD`, return attachment metadata without a response body.
Do not read file contents, generate compressed contents, or traverse the complete directory to answer `HEAD`.
Do not calculate the archive length.
Release any resource acquired while preparing the response.

### H4. Preparation and delivery errors

Validate and inspect the requested directory before committing a successful download response.
Preparation failures produce normal HTTP errors, with application-owned messages using existing i18n conventions.
This validation does not require scanning every descendant before delivery starts.

If traversal, metadata inspection, opening, reading, or compression fails, fail the transfer.
Do not quietly continue with a valid but incomplete archive.
After headers are sent, abort delivery rather than send a new HTTP error body.
Do not append error text to ZIP bytes or signal successful HTTP stream completion after a failure.

## 6. Streaming and resource behavior

### S1. Progressive generation

Send ZIP bytes while the archive is still being generated.
Do not collect the complete archive in memory or materialize it on disk before delivery.
Do not require a complete tree walk before the first response body bytes.
Use asynchronous HTTP iteration so Django does not buffer a synchronous response iterator.

### S2. Bounded content buffering and backpressure

Read and emit bounded content blocks.
The existing download block size is a suitable starting point; the plan selects the exact value.
Do not read whole source files or accumulate the compressed contents of a large file before sending them.

Source consumption follows HTTP response consumption, with bounded buffering between them.
A slow client must not cause the backend to generate the complete archive ahead of delivery.
Compression can consume several input blocks before it produces another nonempty output block.
An empty intermediate compression result must not be mistaken for archive EOF.

ZIP metadata, including the final central directory, may require memory proportional to the number of entries.
Content buffering must remain bounded independently of total file sizes.
Do not claim constant total memory independently of entry count.

### S3. Event-loop availability

Keep blocking traversal, filesystem metadata operations, source reads, compression, and cleanup outside the event loop.
Unrelated requests and asyncio tasks must progress during a download.
Threaded Deflate can use the existing Python runtime: CPython releases the GIL during its `zlib` compression calls.
This is not a hard operating-system latency or CPU-isolation guarantee.

### S4. Ownership and cancellation

Use the existing asynchronous download lifecycle and protected preparation behavior where applicable.
Give every opened source, archive writer, and iterator a defined owner until cleanup completes.
Release them after completion, preparation failure, traversal/read/compression failure, send failure, or disconnect.
Also release them when a prepared response closes before body iteration or while sending is paused.
Repeated cleanup must be safe.

Account for cancellation during preparation and during a worker operation.
A worker that completes acquisition after cancellation must not leave an unowned resource.
Coordinate iterator advancement and closure so cleanup does not close an executing generator concurrently.
After cancellation is observed, stop requesting additional source blocks and entries.
An already-running operating-system call may settle before its source can close safely.
Forcibly interrupting a non-returning filesystem call is outside the contract.

Archive finalization during cleanup must not resume delivery or turn a failed transfer into a successful response.
Do not leave background archive generation running after the request no longer consumes it.
Do not create a new job service, persistent archive cache, subprocess pipeline, or worker-management subsystem.

### S5. Mutable directories

This feature does not lock files or create a filesystem snapshot.
The directory can change while traversal and delivery proceed.
Entry discovery and file contents can therefore reflect different moments.

Observed failures, including a discovered entry disappearing before it can be opened, follow H4.
Changes that cause no observed error can produce a valid archive with the bytes actually read.
The feature does not guarantee an atomic directory listing or stable file content during concurrent edits.

## 7. Compatibility and excluded work

- Keep ordinary file, Git blob, and Git patch downloads unchanged.
- Keep inline previews, artifact broker behavior, composer attachment streaming, and file modifications unchanged.
- Keep Artifacts root and synthetic-root action behavior unchanged.
- Add directory ZIP actions only to the Files mode of the shared tree.
- Do not introduce new frontend enumeration, native Android code, external executables, or package dependencies.
- Do not add archive encryption, format selection, compression controls, archive previews, or background preparation workflows.
- Localize all new interface labels, tooltips, and user-facing error messages through existing i18n mechanisms.

## 8. Acceptance criteria

Validate generated archives by opening them with an independent ZIP reader and checking member content and metadata.
Exercise the real Django ASGI response path for progressive delivery and lifecycle evidence.
Isolated generator checks alone do not establish HTTP streaming behavior.

| Criterion | Required evidence |
| --- | --- |
| A1. Action scope | Real Files/Artifacts directories offer ZIP; Artifacts roots, synthetic nodes, missing lazy roots, and Git directories do not |
| A2. Real source and access | Missing or ineligible sources fail; project/session/standalone authentication and confinement remain intact; read-only readable directories work |
| A3. Full contents | Include hidden and ignored files, paths absent from the lazy tree, nested content, and empty directories |
| A4. Archive layout | Stable top-level directory, relative paths, Unicode names, exact regular-file bytes, and a valid empty-directory archive |
| A5. Symlinks | Preserve normal, broken, cyclic, and external links without reading or traversing their targets |
| A6. Progressive delivery | ASGI emits initial ZIP bytes before the complete source is consumed or the complete tree is walked |
| A7. Bounds and backpressure | Large compressible and incompressible sources use bounded blocks and bounded aggregate retained content; increasing source sizes does not increase content-memory bounds; paused delivery permits only bounded read-ahead, not continued consumption with time |
| A8. Event loop | A delayed read or compression operation allows unrelated asyncio work to progress |
| A9. HTTP | Correct attachment filename, Unicode encoding, media type, cache/nosniff headers, omitted length, and no HTTP gzip |
| A10. HEAD and early close | No content reads, compression, or complete tree traversal for HEAD; source ownership also holds before iteration |
| A11. Source failures | Unreadable descendants, failed traversal/read/compression, vanished discovered entries, and unsupported special entries abort without silent omission or successful completion |
| A12. Lifecycle | Completion, pending-read disconnect, paused-send disconnect, initial-header send failure, and body-send failure release resources |
| A13. Races | Late acquisition after cancellation, outstanding worker operations, and repeated cleanup do not lose ownership or close an executing iterator concurrently |
| A14. ZIP64 | Large-member and many-entry paths use valid standard ZIP64 structures when needed; validation distinguishes simulated boundaries from actual large-file runs |
| A15. Regression scope | Existing file/Git downloads, inline preview, artifact data-store behavior, and excluded tree actions retain their behavior |

Representative project, session, and standalone routes must be covered.
Verify that included downloads produce no Django synchronous-iterator adaptation warnings.
Use deterministic synchronization for cancellation evidence rather than relying on timing guesses.
For A7, bounded block sizes alone are insufficient evidence: account for all retained content and pending generated output.
Exercise paused delivery and sources larger than the permitted content buffers, distinguishing ZIP metadata from payload memory.

## 9. References and analysis evidence

- `src/twicc/http_streams.py`: existing async response, protected preparation, and ASGI request cleanup.
- `src/twicc/views.py`: project/session and standalone raw serving, plus artifact data-store dispatch.
- `frontend/src/components/files/FileTreeContextMenu.vue`: shared Files/Git action availability.
- `frontend/src/components/files/FileTreePanel.vue`: context-menu ownership and download triggering.
- `frontend/src/views/SessionView.vue`: Artifacts root configuration.
- `frontend/src/utils/download.js`: same-origin browser download mechanism.
- `docs/plans/2026-11-11-async-file-downloads-spec.md`: existing download streaming contract.
- [Python 3.13 zipfile documentation](https://docs.python.org/3.13/library/zipfile.html): non-seekable output, incremental member writes, and ZIP64.
- [CPython zlib implementation](https://github.com/python/cpython/blob/3.13/Modules/zlibmodule.c): GIL release around Deflate calls.
- [zipstream-ng](https://github.com/pR0Ps/zipstream-ng): investigated alternative, not a selected dependency.

Throwaway probes run on CPython 3.13.14 before this specification.
They verify standard-library archive generation through the actual `AsyncFileResponse`, bounded blocks, source closure, read-error propagation, and cancellation coordination.
They also verify Unicode, empty directories, symlink representation, traversal-error propagation, HEAD, and a ZIP64 header with a declared large source size.
The ZIP64 probe does not write a multi-gigabyte source.
These probes are feasibility evidence, not completed feature implementation or a substitute for the ASGI acceptance criteria.

## 10. Adversarial review boundary

Review this document as a specification, not an implementation plan or code change.
Challenge missing requirements, contradictions, scope errors, unsupported compatibility claims, and untestable acceptance criteria.
Check whether a builder can derive a small coherent implementation without inventing user-visible behavior.

Do not request class names, signatures, module splits, lock types, executor settings, exact block sizes, or fixture code.
Those choices belong to implementation planning.
Request additional detail only when different choices change observable behavior, access policy, resource guarantees, or feasibility.

## 11. Review outcome

Round 1 uses two independent internal reviewers with separate contract and lifecycle lenses.
Both reviews explicitly exclude implementation-detail requirements.

The contract reviewer finds no substantive issue.
The lifecycle reviewer identifies one acceptance gap: bounded individual blocks do not prove bounded aggregate content memory or read-ahead.
A7 now requires evidence for retained content, scaling with source size, and bounded read-ahead during paused delivery.
The streaming contract remains unchanged.

Both reviewers re-read the revised specification in round 2.
Both report no remaining substantive specification finding.
The lifecycle reviewer confirms that the aggregate-memory and read-ahead acceptance gap is addressed.
The contract review also checks current tree classification and raw-route behavior against the stated scope.
Neither review adds implementation-detail requirements.

No product code, dependency, or implementation plan changes are part of this specification task.
