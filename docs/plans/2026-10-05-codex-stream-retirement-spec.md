# Codex stream retirement through durable item identity

Date: 2026-10-05

Status: draft specification for review. This document does not authorize product implementation.

## Outcome

A persisted Codex item replaces its streaming block without a page refresh.

Replacement depends on the item's identity, not the arrival order of SDK events and watcher broadcasts.

The same replacement works for WebSocket items and REST items. Text and reasoning both use this contract.

## Problem and evidence

Codex streaming events and JSONL synchronization follow separate asynchronous paths.

The agent currently records completed SDK item identifiers in an in-memory FIFO.
The watcher removes one identifier for each eligible item and adds a wire-only `stream_uuid` field.

The agent pushes the identifier after awaiting its `stream_block_stop` and `stream_block_end` broadcasts.
The watcher can publish the persisted item before this push.

This sequence has two defects:

1. The persisted item has no `stream_uuid`. The frontend cannot match it to its streaming block.
2. A later item can consume the delayed identifier. The FIFO then associates different items.

The frontend's fallback reads Claude's `message.id` and `uuid`. It does not read Codex's durable identity.
Turn-end cleanup keeps ended blocks with a UUID. The unmatched block can therefore survive beside the persisted item.

A page refresh removes the temporary streaming state and reloads the persisted history.

The investigation reproduces both defects by executing the current enrichment and retirement methods.
This reproduction establishes a code defect. It does not establish the event order of every reported occurrence.

Local SDK logs and JSONL records confirm equal identifiers for assistant text and visible reasoning.

## Scope

- Replace FIFO matching with identity matching in the frontend.
- Remove the backend streaming registry and its wire enrichment.
- Preserve Claude's existing block matching.
- Preserve streaming publication, retirement cleanup, and scroll continuity.
- Add regression coverage for event ordering and REST loading.

### Outside this change

- SDK or Codex runtime upgrades.
- Database columns, migrations, and JSONL rewriting.
- Markdown rendering, publication rates, and buffer smoothing.
- Display modes, process indicators, and tool rendering.
- New timeouts, text comparisons, or timestamp matching.
- General store or provider refactoring.

## Durable identity

Codex's `stream_block_start.message_id` contains the SDK item identifier.
The identifier is available before `stream_block_end` sets the block's `uuid`.

| Streaming block | Persisted display item | Identity path | Required item kind |
|---|---|---|---|
| Text | `event_msg.item_completed` with `AgentMessage` | `payload.item.id` | `assistant_message` |
| Thinking | `response_item` with `reasoning` | `payload.id` | `reasoning` |

The canonical `Reasoning` completion is a debug duplicate. It must not replace the visible thinking block.
The visible reasoning row remains the existing `response_item.reasoning` row.

Identity matching requires a non-empty string identifier and the expected envelope, item type, and item kind.
An absent or malformed identifier does not match. No positional fallback applies.

Synthetic plan messages and other generated items receive no special matching rule.
They match only if they satisfy the same identity and type requirements.

## Provider boundary

Provider-specific matching belongs in frontend provider helpers.
The generic store must not inspect Codex payload shapes or branch on provider names.

Define this helper contract:

`matchesStreamingBlock(parsed, itemKind, messageId, block): boolean`

- `parsed` comes from `getParsedContent(item)`.
- `itemKind` is the persisted item's `kind`.
- `messageId` is the current streaming entry's `messageId`.
- `block` is a current block in that entry.
- The method returns a match decision. It does not mutate state.

The base helper returns `false`. Each supported provider implements its matching rules.
Resolve the provider from the session or its process state when session metadata is not yet available.
An unresolved provider must not cause an exception or retire an unrelated block.

### Codex rules

- Text matches an `AgentMessage` identifier equal to `messageId` and a block with `blockType === 'text'`.
- Thinking matches a visible reasoning identifier equal to `messageId` and `blockType === 'thinking'`.
- Both rules apply before the block receives a UUID.
- A populated block UUID must agree with the same identifier.
- Matching operates within one session. Equal identifiers in another session cannot retire this block.

Codex currently uses one streaming block per item, with `block_index === 0`.
Multiple reasoning summary parts append to that one block. They do not create separate replacement identities.

### Claude rules

Preserve the current eligible item kinds and matching requirements.
The parsed `message.id` must equal `messageId`. The parsed `uuid` must equal the block's populated UUID.

Do not infer Claude block completion from a message identifier alone.
One Claude message can contain several blocks with separate completion identities.

## Retirement lifecycle

### Persisted item arrives during streaming

`addSessionItems()` places the persisted item in the session array first.
It then calls `_retireStreamingBlocks()` before recomputing visual items.

For Codex, a matching persisted item retires its block immediately, even if `stream_block_end` has not arrived.
The persisted item proves completion. Retirement does not wait for the adaptive buffer to drain.

The next visual list contains the persisted item and excludes its synthetic streaming row.
There must be no intermediate visual-list publication containing both rows from this operation.

### Persisted item is already loaded

When `streamBlockStart()` creates a block, check loaded eligible items for a replacement.
This covers a watcher or REST result that arrives before the SDK start event reaches the frontend.

When `streamBlockEnd()` records a UUID, use the same matching and retirement path for already-loaded items.
This preserves Claude's retroactive matching and handles delayed content loading.

Use `_retireStreamingBlocks()` for actual removal. Do not duplicate its cleanup in event handlers.
Recompute visual items only after the removal operation completes.

### Events after retirement

Late deltas, stop events, and end events for a retired block do not recreate it.
A repeated start event must not recreate a block whose persisted replacement is already loaded.

An event for an old message cannot modify the current message's block.
Keep the existing message and publication-identity guards on buffer callbacks.

### Loading and turn completion

REST item loading already passes through `addSessionItems()`. It must use the same identity rules as WebSocket loading.
Items received while the session is not fetched remain subject to the existing fetch policy.
The later REST result must support retirement without wire-only metadata.

Retain cleanup for interrupted blocks without completion evidence.
Remove the unconditional load-time deletion of ended blocks. Completion alone does not establish an orphan.

Starting or retrying a REST fetch must preserve an ended block until its persisted replacement is loaded.
A slow or failed fetch must not erase that block or its detail and group state.
On success, the matching persisted item retires the block through the normal replacement path.

These rules concern item loading. Existing explicit session disposal and connection-reset policies remain outside this change.
Update cleanup comments to remove claims that Codex identity exists only on live broadcasts.

Turn completion alone does not prove that the persisted item has reached the frontend.
Do not drop all completed blocks at turn end or add a replacement timeout.

## Cleanup and visual continuity

Retirement preserves these existing operations:

- Clear the block's inactivity timer.
- Flush and release its buffer through the current buffer lifecycle.
- Transfer thinking detail state to the persisted item's detail key.
- Transfer or remove synthetic expanded-group entries through the current rules.
- Remove the streaming session entry when no blocks remain.
- Return `{ streamingLineNum, realLineNum }` pairs for the scroller's replacement hook.

The `SessionItemsList.vue` action hook must still receive these pairs before the caller publishes the replacement list.
Keep the current measured-height transfer, temporary height floor, and user-scroll protection.

Do not change full-text storage, parsed-content caching, or publication scheduling.
Use `getParsedContent()` for all item content access.
Do not scan the conversation on each delta or buffer publication.
Search loaded items only at structural lifecycle events that require retroactive matching.

## Backend removal

Remove `src/twicc/providers/codex/streaming_registry.py`.
Remove its import, completion pushes, and session cleanup calls from the Codex agent.

Remove Codex's `enrich_live_items_payload()` override.
Remove the base hook and watcher call, since this hook currently serves only the FIFO bridge.

Stop producing `stream_uuid`. The frontend must not use it as a replacement authority.
The SDK streaming WebSocket events keep their current shapes.
REST and WebSocket item content keep their current shapes.

No schema migration or stored-data update is required.
Existing canonical records already contain the identity.

## Files affected

| File | Responsibility |
|---|---|
| `frontend/src/providers/baseHelpers.js` | Neutral matching contract |
| `frontend/src/providers/codex/helpers.js` | Codex text and reasoning matching |
| `frontend/src/providers/claude_code/helpers.js` | Existing Claude identity matching |
| `frontend/src/stores/data.js` | Shared retirement and retroactive matching |
| `frontend/src/components/session/detail/SessionItemsList.vue` | Remove unconditional ended-block cleanup before REST; preserve scroll hook |
| `src/twicc/providers/codex/agent/agent.py` | Remove registry producer and cleanup calls |
| `src/twicc/providers/codex/helpers.py` | Remove FIFO enrichment |
| `src/twicc/providers/helpers.py` | Remove unused enrichment contract |
| `src/twicc/providers/sessions_watcher.py` | Remove enrichment call |
| `src/twicc/providers/codex/streaming_registry.py` | Delete obsolete registry |

Use existing canonical readers where they fit. Add focused reader helpers only when the provider matching needs them.
Do not add a backend serializer field, another identity registry, or a persisted streaming state.

## Acceptance and regression coverage

Tests must execute the production matching and retirement code.
Cover text and thinking where both follow the same lifecycle.

| Input or sequence | Required result |
|---|---|
| Start, deltas, persisted item, end | Retire on persisted-item arrival; late end is harmless |
| Start, deltas, end, persisted item | Keep content until persisted-item arrival, then retire |
| Persisted item, start, deltas, end | Do not publish a duplicate streaming row |
| Persisted item arrives through REST | Same retirement as WebSocket |
| Items arrive before initial fetch | Later REST load leaves one persisted version |
| Ended thinking block, session switch, slow REST | Keep the block and its open state until replacement |
| Failed REST fetch, then retry | No premature removal; successful replacement transfers preserved state |
| Two consecutive messages | Match each identifier; no FIFO displacement |
| Repeated start, stop, or end | No duplicate block or repeated cleanup |
| Delayed old-message event | Current message and its buffer remain unchanged |
| A completes, B starts, repeated start for A | Do not recreate A or replace B |
| Missing, empty, or malformed identifier | No false retirement and no exception |
| Populated block UUID differs from `messageId` | No retirement through an inconsistent identity |
| Correct identifier with wrong kind or block type | No retirement |
| Canonical `Reasoning` debug duplicate | No retirement of the visible thinking block |
| Reasoning with several summary parts | Replace the one streamed thinking block |
| Same identifier in another session | No cross-session retirement |
| Open thinking details and expanded group | Existing state transfer remains correct |
| Pending buffer callback after retirement | No stale row publication |
| Claude message with several content blocks | Only the matching completed block retires |
| Interrupted block without completion | Existing orphan cleanup remains effective |

Check the frontend dependency graph for new static import cycles.
Provider helpers must not introduce a helper-to-store-to-helper cycle that causes full HMR reloads.

Run focused frontend regression tests, then the frontend suite with `cd frontend && npm test`.
Run relevant existing Codex agent and watcher tests for the backend removal.
Use `uv run pytest` for backend tests. Use `uvx ruff` if Python lint verification is needed.

Verify the rendered replacement without refreshing the browser:

1. Stream text, then supply its persisted item before the end event.
2. Repeat with an open thinking block and several summary parts.
3. Observe one final row and transferred detail state.
4. Check scroll continuity at the bottom and while reading above the bottom.
5. Check a session switch and later REST loading of the completed item.
6. Delay or fail that REST result. Check that content and open thinking state remain until replacement.

A controlled event fixture must exercise both arrival orders. A live turn alone cannot prove race coverage.
If browser validation is unavailable, report that limit separately from automated test results.

## Delivery constraints

Implement only after explicit user authorization. Do not change the changelog under this specification.
No dependency installation, migration, or SDK diagnostic is required by the proposed change.
Backend removal requires the user to restart their running backend through `devctl.py` after implementation.

## Specification review

Two independent internal reviewers complete an adversarial specification review on 2026-10-05.
Both reviews focus on contracts and observable behavior, rather than implementation details.

- Round 1 finds one blocking contradiction: unconditional cleanup before REST loses content and open thinking state.
- The revision removes that cleanup requirement and adds slow-fetch, failed-fetch, retry, and late-start acceptance cases.
- Round 2 finds no blocking defects. It considers the specification ready for implementation planning.
- Its optional inconsistent-UUID acceptance case is included above. The identity contract already requires that behavior.

Review completion does not authorize implementation or mark the draft as user-approved.
