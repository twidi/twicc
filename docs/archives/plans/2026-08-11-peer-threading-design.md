# Peer Message Threading (`reply_to`) — Design

**Status:** designed with the user 2026-08-11; Backend lot 1 and the amended production grammar are implemented at HEAD `8a84421f`; lots 1.1, 2, and 3 are not implemented
**Date:** 2026-08-11
**Scope:** let one peer message reference the message it answers, so an exchange between two sovereign TwiCC instances holds together — the receiving agent gets a handle it can reply with, the receiving human gets the target session proposed, and both inboxes show what answers what.

---

## 0. Relationship to the 2026-07-24 peer messaging design

`docs/plans/2026-07-24-peer-messaging-design.md` is the peer system's founding design. Its §8 recorded threading as deferred and sketched a mechanism as of that date. **That document is historical and stays unchanged.** This one is the current design for threading; where the two differ, this one holds.

Two facts moved between the two dates and change §8's sketch:

- **Delivery no longer injects.** `src/twicc/core/services/peer_messages.py::mark_delivered` resolves the message and returns `(success, envelope, errors)`. `src/twicc/peer/owner_views.py::peer_message_deliver` returns the successful envelope to `frontend/src/components/peer/PeerMessageReviewDialog.vue`, which prefills a composer; the human sends. §8 assumed injection into a session that always exists; the "deliver to a new session" path now produces a local draft with no `Session` row (see §2.5).
- **`origin` lost the sending session's title.** It carries `sent_at` and nothing else (comment on `core.models.PeerMessage.origin`). §8's provenance assumptions are narrower than it described.

## 1. Scope

In scope:

- one new wire field, `reply_to`, the columns backing it, and one safe token grammar for `message_id` and non-empty `reply_to`;
- local thread identity (`thread_id`), reply resolution, and the serializer contract that carries both to the UI;
- pre-selection of the delivery target in the review dialog;
- one line in the inbox row and in the review dialog, one word in the toast;
- `--reply-to` on `twicc peer-send`, and a safe message id in the delivery envelope.

Out of scope: §13.

## 2. Validated pre-implementation baseline

This section records the `peer-system` code baseline validated before Backend lot 1. It is a historical design input, not a current code inventory. Sections 3 and after state decisions. Section 18 states the current implementation status and lot order.

### 2.1 Model

`core.models.PeerMessage` defines these explicit fields: `peer` (FK, CASCADE), `direction` (`core.models.PeerMessageDirection`: `in` / `out`), `message_id`, `title`, `payload`, `attachments_meta`, `origin`, `origin_session` (FK `Session`, SET_NULL), `status` (`core.models.PeerMessageStatus`: `pending` / `delivered` / `refused` / `failed`), `error`, `recipient_note`, `delivered_to_session` (FK `Session`, SET_NULL), `created_at`, `resolved_at`, `purged_at`.

Its `Meta` declares `ordering = ["-created_at"]`, a `UniqueConstraint` on `("peer", "direction", "message_id")` named `uniq_peermessage_peer_direction_msgid`, and an index on `("status", "direction")`.

`core.services.peer_tokens.mint_message_id` returns `"pm_" + secrets.token_hex(8)` — 19 characters, against a `max_length` of 40.

`core.models.Session.archived` is a `BooleanField(default=False)`.

The latest migration file is `src/twicc/core/migrations/0133_share_created_by_session.py`; applied migration state is instance-dependent.

### 2.2 Wire

`peer.outbound.post_message` POSTs `{message_id, title, payload, origin}` to `<base_url>/peer/messages/` with a Bearer token. `title` is a top-level wire field with its own column, for the reasons given in the `PeerMessage.title` comment; `origin` is the sender-declared provenance blob.

### 2.3 Send path

`core.services.peer_messages.send_peer_message_from_payload` handles the `peer:send` drop-request kind. It resolves the peer by id then by exact name, refuses `broken` and non-`active` peers, mints the `message_id`, builds `origin = {"sent_at": …}`, stores the row `pending`, POSTs it, and maps the response: `202` keeps `pending`, `403` marks the row `failed` and the peer `broken` (`core.services.peer_mutation.mark_peer_broken`), anything else marks the row `failed`.

`src/twicc/cli/peer_send.py::peer_send_cmd` takes `PEER`, `TITLE`, `PROMPT` positionally plus `--attach` and `--timeout`. After `django.setup()` it pre-checks the peer with the ORM (`Peer.objects.filter(id=…)` then `filter(name=…)`) and the title with `core.services.peer_messages.validate_title`, then submits the payload through `src/twicc/cli/_drop_request/transport.py`. It fills `origin_session_id` from `cli._drop_request.whoami.resolve_current_session` when that resolves.

### 2.4 Receive path

`core.services.peer_messages.receive_peer_message` rejects non-`active` peers with the same `403` as an unknown token. At that baseline, it accepts `message_id` only as a non-empty string of at most 40 characters. It has no `reply_to` handling and no token-pattern validation. It validates `title` through `validate_title` and validates `payload` through `_validate_inbound_payload`, with `400 invalid_payload` on failure. It accepts an absent or `null` `origin` as `{}`, otherwise requires a dict, and reads `sent_at` from it. It then rebuilds the stored `origin` as `{"sent_at": sent_at}`. Idempotency is re-checked inside the write lock; an existing `(peer, in, message_id)` row short-circuits to `202` with its current status.

### 2.5 Delivery, envelope, and the local end

`core.services.peer_messages.mark_delivered` runs under a per-pk `asyncio.Lock`, re-reads the row through `_fresh_message` (which `select_related`s `peer`, `origin_session`, `delivered_to_session`), applies `_delivery_guards`, builds the envelope, writes the status, and fires the best-effort status callback. It returns `(success, envelope, errors)`; it injects nothing.

`_mark_delivered` links `delivered_to_session` only when a `Session` row already exists. The "deliver to a new session" path passes no `session_id`: the target is a local draft created by `dataStore.createDraftSession`, and `core.services.peer_messages.link_delivered_session` fills the link later, once the provider has created the real session — or never, if the user discards the draft.

`core.services.peer_messages.build_delivery_envelope` produces a single `::` colon-block header line carrying the message title, the peer's local name, the peer's `base_url` and the formatted `sent_at`, followed by the message text and, when present, a second `::` block for the recipient note. Every sender- or owner-typed value interpolated into either header line passes through `src/twicc/cli/_drop_request/sender_header.py::inline_md`, which flattens, truncates and escapes it. The message text and recipient note remain body content.

The **local end** of a message is `origin_session` on an outbound row and `delivered_to_session` on an inbound one. `PeerMessageReviewDialog.vue::localRoute` and `PeerInboxRow.vue` both render that rule.

### 2.6 Serialization and REST

`core.serializers.serialize_peer_message` emits the summary form by default and adds `payload` only when `include_payload=True`. It calls `core.serializers.peer_message_session_ref`, which returns `{id, title, project_id}` read live off the FK, and whose docstring requires async callers to have loaded the relation.

Callers: `peer_messages._serialize_for_broadcast` (both broadcasts), `src/twicc/peer/owner_views.py::peer_messages_list` (pending inbound first, then the rest capped by `limit`, default 50, max 200), `src/twicc/peer/owner_views.py::peer_message_detail` (via `_load_message`), `src/twicc/asgi.py::WSConsumer.connect` (via its `_peer_messages_snapshot` local helper), and `src/twicc/cli/peer_message.py::peer_message_cmd`.

For a resolved message that still has attachment bytes, `peer_purge_task.purge_expired_attachment_bytes` drops those bytes 7 days after `resolved_at`, keeps `text` and `attachments_meta`, and stamps `purged_at`.

### 2.7 Front

On WebSocket connect, `src/twicc/asgi.py::WSConsumer.connect` sends all pending inbound rows plus the 50 most recent other rows as `peer_messages_updated`. `frontend/src/stores/peers.js::applyMessages` replaces `messages` with that summary snapshot. `upsertMessage` then inserts or replaces rows on the `peer_message_received` / `peer_message_updated` broadcasts without trimming the store; a reconnect replaces it with a new snapshot.

`PeerMessageReviewDialog.vue` opens with `mode = null`. `setMode(next)` toggles the mode to `null` when clicked on the active one and resets `pickedProjectId`, `sessionFilter`, `scopeId` (to `defaultScopeId()`) and `selectedSessionId`. `sessionRows` builds on `computeSidebarSessionBlocks` and skips drafts and archived sessions. `selectableProjects` excludes archived and stale projects; `isSelectableProject` accepts a worktree whose main repository is listed. `deliverToSession` and `deliverToNewSession` call `markDelivered` then `prefillComposer`; the latter appends to any existing draft text and re-attaches the payload's blocks.

`dataStore.loadSessionById` returns the existing Pinia row without a request when present. Otherwise it fetches the existing owner route `/api/sessions/<session_id>/` and writes a successful full row into `dataStore.sessions`. A `404` supplies no reason for the missing row. The session-list getters can omit rows that the detail route can still return, including rows outside their pagination window.

`frontend/package.json` runs frontend tests through `node --test`. The repository has no Vue component-test harness.

`PeerInboxRow.vue` renders labelled lines in a fixed reading order: header (arrow, peer, tags, time), then the message title, then the quoted preview, then the routing line for the local end.

`frontend/src/composables/useWebSocket.js` builds the incoming-message toast with the title `Message from ${peerName}` and mounts `PeerToastContent.vue` in mode `message`.

### 2.8 Agent surface

`src/twicc/mcp/tools.py` derives the MCP tool list from the CLI's Click tree, so a new CLI option appears in the MCP schema with no further wiring. `src/twicc/rpc/permissions.py` lists `peers` and `peer-message` among the read-only commands.

Skills: `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md`, `twicc-peer-message/SKILL.md`, `twicc-peers/SKILL.md`. The bundle version lives in `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`.

---

## 3. Decisions

| # | Decision | Defined in | Verified by |
|---|---|---|---|
| D1 | The wire carries `reply_to` as a top-level field, never a thread identifier | §4 | §15.1, outbound-wire and inbound resolution cases |
| D2 | `message_id` and non-empty `reply_to` use the token grammar in §4; an unknown conforming `reply_to` becomes a root and a malformed identifier is a `400` | §4 | §15.1, identifier-validation and unknown-reply cases; §18, lot 1.1 boundary-vector closure |
| D3 | Resolution is always scoped to the peer, and prefers the direction opposite to the new message when both directions share an id | §6 | §15.1, direction tie-break and cross-peer isolation cases |
| D4 | `thread_id` is derived locally at row creation and never crosses the wire; the local thread key is `(peer_id, thread_id)` | §5 | §15.1, convergence and cross-peer isolation; §15.2, migration backfill |
| D5 | `reply_to_message` is resolved once, at row creation; its local-end session id is resolved at each serialization or detail read, then held as the open dialog's snapshot while the hydrated session row stays live | §7 | §15.1, `reply_target` cases; §16.2, M11–M15 |
| D6 | The serializer exposes `thread_id`, `reply_to`, `reply_to_ref` and the local-end session id as `reply_target`; existing session-reference serializers stay unchanged | §8 | §15.1, serializer-contract cases; §15.3, WebSocket snapshot |
| D7 | For a pending inbound message, the review dialog uses the by-id loader when normal candidate rows omit the target; a picker-eligible target seeds the normal session selection once during initialization, keeps picker order, and scrolls the seeded rendered row into view | §9 | §15.5, candidate-source, picker-eligibility and pagination-recovery cases; §16.1, M2–M4 and M9 |
| D8 | The component keeps no parallel pre-selection state machine; its rendered rows derive whether `selectedSessionId` is actionable, and mode changes preserve that normal selection | §9 | §16.2, M10–M14 |
| D9 | After hydration settles, a pending inbound row with non-empty `reply_to` gets one generic warning when its target is not picker-eligible; the warning gives no reason | §9 | §16.1, M4–M9 |
| D10 | The inbox keeps one row per message; a labelled line names the message answered | §10 | §16.3, M19 |
| D11 | The envelope states a conforming message id as a fact, omits a legacy unsafe id, and never invites the agent to reply | §11 | §15.1, envelope and legacy-id cases |
| D12 | `--reply-to` accepts any matching conforming message id of the target peer, in either direction and any status; §6 resolves a cross-direction id collision deterministically | §11 | §15.1, send-service cases; §15.4, CLI cases; §18, lot 1.1 boundary-vector closure |
| D13 | The human approval gate is untouched: no automatic delivery, no shortcut | §12 | §16.3, M20–M21 |

---

## 4. Wire format

`peer.outbound.post_message` gains one top-level field, mirroring `title`:

```json
{"message_id": "pm_…", "title": "…", "reply_to": "pm_…", "payload": {…}, "origin": {"sent_at": "…"}}
```

`reply_to` is the `message_id` of the message being answered. Absent, `null` or `""` means the message opens a thread.

New non-empty wire identifiers use one exact token grammar:

```
[A-Za-z0-9_][A-Za-z0-9_-]{0,39}
```

The grammar applies identically to `message_id` and non-empty `reply_to`. Every caller uses the compiled pattern's `fullmatch(value)` method. The pattern has no `^` or `$` anchors. This prevents Python's `$` behavior from accepting one final LF.

The length is 1 through 40 characters. The first character is an ASCII letter, an ASCII digit or underscore. Each later character can also be a hyphen. A hyphen cannot be the first character, while an underscore can. Dot and colon are invalid everywhere. All other characters, including non-ASCII characters and whitespace, are invalid.

Values are opaque, case-sensitive and never trimmed. The restricted alphabet excludes every URL dot-segment and every colon. It also excludes whitespace, backticks, backslashes, stars and brackets that cannot round-trip safely through the delivery header. Rejecting a leading hyphen avoids ambiguity with CLI option syntax. `core.services.peer_messages` owns the compiled pattern used by inbound validation, send-service validation, the CLI pre-check, the legacy-envelope check and the status-callback check. `mint_message_id` stays unchanged and produces a conforming `pm_` plus 16 hexadecimal characters.

Rows accepted before this grammar can contain unsafe ids. Examples include `.`, `..`, `a.b`, `a:b`, `-abc` and values containing a newline. Local delivery and refusal remain available for those rows. Their delivery envelope omits the unsafe handle. `_notify_status` skips the outbound status callback when the stored `message_id` does not conform. It must not interpolate a legacy unsafe id into a callback URL.

**Top-level, not inside `origin` (D1).** `origin` is a blob the receiver stores as provenance; `reply_to` is message metadata that gets its own column, resolved and indexed — the same argument the `PeerMessage.title` comment makes for `title`. `origin` keeps `{sent_at}` as its only key on the wire and on the row.

**No thread identifier on the wire (D1).** §5 derives it locally.

Validation in `receive_peer_message`, alongside the existing `title` and payload checks (D2):

| Field and received value | Outcome |
|---|---|
| `message_id` matching the token grammar | stored verbatim |
| `message_id` absent, non-string, empty or nonconforming | `400 invalid_payload`; no row stored |
| `reply_to` absent, `null` or `""` | root message; stored as `""` |
| non-empty `reply_to` matching the token grammar | stored verbatim; resolution attempted (§6) |
| `reply_to` non-string, or a non-empty nonconforming string | `400 invalid_payload`; no row stored |

A syntactically valid `reply_to` that resolves to no local row is **not** an error: the row is stored, `reply_to_message` stays null, and the message becomes a thread root. Both ends of this protocol are TwiCC, so a malformed value is a bug or an attack and deserves the `400` the other malformed fields get; an unknown-but-well-formed value is an ordinary consequence of two independent histories.

**The send path is stricter, on purpose.** An unresolvable `--reply-to` is rejected there (§11.2) rather than downgraded to a root. We own our own history: a value that names nothing local is a mistake by our own agent, caught while it can still be corrected. We do not own the peer's history, so the same value arriving from the wire is not evidence of anything wrong.

### 4.1 Version compatibility

There is no capability negotiation for `reply_to`.

- An older sender omits the field. A newer receiver stores a root.
- A newer sender includes the field. An older receiver ignores the unknown top-level key, returns its normal `202`, and stores a root.

The newer sender keeps its local relation in the second case. The instances can permanently disagree for a message first accepted by an older receiver. Upgrading that receiver does not reconstruct the discarded field. A replay with the same `message_id` remains an idempotent no-op against the stored root.

That divergence propagates to descendants. Suppose A stores M2 as a reply in thread M1, while old B stores M2 as root thread M2. After both upgrade, B can send M3 with `reply_to=M2`. Both instances resolve parent M2, but B derives thread M2 and A derives thread M1.

After both instances upgrade, a new root converges. A reply converges only when both instances resolve the same parent and already assign that parent the same `thread_id`. Descendants of a message stored as a root by an old receiver remain divergent along that branch. A later new root starts a new convergent branch.

## 5. Thread identity

`thread_id` is computed once, at row creation, on both the send and the receive path, in both directions (D4):

```
thread_id = reply_to_message.thread_id   when reply_to resolves (§6)
          = self.message_id              otherwise
```

**Nothing about it crosses the wire.** The two instances converge when they resolve the same parent and already assign that parent the same `thread_id`. In a fully upgraded exchange, the shared root `message_id` provides that common seed:

| Event | Row on A | Row on B |
|---|---|---|
| A sends M1 (no `reply_to`) | out, `thread_id = M1` | — |
| B receives M1 | — | in, `thread_id = M1` |
| B answers M2 (`reply_to = M1`) | — | out, resolves its in-row → `thread_id = M1` |
| A receives M2 | in, resolves its out-row → `thread_id = M1` | — |

The local thread key is `(peer_id, thread_id)`. Code that groups or looks up a thread must use both values. `message_id` values are scoped to a peer, so `thread_id` alone is not an instance-wide identity.

Carrying the identifier on the wire would add a value taken from the peer that no local check can validate: a peer could group its own messages arbitrarily in the receiver's inbox. Deriving it locally removes that input when both sides have the same parent thread identity.

An instance that deletes the peer and re-pairs CASCADEs its `PeerMessage` history away. A later reply resolves to nothing on that side and opens a new local thread, while the other side keeps the old one. The divergence changes only local grouping metadata: the key is never compared across instances, never travels, and is not displayed to a human in this scope.

## 6. Resolution rule

For a message being created on peer `P` with direction `D` and a non-empty `reply_to` value `V`:

```
candidates = PeerMessage.objects.filter(peer=P, message_id=V)
parent     = the candidate whose direction is the opposite of D,
             else the remaining candidate,
             else None
```

**Scoping to the peer is mandatory (D3).** Without it, one peer can name a `message_id` belonging to another relationship and steer §7's proposed target onto a session that has nothing to do with it.

**Why a tie is possible at all:** uniqueness is `(peer, direction, message_id)`, and the two instances mint ids independently through `mint_message_id`, so one inbound and one outbound row of the same peer may in principle carry the same id. Preferring the opposite direction encodes the common meaning: a reply answers what the other side sent.

The same rule runs on the send path (an agent answering a message it received) and on the receive path (a peer answering ours). A reply to one of our own rows in the same direction is a follow-up on our own message, and resolves through the second branch.

The tie-break cannot identify a caller's intended row when both directions carry the same id. `--reply-to` carries no direction discriminator. The opposite-direction row wins even when the caller intended the same-direction row. Two unrelated roots with that collision also share `(peer_id, thread_id)`, so a later grouped inbox would merge them. Accidental occurrence requires a collision between independently minted 64-bit random values within one peer relationship; a peer can also reuse a known id deliberately. The result can change relationship-local grouping, parent and proposed session, but cannot cross the peer scope or bypass the human delivery gate.

## 7. Resolution timing

Two different lifetimes, deliberately separated (D5).

**`reply_to_message` — resolved once, at row creation.** Which message answers which is a fact fixed at the moment the message exists. Storing it as an FK keeps `thread_id` derivable and lets the serializer read the parent without a lookup by string.

**The proposed target session id — resolved at every serialization or detail read, never stored.** It is the parent's local end:

```
reply_target = parent.origin_session_id       when parent.direction == "out"
             = parent.delivered_to_session_id when parent.direction == "in"
```

Two states make an early snapshot wrong:

- `delivered_to_session` starts null on the "deliver to a new session" path and is filled later by `link_delivered_session`. A reply that arrives before the draft is sent has no target at that instant and a valid one minutes later. Reading at open time picks it up; a stored snapshot would not.
- A redelivery can move the recorded target (`mark_delivered(redeliver=True)`). The latest read is the only correct one.

`PeerMessageReviewDialog.vue` keeps the detail response as a snapshot while it is open. It resolves that id against live project and session data (§9.4). A changed parent link or status appears after close and reopen.

## 8. Data model and serialization

### 8.1 Columns on `PeerMessage`

| Field | Type | Notes |
|---|---|---|
| `reply_to` | `CharField(max_length=40, blank=True, default="")` | the wire value, verbatim, even when it resolves to nothing |
| `reply_to_message` | `ForeignKey("self", null=True, blank=True, on_delete=SET_NULL, related_name="replies")` | the resolved local row (§6) |
| `thread_id` | `CharField(max_length=40)` | derived locally (§5), always set; used with `peer_id` as the local thread key |

`SET_NULL` and not `CASCADE`: a message whose parent disappears stays a message. Both live under the same `peer` FK, whose CASCADE already removes the whole history at once when the relationship is revoked, so the self-FK only ever fires on a row deleted alone — which nothing in the codebase does today.

`PeerMessage.Meta.indexes` gains a non-unique composite index on `("peer", "thread_id")`. It supports the local thread key without implying that `thread_id` is unique across peer relationships.

### 8.2 Migration

Migration `0134` (next free number after `0133_share_created_by_session`) has six operations:

1. `AddField` for `reply_to`.
2. `AddField` for `reply_to_message`.
3. `AddField` for `thread_id`, with a temporary `default=""`.
4. `RunPython` to backfill `thread_id`.
5. `AlterField` to drop the temporary `thread_id` default.
6. `AddIndex` for `("peer", "thread_id")`.

`reply_to` keeps its `""` default and `reply_to_message` keeps `NULL`, so neither needs a data pass. Every pre-existing row becomes a root. Rows with distinct ids become one-message threads. Opposite-direction rows with the same peer and `message_id` share one `(peer_id, thread_id)` key under §6's collision ambiguity.

The migration-local `RunPython` function gets the historical model through `apps.get_model("core", "PeerMessage")`. It performs one SQL update with `F("message_id")` and has a no-op reverse. It imports no application model or service code.

### 8.3 Serializer contract

`core.serializers.peer_message_session_ref` stays unchanged.

`core.serializers.serialize_peer_message` gains four keys:

| Key | Value |
|---|---|
| `thread_id` | the column |
| `reply_to` | the column (`""` when the message is a root) |
| `reply_to_ref` | `{message_id, title, direction, status}` from `reply_to_message`, or `null` |
| `reply_target` | the parent's local-end session id (§7), or `null` |

`reply_target` carries no title, project or eligibility claim. When normal candidate rows omit the target, the client uses `dataStore.loadSessionById` to obtain the owner-local session row by id. The current Pinia row, whether already present or returned by that fallback, supplies the live inputs for picker eligibility. The id and loaded session stay on authenticated owner routes and never cross the peer wire.

**Relations to load.** These async serialization query sites add `reply_to_message` to `select_related`:

- `peer_messages._serialize_for_broadcast`;
- `src/twicc/peer/owner_views.py::peer_messages_list`;
- `src/twicc/peer/owner_views.py::_load_message` for `peer_message_detail`;
- `src/twicc/asgi.py::WSConsumer.connect`, in its `_peer_messages_snapshot` local helper.

`peer_messages._fresh_message` adds the same path because `mark_delivered` reads the parent while building the envelope. `src/twicc/cli/peer_message.py::peer_message_cmd` is synchronous and may lazy-load, but adds the path to keep one query.

## 9. Receiving flow — the review dialog

Nothing in the approval gate changes: the human opens `PeerMessageReviewDialog.vue`, reads the whole message, and clicks (D13). The toast never delivers.

### 9.1 Direction and status gate

D7–D9 apply only to a pending inbound message:

| Opened row | Delivery controls | Initial mode | Reply-target behaviour |
|---|---|---|---|
| inbound, `pending` | deliver or refuse | `'existing'` for a picker-eligible hydrated target; otherwise `null` | one selection seed when the target is picker-eligible; otherwise one generic warning when `reply_to` is non-empty; a root gets neither |
| inbound, `delivered` | redeliver | `null` | no pre-selection and no generic warning; redelivery starts from a deliberate human choice |
| inbound, `refused` | read-only | `null` | relation line only (§9.6) |
| inbound, `failed` | read-only | `null` | relation line only (§9.6); defensive display for a combination supported services do not create |
| outbound, any status | read-only | `null` | relation line only (§9.6) |

A delivered row can already point at a session different from its parent's local end. Redelivery therefore never derives a default from `reply_target`.

### 9.2 Hydration and request lifetime

**Picker eligibility** asks one question. With existing mode open at the target's project scope, an empty text filter and no page bound, would `PeerMessageReviewDialog.vue::sessionRows` produce the hydrated row? The implementation shares `sessionRows`' exact non-pagination row predicate with `frontend/src/utils/peerReplyTarget.js`. It adds no project-list predicate. In particular, it does not call `isSelectableProject` or require membership in `getListableProjects`. A worktree row or stale-project row remains picker-eligible when `sessionRows` produces it.

An **eligibility override** would admit a row that fails picker eligibility. This design has no eligibility override. **Pagination recovery** is a fallback candidate insertion. It adds a hydrated, picker-eligible target that the normal getter omitted solely because of its current page bound. It changes candidate membership and never changes picker eligibility.

`frontend/src/utils/peerReplyTarget.js::chooseReplyTargetSource` is a pure candidate-or-load decision. Given a target id and normal unfiltered candidates, it returns `{kind: "candidate", session}` when one candidate has that id. Otherwise it returns `{kind: "load", sessionId}`. It performs no lookup and stores no state.

After the detail request completes, initialization follows this order for a pending inbound row:

1. Read `reply_target` as the target id. When it is null, perform no session lookup and mark target hydration settled synchronously.
2. Read the current Pinia row through `dataStore.getSession(reply_target)`. When that row exists, use its `project_id` to build the normal unfiltered candidates. When it does not exist, the candidate input is empty because the project is not known yet.
3. Pass the target id and those candidates to `chooseReplyTargetSource`.
4. When it returns `kind: "candidate"`, use its session as the resolved target row and mark target hydration settled. Do not call `loadSessionById` or pagination recovery.
5. When it returns `kind: "load"`, await `dataStore.loadSessionById(sessionId)`. The returned row supplies `project_id`. When that row is picker-eligible, build its normal unfiltered candidates. If only the page bound omits it, pagination recovery inserts it once at the normal sorted position. Then mark target hydration settled with that resolved row.
6. On a `404`, another unsuccessful response, a request error or a returned row that is not picker-eligible, keep no candidate row and continue with the generic §9.4 result.

These steps do not write `scopeId`, `selectedSessionId` or `mode`. Section 9.3 writes them only after this sequence yields a picker-eligible resolved row and a candidate list that contains it.

This feature changes no owner session route, lookup or resolver. The existing `views.session_by_id` behavior remains unchanged. The client does not infer why a lookup failed.

The generic warning stays absent while this initial target lookup is in flight. One boolean hydration-pending value is ordinary request state. It carries no target identity, reason, eligibility or suggestion provenance.

The reused dialog owns an integer open generation. Its watcher increments the generation synchronously on every `[props.open, props.messageId]` change, including close, before any early return. Initialization captures that generation and message id. Detail, Markdown and hydration results check all three conditions before writing component state: the generation still matches, the dialog remains open, and `props.messageId` still matches. Seed and post-render scroll callbacks use the same guard. This request-lifetime state is not suggestion, dismissal, eligibility or mode-provenance state.

`PeerMessageReviewDialog.vue` factors its row construction into a local candidate builder with an explicit project scope and text filter. Initialization calls that builder with the resolved Pinia row's `project_id` and an empty filter, without reading or writing `scopeId`. The rendered `sessionRows` calls the same builder with the live component inputs.

The candidate builder starts from `computeSidebarSessionBlocks(...).natural` and applies the shared picker-eligibility predicate. Pagination recovery adds the hydrated target only when it is picker-eligible and the page bound omitted it from the explicit project scope. The component sorts the candidate array with the existing session comparator, applies the supplied text filter, then adds section separators. The target keeps its normal position. This feature does not change `computeSidebarSessionBlocks`.

### 9.3 One seed, then normal selection

The dialog computes one seed after the initial §9.2 hydration. A later store change cannot seed. A target can seed the selection when all these conditions hold:

- the row is inbound and `pending`;
- `reply_target` is non-null;
- the unfiltered picker candidate rows contain the hydrated live session, either normally or after pagination recovery.

Pagination recovery is not a seed precondition. For an already-present normal candidate, `chooseReplyTargetSource` returns `kind: "candidate"`, so initialization seeds without a by-id load or candidate insertion.

Only after §9.2 yields a usable resolved row does initialization set `scopeId` from that row's `project_id`, then set `selectedSessionId = reply_target` and `mode = 'existing'`. The target id is stored only in the component's existing `selectedSessionId`. There is no `suggestedSessionId`, dismissal flag, mode-provenance flag or pending-scroll flag.

For a target that is not picker-eligible, a failed hydration, a root or an unresolved reply, initialization leaves `mode = null` and `selectedSessionId = null`. A non-empty `reply_to` still triggers the generic warning for the unresolved case (§9.4). A later store or eligibility change does not run the seed step again. A row click writes its id to the same `selectedSessionId`, replacing the seed permanently.

`setMode(next)` keeps its existing toggle. It keeps resetting `pickedProjectId` and `sessionFilter`. It stops resetting `scopeId` and `selectedSessionId`. The seed or the human's later choice therefore survives both mode switches without origin-specific rules.

### 9.4 Live derivations (D8, D9)

The detail response remains a snapshot while the dialog is open. Parent-row mutations appear only after close and reopen. Projects, sessions, scope, filter and mode remain live Pinia and component inputs.

The component keeps its existing derivation:

```
selectedSession = sessionRows.find(row => row.session.id === selectedSessionId)?.session ?? null
```

`sessionRows` contains only rows rendered in the existing-session picker. It applies the current scope, current text filter, live session fields and pagination recovery. It is empty when the picker is unmounted. The "Prefill session composer" button stays disabled when `selectedSession` is null.

These properties follow without transition rules:

- A text filter can hide the selected row. The id stays in `selectedSessionId`, but the row loses its highlight and delivery is disabled.
- Clearing the filter can make the same id actionable again.
- A scope change can exclude the selected row. Returning to its scope can make it actionable again.
- Archiving the target removes the actionable selection.
- Removing the target from Pinia removes the actionable selection and makes the generic warning apply. Initialization does not hydrate it again.
- A row click overwrites `selectedSessionId`. The original seed cannot return after that overwrite.
- A mode switch unmounts or remounts the picker. `setMode` preserves the id and scope, so the rendered rows decide the result after remount.

`frontend/src/utils/peerReplyTarget.js` contains only three pure derivations: the candidate-or-load decision, picker eligibility for the hydrated target and pagination recovery. It contains no state transition helper.

`PeerMessageReviewDialog.vue` derives `replyTargetSession` from `dataStore.getSession(detail.reply_target)`. It derives `replyTargetPickerEligible` from that live row and the shared `sessionRows` predicate. It stores neither value.

After initial hydration settles, this one condition renders one `<wa-callout variant="warning" size="small">`:

```
isPending && targetHydrationSettled && detail.reply_to !== "" && !replyTargetPickerEligible
```

> This message is part of a thread, but its session is not available for selection. Choose another session, or deliver to a new one.

The callout does not state or infer a reason. Every failed lookup, failed picker-eligibility check and unresolved non-empty `reply_to` produces the same wording. A root has empty `reply_to` and produces no callout. The text filter and current mode do not change picker eligibility or this callout; they only change rendered rows and the actionable `selectedSession`.

### 9.5 Rendering and scroll

Initialization opens the picker-eligible target's project scope before the picker renders. A Vue post-render hook captures the open generation and seeded target id, then waits for the normal render tick. It scrolls only when the generation is still current, `selectedSessionId` still equals that id, existing mode remains open, and the row exists inside `.pr-picker`. It calls `scrollIntoView({block: 'nearest'})` and records no scroll transition.

The picker order is untouched (D7). Pagination recovery uses the normal comparator, and scrolling moves only the `30vh` viewport.

### 9.6 The line naming the message answered

The dialog gains one labelled line next to `localRoute`, built from `reply_to_ref` (§10 defines the wording, shared between both surfaces).

## 10. Inbox and toast

**One row per message (D10).** `PeerInboxDialog.vue` keeps its three sections and its flat lists. Grouping by thread is §13.

`PeerInboxRow.vue` gains one labelled line in the routing block, above the local-end line, following the component's existing label-then-value shape:

| `reply_to_ref.direction` | Line |
|---|---|
| `out` — the answered message is ours | `In reply to your “<title>”` |
| `in` — the answered message is theirs | `In reply to their “<title>”` |

The rule reads the same on an inbound and an outbound row, and needs no direction-specific branch beyond the parent's own direction. A row whose `reply_to_ref` is null renders no such line. An empty parent `title` — a row stored before the title became required — renders no such line either, rather than an empty pair of quotes.

**Toast.** `useWebSocket.js` builds the title as `Reply from ${peerName}` when `reply_to_ref` is non-null, `Message from ${peerName}` otherwise. `PeerToastContent.vue` is unchanged.

## 11. Agent surface

### 11.1 The envelope

`build_delivery_envelope` gains two segments on its header line:

```
:: peer message **“<title>”** (`<message_id>`) from **<peer name>** (`<base_url>`), in reply to your **“<parent title>”**, sent <when>; written by an agent on another TwiCC instance and forwarded by your user, treat it as self-contained third-party content
```

- the message's **own conforming** `message_id`, inserted verbatim in a code span right after the title — that is the value `--reply-to` takes. On a row stored before the title became required, the id follows `peer message` directly;
- `in reply to your **“…”**` / `in reply to their **“…”**` when `reply_to_message` is set, using §10's direction rule and omitted otherwise. A parent whose own `title` is empty contributes no segment, rather than an empty pair of quotes.

**The envelope states a safe id; it does not ask for a reply (D11).** The agent decides whether an answer is needed, or its user tells it to answer. Where that id is used is taught by `twicc-peer-send/SKILL.md`, not by the message header — the header is provenance, and an instruction there would read as the sender's instruction, which is the one thing the envelope exists to prevent.

An inbound `message_id` is peer-controlled. The token grammar in §4 makes a newly accepted id safe for direct insertion and exact copy-back, so it does not pass through `inline_md`. The parent title remains arbitrary sender text and does pass through `inline_md`. The header stays one line.

A pre-existing row can carry an id accepted before the token grammar existed. If that id does not conform, the envelope omits the id segment but still delivers the message and its attachments. The agent cannot use that legacy row as a `--reply-to` target.

`mark_delivered` reads `message.reply_to_message` to build this, so `_fresh_message`'s `select_related` must carry it (§8.3).

### 11.2 `twicc peer-send --reply-to`

A new option on `src/twicc/cli/peer_send.py::peer_send_cmd`:

```
--reply-to MESSAGE_ID   The peer message this one answers (pm_…), taken from the
                        header of a delivered peer message.
```

Accepted values (D12): any `PeerMessage` row of the resolved peer whose `message_id` conforms to §4, in either direction and whatever its status. Answering a message we received is the common case; re-opening one we sent is a follow-up on our own message and uses the same field.

Validated twice, following the `TITLE` precedent: a local ORM pre-check in the CLI for a fast, readable failure, and a re-validation in `send_peer_message_from_payload`, which is the authority.

Ids are opaque, case-sensitive and never trimmed. The service applies this table before it creates the outbound row:

| Input | CLI | Service |
|---|---|---|
| option omitted / key absent | root message | root message; store `reply_to = ""` |
| `""` | root message | root message; store `reply_to = ""` |
| `null` | not representable by the CLI option | root message; store `reply_to = ""` |
| conforming token with a match for this peer | accepted | resolve by §6 and derive `thread_id` from that row |
| conforming token without a match for this peer | `unknown_reply_to`, exit 1 | `PeerError("reply_to", "unknown_reply_to", …)`, `rejected` / exit 3 |
| nonconforming string, including over 40 characters | `invalid_reply_to`, exit 1 | `PeerError("reply_to", "invalid_reply_to", …)`, `rejected` / exit 3 |
| other non-string | not representable by the CLI option | `PeerError("reply_to", "invalid_reply_to", …)`, `rejected` / exit 3 |

A `--reply-to` naming a row of a *different* peer is not a distinct case: scoped by peer, it simply does not resolve, and reports `unknown_reply_to`.

A nonconforming legacy id fails with `invalid_reply_to` before lookup. It is not re-encoded or normalized into a different handle.

When one inbound and one outbound row share the id, both checks accept the id and §6 selects the opposite-direction row. The option cannot select the other row in that collision state.

An outbound row with status `failed` has no confirmed `202`; that status does not prove that the peer failed to store it. A request can reach the peer before the local client loses the response. The row remains an allowed target because the local relation is valid. If the peer stored the parent, it resolves the reply; otherwise it stores the reply as a root. This status therefore does not promise cross-instance relation convergence.

### 11.3 `twicc peer-message` and MCP

`src/twicc/cli/peer_message.py` emits `serialize_peer_message`, so `thread_id`, `reply_to`, `reply_to_ref` and `reply_target` appear in its output with no code change.

Its command description changes the meaning of `failed` from "the send never reached the peer" to "the sender received no confirmed acceptance; the peer may still have stored the message." The same wording is used in `twicc-peer-message/SKILL.md`.

`src/twicc/mcp/tools.py` derives tools from the Click tree, so `mcp__twicc__peer_send` exposes `--reply-to` with no code change.

## 12. Security

- **Peer-scoped resolution** (§6) is the boundary. It is a filter argument on every lookup, not a check that can be forgotten in one branch: the helper takes the peer and builds the queryset itself.
- **`reply_to` never delivers anything (D13).** It changes a default in a picker. The receiving human still reads the whole message and clicks, which is the prompt-injection boundary the 2026-07-24 design §4.4 defines.
- **No new peer endpoint or peer authentication path.** The field rides the existing `POST /peer/messages/`, behind the existing Bearer token.
- **Reply-target access stays on the owner side.** The serializer exposes only the local-end session id. When the fallback calls `dataStore.loadSessionById`, the loader returns a cached Pinia row when present. A cache miss uses the existing authenticated `/api/` route. The id and any fetched session response never cross the peer wire. This feature changes no owner session route.
- **Thread grouping stays inside one relationship** because its key is `(peer_id, thread_id)` (§5). A sender-controlled root id can equal an id under another peer, but the peer component keeps the keys distinct.
- **The parent title is escaped** by `inline_md`. A hostile title cannot break out of the header line.
- **Incoming message ids are peer-controlled.** The accepted ASCII letter, digit, underscore and non-leading-hyphen grammar keeps them safe for verbatim code-span display. Locally minted ids encode 8 random bytes and conform to the same grammar.
- **Dots, colons and leading hyphens are invalid.** `peer.outbound.post_status` inserts `message_id` into a path. Rejecting every dot prevents `httpx` from normalizing a dot-segment callback to another route. Rejecting a leading hyphen also avoids CLI option ambiguity.
- **Legacy unsafe ids never enter envelopes or callback paths.** For `.`, `..`, `a.b`, `a:b`, `-abc`, newline-bearing values and any other nonconforming stored id, the envelope omits the handle and `_notify_status` skips the best-effort callback. The local human can still deliver or refuse the message.

## 13. Out of scope

- **Grouping the inbox by thread.** The user chose one row per message to start. The `(peer_id, thread_id)` columns and index avoid another schema migration. A later design must still define list-API pagination and make every grouping or lookup use both key parts.
- **A session-level surface for peer messages** — showing, inside a session, the messages it sent or received and their statuses. Useful and independent; `origin_session` and `delivered_to_session` already carry the data.
- **Any change to the approval gate**: no delivery from the toast, no automatic routing of a reply, no reduced reading.
- **Reordering the delivery picker** to surface the pre-selected session.
- **A CHANGELOG entry**: this project adds them only when the user asks.

## 14. Edge cases

| Case | Behaviour |
|---|---|
| Reply to a purged parent | The parent's text and `attachments_meta` survive the purge, so `reply_to_ref` and `reply_target` work unchanged. |
| Reply to a refused inbound parent | Allowed. Its `delivered_to_session` is null, so a pending child gets the generic warning (§9.4). |
| Reply to a refused outbound parent | Allowed. Its `origin_session` remains the proposed target when present; otherwise a pending child gets the generic warning (§9.4). |
| Reply to a `failed` outbound row | Allowed. The peer may have stored the parent despite the missing confirmation; it either resolves the reply or stores it as a root (§11.2). |
| Redelivery after a reply arrived | `reply_target` is read at open time (§7), but redelivery starts with `mode = null` and does not use it as a default (§9.1). |
| Picker-eligible target is already in the normal candidates | `chooseReplyTargetSource` returns the candidate. Initialization seeds it without the by-id loader or pagination recovery (§9.2–§9.3). |
| Picker-eligible target is omitted only by the page bound | The by-id loader supplies the cached or fetched row. Pagination recovery inserts it at its normal sorted position, and initialization seeds it (§9.2–§9.3). |
| `sessionRows` produces a worktree or stale-project target | The target remains picker-eligible. No separate project-list rule rejects it (§9.2). |
| Target lookup fails | The dialog shows the generic warning, leaves the picker unselected and infers no reason (§9.2–§9.4). |
| Target resolves but fails picker eligibility | The same generic warning appears. The dialog leaves the picker unselected and does not state which eligibility check failed (§9.3–§9.4). |
| Non-empty `reply_to` does not resolve to a parent | `reply_target` is null. The pending inbound dialog shows the same generic warning and performs no pre-selection (§9.3–§9.4). |
| Seeded target leaves the store or fails picker eligibility while open | `selectedSession` becomes null, delivery is disabled and the generic warning appears. The preserved id cannot deliver (§9.4). |
| Seeded session moves out of scope or is filtered out | `selectedSession` becomes null because the row is not rendered. Delivery is disabled. The generic warning does not change because scope and text filters are not eligibility (§9.4). |
| The same seeded row becomes rendered again | The preserved id becomes actionable again unless a row click overwrote it. No suggestion restoration runs (§9.4). |
| Target is not picker-eligible during initialization, then becomes picker-eligible | Initialization does not run again. The warning clears, but the dialog does not auto-select it (§9.3–§9.4). |
| Dialog closes during detail, Markdown or hydration | The close increments the open generation. No stale result writes state or scrolls (§9.2, §9.5). |
| Dialog switches from message A to message B during initialization | A's generation becomes stale. Its detail, Markdown, target, callout, seed and scroll cannot affect B (§9.2, §9.5). |
| One session, several threads | Supported: nothing ties a thread to a session. |
| One thread, several sessions | Supported: the human may route any reply anywhere. |
| Peer revoked and re-paired | CASCADE removes the history. A later non-empty `reply_to` resolves to nothing, opens a fresh local thread and triggers the generic warning (§5, §9.4). |
| Replayed inbound message | `receive_peer_message` short-circuits on the existing row before any thread computation. |
| Reply replayed after an old receiver stored it as a root | The existing row stays a root after upgrade; replay does not reconstruct `reply_to` (§4.1). |
| Reply descends from a row stored as a root by an old receiver | Each side inherits its local parent's different `thread_id`. The branch remains divergent after both upgrade (§4.1). |
| Same id in both directions for one peer | The opposite-direction candidate wins. The id-only option cannot name the other row. Two unrelated roots also share a local thread key (§6). |
| Same `thread_id` under two peers | The local keys remain distinct because `peer_id` is the other key component (§5). |
| Old sender, new receiver | The absent field creates a root (§4.1). |
| New sender, old receiver | The old receiver ignores `reply_to`, returns its normal `202`, and stores a root that remains a root after upgrade (§4.1). |
| Pre-existing inbound row with a nonconforming `message_id`, including `.`, `..`, `a.b`, `a:b`, `-abc` or any newline-bearing value | Delivery remains available, but the envelope omits the unsafe reply handle and `--reply-to` rejects it (§4, §11). |
| Delivery or refusal of a legacy row with a nonconforming id | The local resolution succeeds. `_notify_status` skips the unsafe callback, so the remote row can remain pending (§4, §12). |
| Inbound row with status `failed` | Supported services do not create it; the dialog renders it read-only as a defensive case (§9.1). |
| Two replies to the same parent, concurrently | Each computes its own `thread_id` from the parent; no shared mutable state. |
| Clock skew between the instances | Ordering uses `created_at` (local), through the model's existing `Meta.ordering`; `origin.sent_at` is display only. |

## 15. Automated tests

### 15.1 Backend tests

Extend `tests/test_peer_messages.py`:

- inbound root and resolved-reply cases: absent, `null` and `""` `reply_to` store a root; a conforming reply resolves our outbound row or their inbound follow-up and inherits its `thread_id`;
- identifier validation: run the accepted tokens `A`, `1abc`, `_abc`, `a-b`, `abc-` and `"x" * 40` independently through inbound `message_id` and non-empty inbound `reply_to`; every value round-trips byte-for-byte through both fields. The rejected strings leading hyphen `-abc`, dot `.`, standalone colon `:`, `..`, internal dot `a.b`, internal colon `a:b`, `"A\n"`, an internal newline, leading or trailing whitespace, backslash, backtick, `*`, `[` or `]` and `"x" * 41` return `400` without a row for both fields. Non-string `message_id` values and non-null non-string `reply_to` values also return `400` without a row;
- unknown reply: a conforming unknown inbound `reply_to` stores a root, while the send service returns `unknown_reply_to`;
- outbound wire: root and reply sends include the normalized `reply_to` value in `peer.outbound.post_message` and never send `thread_id`;
- direction tie-break: an inbound and outbound row sharing a `message_id` selects the opposite direction; the same-direction row cannot be selected by id; two roots with that id share a local thread key;
- cross-peer isolation: the same `message_id` and `thread_id` under two peers resolves within the peer, and the `(peer_id, thread_id)` keys differ;
- convergence: a three-message exchange leaves one `thread_id` on its rows when both receivers support `reply_to` and agree on the parent's thread identity;
- send-service validation: absent, `null` and `""` mean root; conforming `A`, `1abc`, `_abc`, `a-b`, `abc-` and `"x" * 40` resolve or return `unknown_reply_to`; `-abc`, `.`, standalone colon `:`, `..`, `a.b`, `a:b`, whitespace or newline-bearing values, backslash, backtick, `*`, `[` or `]`, `"x" * 41` and non-string values return `invalid_reply_to`; no value is trimmed;
- send-service scope and status: an id under another peer returns `unknown_reply_to`; a conforming `failed` outbound row remains an accepted target;
- serializer contract: summary and detail output include `thread_id`, `reply_to`, `reply_to_ref` and `reply_target`; existing session-reference shapes stay unchanged;
- `reply_target`: parent direction selects the id of `origin_session` or `delivered_to_session`; a parent without a local end yields null;
- envelope: every conforming accepted id appears byte-for-byte in its code span; `in reply to your` / `in reply to their` follows parent direction; arbitrary parent-title Markdown and newlines stay inside the header line;
- legacy envelope: pre-existing nonconforming message ids, including `.`, `..`, `a.b`, `a:b`, `-abc` and newline-bearing values, are omitted from the header while delivery text and attachments remain available;
- legacy status callback: delivery and refusal succeed for legacy `.`, `..`, `a.b`, `a:b`, `-abc` and newline-bearing ids, but `_notify_status` does not call `peer.outbound.post_status`;
- purge leaves `reply_to`, `reply_to_message` and `thread_id` untouched;
- version compatibility: a request without `reply_to` creates a root; an unknown top-level key is ignored; replaying an existing inbound root with the same `message_id` plus `reply_to` leaves the original row unchanged;
- descendant divergence: A stores M2 under thread M1 while old B stores M2 as root M2; after both upgrade, B sends M3 with `reply_to=M2`; A derives M1 and B derives M2.

### 15.2 Create a migration test

Create `tests/test_peer_threading_migration.py`. Use `MigrationExecutor` to migrate to `0133_share_created_by_session`. Create one peer, at least two historical rows with distinct ids, and one inbound/outbound pair sharing an id. Then migrate to `0134`. Assert for every row:

- `thread_id == message_id`;
- `reply_to == ""`;
- `reply_to_message_id is None`;
- rows with distinct ids have distinct `(peer_id, thread_id)` keys;
- the opposite-direction collision pair has empty `reply_to`, null parents and the same `(peer_id, thread_id)` key.

### 15.3 Create the WebSocket snapshot test

Create `tests/test_peer_updates_consumer.py`. Connect `WSConsumer` with a resolved reply present. Assert that `peer_messages_updated` includes all four threading keys and the local-end id without an async relation lazy-load or `SynchronousOnlyOperation`.

### 15.4 Existing CLI tests

Extend `tests/test_peer_cli.py` for `peer-send`:

- unknown id for this peer and an id under another peer: exit 1, `unknown_reply_to`;
- omitted and empty `--reply-to`: root message;
- conforming `A`, `1abc`, `_abc`, `a-b`, `abc-` and `"x" * 40`: payload reaches the transport unchanged;
- leading hyphen `-abc`, dot `.`, standalone colon `:`, `..`, internal dot `a.b`, internal colon `a:b`, whitespace or newline-bearing values, backslash, backtick, `*`, `[` or `]`, and `"x" * 41`: exit 1, `invalid_reply_to`;
- a nonconforming legacy id: exit 1, `invalid_reply_to` before lookup.

### 15.5 Create the frontend helper tests

Create `frontend/src/utils/peerReplyTarget.test.js` for the pure derivations through `node:test`:

- candidate source: `chooseReplyTargetSource` returns the exact existing candidate and no load instruction when the target is present; it returns the target id as a load instruction when absent. This catches an unconditional loader decision because the existing-candidate result cannot request `loadSessionById`;
- picker eligibility: the helper gives the same result as the unpaged, unfiltered `sessionRows` predicate. Worktree and stale-project rows pass when that predicate includes them. An absent row and every row that predicate omits fail. `isSelectableProject` is never an input;
- pagination recovery: an eligible hydrated target below the page bound is inserted once at its normal sorted position. An existing or ineligible target leaves the candidate array unchanged.

## 16. Manual acceptance checklist — no runner

The frontend runner cannot mount Vue components (§2.7). Perform these checks in the running UI. This section is not an automated test group. Each entry identifies its starting state, the steps that reach it, the action, the correct observation and the specific wrong implementation it must expose. A check passes only when the correct observation occurs and no wrong implementation signal occurs. When the two outcomes are not observably different, this section marks the guarantee unverified instead of defining a check.

### 16.1 Reply-target initialization and warning

| ID | Starting state and how to reach it | Action | Correct observation | Wrong implementation signal |
|---|---|---|---|---|
| M2 | In development data, create pending replies to an already-present worktree row and an already-present stale-project row that `sessionRows` produces. | Open each review dialog. | Each target is highlighted and actionable without a generic warning. | Either row is rejected by an extra project-list check, stays unselected, or shows the warning. |
| M3 | Create enough sessions to place a pending reply's picker-eligible target outside the current page. Reload so that target is absent from Pinia. | Clear the browser Network log. Open the review dialog. | One by-id session request occurs. The target appears once at its normal sorted position. It is highlighted, scrolled into view and actionable. | No request occurs, the target is absent or duplicated, its order changes, or selection, actionability or scrolling fails. |
| M4 | Use M3's page-omitted, Pinia-absent target. Set browser Network throttling to Slow 3G. | Open the dialog. Confirm that the by-id request is pending. Then remove throttling. | No generic warning appears while the request is pending. The target becomes selected after the successful response. | The warning flashes while the request is pending, or the successful response does not select the target. |
| M5 | In development data, create a pending reply whose target is absent from Pinia and normal candidates. Block its exact by-id URL in the browser. | Open the dialog. Wait for the blocked request to settle. | The generic warning appears. No row is selected. The warning gives no failure reason. | A row is selected, the warning is absent, or the warning states a reason. |
| M6 | Load a visible target into Pinia. Close the dialog. Archive the target from client B before opening its pending reply in client A. | Open the review dialog in client A. | The generic warning appears. Existing mode stays closed and no row is selected. | Existing mode opens, a row is selected, or the warning is absent or reason-specific. |
| M7 | In development data, create a pending row with non-empty `reply_to` and null `reply_target`. | Clear the browser Network log. Open the review dialog. | The generic warning appears without a by-id session request. No row is selected. | A session request occurs, a row is selected, or the warning is absent or reason-specific. |
| M8 | In development data, create a pending root with empty `reply_to`. | Clear the browser Network log. Open the review dialog. | No generic warning, target seed or by-id request occurs. | A warning appears, a row is selected, or a target request occurs. |
| M9 | In development data, create two delivered inbound replies: one with a picker-eligible target and one with an unavailable target. Also create refused inbound, `failed` inbound and outbound rows. | Open each row in turn. | Both delivered replies show redelivery controls, with null mode, no selected row and no generic warning. The other rows remain read-only. | A delivered reply is pre-selected, warned or read-only, or any other row exposes delivery controls. |

### 16.2 Selection reactivity and request lifetime

| ID | Starting state and how to reach it | Action | Correct observation | Wrong implementation signal |
|---|---|---|---|---|
| M10 | Open a pending reply whose seed is visible in existing mode. Enter a non-empty filter that keeps it visible. Record the scope and selected id. | Switch to new mode and select a non-default project. Return to existing mode and inspect its filter. Return to new mode and inspect its project. Toggle active new mode off and on. Switch to existing mode and toggle it off and on. | The filter is empty when existing mode returns. The project choice is empty when new mode returns. The recorded scope and selected id survive every switch and toggle. Final existing mode highlights the same target. | Either mode-specific control retains its value, either preserved field changes, or the final existing mode does not highlight the target. |
| M11 | Open a pending reply whose seeded target is visible and has no generic warning. | Filter out the row, clear the filter, change scope, then restore the scope. | Delivery disables whenever the row is absent. It re-enables when the row returns. The generic warning stays absent. | Delivery stays enabled while the row is not rendered, does not re-enable when restored, or the filter or scope changes the warning. |
| M12 | Open a pending reply with a seeded target in client A. Open the same session controls in client B. | Archive the target in client B, then unarchive it. | Client A shows the warning and disables delivery after archive. After unarchive, the warning clears and the preserved selected id becomes actionable. | The archived row remains actionable, the warning does not track eligibility, or unarchive loses the preserved selection. |
| M13 | Load a visible target into Pinia in client A and close the dialog. Archive it in client B. Then open its pending reply in client A. | Unarchive the target in client B while the reply stays open. | The warning clears. Mode remains null and no row becomes selected. Initialization does not run again. | The warning remains, or the live change runs a late seed and opens existing mode. |
| M14 | Open a pending reply whose seed selects row A while row B is also visible. | Click B. Then hide and restore A with the text filter. | B remains selected and actionable. Restoring A never restores A's selection. | A becomes selected again or B loses actionability after A returns. |
| M15 | In development data, give a resolved reply old and new parents with visibly different titles and local ends. Open the reply while it points to the old parent. | In a development Django shell, change the row's `reply_to_message_id`. Observe the open dialog. Close and reopen it. | The open dialog keeps the old reply line and target. The reopened dialog shows the new reply line and target. | The open dialog changes live, or the reopened dialog still shows the old parent snapshot. |
| M16 | Create messages A and B with different titles, bodies and targets. Give A the largest accepted attachment. Set Network throttling to Slow 3G. Open A and confirm its detail request is pending. | Close A and open B. Remove throttling after B appears. Repeat by keeping the dialog open and changing `App.vue::peerReviewMessageId` from A to B in Vue Devtools. | B keeps its own title, body, warning, mode, scope and selection. No row scrolls toward A's target. | Any A detail state overwrites B, or B's picker scrolls toward A's target. |
| M17 | Create messages A and B with different titles and bodies. Give A a large syntax-highlighted body. Enable browser CPU throttling. Open A until its title appears but its body does not. | Close A and open B. Remove throttling after B appears. Repeat by keeping the dialog open and changing `App.vue::peerReviewMessageId` from A to B in Vue Devtools. | B keeps its own title and rendered body. A's rendered body never appears in B. | A's rendered body appears in B or replaces any part of B's body. |
| M18 | Create messages A and B with different targets. Put A's target outside the page and Pinia. Set Network throttling to Slow 3G. Open A until its by-id request is pending after detail appears. | Close A and open B. Remove throttling after B appears. Repeat by keeping the dialog open and changing `App.vue::peerReviewMessageId` from A to B in Vue Devtools. | B keeps its own warning, mode, scope and selection. A's target is never selected or scrolled into B's picker. | A's hydration changes B's warning, mode, scope or selection, or scrolls B toward A's target. |

The delayed hydration check observes a late scroll after a delayed response. The current harness cannot pause Vue's post-render callback alone. Therefore, the callback-only interval has no deterministic verification in this project.

### 16.3 Inbox, toast and approval gate

| ID | Starting state and how to reach it | Action | Correct observation | Wrong implementation signal |
|---|---|---|---|---|
| M19 | In development data, create one thread with known inbound and outbound replies. Record the message count. | Open the inbox. Open each reply's review dialog. | The inbox count stays unchanged. Each message has one row. Both surfaces show the direction-correct reply label. | Rows group or disappear, the count changes, or either surface shows a missing or direction-wrong label. |
| M20 | Keep the receiving UI open and use a paired development instance to send a reply. | Click the reply toast. Do not click a delivery action. | The toast opens only the review dialog. The message remains pending and no session composer opens. | The toast resolves or routes the message, opens a composer, or changes its pending status. |
| M21 | Open a pending reply whose picker-eligible target is visible. Confirm that the message is pending and its composer is closed. | Read the dialog. Click the existing-session delivery action once. | The message resolves only after the click. The target composer receives an unsent draft. The agent does not start. | The message resolves before the click, no draft appears, the agent starts, or the draft sends automatically. |

## 17. Documentation

- `CLAUDE.md`: add a new `PeerMessage` bullet under `## Database Models`. Describe the two local session links, `origin_session` and `delivered_to_session`; `reply_to_message`; the `(peer_id, thread_id)` local key; and this design.
- `AGENTS.md`: add the same `PeerMessage` bullet in its condensed form under `## Database Models`.
- `src/twicc/core/models.py::PeerMessage.origin_session` comment: it currently justifies the field by "the deferred threading, design §8"; point it at this document.
- `src/twicc/cli/peer_message.py::peer_message_cmd`: correct the `failed` status description as §11.3 defines.
- `SKILLS-AND-CLI.md`: the `peer-send` entry gains `--reply-to`.
- `twicc-peer-send/SKILL.md`: the option, where the id comes from (the header of a delivered peer message), and the two error codes.
- `twicc-peer-message/SKILL.md`: the four new output fields and the indeterminate meaning of `failed`.
- `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: minor version bump — a new flag on an existing skill.

## 18. Lots

Four lots run sequentially as 1 → 1.1 → 2 → 3. Each depends on the previous one's contract, and each is coherent on its own.

The grammar amendment uses this gate: fix the spec → scoped spec amendment review → complete normal lot 1.1 plan-with-multi-review loop → complete normal lot 1.1 implementation loop → resume lot 2. The committed Backend plan remains a historical execution artifact. Lot 1.1 gets a separate plan and a separate review workspace.

| Lot | Identity and scope |
|---|---|
| 1 | **Backend** — columns, migration, wire field and token grammar, resolution, `thread_id`, serializer, envelope, `--reply-to`, existing backend/CLI tests, and creation of `tests/test_peer_threading_migration.py` and `tests/test_peer_updates_consumer.py`. The agent surface works end to end; the UI shows nothing new. |
| 1.1 | **Backend grammar verification** — add only the missing grammar-verification test vectors from §15.1 and §15.4. Add standalone `:` rejection to the four parametrizations for inbound `message_id`, non-empty inbound `reply_to`, send-service `reply_to` and CLI `--reply-to`. Add accepted `1abc` and `abc-` to the four corresponding acceptance parametrizations. This lot changes no production behavior. The UI still shows nothing new. |
| 2 | **Front** — create `peerReplyTarget.js` and `peerReplyTarget.test.js`; use its candidate-or-load decision; share its picker-eligibility predicate with `sessionRows`; factor the dialog's explicit-scope candidate builder; then wire generation guards, by-id fallback, pagination recovery, one generic callout, pre-selection, inbox and dialog lines, and toast wording. |
| 3 | **Documentation and skills** — §17. |
