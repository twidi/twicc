# Peer Message Post-Implementation Fixes — Design

**Baseline:** `peer-system` at `c225da0a`, after the peer-threading implementation lots.

## 0. Relationship to earlier designs

`docs/plans/2026-07-24-peer-messaging-design.md` is the frozen founding design.
`docs/plans/2026-08-11-peer-threading-design.md` is the validated threading design.
Each document records the system and decisions at its validation time. Later planning and implementation do not make its implementation-status statements stale metadata. Neither document changes in this work.

The receiving human's read-before-delivery gate remains the prompt-injection boundary. A peer message does not reach an agent until the human reads it and chooses delivery. Delivery still creates an unsent draft. Refusal still resolves without delivery.

## 1. Scope

This design decides four corrections:

1. A session with a non-null `parent_session_id` cannot become a peer-message delivery target through by-id pagination recovery, direct delivery, or late linking.
2. The peer review dialog exposes its existing in-flight action state, prevents manual closure during that state, and gives its resolution request a 40-second deadline.
3. Inbound attachment validation checks the complete Base64 value before storage.
4. The `twicc-peer-message` skill states the complete peer-message wire contract and receives the required plugin patch-version bump.

The implementation is one lot. It changes no schema, migration, wire field, REST route, delivery status, thread identity, message-id grammar, or unique constraint.

## 2. Current implementation

This section contains claims about the baseline. Later sections contain decisions.

### 2.1 Delivery-target eligibility

`frontend/src/stores/data.js::getProjectSessions` and `getAllSessions` exclude sessions whose `parent_session_id` is non-null. `frontend/src/components/peer/PeerMessageReviewDialog.vue::buildSessionRows` derives its normal delivery candidates from those session blocks and applies `frontend/src/utils/peerReplyTarget.js::isReplyTargetPickerEligible`.

`isReplyTargetPickerEligible` excludes a missing row, `hidden`, `draft`, `archived`, and rows in archived projects. It does not inspect `parent_session_id`. `recoverReplyTargetPagination` can therefore insert a by-id-loaded internal session that the normal session getters omitted.

`src/twicc/core/services/peer_messages.py::mark_delivered` and `link_delivered_session` check only whether the target `Session` id exists before they assign `delivered_to_session`. Neither service checks `parent_session_id`.

### 2.2 Review-dialog actions

`PeerMessageReviewDialog.vue` is the app-wide review dialog mounted by `frontend/src/App.vue`. Its `busy` ref is true while `deliverToSession`, the post-trust part of `deliverToNewSession`, or `refuse` runs. Delivery and refusal buttons use it for some disabled states.

The footer `Close` button remains enabled while `busy` is true. `PeerMessageReviewDialog.vue::onHide` emits `close` for the dialog's own `wa-hide` event and does not veto Escape or the header close button. The dialog does not enable Web Awesome `light-dismiss`, so a backdrop click already leaves it open. The template shows no in-flight spinner or action label.

`markDelivered` and `refuse` call `apiFetch` without an abort signal or frontend deadline. `src/twicc/peer/outbound.py::OUTBOUND_TIMEOUT_SECONDS` gives the best-effort remote status callback a 30-second timeout after the backend records the local resolution.

Several action continuations read live component state after an await. The product owner accepts programmatic replacement of the reviewed message during `busy` as undefined behaviour under §5.5.

### 2.3 Inbound Base64 validation

`src/twicc/core/services/peer_messages.py::_validate_inbound_payload` delegates each attachment block to `_valid_block`. For a Base64 source, `_valid_block` applies strict decoding only to the first eight characters after locally adding padding. A valid prefix followed by invalid characters can therefore pass validation and be stored.

The same module defines `PEER_ATTACHMENT_MAX_BYTES_PER_FILE` as 5 MiB, `PEER_ATTACHMENT_MAX_TOTAL_BYTES` as 32 MiB, and `PEER_ATTACHMENT_MAX_FILES` as 100. `_block_decoded_size` estimates Base64 bytes from encoded length. `frontend/src/components/peer/PeerMessageReviewDialog.vue::blockToFile` later decodes the complete stored value with `atob`.

### 2.4 Agent skill

`src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md` says local session references do not reach the peer. Its next clause says the wire carries `title`, `sent_at`, and `payload`, and nothing else. `src/twicc/peer/outbound.py::post_message` also sends `message_id`, `reply_to`, and an `origin` object containing `sent_at`.

`src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` has version `0.69.0`. `tests/test_twicc_share_skill.py::test_twicc_share_skill_contract` reads `twicc-share/SKILL.md` and the plugin manifest. It asserts that version. No test reads the bundled peer-message skill. The plugin README requires a version bump for every bundled `SKILL.md` change.

### 2.5 Frontend test surface

Frontend tests use `node:test` through `frontend/src/**/*.test.js`. The repository has no Vue component test harness. `frontend/src/utils/peerReplyTarget.test.js` tests the pure reply-target helpers.

## 3. Decisions

| ID | Decision | Definition | Verification |
|---|---|---|---|
| D1 | A session with non-null `parent_session_id` is never a peer delivery target | §4 | §10.1, §10.3 |
| D2 | Normal candidates and by-id pagination recovery use the same non-pagination eligibility predicate | §4 | §10.1 |
| D3 | `PeerMessageReviewDialog.vue::busy` remains the only in-flight action lock; the action label reuses `confirmingRefuse` | §5 | §10.2 |
| D4 | While `busy`, the dialog shows the action and prevents manual closure | §5 | §10.2 |
| D5 | Deliver and refuse requests have a 40-second frontend deadline and one timeout callout | §5 | §10.2 |
| D6 | The inbound validator checks the complete bounded Base64 value before storage | §6 | §10.3 |
| D7 | The peer-message skill names every peer-message wire field and the plugin version becomes `0.69.1` | §7 | §10.4 |
| D8 | Earlier design documents and pre-existing malformed rows are not revised | §0, §8 | §10.5 |

## 4. Delivery-target eligibility

`frontend/src/utils/peerReplyTarget.js::isReplyTargetPickerEligible` remains the shared non-pagination predicate for normal delivery candidates and `recoverReplyTargetPagination`.

| Hydrated row state | Eligibility |
|---|---|
| Ordinary human-controlled session | eligible |
| Non-null `parent_session_id` | ineligible |
| `hidden` | ineligible |
| `draft` | ineligible |
| `archived` | ineligible |
| Project id in the archived-project set | ineligible |
| Worktree row produced by the normal explicit scope | eligible |
| Stale-project row produced by the normal explicit scope | eligible |
| Missing row | ineligible |

An internal session is controlled only by its parent agent. The receiving human cannot select it as a standalone destination. A by-id load does not make it reachable.

`src/twicc/core/services/peer_messages.py::mark_delivered` and `link_delivered_session` treat an internal target like a missing target before either service can assign `delivered_to_session`. Their target queries require `parent_session_id__isnull=True`. Each service returns the existing `session_not_found` error and leaves the message status and `delivered_to_session` unchanged.

Pagination recovery preserves its existing rules. It inserts one eligible page-omitted target at the normal sorted position. An existing or ineligible target returns the exact input array reference. It does not reorder normal candidates or add a project-list membership rule.

The existing pending-reply warning remains generic. It does not identify `parent_session_id`, hidden state, deletion, archival, or any other reason for an unavailable target.

## 5. Review-dialog in-flight state

### 5.1 State ownership

`PeerMessageReviewDialog.vue::busy` is the only action lock. It exists in the mounted dialog instance and is local to one browser tab.

This work adds no action state to Pinia, `App.vue`, a composable, browser storage, or the backend. Separate tabs do not coordinate their peer actions. Existing backend resolution guards arbitrate requests that reach the same message.

### 5.2 Start and end

For delivery to an existing session, `busy` begins immediately before the deliver request. For delivery to a new session, the trust gate remains first; cancellation leaves the message pending, and `busy` begins immediately before the deliver request. For refusal, `busy` begins immediately before the refuse request.

Immediately before either delivery path sets `busy`, it clears `confirmingRefuse`. For refusal, `confirmingRefuse` is true before `busy` begins and remains true until after `busy` clears. No resolution control changes `confirmingRefuse` while `busy` is true.

`busy` remains true while the response body and any successful local draft preparation complete. A successful delivery clears `busy` before the existing close-and-navigation flow. A successful refusal clears `busy` before the existing close flow. A request or draft-preparation error clears `busy`, keeps the dialog open, and uses the existing danger-callout surface.

### 5.3 Visible state and manual closure

While `busy` is true, the action area shows a Web Awesome spinner and one label:

- `Delivering…` for either delivery path;
- `Refusing…` for refusal.

The label derives from the existing `confirmingRefuse` state. It adds no second in-flight state beside `busy`.

Every resolution control is disabled while `busy` is true. These controls are the two mode buttons, both delivery action buttons, the initial Refuse button, the confirmed Refuse button, and Keep. The footer `Close` button is disabled.

For the dialog's own `wa-hide` event, `PeerMessageReviewDialog.vue::onHide` calls `preventDefault()` while `busy` is true. The handler first verifies that the event target is the dialog itself. Escape and the header close button therefore do not close the dialog. The backdrop remains non-closing because the dialog still does not enable `light-dismiss`. Bubbling `wa-hide` events from nested Web Awesome controls do not change dialog state.

### 5.4 Resolution-request deadline

Each deliver or refuse request receives a 40-second frontend deadline. The deadline covers the fetch and response-body read. It does not apply to the human's time in the trust gate or to local draft preparation after a successful response.

When the deadline expires, the client aborts its wait, clears `busy`, keeps the dialog open, and shows one `wa-callout` with `variant="danger"`:

> The request did not complete in time. Refresh before trying again.

The timeout path does not query the message again. It does not claim that the backend kept or changed the message status. It does not create a toast.

### 5.5 Deliberately undefined replacement behaviour

Programmatic replacement of `props.messageId` while `busy` is true is deliberately undefined. This work does not snapshot action inputs. It does not prevent a toast, inbox event, or another `App.vue` path from changing the reviewed message. It does not define which dialog closes, which route opens, or which live state an action continuation reads after such a replacement.

Review and implementation must not add guards outside `PeerMessageReviewDialog.vue`, add an action coordinator, change toast or inbox opening, queue peer actions, or define the replacement result. The owner accepts this rare state. A page refresh remains the escape from a frontend request that does not settle normally.

## 6. Complete Base64 validation

The inbound peer endpoint accepts standard padded Base64 only. Its alphabet is `A-Z`, `a-z`, `0-9`, `+`, and `/`, followed by zero, one, or two required `=` padding characters at the end. Whitespace, URL-safe `-` or `_`, misplaced padding, missing required padding, and any other character are invalid.

Before decoding, validation compares the encoded length with the maximum possible standard-Base64 length for `PEER_ATTACHMENT_MAX_BYTES_PER_FILE`. A longer value receives the existing per-file size error without allocating decoded bytes.

For a value within that bound, validation strictly decodes the complete string once. A decode failure adds the existing attachment `invalid_block` error. `receive_peer_message` returns `400 {"error": "invalid_payload"}` and stores no row when any validation error exists.

The decoded byte length enforces the 5 MiB per-file and 32 MiB total caps. The 100-file cap remains. Text-source validation remains unchanged.

| Base64 source state | Result |
|---|---|
| Valid standard padded value within the caps | accepted |
| Valid prefix with invalid tail, including `QUJDREVG!!!!` | `400 invalid_payload`; no row |
| Character outside the standard alphabet | `400 invalid_payload`; no row |
| Missing, excess, or misplaced padding | `400 invalid_payload`; no row |
| Empty string | `400 invalid_payload`; no row |
| Decoded file larger than 5 MiB | `400 invalid_payload`; no row |
| Valid files whose decoded total exceeds 32 MiB | `400 invalid_payload`; no row |
| More than 100 valid files | `400 invalid_payload`; no row |

## 7. `twicc-peer-message` skill contract

`src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md` replaces the incomplete wire sentence with this meaning:

- `origin_session` and `delivered_to_session` remain local and never cross the wire;
- the peer-message wire carries `message_id`, `title`, `reply_to`, `origin.sent_at`, and `payload`;
- `reply_to` is the empty string for a root message and a conforming answered message id for a reply.

The skill does not describe `thread_id`, `reply_to_ref`, or `reply_target` as wire fields. Those values remain local serialization results.

The bundled skill change increments `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` from `0.69.0` to `0.69.1`. The existing `tests/test_twicc_share_skill.py::test_twicc_share_skill_contract` keeps all of its share-skill contract assertions; only its plugin-version assertion changes. A new `tests/test_twicc_peer_message_skill.py::test_twicc_peer_message_skill_contract` verifies the peer-message skill contract and the same plugin version.

## 8. Out of scope

- Editing `docs/plans/2026-07-24-peer-messaging-design.md` or `docs/plans/2026-08-11-peer-threading-design.md`. Their implementation-status text is historical, not mutable release metadata.
- Migrating, scanning, repairing, deleting, or adding compatibility behaviour for malformed peer-message rows already stored in development data.
- Defining programmatic reviewed-message replacement during `busy`, as specified in §5.5.
- Adding a Vue component test harness.
- Changing the read-before-delivery gate, delivery from a toast, automatic reply routing, or automatic draft sending.
- Reordering the delivery picker or changing its explicit project-scope rules.
- Grouping inbox rows by thread.
- Changing the `("peer", "direction", "message_id")` unique constraint.
- Changing the message-id grammar `[A-Za-z0-9_][A-Za-z0-9_-]{0,39}` or its command-line leading-hyphen rationale.
- Changing attachment count or byte caps.
- Changing peer REST routes, wire fields, resolution callbacks, or thread resolution.
- Modifying any bundled skill other than `twicc-peer-message`.

## 9. Edge cases

| Case | Decision |
|---|---|
| By-id loader or backend service receives a session with non-null `parent_session_id` | It remains absent from candidate rows and cannot be pre-selected, delivered to, or late-linked. |
| An internal target belongs to a live project and has no other exclusion | `parent_session_id` alone makes it ineligible. |
| An unavailable pending-reply target is internal | The existing generic warning appears; it does not name the reason. |
| User presses Escape or clicks the header close button during a normal in-flight action | The dialog vetoes the close and shows the spinner. |
| User clicks the backdrop during a normal in-flight action | The existing non-`light-dismiss` dialog remains open and shows the spinner. |
| User clicks footer `Close` during a normal in-flight action | The disabled button performs no action. |
| Deliver or refuse responds before 40 seconds | The existing success or error flow runs. |
| Deliver or refuse has not completed at 40 seconds | The client aborts its wait, shows the timeout callout, and makes the dialog manually closable. |
| Backend resolved the message but the frontend deadline expired | No reconciliation occurs. The callout asks for a refresh before retrying. |
| Trust decision takes more than 40 seconds | No peer-resolution deadline is active yet. The trust flow continues normally. |
| Local attachment preparation takes more than 40 seconds after a successful deliver response | The network deadline has ended; `busy` remains true until preparation succeeds or fails. |
| Another tab resolves the same message | Existing backend guards decide the second request. No cross-tab frontend lock exists. |
| `props.messageId` changes programmatically during `busy` | Behaviour is undefined under §5.5. |
| Base64 prefix is valid and its tail contains `!` | Ingress rejects the message and stores no row. |
| Standard Base64 omits required padding | Ingress rejects the message and stores no row. |
| Valid Base64 decodes exactly to the per-file cap | Ingress accepts that file when the total and count caps also pass. |
| Skill describes a root message | It states that `reply_to` crosses as an empty string. |

## 10. Verification means

### 10.1 Pure frontend helper tests

`frontend/src/utils/peerReplyTarget.test.js`, run by `cd frontend && npm test`, adds a non-null `parent_session_id` row to the ineligible-state table. It also passes that row to `recoverReplyTargetPagination` and requires the exact candidate-array reference back. This catches a by-id path that makes an internal session selectable; the broken result is `true` eligibility or an inserted row.

### 10.2 Manual dialog checks

The project has no Vue component test harness. A browser check delays each resolution endpoint below and above 40 seconds.

For a response below the deadline, the check observes the action-specific spinner, disabled closure, and the existing success flow. A broken implementation closes on Escape, the header close button, backdrop, or footer `Close`, omits the spinner, enables another resolution action, or fails to continue after the response.

To discriminate the action label, the check opens the refusal confirmation and then starts each delivery path. Each spinner says `Delivering…`, and the refusal confirmation clears before `busy` begins. During a delayed refusal, Keep and every other resolution control remain disabled, `confirmingRefuse` remains true, and the spinner continues to say `Refusing…`. A broken implementation shows the opposite label or lets Keep change it.

For a response beyond the deadline, the check observes the exact timeout callout and a manually closable dialog after the deadline. A broken implementation waits without a deadline, closes before the deadline, claims a backend result, reloads automatically, or remains locked after the callout.

No manual check changes `props.messageId` programmatically during `busy`. Section 5.5 leaves that result undefined.

### 10.3 Backend service and payload tests

`tests/test_peer_messages.py`, run by the focused pytest suite, submits `QUJDREVG!!!!` in an inbound Base64 block and requires `400 invalid_payload` with no `PeerMessage` row. A broken validator accepts the valid prefix and stores the row.

The same test surface adds a genuinely padded valid case by passing `_image_block(b"a")`, whose encoded value is `YQ==`, and requires it to store one row.

It adds decoded-size boundary cases for an exact per-file cap and one byte over it, an exact total cap and one byte over it, and exactly 100 files versus 101 files. The tests may monkeypatch byte caps to small values. Accepted boundaries store one row. Rejected boundaries return `400 invalid_payload` and store no row. A broken validator rejects an exact boundary, accepts an over-limit payload, or rejects valid standard padding.

Backend service tests pass a `Session` with non-null `parent_session_id` to `mark_delivered` and to `link_delivered_session` while its delivered message has no linked target. Both calls return `session_not_found`. The first message remains pending with unchanged `delivered_to_session`; the second remains delivered with unchanged `delivered_to_session`. A broken service accepts the internal row or mutates either field.

### 10.4 Skill contract tests

Keep `tests/test_twicc_share_skill.py::test_twicc_share_skill_contract`, changing only its plugin-version assertion from `0.69.0` to `0.69.1`.

Add `tests/test_twicc_peer_message_skill.py::test_twicc_peer_message_skill_contract`, run by the focused pytest suite. It reads the bundled peer-message skill and plugin manifest. It requires the corrected wire sentence to name `message_id`, `title`, `reply_to`, `origin.sent_at`, and `payload`; to keep `origin_session` and `delivered_to_session` local; to exclude `thread_id`, `reply_to_ref`, and `reply_target` from the wire; and to state that a root message uses an empty `reply_to`. It also requires plugin version `0.69.1`. A broken update preserves the incomplete wire sentence, describes a local-only value as wire data, changes an existing share-skill assertion, or leaves the cache version at `0.69.0`.

### 10.5 Scope inspection

The implementation diff must omit both earlier design documents, schema and migration files, REST route declarations, thread-resolution code, message-id grammar, and skills other than `twicc-peer-message`. A broken implementation changes one of those named surfaces.

## 11. Implementation lot

| Lot | Contents |
|---|---|
| 1 | Update reply-target eligibility and its pure tests; enforce the internal-session target rule in `mark_delivered` and `link_delivered_session` with backend tests; add the dialog spinner, manual-close veto, 40-second resolution deadline and timeout callout; validate complete bounded Base64 input and add backend tests; correct `twicc-peer-message`, bump the plugin patch version, add its dedicated contract test, and update only the manifest-version assertion in the existing share-skill contract test. |
