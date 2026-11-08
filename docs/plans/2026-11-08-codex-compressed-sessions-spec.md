# Codex compressed sessions specification

Status: Ready for user review after adversarial specification review. Implementation is not authorized by this document.

## Intent

Codex can replace a session's JSONL rollout with a Zstandard-compressed file.
TwiCC treats both files as representations of the same session.
Compression must not make a session disappear, lose its history, or become stale.

This document specifies behavior and acceptance criteria.
It does not prescribe functions, queue structures, cache designs, or decompression algorithms.

## Requirements and scope

The user requires these behaviors:

- Discover and read Codex sessions stored as `rollout-*.jsonl.zst`.
- Keep existing sessions when Codex compresses their source files.
- Update their stored source paths to the available representation.
- Prevent false `stale` states caused by compression.
- Handle Codex restoring a JSONL file when a session resumes.
- Preserve existing session behavior, including history, subagents, and continued ingestion.

These requirements apply at startup and during normal operation.
They also apply when TwiCC misses a transition while stopped.

Support covers the existing configured Codex sessions root.
It uses the provider home selected through the existing provider-home configuration.
This change does not add discovery of `archived_sessions`, other compression formats, or arbitrary file relocations.
It must not regress existing behavior for plain JSONL files or other providers.

TwiCC must not permanently decompress or rewrite Codex files merely to read them.
Codex retains ownership of source compression, restoration, and migration.
Existing authorized Codex operations can still modify those files.

## Verified Codex behavior

The analysis checks upstream commit `748ab55ca7de0b647d9a7a9d9a8e70e00f28666d` and the `rust-v0.167.0` tag.
The latter matches TwiCC's documented bundled runtime version at analysis time.

| Operation | Source behavior |
|---|---|
| Compression | Publishes `.jsonl.zst`, then removes `.jsonl`. Preserves the source modification time. |
| History read | Reads decompressed bytes without restoring the durable JSONL file. |
| Active resume | Restores `.jsonl`, removes `.zst`, and opens the JSONL for append. Refreshes its modification time. |
| Rollout migration | Can replace compressed content and retain the compressed representation. |
| Both representations present | Prefers the plain JSONL file. |

Compression preserves the original JSONL bytes.
Codex does not append session records directly to the compressed representation.
Its current worker requires seven days without modification and excludes rollouts with active writer ownership at publication.
TwiCC must not depend on that delay for correctness.

Sources in the local Codex checkout:

- `codex-rs/rollout/src/compression.rs`: publication, restoration, representation selection, and compression eligibility.
- `codex-rs/rollout/src/recorder.rs`: resume and append behavior.
- `codex-rs/rollout/src/compression_tests.rs`: compressed reads and resume restoration assertions.
- `codex-rs/rollout/src/seekable_reader.rs`: reads using offsets in decompressed JSONL bytes.
- `codex-rs/thread-store/src/local/rollout_migration.rs`: migration of compressed sources.

## Identity and source location

### Three distinct concepts

| Concept | Meaning |
|---|---|
| Session identity | The Codex session ID declared by the rollout's own `session_meta`. |
| Logical source path | The rollout path with only the final `.zst` suffix removed, when present. |
| Physical source path | The available `.jsonl` or `.jsonl.zst` file selected for reading. |

The two representations share one logical source path and one session identity.
They must never create two TwiCC sessions or two copies of the same imported history.

`Session.file_path` continues to describe the selected physical path, relative to the existing provider root.
TwiCC updates that field in both transition directions.
A representation change must not change the session's ID, project, or parent relationship.

Matching must preserve the existing authority of the rollout's own `session_meta` ID.
A new source association must not attach content that declares another session identity.
Two unrelated logical paths declaring the same ID are not automatically a compression transition.
This specification does not introduce a policy for reconciling arbitrary duplicate rollouts.
The new compression-specific association repair applies only to exact sibling representations.
Other duplicate-rollout scenarios retain existing generic handling and its current limitations.

### Representation selection

TwiCC resolves both representations, even when the stored path names the representation that just disappeared.
When both files exist, the plain JSONL file takes precedence, as in Codex.
An older event or background task must not overwrite a newer association with a representation that is no longer selected.

Successful reconciliation eventually persists the selected physical path.
This requirement applies even when no new session items exist.
It also applies when the previous path is already marked stale.

## Offsets and content continuity

`last_offset` always counts bytes in the decompressed JSONL content.
This meaning applies to both source representations.
It never counts compressed bytes, text characters, or JSON records.
`last_line` retains its existing meaning for imported session items.

A pure representation change preserves both checkpoints.
After Codex restores the JSONL, ingestion continues from the same logical offset.
If new records appear before reconciliation completes, TwiCC imports them once under the existing session identity.

Every source-size decision must use compatible units.
This includes growth, truncation, backlog, drain targets, and checks that wait for a final response.
The compressed file size must never be compared with a decompressed offset.

Representation metadata can help avoid unnecessary reads.
However, a frame header does not prove payload integrity or describe every possible multi-frame input.
Support must include frames without a declared content size and valid concatenated frames.

TwiCC must distinguish a representation change from a real content replacement.
Changing the inode, extension, or compressed size alone does not establish content replacement.
Conversely, keeping the same extension or modification time does not establish content continuity.

Existing detection of truncation and source replacement must not become weaker.
Real migrations and replacements continue through the existing rebuild rules.
This requirement does not introduce mandatory full-history hashing on every source observation.
It does not add full-history comparisons against DB content or detection of arbitrary interior edits.
The verified in-place rewrite is legacy rollout migration, which TwiCC already handles.
Paginated revert creates a different rollout and keeps the previous file intact.
Supporting those different logical paths is outside this compression feature.
The user explicitly requests no special handling for paginated revert.
This feature adds no revert detection, current-rollout lookup in Codex SQLite, history reconciliation, or special revert exclusion.
Existing generic behavior remains in place, including its current limitations.

## Startup and recovery

The initial inventory recognizes both representations and selects one source per logical rollout.
An existing session whose representation changes follows the existing-session path, rather than a new-session import.

Reconciliation must persist the source association independently of content ingestion.
An unchanged source, unchanged modification time, or empty incremental read must not suppress the path update.
The normal serialized DB write rules continue to apply.

Startup must repair sessions previously marked stale solely because TwiCC ignored their compressed source.
It must preserve their existing identity and user data.
It must also handle sessions whose last successful import stops before the compressed source's logical end.
Those sessions receive their missing records without duplicating the imported prefix.

A source-related `rollout_missing` diagnosis must recover after a valid matching source becomes available.
Unrelated migration or content errors must not be cleared merely because a sibling file exists.
Interrupted rebuilds, including an offset-zero rebuild marker, retain their existing recovery semantics.

An interrupted reconciliation must be safe to repeat.
A restart must recover through ordinary inventory and reconciliation, without a manual data repair or special migration command.

## Watcher and concurrent transitions

Compression and restoration can occur while the watcher processes another event or reads a source.
TwiCC must handle added, modified, and deleted notifications in any order.
Duplicate notifications must not duplicate items or reverse a completed association update.

A deleted notification for one representation is not proof that the session disappears.
Before marking the session stale, TwiCC checks the logical source's available representations.
A normal representation transition must not publish a transient false `stale` state.
If both representations are genuinely absent, existing deletion behavior remains available.

Temporary compression, restoration, and migration files are not sessions.
If both durable representations exist, TwiCC selects the JSONL without importing both.

Source generations and drain completion must describe logical JSONL progress consistently.
Pending ingestion, `process_path()` waits, and migration replay must survive a representation transition or receive a recoverable outcome.
They must not hang, report uncommitted records as imported, or treat compressed newlines as JSONL boundaries.

An admitted read must not combine bytes from different representations or different content versions.
If continuity cannot be established, TwiCC retries or follows the existing replacement and rebuild behavior.
A temporary path race must not erase history or become a permanent missing-source diagnosis.

The watcher must also discover compressed files when the sessions directory first appears after startup.
Representation handling must preserve existing event-loop responsiveness and fairness between sessions.

## Readers, compute, and migration

All source-reading paths must accept either selected representation where they read Codex rollouts.
This includes:

- Session metadata and provider-internal rollout classification.
- New-session import and incremental catch-up.
- Watcher source observation and bounded ingestion.
- Compute preparation and source history-mode checks.
- Migration preflight and full-history replacement.
- Migration report validation, replay, and registration recovery.
- Source-backlog checks used by response waiting.

These paths must apply the same representation precedence and identity rules.
A migration report must not fail solely because Codex selects the equivalent sibling representation.
Thread identity and logical source validation must remain enforced.

Response polling must not decompress sources or schedule a new source-preparation lifecycle.
It uses available logical-size evidence and retains the existing unknown-source fallback when size is unavailable.
A first-frame size can prove positive backlog, but cannot establish EOF for a concatenated stream.
Active resume restores JSONL, so normal active-session backlog checks retain their existing byte-size comparison.

A compressed source can contain a real migration or replacement.
Its representation does not exempt it from history-mode validation or the existing coordination with active agents.
Existing per-session migration gates and DB writer ownership remain effective.

Viewing history already stored in the DB does not require decompression of the source file.
This change does not alter TwiCC's independent compression of stored item content.

## Integrity and failure behavior

TwiCC distinguishes source absence from unreadable or invalid source content.
An existing corrupted or inaccessible compressed file must not be described as a deleted session solely because parsing fails.
Previously imported history and user data remain preserved.
Failures remain confined to the affected source and follow existing error-reporting conventions.

Compressed ingestion must detect truncated frames and decompression failures.
A decoder returning bytes without an exception is not sufficient proof of a complete frame.
New items and advanced checkpoints must not be published from compressed input whose integrity has not been validated.

Metadata-only observations need not decode the entire payload.
They must not certify complete ingestion or clear an unrelated content failure.
Existing JSONL rules for incomplete trailing records remain applicable after decompression.

A failed source must not abort ingestion of unrelated sessions.
It must not produce an endless immediate retry loop.
Recovery follows normal source changes and startup reconciliation.
New application-owned interface messages, if required, use the existing i18n mechanism.

## Preservation and resource constraints

A pure representation change preserves:

- Session identity, project, parent, and orchestration relationships.
- Imported items, item references, and item line numbers.
- Titles, annotations, visibility, preferences, and other user-owned state.
- Search and compute validity when content does not change.
- Content storage generations and existing history references.

The transition does not justify deleting and recreating a session or rebuilding unchanged history.
Necessary availability updates and normal ingestion of genuinely new records remain allowed.

Decoder memory must stay bounded independently of the rollout's declared or actual size.
Existing list-based startup ingestion and full-history replacement remain the baseline for item materialization.
This feature does not require their conversion to new batches, acknowledgments, or disk-backed item spools.
Repeated bounded ingestion must avoid decoding the complete source again for every slice.
Long source reads must preserve existing fairness and responsiveness guarantees.
Any temporary decoded data must have a bounded lifecycle and cleanup behavior.
Ordinary JSONL ingestion must retain its existing incremental fast path.
Unchanged JSONL ingestion must not add full-history reads, DB queries, source copies, or compressed-worker waits.
Representation transitions and actual deletions can require bounded extra metadata checks.

## Acceptance scenarios

Each applicable scenario covers regular sessions and subagents.
Guardian and other deliberately ignored rollouts retain their existing exclusion rules.

| Scenario | Required result |
|---|---|
| Known JSONL is compressed while TwiCC is stopped | Startup retains the session, updates its path, and does not mark it stale. |
| Known JSONL is compressed while TwiCC runs | No false stale update, duplicate history, or unnecessary rebuild. |
| A new rollout exists only as `.jsonl.zst` | It imports with the normal identity, classification, and history behavior. |
| Known compressed source has unimported records | Only missing records advance the logical checkpoint and line count. |
| Codex restores a compressed session and immediately appends | The same session receives new records once from the preserved checkpoint. |
| Both representations exist during publication | JSONL takes precedence; only one source contributes history. |
| Events arrive late, twice, or in reverse order | Reconciliation converges on the available source without reversing newer state. |
| A transition occurs during a source read or drain wait | Reads remain consistent; waits complete correctly or recover without hanging. |
| A real replacement occurs in either representation | Existing replacement handling applies; compression does not hide the change. |
| A compressed source requires migration or rebuild | Preparation, Codex execution, result validation, and replay succeed under normal coordination. |
| Source uses no declared size or concatenated frames | Logical offsets and completion remain correct. |
| Source is truncated, corrupt, or unreadable | No invalid ingestion checkpoint, false deletion, or loss of stored history. Other sessions continue. |
| A previously false stale or missing-source state exists | A valid matching source repairs the association and applicable availability state. |
| Reconciliation or rebuilding stops midway | Restart safely resumes through the ordinary recovery path. |
| Both representations are genuinely deleted | Existing stale behavior applies without recreating the session. |
| A different session ID appears at the selected source | Content does not attach to the existing identity. |
| Multiple sessions have large sources | Resource use stays bounded and ingestion remains fair. |
| Plain JSONL or another provider is used | Existing ingestion and source-replacement behavior remain intact. |

Validation must check durable DB state and relevant emitted session updates.
Checking only the final `stale` value does not detect a false transient stale update.
Source files must remain unchanged by TwiCC's read-only operations.

## Planning boundaries

This specification chooses behavior, not a decompression implementation.
The implementation plan selects readers, buffering, temporary storage, and observation evidence.
It also selects the exact coordination and serialized DB operations for path reconciliation.

Streaming and temporary seekable snapshots remain valid implementation options.
Either option must satisfy integrity, offset, resource, and concurrency requirements above.
No new dependency or schema change is required by this specification.
Any such change needs justification in the implementation plan.

## Analysis evidence and validation limits

Isolated Python probes exercise current TwiCC helpers and the installed `zstandard` dependency.
They confirm ignored `.zst` discovery, incompatible physical-size comparisons, and false replacement after identical JSONL restoration.
They also confirm unsupported backward seeking on the decompression stream and strict physical-path migration report rejection.

A truncated-frame probe returns 786,432 decoded bytes from an 801,890-byte JSONL without raising an exception.
The decoder reports `eof=False`; 98 complete JSONL lines are present in that partial result.
Another probe confirms that the first frame's declared size does not cover concatenated frames.
`zstandard.ZstdError` does not inherit from `OSError`.

These probes establish constraints, not feature acceptance.
No implementation, DB integration validation, Rust test run, or large-rollout performance measurement exists yet.

## Specification review

Two independent internal subagents perform an adversarial review of the initial draft.
Both receive explicit instructions to assess specification behavior, not implementation details.

| Review | Focus | Result |
|---|---|---|
| Identity and transitions | Identity, durable association, stale recovery, concurrency, and user-data preservation. | Ready for implementation planning. No material findings. |
| Readers and recovery | Reader coverage, logical offsets, integrity, migration, rebuild, resource constraints, and acceptance criteria. | Ready for implementation planning. No material findings. |

The review loop stops after these independent reviews because neither reports a material issue requiring revision.
No unresolved specification findings remain from this review round.
User approval and implementation validation remain separate from these review results.

Reviewers explicitly leave algorithms, internal APIs, queue structures, caches, and exact retry intervals to implementation planning.
They retain the stated scope exclusions and the limits of existing source-replacement detection.

The user's subsequent performance review narrows the implementation scope.
The specification now explicitly retains existing item-list materialization, bounded replacement sampling, and unknown-source polling fallback.
It prohibits new history-sized verification and unrelated overhead on ordinary JSONL paths.
These clarifications supersede any broader interpretation used by the first implementation-plan draft.
