# Nested Subagents Implementation Plan

**Date:** 2026-09-07
**Status:** implemented and independently reviewed; final delivery
**Spec:** [Nested Subagents Design](2026-08-08-nested-subagents-design.md)

## Goal and execution gates

Link, display, and control nested subagents at any depth on Claude Code and Codex.
Keep flat `Session.parent_session` roots and launcher-owned `AgentLink` rows.
Include live ingestion, historical recompute, navigation, running state, stop controls, and sharing.

- [x] Read repository and local instructions. Identify unrelated dirty files.
- [x] Run independent adversarial backend, frontend, and Codex plan reviews.
- [x] Resolve findings and obtain independent approval of both documents.
- [x] Commit only these two documents before implementation.
- [x] Use MCP `process` / `process_wait` to verify session
  `01a07946-32bb-71d0-829c-33e70af669fc` reaches `user_turn` before implementation.
- [x] Re-read Git status after the gate. Preserve unrelated changes in every edit and commit.

Use internal subagents for implementation and independent review where tasks have disjoint file ownership.
No unavailable workflow plugin is required. The user requests full autonomy and no questions.
Stay on the current branch. Do not create a branch or worktree.

## Contracts shared by all tasks

1. `parent_session_id` identifies the tree root. `AgentLink.session_id` identifies the launcher.
2. Valid trees have no depth limit. Walks use visited sets and reject cycles instead of returning intermediate roots.
3. Claude link evidence priority: structured `toolUseResult`, async ack, authoritative sidecar, unique prompt match.
4. Sidecar metadata includes depth-1 launches. An unresolved authoritative tool id defers matching; it never enables prompt fallback.
5. Prompt fallback respects known launcher metadata and cannot steal an agent linked to another owner.
6. Full recompute re-derives every owned link. Its own existing links do not exclude its prompt candidates.
7. Root completion backfills use a separate `agent_links_backfill` payload. Owned and backfill creations both deduplicate at apply time.
8. No new schema columns or constraints. Migration changes existing Codex parent references and stored cost aggregates only.
9. `stopped_at` is persisted completion evidence. `agent_stopped_at` remains a separate provider-gated idle value.
10. Backend `subagent_idle_trusted` and frontend `agentRunEndsOnSubagentIdle()` agree: Codex true, Claude false.
11. Only root lifecycle cutoffs end all descendants. Launcher idle never ends its active child.
12. REST and WS use the same tree identity and state semantics. An older REST response cannot overwrite newer WS evidence.
13. Provider homes resolve at call time. Tests use isolated homes and databases.
14. Existing task-notification rewrites, SendMessage guards, Codex v2 pairing, and workflows remain supported.
15. All artifacts and commits use English. Commit bodies explain behavior and include the executing Codex model trailer.

Completion evidence: [Nested Subagents Validation](2026-09-07-nested-subagents-validation.md).
Backend test files proposed in Tasks 1–6 consolidate into `tests/test_nested_agent_compute.py`
and the existing notification/recompute regression suites.

The tasks below replace the earlier illustrative code sketches. Implement against current source symbols, not old line numbers.
Tests must invoke real integration boundaries. A helper-only test cannot prove watcher, migration, or UI wiring.

## Task 1: Sidecar metadata

**Files:** `providers/claude_code/subagent_meta.py`, `providers/claude_code/compute.py` under `src/twicc/`;
`tests/test_subagent_meta.py`.

- [x] Extract the existing sidecar directory resolution from `_agent_launch_tool_use_id_from_sidecar`.
- [x] Expose `SubagentSpawnMeta`, `subagents_dir_for_file`, `read_subagent_meta`, and `read_subagent_metas`.
- [x] Use `NamedTuple`, `orjson`, call-time `claude_projects_dir()`, and tolerant filesystem/JSON handling.
- [x] Keep the existing helper as a thin caller. Preserve orphan-notification compatibility with synthetic non-hex test ids.
- [x] Directory scans exclude compact sidechains. Metadata validates field types and does not invent launcher identity from malformed fields.
- [x] Test root/nested paths, valid depth-1/depth-2 metadata, missing/invalid/unreadable data, and workflow isolation.

## Task 2: Provider extraction hooks and shared notification parser

**Files:** `src/twicc/providers/compute_base.py`, `src/twicc/providers/claude_code/{compute,notifications}.py`;
`tests/test_nested_agent_hooks.py`.

- [x] Expose provider hooks for one child's spawn metadata, tree metadata, async ack identity, and queue completion.
- [x] Defaults return no evidence. Codex pairing remains unchanged.
- [x] Return depth-1 metadata with launcher=root and its exact `toolUseId`.
- [x] Parse string and text-list ack bodies. Validate complete agent ids and reject ordinary results.
- [x] Extract the shared task-notification parser with its constants, fallback, and routing helper.
- [x] Keep parser result types dependency-free. `notifications.py` must not import compute classes or ORM modules.
- [x] Preserve existing private parser imports where tests or callers rely on them.
- [x] Queue parsing accepts enqueue completion notifications, ignores remove/nonterminal events, and supports historical payload-only completions.
- [x] Test malformed fields, terminal statuses, historical XML fallback, and structured-envelope priority over conflicting ack text.

## Task 3: Live metadata linking

**Files:** `src/twicc/providers/compute_base.py`; `tests/test_nested_agent_links_live.py`.

- [x] Resolve launcher from sidecar metadata before the child-side live path uses its prompt cache.
- [x] Key the done cache by launcher/child. The DB existence guard recognizes a link owned by any launcher.
- [x] Resolve authoritative tool ids only against actual agent-spawn blocks in the correct tree owner.
- [x] If that tool block is not synced, defer. Do not match another same-prompt block.
- [x] Use the launch tool timestamp and line. Preserve exact idempotent link identity.
- [x] Test through live compute, including child-before-tool, duplicate prompt, missing owner, and depth-1 metadata.

## Task 4: Async ack and launcher-side race recovery

**Files:** `src/twicc/providers/compute_base.py`; `tests/test_nested_agent_links_live.py`.

- [x] Gate ack fallback on absence of extracted structured identity, not absence of a database mutation.
- [x] Reuse guarded create/upgrade behavior: metadata/prompt may create foreground before the async ack arrives.
- [x] Upgrade only the same spawning tool id to background before result-count stop evaluation. Re-broadcast the upgrade.
- [x] Preserve the SendMessage continuation guard: another tool id never changes the original launch flag.
- [x] Run launcher-side recovery for root and subagent transcripts. Search the flat tree's sibling candidates.
- [x] Use sidecar identity first. Missing tool ids may use prompts only within a known matching launcher.
- [x] Use unique prompt matches when metadata is absent; ambiguous candidates stay unlinked until stronger evidence appears.
- [x] Test meta-first/ack-second, repeated ack, conflicting envelope, two same-prompt owners, and workflow transcripts.

## Task 5: Queue completion live processing

**Files:** `src/twicc/providers/compute_base.py`; `tests/test_queue_completion.py`.

- [x] Process persisted root enqueue completions on the live path.
- [x] Resolve owner from sidecar first, otherwise from actual spawn blocks in root/tree assistant/content items.
- [x] Confirm the child and owner belong to the notified root. Never stamp an unrelated session.
- [x] Backfill only Agent/Task links. SendMessage and Monitor notifications never create spawn links or flip background flags.
- [x] Share owner resolution with full recompute instead of duplicating scans and validation.
- [x] Stamp stops through existing `AgentStoppedUpdate` channels with the existing monotonic newer-activity guard.
- [x] Preserve completion evidence for transport even when it arrives before the link is cached or child row is ingested.
- [x] Test repeated completion, attachment-plus-queue, missing child/owner, stale completion after wake-up, and non-spawn tool ids.

## Task 6: Full recompute and apply races

**Files:** `src/twicc/providers/compute_base.py`; `tests/test_nested_agent_recompute.py`.

- [x] Derive owned links from structured results, ack, sidecar, and unique prompt evidence in that order.
- [x] Reuse matching rules from live recovery. Authoritative foreign sidecars never enter prompt fallback.
- [x] Existing links owned by the current root or launcher must not exclude their own candidates.
- [x] Include recovered links in result counting, so sync completion uses one result and async completion uses two.
- [x] Mine root completions for stop metadata and launcher backfills through `agent_links_backfill`.
- [x] Root-owned recovered links belong in the root diff. Non-owned links never enter that diff.
- [x] Deduplicate both owned creations and backfills under atomic apply, including a link inserted after compute snapshot creation.
- [x] Preserve revision guards, sliced apply, and zero-write diffs for unchanged links.
- [x] Ensure every queue-backfilled link survives the owner's next recompute, including missing-sidecar history.
- [x] Test repeated root and launcher recompute, all evidence sources, zero churn, and both backfill/owner apply orders.
- [x] Use the actual queue bytes and `ComputeApplyResult.outcome`, following existing recompute tests.

## Task 7: Watcher messages and compute version

**Files:** `src/twicc/providers/sessions_watcher.py`, `src/twicc/settings.py`; watcher/queue tests.

- [x] Add root identity to `agent_link_created` while retaining launcher identity in `parent_session_id`.
- [x] Broadcast persisted `agent_stopped` evidence with child id, timestamp, and root id.
- [x] Keep existing `session_updated` and `_after_agents_stopped` behavior. Do not treat mtime-only idle as persisted completion.
- [x] Test actual watcher output for nested launch and queue-only completion.
- [x] Bump current Claude compute version once, retaining history. Current baseline is 108; planned version is 109.
- [x] Leave Codex compute version unchanged.

## Task 8: Shared tree payload

**Files:** `src/twicc/providers/{helpers.py,claude_code/helpers.py,codex/helpers.py}`,
`src/twicc/core/session_queries.py`, `src/twicc/views.py`; `tests/test_subagents_tree_endpoint.py`.

- [x] Add provider `get_queue_completions` and `subagent_idle_trusted` surfaces without importing compute classes.
- [x] Build owner ids from root plus all flat subagents, including owners from different projects.
- [x] Build result counts by `(owner_session_id, tool_use_id)` and completion evidence by child/tool identity.
- [x] Scan root queue items once per request. Select latest valid completion timestamps deterministically.
- [x] Serialize `owner_session_id`, `started_at`, `stopped_at`, `agent_stopped_at`, and server `running` with existing fields.
- [x] Running applies root cutoff, required result count, persisted completion, then provider-gated child idle.
- [x] Keep the endpoint root-only. Share views call the same tree-state builder to prevent drift.
- [x] Test actual API responses for both providers, multiple owners, missing rows, root restart, and idle gate parity.

## Task 9: Frontend store state and ordering

**Files:** `frontend/src/stores/data.js`, `frontend/src/utils/agentLinkIndex.js`, corresponding node tests.

- [x] Keep owner-keyed link cache and add reverse lookup by child id. Store explicit root identity with each link.
- [x] Keep `stoppedAt` and provider-gated `agentStoppedAt` separate. Keep `startedAt` and server running state.
- [x] Update index on set, replacement, clear, eviction, and complete reset. Remove obsolete reverse entries.
- [x] Keep completion evidence arriving before link creation. Link upgrades cannot erase a newer stop.
- [x] Root cleanup visits every cached tree link, even when launcher Session rows are not loaded.
- [x] Launcher lifecycle updates never sweep its descendants. Preserve the existing child-own-state safety net.
- [x] Reconcile `running=false` by removing stale synthetic state.
- [x] Give tree fetches a generation/event-freshness rule. Older responses cannot overwrite WS changes or a later fetch.
- [x] Make tree loading safe for direct nested URLs and delayed root loading; do not assume mount implies fetch completion.
- [x] Test missing-owner cleanup, delayed REST after stop, stop-before-link, replacement/eviction, and reconnect state.

## Task 10: Main frontend WebSocket behavior

**Files:** `frontend/src/composables/useWebSocket.js`, store/pure state tests.

- [x] Route `agent_link_created` by launcher for cache and root for provider/navigation/synthetic state.
- [x] Route `agent_stopped` through persistent completion cache and remove synthetic state.
- [x] Keep result-count completion and provider idle gates consistent with the tree payload.
- [x] Handle duplicate and reordered messages without erasing completion or resurrecting old spawns.
- [x] Test WS mutations through an executable handler seam, not only independent helper examples.

## Task 11: Nested panels, stop controls, and comments

**Files:** `frontend/src/components/session/detail/items/ToolUseContent.vue`,
`frontend/src/components/session/detail/SessionHeader.vue`, relevant comment context consumers.

- [x] Remove only the View Agent root-only gate. Keep View Workflow behavior.
- [x] Use root identity for navigation, stop calls, and provider lookup. Use launcher identity for tool/link lookup.
- [x] Use root cutoff for agent pulse and starting state. Keep ordinary tools on their own session cutoff.
- [x] Honor persisted completion unconditionally and child idle only behind the provider gate.
- [x] Resolve header stop availability through reverse link lookup.
- [x] Resolve comment `subagentToolLineNum` to the root-owned ancestor spawn, not the immediate launcher line.
- [x] Keep comment context reactive when the tree fetch completes after editor mounting.
- [x] Test nested navigation, delayed links, comments, launcher idle with active child, and root restart.

## Task 12: Shares at every depth

**Files:** `src/twicc/share/{session_views.py,consumer.py}`,
`frontend/src/share-session/{ShareSessionApp.vue,shims/dataStoreShim.js,shims/shareLive.js}`;
`tests/test_share_nested_subagents.py` and frontend tests.

- [x] Use the shared tree payload builder. Preserve compute-readiness checks and `include_subagents` behavior.
- [x] Snapshot visibility follows launcher ownership to a root spawn at/before the frozen line.
- [x] Use visited-set traversal with no depth cap. Missing or cyclic ownership fails closed.
- [x] Apply the same visibility rule to list, metadata, items, tool states/results, and workflow-compatible descendants.
- [x] Preserve root result/item snapshot ceilings; nested items retain their existing un-clamped semantics.
- [x] Group initial and live links by owner. Mirror every newly used store member in the share shim.
- [x] Forward nested link creation and persisted completion to live shares of that root only.
- [x] Dispatch completion in `shareLive`, update the shim, and preserve REST/WS freshness semantics.
- [x] Test live queue-only completion, include-off, frozen-out subtree, visible depth >32, cycles, and cross-tree isolation.
- [x] Rebuild the standalone share bundle with the full frontend build.

## Task 13: Codex flat parenthood and real migration

**Files:** `src/twicc/providers/codex/initial_sync.py`, `src/twicc/providers/sessions_watcher.py`,
a provider-neutral root resolver, next `src/twicc/core/migrations/*_flatten_codex_subagent_parents.py`;
`tests/test_codex_flat_parenthood.py`.

- [x] Resolve ancestry to a proven root with visited-set cycle detection. Missing roots defer ingestion.
- [x] Initial sync resolves the run's new entries in memory before consulting pre-existing DB ancestry.
  Include new top-level roots mapped to `None`, or retain their proven root ids.
  A queued root is valid before DB insertion; an unknown orphan is not.
- [x] Keep producer topological gating on original direct-parent ids. Queue payloads store the root id.
- [x] Watcher updates `parsed.parent_session_id` before row creation, activity routing, or broadcasts.
- [x] Test actual `_sync_subagents` payloads with no writer, including depth >32 and cross-project trees.
  Include a completely new root and descendants, all queued but absent from DB.
- [x] Test the real watcher path and emitted root routing. Invalid cycles/missing ancestry never store a false root.
- [x] Choose migration number/dependency from current source. Use historical models and inline logic only.
- [x] Flatten existing valid chained Codex rows. Recalculate affected roots AND former parents after all moves.
- [x] Refresh affected stored project totals with existing aggregation semantics. Preserve null cost semantics.
- [x] Execute the actual migration callable with historical apps or MigrationExecutor; assert parents and all affected aggregates.
- [x] Audit all Codex parent readers, route resolution, costs, and share descendant membership. Keep v2 pairing untouched.

## Task 14: Acceptance, adversarial review, and delivery

- [x] Run focused backend/frontend tests during implementation, then `uv run pytest -q` and `cd frontend && npm test`.
- [x] Run `cd frontend && npm run build`, including standalone shares. Fix feature regressions before delivery.
- [x] Replay copies of real reference transcripts through isolated test compute and database; never recompute the running user's DB.
- [x] Historical Claude reference: `9e1cfb65-c745-43fc-bc5a-8a999804bf92`.
  Launcher `acc4e760a52c96577` links children `ad387380c886bae1d` and `ae1df3ede72f6b6da` at launch lines 60/62.
  Check stopped state and unchanged depth-1 links.
- [x] Recent Claude reference: `e7932c3f-fb59-4d1b-9d45-7b64eb18639c`.
  Preserve its three links owned by `a4a57eb737828fa4c`, background flags, and duplicate-free recompute.
- [x] Codex reference: `01a004e1-7a55-76e2-8130-f7398710088e`.
  Resolve full child ids from authoritative data. Prove nested route and root cost after actual migration.
- [x] Exercise rendered nested panels, plain tabs, direct URL reload, and visible pulse changes.
  Use an isolated test/browser harness without restarting the user's dev server.
- [x] Run the actual bundled Claude CLI with isolated homes and a controlled local model endpoint where possible.
  Produce root → launcher → async and sync children. Observe launch-time links and completion.
- [x] Send SDK `stop_task` for a running nested background child. Verify the real CLI terminal outcome.
  If the CLI rejects/ignores nested ids, hide nested Stop in both controls and record the evidence.
- [x] Keep useful runtime transcripts and verification results in session scratch/artifacts, outside the repo.
- [x] Run independent adversarial implementation reviews. Correct findings and repeat reviews until no material findings remain.
- [x] Audit every spec requirement against current source, tests, migration execution, and rendered/runtime evidence.
- [x] Update task checkboxes with actual completion evidence. Commit only feature-owned paths/hunks with a descriptive body.
- [x] Report checks, review results, stop verdict, and any material limitations.
  Remind the user to restart their instance through `devctl.py`; startup applies the pending data migration and recompute.
  Do not restart, migrate the running instance, install packages, or edit CHANGELOG without explicit authorization.

## Review record

2026-09-07, first independent review round: backend, frontend, and Codex reviewers identify
async upgrade, authoritative matching, recompute retention/apply races, root cutoff,
missing-owner cleanup, REST/WS ordering, live-share completion, root comment anchors,
unbounded depth, migration aggregate repair, and integration-test gaps.
This revision replaces contradictory sketches with the contracts and executable acceptance boundaries above.
Final review: backend approved after round two; frontend/API approved after round two and the asynchronous-loading wording correction; Codex approved after round three, including queued new roots. No material document findings remain.

Implementation review: backend/API/shares and Codex pass independent review.
Final compute review corrects ambiguous sibling/tool prompt matching; 24 focused tests pass.
Final frontend review corrects pre-link idle/wake ordering and duplicate live-link state erasure.
The independent reviewer approves both corrections; 19 focused frontend tests pass.
Full validation results and the unrelated backend failure appear in the validation report.
