# Session Forking — Product and Architecture Specification

**Date:** 2026-09-08.
**Status:** adversarially reviewed; ready for user review. No implementation is authorized by this document.
**Evidence:** [provider research](2026-07-16-session-forking-research.md), refreshed on 2026-09-08.
**Supersedes:** the 2026-07-16 design and implementation plan. Both originals are archived outside the repository.

## 1. Objective and scope

Users create an alternative continuation without losing the original conversation.
Each branch is a separate TwiCC session on the same provider.
Users navigate from a source boundary to its branches and back to the source boundary.
The product behavior is common to Claude Code and Codex.

Version one includes:

- Fork the latest completed conversation state.
- Fork after an earlier completed agent turn.
- Redo an earlier user request in a new branch, with an editable prefilled composer.
- A persistent draft, with no provider fork until the first send.
- An expandable inherited-history block, initially collapsed.
- Source badges, branch lists, and navigation to the exact departure point.
- Forks of forks, native fork discovery, accounting, search, and share support.
- Reading relevant provider history changes made outside TwiCC, including Codex replacement rollouts.

Already deferred in the previous design, and still deferred:

- A graphical overview of the complete branch tree.
- Additional fork markers in the sidebar.
- A same-session rewind action.
- A user-facing CLI/skill/MCP fork-creation interface.

Do not add cross-provider forks, automatic worktrees, file restoration, or a general transcript editor.
Forking does not restore workspace files. Existing edits remain on disk.

## 2. Product contract

### 2.1 Boundary semantics

A turn starts with a user request and includes the agent's work for that request.
A completed agent turn includes tool calls, results, and trailing protocol entries needed for coherent continuation.
An intermediate assistant update is not a completed-turn boundary.
The provider adapter determines turn completeness; counting displayed user messages is not sufficient.

| Entry point | Retained conversation | Composer |
|---|---|---|
| Fork at end, while waiting for user input | All conversation through the latest durable completed boundary | Empty |
| Fork after a completed response | All conversation through that completed turn | Empty |
| Fork before an earlier user request | All conversation strictly before that request | That request's editable text and reconstructible attachments |

The original session remains unchanged by TwiCC's fork action.
The new branch only receives the replacement or follow-up after the user sends it.
Opening or abandoning a draft never calls a provider fork operation.

Hide fork actions on synthetic streaming placeholders and incomplete turns.
User-request actions require an adapter-certified boundary before the selected request.
Do not implement boundaries by walking to the previous visible assistant message.
An absorbed or queued request inside another turn can lack an independent safe boundary; explain the unavailable action.

For the first user request, version one has no fork-before action.
There is no common certified pre-message boundary across the selected provider paths.
The user can start a normal new session. Fork-after the first completed turn remains available.
This preserves the original v1 limitation; it does not remove a previously promised first-message fork.

### 2.2 The click fixes the boundary

Resolve and persist the exact selected boundary when creating the draft.
A later source append never extends the retained history, including for a fork-at-end draft.
The boundary token identifies native history and includes its revision evidence; it is not a timestamp or a line count.
Capture a fingerprint of the retained semantic context, including applicable compaction and content-replacement state.
A stable message UUID alone does not prove that its effective retained content is unchanged.
Validate this fingerprint before native creation and validate the child's resulting retained semantics before dispatch.
If later replacements change the frozen prefix and the native operation cannot reproduce it, report a boundary conflict.
Do not silently accept a different prefix merely because its terminal UUID still exists.

At send time, validate that the provider can still fork that exact selected history.
If an external revert makes the selected boundary unavailable through the supported operation, reject the send without substituting another boundary.
The draft and unsent content remain available.
Do not silently fork the source's new end or the nearest surviving turn.

A provider can reference an old physical rollout for existing children while its public fork API targets the current logical thread.
Display retention therefore does not guarantee that a new fork from an obsolete boundary remains possible.
Version one rejects that case unless the adapter certifies the exact operation.

### 2.3 Draft and navigation

A fork draft carries an immutable source/boundary descriptor and a mutable composer/settings bundle.
Persist both through the existing IndexedDB draft mechanism.
Do not persist native absolute provider paths in browser drafts; the server resolves opaque identifiers.

The draft shows a source banner and the selected inherited context on demand.
Drafts do not appear in the source's persisted branch list.
After native creation succeeds, the child is listed even if its first send fails.

An inherited-history block is collapsed by default.
Expanding it loads bounded pages; it does not download every ancestor's full JSONL.
Users can fork from eligible inherited entries as well as local entries.
The logical source of such a fork is the branch being viewed, even when the retained bytes belong to an ancestor.

Branch chips appear at the departure boundary and list child title, creation time, and branch-owned cost.
Before/after departures at the same displayed turn remain distinguishable.
The child header navigates back to the logical source using a native anchor, not a stored raw line number.
If the source now shows another history revision, open the retained departure history read-only with a clear historical label.
Never navigate to an unrelated current item occupying the same line number.
If the source is unavailable, preserve a non-clickable provenance label and the child's available context.

### 2.4 Prefill and attachments

Redo copies the original user-authored text into the composer; it does not submit it.
Do not include injected context blocks, tool results, or hidden provider instructions as editable user text.
Carry supported images/documents using the existing draft attachment representation.
Do not reconstruct an attachment solely from a now-invalid local path.
Show missing attachments explicitly and require removal or replacement before send; never silently drop them.
Follow-up and fork-after drafts start with an empty composer.

### 2.5 Settings and workspace

Copy the source session's agent-settings bundle at draft creation; allow normal draft edits afterward.
Keep the same provider and project. Cross-provider/project retargeting is outside this feature.
Resolve the launch cwd within that project using current session creation rules; it is not a historical filesystem snapshot.
Re-apply current project trust and permission constraints at admission and agent build.
Unsupported copied settings produce the normal explicit validation errors.

Create a visible session with fresh pin, annotation, notification, and layout defaults.
Do not inherit `spawned_by`, `spawn_root`, a process, cron, running goal, or a terminal.
Subagent sessions and ephemeral/nonpersistent source sessions cannot be forked in v1.
A persisted hybrid source may produce a normal SDK child; no hybrid process is copied.

Recompose session-specific instructions after binding the canonical child ID.
Current artifact paths, scratch paths, identity, and mutable context must refer to the child before its first model turn.
Copied old instructions may remain as historical transcript content; they cannot remain the active identity instructions.
Inherited tasks/plans can appear in history, but do not schedule work or become current child task state automatically.
Only child-owned task/plan updates populate the child's active Tasks/Plan metadata.

## 3. Architectural decisions

### 3.1 One session, one active conversation, several physical sources

Keep `Session` as the logical process and product identity.
Do not equate it with a single JSONL filename.
Codex revert can create several rollouts owned by one thread; existing forks can still reference older ones.

Separate four concepts:

1. **Physical stream:** a provider-owned transcript/rollout and its ingestion revision.
2. **Stored record:** one physical record, its raw payload, and computed metadata.
3. **Provenance:** the logical fork source and exact retained boundary.
4. **Conversation projection:** the ordered, selected records visible in a particular branch revision.

A projection contains references and derived metadata, not duplicate raw message bodies.
Compose inherited segments and the local suffix on the backend.
The browser does not recursively query source sessions or interpret provider JSONL lineage.

### 3.2 No synthetic copies of inherited content

Do not create child `SessionItem` copies of ancestor records to make a fork appear linear.
Caches may materialize record references, page indexes, and grouping metadata.
They are rebuildable and must not become a second mutable transcript.

Provider-created copies are different. Claude offline forks physically copy records with new UUIDs and provenance.
Ingest those native records once as records of the child's physical file, as required for faithful raw history.
Their copied content is authoritative for that child's snapshot, including content replacements and compaction.
Do not substitute the current parent body merely because `forkedFrom` points to it.
Link their provenance for accounting and navigation, without displaying both the child copy and the source version.

This distinction avoids inventing new copies in TwiCC while preserving actual provider evidence.
Byte-level deduplication of provider-created copies is not required for v1.

### 3.3 Logical ancestry is not physical dependency

Persist logical provenance even when the source session is absent from TwiCC.
A nullable resolved Session FK is a convenience; it is not the sole stored source identity.

A physical history reference identifies a rollout revision and an exclusive bound.
It can target an ancestor different from the logical parent.
Subagent parentage and orchestration parentage remain separate relationships.
A fork must not grant descendant privileges under the existing agent/share control-plane rules.

### 3.4 Isolate provider changes

Provider adapters own schema parsing, native identities, boundary validation, history selection, and fork invocation.
The product layer never depends on `history_base`, `parentUuid`, or a provider's spelling of a turn event.
Adapters return a versioned common descriptor.

Prefer Codex's read-only app-server operations to establish current rollout identity and validate native turn boundaries.
Use the existing managed runtime and isolated transport; do not issue a model turn to read history.
Do not use `thread/resume` as a routine browse operation, because it loads execution state.
Cached DB projections serve browsing without a running agent and without a provider call per page.
An initial unknown native history may require background reconciliation before it becomes available.

Raw provider evidence remains available for a later corrected parser.
Unknown additive fields are retained and ignored when harmless.
Unknown structural semantics produce an explicit unsupported-history state, not a guessed linear transcript.

## 4. Storage and projection contracts

The following are required responsibilities and uniqueness contracts, not final migration names.
The implementation plan must map them to concrete migrations without changing these invariants.

### 4.1 Stream registry and revisions

A stream record stores provider/home namespace, native owner ID, native stream ID, current path, and revision identity.
The namespace prevents collisions between configured provider homes.
Path changes from archive/unarchive do not create another logical session or another identity for unchanged content.
A rewrite creates a new ingestion revision. An append extends the existing revision.
Published bounded prefixes stay immutable for consumers that reference them.

Track read offset, physical line count, mtime, and readiness **per stream revision**.
`Session.last_offset` and `Session.file_path` cannot remain the only ingestion authority for Codex.
The session's active stream pointer is updated from provider evidence, never filename sorting or watcher arrival order.

Revisions referenced by a child or snapshot share cannot be deleted during recompute.
Unique billing evidence also holds a retention reference, even without any child or share.
Preserve enough execution evidence to reconstruct historical totals after a revert or rewrite.
Recompute publishes replacement derived metadata atomically and keeps native identity mappings.
Retained raw records are not duplicated merely for a recompute.
A physical rewrite needs retention of the prior captured revision if a live projection still references it.
That is revision retention, not per-child copying.

### 4.2 Record identity and links

Stored-record uniqueness is `(stream_revision, physical_line_number)`.
A native message UUID, turn ID, or ordinal is an adapter index, scoped by stream/history as required.
Do not assume native IDs or call IDs are globally unique.

Extend or replace the current `(session, line_num)` storage key accordingly.
Resolve tool-use/result links and agent links to physical record identities.
Line-number-only joins cannot span revisions safely.
Maintain compatibility for ordinary single-stream sessions during migration.

A projected occurrence has:

- An opaque stable `item_key` within its selected history revision.
- The physical record locator, retained only on trusted backend/internal surfaces as appropriate.
- A logical source anchor for navigation.
- Its position within a particular projection revision.
- `inherited` and billable-owner attribution.
- Projection-scoped display/group metadata.

Stable identity and ordinal position are different.
Two source line sevens must not collide in a fork's cache, tool lookup, or virtual-scroller key.

### 4.3 Projection revision

The user-visible projection is the selected branch's historical transcript, not the provider's current model-input list.
Keep pre-compaction exchanges readable and retain the branch's compaction marker/summary in their proper historical position.
Do not display alternative sibling branches or duplicate pre-compaction exchanges through multiple backpointers.
The provider's effective model context is a separate adapter-owned description; it may substitute a summary for earlier exchanges.
Boundary certification checks both descriptions against the selected retained history, not equality between their message arrays.
Earlier exchanges remain navigable after compaction. A new fork there still requires a certified native boundary.

A branch projection identifies an immutable inherited prefix and its currently appendable local suffix.
A topology/path-selection change publishes a new projection revision.
Appending local records advances a sequence within that revision, without changing existing item keys or positions.

A projection descriptor contains ordered bounded stream segments or selected record-reference runs.
Claude branch/compaction selection can be non-contiguous in file order.
Do not force every provider into a simple interval model.
Codex segments retain ordinal and byte cutoff evidence internally.

Expose these separate quantities:

- Physical ingestion line counts, for raw diagnostics only.
- Projected item positions, for UI pagination within a revision.
- User-visible counts, after filtering and inherited-ownership rules.

Never overwrite physical `line_num` with a synthetic logical position in a stored raw record.

### 4.4 Compute and grouping

Parse each physical stream revision into reusable record metadata.
Build conversation selection, turn boundaries, and display groups over the branch projection.
Do not reuse raw-file `group_head`/`group_tail` values across concatenated segments.
The inherited block is a projection-level container, separate from ordinary tool/internal groups inside it.

Compute seeded state through inherited history where required for correct local interpretation.
Examples include model context, tool pairing, and cumulative token clocks.
Ownership gates aggregate writes; it does not erase the context needed to interpret the suffix.

Full rebuild and incremental append must produce equivalent projections and accounting.
A metadata-only parser correction invalidates affected derived caches and descendants without creating new spend.
A source append beyond a child's fixed boundary does not invalidate that child's prefix.
Use dependency indexes and bounded queries, not an all-session scan for every append.

### 4.5 Projection read contract

Introduce a common backend conversation reader for owner UI and shares, with an explicit access scope.
Required operations:

- Read the projection descriptor and bounded item-metadata pages.
- Read bounded content pages by projection cursor or item keys.
- Resolve one source/native anchor into a selected projection.
- Resolve tool results, images, diffs, and related content within that projection's allowed set.
- Produce a bounded inherited-prefix preview for a draft.

Use opaque cursors bound to session/projection revision and page scope.
Return a clear stale-revision response when a request references an obsolete mutable projection.
Retained historical/snapshot projections remain independently addressable through their authorized owner/share route.
Limit page sizes and depth; detect cycles and report malformed lineage.
Pagination must not flatten or fetch all raw content before returning one page.

Physical raw-debug APIs remain distinct from projected conversation APIs.
No caller may mistake a physical line-range response for the branch's selected history.

## 5. Provider operations

### 5.1 Codex

For a boundary before a user turn, send experimental `beforeTurnId` when supported.
For a completed-turn boundary, send stable `lastTurnId`.
For fork-at-end drafts, resolve the end to an explicit terminal turn ID at click time.
Do not send an unbounded latest-fork request after the draft has been created.

Send `excludeTurns: true` and use pagination for history hydration.
Verify actual serialized fields; the vendored high-level SDK does not expose the full protocol.
Use a narrow TwiCC wrapper around the low-level request where necessary.
Do not add rollback to the creation path.

Persist and resolve `history_base` through the adapter.
Track `forked_from_id` separately from its physical target and retain the logical exclusive cutoff.
Recognize archived, replacement, and nested referenced rollouts.
`session_meta.id` identifies ownership, not which file is the current active rollout.

Legacy copied forks remain readable through the provider's supported conversion/replay path.
Do not interpret a paginated child as self-contained because it passes the paginated-format gate.
Existing rollback markers are compatibility input, not a fork implementation mechanism.

Do not inherit automatic goal execution. Use supported goal deferral when needed, then clear the copied goal before the first turn.
Never clear or alter the source's goal.
If the pinned runtime cannot prevent the initial continuation, refuse creation from an active-goal source with a specific reason.
No background work may start in a new branch before the user's explicit send is admitted.

### 5.2 Claude Code

Use public offline `fork_session()` followed by a normal SDK resume of the returned child ID.
Use the typed `resume_session_at` option only in separately justified adapter operations; do not rely on hidden-flag string scans.
Do not assume the offline function accepts a caller-chosen child ID.
Use the existing canonical-session rebinding concept for both providers.

Resolve a retained turn to its last required transcript entry, not its last visible assistant text.
For a redo request, exclude the original user prompt and its work.
The resulting native fork must resume the effective model context certified for the retained conversation boundary.

The offline SDK copies a file-order prefix, possibly including sibling branches.
The adapter selects the retained ancestor path and compaction semantics for display.
The release tests must verify the native effective model context is consistent with the selected boundary.
They must not require the visible historical transcript to equal the compacted model-input list.
The SDK reader's decision to omit pre-compaction parents does not authorize dropping those exchanges from the TwiCC history.
`forkedFrom` maps copied entries to their immediate source entries; new local entries have their own ownership.
Imported pre-provenance forks are not linked through guessed timestamp or UUID-duplication heuristics.

The offline fork does not copy file checkpoints.
A later file-rewind feature is unrelated to this creation path.

### 5.3 Source activity and capability gates

Do not impose a new unconditional Claude mid-turn prohibition.
A draft may target an earlier completed boundary while the source continues working.
Creation is permitted only when the adapter certifies a coherent bounded snapshot and validates the resulting child before sending.

For Codex, retain the previous conservative send gate while the source is running until the pinned-runtime active-source tests pass.
Once those tests certify the selected completed-boundary operation, enable it without changing product semantics.
The current unfinished turn never becomes a selectable fork-after boundary in v1.

For Claude, coherent concurrent snapshot behavior is a required release test for maintaining the earlier completed-boundary affordance during source activity.
A genuine unsupported concurrent case receives a capability-specific explanation; do not silently move the boundary.
An external writer is not controlled by TwiCC's process lock. Validate source revision/boundary evidence around native creation.

## 6. Creation transaction and recovery

### 6.1 Persisted creation intent

Extend the normal creation service with a versioned fork descriptor and an idempotent operation ID.
Only the owner UI exposes creation in v1; do not accidentally enable an undocumented CLI/MCP mutation through shared payload handling.
Validate the provider, source type, availability, selected boundary, project, settings, and attachment readiness server-side.
The client cannot supply arbitrary raw paths, lineage edges, or billable ownership.

The first admission freezes the normalized creation payload for its operation ID: source/boundary, project/provider, and initial settings.
Freeze the first-message text, attachment identities/content digests, and send settings under a distinct durable send-attempt ID.
An identical retry returns existing state. Reusing either ID with a different admitted payload returns a conflict without mutation.
Edits made before admission remain normal draft edits; they cannot change an already admitted operation from another tab.

Use the durable operation record as the recovery authority.
In-memory pending attributes are only a delivery optimization.
A backend crash must not erase knowledge that native creation may have happened.

Required states:

`prepared → creating → bound → sending → active`

Operation failures preserve `failed_before_creation`, `created_send_failed`, or `creation_outcome_unknown`.
First-send state is tracked separately: `ready`, `dispatching`, `delivered`, `failed_before_delivery`, or `first_send_outcome_unknown`.
Persist `dispatching` with its send-attempt ID before crossing the provider boundary.
Store the native child ID and confirmed boundary before dispatching the first turn.
Apply provenance and child settings before the session is broadcast as a ready fork.
Watcher-first discovery and manager-first completion converge on one canonical child row.

### 6.2 Retry semantics

Reuse the same operation ID for retries of the same draft creation.
Serialize concurrent attempts; double-clicks and reconnect retries cannot create two known children.
After `bound`, retry on that child, never create another fork.
The browser may reuse its outgoing-send snapshots and ACK presentation, but the current implementation is not durable backend deduplication.
Add durable admission and delivery state for the first send; `request_id` currently only correlates acknowledgments.
A crash after provider dispatch but before confirmed delivery produces `first_send_outcome_unknown`.
Do not automatically resend while delivery remains unknown. Reconcile with sufficient native turn/message evidence, or preserve explicit uncertainty.
A retry is safe only after non-delivery is established; confirmed delivery returns existing state.
Do not claim exactly-once dispatch across an unobservable provider outcome.

Only a child-owned local occurrence linked to that send admission can confirm delivery.
Inherited occurrences, pre-dispatch records, and text/attachment equality alone are never sufficient evidence.
Loading an identical prompt from the inherited prefix must not dismiss an optimistic bubble or delete its recovery snapshot.

After proven non-delivery, an edited message receives a new send-attempt ID on the already bound child.
It does not reuse an admitted send payload or create a second fork.
Before a child exists, a rejected creation with changed creation settings becomes a new operation only after the old outcome is settled.

A native API is not assumed idempotent.
A crash after native creation but before persisting the returned ID can leave an unknown outcome.
Record the pre-call inventory and available native provenance as reconciliation evidence.
Only bind automatically when the evidence uniquely proves association with the operation.
Matching timestamp/title/boundary alone is insufficient when concurrent external creation is possible.

Otherwise surface an interrupted-creation state and do not auto-repeat the provider call.
Expose discovered candidates for explicit recovery or allow the user to abandon the operation and start a separate draft.
Do not claim exactly-once creation across an unobservable provider crash window.
Unbound native children remain discoverable through normal ingestion; do not silently delete them.

### 6.3 First turn and identity

Once bound, rebuild session-specific instructions and current context for the canonical child identity.
Do not launch with the draft UUID embedded in artifact paths or with active source identity instructions.
Confirm that the native child's retained boundary matches the admitted intent.
Do not send if creation produced a different boundary or unresolved history.

If the first turn fails, preserve the created branch, edited message, and attachments for retry.
Navigation and branch chips continue to work for that child.
The normal first-send optimistic bubble remains scoped to the draft/child operation and cannot appear in the source.

## 7. Frontend and synchronization

Move conversation caches to `(session_id, projection_revision, item_key)`.
Use position indexes for efficient ordered access; do not retain the assumption that `items[line_num - 1]` identifies a projected item.
Synthetic streaming and optimistic items have explicit local identities and remain outside persisted boundary selection.
Projected inherited content cannot reach the existing text-only `resolveInflightSends()` path as delivery evidence.
The fork send reconciliation consumes the qualified local delivery evidence defined in section 6.

Use a common conversation item/metadata DTO for single-stream sessions and composed forks.
Adapt the existing virtual scroller and grouping through one projection integration layer.
Do not implement a separate fork-only renderer with different tool, image, or search behavior.
Continue using `getParsedContent`, `setParsedContent`, and `hasContent`; do not parse `item.content` directly.
Preserve lazy imports where new store/composable relationships could introduce HMR cycles.

WebSocket messages identify projection revision and append sequence.
Send bounded append/update events for current local activity.
Send a projection-reset event for native revert, source-selection correction, or incompatible projection rebuild.
On reset, discard old position/group caches, retain an item-key scroll anchor when resolvable, and refetch metadata.
Reject mixed-revision pages and events; resynchronize after sequence gaps.

Native-ID anchors drive branch badges and deep links.
Reverse branch lists are paginated on demand, with counts in session metadata.
Do not broadcast an unbounded reverse fork tree in every `session_updated` payload.
An absent/hidden/deleted logical parent does not erase the stored native provenance.
Owner UI filters and existing access rules still apply when resolving navigation.

## 8. Accounting, metadata, search, and shares

### 8.1 Ownership and aggregates

Fork lineage is not cost aggregation lineage.
A child's session/project/activity totals include only execution billed to that child and its actual spawned agents under existing rules.
Inherited records can display historical source cost as context, but contribute zero additional cost to the child.
Do not sum a source's entire session or subagent totals into every fork.

Use native provenance and explicit retained boundaries to identify inherited records.
Never use record timestamp relative to child creation as the ownership test.
Claude changes a copied leaf timestamp; Codex can inherit without copying any cost event.

Seed cumulative token accounting from inherited context and bootstrap snapshots without charging them again.
Actual new input tokens that resend inherited context to the model remain billable child work.
A restored token snapshot and a new inference charge are not the same event.
Unknown attribution remains explicitly unknown; do not silently convert it to a verified zero.

`user_message_count` counts new child-owned requests, excluding copied/inherited requests.
Current context usage describes the child's current model context, including inherited context where applicable.
Do not derive context-window use from the displayed number of messages.
A revert does not refund past execution; retain historical spend even when its messages leave the active projection.

Maintain execution-charge identity independently from physical record identity and active projection membership.
One execution observed in several stream revisions contributes once, while a later new execution with identical text contributes again.
Provider adapters map charge observations across known rewrites/migrations using execution identity and verified correspondence.
Never deduplicate charges by prompt text, timestamp alone, or identical physical line positions.
Preserve evidence for charges no longer present in the current native history, even without forks or shares.
All session, project, daily/weekly activity, and usage sums consume this canonical charge ownership, not every physical record's cost.
The implementation may use a separate charge table or equivalent canonical attribution, but duplicated observations cannot remain billable.
If a rewrite cannot be correlated safely, retain the last verified aggregate and mark accounting pending/unknown.
Do not add the whole new revision as new spend or erase old spend while reconciliation is unresolved.

### 8.2 Search and other metadata

Index source-owned content once for fork inheritance; do not create extra search documents per projected occurrence.
Native Claude copied entries with proven provenance and equivalent effective content do not generate duplicate inherited hits.
Provenance alone does not prove content equivalence after replacements or compaction.
Keep one searchable representative per distinct effective content variant and navigate to an occurrence containing that variant.
If the source is absent, retain one searchable representative rather than making the content disappear.
Search result navigation uses native/record anchors; prefer the source when available and a readable occurrence otherwise.
In-session search scans only that branch's projection and may return inherited occurrences.

Titles, current settings, task state, plan paths, tool activity, and last activity use branch-owned lifecycle events.
Inherited tool cards are historical. They cannot display the source's currently running terminal or live agent state as child activity.
Raw-debug diagnostics may expose excluded physical records to the owner, but never count them as selected conversation.

### 8.3 Shares

The share token authorizes the selected branch projection, not its source sessions or their sibling branches.
All share endpoints use the same projection resolver with the share's scope and display ceiling.
This includes item bodies, metadata, tool states/results, original-file diffs, images, search, and WebSocket updates.
Never route a shared inherited item to an unrestricted owner source endpoint.
Do not expose native filesystem locators in public DTOs.

Snapshot shares pin a retained projection revision and a terminal item boundary.
Later parent/child appends, revert, and compute revisions cannot expand the share's content scope.
Live shares follow the child's current projection; a reset replaces the displayed projection rather than mixing old/new content.
Inherited context is included when it belongs to the fork's selected prefix.
Folded is a display preference, not an access-control boundary.

Hide source/sibling navigation and branch enumeration in the public viewer for v1.
Inherited subagent calls show only results already recorded in the permitted prefix.
They do not grant access to the source agent's full or later transcript.
Existing `include_subagents` behavior continues for child-owned spawns, with the share's normal snapshot/display restrictions.
Content-specific linked result exceptions remain bounded to an allowed tool occurrence and the pinned history limit.

## 9. Retention and external changes

Archiving a source preserves raw dependency records and provider files required by surviving children.
An archived logical parent remains valid provenance; its process may stop under existing archive rules.
Do not cascade deletion through the logical fork FK.

Version one does not introduce a new destructive-delete UI or a general provider storage garbage collector.
Existing TwiCC cleanup/rebuild paths must not purge referenced stream revisions, session ownership stubs, share dependencies, or unique accounting evidence.
If a destructive existing operation would remove required data, refuse it with the dependent-branch reason rather than secretly copying files.
A later explicit dependency-aware purge can define coordinated deletion; it is outside this feature.

TwiCC cannot prevent an external program from deleting a provider ancestor.
Retain already indexed readable history, mark the missing dependency, and distinguish browsing from resumability.
A cached transcript does not mean Codex can continue without its native dependency files.
Block native continuation/fork when required provider history is missing; do not silently rebuild or rewrite provider files.

Discover native forks independently of creation through TwiCC.
Store unresolved native parent IDs, resolve links when the parent appears, and handle children discovered before parents.
A missing or ambiguous legacy fork boundary is shown as unknown; no guessed branch chip is placed on a message.
Unknown legacy forks may remain unlinked while their ordinary conversation stays readable.

Codex `thread/revert` and native history migration require atomic active-projection changes.
Retain old physical history needed by existing forks and snapshots.
Two rollouts with the same owner ID must not race to overwrite one Session's ingestion offsets.
Compression, archive moves, and provider-home namespace changes go through stream resolution, not filename assumptions.

## 10. Compatibility and rollout requirements

Adapt the current single-file schema without discarding user data.
Backfill one physical stream for each existing ordinary session and preserve current native content, links, and costs.
Do not assign global uniqueness to historical UUIDs or Codex ordinals during backfill.

The new conversation reader must support ordinary sessions before enabling fork creation.
Existing line-based owner links are resolved through their original stream/revision when known.
If that revision cannot be established after a native rewrite, show an explicit unresolved-link state rather than the wrong message.
Existing snapshot shares keep their existing content bounds; do not reinterpret a stored raw `frozen_at_line` as a new projected position.
Freeze their original record membership during migration or keep the legacy resolver until that mapping is verified.

Capabilities are checked against the configured pinned runtime and adapter version.
The research baseline is Codex 0.153.4 and Claude SDK 0.2.152 / CLI 2.1.259; it is not a promise about every installed version.
A missing boundary capability disables the affected operation with a clear explanation.
Never degrade an arbitrary-point fork into an end-only fork without the user's new selection.

## 11. Acceptance and release gates

These tests are required before implementation is declared ready. Research alone does not satisfy them.

### 11.1 Product acceptance

1. Fork at end, fork-after, and redo each preserve exactly the selected context on both providers.
2. A redo draft contains editable original text and attachment readiness; sending never duplicates the original request.
3. Abandoned drafts create no provider child. Reload restores a draft and its fixed boundary.
4. Source append after click does not enter the child. Invalidated boundaries never silently shift.
5. Source and child remain independently resumable; navigation returns to the correct native boundary.
6. Forking an inherited entry preserves the viewed branch as logical parent and creates no duplicated displayed prefix.
7. First-message redo and uncertified boundaries have the specified unavailable affordance.
8. Filesystem state remains untouched by the fork mechanism; the UI communicates that behavior near the draft banner.

### 11.2 Provider contract probes

Use temporary provider homes and synthetic fixtures; never developer histories for destructive/rewrite tests.
Run the real pinned binaries for boundary/storage contracts and controlled model or local mock turns for continuation semantics.

Codex: full/through/before forks, native serialized parameters, nested physical references, old rollout retention, current-path selection,
revert into inherited history, cold restart, source archive/unarchive, compressed ancestors, malformed/missing dependencies,
legacy migration, inherited goal deferral/clearing, source-active completed-boundary forks, cumulative token restoration and new charge.

Claude: offline full/bounded fork, remapped UUID/provenance, actual resume through the selected path,
existing sibling branches, progress links, compaction/content replacement, trailing structured-output/tool entries,
concurrent source appends and replacement detection, attachments, context identity, native copies and new cost ownership.

The source-active Claude and real offline-resume probes are release gates, not verified facts from the earlier research.
If a native behavior fails its gate, correct the adapter/spec and disclose any product change; do not silently remove an affordance.

### 11.3 Storage and projection tests

Assert incremental/full-rebuild equivalence for content, grouping, links, counts, and costs.
Exercise two physical line sevens, repeated call IDs, rewritten source revisions, and children discovered before parents.
Test a nested fork whose boundary belongs to an ancestor rather than its logical parent.
Assert no synthetic inherited body rows are inserted per fork.
Verify a parent append beyond the cutoff leaves the child prefix and keys unchanged.
Verify post-click content replacements affecting the prefix cause a conflict unless the native fork reproduces the frozen effective state.
After compaction, earlier selected-branch exchanges remain readable without exposing sibling branches or duplicating messages.
Revert a session without descendants/shares and rebuild totals: historical spend remains unchanged.
Rewrite a paid prefix and append a new execution: the prefix contributes once and only the new execution adds spend.
Verify search retains distinct native content variants sharing one provenance anchor.
Large/deep histories return bounded pages without loading all content or issuing one query per ancestor item.

### 11.4 Recovery and synchronization tests

Inject failures before native creation, after native creation before binding, after binding, and after first-send delivery.
Double submissions and known-outcome retries converge on one child and one admitted first message.
Unknown native creation and first-send outcomes independently stop automatic retries and preserve recovery evidence.
Crash after provider message acceptance but before ACK/persistence: no second message is automatically dispatched.
An inherited prompt identical to the attempted first message never confirms delivery after an actual send failure.
Two tabs submit one operation/send ID with different payloads: the second receives a conflict without changing the admitted request.
After proven non-delivery, an edited retry gets a new send-attempt ID on the same native child.
Test watcher-before-response, process restart, WS sequence gaps, stale pages, and reset during scrolling.
A source's active turn cannot leak optimistic or live state into a fork.

### 11.5 Share and retention tests

Attempt to access a sibling, a post-boundary parent result, and later inherited-agent activity through every related share endpoint.
Verify snapshot membership across append/revert/recompute and migration of existing frozen-line shares.
Verify owner raw-debug access does not widen share access.
Archive a source and continue its child using native storage.
Delete a required ancestor only in the isolated home: cached reading remains honest and native continuation reports missing history.
Run cleanup/rebuild paths with live child and share references; required records survive.

## 12. Decision register

| Decision | Selected behavior | Reason |
|---|---|---|
| Branch identity | New Session on both providers | Independent continuation and common UX |
| Inherited history | Backend composition of stored references | No synthetic body copies per fork |
| Native Claude copies | Retain and select the child snapshot | Faithful provider content, including replacements |
| Boundary timing | Fixed at click, revalidated at send | Source growth cannot change user intent |
| Boundary identity | Native descriptor plus revision evidence | Physical line positions are not stable semantics |
| Claude creation | Offline fork, then normal resume | Explicit provenance and no model call during copy |
| Codex creation | Direct fork through/before a turn | No rollback dependency |
| First user request | No fork-before action in v1 | Preserve prior scope; no certified common empty-prefix path |
| Reverse branches | Count plus paginated list | Bounded session updates |
| Accounting | Branch-owned execution only | No duplicate inherited spend; real new input remains charged |
| Shares | Projection-scoped resolver | Sharing one branch does not share its siblings |
| Retention | Protect dependencies; refuse destructive purge | Preserve native resumability without inventing file copies |
| Active source | Provider-specific certification | No new universal Claude prohibition |
| Failure recovery | Durable intent; no blind retry of unknown outcome | Native creation has no assumed idempotency |
| Graph/rewind/CLI | Remain deferred | Same scope as the original design |

## 13. Current-code integration map

These references identify integration points, not a step-by-step implementation plan.

- [Session and stream assumptions](../../src/twicc/core/models.py:375), [SessionItem identity](../../src/twicc/core/models.py:753).
- [ToolResultLink and AgentLink](../../src/twicc/core/models.py:823): move physical joins off ambiguous session line numbers.
- [Owner item and metadata endpoints](../../src/twicc/views.py:1411): projection reader and bounded pagination.
- [Frontend session item cache](../../frontend/src/stores/data.js:1213): separate item identity, position, and raw lines.
- [Share display filtering](../../src/twicc/share/display.py), [share related-content routes](../../src/twicc/share/session_views.py): common bounded scope.
- [Creation service](../../src/twicc/core/services/session_creation.py): durable fork operation and canonical binding.
- [Codex wrappers](../../src/twicc/providers/codex/sdk_wrappers.py), [Codex initial sync](../../src/twicc/providers/codex/initial_sync.py).
- [Codex watcher](../../src/twicc/providers/codex/sessions_watcher.py), [migration coordinator](../../src/twicc/providers/codex/background_compute.py).
- [Archive lifecycle](../../src/twicc/core/services/session_update.py:450): stop source activity without removing inherited data dependencies.

## 14. Review status

Two independent adversarial reviews ran after the first complete draft.
Both reviewers checked the corrected specification and reported no remaining blockers within their scopes.
Six review findings and one additional frozen-context self-review correction are incorporated.
See the [review and correction record](/artifacts/01a08017-3fa4-7b82-aeee-f417e8db093d/forking-archive/2026-09-08-session-forking-spec-review.md).
This is a document-level review, not proof that the release-gate provider behaviors or application implementation already pass.
A new implementation plan follows user review of this specification; the obsolete plan is not an execution input.
