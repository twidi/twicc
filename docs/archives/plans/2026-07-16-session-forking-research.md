# Session Forking — Research

**Status:** research only. No implementation or product decision.
**Updated:** 2026-09-08. Replaces the conclusions recorded on 2026-07-16.
**Scope:** Codex and Claude Code fork APIs, persistence, history boundaries, and current TwiCC support.

The design and implementation-plan documents were not opened during this investigation.

## 1. Findings

1. **Codex paginated forks reference history instead of copying it.** The child stores `session_meta.history_base` and its own suffix.
2. **Codex supports a direct fork boundary.** `thread/fork.lastTurnId` includes a completed boundary turn. Experimental `beforeTurnId` excludes its boundary turn.
3. **Fork plus rollback is obsolete for paginated history.** `thread/rollback` rejects paginated threads. `thread/revert` replaces their active rollout while retaining the thread ID.
4. **Logical provenance and physical history are separate.** `forked_from_id` identifies a logical parent. `history_base.thread_id` identifies a physical rollout.
5. **TwiCC does not resolve those history references.** Its paginated-format support does not imply fork support.
6. **Claude now exposes `resume_session_at` as a typed SDK option.** No `extra_args` workaround is necessary for this parameter.
7. **Claude has an offline `fork_session()` function.** It remaps message UUIDs and records `forkedFrom` on copied entries.
8. **Claude offline forks and CLI resume-forks need separate descriptions.** Their copying and provenance rules must not be assumed identical.

## 2. Evidence and versions

### 2.1 Scope of verification

| Evidence | What was verified |
|---|---|
| Local Codex Rust source | Protocol, fork preparation, lineage resolution, revert persistence, and upstream regression tests |
| Real Codex binary, isolated home | Full fork, both boundary parameters, paginated reads, nested fork, rollback rejection, revert |
| Installed Claude SDK source | Public options, CLI argument forwarding, offline fork transform, transcript-chain reader, checkpoint API |
| Real Claude SDK, isolated home | Offline full fork, boundary fork, branching transcript, UUID mapping, provenance, source preservation |
| Official Claude documentation | Public session and checkpoint interfaces |
| Current TwiCC source | Existing ingestion, compute, migration handling, and SDK wrappers |

The probes use synthetic transcripts. They do not load personal sessions or call a model.
Codex uses a local mock-provider configuration with an unreachable loopback endpoint.
No `turn/start` request is sent.

The current Claude CLI's live resume/fork behavior was **not** retested through a model turn.
The old document's live CLI observations remain historical evidence, not fresh verification.
The Rust regression tests listed below were inspected, not executed.

### 2.2 Version baseline

| Component | Version / revision |
|---|---|
| TwiCC Codex runtime | `0.153.4`, from `src/twicc/providers/codex/runtime.py` |
| Vendored Python SDK | `rust-v0.153.4`, from `pyproject.toml` |
| Codex source used for detailed research | `rust-v0.153.4`, commit `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a` |
| Upstream `origin/main`, fetched on 2026-09-08 | `d6489472f3c15e87d2d7763a5fde033545c530f8` |
| Installed / pinned `claude-agent-sdk` | `0.2.152` |
| SDK-bundled Claude Code CLI | `2.1.259`, verified with `--version` |

`/home/twidi/dev/codex` initially held a clean detached checkout of `rust-v0.153.2`.
After fetching, it was moved to `rust-v0.153.4` to match TwiCC.
No Codex source files were edited.

The fetched main branch has additional implementation changes, including migration and history materialization changes.
Its `ThreadForkParams` and `ThreadRevertParams` contracts retain the boundaries described here.
The detailed findings and runtime probes target **0.153.4**, not an untested future runtime.

Installed Claude source references below resolve under:
`/home/twidi/dev/twicc-poc/.venv/lib/python3.13/site-packages/claude_agent_sdk/`.
Primary files: [options](/home/twidi/dev/twicc-poc/.venv/lib/python3.13/site-packages/claude_agent_sdk/types.py:2170), [fork transform](/home/twidi/dev/twicc-poc/.venv/lib/python3.13/site-packages/claude_agent_sdk/_internal/session_mutations.py:240), [chain reader](/home/twidi/dev/twicc-poc/.venv/lib/python3.13/site-packages/claude_agent_sdk/_internal/sessions.py:934).

## 3. Codex

### 3.1 The paginated rollout model

A paginated rollout still uses JSONL records with `timestamp`, `type`, and `payload`.
It also assigns an `ordinal` to records and declares `history_mode: "paginated"` in `session_meta`.

The relevant metadata is:

| Field | Meaning |
|---|---|
| `id` | Logical thread ID; stable across `thread/revert` |
| `session_id` | Root-session identity; distinct from fork provenance, especially for subagents |
| `forked_from_id` | Logical source thread of a fork |
| `forked_from_ordinal_exclusive` | Exclusive inherited boundary associated with that logical parent |
| `history_base` | Physical rollout prefix used to reconstruct inherited history |
| `history_base.thread_id` | **Rollout ID**, despite the field name; not necessarily the owning thread ID |
| `history_base.end_ordinal_exclusive` | First ordinal excluded from that physical prefix |
| `history_base.end_byte_offset` | Byte position immediately after the final included JSONL record |
| `parent_thread_id` / subagent source metadata | Agent-control relationship; not interchangeable with `forked_from_id` |
| `subagent_history_start_ordinal` | Boundary separating inherited model context from a subagent's own projected history |

An ordinal is not a TwiCC line number, message UUID, turn ID, or API cursor.
The child header itself receives the inherited exclusive ordinal.
Its first subsequent local record receives the next ordinal.
For a root without a history base, ordinals start at zero.

Sources: [SessionMeta and HistoryPosition](/home/twidi/dev/codex/codex-rs/protocol/src/protocol.rs:3015), [ordinal allocation](/home/twidi/dev/codex/codex-rs/rollout/src/ordinal.rs:24).

### 3.2 Reference-backed forks

For a persisted paginated source, `thread/fork` prepares a bounded history reference.
The child file contains a new header and local initialization records, followed by future child activity.
It does **not** contain a verbatim copy of the inherited conversation.

Example header fragment from the two-turn probe, with identifiers replaced by labels:

```json
{
  "ordinal": 5,
  "type": "session_meta",
  "payload": {
    "id": "CHILD_THREAD_ID",
    "history_mode": "paginated",
    "forked_from_id": "SOURCE_THREAD_ID",
    "forked_from_ordinal_exclusive": 5,
    "history_base": {
      "thread_id": "SOURCE_ROLLOUT_ID",
      "end_ordinal_exclusive": 5,
      "end_byte_offset": 1213
    }
  }
}
```

The source fixture has nine records: its header and two four-record turns.
Forking through `turn-1` inherits source ordinals below five.
The child's header uses ordinal five; its file does not contain `ALPHA` or `BRAVO`.
Nevertheless, `thread/items/list` on the child returns the inherited `ALPHA` message.

A full fork inherits through ordinal nine and exposes both messages through the API.
Its local file also omits the inherited messages.
The old description of a mandatory double `session_meta` header and full copy is therefore incorrect for paginated forks.

Legacy history uses a separate copied-history code path.
Do not apply the paginated storage rules to every historical rollout.
Ephemeral forks are another exception: they can remain pathless and do not provide durable child files.

Sources: [fork request processing](/home/twidi/dev/codex/codex-rs/app-server/src/request_processors/thread_processor.rs:4693), [paginated fork preparation](/home/twidi/dev/codex/codex-rs/thread-store/src/local/paginated_fork.rs:15), [reference-backed fork regression test](/home/twidi/dev/codex/codex-rs/app-server/tests/suite/v2/thread_fork.rs:1357).

### 3.3 History resolution and dependency lifetime

Codex reconstructs a thread through an ordered sequence of bounded rollout segments.
It follows `history_base` recursively and reads each ancestor only through its recorded boundary.
A later append to the source does not extend that boundary.

The resolver distinguishes the current rollout of a thread from a referenced rollout ID.
It supports references into active or archived storage and handles compressed representations.
Byte boundaries refer to decoded JSONL when compression applies.
It checks missing sources, cycles, history mode, and cutoff validity.

This creates durable dependencies between files:

- Copying only a child JSONL does not produce a self-contained conversation export.
- Deleting or losing a referenced ancestor can break child reconstruction.
- Archiving is not equivalent to deletion; Codex resolves archived ancestors.
- A nested fork can reference its direct parent's rollout or an earlier segment containing the selected boundary.
- The logical fork parent can therefore differ from the physical prefix owner.

In the runtime probe, a nested fork still returns both original turns after its parent reverts to one turn.
The referenced old history remains available.

Sources: [rollout lineage resolution](/home/twidi/dev/codex/codex-rs/thread-store/src/local/rollout_lineage.rs:35), [rollout reference index](/home/twidi/dev/codex/codex-rs/rollout/src/rollout_reference_index.rs), [logical cutoff fallback](/home/twidi/dev/codex/codex-rs/rollout/src/metadata.rs:115).

### 3.4 Direct fork boundaries

| Request field | Behavior | Protocol status in 0.153.4 |
|---|---|---|
| Neither boundary | Fork the latest persisted snapshot | Normal API |
| `lastTurnId` | Include the selected terminal turn; omit later turns | Normal API |
| `beforeTurnId` | Exclude the selected turn and later turns | Experimental field |
| Both boundaries | Reject the request | Verified error |
| `excludeTurns: true` | Omit hydrated `thread.turns` from the response | Does not change inherited history |
| `path` | Select source by rollout path instead of `threadId` | Experimental field |
| `ephemeral: true` | Create a nonpersistent fork | Paginated fork requires `excludeTurns: true` |
| `deferGoalContinuation: true` | Carry a goal without starting its initial automatic continuation | Experimental; incompatible with `ephemeral` |

`lastTurnId` rejects an in-progress boundary turn.
`beforeTurnId` can cut before an unfinished persisted turn.
An unknown turn ID fails; it does not silently fork the full conversation.
Granularity remains **turn boundaries**, not arbitrary tool-call or assistant-message boundaries.

A latest-snapshot fork can capture an active source.
Upstream tests cover freezing that unfinished child snapshot as interrupted.
This does not mean `lastTurnId` accepts an active turn.
The active-source cases were not exercised in the isolated runtime probe.

Fork accepts model, cwd, instruction, approval, and sandbox/configuration overrides.
Persisted approval settings can be restored from the source's current/latest settings, even for an earlier conversation boundary.
A conversation fork is therefore not a complete historical snapshot of all runtime configuration.
Goals can cause automatic continuation; the offline probe contains no goal.

Minimal request:

```json
{"id":2,"method":"thread/fork","params":{"threadId":"SOURCE","lastTurnId":"TURN","excludeTurns":true}}
```

Use `initialize.capabilities.experimentalApi: true` when sending experimental fields such as `beforeTurnId`.
`lastTurnId`, `thread/rollback`, and `thread/revert` have no experimental annotation in the current protocol declarations.
The runtime probes enable experimental API support; ungated behavior is established from the protocol source.

`thread/fork` returns a new thread ID and loads the fork in the app-server.
No extra `thread/resume` step was needed before the probe's revert request.
For paginated threads, full-history response hydration is deprecated.
`excludeTurns: true`, followed by `thread/turns/list` and `thread/items/list`, is the current paging surface.
API cursors are opaque; do not construct them from ordinals.

Sources: [ThreadForkParams](/home/twidi/dev/codex/codex-rs/app-server-protocol/src/protocol/v2/thread.rs:518), [boundary lookup](/home/twidi/dev/codex/codex-rs/thread-store/src/local/paginated_fork.rs:94), [boundary and active-source tests](/home/twidi/dev/codex/codex-rs/app-server/tests/suite/v2/thread_fork.rs:566).

### 3.5 Revert replaces rollback for paginated truncation

`thread/rollback` remains deprecated and applies to legacy history.
The tested paginated request returns:

```text
paginated threads do not support thread/rollback
```

Paginated history instead supports:

```json
{"id":3,"method":"thread/revert","params":{"threadId":"THREAD","beforeTurnId":"TURN"}}
```

This is a truncation operation on an existing logical thread, not a new fork:

1. The app-server coordinates the loaded thread, including interrupting an active turn when necessary.
2. The store creates another rollout containing metadata and a reference to the retained prefix.
3. It preserves the old rollout.
4. It switches the thread's current rollout path in Codex SQLite.
5. The response retains the thread ID and provides the new path and paging cursors.

The response's `thread.turns` is empty. Hydrate retained turns through the paginated APIs.
The protocol also defines a `thread/reverted` notification.

A replacement filename can contain both the stable thread ID and a distinct rollout ID:

```text
rollout-<timestamp>-<thread-id>_<rollout-id>.jsonl
```

Consequences:

- One logical thread can own several physical rollout files.
- Parsing the first header still identifies the owning thread, but cannot identify which file is current.
- Selecting the newest-looking filename is not the documented authority; Codex maintains a current-path pointer.
- Existing forks keep their references to old prefixes after the parent reverts.
- `forked_from_ordinal_exclusive` survives separately from physical references. Reverting into inherited history can reduce this cutoff.

The probe confirms stable thread ID, changed path, preserved old file, and one retained turn.
It also confirms that a previously created nested fork retains both turns.
Neither revert nor fork restores workspace files.

Sources: [revert protocol](/home/twidi/dev/codex/codex-rs/app-server-protocol/src/protocol/v2/thread.rs:1247), [replacement rollout and SQLite cutover](/home/twidi/dev/codex/codex-rs/thread-store/src/local/revert_thread.rs:15), [revert regression tests](/home/twidi/dev/codex/codex-rs/app-server/tests/suite/v2/thread_revert.rs).

### 3.6 Python SDK and CLI surface

The vendored SDK is not equivalent to the complete app-server protocol:

| Layer | Current support |
|---|---|
| `AsyncCodex.thread_fork()` / sync equivalent | Full fork and common configuration overrides; no `last_turn_id` argument |
| Generated `ThreadForkParams` | Includes `last_turn_id` / `lastTurnId` |
| Generated stable fork model | Omits experimental `beforeTurnId`; also omits `excludeTurns` in this vendored model |
| Low-level `client.thread_fork(thread_id, params)` | Can send the generated boundary parameter |
| Typed `thread_revert` / `thread_rollback` client methods | Not present in the inspected clients |
| TwiCC wrapper | Already uses low-level `request(...)` for other missing protocol surfaces |

Passing absent fields to a Pydantic model is not a reliable way to send them.
The generated fork model does not enable arbitrary extra fields.
A later integration must verify the serialized wire request.

The interactive CLI still has `codex fork` selection options.
The JSON-RPC app-server is the relevant programmatic interface for TwiCC.
No terminal automation is needed to obtain the fork behaviors above.

Sources: [high-level wrapper](../../src/openai_codex/api.py:237), [generated fork parameters](../../src/openai_codex/generated/v2_all.py:8135), [low-level client](../../src/openai_codex/client.py:457), [existing TwiCC request wrappers](../../src/twicc/providers/codex/sdk_wrappers.py:142).

## 4. Claude Code

### 4.1 Two separate fork interfaces

| Interface | Execution | Current evidence |
|---|---|---|
| `ClaudeAgentOptions(resume=..., fork_session=True)` | Resume through the CLI into a new session | Public SDK contract; historical live observations |
| Add `resume_session_at=<entry UUID>` | Load through the selected entry, inclusive | Typed option and CLI forwarding verified |
| `resume_session_at` without `fork_session` | Truncating resume under the existing session identity | Historical branching behavior; current typed truncation option |
| `fork_session(session_id, directory=..., up_to_message_id=..., title=...)` | Copy stored transcript without starting the CLI | Current source and isolated runtime probe |
| `fork_session_via_store(...)` | Async equivalent through `SessionStore` | Current source; not separately probed |

The live option `fork_session` and the standalone function `fork_session()` share a name but perform different work.
Do not transfer file-format observations from one to the other without testing.

The SDK now forwards `resume_session_at` as `--resume-session-at=<uuid>`.
It also exposes `session_id` for caller-selected session identity, subject to resume/fork constraints.
The offline fork function has no caller-selected child-ID parameter; it generates one and returns `ForkSessionResult.session_id`.

Sources: installed `claude_agent_sdk/types.py:2000` and `:2170`; `_internal/transport/subprocess_cli.py:698`; `_internal/session_mutations.py:240`.
The [official session documentation](https://code.claude.com/docs/en/agent-sdk/sessions) describes resume and fork as public session operations.

### 4.2 Offline fork persistence

The installed `fork_session()` implementation:

- Reads the source transcript and retains recognized transcript-entry types with UUIDs.
- Removes sidechain entries.
- Applies an inclusive **file-order cutoff** when `up_to_message_id` is supplied.
- Creates fresh UUIDs and remaps `parentUuid` links.
- Omits progress entries and walks through their parents to retain useful chain links.
- Remaps `logicalParentUuid`, used for compaction backpointers.
- Sets `sessionId` to the new session ID.
- Stamps each copied writable entry with `forkedFrom: {sessionId, messageUuid}`.
- Keeps original timestamps except for the last writable entry, whose timestamp becomes current.
- Removes source-specific fields such as `teamName`, `agentName`, `slug`, and `sourceToolAssistantUUID`.
- Re-emits collected content replacements and appends a custom title.
- Does not copy file-history snapshots or undo history.

Provenance example:

```json
{
  "uuid": "NEW_MESSAGE_UUID",
  "sessionId": "CHILD_SESSION_ID",
  "forkedFrom": {
    "sessionId": "SOURCE_SESSION_ID",
    "messageUuid": "ORIGINAL_MESSAGE_UUID"
  }
}
```

The probe confirms new session/message UUIDs, valid remapped parent links, copied provenance, and an unchanged source file.
Forking through the first assistant entry keeps two messages from the five-entry fixture.

**A file-order prefix is not an ancestor-only copy.**
The fixture also contains a sibling assistant entry attached to the first user message.
Forking through that final sibling copies all five earlier entries, including the other branch.
The original tree structure remains through remapped parent links.
This differs from the historical CLI ancestor-chain observation.

The function also collects content replacements separately from the cutoff transcript.
Compacted transcripts and replacement references need additional behavioral tests before relying on arbitrary-point copies in production.

Sources: installed `_internal/session_mutations.py:352` (`_build_fork_lines`) and `:592` (`_parse_fork_transcript`).

### 4.3 Tree interpretation and compaction

Claude transcript links still support a tree through `uuid` and `parentUuid`.
The current SDK reader builds the visible conversation by selecting a terminal user/assistant leaf and walking its parents.
It prefers a main-chain leaf and breaks ties using file order.
It does not use `last-prompt.leafUuid` as its sole source of truth.

`logicalParentUuid` is no longer an unexplained field in this research.
The installed SDK identifies it as a compaction-boundary backpointer.
Its visible-chain reader deliberately does not follow that pointer, to avoid duplicating pre-compaction content.

The old document's claim that the active path always follows the latest `last-prompt.leafUuid` is too strong.
Likewise, not every line with a UUID is a conversation node: offline forks append a UUID-bearing custom-title entry.
Transcript types and compaction semantics matter alongside parent links.

Source: installed `_internal/sessions.py:934` (`_build_conversation_chain`).

### 4.4 Safe truncation option

`ClaudeAgentOptions.resume_drops_turn` accompanies `resume_session_at`.
It names the user prompt whose turn the caller intends to discard.
The CLI checks that every entry after the selected boundary belongs to that discarded turn.

This guards against unseen queued user messages or task notifications entering the discarded suffix.
A rejected resume currently raises a `ProcessError` containing:

```text
Resume rejected by --resume-drops-turn:
```

The SDK guidance chooses the **last transcript entry** of the retained turn as the boundary.
The last assistant message can be too early with structured output or end-turn MCP tools.
Leaving `resume_drops_turn` unset retains unvalidated truncation behavior.
This option is not a generic guard for discarding any number of turns.

Source: installed `types.py:2186`; `_internal/transport/subprocess_cli.py:707`.
The guard's runtime rejection was not exercised in this investigation.

### 4.5 Workspace files

Forking conversation history does not automatically restore files.
However, the old statement that checkpoint rewind is unavailable through the SDK is false.
The SDK exposes `enable_file_checkpointing` and `ClaudeSDKClient.rewind_files(user_message_id)`.
These require suitable checkpoints and do not turn an offline fork into a filesystem snapshot.
Checkpointing tracks `Write`, `Edit`, and `NotebookEdit` changes; Bash edits are not captured.
Rewinding files does not rewind the conversation.

Sources: installed `types.py:2318` and `client.py:335`; [official checkpoint documentation](https://code.claude.com/docs/en/agent-sdk/file-checkpointing).

### 4.6 Historical CLI observations that still need a fresh live probe

The July investigation reported these behaviors for the then-current CLI:

- `--resume --fork-session` copied history with original message UUIDs.
- `--resume-session-at` without `--fork-session` appended another branch under the same session ID.
- Combining the flags copied only the selected ancestor chain into the child.
- Those tested CLI-created child files carried no explicit fork provenance.

These statements are **not** asserted for CLI `2.1.259` without a new live probe.
The current offline SDK implementation already disproves their generalization to all Claude forks.
Searching binary strings alone would not verify transcript-writing behavior.

## 5. Current TwiCC integration gaps

This section records observed gaps and their consequences. It does not select a design.

### 5.1 Codex

| Observed code | Consequence |
|---|---|
| Initial sync reads `payload.id`, cwd, and subagent source metadata | It identifies thread ownership, but does not extract a fork lineage |
| Raw ingestion reads each file independently | A reference-backed child lacks inherited messages in TwiCC's stored rows |
| No `history_base` or `forked_from_ordinal_exclusive` consumer in `src/twicc` | Codex API reconstruction and TwiCC display can contain different history |
| Session storage tracks a single `file_path` | Revert's multiple rollouts per stable thread require explicit current-path handling |
| Rewrite detection checks file shrinkage and legacy-to-paginated transitions | This does not establish correct handling of a newly created replacement rollout |
| Compute recognizes paginated canonical items | Canonical-item support alone does not resolve cross-file history dependencies |
| No current fork-boundary SDK wrapper | The high-level `thread_fork()` convenience method is insufficient for the complete protocol |

The current migration coordinator rebuilds rewritten legacy history through Codex.
It does not resolve a paginated child's missing ancestor prefix.
A file that is already paginated is not automatically a self-contained history.

Costs also need separate treatment from display reconstruction.
Current Codex compute already filters token snapshots and repeated totals.
The July claim that every copied snapshot necessarily doubles costs is therefore too broad.
Conversely, displaying inherited history must not attribute the parent's spend to each child.
The exact accounting behavior for continued forks needs token-event fixtures and a focused runtime probe.

Subagent history is a separate case.
`subagent_history_start_ordinal` can hide inherited model context from the child's projected turns.
A user-visible fork and an agent spawn must not share assumptions merely because both inherit context.
The inspected TwiCC code contains no consumer for that boundary field.

Sources: [initial metadata extraction](../../src/twicc/providers/codex/initial_sync.py:69), [initial file matching](../../src/twicc/providers/codex/initial_sync.py:546), [rewrite detection](../../src/twicc/providers/codex/sessions_watcher.py:95), [compute rules and accounting](../../src/twicc/providers/codex/compute.py:11), [migration coordinator](../../src/twicc/providers/codex/background_compute.py).

### 5.2 Claude

TwiCC has no current consumer for `forkedFrom`, `fork_session`, or `resume_session_at` in the inspected backend.
Its raw transcript ingestion does not itself supply a fork relationship or active-branch projection.

The new offline provenance provides exact source-message mappings.
UUID duplication is no longer a general detection strategy because the SDK remaps UUIDs.
Older CLI forks can still lack the new field.
The provider version and creation route remain relevant when interpreting imported forks.

### 5.3 Shared implications

- A new session ID does not imply independent history storage.
- Physical JSONL order does not always equal the selected conversation path.
- Fork origin, active history, subagent parentage, and filesystem state are different relationships.
- Append-only tailing alone is insufficient: migration can rewrite files, and revert can replace a thread's current file.
- Export, deletion, archive handling, search indexing, costs, and message navigation all depend on the inherited-history boundary.

## 6. Reproduction and acceptance evidence

### 6.1 Codex runtime probe

Run the existing `0.153.4` binary with a fresh `CODEX_HOME` and a synthetic workspace.
Create one paginated rollout with a fresh UUID and ordinals `0..8`:

| Ordinal | Record |
|---|---|
| 0 | `session_meta`, paginated, synthetic cwd and provider |
| 1 | `event_msg.task_started`, `turn_id: "turn-1"` |
| 2 | `response_item.message`, user text `ALPHA` |
| 3 | `event_msg.item_completed`, canonical `UserMessage`, ID `user-1`, text `ALPHA` |
| 4 | `event_msg.task_complete`, `turn_id: "turn-1"` |
| 5–8 | Same four records for `turn-2`, ID `user-2`, text `BRAVO` |

Canonical completion records include `thread_id`, `turn_id`, and `completed_at_ms`.
The wire projection returns `userMessage`, while persisted canonical items use `UserMessage`.

Start `codex app-server --listen stdio://` and send `initialize` with experimental API support.
Send these requests and inspect their child files and paginated responses:

| Operation | Verified result |
|---|---|
| Full fork with `excludeTurns: true` | New ID; base exclusive ordinal 9; local file omits both messages; API exposes both |
| Fork with `lastTurnId: "turn-1"` | Base exclusive ordinal 5; API exposes only `ALPHA` |
| Fork with `beforeTurnId: "turn-2"` | Same prefix as the previous request |
| Fork with nonexistent `lastTurnId` | Error `turn not found: missing` |
| Fork with both boundaries | Error: incompatible parameters |
| Fork the full fork | New ID; logical parent is the first child |
| Rollback the full fork | Error: paginated threads do not support rollback |
| Revert the full fork before `turn-2` | Same ID; new rollout path; old file unchanged; one retained turn |
| Read the nested fork after parent revert | Both original turns remain |
| Compare original source bytes | Unchanged throughout the probe |

The observed child files contain one new `session_meta` plus an initialization record before any new user turn.
That count is an observation from this fixture, not a required general file layout.

### 6.2 Claude offline probe

Use the installed SDK with a fresh `CLAUDE_CONFIG_DIR`.
Create a five-entry transcript with this parent graph:

```text
ALPHA (user)
  A (assistant)
    BRAVO (user)
      B (assistant)
  SIBLING (assistant, written last)
```

Call `fork_session()` for the full transcript, through `A`, and through `SIBLING`.
Inspect the new files:

| Check | Verified result |
|---|---|
| Full fork | Five copied messages; fresh UUIDs; per-entry provenance |
| Fork through `A` | Two copied messages |
| Fork through `SIBLING` | Five copied messages, not only its two-node ancestor chain |
| Parent links | Remapped to copied UUIDs |
| Original file | Byte-for-byte unchanged |

### 6.3 Remaining verification limits

Before implementation, distinguish source-established behavior from these unexecuted cases:

- Current Claude CLI live resume-forks: copied UUIDs, provenance, same-file branches, and chosen child IDs.
- Claude guarded truncation with queued messages, structured output, and compaction.
- Codex latest-snapshot forks during an active model/tool turn; source tests cover these cases.
- Codex goal inheritance, file compression, missing ancestors, archive/unarchive, and cold restart after revert.
- Cost attribution after a real continued fork, across both providers.
- TwiCC end-to-end ingestion of these fixtures. No application implementation or integration test was added.

These limits do not affect the verified central finding: paginated Codex forks depend on referenced rollout prefixes.
