# Peer Messaging (Cross-Instance Agent Messages) — Design

**Status:** design drafted with the user 2026-07-24; all open questions resolved with the user the same day (§10); out-of-band identity verification (§2.7, §4.2) added the same day after discussion with the first test user; implementation not started
**Date:** 2026-07-24
**Scope:** let two independent TwiCC instances (two users, e.g. colleagues on the same project) exchange agent-to-agent messages: instance-level pairing with mutual consent ("friend request" model), agent-initiated sends through the CLI/MCP surface, and a mandatory human approval gate on the receiving side before anything reaches the receiving agent.

---

## 0. Relationship to multi-host — terminology firewall

This feature is **entirely separate** from the prepared multi-host feature (`2026-07-02-multi-host-design.md`, `multi-hosts` worktree). Multi-host is one user aggregating their own machines with full trust (hub mirrors and controls remotes); peer messaging is two sovereign users with minimal trust — the only allowed flow is a message, and only after human approval.

The two must never share vocabulary. Multi-host reserves **host**, **remote**, **hub**, **federation** (models `Host`, `FederationClient`, `HostKey`, …). This feature uses **peer** exclusively: `Peer`, `PeerMessage`, `peerBaseUrl`, `/peer/…`, `twicc peers` / `peer-send`. "Peer" is free in the current codebase (only TCP loopback jargon in `auth/local_access.py` and tool_use/tool_result "pairing" in compute helpers — no product concept). If multi-host lands later, a peer stays a distinct concept; do not fold one into the other.

## 1. Background — building blocks that exist today

- **Token model:** `core/services/share_tokens.py` — `secrets.token_urlsafe(32)` secrets stored plaintext on an indexed column, verified with `hmac.compare_digest`. Same trade-off reused here.
- **Exposed-surface precedent:** sharing gates a public surface behind a synced setting (`shareBaseUrl`) read live per request (`share/asgi_filter.py`). The share host exists for cookie isolation — irrelevant here (peer endpoints are API-only, Bearer-auth, cookie-free), so peers do **not** get a dedicated host; see §4.3.
- **Outbound HTTP:** `httpx` is the client everywhere (artifact broker proxy, usage fetch, OpenRouter pricing).
- **Message injection:** `core/services/send_message.py::send_message_to_session_from_payload` is the single entry point used by CLI/MCP/drop-requests, with its business-rule rejections (`session_stale`, `awaiting_user_input`, …). Session creation has the equivalent service path used by `create-session`.
- **Agent surface generation:** adding a CLI command auto-adds the RPC route and the MCP tool (`src/twicc/mcp/`); management verbs can be excluded via `MCP_EXCLUDED_ROOTS` (precedent: `share`).
- **Realtime + toasts:** the `"updates"` channel-layer group with `data.type`-discriminated broadcasts (`asgi.py`), actionable toasts via `useToast.js` / `toast.custom(...)` (live example with buttons: `SessionToastContent.vue`), OS notifications (`utils/notificationSounds.js`), server-push via Apprise (`external_notifications.py`).
- **Not reused:** the Codex `pending_requests` machinery — it is coupled to a live `ProcessRun` that blocks the agent awaiting the answer. A peer message blocks nothing; it waits in a persistent inbox.

## 2. Requirements (user-stated)

1. A user registers instances to communicate with: **name + URL**, with a pending/approved state — a friend-request flow. One side requests, the other accepts (and names the peer on their own side too).
2. **No sender-side confirmation**: the user asks their agent to send ("send David's instance a recap"), the agent calls the tool, it goes out. The MCP surface is the trusted control plane, as everywhere in TwiCC.
3. **Silent revocation**: either side can revoke at any time by deleting locally, without informing the other. The authority is the *receiving* side's state, checked live on every send; a revoked sender simply gets rejected on their next attempt.
4. **Receiving-side human gate**: an incoming message never reaches an agent directly. The receiving user is notified, reads the full message, then either refuses or delivers it — choosing an **existing session** or a **new session** (project picker, like today's new-session flow).
5. **Images are first-class** (e.g. a front-end screenshot sent to the colleague doing the back-end). The wire payload is the same JSON shape as today's send-message payload (`text`, `images`, `documents`).
6. Each side displays the peer under its own local alias ("Message from David").
7. **Out-of-band identity verification** (added 2026-07-24 with the first test user): a claimed name/URL proves nothing about who sent a request. The receiving side displays a short numeric code; the receiving user transmits it to the requester over a channel the two humans trust; the requester's instance echoes it back over the wire. Acceptance stays locked until the code checks out — no exception, including crossed requests (a locally-typed URL can be stale or wrong). Symmetrically, a requester's row only activates once its own code submission has succeeded, so an accept coming from a hijacked/stale URL cannot silently activate a relationship.

## 3. Data model

### 3.1 `Peer` (new model)

One row per related instance, on each side.

| Field | Type | Notes |
|---|---|---|
| `id` | CharField PK | `peer_` + short hex, minted like `mint_share_id()` |
| `name` | CharField | **local** alias, set by this side (requester at creation, acceptor at accept time); editable |
| `remote_display_name` | CharField | the name the other instance claims for itself in the handshake — a hint shown in the accept dialog, never authoritative |
| `base_url` | URLField | the peer's public `peerBaseUrl`; editable (an address, not an identity — tokens are the identity) |
| `state` | CharField | `pending_sent` / `pending_received` / `active` / `broken` (see below) |
| `token_ours` | CharField, indexed | secret **they** present to call us; minted locally (`token_urlsafe(32)`), plaintext + constant-time compare (Share trade-off) |
| `token_theirs` | CharField, null | secret **we** present to call them; null until accepted (requester side) |
| `verification_code` | CharField, blank | 6-digit code shown to the local user on a `pending_received` row; the requester must echo it back (§4.2 step 2). Regenerated after 5 failed attempts |
| `verification_attempts` / `verification_regens` | SmallInt | failed echo attempts; at 5 → regenerate code + reset; at the 3rd regeneration the pending request is dropped (hard guess ceiling) |
| `verified_at` | DateTime, null | the requester correctly echoed our code — unlocks Accept |
| `code_confirmed_at` | DateTime, null | requester side: our own code submission was confirmed by the peer — required before the row may activate |
| `remote_accepted_at` | DateTime, null | requester side: accept callback received before `code_confirmed_at`; held, with a UI warning |
| `created_at` / `accepted_at` / `last_contact_at` | DateTime | |

State machine: the state that matters for an A→B send is **B's row, checked live on every request**. Revocation = local row deletion; the token becomes unknown, the next inbound call 403s, and the *caller* flips its own row to `broken` (kept for display, with a clear error surfaced to agent and user). No cross-instance state sync exists — that absence is the robustness.

`broken` is also the landing state for a refused friend request (the acceptor may answer with an explicit refusal, or simply delete; the requester's row then breaks on first send).

### 3.2 `PeerMessage` (new model)

One row per message, both directions.

| Field | Type | Notes |
|---|---|---|
| `peer` | FK `Peer`, CASCADE | |
| `direction` | CharField | `in` / `out` |
| `message_id` | CharField | minted by the sender; unique per `(peer, direction)` — idempotency / replay guard |
| `payload` | JSONField | same shape as the send-message payload: `{text, images, documents}`, attachments as base64 data-URIs; same limits as today's attachments (5 MB/file, 32 MB total) |
| `origin` | JSONField | sender-declared provenance for display: sending session **title** + sent-at timestamp. Deliberately no session id on the wire — local ids stay local (see §8). On the outbound row, the sending session id is stored **locally** alongside (needed for the deferred threading, §8). |
| `status` | CharField | `pending` / `delivered` / `refused` / `failed` (outbound network/4xx errors, with `error` detail) |
| `delivered_to_session` | FK `Session`, null | the session the receiving user routed it to |
| `created_at` / `resolved_at` | DateTime | |

Retention (resolved §10.4): keep the row (text + metadata, incl. attachment names/sizes) as history for the lifetime of the relationship — revoking (deleting) a peer deletes its message history with it (FK CASCADE); attachment **bytes** are purged by a background job **7 days after resolution** (deferred purge — a recent image can still be re-viewed in the inbox history, and the DB doesn't accumulate base64 blobs; `SessionItem.content` already dominates DB size — don't recreate the problem).

## 4. Transport

### 4.1 Endpoints (inbound)

All under `/peer/`, JSON, served by the instance being called. `httpx` for the outbound leg.

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /peer/handshake/request` | **none** (it *is* the friend request) | `{display_name, base_url, token}` — the token the requester minted for callbacks. Creates a `pending_received` row + notification. Protections: IP rate-limit, dedup by `base_url`, cap on pending rows. |
| `POST /peer/handshake/verify` | Bearer (the requester's minted token) | the requester echoes the code its user received out-of-band. Match → the pending row is verified (Accept unlocks). Rate-limited; 5 mismatches → code regenerated; 3 regenerations → request dropped. Idempotent on `active` rows (held-accept recovery). |
| `POST /peer/handshake/accept` | Bearer (token from the request) | acceptor → requester after the human accepts; carries the acceptor's minted token. Requester's row flips `active` only if its own `code_confirmed_at` is set (held otherwise); toast "David accepted". A `refuse` variant is optional (silence works too — the row breaks on first send). |
| `POST /peer/messages` | Bearer `token_ours` | the message. `202` = stored `pending`; `403` = unknown token (revoked/never accepted) → caller marks the relationship `broken`. This is where "revoked ⇒ rejected immediately" materializes. |
| `POST /peer/messages/<message_id>/status` | Bearer | resolution callback (`delivered` / `refused`) so the sender's row — and its agent via the CLI — learns the outcome. Best-effort (failure never blocks the local resolution). |

### 4.2 Handshake summary

1. User A creates a `Peer` (name + B's URL) → A mints `token_ours`, POSTs `handshake/request` to B with `{A's display name, A's peerBaseUrl, that token}` → A's row `pending_sent`, B's row `pending_received` with a freshly minted 6-digit `verification_code`.
2. **Identity verification (out-of-band).** B's UI shows the code next to the request. B transmits it to A over a channel the two humans trust (call, Signal, in person) — the same conversation in which B confirms A really sent a request. A enters it; A's instance POSTs `handshake/verify` to B (authenticated with A's token). Match → B's row is verified (Accept unlocks) and A's row records `code_confirmed_at`. This is the identity boundary: the accept callback proves URL *control*, the code proves *who* is behind the request.
3. User B names the peer locally and accepts → B mints its own `token_ours`, calls back `handshake/accept` on A's URL (authenticated with the token from step 1), carrying B's token.
4. A's row activates only if its own `code_confirmed_at` is set — always true in the honest flow, since B cannot accept before verification. Otherwise the accept is held (`remote_accepted_at`) and A's UI warns to contact the peer. Both rows `active`; both directions can send.

Crossed requests (both users add each other near-simultaneously) merge into a single row per side; verification applies symmetrically — each side shows a code, each user echoes the other's, and each Accept unlocks on its own row's verification. **No crossed exemption**: a locally-typed URL can be stale or wrong, and an attacker controlling it could fabricate the crossed case precisely to dodge the code.

### 4.3 Ingress: main origin carve-out + `peerBaseUrl` setting

The `/peer/` prefix is **exempt from the human auth gates** (password gate, local-only remote refusal in `auth/middleware.py` / `local_access.py`) — it carries its own Bearer auth. No dedicated host à la share: the share host exists for cookie isolation, and peer endpoints use no cookies.

A new synced setting **`peerBaseUrl`** (default `""`) plays the same dual role as `shareBaseUrl`:

- empty ⇒ feature disabled: no `/peer/` endpoint answers (uniform 404), no outbound handshake possible;
- set ⇒ it is the URL advertised to peers in handshakes (and shown in the settings UI as "your address").

HTTPS strongly recommended; warn in the UI on a plain-`http` peer or self URL.

### 4.4 Security posture

- 256-bit bearer tokens per direction, constant-time compare; the human accept is the trust root.
- The 6-digit verification code is the **sole barrier against an attacker-requester** — such an attacker mints their own Bearer token for free, so "the code is useless without the token" only holds against third parties. It must therefore be brute-force-proof: the `verify` endpoint is rate-limited (per source IP), 5 mismatches regenerate the code, and after 3 regenerations (≤ 15 total guesses) the pending request is dropped entirely. Constant-time compare. The code never appears on the agent surface and must never be forwarded to share viewers (`ShareConsumer`'s broadcast whitelist is load-bearing).
- The **receiving-side human read-before-delivery is the prompt-injection boundary** — an agent-authored message never reaches another agent without a human having read it. The injection envelope (§6.3) is the second half: the receiving agent sees it as external third-party content, not as its own user's words.
- Agents cannot manage relationships (§5): no tool creates, accepts, or revokes a peer, so an agent can neither self-authorize a channel nor pick an arbitrary URL to exfiltrate to — `peer-send` only reaches user-approved instances.
- Replay/idempotency by `message_id`; size caps as today's attachments; rate-limit on the unauthenticated handshake endpoint.

## 5. Agent surface (CLI → RPC + MCP, auto-generated)

Agent-facing verbs only — relationship **management is human-only** (REST for the UI; the management roots go to `MCP_EXCLUDED_ROOTS`, precedent: `share`):

- `twicc peers` — list `active` peers (name, state, last contact) so the agent resolves "David's instance".
- `twicc peer-send <peer> --text … [--attach …]` — create the outbound `PeerMessage`, backend POSTs it, returns `{message_id, status}`. `--attach` accepts local paths and data-URIs like existing commands. No confirmation step (requirement §2.2). (`--reply-to` is deferred with threading, §8.)
- `twicc peer-message <message_id>` — re-check status ("still pending approval", "refused", "delivered").

A `broken` relationship surfaces as an explicit send error telling the agent (and through it the user) that the peer no longer accepts messages.

## 6. Receiving flow

An inbound message is stored `pending`; nothing touches any agent.

1. **Notify.** New `"updates"` broadcast type `peer_message_received` → actionable toast (`toast.custom`, modeled on `SessionToastContent.vue`: "Message from David — Read / Later") + a persistent **inbox badge** (header) listing pending messages — a missed toast must never lose a message. Optionally wire `external_notifications.py` (Apprise) for absent users, and the existing OS-notification path.
2. **Read & route.** A dialog renders the full message (markdown text + images), provenance ("David · session *Front revamp* · 2 min ago"), and three actions:
   - **Deliver to an existing session** — session picker (search); injection via `send_message_to_session_from_payload`, inheriting its existing rejections (`awaiting_user_input`, stale, archived project, …) which surface in the dialog;
   - **Deliver to a new session** — project picker as in today's new-session flow, then the create-session service with the message as first prompt (agent settings resolved from project defaults as usual);
   - **Refuse** — status `refused`, best-effort status callback.

   The message itself is **never editable** (resolved §10.3) — provenance stays intact. Instead, both deliver actions offer an optional **recipient note** field: a short text injected alongside the message, explicitly attributed to the receiving user in the envelope (e.g. framing instructions for the local agent). The sender's `delivered` status is unaffected by the note.
3. **Injection envelope.** The delivered message is wrapped in an explicit header block (peer alias, origin session title, "external message approved by your user") so the receiving agent treats it as third-party communication, not user input; the recipient note, when present, appears as a separate clearly-attributed section. Sessions share no memory across instances — the envelope also reminds that every message must be self-contained.
4. **Resolution broadcast** (`peer_message_updated`) refreshes the inbox/badge on all connected clients.

## 7. Sending flow

`peer-send` → outbound `PeerMessage` (`pending`) → POST to the peer. `202` keeps `pending` (awaiting the colleague's approval); `403` → row `failed`, peer `broken`; network error → `failed` with detail (no automatic retry in v1). The status callback (or a later `peer-message` poll) moves it to `delivered` / `refused`; a small `peer_message_updated` broadcast lets the UI show "David('s user) accepted your message". No automatic injection of the outcome into the sending session — the agent asks via `peer-message` if it cares.

## 8. Threading (`reply_to`) — DEFERRED (not in v1)

Deferred by decision (§10.5): in v1, every reply is routed by hand through the session picker. The mechanism, recorded for a later version:

`origin.reply_to` carries the `message_id` being answered. The `message_id` acts as an **opaque communication id** — no session id ever travels: each side resolves it against its own `PeerMessage` rows (outbound row records the local sending session; inbound row records `delivered_to_session`). Sole effect on the receiving side: the approval dialog **pre-selects the session tied to the original message** as the delivery target (still overridable). The envelope (§6.3) would then expose the `message_id` so a replying agent can pass `--reply-to`. Note when picking it up: the v1 envelope does not include the `message_id`, so older sessions won't know how to reply-to.

## 9. UI surface (summary)

- **Settings › Peers** (label resolved §10.1): list (alias, URL, state, last contact), add (name + URL → sends the request), accept/refuse incoming (with local naming), rename, revoke (delete, silent), edit URL. Shows own `peerBaseUrl` + enable state.
- **Inbox**: header badge + panel of pending inbound messages (opening the read/route dialog) and recent history (both directions, with statuses).
- **Toasts**: incoming request, incoming message, accepted handshake, outbound resolution.

## 10. Resolved decisions (user, 2026-07-24)

1. **Naming** — human-facing label is **"Peers"**, aligned with the technical name.
2. **Ingress** — **main-origin `/peer/` carve-out + `peerBaseUrl`** confirmed (§4.3); dedicated host rejected as oversized.
3. **Edit before delivery** — **no editing**; optional **recipient note** injected alongside, clearly attributed (§6.2).
4. **Attachment retention** — **deferred purge**: attachment bytes dropped 7 days after resolution, text + metadata kept forever (§3.2).
5. **`reply_to`** — **deferred, not in v1** (§8); mechanism recorded there for a later version.
6. **Identity verification** (2026-07-24, after discussion with the first test user) — mandatory out-of-band 6-digit code on every incoming request (§4.2 step 2), with **no crossed-request exemption**; a correct code **unlocks** Accept (no auto-accept); the requester side activates only after its own submission succeeded.
