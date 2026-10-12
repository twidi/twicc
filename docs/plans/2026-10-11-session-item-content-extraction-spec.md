# Session item content storage and cold compression

Date: 2026-10-11.
Status: consolidated specification, adversarially reviewed; ready for implementation planning. Documentation only; no implementation is authorized by this document.
Source inventory: [impact audit](2026-10-11-session-item-content-extraction-audit.md).

## 1. Outcome and scope

Move JSON record strings out of `SessionItem` into a shared content table with immutable logical strings.
Keep item identity, line numbering, metadata, and external payloads unchanged.
Reduce the size of item rows and the cost of future item-table operations.

Existing contents move through the existing provider background compute system.
Recent sessions become available before older sessions, as they do today.
New ingestion writes directly to the new storage from the first release.

This specification covers schema, ingestion, transformations, progressive extraction, readers, serialization, cold eligibility, compression, decompression, dictionaries, ownership, maintenance, and validation.
It includes initial sync, live sync, recompute, and Codex history replacement.

Outside this release:

- New paginated-rollout or fork-resolution behavior.
- Content garbage collection or deletion.
- Physical removal of the legacy column.
- Vacuum or automatic database compaction.
- Recovery of unavailable legacy originals from provider files.
- A new compute transport or strict whole-worker memory bound.

Logical fork items remain separate. Equal strings can share content rows without changing `line_num`.

## 2. Schema

### SessionItemContent

| Field | Contract |
|---|---|
| `id` | Numeric primary key, using `BigAutoField`. |
| `sha256` | Unique `BinaryField`, containing a 32-byte SHA256 digest. |
| `payload` | Non-null binary field. The only payload column; exact UTF-8 bytes or their lossless compressed representation. |
| `codec` | Explicit plaintext or zstd representation state. |
| `dictionary` | Nullable protected FK to an immutable dictionary row. Required for a dictionary-dependent representation; NULL for plaintext. |
| `raw_size` | Exact uncompressed UTF-8 byte length. |
| `session_ids` | JSON list of unique current referencing session IDs, covering both item FKs. |
| `compression_attempt_version` | Nullable identity of the compression policy last tried, including unsuccessful attempts. |
| `compression_no_gain` | Whether that attempt cannot reduce payload size. Independent of the current codec state. |

Calculate the digest from the exact logical string encoded as UTF-8, before compression.
Do not parse or normalize JSON for hashing.
Do not change whitespace, escaping, key order, or malformed JSON during extraction.

The source string is the normalized ingestion output used today: decoded and stripped by the existing reader.
It is not necessarily identical to the original file bytes.
The content table preserves that existing ingestion contract.

Logical decoded content and its hash are immutable after insertion.
Payload representation, ownership metadata, and compression-attempt metadata may change.
Transformations insert or reuse another row; they never update shared logical text in place.
Each resulting string has its own digest. No original-to-result uniqueness rule exists.

Reuse content rows through their hash and resolve their numeric PKs before assigning item references.
Bulk writes must handle duplicate hashes and concurrent/conflicting insertion safely.
Hash reuse and equality checks work for plaintext and compressed rows.
A newly created row starts plaintext unless the progressive compute contract safely establishes cold compression eligibility.
An encountered digest with different text is an invariant failure; never silently reuse different text.

### SessionItem

| Field | Contract |
|---|---|
| Existing `content` | Retained legacy field. New writes use `''`; extraction progressively clears old values. |
| `current_content` | Nullable FK to `SessionItemContent`, `db_index=False`, `on_delete=PROTECT`. |
| `original_content` | Nullable FK to `SessionItemContent`, `db_index=False`, `on_delete=PROTECT`. |

Do not create child FK indexes in this release or schedule their later creation.
Forward content reads use the content PK. Hash deduplication uses the unique hash index.

### Session

Add `content_storage_version` as a nullable `IntegerField`, without a non-null default or index.
The first extracted storage format has version `1`.
NULL denotes unverified or incomplete placement of this session's items in the new storage.

This marker describes storage placement, not original provenance or computed metadata readiness.
Provider compute versions remain the metadata readiness mechanism.

## 3. Source provenance

The original FK always references the known source, even when the current string is unchanged.
NULL means the original is unavailable or unverified.
The original FK is preserved across all later recomputes.

| Item state | Original | Current | Legacy field |
|---|---|---|---|
| Newly ingested known source, unchanged | Source PK | Source PK | `''` |
| Known source, transformed | Source PK | Result PK | `''` |
| Extracted legacy item, source unavailable | NULL | Current PK | `''` |
| Legacy item awaiting extraction | NULL | NULL | Existing string |

Two FK values pointing to one content row do not duplicate text.
Comparing the IDs determines whether a known source differs from the current result without fetching either text.

New file ingestion knows its source before any TwiCC transformation.
Legacy extraction conservatively treats the existing stored string as current content, not as an exact native original.
Do not infer source provenance from missing enrichment keys or reconstructed JSON.

Keep embedded original/debug fields in current JSON. Existing repair and rendering code still uses them.
Captured `originalFile` and `original_files` describe pre-edit files, not the original JSON record.

## 4. Schema migration and upgrade contract

Django migration performs schema work only:

1. Create the content and dictionary tables, including the unique content hash index.
   Seed only the small bundled immutable dictionaries. This is fixed reference data, not content conversion.
2. Add the two nullable FK columns, without indexes or non-null defaults.
3. Add the nullable session storage marker.

No `RunPython` data conversion, item-table rebuild, global content scan, or vacuum runs at startup.
Use the dedicated migration backend's proven-null-column support, already merged on `main`.
Validate generated operations and actual SQLite behavior on a disposable representative database.

Increase the Claude Code and Codex compute versions for this release.
Extraction runs through their existing compute coordinators, writer ownership, and retry handling.

The runtime bridge remains able to read legacy and extracted items together.
An upgrade that skips this release must still be able to process inline legacy contents.
No later unconditional startup migration may remove the column before progressive extraction completes.
This release does not implement the later removal or maintenance protocol.

## 5. New ingestion

### Shared initial-sync writers

Keep producer payloads as their existing `(line_num, string)` records.
Perform content-table writes in the serialized DB writer, not producer threads.

For session creation and append:

1. Insert or reuse each source string.
2. Assign both original and current FK to its PK.
3. Bulk-create items with empty legacy content.
4. Commit content rows, items, and tracking offsets within the existing transaction boundary.

Preserve existing `ignore_conflicts` semantics.
A duplicate item must not replace an existing enriched current string or original reference.
Unused content rows from ignored item conflicts may remain for future garbage collection.

A newly created session containing only new-storage items can receive storage version `1` in the same transaction.
This does not mark its metadata compute complete.
Appending new rows to an old session does not upgrade its storage marker while old rows remain.

### Live sync

Capture the source string before generic/provider transforms and out-of-band cache enrichment.
Retain the existing transformation order and same-batch resolver evidence.

Persist:

- Original FK to the captured source row.
- Current FK to the final enriched row, or the same source row when unchanged.
- Empty legacy content.

Use the existing live-state transaction for rows, tracking, metadata, and cache ownership.
Rollback and retry must retain existing original-file cache behavior.
Broadcast only committed effective current content.

Unsaved live items need explicit in-memory source/current values until PK assignment.
Content readers must support that state without querying a nonexistent relation.

## 6. Recompute and progressive extraction

### Worker responsibilities

Read effective current content for each item, including mixed storage.
Run existing transformations from current content, not automatically from original content.
This preserves live-only enrichment and persisted task snapshots.

Keep the existing transformed-override transport for this release.
Do not add unchanged legacy strings to the worker result or IPC messages.
Include the observed history generation and item/session boundary needed to reject stale apply results.

For changed items, carry enough input identity to detect an obsolete overwrite.
An existing current PK or the digest of the legacy input can supply that identity.
The worker does not allocate content rows or write extraction progress to the database.

### Writer apply order

Use the existing compute apply lease and synchronous executor:

1. Validate run ownership and session revision/generation.
2. Apply metadata chunks through their existing path.
3. Apply transformed overrides in bounded transactions.
4. Sweep remaining legacy items in bounded storage-only transactions.
5. Verify storage completion and revision in the final transaction.
6. Apply final links/session metadata and publish storage and compute versions after initial representation reconciliation.
7. Use existing successful-completion broadcasts.

Both small and large compute results follow this ordering.
The small-result shortcut must not skip extraction or replay already consumed overrides in final apply.

### Transformed overrides

Validate the input identity and history generation before assigning a result.
Insert or reuse the result content row and update current FK.
Preserve original FK, including NULL for legacy items with unavailable originals.
Set the legacy field to `''` in the same transaction.

Do not replay transformations in the writer. Provider state and ephemeral caches can change their output.

### Legacy storage sweep

Read legacy strings directly in writer transactions after applying overrides.
Use ascending keyset pagination on the existing `(session, line_num)` index and an observed upper line boundary.
Advance the cursor; do not restart the scan at line 1 for each chunk.

For each candidate with no current FK:

1. Preserve its exact stored string, including malformed JSON or an empty string.
2. Insert or reuse the corresponding content row.
3. Assign current FK and clear legacy text atomically.
4. Preserve original FK without inventing source provenance.

If a current FK and legacy text coexist, current FK is authoritative.
Clear the redundant legacy value without replacing either reference.
New-storage rows are skipped.

Bound each transaction by both row count and encoded-content bytes.
Enforce budgets while reading; do not materialize a whole session before applying limits.
Allow one oversized row so the process always advances.
The memory bound includes that largest row and bounded hashing/insertion buffers.

Storage-only slices do not change costs, activity, offsets, groups, facts, links, or compute version.
They do not need the metadata path's aggregate contribution scans.
Return only small cursor/counter values to the event loop.

Keep newest-first scheduling. Finish each recent session before advancing to older sessions.
Do not add an instance-wide conversion pass before serving requests.

### Memory scope

Extraction adds no full-history string payload to compute IPC.
Existing changed-string override accumulation remains an explicit limitation.
This release does not introduce a spool, streaming result protocol, or whole-worker memory redesign.
Consider those separately only if measurements establish a need.

## 7. Completion, interruptions, and concurrency

Obtain the write lock through the existing revision guard before reads in each apply transaction.
Validate `history_epoch` as well as the observed session boundary.
Offset alone cannot identify a history replacement that resets tracking.
Preserve existing guard semantics for unrelated callers; do not silently alter its comparison globally.

Reject missing sessions, abandoned runs, superseded revisions, generation changes, or conflicting item inputs.
Do not mark these results complete.

| Event | Required result |
|---|---|
| Failure before transaction commit | Item reference and legacy clear both roll back. |
| Failure after a storage chunk commits | Moved rows remain readable. Session remains outdated until final apply succeeds. |
| Retry after interruption | Traverse again and skip moved rows. Preserve existing original references. |
| Live append before apply ownership | Reject obsolete result through revision handling. New items already use new storage. |
| History replacement | Reject old-generation results, even if offsets or line numbers match again. |
| Item/session deletion | Retained shared content rows remain valid for other references. |
| Known unchanged source transformed by a later version | Original remains the same source PK; current changes. |

The writer lease still spans result application, while SQLite transactions commit per chunk.
This preserves current serialization behavior; chunking does not promise concurrent writer access within that lease.

Before setting storage version `1`, verify all extant items in the authoritative session boundary have current references.
Verify their legacy text is cleared. Do not scan all sessions to set one session's marker.
Handle empty sessions explicitly: storage completion is vacuously valid once their generation is validated.
Publish compute readiness only after all required chunks and final metadata apply succeed.
The initial compute also compresses eligible contents as specified in section 18.
Cold eligibility uses the authoritative final lifecycle fields, or their guarded effective values, before representation reconciliation.
Final metadata publication cannot certify plaintext readiness after making a compressed owner hot.
It does not wait for the first periodic maintenance pass.

## 8. Codex history replacement

Format detection must read effective content before extraction completes.
Preserve the existing replacement crash marker, history-epoch increment, and compute/search resets.
Reset the storage marker when old items are removed.

Replacement chunks insert raw strings directly into the content table, with both FKs referencing their known source.
Keep the existing item chunk and tracking transaction boundaries.
The replacement can mark storage placement complete after all replacement chunks succeed and are verified.
It must not mark metadata compute complete.

Do not delete old content rows or reconnect old enrichment by guesswork.
Extraction preserves storage, not a new policy for recovering enrichment after history replacement.

## 9. Effective content access

### Storage selection

For an item with a current FK, the exact decoded referenced string is authoritative.
Explicit loaders decode compressed payloads and load their dictionary identity when required.
Otherwise, use the retained legacy string during transition.
Original content is never the default source for existing API, search, parser, or compute consumers.

Distinguish an unloaded value, an empty string, an unsaved in-memory value, and a missing referenced row.
Use an explicit unloaded sentinel; do not use string truthiness to choose storage.
A missing referenced content row is an invariant failure, not permission to return obsolete legacy text.
A session marked storage-complete cannot silently contain an item without a current FK.

### Loaders and async boundaries

Provide explicit effective-current access and loading helpers.
A pure accessor returns an in-memory current override or an already loaded effective string; it performs no SQL.
If required data is unloaded, it fails clearly rather than triggering hidden relation reads.

Synchronous callers can use an explicit targeted loader.
Batch callers can choose joins, targeted queries, or bounded distinct-PK lookups.
Load original text only for an explicit original-content operation.
Never install an always-joined manager or systematically fetch content on metadata queries.

Compare actual elapsed time, allocation, and bytes fetched for representative workloads.
Query count alone does not determine the best SQLite strategy.
Per-item relation caching does not deduplicate content objects across items; a batch PK map can do that.
Keep caches scoped to the page/operation, not an unbounded global map.

In async endpoints, load required strings inside an explicit async loader or the existing synchronous database boundary.
Serialize only already loaded data outside that boundary.
Serializers must not execute lazy synchronous FK queries from async code.

Changing current FK or an in-memory current string must invalidate the associated loaded/parsed value.
The in-memory transformed value takes precedence until it is persisted.

### Predicates and projections

For plaintext-ready sessions, replace legacy content filters with effective-current text predicates:

```text
(current FK exists AND referenced content matches)
OR
(current FK is NULL AND legacy content matches)
```

Preserve CLI AND-combination of repeated terms and existing case sensitivity, escaping, paging, and line order.
Do not search obsolete legacy text when a current FK exists.
Do not replace raw JSON substring matching with extracted-message matching.
Binary plaintext storage must preserve existing text matching semantics, including Unicode and case behavior.
The public cold/warming gate in section 19 applies before either substring operation.
Internal predicates must remain complete for compressed contents through bounded decoded candidate reads.
A SQL predicate over compressed bytes is never a valid content predicate.

Adapt `values()`, `values_list()`, `only()`, `defer()`, and raw SQL separately from accessors.
Preserve tuple shapes and aliases expected by history resolvers and watchers.
Joined `.only()` loaders must include connector fields.
Projections choose storage by FK presence; a fallback must not hide a missing referenced row.

Metadata paths continue deferring legacy text while it can remain large.
They fetch neither current nor original content text.

## 10. External contracts

Physical extraction does not change existing payload structures.
Resolve the effective current string, then apply existing consumer-specific formatting.

| Consumer | Contract |
|---|---|
| Full item serializer, REST, WebSocket, shares, frontend | Existing `content` field remains a JSON string. |
| CLI `session content`, including MCP/RPC invocation | Existing `content` field remains parsed JSON. |
| CLI `session messages` and wait helpers | Existing extracted message fields and classification remain unchanged. |
| Metadata-only serializers and responses | No content field and no content-text loading. |
| Search/title/provider parsers | Consume the same effective current strings as before. |

Do not expose FK IDs, hashes, or nested content-row objects as replacements for `content`.
Do not return a loading placeholder as an empty successful content string.
Keep frontend `parsedContent.js`, synthetic items, cache invalidation, and line-based rendering contracts unchanged.
Keep search document identity and existing share visibility/freeze/readiness rules unchanged.

## 11. Impact checklist

Use the audit's file/function inventory as the implementation checklist.
Repeat its searches against the implementation checkout before declaring coverage complete.

| Area | Required change |
|---|---|
| Models and schema migration | Add content/dictionary models, two item FK fields, storage marker, and any bounded readiness state without item indexes or data conversion. |
| Initial-sync DB writer | Bulk source reuse and item FK assignment for create/append. |
| Shared live compute | Capture source before all transforms; persist final current content inside current transaction. |
| Background worker/apply | Mixed decoded reads, transformed references, writer-side sweep, initial cold compression, generation guards, final completion. |
| Codex rollout replacement | Mixed format detection, direct new storage, marker reset/completion. |
| Resolvers and provider helpers | Effective strings for earlier records, projected history, task state, images, spawn/tool lookups. |
| API/WS/share serializers | Explicit loaded current content; unchanged wire structures and async safety. |
| CLI/MCP/search/title | Effective raw filters, parsing, messages, indexing, prompt extraction, and wait behavior. |
| Workflow synthesis and watcher side effects | Effective filters/projections for launch arguments, process state, goals, and notifications. |
| Data maintenance and lifecycle mutation | Shared root cold rule, exact membership, progressive compression/promotion, restart recovery, existing cleanup policies. |
| Dictionary generation and packaging | Reviewed public-only generator, immutable provider assets, durable dictionary seeding. |
| Tests and diagnostic scripts | Mixed storage/codec fixtures, reader/writer assertions, ownership/transition recovery, and relevant workload probes. |

Cost/activity aggregates, facts, links, workflow envelopes, and session metadata keep their separate storage.
They only change where they actually consume item text.
Do not modify historical migrations to redirect runtime content reads.

## 12. Deletion and maintenance

Deleting items or sessions does not delete content rows.
History reconstruction, Guardian cleanup, and `sync --reset` retain content rows under this release's policy.
Shared contents remain valid for surviving items.
Ownership memberships are reconciled after reference removal as specified in section 17.
No global unindexed reverse-reference scan runs for each removed item.

Clearing legacy text can release SQLite pages for reuse; it does not promise a smaller database file.
Content retention and temporary mixed storage can also increase disk use during conversion.
Do not claim immediate file-size reduction without compaction measurements.

Future garbage collection must consider both FK references.
Future physical removal/compaction must run through a separate validated maintenance process.
Neither operation runs automatically at startup in this release.

## 13. Acceptance criteria

### Schema and availability

- Schema addition performs no existing-data conversion, item-table rebuild, child FK index creation, or unnecessary FK scan.
- Representative migration duration is measured on a disposable copy using the proven SQLite backup method.
- Startup proceeds to existing initial sync and newest-first background compute without a global extraction pass.
- Provider compute targets gate metadata readiness; storage marker completion remains separate.
- New initial/live/replacement writes never populate legacy text.

### Content integrity and provenance

- Equal exact strings across items/sessions yield one content row and distinct logical item identities.
- Different serialized strings remain different contents even when their parsed JSON is equivalent.
- Malformed JSON and empty stored strings survive extraction unchanged.
- Known raw sources have original references before transformation and across restarts/recomputes.
- Legacy unavailable originals remain NULL, including after later transformations.
- One original can support different current results on different items.
- Current shared logical strings never change in place; representation updates preserve exact decoded bytes and hash.
- Original-file, task, attachment, screenshot, and debug enrichment remains usable after recompute.

### Transactions and retries

- FK assignment and legacy clearing commit together or roll back together.
- Live rollback retains existing cache ownership and retry evidence.
- Duplicate item insertion does not overwrite existing current/original references.
- Interrupted extraction resumes across mixed storage without duplicate logical items or lost text.
- Stale run, offset/input conflict, deletion, and history-generation changes cannot publish false completion.
- Both small and chunked compute results move unchanged legacy items before final readiness.
- Storage sweep adds no unchanged-content strings to compute IPC and uses bounded row/byte slices.

### Readers and consumers

- Full API/WS/share payloads preserve raw string `content`; CLI content preserves parsed JSON.
- Metadata-only paths load no content text, including original text.
- Mixed filters prefer current FK; ready non-cold public filters preserve matching/paging semantics.
- Internal features remain complete on cold compressed history; public cold substring calls fail explicitly.
- Async serialization performs no hidden synchronous relation query.
- Resolvers work across mixed storage and current-batch in-memory items.
- Search, title, CLI/MCP, wait, workflow, and provider watcher behavior passes targeted regression tests.
- Item/session deletion preserves shared content rows.

### Upgrade compatibility

- An older inline database can upgrade directly to the new release without an intermediate extraction release.
- Partial conversion remains readable and resumable after process restart.
- Column removal and vacuum remain absent from startup migration operations.

## 14. Implementation choices and validation

These choices do not change the contracts above:

- Exact row/byte budgets for storage slices.
- Targeted, batched, or joined loading for each content-needed workload.
- Internal accessor names and placement of shared storage helpers.
- Error/reporting details for missing content rows and stale inputs.
- Efficient final per-session storage-completeness checks.

Validate choices against real workloads. Use isolated database copies for data-dependent measurements.
Preserve the current serialized writer architecture and avoid unrelated compute redesign.
The audit identifies relevant tests and source anchors; it remains the supporting implementation inventory.

Writing this specification changes documentation only. It does not create migrations, run compute, or alter a database.

## 15. Cold eligibility and session trees

Expose pure `Session.is_cold_for_content` and `Session.is_cold_for_scratch` properties.
Both inspect explicitly loaded fields and cached native parents without queries.
Do not add a stored or generated cold column: elapsed time changes eligibility without a database write.
Batch operations use the matching predicate with projected state and one evaluation time.

For a root session:

1. Take the latest non-NULL value of `created_at`, `last_started_at`, `last_updated_at`, and `last_stopped_at`.
2. Content becomes cold after fifteen days when the current root is archived OR hidden.
3. Content becomes cold after thirty days for every other root, including visible, nonarchived roots.
4. Scratch becomes cold only when the current root is archived OR hidden and inactive for thirty days.
5. Missing lifecycle fields or unknown dates cannot authorize either cold policy.
6. Archive and hide timestamps do not affect these thresholds.

A subagent inherits the root's cold status at every depth.
Its own flags and timestamps do not establish a separate cold status.
Use the native `parent_session` subagent relationship, including nested descendants.
Separate `spawned_by`/`spawn_root` orchestration sessions retain independent lifecycle eligibility.
That orchestration relationship alone does not make a session inherit cold status or require promotion of another session.
A fork that is an independent root has its own cold status.
An unresolved or inconsistent root cannot authorize compression or public substring-search readiness.
Requested-content reads remain available through decoding.
Late parent resolution, reparenting, and ancestry removal invalidate affected tree readiness and reevaluate representation requirements.

Compression also checks active-agent protection for the whole root tree.
An active descendant prevents compression even if the date-and-flag property still says cold.
Required promotion on active/resumed work cannot wait for daily maintenance.
This protection is an operation guard, not a second definition of the pure properties.

Scratch cleanup uses its independent thirty-day predicate and inherited native root status for session users.
Its batch resolver cannot consume content eligibility.
Retain its filesystem ownership checks, shared-folder checks, and active-work protection.
Empty directory age rules, orphan scratch rules, and draft attachment retention remain distinct.
Do not replace those rules with session-cold eligibility.

## 16. Representation and dictionary contracts

Store contents in SQLite, with independent lossless compression per content row.
Use one binary payload column; promotion and compression replace that column atomically.
Never retain a second plaintext or compressed payload column.
Empty and malformed JSON contents use the same exact-byte contract as valid JSON.

The initial compression policy uses zstd level 3.
Each provider has one bundled public dictionary, with a maximum size of 16 KiB.
Use raw public-template dictionaries, not locally trained private dictionaries.
A content shared across providers still has one representation and one dictionary identity.
Choose one applicable provider dictionary deterministically; mixed-provider sharing does not duplicate the content.
Decoding always uses the stored identity, never the current session's provider.

Each immutable dictionary row records provider, version, format, SHA256, and exact bytes.
Seed the release assets during schema migration before dependent representations can be written.
The small seed operation is allowed; scanning or converting existing item data in migrations is not.
A newer dictionary receives a new identity. Retain old dictionary bytes while dependent contents exist.
Dictionary deletion is protected; content garbage collection remains out of scope.

Generate release assets offline through the reviewed policy in `scripts/content_dictionary_policy.json`.
Use one random whole-database sample and separate provider corpora, without per-session dictionaries or quotas.
No tool results, thinking, reasoning, or user/assistant prose enters dictionary generation.
Only explicit public keys and per-field/provider constants survive sanitization.
Other scalar values become fixed synthetic values; unknown keys and typed blocks disappear.
Dictionary generation exports no private literal values or private-reference dictionary.
The installed application never trains dictionaries on user data.

A compression-policy identity covers codec, level, dictionary, and the byte-savings decision rule.
Record the policy tried and whether it gave no gain.
If compressed payload bytes are not smaller than plaintext UTF-8 bytes, retain plaintext and mark no gain.
Skip that unsuccessful policy on later maintenance passes.
A different policy permits a new trial.
A successful earlier attempt must not prevent recompression after a hot-to-cold transition.
Compression failures are errors, not successful no-gain attempts.

Missing dictionaries, invalid codec state, corrupt frames, size mismatch, and hash mismatch are explicit integrity failures.
Never fall back to stale legacy text or silently discard a row.
Decode preserves the exact logical bytes, length, and digest.
Readers and caches must not retain stale representation metadata across a replacement.

## 17. Content ownership

`session_ids` is the exact set of extant sessions with an item referencing the content through either FK.
Store actual session IDs, not only root IDs; resolve their roots for eligibility.
Original=current and repeated items count as one membership for that session.
Item FKs remain the authoritative graph. The JSON list is derived metadata for eligibility.
No separate mapping table or child content-FK index is introduced.

A content can be compressed only when every actual owner belongs to a cold, inactive root tree.
Any hot owner prevents new compression. Promotion of an explicitly targeted owner makes shared content plaintext.
Unknown, incomplete, or inconsistent ownership cannot authorize compression.
Unused retained content rows do not become cold candidates merely because their owner list is empty.

Membership additions commit with persisted FK assignments.
Ignored duplicate item insertion does not add membership for a reference that never persists.
Removal of one reference removes a membership only when that session has no remaining reference through either FK.
Use session-scoped reconciliation, covering transformations, deletion, reset, and history replacement.
Do not run a full-database reverse scan for each content or item mutation.
Any multi-transaction reconciliation must retain conservative memberships and block affected compression until complete.
Missing live memberships are never an allowed intermediate state.

History replacement reconciles the final reference graph, including reuse of the same content in replacement items.
Deleting a root does not remove memberships of surviving descendants by assumption.
Deleted session IDs must not remain permanent compression blockers after reconciliation completes.
Extraction builds and verifies membership progressively through its existing writer ownership.
Interrupted ownership work resumes without falsely declaring the affected representation eligible.

## 18. Initial conversion and ongoing maintenance

The first provider compute after upgrade performs extraction and initial cold compression together.
Do not write a mandatory full plaintext copy and defer all eligible compression to daily maintenance.
Compress newly extracted eligible rows directly when complete ownership and root eligibility are established safely.
Existing or shared rows require representation reconciliation under the same all-owners rule.
Unknown ownership keeps the row plaintext; later bounded reconciliation can establish eligibility.

Compute completion verifies extraction placement and required hot plaintext readiness for that session.
It also processes safely eligible cold candidates discovered for that session before publishing completion.
This does not promise that every cold row in the database compresses before recent sessions become available.
Other sessions may remain unconverted; shared ownership may delay compression until reconciliation is complete.
No whole-database ownership scan blocks startup or newest-first session availability.

Evolve `session_dirs_cleanup_task` into data maintenance while preserving its existing cleanup work.
The first periodic pass runs thirty minutes after startup; later passes run every twenty-four hours.
The pass handles cold compression, pending promotion recovery, and ownership reconciliation.
It also detects eligibility changes caused by elapsed time, not only explicit flag changes.

Maintenance performs bounded work and yields to interactive writer operations.
CPU encoding/decoding does not hold a long database write transaction.
Do not extend the cleanup's agent-start exclusion guard over an entire database compression pass.
Compression publication rechecks row identity, ownership completeness, root eligibility, and active protection under writer coordination.

**Authorized amendment, 2026-10-12 (Task 17):** Persistent plaintext promotion does not require per-content compression eligibility proofs.
It verifies observed payload, codec, dictionary identity, sha256, and raw_size under storage admission and writer ownership.
Decode verifies exact UTF-8 bytes before publication.

Root incarnation/epoch still fence mutations. Task 19 scopes reference traversal and readiness to the target session.
Both FK slots, ownership, immutable dictionary identity, bounded budgets, and cancellation semantics remain unchanged.
Compression retains every existing owner/root cold guard.

Task 19 below supersedes the original native-tree readiness scope.

A hot reference or tree transition invalidates an obsolete compression result.

Compression changes payload, codec, dictionary reference, and attempt metadata atomically.
Promotion changes payload, codec, and dictionary reference atomically while preserving attempt metadata.
A crash leaves a valid old or new representation, never partially written bytes.

Unarchive, unhide, resume, activity update, and tree changes reevaluate effective root eligibility.
Removing only one flag does not require promotion if the other flag keeps the inactive root cold.
A newly hot session or a targeted hot reference triggers promotion of that actual session promptly.
Promotion outranks optional compression and does not wait thirty minutes or twenty-four hours.
Deduplicate obsolete work. Recover pending root targets after restart without a global blocking startup conversion.
Interrupted native-child promotion requires a new explicit resume or targeted write after restart.
Lifecycle mutation applies root cold eligibility to every descendant, but queues only the changed session for promotion.
Promotion uses both FK columns and includes originals, even when ordinary reads use current content only.

Preserve worktree isolation and the existing filesystem cleanup disable behavior.
A cleanup guard or filesystem disable flag must not prevent required content promotion and recovery.
Exact budgets, queues, worker placement, and scheduling APIs belong to the implementation plan.

## 19. Reads, warming, and search

A cold session remains readable through all existing authorized range, item, attachment, share, compute, and provider paths.
Decode only the requested distinct contents, not the whole cold session for a page read.
A temporary read leaves the stored payload compressed.
Do not persist plaintext or update a read timestamp merely because a cold content is read.
Any decoded memory cache is bounded by bytes and includes a limit for oversized entries.
No cache is required for initial correctness; benchmarks favor temporary decode over a persistent DB read cache.

Promotion makes both references of the actual target session persistently plaintext.
Root resume does not promote its native descendants. Their existing compressed rows can remain compressed under inherited hot policy.
Shared rows become plaintext for every owner when any targeted session promotes them.
A transition may temporarily enter warming while bounded promotion completes.
Normal reads remain available through the decoder during warming.
Neither unarchive nor the entire session interface waits for full synchronous promotion.

Public `session content --contains` and `session messages --contains` validate the effective root state before candidate selection.
Reject cold roots explicitly, even if a requested range happens to contain only plaintext rows.
Root queries use a current root certificate as the fast path.
An uncertified root validates its own plaintext metadata without writing a certificate.
Native-child queries validate complete extraction and both FK codecs in the same read transaction as substring SQL.
A plain child succeeds even when root readiness or root extraction remains pending.
A compressed or incomplete child returns `warming`, even when its root certificate is ready.
Metadata validation reads no payload bytes and uses bounded pages, with query cost proportional to child item count.
Substring search stays read-only and never requests promotion.
Unresolved or inconsistent native ancestry produces an explicit not-ready error, rather than assumed hot/cold readiness.
Readiness validation covers late parent resolution and tree changes before accepting a substring query.
Do not silently return only plaintext matches, partial counts, or incomplete pages.
Ready non-cold roots retain current matching, AND terms, case behavior, ranges, total counts, pagination, and tail semantics.
Apply the same gate through CLI, RPC, and MCP, with help/schema/error documentation updated together.
A new hot reference invalidates readiness until its required plaintext placement is established.
Readiness is recoverable and distinct from metadata compute version and extraction placement.

Global Tantivy search keeps its existing indexing and archive/hidden filters.
Compression neither deletes documents nor changes message extraction or search identity.
Following an indexed result decodes only the requested lines.
No new cold decode-and-scan public search option is introduced.

Mandatory internal content predicates remain supported for cold and warming sessions.
Cover task reconstruction, screenshots, spawn resolution, workflow synthesis/linking, and attachment lookup.
Use available metadata to narrow candidates only when complete; incomplete facts require a decoded fallback.
Preserve match order and limits after matching. Never apply an old post-filter limit before decoding candidate matches.
Do not assume SQL contains over binary plaintext has the old text semantics without validation.
Historical migrations continue running in their existing order before extraction; do not retroactively rewrite them for codecs.

## 20. Additional acceptance criteria

- Direct upgrade and interrupted extraction preserve mixed legacy/plaintext/compressed reads and exact payloads.
- First compute handles cold compression progressively, before periodic maintenance, without a global startup scan.
- All nested subagents inherit root cold status; a separate fork root retains independent eligibility.
- A hot owner through either FK prevents compression of shared content.
- Membership survives duplicate inserts, repeated references, transformation, deletion, reset, and history replacement.
- Partial ownership reconciliation prevents compression and recovers after restart.
- Cold-to-hot transitions promote only the actual target session progressively; normal reads remain available.
- Cold/warming substring calls return explicit errors; ready hot calls preserve Unicode matching and pagination.
- Internal task/image/workflow/spawn features work on cold compressed and partially computed history.
- Compression publication rejects stale ownership, root state, active protection, or representation identity.
- No-gain attempts are skipped for the same policy; a policy update can retry; successful promoted rows can recompress.
- Dictionary assets contain only reviewed public/synthetic material; installed users perform no training.
- Old dictionary identities remain decodable after new assets ship.
- Missing/corrupt dictionaries and contents fail explicitly without stale fallback.
- Maintenance preserves thirty-minute/daily cadence and existing distinct filesystem cleanup rules.
- Content conversion, dictionary seeding, list maintenance, and representation updates are measured on isolated copies.
- Measurements include migration duration, ownership metadata/WAL overhead, shared-content updates, large-record reads, and bounded writer responsiveness.
- No startup vacuum, item-column removal, child FK index build, or data-conversion migration is introduced.

## 21. Supporting evidence and planning boundary

The extraction audit remains the reader/writer inventory.
Its section 11 adds the compression integration investigation and identifies required engineering design/validation tasks.
In particular, active-admission fencing, final-date ordering, and grouped ownership cost are not established by specification review.
The audit section 11.13 preserves the useful compression measurements and public dictionary validation.
These are component measurements, not whole-application performance guarantees.
The ownership and filter investigation decisions are consolidated here; their intermediate reports are removed.

The plan must cover all sections as one coherent storage change.
Exact field/helper names beyond the external contracts, transaction budgets, loader strategies, SQL expressions, and worker APIs remain plan/design choices.
Dictionary release bytes must be generated, frozen, versioned, and packaged during implementation preparation.
No further product decision is needed to choose the selected policy's first dictionary assets.

## 22. Adversarial specification review

Two independent internal reviewers review this consolidated specification on 2026-10-11.
The scope is completeness, contradictions, data integrity, progressive availability, and recovery at specification level.
Reviewers do not require implementation APIs, exact chunk constants, detailed SQL, or unrelated storage redesign.

The initial round identifies three required clarifications: native versus orchestration ancestry, unresolved ancestry/readiness, and combined archived/hidden transitions.
The specification incorporates all three clarifications.
Both reviewers recheck the final document and report no remaining specification-level blockers.
The earlier extraction-only review is separate and does not substitute for this consolidated review.

The review outcome and resolved findings are retained here; the separate review report is removed.
This verdict supports planning; it does not establish implementation correctness or measured end-to-end performance.

## Byte representation preparation and worker cooperation

Maintenance and extraction reconciliation verify exact source bytes before changing representation.
They retain the verified size and digest instead of decoding text, re-encoding text, and hashing again.
Strict incremental UTF-8 validation uses bounded chunks and carries decoder state between chunks.
The requested-content text API still rejects invalid UTF-8.

Worker `time.sleep(0)` calls release the GIL between validation chunks and after each prepared content.
These calls provide scheduling opportunities without promising a maximum event-loop delay.
The existing coordinator yield, bounded batches, one oversized-row progress, writer admission, and cancellation drain remain intact.

Dictionary reuse remains bounded and operation-local.
New operations reload and validate dictionary bytes.
Already-selected compressed frames retain their exact verified representation.
No-gain or missing-dictionary skips publish no representation and require no representation transform.
Compression preparation uses the same observed source row as its publication proof.
Publication retains all owner/root cold checks, policy checks, exact source CAS, and readiness/incarnation/epoch fences.


## Authorized target-only promotion amendment — 2026-10-12 (Task 19)

This amendment replaces native-subtree promotion and subtree-wide readiness.
Cold compression eligibility remains inherited from the native root at every depth.
The content thresholds remain 15 days for archived/hidden roots and 30 days for other roots.
Scratch policy, all-owner compression guards, CPU yielding, and source CAS remain unchanged.

A promotion snapshot contains the target ID/incarnation and native root ID/incarnation/epoch.
Every existing ownership and tree mutation still invalidates the old/new native root generations.
Promotion queues use the actual changed session ID, separate from these root mutation fences.
Root readiness certifies only root contents. Historical whole-tree certificates remain sufficient for their root's own contents.
Child search never trusts a parent certificate or an old child certificate.
Native-child resume uses the same lifecycle path and promotes only that child's own references.
Startup recovery discovers root targets only; it never queues all inherited-hot children.
No durable child promotion-demand state, schema change, or new process is introduced.
Ordinary reads decode only requested contents temporarily. Tantivy search remains available.

Recovery filters each raw root page with persisted cold policy and no active override.
An active child alone cannot warm its cold root. Cold pages still advance the raw ID cursor to later hot roots.
Explicit target callbacks retain admitted active eligibility.

Recovery provenance remains operation-local in the target queue and promotion snapshot.
Snapshot capture under admission, preparation, and publication recheck persisted hot policy without active override for recovery-only roots.
A cold-threshold crossing between awaits cannot create root demand from an active descendant.
An explicit target enqueue supersedes recovery provenance and retains normal active eligibility.
Root incarnation/epoch and target incarnation fences remain unchanged.
