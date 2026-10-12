# Session item content extraction: impact audit

Date: 2026-10-11. Audited checkout: `07c647c416211dd6534f2628f3db3967f50b6e4d`.

This is a source audit. It does not implement the extraction.
Sections 1–10 record the extraction-only investigation. Section 11 extends the audit to consolidated cold compression.
The current consolidated specification supersedes older text-only storage proposals in sections 1–10.
No production database, provider files, server, migration, or Git branch was changed for this audit.
The compression extension includes isolated in-memory SQLite probes only.
Line references identify the audited checkout. Proposed adjustments below are not existing behavior.

## Findings that affect implementation

1. **Three production insertion paths require changes.** Initial sync, live sync, and Codex history replacement all create items with inline content.
2. **Unchanged legacy items need a writer-side storage sweep.** Existing `content_overrides` contains transformations only. Do not ship unchanged strings through compute IPC.
3. **A source does not globally determine one transformed content.** Session identity, previous lines, task state, and ephemeral file caches affect transformations.
4. **Preserve current content during recompute.** Existing transformations intentionally preserve enrichment that cannot be reconstructed from the source alone.
5. **Keep a persistent original FK for every known source.** Keep original=current for unchanged known sources. Reserve NULL for unavailable originals. This convention is accepted under the user's delegated design decision.
6. **Readiness is not a universal read barrier.** Owner APIs, CLI, and history resolvers can read old sessions. Mixed storage must remain readable.
7. **Query changes must be demand-driven.** Metadata-only paths must not fetch contents. There is no blanket requirement to use joins.
8. **Content substring filters are important.** Changing model attributes alone does not update `content__contains`, projections, or raw SQL.

## Audit boundary and agreed storage contract

The target table is `SessionItemContent`: numeric PK, unique binary SHA256, and immutable text content.
The hash identifies the exact stored string encoded as UTF-8. It does not identify normalized JSON.

`SessionItem` receives nullable numeric `current_content` and `original_content` FK fields.
Both use `db_index=False` and `on_delete=PROTECT`.
The earlier summary left `original_content` NULL when current content was the unchanged source.
The accepted convention now gives every known original a FK, including when original=current.
The user delegated this decision to the assistant. The two-FK schema remains unchanged; implementation is still pending.

The legacy `content` column remains during rollout. Background compute moves and clears it progressively.
Schema migration does not move data. Initial sync and live sync use the new table immediately.
Provider compute versions and a session storage marker control completion.

Item identity, `(session, line_num)`, and line-based relationships remain unchanged.
Forks keep separate logical items. Equal content strings can reference the same content row.
Item deletion does not delete content. Garbage collection, column removal, and vacuum are outside this lot.

## 1. Writes and transaction boundaries

### Initial sync: producers and actual writers

| Source location | Actual behavior | Required adjustment |
|---|---|---|
| `src/twicc/sync_helpers.py:115`, `:187`, `:204`, `:215` | Reads file slices, decodes UTF-8, filters empty lines, strips lines, returns `(line_num, content)` tuples. | Keep producer payloads as strings. Hash the resulting stored strings, not the original file buffer. |
| `src/twicc/providers/claude_code/initial_sync.py:147`, `:281`, `:385` | Builds initial-sync payloads for sessions and subagents. | Trace both create and append payloads through the shared writer. Do not insert content rows from producer threads. |
| `src/twicc/providers/codex/initial_sync.py:220`, `:276`, `:370` | Builds create/append payloads after rollout inventory. | Same new writer contract, including fork and subagent records. |
| `src/twicc/providers/db_writer.py:2425` | `_apply_create_session_payload` bulk-creates `SessionItem(content=ct)` inside an atomic block. Tracking offsets are saved afterward. | Insert/reuse content rows before item insertion. Set current FK and empty legacy content in the same transaction. Preserve offsets after successful item writes. |
| `src/twicc/providers/db_writer.py:2448` | `_apply_update_session_payload` appends with `ignore_conflicts=True`; then saves tracking and sometimes resets compute version. | Same content insertion contract. Preserve existing item rows on conflicts. Do not replace an existing enriched current content with a raw duplicate. |

The producer already uses UTF-8 replacement for malformed bytes (`sync_helpers.py:190`).
The stored source therefore differs from the source-file bytes in that case.
Leading/trailing line whitespace is also removed (`sync_helpers.py:224`).

**Hash contract:** hash the exact string that would previously have entered `SessionItem.content`.
Do not serialize parsed JSON merely to calculate its hash. This would change whitespace, escaping, and key order.
Preserve invalid JSON strings too; extraction must not discard them.

`bulk_create` bypasses model `save()` methods and signals.
A model save override alone cannot implement any of these three insertion paths.
The content helper must support bulk reuse, conflict-safe insert, and PK resolution by hash.

An ignored duplicate item can leave a newly inserted unreferenced content row.
That is acceptable under deferred garbage collection. It must not alter the existing item's original or current FK.
Do not infer insertion success from unsaved model objects after `ignore_conflicts=True`.

### Live sync

| Source location | Actual behavior | Required adjustment |
|---|---|---|
| `src/twicc/providers/compute_base.py:4048` | `sync_session_slice` reloads the session and uses `live_state_transaction`. | Keep storage writes inside this existing transaction boundary. |
| `src/twicc/providers/compute_base.py:970` | Provider maps are snapshotted; durable atomic transaction restores maps and cache borrows after failure. | Content insertion and FK updates must participate. Do not add an independent commit or consume caches early. |
| `src/twicc/providers/compute_base.py:4171` | Constructs an in-memory item with the raw line. | Keep the source string before parsed content is mutated. An explicit in-memory current string avoids unnecessary relation reads. |
| `src/twicc/providers/compute_base.py:4194`, `:4200` | Runs generic and provider inline transformations; changes the in-memory content. | Keep the known original source PK even if unchanged under section 9.1. Do not use an intermediate result as the original. |
| `src/twicc/providers/compute_base.py:4206`, `:4210` | Adds cache enrichment after inline transformation. | Persist the final enriched string as current. Preserve the raw source across both transformation stages. |
| `src/twicc/providers/compute_base.py:4218` | Publishes parsed current-batch evidence before inserting items. | Keep this ordering. Same-batch screenshot and resolver lookups depend on it. |
| `src/twicc/providers/compute_base.py:4367` | Bulk inserts items before second-pass metadata/link updates. | Resolve content PKs before this insert. Preserve existing second-pass behavior and conflict handling. |
| `src/twicc/providers/compute_base.py:4339` | Reads the first user message to recover subagent prompt metadata. | Read its effective current content, including mixed legacy storage. |

The live path already commits offsets, rows, metadata, facts, and cache ownership together.
New content writes must preserve that property. Broadcast data must still describe committed current content.
Filesystem screenshot writes are existing external effects; content transactions do not make these files transactional.

### Background compute: read-only worker and serialized writer

| Source location | Actual behavior | Required adjustment |
|---|---|---|
| `src/twicc/providers/compute_base.py:2873` | Full compute iterates all session items, currently with inline content. | Read effective current strings for all items. Choose loading strategy by workload. |
| `src/twicc/providers/compute_base.py:3059` | Parses current stored content, then transforms it. | Keep this input behavior; do not automatically replay from original FK. |
| `src/twicc/providers/compute_base.py:3072` | Emits `content_overrides` only when transformation changes the string. | Add storage moves for every legacy item, including unchanged and malformed contents. |
| `src/twicc/providers/compute_base.py:3509` | Ships transformation strings to the writer in `session_complete`. | Define an explicit storage payload with source/current provenance. Do not overload metadata updates implicitly. |
| `src/twicc/providers/compute_base.py:3633` | `apply_content_overrides` directly executes `UPDATE ... SET content=%s WHERE id=%s`. | Replace this persistence operation with content reuse plus FK updates. Clear legacy column atomically with its current FK. |
| `src/twicc/providers/compute_base.py:3649` | `apply_session_items_chunk` commits compute slices separately. | Apply new storage payloads in bounded slices under the same revision guard. |
| `src/twicc/providers/db_writer.py:1992`, `:2002`, `:2018` | Large metadata and content override lists are chunked independently. | Count and chunk storage moves, including unchanged contents. Avoid a single transaction for an entire huge session. |
| `src/twicc/providers/compute_base.py:3684`, `:3706` | Final apply guards against changed `last_offset` before writing. | Preserve the guard. Do not publish a storage marker or compute version for a superseded result. |
| `src/twicc/providers/compute_base.py:3897` | Final session fields include compute version. | Mark storage completion only after every required storage slice succeeds. |
| `src/twicc/providers/db_writer.py:2063`, `:2077`, `:2102` | Applies results under writer/run ownership; rejects abandoned runs. | Route extraction through this protocol, not a separate background migration writer. |

**Crash behavior:** a committed storage slice can survive a later failure or superseded result.
The session remains outdated. Its next compute must read both migrated and legacy rows correctly.
An item already moved must never require the cleared legacy string to finish its recompute.

Content rows should be inserted/reused inside the writer's item transaction.
The worker should not create content rows while generating metadata.
Do not trust worker-only content PK allocation or mutate shared content rows.

The current worker accumulates results for a complete session.
Do not add unchanged source strings to its output. Section 9 recommends a bounded writer-side storage sweep.
Changed strings already accumulate in `content_overrides`; a stricter bound also needs a transformation transport change.
Section 9 distinguishes that existing risk from the new extraction work and compares bounded transport options.

### Codex history reconstruction

| Source location | Actual behavior | Required adjustment |
|---|---|---|
| `src/twicc/providers/codex/rollout_migration.py:167` | Reads the first DB item with `.only("content").afirst()` to detect history format. | Read effective current string in an async-safe way. Format detection must still work before storage migration. |
| `src/twicc/providers/codex/rollout_migration.py:350`, `:380` | Deletes old items; commits `last_offset=0`, resets compute/search, increments history epoch. | Keep content rows. Reset the storage completion marker because the logical item set is replaced. |
| `src/twicc/providers/codex/rollout_migration.py:391` | Inserts replacement raw items in atomic chunks of 2,000. | Store replacement contents directly in the new table inside each chunk. |
| `src/twicc/providers/codex/rollout_migration.py:400` | Finishes tracking after replacement. | Do not mark metadata compute complete here. Storage marker semantics must distinguish raw storage completion from metadata completion. |
| `src/twicc/providers/codex/background_compute.py:169`, `:175`, `:176` | Selects obsolete/forced sessions by descending mtime. | Bump Codex compute target and preserve forced rebuild behavior. |

Replacement already clears ephemeral enrichment after deletion commits (`rollout_migration.py:371`).
A replacement can therefore lose enrichment that exists only in old current content.
The content table keeps those old strings but does not automatically reconnect their enrichment to reconstructed items.
Changing this policy is separate work; do not claim extraction alone preserves it.

## 2. Transformation inventory and original recovery

### Shared transformations

`src/twicc/providers/compute_base.py:1022` defines the order:

1. Extract attachment manifests and unwrap CLI paste wrappers (`:1073`).
2. Strip injected TwiCC context/instruction blocks (`:1059`; `src/twicc/context_injection.py:159`).
3. Run provider rewrites (`:1133`).
4. In live sync, apply out-of-band tool-result enrichment (`:4206`).

`twicc_attachments` retains the validated manifest, not the exact removed tag text.
Stripped context blocks are not retained in current content.
These operations generally do not permit exact reconstruction of the previous stored string.

### Claude Code transformations

| Source location | Transformation | Source or context dependency |
|---|---|---|
| `src/twicc/providers/claude_code/compute.py:972` | Enriches task tools with task snapshots; removes enrichment on compaction duplicates. | Previous task tools, seen tool-use IDs, and stored snapshots. |
| `src/twicc/providers/claude_code/compute.py:1249` | Normalizes task/Monitor/background notifications and local command output into messages/tool results. | Session-scoped Monitor mapping and notification shape. |
| `src/twicc/providers/claude_code/compute.py:1294` | Repairs previously misrouted notifications from embedded original fields. | Earlier persisted TwiCC rewrite evidence. |
| `src/twicc/providers/claude_code/compute.py:1337` | Combines assistant task enrichment with screenshot substitution. | Prior task state and prior image results. |
| `src/twicc/providers/claude_code/compute.py:1681` | Stores local-command original content, then replaces the message and removes subtype. | Original text is only a partial record snapshot. |
| `src/twicc/providers/claude_code/compute.py:2662` | Injects `toolUseResult.originalFile`. | Ephemeral PreToolUse capture; already enriched results stay unchanged. |
| `src/twicc/providers/claude_code/agent/agent.py:179`, `:198` | Captures pre-execution files for Edit/Write. | Actual file state before execution, not the JSONL source alone. |
| `src/twicc/providers/claude_code/agent/original_file_cache.py:18`, `:25` | Keeps capped pre-execution text in an ephemeral cache. | 100 KB per-file capture limit; capture availability. |

### Codex transformations

| Source location | Transformation | Source or context dependency |
|---|---|---|
| `src/twicc/providers/codex/compute.py:2536` | Latches fork metadata before attachment processing. | Session ownership and fork lineage. |
| `src/twicc/providers/codex/compute.py:3890` | Converts injected commands into canonical user messages. | Session ID and line number enter the generated payload. |
| `src/twicc/providers/codex/compute.py:3911`, `:4004` | Restores `/plan` prompt prefix. | Previous collaboration-mode transition and bare `/plan` marker; one-shot state. |
| `src/twicc/providers/codex/compute.py:3927` | Converts proposed plans into canonical assistant messages. | Session ID and line number enter the generated payload. |
| `src/twicc/providers/codex/compute.py:3941`, `:3969` | Replaces screenshot tags. | Prior image results and session-scoped artifact URL. |
| `src/twicc/providers/codex/canonical.py:194`, `:210` | Builds private canonical messages. | Embeds `thread_id=session_id`, `twicc-line-{line_num}`, and `twicc-item-{line_num}`. |
| `src/twicc/providers/codex/compute.py:5529`, `:5557` | Injects `original_files` into FileChange completion. | Ephemeral pre-patch capture; already enriched content stays unchanged. |
| `src/twicc/providers/codex/agent/agent.py:224`, `:267`, `:3370` | Captures file state on item-start notification. | File state and notification timing. |
| `src/twicc/providers/codex/agent/original_files_cache.py:29`, `:36` | Keeps per-call file maps. | 100 KB per-file cap and cache availability. |

### Why one original can produce different transformed strings

This is a code-supported possibility, not a claim about measured frequencies.

- Screenshot substitution explicitly produces `/artifacts/<session_id>/<filename>` (`compute_base.py:595`). The same source tag in two sessions can produce different URLs.
- Screenshot lookup reads images before a specific line. Identical tag text can resolve to different prior results or a missing-image placeholder.
- Private Codex message builders embed session ID and line number (`codex/canonical.py:210`). Equal input strings do not imply equal output strings across item occurrences.
- Claude task enrichment reads per-session state and treats duplicate tool IDs differently (`claude_code/compute.py:972`).
- Original-file enrichment depends on a cache capture. The same source record can be stored with or without enrichment.
- A newer compute version can repair an earlier rewrite (`claude_code/compute.py:1294`).

**Schema implication:** the two FK fields per item remain sufficient.
Do not add a uniqueness constraint or one-to-one mapping from original content to transformed content.
Deduplicate each resulting string independently by its own hash.

### Which old originals can actually be recovered

| Available evidence | Recovery limit |
|---|---|
| Legacy item with no previous transformation | Its stored string can be used as source if that provenance is known. The schema does not currently record this fact. |
| `twiccOriginalContent` in Claude notifications/local commands | Retains a message content slot, not necessarily the full record or removed fields. It cannot recover exact original JSON bytes. |
| `twiccOriginalEntry` in Claude attachment rewrites (`claude_code/compute.py:1530`, `:1559`, `:1615`) | Stores a serialized whole parsed record before the provider rewrite. Earlier generic stripping/extraction has already run. Original whitespace/escaping is lost. |
| Codex `twiccOriginalContent` (`codex/canonical.py:206`, `codex/compute.py:1116`) | Retains the old payload. It supports semantic repair, not proof of exact source-string recovery. |
| Provider file still present | A matching line may supply the source, but only after validating file identity, history epoch, numbering, and normalization. No such recovery is implemented by this audit. |
| Injected `originalFile` / `original_files` | These are pre-edit file contents. They are not originals of the SessionItem JSON record. |
| Stripped context/paste wrappers or substituted screenshot tag | The complete original string can be unavailable from current DB content. |

Therefore, embedded debug fields must remain in current content in this lot.
Repair code and frontend behavior use them. Do not remove them merely because a new original FK exists.
Do not hash a reconstructed parsed record and label it the exact original.

### Recompute invariants and provenance decision

Recompute must begin from **current** content, as it does today (`compute_base.py:3059`).
It must keep an existing original FK unchanged when it transforms that current content again.
It must never update a shared content row in place.

Starting every recompute from the original would remove live-only file enrichment.
It could also discard immutable legacy task snapshots (`claude_code/compute.py:972`).
An extraction-only release must not change this behavior.

Under the earlier convention, `original_content=NULL` cannot distinguish an unchanged known source from an old transformed string with lost provenance.
Filling that FK from current content on every first observed change can fabricate an original.
A session-level storage marker alone does not resolve item-level provenance after mixed legacy/new writes.

Section 9 resolves this with the accepted durable original=current convention.
It also explains why temporary equality alone is insufficient after a later recompute.
No extra provenance column is required. Accepting this design does not authorize code implementation.

## 3. Read inventory

### Effective current content and mixed storage

Readers need one explicit content contract during migration:

- If a current FK exists, return that content string.
- Otherwise, read the retained legacy string.
- If neither exists for an item that should contain a record, report an invariant failure or controlled error.
- Never expose original content through existing API/search/message paths by accident.

The legacy field still exists. A property named `content` cannot simply replace it while it remains a Django field.
Choose an explicit accessor/loader and adapt ORM filters/projections separately.
Avoid silently writing a compatibility attribute that never persists its FK changes.

### API, WebSocket, and share reads

| Source location | Consumer | Content needed? | Required adjustment |
|---|---|---|---|
| `src/twicc/core/serializers.py:349`, `:359` | Full item serializer | Yes | Keep wire `content` as a string. Resolve current content before serialization, with legacy fallback. |
| `src/twicc/core/serializers.py:372` | Metadata serializer | No | Keep no-content output and no content loading. |
| `src/twicc/views.py:1539` | Owner item range endpoint | Yes, selected ranges | Fetch selected item current strings using an async-safe strategy. |
| `src/twicc/views.py:1624` | Owner all-item metadata | No | Preserve metadata-only behavior. Do not add default relation loading. |
| `src/twicc/views.py:1560` | Hybrid attachment image lookup | Yes, filtered candidates | Rewrite substring predicate and parse effective content. |
| `src/twicc/views.py:964`, `:975` | User-message browser | Yes, user items only | Provider helper consumes current strings. Preserve sender-header filtering. |
| `src/twicc/views.py:1763` | Workflow link derivation | Yes, content-filtered candidates | Rewrite predicate and `.only("content")` projection. |
| `src/twicc/core/session_queries.py:113` | Owner/share tool-result payload | Yes, linked result lines | Adapt provider helper input loading. Keep line ceilings. |
| `src/twicc/core/session_queries.py:206` | Spawn display names | Yes, spawn call items | Adapt `.only("session_id", "line_num", "content")`. Preserve lookup keys. |
| `src/twicc/providers/sessions_watcher.py:251`, `:255` | Full committed item broadcasts | Yes | Keep string payload; fetch only announced lines. |
| `src/twicc/providers/sessions_watcher.py:263`, `:266` | Metadata broadcasts | No | Keep relation content unloaded. |
| `src/twicc/share/session_views.py:140` | Shared metadata | No | Keep no-content queryset. |
| `src/twicc/share/session_views.py:155`, `:157` | Shared selected items | Yes | Keep ceiling, freeze, and readiness gates; return current content strings. |
| `src/twicc/share/session_views.py:201`, `:205` | Shared raw result items for full diffs | Yes | Size cap must use effective current content, including original-file enrichment. |
| `src/twicc/share/consumer.py:167` | Live shared item forwarding | Already serialized | Preserve wire contract and readiness. No new content lookup needed here. |
| `src/twicc/share/display.py:31`, `:54` | Display and visible-call filtering | No | Preserve content-free metadata predicates. |

**Async risk:** owner/share endpoints materialize rows inside `sync_to_async`, then serialize outside it.
A serializer that lazily accesses a Django FK outside that boundary can execute synchronous SQL in async code.
Load required strings within the synchronous boundary or use an explicit async loader.
This requirement does not mandate joins; targeted/batched lookups inside that boundary also work.

Owner `_resolve_session_or_404` checks routing/visibility, not compute readiness (`views.py:1019`).
Share item routes explicitly call `_resolve_ready_content_session` (`share/session_views.py:136`, `:149`).
UI readiness gating alone cannot replace mixed-storage support in backend readers.

### Message helpers, CLI, MCP, search, and titles

| Source location | Behavior | Required adjustment |
|---|---|---|
| `src/twicc/providers/helpers.py:748`, `:827`, `:857` | Common user/indexable-message extraction and parsing | Adapt the shared parser to effective current content. Preserve parse-once behavior and invalid JSON handling. |
| `src/twicc/providers/helpers.py:775`, `:812` | First-user message and title source | Keep single-row versus full-user-history loading distinct. |
| `src/twicc/providers/claude_code/helpers.py:250`, `:792` | Spawn names and tool-result parsing | Adapt direct `item.content` reads. |
| `src/twicc/providers/codex/helpers.py:175`, `:817` | Spawn names and tool-result parsing | Same requirement; keep provider-native JSON interpretation. |
| `src/twicc/cli/session.py:177`, `:184` | Raw content command | Rewrite case-insensitive substring filter and current-string parsing. Preserve AND filters, count, tail, paging, and line addresses. |
| `src/twicc/cli/session.py:317`, `:327`, `:341` | Message command | Adapt the selected item loader; preserve extracted-text filters and final-message classification. |
| `src/twicc/cli/_wait_reply.py:275`, `:479`, `:492`, `:517` | Wait reply/error detection | Load current strings for only scanned assistant/error items. Cursor/metadata-only polls stay content-free. |
| `src/twicc/mcp/events/prompts.py:11`, `:15` | First non-command prompt | Adapt direct current-content read; preserve command exclusion and cursor. |
| `src/twicc/mcp/events/methods.py:252`, `runtime.py:488` | Calls prompt resolver | Transitive parser impact; no direct content schema in event payload. |
| `src/twicc/search.py:418`, `:422` | Whole-session reindex | Load current strings for indexable items only. Keep readiness checks before and after extraction. |
| `src/twicc/search_indexing_task.py:496`, `:525` | Background search indexing | Same demand-driven current-string loading. |
| `src/twicc/providers/sessions_watcher.py:675`, `:686` | Live search indexing | Load only new/modified indexable lines. |
| `src/twicc/asgi.py:1918` | Title suggestion source | Transitive helper impact. |
| `src/twicc/cli/_drop_request/sender_header.py:112` | Tests extracted text for sender header | No storage change; preserve cleaned-text semantics upstream. |

MCP command invocation reuses the CLI/RPC functions. Do not implement a separate MCP content storage path.
Search index documents keep session ID and line number. No Tantivy schema change is required by extraction alone.
Search freshness must still advance only after successful indexing of completed current content.

### Compute resolvers: all direct current-content consumers

These functions read earlier records. They must support partially moved histories and in-memory current-batch records.
They must continue using current content rather than original FK content.

| Source location | Function or operation |
|---|---|
| `src/twicc/providers/compute_base.py:1599` | `iter_images_backward` parses earlier image results. |
| `src/twicc/providers/compute_base.py:2069` | `find_open_group_head` parses the previous item. |
| `src/twicc/providers/compute_base.py:2230` | `create_tool_result_link_live` validates call candidates. |
| `src/twicc/providers/compute_base.py:2389` | `create_agent_link_from_tool_result` parses spawn candidates. |
| `src/twicc/providers/compute_base.py:2461` | `_spawn_items` parses candidate spawning items. |
| `src/twicc/providers/compute_base.py:2554` | `_recover_owned_agent_links` parses recovery candidates. |
| `src/twicc/providers/compute_base.py:2609`, `:2624` | `_resolve_queue_spawn` parses owner/peer queue candidates. |
| `src/twicc/providers/compute_base.py:2659` | `_tree_queue_completions` reads queue completion records. |
| `src/twicc/providers/claude_code/compute.py:914`, `:951` | `_rebuild_state_if_missing` reads task snapshots and tools. |
| `src/twicc/providers/claude_code/compute.py:1990` | `_lookup_context_baseline` reads earlier messages. |
| `src/twicc/providers/claude_code/compute.py:2120` | Image-result candidate queryset. |
| `src/twicc/providers/claude_code/compute.py:2594` | Spawn-tool candidate queryset. |
| `src/twicc/providers/codex/compute.py:3113` | `_lookup_task_started_line`. |
| `src/twicc/providers/codex/compute.py:3126` | `_live_fork_fields` reads line-1 metadata through `values_list`. |
| `src/twicc/providers/codex/compute.py:3435` | `_lookup_tool_call`. |
| `src/twicc/providers/codex/compute.py:3472` | `_lookup_spawn_for_agent_path`. |
| `src/twicc/providers/codex/compute.py:3513` | `_lookup_orphan_end_exec_call_id`. |
| `src/twicc/providers/codex/compute.py:3570` | `_lookup_code_cell_call_id`. |
| `src/twicc/providers/codex/compute.py:3618` | `_lookup_exec_command_call_id`. |
| `src/twicc/providers/codex/compute.py:4071`, `:4089` | `_lookup_prev_plan_context`. |
| `src/twicc/providers/codex/compute.py:4142`, `:4162` | `_lookup_prev_goal_context_state`. |
| `src/twicc/providers/codex/compute.py:4637` | `_lookup_prev_total_tokens`. |
| `src/twicc/providers/codex/compute.py:4956`, `:4960` | Tool-image candidate predicates. |
| `src/twicc/providers/codex/compute.py:5119` | Spawn candidate content predicate. |
| `src/twicc/providers/history_facts.py:57` | `iter_history_items` projects `(line_num, content)` in descending pages. |
| `src/twicc/providers/history_facts.py:115` | `iter_resolver_items` uses indexed facts for current sessions and raw pages otherwise. |
| `src/twicc/providers/history_facts.py:212` | `HistoryFactContext._source_items` parses candidates. |

History-fact readiness follows compute version, not content placement.
Keep authoritative indexed absence for current sessions and raw fallback for old sessions.
The new storage migration must not mark partially computed facts as current.

### Provider watcher side effects and workflow synthesis

| Source location | Operation | Required adjustment |
|---|---|---|
| `src/twicc/providers/codex/sessions_watcher.py:401` | Reads new content to detect process announcements/completions. | Adapt projected current strings; retain line scope. |
| `src/twicc/providers/codex/sessions_watcher.py:451` | Reads new content for goal-stop detection. | Same projection change. |
| `src/twicc/providers/claude_code/sessions_watcher.py:659` | Projects `(kind, content)` to derive hybrid user/turn/error state. | Preserve terminal versus retry-error behavior with current strings. |
| `src/twicc/providers/claude_code/workflow_synthesis.py:179`, `:182`, `:202` | Finds launch args from run ID, result tool ID, and call input. | Rewrite substring filters and content-only loading; preserve survival after provider file removal. |

`Workflow.raw_json` is a separate durable JSON field. It is not moved into the new table.
`Session.tasks`, inline artifact catalogs, history facts, links, and session metadata remain separate fields/tables.
Their compute inputs need current content, but their storage format does not change.

## 4. SQL/query changes and loading policy

### Demand-driven loading

Do not add `select_related("current_content")` to a default manager or every queryset.
Do not fetch original text on ordinary current-content reads.
Original content is needed only for explicit provenance/recovery operations.

SQLite has no network round trip for each query.
Its official guidance allows many small queries when appropriate: [Many Small Queries Are Efficient In SQLite](https://www.sqlite.org/np1queryprob.html).
Django adds Python, query-construction, relation-object, and string-allocation overhead; these costs still require measurements.

| Workload | Strategies to compare |
|---|---|
| Metadata page, cost aggregates, readiness, row existence | Item fields only. No content query. |
| One line, first user prompt, resolver that stops after one candidate | Targeted item plus lazy/explicit content PK read can be appropriate. |
| Many selected lines requiring all contents | Compare a current-content join, separate batched PK lookup, and targeted reads. |
| Large history scan | Compare bounded joins with per-page distinct-PK lookup. Bound memory and do not load original strings. |
| Many items sharing a large content row | Batched lookup can load that string once; a join can repeat the large payload for every item. |

Django FK caching is per item instance. It does not automatically share one content object across different item instances.
An explicit bounded map by content PK can avoid duplicate reads within a batch.
Do not create an unbounded process-wide cache of all content strings.

Required strings must be loaded within safe sync/async boundaries.
Measure actual elapsed time, memory, and bytes fetched. Query count alone is not the success criterion.
Any tests asserting a fixed query count must encode a chosen workload, not a universal join rule.

### Substring predicates: complete live-code inventory

These predicates cannot keep pointing at the progressively cleared legacy field:

- `cli/session.py:177`: `content__icontains`.
- `views.py:1564`, `:1764`: attachment reference and workflow run marker.
- `claude_code/compute.py:905`, `:942`: task snapshot/tool-name filters.
- `claude_code/compute.py:2123`, `:2595`: image and spawn candidate filters.
- `claude_code/workflow_synthesis.py:182`, `:202`: run/call lookup.
- `codex/compute.py:4956`, `:4957`, `:4960`, `:5119`: output/image/spawn candidates.

Paths in this subsection are under `src/twicc/` or `src/twicc/providers/`, as in the tables above.
Comments in `compute_base.py:1544` and `:1900` also describe the old field lookup and need documentation updates.

For mixed storage, filter the current relation when its FK exists, and legacy only when it does not.
An unconditional OR with legacy content can return obsolete text if both stores are present during a partial transition.
A relation predicate performs a join when needed; this does not justify joins for unrelated metadata queries.

Projection changes are separately required at:

- `history_facts.py:57`.
- `codex/compute.py:3126`.
- `codex/sessions_watcher.py:401`, `:451`.
- `claude_code/sessions_watcher.py:659`.
- `core/session_queries.py:206`.
- `views.py:1765`.
- `claude_code/compute.py:1987`.
- `claude_code/workflow_synthesis.py:179`.
- `codex/rollout_migration.py:167`.

During this lot, `.defer("content")` still defers the large legacy field.
It does not fetch new relation contents unless another operation requests them.
Preserve it in owner/share/watcher metadata paths while the legacy field remains populated.

For a chosen joined loader, `.only()` must include the FK connector and requested relation fields.
Omitting connectors can conflict with relation loading or cause deferred fetches.
For `values()`/`values_list()`, preserve tuple shape and string semantics explicitly.

The raw content UPDATE in `compute_base.py:3639` is the production SQL mutation found by this audit.
General metadata SQL at `compute_base.py:3618` must accept FK attnames/field preparation correctly if extended.
Do not rename or modify historical migrations to adapt current runtime readers.

## 5. Frontend and wire compatibility

**No FK IDs are required in the existing frontend payload.** Keep returning current JSON strings under `content`.

### External payload contract

Moving physical storage does not change existing item payloads.
Resolve effective current content first; then put the value in the existing `content` location.
Do not expose a nested content-row object or replace content with a FK/hash.

| Surface | Existing representation to preserve |
|---|---|
| Full item serializer, REST, WebSocket, shared viewer | `content` is the raw JSON string. |
| CLI `session content`, reused through MCP/RPC | `content` is parsed JSON: `orjson.loads(effective_current_string)` (`cli/session.py:187`). |
| CLI `session messages` | Existing extracted `text`, `role`, timestamp, and final-message fields. |
| Metadata-only item payloads | No `content` field and no content-text load. |

Only the source of the value changes. Existing consumer-specific parsing remains unchanged.
The loader performs required reads before serialization; the serializer does not trigger hidden SQL.

### Frontend contracts

| Source location | Existing contract to preserve |
|---|---|
| `frontend/src/utils/parsedContent.js:14`, `:33`, `:44`, `:62` | Lazy parsing, explicit parsed synthetic content, invalidation, and presence detection. |
| `frontend/src/stores/data.js:2230` | Incoming full-item updates contain `{line_num, content: string}`. |
| `frontend/src/stores/data.js:4380`, `:4401`, `:4402` | Metadata placeholders receive fetched content; parsed cache is invalidated. |
| `frontend/src/utils/visualItems.js:92`, `:100` | Visual items carry string content and reuse parsed cache. |
| `frontend/src/utils/visualItems.js:457`, `:468` | Synthetic items can have NULL content and explicit parsed content. |
| `frontend/src/stores/data.js:1556`, `:3368`, `:3413`, `:3466`, `:3687`, `:3951` | Ephemeral, streaming, optimistic, and synthetic items do not imply durable SessionItem rows. |
| `frontend/src/share-session/shims/dataStoreShim.js:120`, `:131`, `:132`, `:145` | Shared viewer metadata placeholders and full-item cache invalidation. |
| `frontend/src/share-session/ShareItemsList.vue:223`, `:288` | Lazy content requests and parsed rendering. |

Do not turn missing current content into an empty successful API string.
The frontend treats absent content as a fetch/loading condition and stores parsed caches separately.
Storage migration must remain invisible to visual item identity and line-based scrolling.

Provider renderers continue receiving the same parsed native JSON.
`originalFile`, `original_files`, task snapshots, attachment descriptors, and debug original fields stay in current JSON.
Content extraction does not require a rewrite of their components or parsing helpers.

The audited tree has no separate transcript-download implementation discovered by export/download searches.
CLI raw/message output and shared session rendering are the relevant transcript surfaces found.
`src/twicc/inline_artifacts/share_exports.py` exports artifact files/catalog selections, not SessionItem content; no direct extraction change is required there.
Inline artifact publications are derived from transformed parsed content (`compute_base.py:4218`) and must keep that ordering.

## 6. Readiness, storage markers, and unaffected metadata

| Source location | Finding |
|---|---|
| `src/twicc/core/serializers.py:18` | `session_compute_ready` compares provider compute version. |
| `src/twicc/providers/background_compute_task.py:573`, `:618`, `:619` | Processes all obsolete provider sessions, including subagents, newest first. |
| `src/twicc/providers/codex/background_compute.py:175`, `:176` | Codex coordinator also includes forced rebuilds, newest first. |
| `src/twicc/providers/sessions_watcher.py:619` | New live-created sessions receive provider compute target. |
| `src/twicc/providers/claude_code/helpers.py:363`, `codex/helpers.py:357` | Provider compute targets are exposed through helpers. |
| `frontend/src/stores/data.js:3049` | UI skips outdated session loading, but this is not a backend access guard. |

The storage marker must not hide legacy rows from compute or stop retry discovery prematurely.
A new live-created session should be marked according to its fully new storage writes.
Initial-sync appends to old sessions must not falsely mark all earlier legacy items as moved.
Reset/rebuild must reset completion state. Empty sessions and providers without a compute pipeline need explicit treatment.

The following paths need no content loading for their actual query:

- `providers/live_aggregates.py:51`: values projection of activity/cost metadata.
- `usage.py:32`: cost aggregates.
- `core/agent_runs.py:140`: freeze-time timestamp aggregate.
- `share/display.py:54`: visible call metadata.
- `share/consumer.py:139`: tool-use line and display-level lookup.
- `mcp/events/runtime.py:500`: item timestamp lookup.
- `providers/claude_code/agent/hybrid/agent.py:758`: timestamp existence test.
- `cli/_wait_reply.py:204`: last-user-line aggregate for ready sessions.

These metadata queries should benefit from smaller item rows over time.
Do not add content relations to them through a default queryset policy.

## 7. Deletion and retained schema

| Source location | Actual deletion | Extraction implication |
|---|---|---|
| `src/twicc/providers/codex/rollout_migration.py:380` | Deletes items for history replacement. | Retain shared content rows; new history can reuse hashes. |
| `src/twicc/providers/db_writer.py:2536` | Deletes sessions through serialized delete payloads. | Item cascade does not cascade upward to content. |
| `src/twicc/providers/codex/initial_sync.py:542` | Guardian inventory cleanup feeds deletion work. | Keep the same session cleanup behavior without content GC. |
| `src/twicc/core/management/commands/sync.py:23` | `sync --reset` deletes projects, cascading to sessions/items. | Contents remain unless a separately authorized full reset policy explicitly removes them. |
| `src/twicc/core/migrations/0044_reclassify_clear_command_as_system.py:109`, `:150` | Historical migration filters/parses inline content. | Leave old migrations unchanged while the legacy column remains. Fresh installs replay historical schema first. |

No production content-delete path exists yet because the table is new.
Do not add per-item reverse-reference checks or content deletes in this extraction lot.
The unique hash index and content PK support forward reads and deduplication without child FK indexes.

Future direct content deletion must consider both current and original references.
It will need a separate garbage-collection strategy and may scan item references without indexes.
This does not impose FK indexes now.

Column removal and vacuum cannot be ordinary unconditional startup migrations in a later release.
The retained runtime bridge must support upgrades that skip intermediate releases.
This audit does not implement the later maintenance protocol.

## 8. Test impact and required new coverage

The search finds `SessionItem` references in **61 Python test files** at the audited checkout.
Not all require changes. Metadata-only fixtures can continue using minimal legacy rows where relevant.
Do not replace every fixture blindly or remove legacy fixtures needed to test the bridge.

| Test group | Existing examples | Required extraction checks |
|---|---|---|
| Live transactions and cache retries | `tests/test_live_sync_rollback.py:195`, `:196`, `:331`; `tests/test_live_sync_slices.py` | New writes use content FKs; rollback restores item/content writes and cache ownership; retry preserves captured file data. |
| Initial sync/writer diagnostics | `tests/test_sync_diagnostics.py`, `tests/test_live_sync_aggregates.py` | New/append writes bypass inline storage; duplicate item handling does not overwrite enrichment or offsets. |
| Compute apply/retry | `tests/test_compute_apply_signals.py`, `tests/test_background_compute_retries.py`, `tests/test_history_fact_compute_apply.py` | Unchanged/invalid legacy rows move; chunks resume; superseded results do not mark completion; storage and clearing are atomic. |
| Recompute persistence | `tests/test_codex_recompute_persistence.py`, `tests/test_claude_task_dedup.py:342`, `:349` | Preserve enrichment and original FK on recompute; duplicate task rules remain stable. |
| History replacement | `tests/test_codex_rollout_migration.py`, `tests/test_codex_migration_integration.py`, `tests/test_codex_guardian_session_filtering.py` | Detection handles old/new storage; replacement writes new storage; crash recovery marker remains valid; contents survive item deletion. |
| Resolver facts/fallback | `tests/test_history_fact_resolution.py:22`, `tests/test_history_facts.py`, `tests/test_nested_agent_compute.py` | Current facts and stale raw fallback read mixed storage correctly; no false absence. |
| Screenshots/attachments | `tests/test_insert_screenshot.py`, `tests/test_attachment_manifest_ingestion.py`, `tests/test_hybrid_attachment_image_view.py` | Transformation order and same-batch lookup remain unchanged; exact source versus current hashes differ appropriately. |
| CLI/message/wait | `tests/test_cli_session_selection.py`, `tests/test_cli_session_payload.py`, `tests/test_wait_reply.py`, `tests/test_wait_background.py` | Raw substring selection/paging, extracted messages, command exclusion, and final/error detection read effective current strings. |
| Shares and owner APIs | `tests/test_share_owner_api.py`, `tests/test_share_public_routes.py`, `tests/test_share_updates_consumer.py`, `tests/test_share_nested_subagents.py` | String wire contract, readiness, display/freeze ceilings, and large diff cap are unchanged; async serialization performs no forbidden lazy sync SQL. |
| Search and title | `tests/test_search_compute_readiness.py`, `tests/test_title_compute_apply.py`, `tests/test_title_auto_task.py` | Index/suggestion uses current strings; metadata/version gates remain correct. |
| MCP events | `tests/test_mcp_events_readiness.py`, `tests/test_mcp_events_turns.py`, `tests/test_mcp_events_lookup.py` | Genuine prompt detection and readiness work through shared parser. |
| Workflow | `tests/test_workflow_discovery.py`, `tests/test_workflow_meta_extract.py` | Run/call lookup and launch-arg synthesis work after inline clearing. |
| Frontend contracts | `frontend/src/utils/visualItems.test.js`, `frontend/src/share-session/ShareSessionApp.snapshot.test.js`, `frontend/src/share-session/ShareItemsList.loading.test.js` | Existing string/synthetic parsing contracts remain valid without exposing FKs. |

Add focused storage tests for:

- Same exact string across sessions/forks yields one content row and distinct item rows.
- Semantically equal JSON with different stored bytes produces different hashes.
- Current string selection prefers FK over legacy when both exist.
- Original remains NULL for unknown old source; it is not fabricated during later recompute.
- Under the accepted convention, a known unchanged source retains original=current across compute versions and restarts.
- Known original is captured before all transforms and remains stable across later changes.
- One original may point from multiple items to different current strings.
- Item/session deletion leaves content rows and preserves other references.
- Metadata paths do not read content text, including original text.
- A large shared payload does not multiply memory unnecessarily in the chosen loader.
- Schema additions produce no table rebuild, FK scan, or child FK index.
- Upgrade skipping an intermediate version still supports legacy rows.

Existing test helpers such as `tests/live_sync_helpers.py:8` drain the live sync API and remain useful.
Many tests directly assert `item.content`; update these to distinguish legacy physical storage from effective current content.
Do not keep those assertions passing through an accessor that conceals an unconverted write path.

## 9. Concrete process: accepted provenance and implementation proposals

This section deepens the three implementation topics after the first audit.
It describes interfaces and control flow, not implemented code.
The user has authorized analysis only.

### 9.1 Persistent source provenance with two FK fields

**Accepted decision: original always references the known source, including when original=current.**
An unavailable original stays NULL. The original FK never changes during recompute.
This replaces the earlier NULL-means-unchanged convention, without adding a field or duplicating text.

| Item state | Original FK | Current FK | Meaning |
|---|---|---|---|
| New known source, unchanged or waiting for compute | Source PK | Same source PK | Known source, no current text difference. |
| Known source, transformed | Source PK | Result PK | Known source with changed current text. |
| Migrated legacy, source unavailable | NULL | Legacy/current-result PK | Do not infer native source from current text. |
| Legacy awaiting extraction | NULL | NULL | Read inline legacy text until a writer storage transaction moves it. |

`original_id == current_id` answers whether a known source remains unchanged.
It needs only the two IDs. It does not load either content string.
Two FK values referencing one row do not create a second content text or hash row.

New initial sync and history replacement know their raw normalized input strings.
They create/reuse one source row and set both FKs to that PK before committing offsets.
Live sync also records this source PK before all transformations; final current can reference another row.

Legacy extraction treats the old current string conservatively as current, with original NULL.
It does not infer provenance from the absence of TwiCC keys or a comparison against reconstructed JSON.
An explicit future verified-source recovery can populate an unavailable original, but that is outside this extraction lot.

During recompute:

1. Read effective current content.
2. Run existing transforms on parsed current content.
3. If changed, create/reuse the result and update current FK only.
4. Preserve original FK verbatim, including NULL.
5. Clear legacy content in the same transaction as current FK assignment where necessary.

A migrated old item that later changes still has original=NULL.
A newly ingested item that first changes two compute versions later still has its original source PK.
No staging finalization or per-item provenance classification is needed.

| Alternative | Benefit | Cost or reason to reject |
|---|---|---|
| Temporary original=current, cleared after unchanged first compute | Uses existing FKs during initial staging. | Loses provenance after finalization. A later first transform cannot distinguish known raw from unknown legacy. |
| Persistent per-item provenance flag plus final original=NULL for unchanged | Keeps the earlier NULL convention and source knowledge. | Adds another column, state transitions, and invariants. A SQLite BooleanField can introduce CHECK/schema behavior; a nullable integer is more conservative but still extra state. |
| Session-level raw/source flag or storage version | Small schema cost. | Cannot classify mixed legacy/new rows appended within the same session. |
| In-memory provenance flag | No schema cost. | Lost after initial sync, partial compute, restart, and retry. |
| Raw-string recognition or embedded debug fields | No additional state. | Does not prove exact full-source recovery. Can fabricate an original. |
| Persistent original=current for known unchanged source | Existing two columns; restart-safe and item-local. | Changes the earlier convention. Stores a second numeric ID on unchanged known-source items. |

There are no child FK indexes under this process.
The extra numeric reference does not imply a reverse lookup or an extra text read.
Deletion remains unchanged: deleting an item does not delete either content row.

### 9.2 Move unchanged legacy strings inside bounded writer slices

**Recommendation: transform overrides first; then sweep remaining legacy rows from the writer; finalize compute last.**
Unchanged strings never enter the worker result message.
The storage sweep uses the same compute apply lease and `run_compute_sync` executor as existing chunks.

Exact integration points:

- `_apply_compute_items_in_chunks` (`db_writer.py:1989`) currently applies metadata chunks before content chunks.
- Its small-result shortcut (`db_writer.py:2003`) means an added sweep must not be conditional on override count.
- `_process_compute_message` (`db_writer.py:2092`) runs chunks before final `apply_session_complete` (`:2102`).
- `COMPUTE_APPLY_CHUNK_SIZE=2000` (`db_writer.py:81`) bounds existing item counts, not content bytes.
- `apply_session_items_chunk` (`compute_base.py:3647`) guards each transaction and preserves aggregate repair.
- `guard_compute_revision` (`compute_base.py:3585`) performs its conditional no-op UPDATE before other DB work.

Proposed writer flow:

```text
accept tracked run_id and session_complete
retain existing writer lease for the whole apply
apply bounded metadata chunks, as today
apply bounded transformed-content overrides
move remaining legacy rows in storage-only chunks
run final revision and storage-completeness checks
apply final links, session metadata, storage version, and compute version
broadcast success through existing completion handling
```

Small results must also pre-apply or incorporate their overrides before the legacy sweep.
The trimmed final message must not replay consumed overrides afterward.
The transformation helper preserves original FK under section 9.1 and assigns current FK atomically with inline clearing.

Proposed storage-only call, expressed as an interface:

```text
move_legacy_content_slice(
    session_id, observed_last_offset, observed_history_epoch,
    after_line, through_line, max_rows, max_bytes,
) -> (outcome, next_line, moved_rows, moved_bytes, complete)
```

`through_line` is the worker's observed session boundary. `after_line` is an in-memory keyset cursor.
Use the existing `(session, line_num)` index, ascending line order, and a fixed upper line boundary.
Do not repeatedly scan from line 1 for every chunk.

Each call performs one atomic transaction:

1. Invoke the existing revision guard first, so it obtains the write lock before reads.
2. Validate the same history generation and session boundary observed for the compute result.
3. Read bounded legacy candidates inside that transaction, ordered by line number.
4. Hash exact strings; insert missing content rows conflict-safely; resolve their numeric PKs.
5. Update current FK and set legacy content to `''` together. Preserve original FK.
6. Commit and return only small counters/cursor values to the event loop.

Ordinary candidates have `current_id IS NULL`. Known raw/new items already have both FKs and are skipped.
Unknown old transformed items move with original=NULL.
If an item has both stores populated, current FK wins: clear redundant legacy text without replacing current or original.
Such repair candidates should be handled explicitly, not through an unconditional overwrite.

Use row and byte budgets. A row count alone does not bound large tool-result strings.
Read candidates incrementally; stop after either budget, and allow one oversized row to guarantee progress.
The bound therefore includes the largest individual row, hashing buffer, and a bounded batch of insert/lookup objects.

The byte budget is about encoded content bytes. SQLite string length is not a UTF-8 byte count.
Avoid making a whole-session list before enforcing the budget.
No precise budget is selected without workload measurements; the existing 2,000-row constant is only a starting row cap.

Content-only slices do not change cost/activity, kind, facts, links, offsets, or compute version.
They need no aggregate contribution scan. Existing metadata chunks keep their existing aggregate bookkeeping.
They remain durable even if final metadata apply later fails, because logical current text stays readable.

### Crash and concurrency cases

| Event | Expected behavior |
|---|---|
| Crash before content/item transaction commits | Neither new FK nor inline clear becomes visible. Retry reads legacy content. |
| Crash after a storage slice commits | Moved rows are valid. Session remains outdated. Retry begins with fresh keyset traversal and skips moved rows. |
| Later result becomes superseded | Do not advance storage completion or compute version. Earlier storage-only slices remain safe. |
| Live append before writer acquires lease | Offset guard rejects the stale result. New live records already use the new table. |
| Watcher waits while writer applies chunks | Preserve existing writer ownership. SQL transactions release per chunk; the writer lease still spans the result. |
| History replacement or reset changes line identity | Reject mismatched generation; never apply a stale override to reconstructed history. |
| Session deleted | Existing missing outcome stops the apply. Retained content rows are harmless until future GC. |
| Known original=current row is recomputed unchanged | Keep both IDs. No provenance finalization is needed. |

`last_offset` alone is not a universal generation identity.
The current guard tests `last_offset <= observed` (`compute_base.py:3597`), while replacement resets offset to zero (`rollout_migration.py:382`).
Use `history_epoch`, already incremented during replacement (`rollout_migration.py:383`), for the new storage/result boundary.
Do not silently change the existing inequality's semantics without tracing other callers.

Transformed overrides should carry item identity and expected input identity where appropriate.
A numeric current PK is sufficient for already moved input; legacy input can carry its exact-string hash for changed rows only.
Validate stale inputs rather than overwriting a newer current content under an equal offset.
This is a result-protocol refinement, not permission for a worker to write DB rows.

Final completion checks occur under the final transaction's revision guard.
Confirm there are no unmoved items within the authoritative session boundary before setting storage version.
Confirm history epoch and session boundary still match; publish compute readiness only after successful final apply.

Do not create one global extraction pass before serving sessions.
Keep provider newest-first scheduling (`background_compute_task.py:619`, `codex/background_compute.py:176`).
Complete each recent session before moving to older sessions, as the current coordinator intends.

### Bound transformed strings separately from unchanged extraction

The writer-side sweep removes the new amplification risk: unchanged strings add no IPC payload.
Existing code still accumulates changed strings at `compute_base.py:3074` and serializes them all at `:3488`.
Splitting their DB apply at `db_writer.py:2018` does not bound worker or queue memory.

| Option | Assessment |
|---|---|
| Ship every old string in `session_complete` | Reject. Copies unchanged data into worker result, serialized bytes, queue, and writer objects. |
| Writer-side sweep plus current override transport | Smallest extraction change. No new full-session content payload. Existing transformed-string risk remains. |
| Add streaming content-chunk messages | Can bound strings, but expands run completion, error, ordering, and revision protocols. Partial transformations apply before metadata completion. |
| Worker-owned local spool of transformed overrides | Bounds additional strings without new early-apply messages. Final result ships a descriptor; writer reads bounded records under its existing lease. Adds temporary-file lifecycle. |
| Perform the same provider transforms again in the writer | Reject. Duplicates compute and can differ because of state and caches. |

**Recommendation for the first lot: writer-side legacy sweep, with the existing transformed-override transport.**
This avoids amplification caused by extraction without redesigning the existing compute protocol.
Existing changed-string memory behavior remains a documented limitation, not a newly introduced whole-history payload.

If a strict bound on existing changed strings becomes a separate requirement, a local spool is a possible option.
The following describes that optional improvement, not a required extraction step.
The worker writes each changed record as it computes it and does not retain the string in `content_overrides`.
Its final message includes a run/session/generation-bound descriptor, record count, and observed boundary, not all result strings.

The writer consumes that spool after metadata chunks and before the legacy sweep, with row and byte limits.
It validates each record's session/item/input identity and assigns immutable current-content rows transactionally.
The final completion message remains the sole publisher of readiness.

Spool files belong to the instance's private data area, not provider homes or versioned files.
Use a closed, flushed file before publishing the descriptor. Restrict its identity to the producing run/session.
Clean consumed/failed/abandoned spools through run lifecycle handling; reconcile orphan files on next start.

A crash requires recompute, not blind replay of an unowned old spool.
Already committed overrides are idempotent because they resolve immutable hashes and preserve original FK.
Spool consumption cannot provide a strict whole-worker memory bound: existing metadata/fact/link collections remain session-sized.
It does bound the additional changed-content string accumulation and queue serialization targeted here.

The spool is a proposal, not an existing interface. Its disk usage and cleanup need targeted tests.
If the first lot deliberately retains current override transport, state that limitation explicitly instead of claiming strict memory bounds.

### 9.3 Demand-driven accessors, loaders, and query helpers

**Recommendation: explicit current-string access, with SQL work confined to deliberate loading boundaries.**
Keep legacy `content` as a physical field while the bridge exists.
Do not rename it to a property or install an always-joined manager.

Proposed interfaces, documentation only:

| Interface | Responsibility |
|---|---|
| `current_content_text(item)` | Pure accessor for an in-memory override, an explicitly loaded current relation/string, or an already loaded legacy string when current FK is absent. No SQL. |
| `load_current_content_text(item)` | Explicit synchronous targeted load when the pure accessor lacks data. Must run in sync code. |
| `load_current_content_texts(items, strategy=...)` | Explicit synchronous page loader: targeted reads, a distinct-PK batch map, or a selected join. No original text by default. |
| Async content boundary | Materialize items and requested strings inside one `sync_to_async` operation, or use explicit async queries; serialize only loaded objects afterward. |
| `current_content_matches(lookup, value)` | Query predicate with current-FK priority and legacy fallback only when FK is NULL. |
| Content projection helper | Produces exact string tuples/rows for callers currently using `values_list("content")`. |

The pure accessor should fail clearly if a requested value was not loaded.
It must not confuse unloaded data, a valid empty stored string, a missing content row, and a synthetic frontend object.
Detect a missing referenced content row as corruption; do not fall back to obsolete legacy text when current FK exists.

For a chosen joined loader, read the related content field without an extra request.
For a batch loader, attach a private loaded-current string or pass a bounded PK map to the accessor.
Use an explicit sentinel for absent loaded data. A truthiness check would discard empty strings.

For one-item loading, relation caching can be appropriate and simple.
For a page with repeated large content PKs, a distinct-PK map avoids repeated large text allocation.
These private caches are bounded by the operation/page. They are not global caches.

The backend's unsaved live SessionItem currently carries a string before insertion (`compute_base.py:4171`).
Represent its source/current strings explicitly until content PKs are assigned.
That transient current string must take precedence over a previously loaded relation after an in-memory transform.
Invalidate or replace the private loaded current value when changing current FK.

Frontend synthetic items remain frontend objects under `parsedContent.js`.
They do not need content-table rows or backend FK emulation.
Provider pure parsing/transform helpers that accept dicts or strings remain independent of DB access.

### Explicit filter and projection semantics

Conceptual predicate for each substring term:

```text
(current_id IS NOT NULL AND current_relation.content <lookup> term)
OR
(current_id IS NULL AND legacy_content <lookup> term)
```

Repeated CLI terms remain AND-combined around these complete predicates.
Preserve existing case sensitivity, escaping, ordering, line limits, count, offset, and tail semantics.
Do not substitute extracted-text searching for raw JSON-string filtering.

For SQL projections, current-FK presence decides the source.
A CASE expression can project current string versus legacy string for a content-needed queryset.
An unconditional `COALESCE(current_text, legacy)` can hide a missing referenced row; avoid that fallback policy.

For joined projections, distinguish SQL NULL due to a missing parent from an empty string in a valid parent row.
Keep projected `(kind, text)` and `(line_num, text)` tuple shapes for watcher/history callers.
Alias explicitly instead of pretending the physical legacy column already contains the effective string.

`only()`/`defer()` policy:

- Metadata query: defer legacy text; no current/original relation text.
- Targeted content query: include current FK connector and requested fields if using a join.
- Batch content query: read item IDs/connectors and only legacy text for fallback candidates; fetch distinct current PKs separately.
- Original provenance query: fetch original text only when the caller explicitly requests it.

For mixed-storage batch loading, loading legacy text for every row can erase the intended benefit during transition.
Choose either a CASE projection or separate FK-present/FK-absent content fetches within the selected item set.
Preserve original item ordering and missing-line behavior after combining results.

Owner/share views materialize rows inside `sync_to_async` then serialize outside (`views.py:1539`, `share/session_views.py:155`).
Move all required string loading inside that boundary. The serializer calls the pure accessor only.
Synchronous compute, CLI, resolver, and search callers can choose targeted reads without an async bridge.

Metadata serializers must never call a content accessor.
Helpers that parse strings should receive effective strings from the chosen loader; they must not hide unrelated relation loading.
SQLite's efficient small-query model remains valid. Measure elapsed time and allocation for actual workloads before choosing joins.

### Remaining implementation checks

1. Implement the accepted **persistent original=current** convention consistently across all new writers and recompute paths.
2. Keep current transformed-string transport in the limited first lot. Consider strict-bound transport separately only if measurements justify it.
3. Select row/byte budgets and workload-specific loaders after measurement. No benchmark is run during this audit.
4. Trace history-epoch protection through both compute coordinators and result consumers before implementing the new generation checks.
5. Define missing-content invariant errors and retry reporting for the extraction paths.

Spool cleanup and error handling become decisions only if the optional transport improvement is selected later.
They do not block the recommended extraction process.

The recommended process keeps two content FK columns, numeric content PKs, and all line identities.
It adds no child FK indexes, original-to-result uniqueness, startup data migration, garbage collection, or vacuum.

## 10. Search methodology and limits

The audit uses repository reads only:

- Reads `AGENTS.md` and `AGENTS.local.md`.
- Searches `SessionItem`, `core_sessionitem`, `.content`, `content__`, `.only`, `.defer`, `values`, `values_list`, `bulk_create`, `bulk_update`, and raw SQL references.
- Traces common helper callers, serializers, sync producers/writers, compute payloads/chunks, and provider rewrite hooks.
- Uses AST inspection to map direct item-content attributes to enclosing functions.
- Searches frontend parsed-content helpers, store loading/invalidation, synthetic item construction, shares, and export/download terms.
- Searches tests and scripts for direct model construction and content assumptions.
- Inspects history facts, activity metadata, deletion, rollout replacement, original-file caches, and workflow synthesis.

Supplementary scripts reviewed: `scripts/benchmark_session_sync.py:108`, `:367` and `scripts/prototypes/sqlite_migration_checks.py:593`.
The benchmark measures metadata/resolver workloads; adapt it when using it to compare new loaders.
The migration prototype creates a historical test model; it is not a production item writer.

No test suite or performance benchmark runs for this documentation-only audit.
No production data is inspected, so transformation frequencies, deduplication ratios, and memory/latency gains remain unmeasured.
External consumers outside this repository cannot be enumerated. Keeping the public `content` string contract limits their impact.
Future code changes require repeating the searches above before considering the inventory complete for implementation.

## 11. Compression integration investigation

Date: 2026-10-11. Source checkout: `589344e7cc10cc058ca58560e3f99bf38cb95903`.
Scope: implementation feasibility and integration boundaries for the consolidated specification.
This section separates observed behavior, recommended mechanisms, and unresolved engineering validation.
It changes no application code, running instance, provider files, or production database.

### 11.1 Findings and readiness for planning

The extraction inventory remains reusable. Its proposed text projections are no longer the complete storage design.
Compression adds ownership updates, representation publication, root readiness, and lifecycle recovery.
No existing application mechanism already implements those new responsibilities.

The source supports a bounded maintenance coordinator using the current writer and synchronous executor.
Use separate writer acquisitions for maintenance slices. Do not enqueue one job that owns the lease for a complete pass.
Initial compute retains its existing result-apply lease; this is a different scheduling boundary.

Three integration risks need explicit design tasks before their implementation tasks:

1. Active-agent admission versus representation publication, including manager/writer lock ordering.
2. Final computed lifecycle dates versus initial compression eligibility.
3. Ownership removal cost for repeated transformed overrides in very large sessions.

These are engineering questions, not missing product decisions or codec choices.
The recommendations below make a plan possible, but do not claim these mechanisms already pass concurrency or performance tests.

### 11.2 Writer, executor, and maintenance lifecycle

| Source anchor | Observed behavior | Integration consequence |
|---|---|---|
| `providers/db_writer.py:1398`, `:1582` | Cancellation-safe writer ownership; each queue message owns a complete lease. | Submit one bounded maintenance apply per acquisition. A nested await or SQLite commit does not release the outer lease. |
| `providers/db_writer.py:1989` | Large compute results use per-slice transactions under one result-wide lease. | Reuse compute ownership for extraction. Do not infer interactive fairness from transaction chunking. |
| `providers/compute_executor.py:20`, `:28` | One dedicated DB worker, with measured queue/run timing. | Keep synchronous writer applies here. Long codec CPU jobs on this executor can delay unrelated applies even without a SQLite transaction. |
| `session_dirs_cleanup_task.py:190` | Filesystem cleanup holds every provider manager lock while its worker runs. | Keep this guard around filesystem work only. Never put a complete compression pass inside it. |
| `session_dirs_cleanup_task.py:207` | Whole task exits if filesystem cleanup is disabled. | Move that condition into the filesystem branch of combined maintenance. Promotion and recovery must remain enabled. |
| `cli/run.py:325` | Cleanup task joins cross-provider startup tasks and uses the common shutdown event. | Replace its role in the existing lifecycle; register the new coordinator's work with shutdown and writer drain. |

Recommended maintenance cycle:

1. Recover pending hot promotion and required ownership work first.
2. Run optional cold compression in bounded content-PK pages.
3. Retain existing directory cleanup as a separate guarded operation.
4. Keep the thirty-minute first periodic pass and twenty-four-hour interval.

Event-driven promotion uses the same coordinator immediately; it does not wait for the periodic timer.
CPU work prepares a candidate outside the publication transaction and, for maintenance, outside the writer lease.
Publication reacquires writer ownership and validates the candidate before replacing representation fields together.
Shutdown stops admitting candidates and drains admitted applies through the existing cancellation-safe wrapper.
No detached writer thread may continue after its ownership has been released.

### 11.3 Active protection and lock ordering

`agent/base_manager.py:650–707` adds the agent to the in-memory registry before persisting start timestamps.
Actual `agent.start()` follows that persistence.
Starts/resumes can already hold a provider manager lock while awaiting writer ownership.
`agent/registry.py:66` acquires manager locks for filesystem cleanup.

A registry snapshot before CPU compression is insufficient protection at publication time.
Acquiring manager locks while already holding the writer lease can deadlock against a start waiting for that lease.
Do not reuse `work_dirs_cleanup_guard` inside a compute apply or maintenance writer factory without resolving this ordering.

Selected admission design, validated in Task 1:

- Order: existing provider manager lock, when applicable → storage-admission fence → writer lease → DB transaction.
- `providers/db_writer.py` owns `storage_admission()` and creates its asyncio lock with the writer lifetime.
- Registration acquires the fence before installing registry state and before requesting writer ownership. Task 11 activates this hook.
- Compute `_drain_one` acquires the fence before its internal writer runner. It holds admission for one result application.
- Maintenance prepares candidates outside the fence. Its bounded publication enters the fence before the public writer runner.
- Fence holders never acquire provider manager locks. Registry snapshots use the existing lock-free `get_active_agents()` access.
- Snapshot active native roots while holding admission. Pass immutable protection into synchronous publication.
- Pending start admission excludes stale optional compression. Durable lifecycle transitions invalidate readiness and schedule required promotion.
- Acquiring admission from an admitted writer-held apply raises `RuntimeError`. Admission is not reentrant.
- Await the existing cancellation-safe writer runner directly inside admission. Its drive joins executor work before cancellation releases either lock.
- Never await a background child that reacquires writer ownership under its parent's lease. Await that child after release.
- Shutdown drains admission before external writer leases, then clears both locks. Final compute drain retains admission access during shutdown.

Deterministic tests pause fake registration before persistence and publication before worker completion.
Both compute drain and maintenance publication complete without manager/writer inversion.
Start admission wins the controlled ordering; stale publication skips the active root.
Cancellation keeps admission and writer ownership until the dedicated compute worker finishes.
These tests validate scaffolding; Task 11 repeats the scenarios against actual registration and representation publication.

The isolated admission probe compares 5,000 empty result applications.
Writer-only applications take 60.841 ms; fenced applications take 75.365 ms.
Added admission cost is 2.905 microseconds per result in this run.
A controlled 10 ms admission holder adds 10.151 ms wait to a competing publication.
The fence does not extend across an entire compute run or candidate CPU preparation.
Result-wide application can still delay starts; transaction chunking alone does not release admission or writer ownership.

### 11.4 Cold roots and mutation inventory

Resolve native roots through `parent_session`, with a visited-ID guard for cycles and missing ancestors.
Do not use `spawn_root` for this policy; that column describes independent orchestration sessions.
Batch maintenance can build a metadata-only native-root map and reuse one evaluation time per slice.
Property callers must not silently load a whole ancestry from a deferred model inside async serialization.
Use explicit root loading at async boundaries and a shared pure predicate over loaded state.

| Mutation path | Required integration |
|---|---|
| `core/services/session_update.py:410`, `apply_session_archived_change` | Save flag changes and readiness invalidation together under writer ownership. Schedule promotion after commit only if effective eligibility requires it. |
| `core/services/session_visibility.py:117`, `_apply_visibility_flag` | Add invalidation to the existing flag/activity transaction; preserve aggregate repair and broadcasts. |
| `views.py:1449` | Bulk archive bypasses the single-session service. Cover its direct update; do not rely only on service hooks. |
| `agent/base_manager.py:692`, `:1266` | Start and stop dates change directly. Cover runtime registration, start/resume, and stop. |
| `providers/compute_base.py:3103`, `:4035`, `:4560` | Full compute and live ingestion update lifecycle dates. Evaluate storage using the resulting root dates, not a stale worker snapshot. |
| `providers/claude_code/agent/manager.py:864–880` | Parent-side stop repairs descendant lifecycle state. Root eligibility and active protection must remain coherent. |
| `providers/codex/initial_sync.py:136`, `:245`, `:650`; `providers/sessions_watcher.py:748` | Parent discovery can occur during sync. Invalidate old and new affected roots when ancestry becomes known or changes. |
| `core/services/session_creation.py:477`; shared watcher creation | Sessions may start hidden. Initial readiness cannot assume a later explicit hide operation. |

Apply the effective root predicate to every native descendant at every depth.
A root-tree membership change invalidates the affected readiness even without an archived/hidden mutation.
Elapsed time requires periodic evaluation; it does not require periodic writes to a stored cold flag.
An unresolved root remains readable but cannot pass the public contains gate.

### 11.5 Durable warming readiness

Existing compute/search versions do not describe root-wide plaintext placement.
`history_epoch` describes transcript replacement, not archived/hidden or active-start transitions.
Do not overload those fields or rely on a volatile queue as the recovery record.

Recommended mechanism: nullable root storage-generation and verified-generation fields on `Session`.
The names are design proposals, not new external fields.
Advance the generation when tree membership, a required representation, or effective eligibility invalidates readiness.
A promotion task records its target generation and validates that generation before publishing completion.
Do not require per-descendant flag updates for one root transition.

Promotion projects only both content FK IDs through existing per-session item indexes.
Traverse native descendants in bounded metadata pages; deduplicate payload loading within each bounded slice.
Revisiting a shared content in another slice is harmless because plaintext promotion is idempotent.
A complete pass verifies all relevant extant references at the target generation before setting verified readiness.
Legacy inline rows are already plaintext but still obey extraction readiness and effective-storage selection.

Adding a compressed shared content to a hot tree invalidates its readiness in the reference transaction.
One shared row becoming plaintext satisfies all of its hot owners without duplicate payload writes.
Root readiness must still be verified independently; promoting one row does not prove a whole tree is ready.

After restart, a metadata-only session query discovers pending generations and schedules bounded promotion immediately.
Use a conservative NULL/unknown initial marker; no migration backfill scans item contents.
Cold roots remain rejected regardless of readiness markers.
Recheck current root state and readiness when a substring query starts; do not reuse an old Session instance's flags.

The plan must define generation invalidation alongside each mutation in section 11.4.
It must also define query consistency for a contains request racing a new hot reference.
A readiness check and its candidate selection cannot silently observe different eligibility generations.

### 11.6 Exact JSON ownership without reverse FK indexes

All new item FK assignments start from NULL columns.
Completeness can be established from the first writer mutation, rather than a full scan of old inline content.
An unconverted inline item does not yet reference a content row.
When later conversion reuses a compressed row for a hot session, it adds membership and invalidates readiness atomically.
Do not claim an undiscovered legacy duplicate is already an FK owner.

Recommended normal mutation algorithm:

1. Resolve content PKs without changing existing item references.
2. Persist actual item inserts or updates and determine which assignments survive conflicts.
3. Add memberships for those actual references in the same transaction.
4. Group old content IDs displaced by the bounded mutation batch.
5. Read surviving references for that session through both FK columns in one batched query.
6. Remove only memberships absent from both columns; commit list and item changes together.

Existing session indexes support step 5. Content-side FK indexes are unnecessary.
Do not run one surviving-reference scan for every content ID.
Repeated bounded scans of one huge session can still be expensive; this is a measured design concern, not a free lookup.
Avoid retaining an unbounded full-session Python reference-count map as the default solution.
Task 1 selects grouped reconciliation for isolated small batches.
Large multi-batch overrides use operation-scoped reference accounting when estimated item scans reach 1,000,000 across at least two batches.
This provisional threshold is an engineering choice from the isolated probe; Task 15 validates and tunes it.
Estimate item scans as session item count multiplied by expected mutation batch count.

Initialize a connection-local temporary SQLite table of content IDs and reference counts once per logical override.
Count both FK slots through the session index. Original=current contributes two references, but one membership.
Keep the existing writer lease across initialization and all mutation deltas; no concurrent writer changes the counted graph.
Update counts from actual persisted old/new assignments, after conflicts and override guards resolve.
Count deltas and item/list changes share each existing transaction; rolled-back changes also roll back their deltas.
Remove membership only when the surviving count reaches zero across both slots.
Drop the temporary table in `finally`; retry rebuilds counts from the authoritative persisted graph, including committed earlier chunks.
SQLite temporary storage bounds Python memory. It adds no durable schema or pending-recovery state.
Do not implement this as an unbounded full-session Python map.
Task 5 supplies this operation-scoped facility and connects it to bounded override callers.

Entire-history deletion knows that all references from that session disappear.
Stream its old FK IDs before deletion and remove that membership idempotently, without a final-reference scan per row.
Repeated IDs can skip an unchanged JSON write once the membership is already absent.
Retain existing transaction/rollback coupling for the complete history removal.
Streaming bounds memory, not transaction duration. Do not split deletion or add pending-reconciliation state in this release.
After replacement, new chunks add their actual memberships again, including reused content IDs.
No payload text is required to maintain membership.

Do not use Django signals as the only integration mechanism.
Bulk creation, direct updates, raw SQL overrides, and queryset deletion use several different paths.
Ownership belongs in the audited writer/reference operations.

### 11.7 Destructive paths and recovery

| Source anchor | Observed behavior | Required ownership/readiness change |
|---|---|---|
| `providers/db_writer.py:2516` | Deletes a selected native tree and repairs aggregates in one transaction. | Collect both old FK sets before cascade; remove actual deleted session memberships; retain content rows. |
| `providers/codex/initial_sync.py:618` | Guardian reconciliation uses `DeleteSessionsPayload`. | Shared deletion integration covers this path; no separate Guardian content collector. |
| `providers/codex/rollout_migration.py:350–395` | Replacement deletes old items, increments `history_epoch`, sets offset zero, then inserts chunks. | Invalidate storage/readiness and reconcile old memberships at begin; add new memberships per chunk; preserve unfinished-replacement recovery. |
| `core/management/commands/sync.py:20` | `sync --reset` deletes Projects directly; cascade bypasses provider writer jobs. | Add explicit ownership reset handling here. If all sessions disappear, retained contents have empty ownership, never a guessed surviving tree. |

Project cascade is a separate deletion mechanism even though its eventual SessionItem deletion is automatic.
Direct reset handling must complete or durably mark reconciliation before reimported sessions reuse the same IDs.
A reused session ID cannot inherit stale readiness or membership proof from the deleted session.
Dictionary/content retention remains independent from item deletion.

### 11.8 First compute, final metadata, and generations

`background_compute_task.py:619` orders stale sessions by descending mtime, across roots and subagents.
There is no guarantee that a root computes before every descendant or that a whole tree computes as one unit.
Maintain that newest-first ordering; do not add a tree-wide compute prerequisite for normal reads.

`guard_compute_revision` currently checks observed offset through a first-statement no-op UPDATE.
It does not check history epoch, lifecycle flags, root ancestry, or compression ownership.
Storage apply must add those specific checks without changing unrelated callers' revision behavior.

A final metadata update can change the dates used by the cold predicate.
Initial compression cannot rely only on dates loaded before metadata compute.
Either make the relevant final fields authoritative before representation reconciliation, or include their guarded effective values in the eligibility decision.
Final publication must recheck the resulting state; a compute result must not mark a newly hot root plaintext-ready with compressed references.
This ordering must apply to small-result and chunked-result paths alike.

A new content can be compressed directly in a bounded extraction slice when its persisted owners and effective final root state authorize it.
A new known source and a transformed result are separate content rows; both need correct owner membership and placement.
Shared rows must be reused independent of codec and verified by exact decoded equality when needed.
Initial cold compression must not add all unchanged source strings to compute IPC.
Use bounded writer-side source reads as specified in the existing extraction process.

A cold root or subagent can complete metadata compute before other cold descendants finish extraction.
Root-wide contains readiness remains separate; recent root availability must not wait for every old descendant.
Uncertain ownership, ancestry, or final dates postpone optional compression rather than block correct extraction.

### 11.9 Effective loading and binary text predicates

Keep a pure effective-content accessor and explicit synchronous/async loaders from the extraction audit.
Extend loaders to return exact decoded strings from legacy text, plaintext UTF-8 BLOBs, and zstd payloads.
Load only current payloads for ordinary consumers, and only explicit originals for provenance operations.
A batch content-PK map avoids loading/decompressing a shared large row repeatedly on one page.
Prepared dictionaries can be cached by immutable identity with a bounded cache; lookup must not create per-item lazy SQL in async serialization.

Do not route every read or decoder CPU call through the single writer executor.
Normal read boundaries may use their existing DB async/thread path; CPU decode must not block the event loop.
Each worker/connection must respect existing Django connection cleanup.
Return loaded decoded values before serialization leaves that boundary.

For ready-hot public raw contains, `CAST(plaintext_payload AS TEXT)` is a viable SQL expression direction.
Use current-FK presence to choose new versus legacy storage; do not fall back from a missing current row.
Preserve Django LIKE escaping and current SQLite Unicode/case behavior.
Do not use Python `casefold()` as an assumed replacement for SQLite LIKE.
The cast expression requires no new index and no item-table rebuild.

A small in-memory SQLite probe checks TEXT versus CAST UTF-8 BLOB matching for ASCII case, accented text, escaped percent/underscore, embedded NUL, and empty text.
All six probes match the original TEXT result.
The local SQLite build also allows LIKE on BLOB in those probes; this is not relied upon as a portable contract.
These probes use Python's local SQLite build, not a Django ORM migration or deployment matrix.
The plan still needs ORM-level matching regression checks on the target runtime.

### 11.10 Compression-safe internal filtering

| Consumer | Preserve | Recommended fallback boundary |
|---|---|---|
| Claude task-state rebuild, `claude_code/compute.py:872` | Latest textual snapshot candidate, then task replay and duplicate IDs. | Descending decoded candidate pages for the snapshot; ascending decoded replay pages. Keep existing parser and occurrence ordering. |
| Images, `compute_base.py:1535–1602`, both provider overrides | Provider text-prefilter result order and `[:images_needed]`, then image extraction. | Decode ordered candidates, apply equivalent prefilter, then its row limit; keep same-batch in-memory images first. |
| Hybrid attachments, `views.py:1559` | Next 500 lines, first five textual matches, then structure/base64 validation. | Decode bounded line window, select first five prefilter matches, then preserve parser validation. |
| Workflow links, `views.py:1754` | All runId text candidates, validation, deduplication. | Stream decoded candidate pages; no assumed complete tool kind filter. |
| Workflow synthesis, `claude_code/workflow_synthesis.py:162` | run ID/result lookup and linked launch arguments. | Existing tool links where authoritative; ordered decoded fallback when links are incomplete. |
| Spawn resolution, provider compute helpers | Exact tool ID/prompt evidence and earlier-line boundary. | Reuse `iter_resolver_items`, extending its effective loader. |

`history_facts.py:115` explicitly falls back to raw pages when compute version is stale.
A compute-version bump therefore removes the indexed fast path for old sessions during conversion.
Partial facts or a kind guess cannot replace this fallback.

Some internal predicates are only prefilters, but their matching semantics and limit placement are still observable.
Simply replacing a filtered queryset with an unfiltered queryset and keeping its slice loses later matching records.
Some helpers currently return QuerySets whose callers slice them; both the producer and caller must change together.
A decoded iterator alone is not a compatible drop-in QuerySet replacement.

Keep textual prefilter behavior distinct from structural validation.
A Python fallback must reproduce relevant SQLite ASCII case/escaping/NUL semantics or use a tested equivalent matching helper.
Returning only hot rows is never a valid fallback for required internal features.
No new facts table or complete JSON indexing project is required for first correctness.

### 11.11 Dictionary policy and no-gain integration

The current generator uses the reviewed explicit provider policy and legacy inline query.
Generate the first immutable assets before changing the sampling query to extracted storage.
After extraction, sampling must load/decode effective current contents without requiring plaintext promotion.
Do not mix transformed originals and current rows arbitrarily when evaluating a provider corpus.

A cross-provider shared row needs stable encoding affinity, not multiple payloads.
Recommended rule: retain the first assigned provider affinity on the content row and select its versioned dictionary policy.
The stored dictionary identity remains authoritative for decoding across all owners.
Adding this affinity is an internal schema choice, not an external provider field or a change to content hash identity.

Store attempted-policy identity and no-gain separately from codec state.
Cold maintenance skips only the same unsuccessful policy; a successfully promoted row remains recompressible.
A policy change can retry plaintext no-gain rows without a data migration.
Codec exceptions and corruption do not become no-gain outcomes.
Dictionary seeding affects only small new tables; inspect migration effect checks to ensure it does not broaden validation to legacy item rows.
New readiness/session fields must retain nullable, no-default additions to avoid rebuilding existing tables.

### 11.12 Narrow probes and limits

Task 1 uses isolated synthetic SQLite WAL databases, not a production database copy.
The ownership fixture has 100,000 items, an existing session index, and both nullable content FK columns.
It includes 1,200 content rows with 512-byte payloads and two 36-character ownership IDs.
Current IDs repeat across 1,000 contents. Original IDs repeat as 1, 2, 3, NULL, and 1,001.
The displaced batch contains IDs 900–1,099; 102 survive through at least one slot.
No reverse-FK indexes exist. `EXPLAIN QUERY PLAN` uses the session index.
Queries stream 256 rows per page. Allocation measures peak Python memory with `tracemalloc`.

| Operation | Elapsed | Peak Python allocation | Main WAL | Result |
|---|---:|---:|---:|---|
| One grouped 200-ID reconciliation | 85.223 ms | 70,167 bytes | 0 bytes | 102 surviving IDs |
| 200 per-ID `EXISTS` scans | 1,234.231 ms | 17,992 bytes | 0 bytes | Same 102 IDs |
| Fifty grouped reconciliation batches | 3,617.830 ms | 47,063 bytes | 0 bytes | Same result per batch |
| Streamed ownership removal and whole-history deletion, one transaction | 750.621 ms | 87,646 bytes | 11,280,592 bytes | 100,000 items deleted; 1,001 ownership writes |
| Temporary count-table initialization from both slots | 54.630 ms | 808 bytes | 0 bytes | 1,001 rows; 20,480 SQLite temp page bytes |
| Fifty indexed count-table lookups | 19.997 ms | 14,042 bytes | 0 bytes | Same 102 IDs per batch |

Grouped reconciliation is approximately 14.5 times faster than per-ID scans on this fixture.
Repeated whole-session scans dominate large override workloads: count initialization plus fifty lookups takes 74.627 ms.
The temporary count probe measures initialization and lookups, not subsequent assignment-delta writes.
Its fixture preserves the same item reference distribution; it omits payload rows because counting never reads payload bytes.
Use operation-scoped accounting for the large multi-batch case described in section 11.6.

Whole-history removal retains the existing transaction in this release.
The measured 0.751-second lease and 10.76 MiB WAL are acceptable for this representative fixture.
This is no latency bound for larger histories, larger ownership lists, or slower disks.
Repeated originals produce one ownership update per distinct row; surviving unrelated ownership remains intact.
Read-only scans add no WAL. Membership writes and deletion remain coupled in one commit.
These observations do not require a replacement durable schema or a recovery-contract change.

Measurements run on the development host while the full test suite runs.
Python allocation excludes SQLite native memory; temp page bytes report the count-table footprint separately.
Synthetic rows omit unrelated production item columns and do not establish production WAL amplification.
Temporary databases and scripts are removed; execution logs remain under the ignored `.superpowers/sdd/` task directory.
Synthetic JSON ownership sizes remain 40 bytes for one UUID, 3,901 bytes for 100, and 39,001 bytes for 1,000.
Ownership metadata can dominate very short shared contents.

The existing payload benchmarks already cover small, medium, and multi-megabyte contents.
Do not repeat codec-level selection merely to start the plan.
Further focused validation belongs to these design tasks:

- Repeat the validated admission ordering against real registration and representation publication.
- Validate persisted reference mutation, transactional removal, and temporary count deltas against the real storage schema.
- Root generation recovery and a contains request racing a new compressed hot reference.
- Actual Django CAST/LIKE queries and decoded internal candidate limits.
- Combined schema migration including dictionary seeds and nullable readiness fields.

These checks need no product-policy change.
A detailed plan must schedule their design/validation before dependent implementation, rather than treating them as already solved.


### 11.13 Preserved measurements and dictionary validation

This section consolidates useful evidence from the removed benchmark and dictionary-review reports.
It does not replace the implementation validation gates or claim whole-DB performance.

#### Codec choice

The component benchmark evaluates 6,000 real records totaling 29,043,801 UTF-8 bytes.
The machine uses an Intel Core i7-10510U, SQLite 3.53.1, and Python zstandard 0.25.0.
Numbers below use no dictionary, avoiding gains from private experimental dictionaries that are not distributable.

| Codec | Stored fraction of raw payload | Compression time, whole sample | Median decode time, whole sample |
|---|---:|---:|---:|
| zstd level 1 | 37.72% | 122.75 ms | 37.49 ms |
| zstd level 3 | 36.72% | 126.38 ms | 38.78 ms |
| zstd level 6 | 35.68% | 305.75 ms | 36.83 ms |

Level 3 is the selected compromise. A no-gain fallback remains necessary for individual rows.
These fractions measure payload bytes, not SQLite file size.

#### Requested reads versus persistent read caching

The 100-row page contains 264,057 raw bytes.
Its warm-cache component experiment uses zstd level 3 and a local experimental dictionary, not the final public release dictionary.

| Operation | Median time for that page |
|---|---:|
| Decode requested contents only | 1.298 ms |
| SQLite read plus decode | 1.627 ms |
| Replace payload with plaintext and commit | 6.107 ms |
| Plaintext reread | 0.358 ms |
| Recompression CPU | 5.044 ms |
| Replace payload with compressed bytes and commit | 0.843 ms |
| Separate explicit checkpoint | 11.848 ms |

Promotion/recompression add writes and cost more than a single read-time decode in this experiment.
A persistent cold-read cache is therefore excluded; bounded decoded memory caching remains an optional measured optimization.
Plaintext promotion for a genuinely hot owner is required independently from this cache comparison.

Eight targeted records range from 4,468,470 to 11,528,181 raw bytes, totaling 63,036,395 bytes.
Individual decode medians range approximately 4.5–13.3 ms using that experimental dictionary.
This targeted sample is neither random nor a disk-cold latency guarantee.
It establishes that the experiments include multi-megabyte contents, not only small messages.

Promoting/recompressing the twenty largest records in a 6,000-row isolated DB grows its file from 10,010,624 to 15,417,344 bytes.
The resulting 1,314 free pages remain reusable; checkpointing does not shrink the file.
Do not promise immediate on-disk shrink from extraction or compression. Vacuum remains outside this release.

#### Public dictionary validation

The definitive allowances live in [content_dictionary_policy.json](../../scripts/content_dictionary_policy.json).
The [offline generator](../../scripts/benchmark_public_schema_dictionaries.py) reads that explicit policy; it does not discover additional allowances from data or source.

| Provider | Explicit keys | Field-specific constant entries |
|---|---:|---:|
| claude_code | 76 | 48 |
| codex | 94 | 71 |

Allowed constants apply only to their declared field and provider.
Unknown keys and unknown typed blocks disappear. Whole tool-result, text, thinking, and reasoning blocks are excluded.
Excluded Codex payloads propagate through response/item-completed wrappers.
Other values become fixed synthetic values, including commands, paths, IDs, timestamps, numbers and booleans.
No custom MCP name or private literal becomes an allowance.

The loader rejects duplicate/forbidden keys, undeclared enum fields, and non-string enum values.
Validation covers cross-field/cross-provider isolation, unknown types/keys, case variants, nested result exclusion, and exact codec round trips.
The selected dictionaries use raw public templates, zstd level 3, and a maximum 16,384 bytes per provider.

| Provider | Training rows | Project-disjoint evaluation rows | Dictionary bytes | Smaller compressed payload than no dictionary |
|---|---:|---:|---:|---:|
| claude_code | 28,923 | 1,677 | 16,384 | 4.68% |
| codex | 17,321 | 1,690 | 12,517 | 4.23% |

The run targets 60,000 random IDs across the database without per-session quotas.
Training and evaluation separate projects, use session fallback for NULL projects, and exclude shared raw hashes.
Actual contents remain in memory for evaluation; dictionary inputs contain only reviewed public/synthetic material.
The source database changes between experiments, so earlier runs are not paired comparisons with these figures.
Trained dictionaries perform worse than no dictionary on this restricted corpus; raw templates remain selected.

The public policy is validated. Final immutable release bytes still need generation, versioning, manifest SHA256, packaging and small migration seeding.
Old dictionary identities remain available while contents reference them. Installed users perform no live training.
Temporary samples/candidates and separate raw benchmark result files are not retained.

## 12. Integrated implementation validation

This section records the final implementation gates for the integrated storage release.
The earlier sections preserve the investigation and its historical measurements.
Task 15 uses the isolated `feature/content-storage-compression` worktree and its authorized database copy.
No application starts against that database.

### 12.1 Final production inventory

The final search covers every Python file under `src/twicc`, with historical migrations checked separately.
It searches attribute access, content projections/predicates, SQL writes, item construction, deletion, serialization, and lifecycle/parent mutations.
The complete search output remains in the ignored task workspace; private payloads never enter these logs.

All remaining live `SessionItem.content` accesses have these explicit purposes:

| Exact path and occurrence | Effective-storage path or exemption |
|---|---|
| `core/content_storage/readers.py:120` | Deferred legacy projection executes only for items whose current FK is NULL. FK contents use bounded PK payload reads. |
| `core/content_storage/readers.py`, `effective_current` | The accessor checks an explicit override, then the loaded current FK identity, then legacy text only when the FK is NULL. It performs no SQL. |
| `core/content_storage/readers.py`, `effective_plaintext_expression` | Ready-hot SQL chooses CAST(current payload AS TEXT), otherwise legacy text. Public contains first checks root readiness. |
| `core/content_storage/extraction.py:125,140,149,151,157,160,162` | Bounded extraction reads legacy bytes, checks the observed row, interns only NULL-current items, binds persisted assignments, and clears legacy text. |
| `core/content_storage/extraction.py:197,212,213` | A legacy override checks its observed digest. Guarded current-FK assignment and clearing share the membership transaction. |
| `core/content_storage/ingestion.py:65,67` | New persisted items clear inline text and use current/original FKs. Conflict checks precede authoritative ownership binding. |
| `providers/compute_base.py:3093` | The worker pairs its in-memory assignment with `set_current_override`. Its IPC contains only changed text and the observed input identity. |
| `providers/compute_base.py:4275,4286` | Live transformations pair in-memory assignments with `set_current_override`. Ingestion receives the separately retained original source. |
| `providers/compute_base.py:4245`, `providers/db_writer.py:2507,2531`, `providers/codex/rollout_migration.py:409` | Unsaved item construction carries source text into `insert_ingested_items`. These constructors do not persist legacy producer fallbacks. |
| `context_injection.py:41,159`, `search_indexing_task.py:6,478` | Historical explanatory comments reference the old field name. Executable consumers use explicitly loaded effective current strings. |

Line references identify the final implementation before this validation section changes documentation only.
The following `.content` occurrences describe different objects and never read a SessionItem:

- HTTP response bytes: `cli/_remote.py:788,877`; `inline_artifacts/views.py:62,63,199,200`; `share/inline_artifact_views.py:70,71,134,135`; `uploads/views.py:160`.
- Claude SDK messages: `providers/claude_code/hermetic.py:151`; `providers/claude_code/agent/agent.py:1576,1577,1675,1684,1685,1689,1690,1691,1694,1937`.
- Provider JSON comments: `providers/codex/compute.py:349,4930`; `providers/claude_code/compute.py:1564`.

No live consumer applies `content__contains`, `content__icontains`, or a raw legacy content projection.
The only legacy string projection is the explicitly NULL-current reader above.
`references.py` projects both FK IDs for binding validation, session-scoped survivor reconciliation, and whole-history membership removal.
Its raw SQL creates and updates only connection-local temporary count tables.
No raw SQL writes the item payload column or changes native ancestry.
The maintained `session_ids` list authorizes eligibility only after storage completion; compression does not perform per-content reverse item scans.

| Consumer family | Exact implementation paths and loading contract |
|---|---|
| REST, WebSocket, shares | `views.py`, `core/session_queries.py`, `core/serializers.py`, `share/session_views.py`: explicit `load_current`/`aload_current`, then `serialize_session_item`/`effective_current`. Raw-string wire content remains unchanged. |
| CLI/RPC/MCP | `cli/session.py`, `cli/_wait_reply.py`, `core/agent_runs.py`, `mcp/events/prompts.py`, `providers/helpers.py`: requested rows load before parsing. Public contains uses the ready-hot expression and root gate. |
| Compute and internal predicates | `providers/compute_base.py`, `providers/claude_code/compute.py`, `providers/codex/compute.py`, `providers/history_facts.py`: bounded current readers and decoded matching retain complete fallback candidates and match order. |
| Watcher tasks and workflows | `providers/claude_code/sessions_watcher.py`, `providers/codex/sessions_watcher.py`, `providers/claude_code/workflow_synthesis.py`: bounded decoded task/image/workflow candidates. |
| Search | `search.py`, `search_indexing_task.py`: explicitly load requested/indexed items; Tantivy document identity and archive/hidden policy remain unchanged. |
| Metadata-only queries | `share/display.py`, `share/consumer.py`, `usage.py`, `sync_helpers.py`, `providers/live_aggregates.py`, `mcp/events/runtime.py`, `core/models.py`, `providers/claude_code/agent/hybrid/agent.py`: timestamps, kind, grouping, aggregate, and existence queries need no payload. |
| Offline dictionary sampling | `scripts/benchmark_public_schema_dictionaries.py`: sampled IDs select current FK bytes, decode immutable dictionaries, validate identity/UTF-8, and reject integrity failures without stale fallback. |

The destructive boundaries remain explicit:

- `providers/db_writer.py::_apply_delete_sessions_payload` computes the actual native cascade and removes memberships inside its deletion transaction.
- `providers/codex/rollout_migration.py::_begin_replace_codex_history` removes memberships, deletes history, and invalidates the generation atomically.
- Replacement chunks call `insert_ingested_items`; replacement reuse restores exact final memberships.
- `core/management/commands/sync.py::Command.handle` resets all Projects and their Session cascades inside one transaction.
- `Session.project` is nonnullable in `core/models.py:403`; `project_id` has a database NOT NULL constraint.
- The earlier diagnostic confirmation of nullable Session.project is incorrect. A rejected fixture confirms the NOT NULL constraint.
- The NULL-project survivor risk is inapplicable. The proposed Collector reset correction is rescinded; reset semantics remain unchanged.
- Orchestration `spawned_by`/`spawn_root` use SET_NULL and preserve independent sessions. The linked survivor regression verifies retained membership.
- No content row or dictionary is garbage-collected at these boundaries.

Lifecycle and parent writes use `core/content_storage/lifecycle.py` and ingestion generation initialization.
The covered mutation boundaries are `core/services/session_update.py`, `core/services/session_visibility.py`, `providers/db_writer.py`, `providers/compute_base.py`, and `providers/sessions_watcher.py`.
Provider initial-sync modules construct payloads; the serialized DB writer persists them through ingestion.
`agent/base_manager.py` and provider managers capture active protection under storage admission before lifecycle publication.
Direct writes to search versions, titles, settings, tasks, costs, offsets, mtime, and orchestration links do not change cold policy or native ancestry.
Historical migrations keep their original legacy operations and order; frontend `content` fields remain wire names.

### 12.2 Bounded-memory correction and focused acceptance

Task 15 finds an actual retention-budget defect in maintenance preparation.
An encoded-byte budget admits several highly compressible 5 MiB rows, then retains their decoded promotion candidates together.
Two focused RED tests observe three candidates where the 8 MiB contract permits one.

Both maintenance selectors now charge:

`raw_size + length(payload) + length(CAST(session_ids AS BLOB))`.

Selectors fetch scalar lengths before parsing an admitted row's owner list.
The final measured row limit is 100; the byte limit remains 8 MiB.
Section 12.6 records the measured reduction from the provisional 500-row limit.
One oversized row advances alone; null metadata traversal remains bounded by the row limit.
Direct requested loaders retain their existing `raw_size + stored_size` cost and 256-row limit.
They never fetch owner lists.

A separate RED test shows that two large owner lists bypass the old byte selection.
The correction admits only the first oversized owner-list row, even when its incomplete owners make compression ineligible.
The focused promotion/maintenance gate passes 38 tests after both corrections.

Additional final regressions cover direct loader row/byte batches, eleven offline generator integrity-rejection branches, and an independently linked orchestration survivor.
The count-table test now keeps FK assignments, binding, and membership reconciliation in the same atomic transaction.
Its name explicitly identifies savepoint rollback; existing ingestion/extraction tests cover real commit failures and retries.
The existing downloaded Codex 0.162.0 binary passes all five throwaway-home integration tests.
Its old test consumer reads effective current contents after rollout replacement.

### 12.3 Representative-copy migration gate

The authorized target is `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression/db/data.sqlite`.
The original copy comes from devctl's SQLite backup setup; no backend starts or migrates it before this task.
A first probe incorrectly uses runtime `twicc.settings` directly with `MigrationExecutor`.
That bypasses the normal migration subprocess's `twicc.settings_migration` selection and triggers Django's standard global FK check.
The failed assertion and log remain explicit evidence of this probe error, not a product migration measurement.

The controller authorizes refreshing the same target with `source.backup(target, pages=-1)`.
The source opens read-only at `/home/twidi/.twicc/db/data.sqlite`; an assertion excludes source/target equality.
All target handles close before refresh; no second 22 GiB copy, reverse migration, or shadow migration is created.
The refresh takes 500.553 seconds, including SQLite's same-target WAL work.
The completion log timestamp is `2026-10-11T21:28:24.037322+00:00` (UTC).
Its handles close and its sidecars disappear before the corrected probe.

The corrected fresh process selects `twicc.settings_migration` and asserts `twicc.db.backends.sqlite3`.
It prints and asserts the resolved target path before migration execution.
It checkpoints only the isolated target; the baseline reports `(0, 0, 0)`.
The pending plan contains only forward `0156_content_storage_schema`.
Incremental SQL logging and a timed stack dump protect the diagnostic against an unexplained long scan.

| Snapshot measurement | Value |
|---|---:|
| SQLite version | 3.53.1 |
| Database bytes before migration | 22,690,983,936 |
| SessionItem rows | 3,937,868 |
| Claude sessions | 11,606 |
| Codex sessions | 2,849 |
| Schema migration duration | 0.230572 seconds |
| Nullable ALTER additions | 6 |
| Existing Session/SessionItem table rootpage changes | 0 |
| New SessionItem indexes | 0 |
| Existing-item global FK checks | 0 |
| Existing-content conversions | 0 |
| Seeded Claude/Codex dictionary bytes | 16,384 / 12,496 |

Four ALTER statements add nullable Session state fields.
Two ALTER statements add nullable, unindexed item content FKs.
The only FK check is `PRAGMA foreign_key_check("core_sessionitemcontent")`, against the newly created empty dependent table.
Dictionary seeding reads only the immutable package assets.
No item-table rebuild, content-data update, column removal, vacuum, or database-wide conversion runs.
Backup and the first full test run finish before the corrected migration and workload timing.
The copied database remains in the worktree after validation.


### 12.4 Initial reconciliation correction

The measured initial reconciliation repeats owner/root queries for every content row.
Its previous item-based loop also loads both oversized FK payloads before enforcing a byte limit.
RED tests confirm both failures and the hot-plain unnecessary payload/owner hydration.

Reconciliation now selects item FK IDs and scalar content lengths in bounded pages.
Its cost includes raw bytes, stored payload bytes, and UTF-8 owner JSON bytes.
It fetches owner JSON and payload only after admission.
One oversized content reference advances; a second oversized reference waits for the next slice.
An operation-local `(line, slot)` cursor resumes between current and original references.
The public extraction cursor and worker IPC contract remain unchanged.
A line completes only after both slots complete; a shared FK decodes once.

A guarded hot or unresolved incoming owner proves that existing plain bytes can remain plain.
That path performs no owner JSON or payload hydration.
Cold candidates reuse owner/root eligibility only within one bounded preparation slice.
Dictionary selection also reuses one lookup per provider within that slice.
Publication starts a separate, fresh owner/root cache inside one atomic transaction.
The existing write guard proves root incarnation, epoch, and pending lifecycle before any batch write.
Each changed row verifies its exact payload, SHA-256, raw size, codec, dictionary, owner list, and item FK assignment.
SQLite writer ownership prevents a concurrent root change during this transaction.
No eligibility cache crosses transactions or slices.
A stale guard rolls back the batch and leaves its cursor unchanged.
No per-content reverse item scan or persistent cache is added.

Focused tests cover distinct oversized FK resume, shared FK completion, scalar owner-byte admission,
hot-plain hydration avoidance, bounded cold-owner queries, and epoch rejection before publication.
The full focused storage selection passes 126 tests in 11.73 seconds after this correction.

### 12.5 Workload instrumentation

All workloads use only the authorized worktree copy and database-only synthetic sessions.
No provider transformation, live server, or shared filesystem side effect runs.
The original workload uses `DEBUG=True` plus conversion `tracemalloc`; it is diagnostic evidence only.
That instrumentation records 161.255 seconds for 20,000 controlled items.
Its 500-row maintenance publication slices take 2.091 and 1.941 seconds.
These values are not normal user latency measurements.

Normal latency uses a fresh process with `DEBUG=False` and no conversion tracing.
A repeated fixture assertion detects accumulated synthetic owners from the earlier probe.
The corrected probe uses a unique shared string and unique large-history strings per run.
Both diagnostic logs remain available in the ignored plan workspace.
Backup and the initial full test suite finish before these measurements.
Only the focused tests overlap the corrected workload; the final full suite starts after workload measurements.


| Normal workload, DEBUG=False, untraced conversion | Measurement |
|---|---:|
| Actual Claude sample, 600 rows / 4,753,034 UTF-8 bytes | 1.503 seconds |
| Actual Codex sample, 600 rows / 1,772,551 UTF-8 bytes | 1.379 seconds |
| Recent one-row session, before large history | 0.0394 seconds |
| Unique controlled history, 20,000 items | 9.806 seconds |
| Unique 20,000-item conversion WAL growth | 98,274,360 bytes |
| Shared 64-owner workload | 5.400 seconds |
| Shared 64-owner JSON bytes | 3,457 |
| Shared workload WAL growth | 7,350,080 bytes |
| Cold 5,040,035-byte current read | 0.0223 seconds; zero WAL growth |
| Three 5 MiB rows: promotion entries per slice | 1 |
| Three 5 MiB rows: root readiness | 3 slices; 0.105–0.109 seconds |
| Promotion Python allocation peak | 10,544,131 bytes |
| Recompression Python allocation peak | 15,931,092 bytes |

The large cold read measures 20,175,834 bytes of transient Python allocations.
Read validation and UTF-8 decoding require transient copies; the persisted representation remains compressed.
Promotion and recompression peaks use separate `tracemalloc` measurements with `DEBUG=False`.
A separate traced conversion probe records 18,884,717-byte Claude and 7,227,977-byte Codex Python peaks.
Its 20,000 unique items take 50.108 seconds under tracing and peak at 2,698,969 Python bytes.
Tracing therefore changes timing materially; normal latency values use the untraced probe.
The traced process RSS high-water mark is 167,816 KiB.

The actual ownership-accounting comparison uses 20 repetitions over 200 displaced IDs in a 20,000-item session.
Grouped accounting takes 0.2366 seconds; temporary counts take 0.0407 seconds plus 0.0229-second initialization.
The existing threshold remains at least 1,000,000 items and at least two estimated batches.
No lower threshold is selected from this smaller-session diagnostic.
Tests and measured service workloads confirm interrupted committed slices, mixed reads, retry,
native descendants, shared hot reuse, history replacement, and no-gain policy retry.
Unchanged content remains absent from worker IPC; recent-first discovery and wire tests pass.

### 12.6 Maintenance row-budget tuning

The provisional 500-row budget causes ordinary interactive waits above one second.
A fresh untraced `DEBUG=False` comparison uses unique 1,200-row fixtures for each candidate limit.
Each candidate runs actual storage admission, writer ownership, and compute-executor publication.
An interactive title write queues during every publication and finishes before the next maintenance slice.
Preparation runs on the separate preparation executor without writer ownership.

| Rows per slice | Total 1,200-row maintenance seconds | Maximum publication seconds | Maximum interactive wait seconds | Median interactive wait seconds |
|---:|---:|---:|---:|---:|
| 500 | 8.956 | 1.675 | 1.681 | 1.609 |
| 128 | 8.466 | 0.470 | 0.471 | 0.418 |
| 100 | 8.563 | 0.384 | 0.385 | 0.340 |

The final `content_maintenance_task.ROW_BUDGET` is 100.
It provides more interactive latency headroom than 128 with similar measured throughput.
Measured total time improves 4.4 percent versus 500 in this run.
The byte limit remains 8 MiB, with one oversized candidate permitted for progress.
Initial extraction retains its separate 256-row limit.
An additional mixed hot/cold scan confirms empty publication slices release the writer between pages.
Those queued interactive writes take approximately 2 milliseconds.
Executor queue waits are recorded separately from writer-plus-executor waits in the ignored diagnostic log.
The first publication includes executor connection initialization; later waits do not.
No broader metadata-proof optimization is selected from these measurements.

Physical SQLite file shrink is not an acceptance condition.
The copied database retains legacy columns and free space; WAL growth measures writes, not disk reclamation.


The combined payload census after the mixed workload, before the separate traced conversion,
records only extracted rows; the remaining legacy production-copy items remain unchanged.

| Codec | Extracted rows | Raw bytes | Stored payload bytes | Stored owner JSON bytes |
|---|---:|---:|---:|---:|
| plain | 40,307 | 26,324,380 | 26,324,380 | 2,930,370 |
| zstd | 2,327 | 28,067,114 | 3,538,414 | 311,435 |

This census includes representative records and controlled repeated-content fixtures.
It does not estimate the compression ratio of all production history.
The promotion timing uses the probe's known complete three-row native-tree pass proof.
Production pass-proof and stale-proof tests cover the normal coordinator path.


### 12.7 Final verification gate

The final complete backend run executes after all production corrections and measured budget tuning:

`cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q`

It passes **10,073 tests**, skips 26, and records three unchanged baseline warnings in **313.76 seconds**.
The pytest process exits with code zero.
The final focused storage selection passes 126 tests in 11.40 seconds.
The already-downloaded Codex 0.162.0 runtime passes all five opt-in rollout integration tests in 9.14 seconds.
Those integration tests use throwaway homes and never access the real Codex home.
No runtime download occurs.

Ruff passes for every changed Python file across the branch, with exact baseline exemptions.
BASE `59953ad7` already contains `db/backends/sqlite3/schema.py` UP031.
BASE also contains four F811 and one PIE807 findings in `tests/test_title_auto_task.py`.
The final command retains only these file-specific exemptions; no new lint finding is accepted.
`git diff --check` passes.
No frontend source changes, so no frontend test run is required.
Server wire-shape and unchanged-IPC tests run in the complete backend suite.

The unchanged warnings concern AsyncMock calls in `tests/test_codex_sdk_wrappers.py:133,152`
and `BaseAgent._notify_state_change` in `tests/shell_notice_helpers.py:31`.
They remain outside this storage task.
Task-owned temporary probe scripts are removed; evidence logs and the authorized database copy remain.
The production instance still needs its normal migration and restart through devctl after integration.
No production migration or restart runs during this validation.

## 2026-10-12: Independent content and scratch cold policies

Content eligibility now uses the current native root's flags and latest lifecycle activity.
Archived or hidden roots qualify at exactly 15 days.
Every other root qualifies at exactly 30 days, including visible, nonarchived roots.
Scratch eligibility retains archived OR hidden and exactly 30 days.
The latest activity is the maximum non-NULL created/start/update/stop timestamp.
Archive and hide timestamps do not participate.

`Session.is_cold_for_content` and `Session.is_cold_for_scratch` are pure properties.
Both inspect loaded fields and cached native parents without SQL.
Unknown fields, missing parents, inconsistent ancestry, and cycles remain conservative.
Independent orchestration sessions do not inherit eligibility.
The scratch batch resolver explicitly selects its independent predicate.
Native active-tree and exact ephemeral-work protections remain unchanged.
Filesystem ownership, orphan, empty-directory, and destructive-removal rules remain unchanged.

No schema, migration, storage version, API, provenance, wire, or frontend change occurs.

TDD first observes ten failures against the prior policy and forty passing cleanup tests.
The focused content/cleanup suite passes 337 tests after implementation.
A separate policy run passes ten tests, including deferred-field zero-SQL checks.
Self-review precedes the full suite: **10,084 passed, 26 skipped, 3 warnings in 341.07 seconds**.
Scoped Ruff passes all eight changed Python files. `git diff --check` passes.
The six prior broader Ruff findings remain outside this change.

The existing warning names remain unchanged:

- `test_codex_sdk_wrappers.py::test_turn_forwards_auto_review`: unawaited AsyncMock coroutine.
- `test_codex_sdk_wrappers.py::test_turn_forwards_fast_service_tier`: unawaited AsyncMock coroutine.
- `test_shell_notice_codex.py::test_turn_openings_restart_the_delay`: unawaited agent notification coroutine.

## Real session-ID persistent promotion benchmark

Five real native trees use the copied database. Synthetic Task 15 roots are excluded.
Workload sizes include every native descendant. Distinct content counts include both FK slots.
All legacy original slots are NULL: **zero original FK slots** in every measured tree.
No historical originals are fabricated. The production cursor still checks both slots.
Every distinct referenced content compresses with the genuine provider dictionary.
All measured cases have **zero no-gain rows, zero protected rows, and zero multi-session content owners**.
Duplicate references within one session account for the large Claude item/content count difference.

| Provider | Root items | Native sessions | All items | Compressed distinct contents | Raw bytes | Stored bytes | Largest payload bytes |
|---|---:|---:|---:|---:|---:|---:|---:|
| claude_code | 100 | 1 | 100 | 100 | 321,149 | 112,814 | 43,005 |
| codex | 1,039 | 6 | 1,889 | 1,889 | 6,226,852 | 2,902,686 | 97,070 |
| claude_code | 5,297 | 30 | 7,997 | 7,997 | 22,698,816 | 8,170,246 | 238,571 |
| claude_code | 22,485 | 8 | 23,145 | 19,376 | 108,054,632 | 41,500,810 | 683,972 |
| codex | 23,521 | 1 | 23,521 | 23,521 | 50,921,722 | 25,735,901 | 347,853 |

The stopwatch starts before production admission of a lifecycle update supplied only the root session ID.
The lifecycle service updates `last_started_at`, invalidates readiness, commits, and schedules through `on_commit`.
The production coordinator resolves native ancestry and references, prepares slices, and publishes persistent plaintext.
The stopwatch stops after a database read observes the committed root plaintext-readiness epoch.
Timing includes lookup, metadata, both-slot cursor traversal, scheduling, admission, writer/executor work, publication, and commits.
The final observation adds up to the 2-millisecond polling interval plus its database read.
Post-timing checks verify every referenced content is plain and every native session effective-content hash is unchanged.
This measures **persistent promotion**, not requested-content cold reading.

All timing uses `DEBUG=False`, without allocation tracing.
Full pytest, the single largest-payload census, and workload extraction finish before timing.
Only the selected root queue runs; unrelated pending-root recovery is excluded.
Filesystem cleanup is disabled. No periodic global compression sweep runs.

| Provider / root items | Repeat | Total seconds | Preparation/proof seconds | Apply/validation/commit seconds | Batches | Endpoint WAL delta bytes |
|---|---:|---:|---:|---:|---:|---:|
| claude_code / 100 | 1 | 1.278 | 0.284 | 0.399 | 1 | 0 |
| claude_code / 100 | 2 | 0.738 | 0.294 | 0.394 | 1 | 0 |
| codex / 1,039 | 1 | 14.460 | 5.398 | 8.816 | 19 | 0 |
| codex / 1,039 | 2 | 14.637 | 5.465 | 8.911 | 19 | 0 |
| claude_code / 5,297 | 1 | 64.793 | 26.758 | 37.077 | 80 | 0 |
| claude_code / 22,485 | 1 | 151.309 | 58.801 | 89.763 | 232 | 0 |
| codex / 23,521 | 1 | 172.806 | 63.262 | 106.805 | 236 | 0 |

Preparation includes decode, row lookup, and ownership/root proof generation.
Apply includes proof validation, writes, and transaction commits inside the production apply function.
Worker connection cleanup, scheduling, admission waits, snapshot creation, and readiness observation remain in total wall time.
These preparation values are not isolated decode measurements.
Validation and publication dominate the observed total.
Endpoint WAL deltas do not measure peak growth or total writes.
Production connection cleanup and automatic checkpoints remain enabled and reduce these endpoint deltas to zero.

### Excluded extreme and copied fixture restoration

The single legacy census orders records by SQLite text character length.
Scoped extracted metadata supplies their exact UTF-8 sizes.
The largest observed payload contains **15,614,580 UTF-8 bytes**.
Its root has 145,950 items; its complete native tree has **414 sessions and 458,192 items**.
Partial extraction of that copied tree remains. Full setup and timing are excluded to retain a small representative benchmark.
Its temporary lifecycle age shift restores through the exact inverse 90-day datetime operation.
Nullable dates and microseconds remain unchanged; extraction performs no lifecycle mutation.
The naturally hot status is rechecked after restoration.
Six original root compute markers are saved, restored, and compared with their saved values.
All measured root lifecycle fields restore from their original loaded dictionaries.
An initially selected naturally hot 21,222-item root is extracted but excluded from timing.

A repeat-count reduction interrupts an extra middle-sized repeat after its first complete measurement.
An untimed production cleanup verifies full plaintext readiness and unchanged hashes before remaining large measurements.
Both large cases then use one measured pass. The first middle-sized measurement remains unchanged.

Final checks confirm all five measured trees have only plain referenced contents and matching readiness epochs.
Main data, provider files, and shared scratch/artifacts remain untouched.
No server starts, provider compute, provider migration, content GC, vacuum, or column removal occurs.
The temporary harness and machine-readable statistics remain in the ignored implementation workspace for review.
