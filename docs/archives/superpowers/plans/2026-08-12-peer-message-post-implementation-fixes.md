# Peer Message Post-Implementation Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/plans/2026-08-12-peer-message-post-implementation-fixes-design.md` at commit `9d64d2ea0c2ea256c1abbbda5d6092c3404d5675` is the authority. This plan implements its single lot.

**Goal:** Close four peer-message defects: internal-session delivery targets, incomplete Base64 validation, hidden dialog action progress, and an incomplete peer-message skill contract.

**Architecture:** One shared frontend predicate and both backend assignment services enforce the same delivery-target boundary. The inbound service strictly decodes each bounded Base64 value before storage. `PeerMessageReviewDialog.vue` keeps `busy` as its only action lock and adds one abortable request helper. The bundled peer-message skill states the wire boundary and gets a patch-version bump.

**Tech Stack:** Django 6, Python 3.13, pytest, Vue 3 Composition API, Pinia 3, Vite 7, Web Awesome 3.3, Node `node:test`.

## Global Constraints

- **Lot boundary:** implement only the single lot in design §11. Do not add adjacent peer-message features.
- **Worktree:** every command starts with `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && `. Never read or write `/home/twidi/dev/twicc-poc` for this work.
- **Authority:** keep the decisions in `docs/plans/2026-08-12-peer-message-post-implementation-fixes-design.md` settled. Do not reopen them during implementation.
- **Historical designs:** never edit `docs/plans/2026-07-24-peer-messaging-design.md` or `docs/plans/2026-08-11-peer-threading-design.md`.
- **Target boundary:** a `Session` with non-null `parent_session_id` is not human-controlled. It is never a peer delivery target through normal candidates, pagination recovery, direct delivery, or late linking.
- **Target error:** backend target rejection uses the existing `session_not_found` error. It leaves status and `delivered_to_session` unchanged.
- **Picker behavior:** keep the existing order, pagination recovery, explicit project scopes, and generic unavailable-target warning. Do not add project-list membership to eligibility.
- **Dialog state:** keep `busy` as the only in-flight action lock. Reuse `confirmingRefuse` for the action label. Do not add Pinia state, a composable, an action coordinator, or browser-storage state.
- **Dialog scope:** do not change `App.vue`, toast behavior, inbox behavior, routing policy, or automatic delivery. Do not define programmatic `props.messageId` replacement during `busy`.
- **Dialog deadline:** apply 40 seconds only to the deliver or refuse fetch and response-body read. Do not include the trust gate or local draft preparation.
- **Timeout result:** use exactly `The request did not complete in time. Refresh before trying again.` Do not reconcile, reload, create a toast, or claim a backend result.
- **Base64 contract:** accept only complete strict standard Base64. Preserve the 5 MiB per-file, 32 MiB total, and 100-file caps. Do not repair existing malformed rows.
- **Wire contract:** do not add or remove wire fields. The skill names `message_id`, `title`, `reply_to`, `origin.sent_at`, and `payload` as wire data. It keeps local serialization values local.
- **Plugin bundle:** modify only `twicc-peer-message/SKILL.md` in the skill bundle. Bump the plugin from `0.69.0` to `0.69.1`.
- **Share test:** preserve every existing share-skill assertion. Change only its manifest-version assertion.
- **Frontend tests:** use `node:test` through `frontend/src/**/*.test.js`. Do not add a Vue component test harness.
- **Database invariants:** do not change schema, migrations, REST routes, thread resolution, `("peer", "direction", "message_id")`, or `[A-Za-z0-9_][A-Za-z0-9_-]{0,39}`.
- **Dependencies:** do not install packages. Use `uv run` for project dependencies. Ruff is absent from project dependencies, so use `uvx` for focused Ruff checks.
- **Language:** all code, comments, tests, UI text, and commit subjects are English.
- **Commits:** create one commit per task. Follow `AGENTS.md` and `CLAUDE.md` for the commit body and trailer.

## Existing Interfaces

- Produces: `apiFetch(url: string, options?: RequestInit) -> Promise<Response>`; it passes `options.signal` to browser `fetch`.
- Produces: `PeerMessageReviewDialog.vue action state: busy: Ref<boolean>; confirmingRefuse: Ref<boolean>; actionError: Ref<string>`.
- Produces: `attachment caps: PEER_ATTACHMENT_MAX_BYTES_PER_FILE = 5 MiB; PEER_ATTACHMENT_MAX_TOTAL_BYTES = 32 MiB; PEER_ATTACHMENT_MAX_FILES = 100`.
- Produces: `backend target failure: PeerError("session_id", "session_not_found", "Target session not found.")`.
- Produces: `plugin manifest version before this lot: 0.69.0`.

## Task Map

| Task | Deliverable | Depends on |
|---|---|---|
| 1 | Shared frontend and backend internal-target exclusion | — |
| 2 | Complete bounded Base64 validation and boundary tests | 1 |
| 3 | Visible, non-dismissible, deadline-bound dialog actions | 1 |
| 4 | Correct peer-message skill contract and plugin patch version | — |

---

### Task 1: Exclude internal sessions from every delivery-target path

**Files:**
- Modify: `frontend/src/utils/peerReplyTarget.js`
- Modify: `frontend/src/utils/peerReplyTarget.test.js`
- Modify: `src/twicc/core/services/peer_messages.py`
- Modify: `tests/test_peer_messages.py`

**Interfaces:**
- Consumes: `backend target failure: PeerError("session_id", "session_not_found", "Target session not found.")` from Existing Interfaces.
- Produces: `isReplyTargetPickerEligible(session: Object | null, archivedProjectIds: Set<string>) -> boolean; rejects non-null parent_session_id plus existing exclusions`.
- Produces: `eligible backend target query: Session.objects.filter(id=session_id, parent_session_id__isnull=True).exists()`.

- [ ] **Step 1: Add the failing pure frontend cases**

In `frontend/src/utils/peerReplyTarget.test.js`, add `parent_session_id: null` to the `session()` fixture:

```javascript
function session(id, overrides = {}) {
    return {
        id,
        project_id: 'project-live',
        parent_session_id: null,
        hidden: false,
        draft: false,
        archived: false,
        mtime: 0,
        ...overrides,
    }
}
```

In `matches the unpaged picker exclusions without a project-list rule`, add:

```javascript
assert.equal(isReplyTargetPickerEligible(
    session('internal', { parent_session_id: 'parent-session' }),
    archivedProjectIds,
), false)
```

In `leaves existing and ineligible candidate arrays unchanged`, add:

```javascript
assert.strictEqual(
    recoverReplyTargetPagination(
        candidates,
        session('internal-target', { parent_session_id: 'parent-session' }),
        archivedProjectIds,
        compareSessions,
    ),
    candidates,
)
```

- [ ] **Step 2: Run the focused frontend test and verify the new cases fail**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/utils/peerReplyTarget.test.js
```

Expected: FAIL because the current predicate accepts the row with non-null `parent_session_id`. The recovery assertion also fails because it receives a new array containing that row.

- [ ] **Step 3: Add failing backend service tests**

After `_make_target_session` in `tests/test_peer_messages.py`, add:

```python
def _make_internal_target_session():
    project, parent = _make_target_session()
    now = djtz.now()
    internal = Session.objects.create(
        id="sess-internal-target",
        project=project,
        provider="claude_code",
        file_path="internal-target.jsonl",
        type=SessionType.SUBAGENT,
        parent_session=parent,
        title="Internal target",
        created_at=now,
        last_new_content_at=now,
    )
    return internal
```

After `test_deliver_guards`, add:

```python
def test_mark_delivered_rejects_internal_target(transactional_db, status_callbacks):
    peer = _active_peer()
    message = _in_message(peer, message_id="pm_internal_target")
    internal = _make_internal_target_session()

    success, envelope, errors = _run(peer_messages.mark_delivered(
        message, session_id=internal.id, note="",
    ))

    assert not success and envelope is None
    assert errors[0].code == "session_not_found"
    message.refresh_from_db()
    assert message.status == PeerMessageStatus.PENDING
    assert message.delivered_to_session_id is None
    assert status_callbacks == []
```

After `test_link_delivered_session_guards`, add:

```python
def test_link_delivered_session_rejects_internal_target(transactional_db, status_callbacks):
    peer = _active_peer()
    message = _in_message(
        peer,
        message_id="pm_internal_link",
        status=PeerMessageStatus.DELIVERED,
        resolved_at=djtz.now(),
    )
    internal = _make_internal_target_session()

    success, errors = _run(peer_messages.link_delivered_session(message, internal.id))

    assert not success and errors[0].code == "session_not_found"
    message.refresh_from_db()
    assert message.status == PeerMessageStatus.DELIVERED
    assert message.delivered_to_session_id is None
    assert status_callbacks == []
```

- [ ] **Step 4: Run the focused backend tests and verify they fail**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_messages.py -k 'internal_target' -q
```

Expected: FAIL. `mark_delivered` accepts the internal row and changes the first message to delivered. `link_delivered_session` accepts it and writes the second link.

- [ ] **Step 5: Update the shared frontend predicate**

In `frontend/src/utils/peerReplyTarget.js`, replace `isReplyTargetPickerEligible` with:

```javascript
export function isReplyTargetPickerEligible(session, archivedProjectIds) {
    return !!session
        && !session.parent_session_id
        && !session.hidden
        && !session.draft
        && !session.archived
        && !archivedProjectIds.has(session.project_id)
}
```

Do not add a project-membership or session-type rule.

- [ ] **Step 6: Update both backend target queries**

In `mark_delivered`, replace the existence query with:

```python
exists = await sync_to_async(
    lambda: Session.objects.filter(
        id=session_id,
        parent_session_id__isnull=True,
    ).exists()
)()
```

In `link_delivered_session`, replace its existence query with:

```python
exists = await sync_to_async(
    lambda: Session.objects.filter(
        id=session_id,
        parent_session_id__isnull=True,
    ).exists()
)()
```

Keep the existing `session_not_found` branches unchanged.

- [ ] **Step 7: Run Task 1 verification**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/utils/peerReplyTarget.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_messages.py -k 'deliver or link_delivered_session' -q
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uvx ruff check --select E4,E7,E9,F src/twicc/core/services/peer_messages.py tests/test_peer_messages.py
```

Expected: all commands PASS. The Node file still reports 5 tests because the new assertions extend existing cases.

- [ ] **Step 8: Commit Task 1**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Commit the changes produced by this task.

Commit subject:

```text
fix(peer): reject internal delivery targets
```

---

### Task 2: Validate complete bounded Base64 input

**Files:**
- Modify: `src/twicc/core/services/peer_messages.py`
- Modify: `tests/test_peer_messages.py`

**Interfaces:**
- Consumes: `eligible backend target query: Session.objects.filter(id=session_id, parent_session_id__isnull=True).exists()` from Task 1.
- Consumes: `attachment caps: PEER_ATTACHMENT_MAX_BYTES_PER_FILE = 5 MiB; PEER_ATTACHMENT_MAX_TOTAL_BYTES = 32 MiB; PEER_ATTACHMENT_MAX_FILES = 100` from Existing Interfaces.
- Produces: `_validated_block_size(block: object) -> int | None`; `None` means malformed, and `PEER_ATTACHMENT_MAX_BYTES_PER_FILE + 1` is the bounded oversize sentinel.
- Produces: `_block_decoded_size(block: dict) -> int` returns the exact standard-Base64 decoded size after validation.

- [ ] **Step 1: Add the failing invalid-tail and padded-valid tests**

After `test_receive_invalid_payloads` in `tests/test_peer_messages.py`, add:

```python
def test_receive_rejects_invalid_base64_tail(client, transactional_db, peer_host):
    peer = _active_peer()
    block = _image_block()
    block["source"]["data"] = "QUJDREVG!!!!"
    body = _wire_body(payload={
        "text": "x", "images": [block], "documents": [],
    })

    res = _post(client, "/peer/messages/", body, bearer=peer.token_ours)

    assert res.status_code == 400
    assert orjson.loads(res.content) == {"error": "invalid_payload"}
    assert PeerMessage.objects.count() == 0


def test_receive_accepts_valid_padded_base64(client, transactional_db, peer_host):
    peer = _active_peer()
    block = _image_block(b"a")
    assert block["source"]["data"] == "YQ=="
    body = _wire_body(payload={
        "text": "x", "images": [block], "documents": [],
    })

    res = _post(client, "/peer/messages/", body, bearer=peer.token_ours)

    assert res.status_code == 202
    message = PeerMessage.objects.get()
    assert message.attachments_meta[0]["bytes"] == 1
```

- [ ] **Step 2: Add the failing decoded boundary matrix**

After the two tests from Step 1, add:

```python
@pytest.mark.parametrize(("size", "expected_status"), [(4, 202), (5, 400)])
def test_receive_attachment_per_file_boundaries(
        client, transactional_db, peer_host, monkeypatch, size, expected_status):
    monkeypatch.setattr(peer_messages, "PEER_ATTACHMENT_MAX_BYTES_PER_FILE", 4)
    peer = _active_peer()
    body = _wire_body(payload={
        "text": "x", "images": [_image_block(b"x" * size)], "documents": [],
    })

    res = _post(client, "/peer/messages/", body, bearer=peer.token_ours)

    assert res.status_code == expected_status
    assert PeerMessage.objects.count() == (1 if expected_status == 202 else 0)


@pytest.mark.parametrize(("sizes", "expected_status"), [((3, 3), 202), ((3, 4), 400)])
def test_receive_attachment_total_boundaries(
        client, transactional_db, peer_host, monkeypatch, sizes, expected_status):
    monkeypatch.setattr(peer_messages, "PEER_ATTACHMENT_MAX_TOTAL_BYTES", 6)
    peer = _active_peer()
    body = _wire_body(payload={
        "text": "x",
        "images": [_image_block(b"x" * size) for size in sizes],
        "documents": [],
    })

    res = _post(client, "/peer/messages/", body, bearer=peer.token_ours)

    assert res.status_code == expected_status
    assert PeerMessage.objects.count() == (1 if expected_status == 202 else 0)


@pytest.mark.parametrize(("count", "expected_status"), [(100, 202), (101, 400)])
def test_receive_attachment_count_boundaries(
        client, transactional_db, peer_host, count, expected_status):
    peer = _active_peer()
    body = _wire_body(payload={
        "text": "x",
        "images": [_image_block(b"x") for _ in range(count)],
        "documents": [],
    })

    res = _post(client, "/peer/messages/", body, bearer=peer.token_ours)

    assert res.status_code == expected_status
    assert PeerMessage.objects.count() == (1 if expected_status == 202 else 0)
```

- [ ] **Step 3: Run the new payload tests and verify the invalid-tail case fails**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_messages.py -k 'base64 or attachment_per_file_boundaries or attachment_total_boundaries or attachment_count_boundaries' -q
```

Expected: FAIL because `QUJDREVG!!!!` is accepted and stored. The exact metadata assertion can also expose the current approximate size calculation.

- [ ] **Step 4: Replace prefix validation with one bounded full decode**

Add `import binascii` beside the existing standard-library imports in `src/twicc/core/services/peer_messages.py`.

Replace `_block_decoded_size` and `_valid_block` with:

```python
def _block_decoded_size(block: dict) -> int:
    """Exact byte size after inbound validation accepted the SDK block."""
    source = block.get("source") or {}
    data = source.get("data") or ""
    if not isinstance(data, str):
        return 0
    if source.get("type") == "base64":
        padding = len(data) - len(data.rstrip("="))
        return (len(data) // 4) * 3 - padding
    return len(data.encode("utf-8"))


def _validated_block_size(block) -> int | None:
    if not isinstance(block, dict):
        return None
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    source_type = source.get("type")
    if source_type not in ("base64", "text"):
        return None
    data = source.get("data")
    if not isinstance(data, str) or not data:
        return None
    if source_type == "text":
        return len(data.encode("utf-8"))

    max_encoded_length = 4 * ((PEER_ATTACHMENT_MAX_BYTES_PER_FILE + 2) // 3)
    if len(data) > max_encoded_length:
        return PEER_ATTACHMENT_MAX_BYTES_PER_FILE + 1
    try:
        return len(base64.b64decode(data, validate=True))
    except (binascii.Error, ValueError):
        return None
```

The encoded-length check must run before `base64.b64decode`. Do not locally add padding. Do not accept URL-safe characters or whitespace.

- [ ] **Step 5: Make inbound validation consume the validated size**

In `_validate_inbound_payload`, replace:

```python
if not _valid_block(block):
    errors.append(PeerError(key, "invalid_block", f"malformed attachment block in {key}"))
    continue
size = _block_decoded_size(block)
```

with:

```python
size = _validated_block_size(block)
if size is None:
    errors.append(PeerError(key, "invalid_block", f"malformed attachment block in {key}"))
    continue
```

Keep the existing file-count, total-byte, and per-file error branches. `_attachments_meta` uses `_block_decoded_size` only after validation succeeds.

- [ ] **Step 6: Run Task 2 verification**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_messages.py -q
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uvx ruff check --select E4,E7,E9,F src/twicc/core/services/peer_messages.py tests/test_peer_messages.py
```

Expected: all peer-message tests and focused Ruff checks PASS. The new cases accept `YQ==`, exact caps, and 100 files. They reject the invalid tail and each one-over boundary without storing a row.

- [ ] **Step 7: Commit Task 2**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Commit the changes produced by this task.

Commit subject:

```text
fix(peer): validate complete base64 attachments
```

---

### Task 3: Expose and bound review-dialog resolution actions

**Files:**
- Modify: `frontend/src/components/peer/PeerMessageReviewDialog.vue`

**Interfaces:**
- Consumes: `isReplyTargetPickerEligible(session: Object | null, archivedProjectIds: Set<string>) -> boolean; rejects non-null parent_session_id plus existing exclusions` from Task 1.
- Consumes: `apiFetch(url: string, options?: RequestInit) -> Promise<Response>` from Existing Interfaces.
- Consumes: `PeerMessageReviewDialog.vue action state: busy: Ref<boolean>; confirmingRefuse: Ref<boolean>; actionError: Ref<string>` from Existing Interfaces.
- Produces: `requestPeerResolution(url: string, options?: RequestInit) -> Promise<{response: Response, payload: object | null}>` with a 40-second fetch-and-body deadline.
- Produces: `busy action label: confirmingRefuse ? 'Refusing…' : 'Delivering…'`.

- [ ] **Step 1: Add the request-deadline helper**

After `errorText` in `frontend/src/components/peer/PeerMessageReviewDialog.vue`, add:

```javascript
const PEER_RESOLUTION_TIMEOUT_MS = 40_000
const PEER_RESOLUTION_TIMEOUT_MESSAGE = 'The request did not complete in time. Refresh before trying again.'

class PeerResolutionTimeoutError extends Error {}

async function requestPeerResolution(url, options = {}) {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), PEER_RESOLUTION_TIMEOUT_MS)
    try {
        const response = await apiFetch(url, { ...options, signal: controller.signal })
        let payload = null
        try {
            payload = await response.json()
        } catch (error) {
            if (controller.signal.aborted) throw error
        }
        return { response, payload }
    } catch (error) {
        if (controller.signal.aborted) throw new PeerResolutionTimeoutError()
        throw error
    } finally {
        clearTimeout(timeoutId)
    }
}

function setActionFailure(error) {
    actionError.value = error instanceof PeerResolutionTimeoutError
        ? PEER_RESOLUTION_TIMEOUT_MESSAGE
        : 'Network error — could not reach the server.'
}
```

The inner catch must rethrow an abort. Otherwise a timeout during `response.json()` becomes a false successful empty body.

- [ ] **Step 2: Route delivery and refusal requests through the helper**

Replace `markDelivered` with:

```javascript
async function markDelivered(sessionId) {
    const { response, payload } = await requestPeerResolution(
        `/api/peer-messages/${props.messageId}/deliver/`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId || undefined,
                note: note.value,
                // Opt-in server-side: an already-delivered message is only
                // re-routed when the UI asks for it explicitly.
                redeliver: isRedeliverable.value || undefined,
            }),
        },
    )
    if (!response.ok) {
        actionError.value = errorText(payload)
        return null
    }
    return payload.envelope
}
```

In `refuse`, replace the direct `apiFetch` and response-body read with:

```javascript
const { response, payload } = await requestPeerResolution(
    `/api/peer-messages/${props.messageId}/refuse/`,
    { method: 'POST' },
)
```

Keep the existing non-OK `errorText(payload)` branch.

- [ ] **Step 3: Clear `busy` before successful close and navigation**

Replace the comment above `defineEmits`:

```javascript
// `close` carries an optional reason: 'navigating' when the dialog closes
// because the user is being sent to the delivery target (see prefillComposer).
```

with:

```javascript
// `close` carries an optional reason: 'navigating' when the dialog closes
// because the user is being sent to the delivery target (see navigateToComposer).
```

Replace the `prefillComposer` docstring:

```javascript
/** Prefill a composer (existing session's draft, or a fresh draft session)
 *  with the envelope + the peer attachments, then jump to it. Nothing is
 *  sent — the user reviews and sends through the normal pipeline. */
```

with:

```javascript
/** Prefill a composer (existing session's draft, or a fresh draft session)
 *  with the envelope + the peer attachments. Nothing is
 *  sent — the user reviews and sends through the normal pipeline. */
```

Remove the close and route calls from the end of `prefillComposer`. Add this function after it:

```javascript
function navigateToComposer(sessionId, projectId) {
    // 'navigating': the user leaves for the target session — the inbox must
    // NOT come back over the composer they are being sent to.
    emit('close', 'navigating')
    router.push(sessionRouteLocation({ id: sessionId, project_id: projectId }, route))
}
```

Replace `deliverToSession` with:

```javascript
async function deliverToSession(session) {
    actionError.value = ''
    confirmingRefuse.value = false
    busy.value = true
    let shouldNavigate = false
    try {
        envelopeText = await markDelivered(session.id)
        if (envelopeText == null) return
        await prefillComposer(session.id, session.project_id)
        shouldNavigate = true
    } catch (error) {
        setActionFailure(error)
    } finally {
        busy.value = false
    }
    if (shouldNavigate) navigateToComposer(session.id, session.project_id)
}
```

Replace `deliverToNewSession` with:

```javascript
async function deliverToNewSession(projectId) {
    // Trust gate FIRST — if the user backs out, the message must stay pending.
    const gate = await ensureProjectTrust(projectId)
    if (!gate) return
    actionError.value = ''
    confirmingRefuse.value = false
    busy.value = true
    let draftId = null
    try {
        envelopeText = await markDelivered(null)
        if (envelopeText == null) return
        draftId = dataStore.createDraftSession(projectId, gate.state)
        // The delivery was just recorded with NO target: the session does not
        // exist yet. Tie the message to the draft so the store can complete the
        // link once the provider creates the real session.
        dataStore.setDraftPeerMessage(draftId, props.messageId)
        await prefillComposer(draftId, projectId)
    } catch (error) {
        setActionFailure(error)
        draftId = null
    } finally {
        busy.value = false
    }
    if (draftId != null) navigateToComposer(draftId, projectId)
}
```

This keeps the trust gate outside the deadline and outside `busy`. It keeps local draft preparation inside `busy` but after the request deadline ends.

- [ ] **Step 4: Keep refusal identity stable until `busy` clears**

Replace `refuse` with:

```javascript
async function refuse() {
    actionError.value = ''
    busy.value = true
    let shouldClose = false
    try {
        const { response, payload } = await requestPeerResolution(
            `/api/peer-messages/${props.messageId}/refuse/`,
            { method: 'POST' },
        )
        if (!response.ok) {
            actionError.value = errorText(payload)
            return
        }
        shouldClose = true
    } catch (error) {
        setActionFailure(error)
    } finally {
        busy.value = false
        confirmingRefuse.value = false
    }
    if (shouldClose) emit('close')
}
```

Do not clear `confirmingRefuse` before or during the refusal request.

- [ ] **Step 5: Veto the dialog's own hide event while busy**

Replace `onHide` with:

```javascript
function onHide(event) {
    if (event.target !== dialogRef.value) return
    if (busy.value) {
        event.preventDefault()
        return
    }
    emit('close')
}
```

The target check must remain first. Nested Web Awesome `wa-hide` events must not control the dialog.

- [ ] **Step 6: Render one action-specific spinner and disable every resolution control**

Add `:disabled="busy"` to both delivery mode buttons.

After the `.pr-actions` block and before the refusal confirmation callout, add:

```vue
<div v-if="busy" class="pr-busy" role="status" aria-live="polite">
    <wa-spinner></wa-spinner>
    <span>{{ confirmingRefuse ? 'Refusing…' : 'Delivering…' }}</span>
</div>
```

Keep `:disabled="busy"` on the initial Refuse button, confirmed Refuse button, and both delivery action buttons. Add `:disabled="busy"` to Keep:

```vue
<wa-button
    size="small" appearance="outlined" :disabled="busy"
    @click="confirmingRefuse = false"
>Keep</wa-button>
```

Disable the footer button:

```vue
<wa-button :disabled="busy" @click="emit('close')">Close</wa-button>
```

Do not disable picker inputs. They are not resolution controls and cannot resolve a message without a disabled action button.

- [ ] **Step 7: Style the in-flight indicator**

After `.pr-actions__refuse`, add:

```css
.pr-busy {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
.pr-busy wa-spinner { font-size: 1rem; }
```

`wa-spinner` is already imported in `frontend/src/main.js`. Do not change `main.js`.

- [ ] **Step 8: Run automated frontend regression checks**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm run build
```

Expected: the complete Node suite and all Vite bundles PASS. Existing Vite chunk-size warnings are non-failures. These commands are regression and build checks. They do not replace the manual dialog cases.

- [ ] **Step 9: Run the manual dialog acceptance matrix**

Use a development instance with six distinct pending inbound peer messages, one for each M1–M6 case. Before starting the matrix, send six test messages from the configured peer and give them titles that identify M1 through M6. Do not reset a resolved row or reuse any message in another case.

For M1–M5, temporarily wrap `window.fetch` in browser DevTools so only `/peer-messages/<id>/deliver/` and `/refuse/` wait before calling the original fetch. Restore the original function after each case.

For below-deadline cases, delay three seconds:

```javascript
window.__peerOriginalFetch = window.fetch
window.fetch = async (...args) => {
    const url = String(args[0])
    if (/\/peer-messages\/[^/]+\/(deliver|refuse)\/$/.test(url)) {
        const signal = args[1]?.signal
        await new Promise((resolve, reject) => {
            const timeoutId = setTimeout(resolve, 3000)
            signal?.addEventListener('abort', () => {
                clearTimeout(timeoutId)
                reject(signal.reason || new DOMException('Aborted', 'AbortError'))
            }, { once: true })
        })
    }
    return window.__peerOriginalFetch(...args)
}
```

For timeout cases, change `3000` to `45000`. Restore with:

```javascript
window.fetch = window.__peerOriginalFetch
delete window.__peerOriginalFetch
```

For M6, call the original fetch immediately and delay only the matching response body's `json()` method. Install this separate wrapper:

```javascript
window.__peerOriginalFetch = window.fetch
window.fetch = async (...args) => {
    const response = await window.__peerOriginalFetch(...args)
    const url = String(args[0])
    if (!/\/peer-messages\/[^/]+\/(deliver|refuse)\/$/.test(url)) return response

    const signal = args[1]?.signal
    const originalJson = response.json.bind(response)
    Object.defineProperty(response, 'json', {
        configurable: true,
        value: async () => {
            await new Promise((resolve, reject) => {
                let timeoutId
                const onAbort = () => {
                    clearTimeout(timeoutId)
                    reject(signal?.reason || new DOMException('Aborted', 'AbortError'))
                }
                if (signal?.aborted) {
                    onAbort()
                    return
                }
                timeoutId = setTimeout(() => {
                    signal?.removeEventListener('abort', onAbort)
                    resolve()
                }, 45000)
                signal?.addEventListener('abort', onAbort, { once: true })
            })
            return originalJson()
        },
    })
    return response
}
```

Restore the original fetch after M6 with the same restore snippet.

Run these cases:

- **M1 — existing delivery below deadline:** start delivery. Observe `Delivering…`, the spinner, disabled mode buttons, delivery buttons, Refuse controls, Keep if visible, and footer Close. Escape, header close, backdrop, and footer Close do not close. After the response, the existing composer opens through the normal flow.
- **M2 — new delivery below deadline:** open refusal confirmation, then choose new delivery. The confirmation clears before the request. Observe `Delivering…`. The trust gate happens before the spinner. After approval and response, the new draft opens through the normal flow.
- **M3 — refusal below deadline:** start refusal. Observe `Refusing…`. Keep and every other resolution control stay disabled. Escape, header close, backdrop, and footer Close do not close. The dialog closes only after the successful response.
- **M4 — delivery timeout:** use the 45-second wrapper. At 40 seconds, observe the exact danger callout `The request did not complete in time. Refresh before trying again.` The spinner stops. The dialog stays open and becomes manually closable. No reload or toast occurs.
- **M5 — refusal timeout:** use the 45-second wrapper. Observe the same exact callout, no backend-result claim, no reload, and no toast. The spinner stops, confirmation clears, and the dialog becomes manually closable.
- **M6 — response-body timeout:** use the response-body wrapper and start delivery to an existing session. The request reaches the backend immediately, but the delayed `json()` read crosses the same 40-second deadline. Observe the exact danger callout `The request did not complete in time. Refresh before trying again.` The spinner stops, `busy` clears, and the dialog stays open and becomes manually closable. Do not reconcile or inspect the message status. No reload, toast, or backend-result claim occurs.

Do not change `props.messageId` programmatically during any case. That behavior is deliberately undefined.

- [ ] **Step 10: Commit Task 3**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Commit the changes produced by this task.

Commit subject:

```text
fix(peer): expose review action progress
```

---

### Task 4: Correct the bundled peer-message skill contract

**Files:**
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`
- Modify: `tests/test_twicc_share_skill.py`
- Create: `tests/test_twicc_peer_message_skill.py`

**Interfaces:**
- Consumes: `plugin manifest version before this lot: 0.69.0` from Existing Interfaces.
- Produces: `peer-message skill wire boundary: message_id, title, reply_to, origin.sent_at, payload; root reply_to = ""; origin_session and delivered_to_session local; thread_id, reply_to_ref, reply_target not wire fields`.
- Produces: `plugin manifest version after this lot: 0.69.1`.

- [ ] **Step 1: Read the plugin skill guidance and comparison skills**

Before editing any bundled skill, read these files in full:

- `src/twicc/agent/plugin/README.md`
- `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md`
- `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md`
- `src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md`

Use the README requirements and the existing skills to preserve the bundle's structure, tone, and contract wording.

- [ ] **Step 2: Add the failing dedicated peer-message skill test**

Create `tests/test_twicc_peer_message_skill.py` with:

```python
from pathlib import Path

import orjson

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md"
PLUGIN = ROOT / "src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json"


def test_twicc_peer_message_skill_contract():
    text = SKILL.read_text()
    local_line = next(
        line for line in text.splitlines()
        if line.startswith("- `origin_session` / `delivered_to_session`")
    )
    assert "The peer receives neither." in local_line

    wire_line = next(
        line for line in text.splitlines()
        if line.startswith("- **Wire boundary**")
    )
    for name in ("`message_id`", "`title`", "`reply_to`", "`origin.sent_at`", "`payload`"):
        assert name in wire_line
    assert 'A root message carries `reply_to` as `""`.' in wire_line
    assert (
        "`thread_id`, `reply_to_ref`, and `reply_target` are local serialization values, "
        "not wire fields."
    ) in wire_line

    plugin = orjson.loads(PLUGIN.read_bytes())
    assert plugin["version"] == "0.69.1"
```

- [ ] **Step 3: Update only the version assertion in the share-skill test**

In `tests/test_twicc_share_skill.py`, replace only:

```python
assert plugin["version"] == "0.69.0"
```

with:

```python
assert plugin["version"] == "0.69.1"
```

Do not change any other line in that test.

- [ ] **Step 4: Run both contract tests and verify they fail**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_twicc_peer_message_skill.py tests/test_twicc_share_skill.py -q
```

Expected: FAIL. The peer-message skill has no `Wire boundary` row, and the manifest still reports `0.69.0`.

- [ ] **Step 5: Correct the peer-message skill wording**

In `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md`, replace the `origin_session` / `delivered_to_session` bullet with these two bullets:

```markdown
- `origin_session` / `delivered_to_session` — the local session at each end (`null` when there is none), with its title read live. The peer receives neither.
- **Wire boundary** — the peer-message wire carries `message_id`, `title`, `reply_to`, `origin.sent_at`, and `payload`. A root message carries `reply_to` as `""`. `thread_id`, `reply_to_ref`, and `reply_target` are local serialization values, not wire fields.
```

Keep the existing per-field explanations above these bullets. They describe local serialized output, not additional wire data.

- [ ] **Step 6: Bump the plugin patch version**

In `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, change:

```json
"version": "0.69.0"
```

to:

```json
"version": "0.69.1"
```

Do not change other manifest fields or bundled skills.

- [ ] **Step 7: Run Task 4 verification**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_twicc_peer_message_skill.py tests/test_twicc_share_skill.py -q
```

Expected: 2 tests PASS. The existing share contract still checks its complete previous surface. The new peer-message contract checks the wire boundary and `0.69.1`.

- [ ] **Step 8: Run the complete focused verification set**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_messages.py tests/test_twicc_peer_message_skill.py tests/test_twicc_share_skill.py -q
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uvx ruff check --select E4,E7,E9,F src/twicc/core/services/peer_messages.py tests/test_peer_messages.py tests/test_twicc_peer_message_skill.py tests/test_twicc_share_skill.py
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm run build
```

Expected: focused pytest, focused Ruff, the complete frontend Node suite, and all Vite bundles PASS. Manual M1–M6 remain separate evidence and must not be inferred from these commands.

- [ ] **Step 9: Commit Task 4**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Commit the changes produced by this task.

Commit subject:

```text
docs(peer): correct peer message wire guidance
```
