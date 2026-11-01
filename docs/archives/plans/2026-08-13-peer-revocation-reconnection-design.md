# Peer Revocation, Reconnection, and Address Changes — Design

**Status:** implemented
**Date:** 2026-08-30
**Scope:** retain Peer history after revocation, reconnect an established Peer, and invalidate Peers after a local address change.

---

## 0. Relationship to existing Peer designs

`docs/plans/2026-07-24-peer-messaging-design.md` remains the founding Peer design.

This document replaces its established-Peer deletion contract. It also replaces the previous content at this path.

The Peer system remains development-only. This design requires no compatibility with an earlier development schema or wire contract.

The threading contract in `docs/plans/2026-08-11-peer-threading-design.md` remains unchanged.

## 1. Current implementation

This section records current behavior that this feature changes.

### 1.1 Peer lifetime

`src/twicc/core/models.py::PeerState` defines `pending_sent`, `pending_received`, `active`, and `broken`.

`src/twicc/core/services/peer_mutation.py::delete_peer` deletes any Peer.

`src/twicc/core/models.py::PeerMessage.peer` uses `CASCADE`. Deleting a Peer therefore deletes its message history.

### 1.2 Pairing state

`src/twicc/core/models.py::Peer` stores the initial handshake fields on the Peer row.

These fields include both bearer tokens, the verification code, and the verification and acceptance timestamps.

`src/twicc/core/services/peer_mutation.py` already implements request, verification, acceptance, refusal, and retry behavior.

The initial handshake supports crossed initial requests. This feature does not change that behavior.

### 1.3 Message behavior

`src/twicc/core/services/peer_messages.py::send_peer_message_from_payload` creates an outbound message before the remote call.

A remote `403 unknown_token` marks the message failed and the Peer broken.

An ordinary network error marks the message failed. It does not mark the Peer broken.

Inbound messages and status callbacks authenticate against a live active Peer.

### 1.4 Local Peer address

`peerBaseUrl` is a synchronized setting.

`src/twicc/core/services/settings_mutation.py` writes the setting without changing Peer credentials or lifecycle state.

`src/twicc/core/services/public_origin.py::normalize_public_origin` owns the canonical public-origin contract.

### 1.5 Owner UI state

`frontend/src/stores/peers.js` receives complete Peer snapshots and incremental updates over the existing WebSocket.

`frontend/src/components/peer/PeersManagerDialog.vue` owns the owner actions for pairing and Peer management.

The dialog already reports a rejected local `fetch()` as a network error. It does not reload the authoritative Peer list.

## 2. Design principles

### 2.1 Local correctness

Every completed local mutation leaves SQLite in a valid state.

A process interruption can leave a visible operation incomplete. It must not restore an invalid credential.

The database write lock serializes lifecycle changes that can affect the same Peer.

No database lock remains held during a remote network call.

### 2.2 Best-effort network behavior

The feature does not implement distributed transaction semantics.

It does not determine every uncertain remote result automatically.

Normal user actions remain retryable when a lost response could otherwise block progress.

Rare concurrent workflows use Refresh, Cancel, Refuse, or Retry.

The feature does not add background retries, reconciliation jobs, timers, or startup repair.

### 2.3 Single-user frontend

The frontend does not coordinate actions across several browser tabs or devices.

The backend re-reads mutable rows before each lifecycle write.

A stale frontend action returns the current conflict. A refresh restores the authoritative view.

The feature does not add row authorities, action identities, watermarks, or client request sequencing.

## 3. Peer states and storage

### 3.1 Durable lifecycle state

`PeerState` gains `revoked`.

The durable states have these meanings:

| State | Meaning | Messageable |
|---|---|---:|
| `pending_sent` | An initial pairing request was sent. | No |
| `pending_received` | An initial pairing request was received. | No |
| `active` | The current credentials are usable. | Yes |
| `broken` | The relation is unavailable without an explicit owner action. | No |
| `revoked` | The local owner ended the relation. | No |

Only `active` authorizes credential-sensitive Peer operations.

### 3.2 Broken reason

`Peer` gains a blankable `broken_reason`.

The feature writes only these values:

| Value | Meaning |
|---|---|
| `remote_credential_rejected` | The remote instance rejected the credential with `403 unknown_token`. |
| `local_address_changed` | The local owner replaced a non-empty `peerBaseUrl`. |
| `local_address_disabled` | The local owner cleared `peerBaseUrl`. |

A legacy broken Peer can keep a blank reason.

An ordinary network error does not create a broken reason.

### 3.3 One reconnect attempt

`Peer` gains a blankable `reconnect_direction`.

Its non-empty values are `sent` and `received`.

An established Peer can store at most one reconnect attempt.

The feature does not store both directions at once. It does not define a crossed reconnect state.

### 3.4 Reused handshake fields

The reconnect reuses the existing Peer handshake fields:

- `token_ours`;
- `token_theirs`;
- `verification_code`;
- `verification_attempts`;
- `verification_regens`;
- `verified_at`;
- `code_confirmed_at`;
- `remote_accepted_at`.

On an active Peer, the token fields are the current credentials.

On a broken or revoked Peer with `reconnect_direction`, they are provisional credentials.

Every reader checks the durable state and reconnect direction before using them.

The reconnect uses the existing verification-attempt limits. This feature does not add another limit system.

### 3.5 Local-origin binding

`Peer` gains a blankable `paired_local_base_url`.

A successful initial pairing or reconnect stores the current canonical `peerBaseUrl` in this field.

Every credential-sensitive operation compares this field with the current canonical setting.

A non-empty mismatch rejects the operation without changing the Peer.

A blank binding never authorizes a credential-sensitive operation.

The schema migration converts existing active Peers to `broken / local_address_changed` and clears their credentials.

The migration preserves their identity and message history.

Direct manual edits to `settings.json` remain outside this contract.

### 3.6 Message ownership

`PeerMessage.peer` changes from `CASCADE` to `PROTECT`.

An established Peer is never deleted by an owner lifecycle action.

Initial pending Peers remain deletable. They cannot own messages.

The model adds no `revoked_at`, retry counter, network-result field, or message reconciliation field.

## 4. Revoke

### 4.1 Allowed states

Revoke accepts an active or broken established Peer.

Revoke on an already revoked Peer is an idempotent success.

Initial pending rows use their existing Cancel or Refuse actions. They are not revoked.

### 4.2 Local transition

Revoke re-reads the Peer under the database write lock.

The transaction:

- sets `state = revoked`;
- clears `broken_reason`;
- clears both bearer tokens;
- clears every handshake field;
- clears `reconnect_direction`;
- preserves the Peer name, remote origin, timestamps, and messages.

The service publishes the updated Peer after the transaction commits.

Revoke makes no remote call. It sends no revocation notification.

### 4.3 Messages after Revoke

Revoke does not modify any existing `PeerMessage`.

Outbound messages that are `pending` remain `pending`.

The system does not query their remote status later.

An inbound pending message remains locally reviewable through explicit revoked-Peer history.

Resolving that message locally after Revoke sends no remote status callback.

New messages and status callbacks using an old token fail authentication.

### 4.4 Later reconnect requests

A revoked Peer can receive a later reconnect request from the same remote origin.

The owner can Accept, Refuse, or ignore that request.

Revoke is not a permanent block-list feature.

The reconnect request keeps the initial handshake endpoint's existing rate limit and payload validation.

## 5. Reconnect

### 5.1 Identity

A same-origin reconnect reactivates the same local Peer row.

It preserves that row's complete message history.

Reconnect accepts only `broken` and `revoked` established Peers.

An active or initial-pending Peer returns a state conflict.

The current canonical local `peerBaseUrl` must be non-empty.

### 5.2 Start, manual Retry, and Cancel

The owner Reconnect action creates `reconnect_direction = sent` under the write lock.

It clears obsolete handshake values and mints one new `token_ours`.

It then posts a pairing request to the Peer's unchanged remote origin.

A successful HTTP response leaves the sent attempt visible.

A network error also leaves the sent attempt visible. The direct response reports the error.

Retry calls the same owner endpoint. It resends the same request with the same `token_ours`.

Retry does not mint another token. It has no timer and no automatic invocation.

Cancel posts `/peer/handshake/cancel/` with the sent attempt's provisional token.

The remote instance clears only the received reconnect attempt with that token.

The requester then clears the same local attempt. Both durable Peer states and both histories remain unchanged.

A `2xx` response confirms the remote cancellation.

A `404 unknown_request` also succeeds because the remote attempt is already absent.

A network error, redirect, or other HTTP error keeps the local sent attempt visible. The owner can retry Cancel.

### 5.3 Receive and replay

An inbound reconnect request matches an established Peer by canonical remote origin.

A broken or revoked Peer with no attempt records `reconnect_direction = received`.

It stores the requester's token and creates the normal verification code.

An exact replay with the same token is an idempotent success.

A different request while an attempt exists returns `409` and changes nothing.

An active Peer returns the existing already-related conflict.

An unknown origin enters the normal initial pairing flow.

### 5.4 Verify, Accept, and Refuse

The reconnect uses the existing six-digit verification workflow.

Verify retries the same code submission. A lost successful response remains recoverable by another click.

Accept persists and reuses one local provisional token before the remote callback.

Accept calls the initiator before activating the accepting Peer.

If the callback response is lost, another Accept click sends the same token again.

An initiator already activated by that exact callback returns idempotent success.

Refuse clears a received reconnect attempt. It leaves the durable Peer and its history unchanged.

### 5.5 Promotion

A successful reconnect promotes only the current stored attempt.

Promotion:

- sets `state = active`;
- keeps the new token pair;
- updates the remote display name when supplied;
- clears `broken_reason`;
- clears `reconnect_direction`;
- stores the current canonical `paired_local_base_url`;
- preserves the Peer id, local name, accepted history, and messages.

A Revoke or address change clears the attempt before a late callback can promote it.

A late callback that no longer matches the stored token fails without changing the Peer.

### 5.6 Simultaneous reconnects

Two users can start Reconnect before either request reaches the other instance.

Both local Peers can then show a sent attempt. Each inbound request can receive `409`.

The supported recovery is manual:

1. one user selects Cancel;
2. the other user selects Retry;
3. the remaining request becomes received.

The feature does not choose an initiator automatically. It does not merge both attempts.

## 6. Local network errors

### 6.1 Unknown local mutation result

A rejected browser `fetch()` does not prove that the local server skipped the mutation.

Manage Peers shows one dialog-level banner:

> The request result is unknown. Reload peers to read the current server state.

The banner provides **Reload peers**.

Reload peers performs `GET /api/peers/` and passes the returned list to `peersStore.applyPeers`.

It never replays the failed mutation.

A failed reload keeps the banner visible.

### 6.2 WebSocket state

The existing WebSocket snapshot remains the normal hydration path.

The feature adds no Peer reconciliation hook on WebSocket reconnect.

An owner can use Reload peers or refresh the page when a view appears stale.

## 7. Changes to the local `peerBaseUrl`

### 7.1 Confirmation

The owner UI asks for confirmation when a non-empty address becomes empty or changes to another address.

The confirmation states that active relations become unavailable, credentials are cleared, and reconnect is manual.

The Settings section shows this confirmation below the address input. It does not use a browser-native dialog.

Cancel restores the saved address and sends no settings write.

Empty to valid needs no confirmation. It changes no Peer and starts no reconnect.

The confirmation offers no automatic-reconnect option.

### 7.2 Supported setting writes

Every supported backend write of a changed `peerBaseUrl` applies this lifecycle contract.

The backend canonicalizes the proposed value with `normalize_public_origin`.

The owner UI does not become a second origin authority.

A settings write never starts remote Peer work.

### 7.3 Local invalidation

After confirmation, the service writes the canonical setting and applies one SQLite transaction.

For a valid address A changed to valid address B:

- every Peer active at the transition becomes `broken / local_address_changed`;
- its current credentials are cleared;
- every reconnect attempt is cleared.

For a valid address changed to empty:

- every Peer active at the transition becomes `broken / local_address_disabled`;
- its current credentials are cleared;
- every reconnect attempt is cleared.

For both transitions:

- initial pending Peer rows are deleted;
- already broken Peers keep their state and previous reason;
- already revoked Peers remain revoked;
- provisional reconnect fields are cleared from every established Peer;
- no outbound request starts.

The owner reconnects affected Peers individually.

### 7.4 Interrupted setting transition

The settings file and SQLite cannot share one transaction.

The service writes the setting before the SQLite invalidation.

If the process stops in that interval, the local-origin binding blocks every old credential.

Reapplying the already-stored address completes the local invalidation when mismatched active Peers remain.

The system adds no durable transition marker and no startup repair.

### 7.5 Remote view of a moved instance

An instance that changes its own origin can reconnect its existing Peers manually.

The remote instance receives the request from the new canonical origin.

That origin creates a normal new initial Peer on the remote instance.

The new Peer has new history. It has no stored link to the old origin.

The request carries no previous-origin claim.

Accepting the new Peer does not change the old Peer automatically.

The remote owner can revoke the old Peer manually.

## 8. Canonical remote origins

New Peer origins and inbound Peer origins use `normalize_public_origin`.

New creation checks for an existing canonical origin under the database write lock.

An exact active or initial-pending match returns the existing duplicate conflict.

An exact broken or revoked match directs the owner to Reconnect.

The feature does not add a SQL uniqueness constraint or merge historical Peer rows.

It does not create a new duplicate through supported owner or inbound flows.

An ambiguous legacy match returns a conflict. It does not choose a history automatically.

## 9. Owner and read-only surfaces

### 9.1 Manage Peers

Manage Peers presents these groups:

- incoming initial pairing requests;
- sent initial pairing requests;
- active and broken Peers;
- revoked Peers.

Actions depend on the current row:

| Row | Actions |
|---|---|
| Active | Rename, Revoke |
| Broken without reconnect | Rename, Reconnect, Revoke |
| Revoked without reconnect | Rename, Reconnect |
| Reconnect sent | Retry, Verify, Cancel |
| Reconnect received | Accept after remote verification, Refuse |

The dialog explains the manual Cancel-then-Retry recovery for simultaneous sent attempts.

### 9.2 Inbox

The default inbox excludes messages owned by revoked Peers.

An explicit revoked-Peer selection shows that Peer's retained history.

The global pending badge excludes pending inbound messages owned by revoked Peers.

The existing search, result cap, threading, delivery, and attachment contracts remain unchanged.

### 9.3 Agent-facing Peer reads

Read-only agent Peer lists exclude revoked Peers.

They retain broken Peers and expose the available broken reason.

Peer send rejects every non-active Peer and every local-origin mismatch.

This feature adds no agent, MCP, CLI, or RPC action for Revoke, Reconnect, Cancel, Accept, or Refuse.

## 10. Security and concurrency

### 10.1 Credential invalidation

Revoke and local-address invalidation clear credentials under the database write lock.

Inbound messages, status callbacks, verify calls, and accept callbacks require both a matching token and an allowed state.

Reconnect cancellation requires the provisional token and clears only the matching received attempt.

A provisional reconnect token cannot authorize a message because its Peer is not active.

An old token cannot reactivate a Peer after its reconnect attempt was cleared.

Tokens never enter owner serializers, WebSocket frames, CLI output, or logs.

### 10.2 Local races

Revoke and inbound message persistence serialize on the database write lock.

Whichever commits first defines whether the inbound message is stored.

An outbound message already sent before Revoke is not recalled.

The backend checks the current Peer again before every post-network promotion.

A stale local action returns a conflict or idempotent success. It does not mutate a replacement attempt.

### 10.3 Deliberate limits

The system does not provide permanent harassment prevention by remote origin.

The system does not guarantee convergence for two simultaneous reconnect starts.

The system does not recover a lost browser mutation result without an owner reload.

These limits do not permit invalid credentials or destructive history loss.

## 11. Verification contract

### 11.1 Backend tests

Backend tests exercise Revoke against active, broken, revoked, and initial-pending rows.

They fail if history is deleted, a pending message changes status, or an old token remains usable.

Reconnect tests exercise sent, received, Retry, Cancel, Refuse, Verify, Accept, and promotion.

They fail if Retry changes the token, success creates a second local Peer, or a late callback restores a cleared attempt.

Lost-response tests replay Verify, Accept, and Cancel. They fail if either side becomes permanently unable to complete.

Address tests exercise migration safety, empty-to-valid, valid-to-empty, valid-to-different, and reapplication after interruption.

They fail if an old credential remains usable or a previously broken or revoked Peer is misclassified.

Origin tests exercise canonical matching and the new-origin initial pairing path.

They fail if remote history merges or a supported flow creates a same-origin duplicate.

### 11.2 Frontend tests

Frontend tests exercise Peer grouping, action visibility, revoked inbox filtering, and badge exclusion.

They fail if a revoked message appears in the default inbox or if a reconnect action appears for an active Peer.

The local-network test rejects a mutation `fetch()`, then completes Reload peers.

It fails if the mutation is replayed or the authoritative list does not replace the store.

### 11.3 Integration verification

A two-instance smoke test performs initial pairing, Revoke, same-origin Reconnect, and message exchange with fresh credentials.

It also performs a local address change and verifies that the remote instance receives a new initial Peer.

The smoke test fails if old credentials work, local history moves to a different row, or remote history merges.

The project full backend suite, frontend suite, lint, and production build remain the delivery gate.

## 12. Out of scope

- message-status reconciliation after reconnect;
- a manual remote-status refresh endpoint;
- retransmission of an existing Peer message;
- automatic reconnect retries;
- automatic reconnect batches after `peerBaseUrl` changes;
- crossed reconnect merge or deterministic initiator selection;
- remote-address replacement claims or associations;
- history transfer or merge between remote origins;
- a permanent block list for reconnect requests;
- multi-tab action ordering or row authority;
- a transition table, marker file, or startup repair;
- behavior after direct manual edits to synchronized settings files;
- Peer administration through agents, MCP, CLI, or RPC;
- changes to generic drop-request transport, acknowledgement words, or timeout rules.
