# Peer Messages — the `done` Resolution and "Answered by" — Design

**Status:** design settled with the user 2026-09-01; self-reviewed; implemented 2026-09-01 (suite green, awaiting the user's manual pass and commit)
**Date:** 2026-09-01
**Scope:** give the receiving user a third resolution for an inbound peer message — *done*: read and dealt with by the user themselves, without an agent — reachable from the review dialog and from the manual-reply form; make every resolution reversible; show on any message who answered it. Builds on the owner composer (`3fd094da`) and the human-written reply (`b7714d03`).

---

## 1. Background — what exists today

- **`PeerMessage.status`** (`core/models.py`, `PeerMessageStatus`): `pending` / `delivered` / `refused` / `failed`. For an inbound message it is **the answer given to the sender**: `mark_delivered` and `refuse_peer_message` (`core/services/peer_messages.py`) both call `_notify_status`, which POSTs the new value to the peer's `/peer/messages/<id>/status/`. `failed` is outbound-only.
- **The callback is best-effort.** `_notify_status` runs after the local resolution and swallows every failure; `peer/outbound.py::_post` raises on network errors only — a 4xx comes back as an ordinary return value and is ignored.
- **The receiving side of the callback**, `apply_status_callback`, whitelists exactly `delivered` and `refused` (anything else → 400 `invalid_payload`) and ignores a callback on a row whose `resolved_at` is already set: **the sender keeps the first resolution it hears of.**
- **`resolved_at`** is set at resolution; the purge task drops attachment bytes 7 days later (`purged_at`). A redelivery deliberately keeps the *first* `resolved_at` (comment in `mark_delivered`).
- **Transitions:** `_delivery_guards` accepts `pending`, and `delivered` only with `allow_redeliver`; `refused` is terminal ("A refused message stays refused" in the help page). `refuse_peer_message` accepts `pending` only. The review dialog mirrors this: `isRedeliverable` = `delivered`, `peerDeliveryActionVisibility(...).refusal` = pending.
- **The manual reply** (`PeerComposeDialog.vue`, opened by "Reply manually" in `PeerMessageReviewDialog.vue`) threads on the reviewed message and resolves nothing: the message keeps waiting for a decision.
- **Authorship on every message:** `origin.author` (`"agent"` / `"human"`), on the wire and on the row, whitelisted on receive. Replies link to the message they answer through `reply_to_message` (FK to self, `related_name="replies"`), resolved locally on receive against the opposite direction.
- **Inbox buckets** (`frontend/src/utils/peerInboxFilter.js::peerInboxView`): inbound + `pending` → "awaiting review"; everything else → history. The badge counts inbound `pending` (non-revoked peer) plus pending pairing requests.
- **Sender-side feedback:** `useWebSocket.js` toasts *"Your message to X was ${status}"* when an outbound row moves from `pending` to `delivered`/`refused`. `statusVariant` (inbox row and review dialog) maps `delivered` → success, `pending` → neutral, **anything else → danger**.

## 2. Problem

Two real outcomes of an inbound message have no honest resolution:

1. **The user answered it themselves.** The sender's agent asked which port staging uses; the user replies "8443" by hand. Nothing to deliver, nothing to refuse — yet the only ways out of the counter are *Refuse* (tells the sender "refused": false) or *Deliver* (prefills a composer nobody wants).
2. **Nothing to do.** "FYI, v2 is deployed." Same dead end.

A thread handled entirely by hand therefore leaves every message `pending` on both sides forever, and both badges never go down.

A separate, smaller gap: a message that received replies shows nothing about it. The sender sees `pending` and cannot tell that the other user already wrote back.

## 3. Decisions

| # | Decision |
|---|---|
| D1 | One new `status` value, **`done`**: the receiving user read the message and dealt with it themselves; no agent received it. Same word everywhere — wire, row, tag, button, radio. |
| D2 | `done` crosses the wire like the other two resolutions, and is **accepted** by `apply_status_callback`. An instance without this change answers 400, which the sender drops silently: its row stays `pending`, exactly as today. |
| D3 | **Every resolution is reversible**, `refused` included: `delivered` ↔ `done` ↔ `refused` in any direction. **`pending` is a starting state, never a destination** — there is no "reopen". |
| D4 | The sender only ever learns the **first** resolution (existing `resolved_at` guard in `apply_status_callback`, unchanged). |
| D5 | **`resolved_at` is set again on every resolution change**, redelivery included. The attachment purge window restarts each time. This reverses the "anchored to the first delivery" rule in `mark_delivered`. Once `purged_at` is set, nothing is restored. |
| D6 | The review dialog offers **Done** next to the two Deliver actions and Refuse, on **every** inbound message whatever its status — minus the button for the state it is already in. Done is **not** gated on attachment loading (like Refuse; the gate exists for delivery to an agent). |
| D7 | The manual-reply form gains a **three-way radio**, shown only when the answered message is still `pending`: **Keep it open** (default) / **Mark it done** / **Refuse it**. The chosen resolution is applied **server-side, after the reply was accepted by the peer (202)**, in the same request. A failed send resolves nothing. |
| D8 | Any message with replies shows who answered it, read from the replies' `origin.author`; the most recent reply decides. Replies to an **outbound** message come from the peer: **Answered by \<peer name\>** / **Answered by \<peer name\>'s agent**. Replies to an **inbound** message are the owner's own: **Answered by you** / **Answered by your agent**. Displayed with the routing lines, in the inbox row and the review dialog. Nothing is derived from `status`. |
| D9 | No local "seen" flag, no intermediate inbox section. `done` lands in history through the existing bucketing. |
| D10 | Sender-side toast gets a dedicated sentence for `done`. The toasts themselves are a separate, later topic. |
| D11 | Agent surface updated: `peer-message` documents `done` and what an agent may still expect; `peer-send` help no longer says "until they deliver or refuse it"; plugin version bumped. |

## 4. Data model

`PeerMessageStatus` gains `DONE = "done", "Done"`. One `AlterField` migration (choices only). No new column: `done` is a value of the existing `status`, and "answered by" is read from the existing `replies` relation.

## 5. Wire

- `_notify_status(peer, message_id, "done")` on a `done` resolution — same call as the other two.
- `apply_status_callback`: whitelist becomes `{delivered, refused, done}`.
- Older peer: 400 on `done`, dropped by the sender (`_post` does not raise on 4xx; `_notify_status` ignores the return). No regression: that message stays `pending` on the older side, which is today's behaviour for a hand-answered message.
- Nothing else changes on the wire. `origin.author` already travels with every reply (D8 needs no new field).

## 6. Transitions and guards

For an **inbound** message:

```
pending   → delivered | done | refused
delivered → done | refused | delivered (redeliver, another target)
done      → delivered | refused
refused   → delivered | done
```

- **Guards split by target.** Today `refuse_peer_message` reuses `_delivery_guards`, which also rejects a purged *pending* row — so a purged pending message can neither be delivered nor refused. The purge check is about attachment bytes reaching an agent; it stays on **delivery of a pending row only**. `done` and `refused` never look at `purged_at`.
  - Delivery: inbound, any status; `allow_redeliver` keeps its meaning for `delivered` (retarget); purged + `pending` → `purged` error as today; purged + resolved → allowed, text-only, with the existing warning.
  - Refuse: inbound, any status except `refused`.
  - Done: inbound, any status except `done`.
  - The "REFUSED is never re-openable" docstring and branch go.
- New `mark_done(message)`: same shape as `refuse_peer_message` — `_resolution_lock`, `_fresh_message`, guard, write `status` + `resolved_at = now` under the DB write lock, `broadcast_peer_message_updated`, then `_notify_status(..., "done")`. Clears nothing else: `delivered_to_session` and `recipient_note` are the history of a previous delivery and stay readable.
- Every resolution write sets `resolved_at = now` (D5). In `mark_delivered`, the `if message.resolved_at is None` branch and its comment are removed.
- The purge task selects on `resolved_at` alone (`peer_purge_task.py`), so `done` is purged like the others with no change there; a row already purged is never re-selected, whatever happens to `resolved_at` afterwards.

`failed` and outbound rows: untouched.

## 7. Owner UI

### 7.1 Review dialog (`PeerMessageReviewDialog.vue`)

- Action group on every inbound message: **Deliver to existing session** · **Deliver to new session** · **Done** · **Refuse**. The two Deliver actions are always offered (a `delivered` message can be retargeted, and keeps today's "again" wording); **Done** is hidden on a `done` message, **Refuse** on a `refused` one. Concretely: `canDeliver` → any inbound; `isRedeliverable` → `delivered` (wording only); `peerDeliveryActionVisibility(...).refusal` → status `!== 'refused'` instead of `pending`; a `refused` message is no longer read-only.
- **Done** is a single click with no confirmation and no note. It is not gated by `contentAllowsDelivery`.
- `statusVariant`: `done` → success.
- Attachment-purge warning (`attachmentsLost`): shown for any resolved status, not `delivered` only.
- Status tag shows the raw value: `done`.

### 7.2 Manual-reply form (`PeerComposeDialog.vue`)

- When `replyTo` is set **and** `replyPending` is true, a radio group under the message:
  - **Keep it open** — default
  - **Mark it done**
  - **Refuse it**
- Sent as `resolve_reply_to: "done" | "refused"` (absent for *Keep it open*) to `POST /api/peer-messages/send/`.
- The dialog's `dirty` check ignores the radio: choosing a resolution without writing is not typing.

### 7.3 Owner send endpoint (`peer/owner_views.py::peer_message_send`)

- Accepts the optional `resolve_reply_to`. Validates it is one of `done` / `refused` and that `reply_to` is present; else 400.
- On a successful send (`result.success`, i.e. the peer answered 202), resolves the **local inbound row** `reply_to` points at, through `mark_done` / `refuse_peer_message`, and returns its outcome alongside the send result. Resolution errors (row no longer pending, etc.) are reported in the response, the send itself is not rolled back — it already left.
- Any failure to send → nothing is resolved.

### 7.4 "Answered by" (`PeerInboxRow.vue`, `PeerMessageReviewDialog.vue`)

- New routing line when the serialized message carries `latest_reply_author`. The wording follows the message's own `direction` (a reply always sits on the other side of its parent): outbound parent → **Answered by David** (`human`) / **Answered by David's agent** (`agent`), with `peersStore.peerLabel`; inbound parent → **Answered by you** / **Answered by your agent**.
- Coexists with the existing *In reply to your/their "…"* line.

## 8. Serializer and queries

- `serialize_peer_message` adds `latest_reply_author`: `null` when the row has no replies, else the `origin.author` of the most recent reply (`created_at` order; absent author reads as `agent`, the historical meaning).
- Read from the prefetched `replies` relation with `.all()` iterated in Python — any `.filter()` on the manager would issue a query per row.
- `prefetch_related("replies")` added next to the existing `select_related(...)` in: the REST list (`peer_messages_list`, both branches), the REST detail (`owner_views._load_message`), the WS snapshot (`asgi.py`), and the two single-row reloads in `peer_messages.py` — `_fresh_message` and the reload behind `broadcast_peer_message_received` / `broadcast_peer_message_updated`. One extra query per list, never per row.

## 9. Sender side

- `useWebSocket.js`: the outbound-row toast fires for `done` too, with its own sentence: **"\<peer\> marked your message as done"** (D10). The `delivered`/`refused` sentence is unchanged.
- `statusVariant` in `PeerInboxRow.vue`: `done` → success.

## 10. Agent surface

- `cli/peer_send.py` help: "the returned peer_status stays "pending" until they deliver or refuse it" → "…until they deliver it, mark it done, or refuse it".
- Skill `twicc-peer-message/SKILL.md`: add `done` to the status list and to the translation step, with the meaning an agent must act on: *the remote user dealt with the message themselves; no agent received it; if they answered, the answer arrived as a peer message in your user's inbox — it will not come through this status.*
- `SKILLS-AND-CLI.md`: mirror the new value.
- `plugin.json`: patch bump.

## 11. Help page (`frontend/public/help/peers.md`)

- *Reviewing an incoming message*: a fourth choice — mark it done, when you dealt with it yourself or there is nothing to do; no agent receives it.
- *Replies and session suggestions*: the reply form can keep the answered message open, mark it done, or refuse it.
- *Message status*: add `done`, and state the three meanings from the sender's side — `delivered`: read and handed to an agent, something may follow; `refused`: declined, nothing follows except a possible reply; `done`: dealt with by the user, nothing more will come from an agent — look for a reply. Say that the sender sees the first decision only.
- *Redelivery and history*: drop "A refused message stays refused. It cannot be delivered later."; state that any decision can be changed later and that the attachment purge restarts from the latest one.
- The "answered by" line: one sentence under *Replies*.

## 12. Out of scope

- Reviewing the sender-side toasts as a whole (D10 only adds the missing sentence).
- Browser / Apprise notifications for peer events.
- A local "seen" / "mark as read" flag, and any intermediate inbox section.
- Reopening a resolved message to `pending`.
- Refusing with a reason in one step outside the reply form (the reply form with *Refuse it* covers it).

## 13. Test scenarios

Backend (`tests/test_peer_messages.py`, `tests/test_peer_updates_consumer.py`):

- T1 — `mark_done` on a pending inbound row: status `done`, `resolved_at` set, callback `done` sent, broadcast emitted.
- T2 — `apply_status_callback` accepts `done` on a pending outbound row; still ignores any value once `resolved_at` is set.
- T3 — Every transition of §6 succeeds; `pending` is never reachable; `done → done` and `refused → refused` are rejected.
- T4 — Each transition sets a fresh `resolved_at` (redelivery included).
- T5 — Owner send with `resolve_reply_to: "done"`: on 202 the parent is `done`; on 403/network error the parent stays `pending`; invalid value or missing `reply_to` → 400 before any send.
- T6 — `latest_reply_author`: `null` without replies; `human` / `agent` from the latest reply; absent author → `agent`; no query per row (assert query count on the list endpoint and the WS snapshot).
- T7 — The WS snapshot and list contracts include the new key.

Frontend (`src/**/*.test.js`): `peerReplyTarget.js` visibility helpers cover the new statuses; `peerInboxFilter.js` buckets `done` into history.

Manual (two up-to-date instances):

- M1 — Reply with *Mark it done*: the answered message moves to history as `done`; the other side sees `done` and the reply, the toast sentence reads correctly.
- M2 — Reply with *Keep it open*: message stays in "awaiting review" and shows *Answered by you*; the other side stays `pending` and shows *Answered by \<name\>*.
- M3 — Reply with *Refuse it*: `refused` locally and on the other side; the reply still arrives.
- M4 — Reply from a `delivered` message in history: no radio group.
- M5 — Done from the review on a message with unloaded attachments: allowed.
- M6 — Change a `refused` message to `done`, then to `delivered`: local status follows; the other side keeps `refused`.
- M7 — Against an instance without this change: *Mark it done* resolves locally, the other side stays `pending`, no error shown.
