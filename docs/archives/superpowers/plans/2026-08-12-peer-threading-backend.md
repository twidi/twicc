# Peer Message Threading — Lot 1 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/plans/2026-08-11-peer-threading-design.md` is the authority. This plan implements only lot 1 from §18.

**Goal:** Add peer-message reply persistence, wire validation, local thread resolution, owner and agent serialization, safe delivery-envelope handles, and `twicc peer-send --reply-to` without changing the UI.

**Architecture:** `PeerMessage` stores the verbatim wire handle, its once-resolved local parent, and the locally derived thread root. `core.services.peer_messages` owns the token grammar and peer-scoped direction tie-break. Every serializer computes the reply target from the parent's current local end. The CLI performs a fast pre-check, while the send service remains authoritative.

**Tech Stack:** Django 6 ASGI, Channels, SQLite, Typer CLI, async `httpx`, pytest, pytest-django.

## Global Constraints

- **Lot boundary:** implement only §18 lot 1. Do not change Vue, Pinia, frontend tests, project documentation, agent skills, plugin metadata, or `CHANGELOG.md`.
- **Visible result:** the agent surface works end to end. The UI receives new serialized fields but shows nothing new.
- **Worktree:** every command starts with `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && `. Never read or write `/home/twidi/dev/twicc-poc` for this work.
- **Data safety:** any direct Django command also sets `TWICC_DATA_DIR=$PWD`. Never apply migrations. Never start or restart dev servers. Never run package installation.
- **Tests:** use `uv run --active pytest ...`. This worktree's editable package must resolve to `/home/twidi/dev/twicc-poc/.worktrees/peer-system/src/twicc`.
- **Language:** all code, comments, test names, migration names, and commit subjects are English.
- **Commits:** one commit per task. Each commit step declares only the worktree, staged files, and Conventional Commit subject. The implementer follows `CLAUDE.md` and `AGENTS.md` for body and trailer rules.
- **Historical design:** never edit `docs/plans/2026-07-24-peer-messaging-design.md`.
- **Reply target:** resolve the local end. Pre-selection is a later lot. Hidden, deleted, and archived states remain indistinguishable. Lot 1 exposes only the local session id or `null`.
- **Approval gate:** do not add delivery from a toast or automatic reply routing. The human still reads before delivery.
- **Picker order:** do not add any ordering or pagination behavior in this lot.
- **Inbox:** preserve one row per message. Thread grouping stays deferred.
- **Database invariant:** preserve `UniqueConstraint(fields=["peer", "direction", "message_id"], name="uniq_peermessage_peer_direction_msgid")` exactly.
- **Thread key:** every lookup or grouping key is `(peer_id, thread_id)`, never `thread_id` alone.
- **Wire:** send `reply_to` as a top-level field. Never send `thread_id`. Keep `origin` limited to `sent_at`.
- **Identifiers:** use `(?!\.{1,2}\Z)[A-Za-z0-9._:-]{1,40}` with `fullmatch`. Values are case-sensitive, opaque, and never trimmed.
- **Resolution:** scope every parent lookup to one `Peer`. Prefer the direction opposite to the new row. Fall back to the same direction. Resolve once at row creation.
- **Legacy rows:** local delivery and refusal remain available. Omit unsafe ids from envelopes. Skip their status callbacks.
- **Migrations:** create `0134_peermessage_threading.py` with the six operations in spec §8.2. Do not import application models or services in its data function.

## Task map

| Task | Deliverable | Depends on |
|---|---|---|
| 1 | Schema, migration, token grammar, resolution, send/receive wire, backend tests | — |
| 2 | Serializer contract, eager relation loading, WebSocket snapshot test | 1 |
| 3 | Safe delivery envelope, legacy callback guard, purge invariants | 1, 2 |
| 4 | `peer-send --reply-to`, CLI validation and end-to-end tests | 1, 2, 3 |

---

### Task 1: Persist and transport reply relationships

**Files:**
- Modify: `src/twicc/core/models.py`
- Create: `src/twicc/core/migrations/0134_peermessage_threading.py`
- Modify: `src/twicc/core/services/peer_messages.py`
- Modify: `src/twicc/peer/outbound.py`
- Modify: `tests/test_peer_messages.py`
- Modify: `tests/test_peer_cli.py`
- Create: `tests/test_peer_threading_migration.py`

**Interfaces:**
- Produces: `PeerMessage.reply_to: str`, `PeerMessage.reply_to_message: PeerMessage | None`, and `PeerMessage.thread_id: str`.
- Produces: database index `idx_peermessage_peer_thread` on `("peer", "thread_id")`.
- Produces: `PEER_MESSAGE_ID_PATTERN: re.Pattern[str]` with pattern `(?!\.{1,2}\Z)[A-Za-z0-9._:-]{1,40}`.
- Produces: `validate_reply_to(value) -> tuple[str, PeerError | None]`.
- Produces: `_resolve_reply_to_message(peer, direction: str, reply_to: str) -> PeerMessage | None`.
- Produces: `outbound.post_message(base_url, *, bearer, message_id, title, reply_to, payload, origin)`.
- Produces: send payload contract key `reply_to`, normalized to `""` for absent, `None`, or empty input.

- [ ] **Step 1: Write the migration regression test**

Create `tests/test_peer_threading_migration.py`:

```python
"""Migration coverage for peer-message threading lot 1."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


MIGRATE_FROM = [("core", "0133_share_created_by_session")]
MIGRATE_TO = [("core", "0134_peermessage_threading")]


@pytest.mark.django_db(transaction=True)
def test_peer_threading_backfill_makes_every_historical_row_a_root():
    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_FROM)
    old_apps = executor.loader.project_state(MIGRATE_FROM).apps
    Peer = old_apps.get_model("core", "Peer")
    PeerMessage = old_apps.get_model("core", "PeerMessage")

    peer = Peer.objects.create(
        id="peer_old", name="Old peer", base_url="https://old.example.com", state="active",
    )
    for direction, message_id in (
        ("in", "root-in"),
        ("out", "root-out"),
        ("in", "collision"),
        ("out", "collision"),
    ):
        PeerMessage.objects.create(
            peer=peer,
            direction=direction,
            message_id=message_id,
            title="Historical",
            payload={"text": "old", "images": [], "documents": []},
            status="pending",
        )

    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_TO)
    new_apps = executor.loader.project_state(MIGRATE_TO).apps
    PeerMessage = new_apps.get_model("core", "PeerMessage")
    rows = list(PeerMessage.objects.order_by("direction", "message_id", "pk"))

    assert len(rows) == 4
    for row in rows:
        assert row.thread_id == row.message_id
        assert row.reply_to == ""
        assert row.reply_to_message_id is None

    distinct = [row for row in rows if row.message_id != "collision"]
    assert len({(row.peer_id, row.thread_id) for row in distinct}) == 2
    collision = [row for row in rows if row.message_id == "collision"]
    assert len(collision) == 2
    assert len({(row.peer_id, row.thread_id) for row in collision}) == 1
```

- [ ] **Step 2: Run the migration test to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_threading_migration.py -x -q`

Expected: FAIL because migration node `core.0134_peermessage_threading` does not exist. This catches a missing migration before model code can hide the schema gap.

- [ ] **Step 3: Add the model fields and composite index**

In `src/twicc/core/models.py`, replace this exact block:

```python
    message_id = models.CharField(max_length=40)
    # Sender-written subject (required on every send since 2026-08-11; older
```

with:

```python
    message_id = models.CharField(max_length=40)
    # Verbatim wire handle of the answered message. Empty means thread root.
    reply_to = models.CharField(max_length=40, blank=True, default="")
    # Resolved once at row creation, within this peer relationship. SET_NULL
    # preserves a child if a single parent row is ever deleted independently.
    reply_to_message = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies",
    )
    # Local thread root. The complete key is always (peer_id, thread_id).
    thread_id = models.CharField(max_length=40)
    # Sender-written subject (required on every send since 2026-08-11; older
```

In the same model, replace this exact index block:

```python
        indexes = [
            models.Index(fields=["status", "direction"], name="idx_peermessage_status"),
        ]
```

with:

```python
        indexes = [
            models.Index(fields=["status", "direction"], name="idx_peermessage_status"),
            models.Index(fields=["peer", "thread_id"], name="idx_peermessage_peer_thread"),
        ]
```

Do not alter the existing unique constraint.

- [ ] **Step 4: Create the six-operation migration**

Create `src/twicc/core/migrations/0134_peermessage_threading.py`:

```python
import django.db.models.deletion
from django.db import migrations, models


def backfill_thread_ids(apps, schema_editor):
    PeerMessage = apps.get_model("core", "PeerMessage")
    PeerMessage.objects.update(thread_id=models.F("message_id"))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0133_share_created_by_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="peermessage",
            name="reply_to",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="peermessage",
            name="reply_to_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="core.peermessage",
            ),
        ),
        migrations.AddField(
            model_name="peermessage",
            name="thread_id",
            field=models.CharField(default="", max_length=40),
        ),
        migrations.RunPython(backfill_thread_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="peermessage",
            name="thread_id",
            field=models.CharField(max_length=40),
        ),
        migrations.AddIndex(
            model_name="peermessage",
            index=models.Index(fields=["peer", "thread_id"], name="idx_peermessage_peer_thread"),
        ),
    ]
```

The temporary default remains in migration state through the data update. The explicit `AlterField` removes it in operation 5.

- [ ] **Step 5: Add the grammar and resolution helpers**

In `src/twicc/core/services/peer_messages.py`, replace this exact block:

```python
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def validate_title(value) -> tuple[str, PeerError | None]:
```

with:

```python
_WHITESPACE_RUN_RE = re.compile(r"\s+")
PEER_MESSAGE_ID_PATTERN = re.compile(r"(?!\.{1,2}\Z)[A-Za-z0-9._:-]{1,40}")


def validate_title(value) -> tuple[str, PeerError | None]:
```

Then replace this exact boundary block:

```python
    return flat, None


class PeerSendResult(NamedTuple):
```

with the same return, the new helpers, and the existing class header:

```python
    return flat, None


def validate_reply_to(value) -> tuple[str, PeerError | None]:
    """Normalize root values and validate a non-empty opaque message id."""
    if value is None or value == "":
        return "", None
    if not isinstance(value, str) or PEER_MESSAGE_ID_PATTERN.fullmatch(value) is None:
        return "", PeerError(
            "reply_to", "invalid_reply_to",
            "reply_to must be a valid peer message id",
        )
    return value, None


def _resolve_reply_to_message(peer, direction: str, reply_to: str):
    """Resolve within one peer, preferring the direction opposite the new row."""
    from twicc.core.models import PeerMessage, PeerMessageDirection

    if not reply_to:
        return None
    opposite = (
        PeerMessageDirection.OUT
        if direction == PeerMessageDirection.IN
        else PeerMessageDirection.IN
    )
    candidates = PeerMessage.objects.filter(peer=peer, message_id=reply_to)
    return candidates.filter(direction=opposite).first() or candidates.first()


class PeerSendResult(NamedTuple):
```

The remainder of `PeerSendResult` stays unchanged.

- [ ] **Step 6: Extend the outbound wire**

In `src/twicc/peer/outbound.py`, replace the exact `post_message` function:

```python
async def post_message(
    base_url: str, *, bearer: str, message_id: str, title: str, payload: dict, origin: dict,
) -> tuple[int, dict]:
    return await _post(
        base_url,
        "/peer/messages/",
        {"message_id": message_id, "title": title, "payload": payload, "origin": origin},
        bearer=bearer,
    )
```

with:

```python
async def post_message(
    base_url: str, *, bearer: str, message_id: str, title: str,
    reply_to: str, payload: dict, origin: dict,
) -> tuple[int, dict]:
    return await _post(
        base_url,
        "/peer/messages/",
        {
            "message_id": message_id,
            "title": title,
            "reply_to": reply_to,
            "payload": payload,
            "origin": origin,
        },
        bearer=bearer,
    )
```

- [ ] **Step 7: Validate and resolve replies on the send path**

First replace the exact payload-contract text in the function docstring:

```python
    Payload: ``{peer: <peer_id or exact local name>, title, text, images,
    documents, origin_session_id?}``. Attachments are already
```

with:

```python
    Payload: ``{peer: <peer_id or exact local name>, title, reply_to?, text,
    images, documents, origin_session_id?}``. Attachments are already
```

In `send_peer_message_from_payload`, replace this exact input block:

```python
    peer_ref = (payload.get("peer") or "").strip()
    title, title_error = validate_title(payload.get("title"))
    text = (payload.get("text") or "").strip()
    images = payload.get("images") or []
    documents = payload.get("documents") or []

    errors: list[PeerError] = []
    if not peer_ref:
        errors.append(PeerError("peer", "missing", "peer is required"))
    if title_error is not None:
        errors.append(title_error)
    if not text:
        errors.append(PeerError("text", "empty_text", "text is required"))
```

with:

```python
    peer_ref = (payload.get("peer") or "").strip()
    title, title_error = validate_title(payload.get("title"))
    reply_to, reply_to_error = validate_reply_to(payload.get("reply_to"))
    text = (payload.get("text") or "").strip()
    images = payload.get("images") or []
    documents = payload.get("documents") or []

    errors: list[PeerError] = []
    if not peer_ref:
        errors.append(PeerError("peer", "missing", "peer is required"))
    if title_error is not None:
        errors.append(title_error)
    if reply_to_error is not None:
        errors.append(reply_to_error)
    if not text:
        errors.append(PeerError("text", "empty_text", "text is required"))
```

After the existing active-peer guard ending with this exact block:

```python
    if peer.state != PeerState.ACTIVE:
        return PeerSendResult(False, None, peer.id, [PeerError(
            "peer", "not_active", "This peer relationship is still pending.",
        )], {})

    message_id = mint_message_id()
```

replace the final line with:

```python
    reply_to_message = await sync_to_async(
        _resolve_reply_to_message
    )(peer, PeerMessageDirection.OUT, reply_to)
    if reply_to and reply_to_message is None:
        return PeerSendResult(False, None, peer.id, [PeerError(
            "reply_to", "unknown_reply_to",
            "No message with this id exists for the selected peer.",
        )], {})

    message_id = mint_message_id()
```

In the `PeerMessage(...)` constructor, replace this exact block:

```python
        direction=PeerMessageDirection.OUT,
        message_id=message_id,
        title=title,
```

with:

```python
        direction=PeerMessageDirection.OUT,
        message_id=message_id,
        reply_to=reply_to,
        reply_to_message=reply_to_message,
        thread_id=reply_to_message.thread_id if reply_to_message is not None else message_id,
        title=title,
```

In the `outbound.post_message` call, replace:

```python
            message_id=message_id, title=title, payload=wire_payload, origin=origin,
```

with:

```python
            message_id=message_id, title=title, reply_to=reply_to,
            payload=wire_payload, origin=origin,
```

- [ ] **Step 8: Validate and resolve replies on the receive path**

Replace the existing inbound identifier guard:

```python
    message_id = body.get("message_id")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 40:
        return 400, {"error": "invalid_payload"}
```

with:

```python
    message_id = body.get("message_id")
    if not isinstance(message_id, str) or PEER_MESSAGE_ID_PATTERN.fullmatch(message_id) is None:
        return 400, {"error": "invalid_payload"}
    reply_to, reply_to_error = validate_reply_to(body.get("reply_to"))
    if reply_to_error is not None:
        return 400, {"error": "invalid_payload"}
```

In the inbound `PeerMessage(...)` constructor, replace:

```python
        direction=PeerMessageDirection.IN,
        message_id=message_id,
        title=title,
```

with:

```python
        direction=PeerMessageDirection.IN,
        message_id=message_id,
        reply_to=reply_to,
        title=title,
```

Inside `_store`, replace this exact save block:

```python
        if existing is not None:
            return existing.status
        message.save(force_insert=True)
```

with:

```python
        if existing is not None:
            return existing.status
        reply_to_message = _resolve_reply_to_message(
            peer, PeerMessageDirection.IN, reply_to,
        )
        message.reply_to_message = reply_to_message
        message.thread_id = (
            reply_to_message.thread_id if reply_to_message is not None else message_id
        )
        message.save(force_insert=True)
```

This order preserves replay idempotency. A replay returns before any new resolution or thread computation.

- [ ] **Step 9: Update existing test builders for the required column and wire argument**

In `tests/test_peer_messages.py`, replace `_out_message` with:

```python
def _out_message(peer, **kw):
    message_id = kw.get("message_id", "pm_" + "b" * 16)
    defaults = dict(
        peer=peer, direction=PeerMessageDirection.OUT, message_id=message_id,
        thread_id=message_id,
        payload={"text": "hi", "images": [], "documents": []},
        origin={"sent_at": "2026-07-24T12:00:00+00:00"},
        status=PeerMessageStatus.PENDING,
    )
    defaults.update(kw)
    return PeerMessage.objects.create(**defaults)
```

Replace `_in_message` with:

```python
def _in_message(peer, **kw):
    message_id = kw.get("message_id", "pm_" + "c" * 16)
    defaults = dict(
        peer=peer, direction=PeerMessageDirection.IN, message_id=message_id,
        thread_id=message_id,
        title="The *subject*",
        payload={"text": "the message body", "images": [], "documents": []},
        origin={"sent_at": "2026-07-24T12:00:00+00:00"},
        status=PeerMessageStatus.PENDING,
    )
    defaults.update(kw)
    return PeerMessage.objects.create(**defaults)
```

Replace `_patch_post_message` with:

```python
def _patch_post_message(monkeypatch, status=202, *, network_error=False, calls=None):
    async def _fake(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        if calls is not None:
            calls.append({
                "base_url": base_url,
                "bearer": bearer,
                "message_id": message_id,
                "title": title,
                "reply_to": reply_to,
                "payload": payload,
                "origin": origin,
            })
        if network_error:
            raise outbound.PeerOutboundError("ConnectError")
        return status, {}
    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake)
```

In `tests/test_peer_cli.py`, replace this exact block:

```python
        peer=peer, direction=PeerMessageDirection.OUT, message_id="pm_cli1",
        payload={"text": "hello", "images": [], "documents": []},
```

with:

```python
        peer=peer, direction=PeerMessageDirection.OUT, message_id="pm_cli1",
        thread_id="pm_cli1",
        payload={"text": "hello", "images": [], "documents": []},
```

In `test_peer_send_end_to_end_in_process`, replace:

```python
    async def _fake_post(base_url, *, bearer, message_id, title, payload, origin):
        calls.append({"bearer": bearer, "message_id": message_id, "title": title})
```

with:

```python
    async def _fake_post(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        calls.append({
            "bearer": bearer,
            "message_id": message_id,
            "title": title,
            "reply_to": reply_to,
        })
```

After `assert calls[0]["title"] == "Daily recap"`, add:

```python
    assert calls[0]["reply_to"] == ""
```

- [ ] **Step 10: Add inbound grammar, root, resolution, scope, tie-break, and compatibility tests**

Insert after `test_receive_idempotent_replay` in `tests/test_peer_messages.py`:

```python
@pytest.mark.parametrize("wire_value", [None, ""])
def test_receive_null_or_empty_reply_to_stores_root(
        client, transactional_db, peer_host, wire_value):
    peer = _active_peer()
    res = _post(
        client, "/peer/messages/",
        _wire_body(message_id=f"root-{wire_value is None}", reply_to=wire_value),
        bearer=peer.token_ours,
    )
    assert res.status_code == 202
    message = PeerMessage.objects.get()
    assert message.reply_to == ""
    assert message.reply_to_message_id is None
    assert message.thread_id == message.message_id


def test_receive_absent_reply_to_stores_root(client, transactional_db, peer_host):
    peer = _active_peer()
    body = _wire_body(message_id="root-absent")
    body.pop("reply_to", None)
    res = _post(client, "/peer/messages/", body, bearer=peer.token_ours)
    assert res.status_code == 202
    message = PeerMessage.objects.get()
    assert (message.reply_to, message.reply_to_message_id, message.thread_id) == (
        "", None, "root-absent",
    )


@pytest.mark.parametrize("token", ["A", "A._:-z", "x" * 40])
def test_receive_message_id_tokens_round_trip_byte_for_byte(
        client, transactional_db, peer_host, token):
    peer = _active_peer()
    res = _post(
        client, "/peer/messages/",
        _wire_body(message_id=token),
        bearer=peer.token_ours,
    )
    assert res.status_code == 202
    message = PeerMessage.objects.get()
    assert message.message_id == token


@pytest.mark.parametrize("token", ["A", "A._:-z", "x" * 40])
def test_receive_identifier_tokens_round_trip_byte_for_byte(
        client, transactional_db, peer_host, token):
    peer = _active_peer()
    parent = _out_message(peer, message_id=token, thread_id=token)
    child_id = "child-" + str(parent.pk)
    res = _post(
        client, "/peer/messages/",
        _wire_body(message_id=child_id, reply_to=token),
        bearer=peer.token_ours,
    )
    assert res.status_code == 202
    child = PeerMessage.objects.get(message_id=child_id)
    assert child.reply_to == token
    assert child.reply_to_message_id == parent.pk
    assert child.thread_id == token


@pytest.mark.parametrize(
    "bad_id",
    [None, 7, "", ".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]", "x" * 41],
)
def test_receive_rejects_nonconforming_message_id_without_row(
        client, transactional_db, peer_host, bad_id):
    peer = _active_peer()
    res = _post(
        client, "/peer/messages/", _wire_body(message_id=bad_id),
        bearer=peer.token_ours,
    )
    assert res.status_code == 400
    assert PeerMessage.objects.count() == 0


@pytest.mark.parametrize(
    "bad_reply",
    [7, ".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]", "x" * 41],
)
def test_receive_rejects_nonconforming_reply_to_without_child(
        client, transactional_db, peer_host, bad_reply):
    peer = _active_peer()
    _out_message(peer, message_id="parent", thread_id="parent")
    res = _post(
        client, "/peer/messages/",
        _wire_body(message_id="child", reply_to=bad_reply),
        bearer=peer.token_ours,
    )
    assert res.status_code == 400
    assert list(PeerMessage.objects.values_list("message_id", flat=True)) == ["parent"]


def test_receive_unknown_conforming_reply_becomes_root(client, transactional_db, peer_host):
    peer = _active_peer()
    res = _post(
        client, "/peer/messages/",
        _wire_body(message_id="child", reply_to="unknown"),
        bearer=peer.token_ours,
    )
    assert res.status_code == 202
    child = PeerMessage.objects.get()
    assert child.reply_to == "unknown"
    assert child.reply_to_message_id is None
    assert child.thread_id == "child"


def test_receive_reply_prefers_opposite_direction_and_stays_peer_scoped(
        client, transactional_db, peer_host):
    peer = _active_peer()
    other = _active_peer(
        name="bob", base_url="https://bob.example.com", token_ours=mint_token(),
    )
    same_direction = _in_message(
        peer, message_id="collision", thread_id="collision",
    )
    opposite_direction = _out_message(
        peer, message_id="collision", thread_id="collision",
    )
    other_root = _out_message(other, message_id="collision", thread_id="collision")

    res = _post(
        client, "/peer/messages/",
        _wire_body(message_id="reply", reply_to="collision"),
        bearer=peer.token_ours,
    )
    assert res.status_code == 202
    reply = PeerMessage.objects.get(peer=peer, message_id="reply")
    assert reply.reply_to_message_id == opposite_direction.pk
    assert reply.reply_to_message_id != same_direction.pk
    assert reply.thread_id == "collision"
    assert (same_direction.peer_id, same_direction.thread_id) == (
        opposite_direction.peer_id, opposite_direction.thread_id,
    )
    assert (reply.peer_id, reply.thread_id) != (other_root.peer_id, other_root.thread_id)


def test_receive_reply_falls_back_to_same_direction_parent(
        client, transactional_db, peer_host):
    peer = _active_peer()
    parent = _in_message(
        peer, message_id="parent-in", thread_id="thread-root",
    )

    res = _post(
        client, "/peer/messages/",
        _wire_body(message_id="child-in", reply_to=parent.message_id),
        bearer=peer.token_ours,
    )

    assert res.status_code == 202
    child = PeerMessage.objects.get(message_id="child-in")
    assert child.reply_to_message_id == parent.pk
    assert child.thread_id == parent.thread_id


def test_replay_does_not_reconstruct_reply_from_new_wire_data(
        client, transactional_db, peer_host):
    peer = _active_peer()
    _post(client, "/peer/messages/", _wire_body(message_id="legacy-root"), bearer=peer.token_ours)
    parent = _out_message(peer, message_id="later-parent", thread_id="later-parent")
    replay = _post(
        client, "/peer/messages/",
        _wire_body(message_id="legacy-root", reply_to=parent.message_id, unknown_key=True),
        bearer=peer.token_ours,
    )
    assert replay.status_code == 202
    stored = PeerMessage.objects.get(direction=PeerMessageDirection.IN, message_id="legacy-root")
    assert stored.reply_to == ""
    assert stored.reply_to_message_id is None
    assert stored.thread_id == "legacy-root"
```

The `unknown_key` assertion also proves newer receivers preserve the existing behavior of ignoring unknown top-level keys.

- [ ] **Step 11: Add send-service, wire, collision, convergence, and divergence tests**

Insert after `test_send_network_error`:

```python
def test_outbound_post_message_builds_exact_threading_wire(monkeypatch):
    calls = []

    async def _fake_post(base_url, path, json_body, *, bearer):
        calls.append({
            "base_url": base_url,
            "path": path,
            "json_body": json_body,
            "bearer": bearer,
        })
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound._post", _fake_post)
    origin = {"sent_at": "2026-07-24T12:00:00+00:00"}
    payload = {"text": "body", "images": [], "documents": []}

    for reply_to in ("", "A._:-z"):
        status, response = _run(outbound.post_message(
            "https://alice.example.com",
            bearer="their-token",
            message_id="message-id",
            title="Subject",
            reply_to=reply_to,
            payload=payload,
            origin=origin,
        ))
        assert (status, response) == (202, {})

    assert [call["path"] for call in calls] == ["/peer/messages/", "/peer/messages/"]
    assert [call["json_body"]["reply_to"] for call in calls] == ["", "A._:-z"]
    for call in calls:
        assert call["base_url"] == "https://alice.example.com"
        assert call["bearer"] == "their-token"
        assert "thread_id" not in call["json_body"]
        assert call["json_body"]["origin"] == {"sent_at": "2026-07-24T12:00:00+00:00"}


@pytest.mark.parametrize(
    ("include_reply_to", "reply_input"),
    [(False, None), (True, None), (True, "")],
)
def test_send_root_normalizes_reply_to_and_never_sends_thread_id(
        transactional_db, peer_host, monkeypatch, include_reply_to, reply_input):
    _active_peer()
    calls = []
    _patch_post_message(monkeypatch, calls=calls)
    payload = {"peer": "alice", "title": "Root", "text": "body"}
    if include_reply_to:
        payload["reply_to"] = reply_input
    result = _run(peer_messages.send_peer_message_from_payload(payload))
    assert result.success
    message = PeerMessage.objects.get()
    assert (message.reply_to, message.reply_to_message_id, message.thread_id) == (
        "", None, message.message_id,
    )
    assert calls[0]["reply_to"] == ""
    assert "thread_id" not in calls[0]


@pytest.mark.parametrize("token", ["A", "x" * 40])
def test_send_conforming_reply_resolves_and_reaches_wire_unchanged(
        transactional_db, peer_host, monkeypatch, token):
    peer = _active_peer()
    parent = _in_message(peer, message_id=token, thread_id="thread-root")
    calls = []
    _patch_post_message(monkeypatch, calls=calls)
    result = _run(peer_messages.send_peer_message_from_payload({
        "peer": peer.id, "title": "Reply", "text": "body", "reply_to": token,
    }))
    assert result.success
    child = PeerMessage.objects.exclude(pk=parent.pk).get()
    assert child.reply_to == token
    assert child.reply_to_message_id == parent.pk
    assert child.thread_id == "thread-root"
    assert calls[0]["reply_to"] == token
    assert "thread_id" not in calls[0]


@pytest.mark.parametrize(
    "bad_reply",
    [7, ".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]", "x" * 41],
)
def test_send_service_rejects_nonconforming_reply_before_insert(
        transactional_db, peer_host, monkeypatch, bad_reply):
    _active_peer()
    _patch_post_message(monkeypatch)
    result = _run(peer_messages.send_peer_message_from_payload({
        "peer": "alice", "title": "Reply", "text": "body", "reply_to": bad_reply,
    }))
    assert not result.success
    assert result.errors[0].code == "invalid_reply_to"
    assert PeerMessage.objects.count() == 0


def test_send_service_rejects_unknown_and_cross_peer_reply_targets(
        transactional_db, peer_host, monkeypatch):
    peer = _active_peer()
    other = _active_peer(
        name="bob", base_url="https://bob.example.com", token_ours=mint_token(),
    )
    _in_message(other, message_id="other-message", thread_id="other-message")
    _patch_post_message(monkeypatch)
    for reply_to in ("unknown", "other-message"):
        result = _run(peer_messages.send_peer_message_from_payload({
            "peer": peer.id, "title": "Reply", "text": "body", "reply_to": reply_to,
        }))
        assert not result.success
        assert result.errors[0].code == "unknown_reply_to"
    assert PeerMessage.objects.filter(peer=peer).count() == 0


def test_send_failed_parent_is_allowed_and_collision_prefers_inbound(
        transactional_db, peer_host, monkeypatch):
    peer = _active_peer()
    outbound_parent = _out_message(
        peer, message_id="collision", thread_id="out-root", status=PeerMessageStatus.FAILED,
    )
    inbound_parent = _in_message(
        peer, message_id="collision", thread_id="in-root", status=PeerMessageStatus.REFUSED,
    )
    calls = []
    _patch_post_message(monkeypatch, calls=calls)
    result = _run(peer_messages.send_peer_message_from_payload({
        "peer": peer.id, "title": "Reply", "text": "body", "reply_to": "collision",
    }))
    assert result.success
    child = PeerMessage.objects.exclude(pk__in=[outbound_parent.pk, inbound_parent.pk]).get()
    assert child.reply_to_message_id == inbound_parent.pk
    assert child.thread_id == "in-root"
    assert calls[0]["reply_to"] == "collision"
    assert outbound_parent.thread_id != child.thread_id

    failed_only = _out_message(
        peer,
        message_id="failed-only",
        thread_id="failed-only",
        status=PeerMessageStatus.FAILED,
    )
    second = _run(peer_messages.send_peer_message_from_payload({
        "peer": peer.id,
        "title": "Follow-up",
        "text": "body",
        "reply_to": failed_only.message_id,
    }))
    assert second.success
    follow_up = PeerMessage.objects.get(message_id=second.message_id)
    assert follow_up.reply_to_message_id == failed_only.pk
    assert follow_up.thread_id == failed_only.thread_id


def test_three_message_exchange_converges_on_one_local_thread(
        client, transactional_db, peer_host, monkeypatch):
    peer = _active_peer()
    calls = []
    _patch_post_message(monkeypatch, calls=calls)
    root_result = _run(peer_messages.send_peer_message_from_payload({
        "peer": peer.id, "title": "M1", "text": "one",
    }))
    root = PeerMessage.objects.get(message_id=root_result.message_id)
    receive = _post(
        client, "/peer/messages/",
        _wire_body(message_id="M2", reply_to=root.message_id),
        bearer=peer.token_ours,
    )
    assert receive.status_code == 202
    reply_result = _run(peer_messages.send_peer_message_from_payload({
        "peer": peer.id, "title": "M3", "text": "three", "reply_to": "M2",
    }))
    assert reply_result.success
    assert set(PeerMessage.objects.values_list("thread_id", flat=True)) == {root.message_id}


def test_descendants_keep_each_local_parents_thread_identity(
        transactional_db, peer_host, monkeypatch):
    peer_a = _active_peer()
    peer_b = _active_peer(
        name="bob", base_url="https://bob.example.com", token_ours=mint_token(),
    )
    _in_message(peer_a, message_id="M2", thread_id="M1")
    _in_message(peer_b, message_id="M2", thread_id="M2")
    _patch_post_message(monkeypatch)
    for peer in (peer_a, peer_b):
        result = _run(peer_messages.send_peer_message_from_payload({
            "peer": peer.id, "title": "M3", "text": "three", "reply_to": "M2",
        }))
        assert result.success
    child_a = PeerMessage.objects.get(peer=peer_a, direction=PeerMessageDirection.OUT, reply_to="M2")
    child_b = PeerMessage.objects.get(peer=peer_b, direction=PeerMessageDirection.OUT, reply_to="M2")
    assert child_a.thread_id == "M1"
    assert child_b.thread_id == "M2"
    assert (child_a.peer_id, child_a.thread_id) != (child_b.peer_id, child_b.thread_id)
```

- [ ] **Step 12: Run Task 1 tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_threading_migration.py tests/test_peer_messages.py tests/test_peer_cli.py -q`

Expected: PASS. If resolution is not peer-scoped, the cross-peer tests select the wrong parent. If direction preference is reversed, both collision tests fail. If roots do not derive `thread_id` from their own id, the root and migration assertions fail. The direct `outbound._post` assertions fail if the real wire builder drops `reply_to`, sends `thread_id`, changes the endpoint, or expands `origin` beyond `sent_at`. The receive regressions fail if an accepted `message_id` changes byte identity or same-direction fallback is absent.

- [ ] **Step 13: Verify model and migration state agree**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active python -m django makemigrations --check --dry-run --settings=twicc.settings`

Expected: `No changes detected`. A field option, index name, relation, or migration-state mismatch makes this command exit non-zero.

- [ ] **Step 14: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/models.py src/twicc/core/migrations/0134_peermessage_threading.py src/twicc/core/services/peer_messages.py src/twicc/peer/outbound.py tests/test_peer_messages.py tests/test_peer_cli.py tests/test_peer_threading_migration.py`
Subject: `feat(peer): add message reply persistence`
```

---

### Task 2: Serialize threading data on every owner and agent read path

**Files:**
- Modify: `src/twicc/core/serializers.py`
- Modify: `src/twicc/core/services/peer_messages.py`
- Modify: `src/twicc/peer/owner_views.py`
- Modify: `src/twicc/asgi.py`
- Modify: `src/twicc/cli/peer_message.py`
- Modify: `tests/test_peer_messages.py`
- Modify: `tests/test_peer_cli.py`
- Create: `tests/test_peer_updates_consumer.py`

**Interfaces:**
- Consumes: Task 1's `reply_to`, `reply_to_message`, and `thread_id` fields.
- Produces: `serialize_peer_message(...)` keys `thread_id: str`, `reply_to: str`, `reply_to_ref: dict | None`, and `reply_target: str | None` in summary and detail forms.
- Produces: `reply_to_ref == {"message_id", "title", "direction", "status"}` for a resolved parent.
- Produces: live `reply_target` from `origin_session_id` for an outbound parent or `delivered_to_session_id` for an inbound parent.
- Produces: `_fresh_message(...).select_related("reply_to_message")` guarantee.
- Preserves: `peer_message_session_ref(session) -> {id, title, project_id} | None` unchanged.

- [ ] **Step 1: Write serializer contract tests**

Insert after `test_serializer_carries_live_session_titles` in `tests/test_peer_messages.py`:

```python
def test_serializer_carries_threading_contract_and_live_parent_local_end(transactional_db):
    from twicc.core.serializers import serialize_peer_message

    peer = _active_peer()
    _, origin_session = _make_target_session()
    _, delivered_session = _make_target_session(
        project_id="-tmp-received", directory="/tmp/received",
    )
    outbound_parent = _out_message(
        peer,
        message_id="parent-out",
        thread_id="thread-root",
        title="Our parent",
        origin_session=origin_session,
        status=PeerMessageStatus.DELIVERED,
    )
    inbound_parent = _in_message(
        peer,
        message_id="parent-in",
        thread_id="thread-root",
        title="Their parent",
        delivered_to_session=delivered_session,
        status=PeerMessageStatus.DELIVERED,
    )
    reply_to_outbound = _in_message(
        peer,
        message_id="reply-in",
        reply_to=outbound_parent.message_id,
        reply_to_message=outbound_parent,
        thread_id="thread-root",
    )
    reply_to_inbound = _out_message(
        peer,
        message_id="reply-out",
        reply_to=inbound_parent.message_id,
        reply_to_message=inbound_parent,
        thread_id="thread-root",
    )

    rows = PeerMessage.objects.select_related("reply_to_message").filter(
        pk__in=[reply_to_outbound.pk, reply_to_inbound.pk],
    )
    data = {
        row.message_id: serialize_peer_message(row, include_payload=True)
        for row in rows
    }

    inbound_data = data["reply-in"]
    assert inbound_data["thread_id"] == "thread-root"
    assert inbound_data["reply_to"] == "parent-out"
    assert inbound_data["reply_to_ref"] == {
        "message_id": "parent-out",
        "title": "Our parent",
        "direction": PeerMessageDirection.OUT,
        "status": PeerMessageStatus.DELIVERED,
    }
    assert inbound_data["reply_target"] == origin_session.id
    assert "payload" in inbound_data

    outbound_data = data["reply-out"]
    assert outbound_data["reply_to_ref"]["direction"] == PeerMessageDirection.IN
    assert outbound_data["reply_target"] == delivered_session.id


def test_serializer_root_and_parent_without_local_end_have_null_reply_data(transactional_db):
    from twicc.core.serializers import serialize_peer_message

    peer = _active_peer()
    root = _in_message(peer, message_id="root", thread_id="root")
    parent = _out_message(peer, message_id="parent", thread_id="parent")
    child = _in_message(
        peer,
        message_id="child",
        reply_to=parent.message_id,
        reply_to_message=parent,
        thread_id="parent",
    )

    root_data = serialize_peer_message(root)
    assert root_data["thread_id"] == "root"
    assert root_data["reply_to"] == ""
    assert root_data["reply_to_ref"] is None
    assert root_data["reply_target"] is None
    assert "payload" not in root_data

    child = PeerMessage.objects.select_related("reply_to_message").get(pk=child.pk)
    child_data = serialize_peer_message(child)
    assert child_data["reply_to_ref"]["message_id"] == "parent"
    assert child_data["reply_target"] is None
```

- [ ] **Step 2: Run the serializer tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_messages.py -k 'serializer_carries_threading or serializer_root_and_parent' -q`

Expected: FAIL with missing `thread_id`, `reply_to`, `reply_to_ref`, or `reply_target` keys. This catches a serializer that updates only the detail form or only one parent direction.

- [ ] **Step 3: Extend `serialize_peer_message` without changing session refs**

In `src/twicc/core/serializers.py`, replace this exact function opening:

```python
def serialize_peer_message(message, *, include_payload=False):
    """Peer-message serializer. Summary form by default — base64 blobs must never
    transit the channel layer; only the REST detail endpoint passes
    ``include_payload=True``."""
    text = (message.payload or {}).get("text", "") or ""
```

with:

```python
def serialize_peer_message(message, *, include_payload=False):
    """Peer-message serializer. Summary form by default — base64 blobs must never
    transit the channel layer; only the REST detail endpoint passes
    ``include_payload=True``."""
    from twicc.core.models import PeerMessageDirection

    reply_to_message = message.reply_to_message
    reply_to_ref = None
    reply_target = None
    if reply_to_message is not None:
        reply_to_ref = {
            "message_id": reply_to_message.message_id,
            "title": reply_to_message.title,
            "direction": reply_to_message.direction,
            "status": reply_to_message.status,
        }
        reply_target = (
            reply_to_message.origin_session_id
            if reply_to_message.direction == PeerMessageDirection.OUT
            else reply_to_message.delivered_to_session_id
        )
    text = (message.payload or {}).get("text", "") or ""
```

Then insert these four entries immediately after the existing `"message_id"` entry:

```python
        "thread_id": message.thread_id,
        "reply_to": message.reply_to,
        "reply_to_ref": reply_to_ref,
        "reply_target": reply_target,
```

Do not edit `peer_message_session_ref` or either existing local-session reference shape.

- [ ] **Step 4: Create WebSocket, owner REST, and CLI query regressions**

Create `tests/test_peer_updates_consumer.py`:

```python
"""Initial owner WebSocket snapshot for threaded peer messages."""

import asyncio

import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone as djtz

from twicc.asgi import WSConsumer
from twicc.core.models import (
    Peer,
    PeerMessage,
    PeerMessageDirection,
    PeerMessageStatus,
    PeerState,
    Project,
    Session,
    SessionType,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_initial_peer_message_snapshot_serializes_resolved_reply_without_async_lazy_load(
        transactional_db, monkeypatch, settings):
    settings.TWICC_PASSWORD_HASH = ""
    now = djtz.now()
    project = Project.objects.create(
        id="-tmp-peer-updates", directory="/tmp/peer-updates",
    )
    session = Session.objects.create(
        id="peer-origin", project=project, provider="claude_code",
        file_path="peer-origin.jsonl", type=SessionType.SESSION,
        title="Origin", created_at=now, last_new_content_at=now,
    )
    peer = Peer.objects.create(
        name="alice", base_url="https://alice.example.com", state=PeerState.ACTIVE,
    )
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="parent",
        thread_id="parent",
        title="Parent",
        payload={"text": "one", "images": [], "documents": []},
        origin_session=session,
        status=PeerMessageStatus.DELIVERED,
    )
    child = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id="child",
        reply_to="parent",
        reply_to_message=parent,
        thread_id="parent",
        title="Child",
        payload={"text": "two", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )

    class Registry:
        def set_broadcast_callback(self, callback):
            self.callback = callback

    registry = Registry()
    monkeypatch.setattr("twicc.asgi.scope_remote_access_blocked", lambda scope: False)
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: registry)

    async def scenario():
        comm = WebsocketCommunicator(
            WSConsumer.as_asgi(), "/ws/?subscribe=peer_messages_updated",
        )
        connected, _ = await comm.connect()
        assert connected
        message = await comm.receive_json_from(timeout=2)
        assert message["type"] == "peer_messages_updated"
        row = next(item for item in message["messages"] if item["id"] == child.pk)
        assert row["thread_id"] == "parent"
        assert row["reply_to"] == "parent"
        assert row["reply_to_ref"] == {
            "message_id": "parent",
            "title": "Parent",
            "direction": "out",
            "status": "delivered",
        }
        assert row["reply_target"] == session.id
        await comm.disconnect()

    _run(scenario())
```

In `tests/test_peer_messages.py`, insert after
`test_serializer_root_and_parent_without_local_end_have_null_reply_data`:

```python
def _resolved_owner_reply():
    peer = _active_peer()
    _, origin_session = _make_target_session()
    parent = _out_message(
        peer,
        message_id="owner-parent",
        thread_id="owner-parent",
        title="Owner parent",
        origin_session=origin_session,
        status=PeerMessageStatus.DELIVERED,
    )
    child = _in_message(
        peer,
        message_id="owner-child",
        reply_to=parent.message_id,
        reply_to_message=parent,
        thread_id=parent.thread_id,
    )
    return parent, child, origin_session


def _assert_owner_reply_contract(row, parent, child, origin_session):
    assert row["thread_id"] == parent.thread_id
    assert row["reply_to"] == parent.message_id
    assert row["reply_to_ref"] == {
        "message_id": parent.message_id,
        "title": parent.title,
        "direction": PeerMessageDirection.OUT,
        "status": PeerMessageStatus.DELIVERED,
    }
    assert row["reply_target"] == origin_session.id
    assert row["message_id"] == child.message_id


def test_owner_message_list_serializes_resolved_reply_without_async_lazy_load(
        client, transactional_db):
    parent, child, origin_session = _resolved_owner_reply()

    response = _run(client.get("/api/peer-messages/"))

    assert response.status_code == 200
    body = orjson.loads(response.content)
    row = next(item for item in body["messages"] if item["id"] == child.pk)
    _assert_owner_reply_contract(row, parent, child, origin_session)
    assert "payload" not in row


def test_owner_message_detail_serializes_resolved_reply_without_async_lazy_load(
        client, transactional_db):
    parent, child, origin_session = _resolved_owner_reply()

    response = _run(client.get(f"/api/peer-messages/{child.pk}/"))

    assert response.status_code == 200
    row = orjson.loads(response.content)
    _assert_owner_reply_contract(row, parent, child, origin_session)
    assert row["payload"]["text"] == child.payload["text"]
```

In `tests/test_peer_cli.py`, insert after `test_peer_message_found_and_not_found`:

```python
@pytest.mark.django_db(transaction=True)
def test_peer_message_resolved_reply_uses_one_query(django_assert_num_queries):
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id="cli-parent",
        thread_id="cli-parent",
        title="CLI parent",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.DELIVERED,
    )
    child = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="cli-child",
        reply_to=parent.message_id,
        reply_to_message=parent,
        thread_id=parent.thread_id,
        payload={"text": "child", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )

    with django_assert_num_queries(1):
        response = invoke(["peer-message", child.message_id])

    assert response.exit_code == 0
    assert response.result["thread_id"] == parent.thread_id
    assert response.result["reply_to"] == parent.message_id
    assert response.result["reply_to_ref"]["message_id"] == parent.message_id
    assert response.result["reply_target"] is None
```

- [ ] **Step 5: Run the read-path regressions before eager loading**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_updates_consumer.py tests/test_peer_messages.py tests/test_peer_cli.py -k 'initial_peer_message_snapshot_serializes_resolved_reply or owner_message_list_serializes_resolved_reply or owner_message_detail_serializes_resolved_reply or peer_message_resolved_reply_uses_one_query' -q`

Expected: FAIL. The WebSocket snapshot, owner list, and owner detail tests raise `SynchronousOnlyOperation` when the async serializer touches an unloaded `reply_to_message`. The CLI regression records two queries instead of one. Step 6 makes all four tests pass. These tests cover `_peer_messages_snapshot`, `peer_messages_list`, `_load_message`, and `peer_message_cmd` independently.

- [ ] **Step 6: Add `reply_to_message` to every serialization query**

Apply these exact replacements:

1. In `src/twicc/core/services/peer_messages.py`, replace both occurrences of:

   ```python
        .select_related("peer", "origin_session", "delivered_to_session")
   ```

   with:

   ```python
        .select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
   ```

   The two sites are `_serialize_for_broadcast` and `_fresh_message`.

2. In `src/twicc/peer/owner_views.py`, replace the `_load_message` relation line:

   ```python
        .select_related("peer", "origin_session", "delivered_to_session")
   ```

   with:

   ```python
        .select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
   ```

   Replace the list queryset:

   ```python
        rows = PeerMessage.objects.select_related("origin_session", "delivered_to_session")
   ```

   with:

   ```python
        rows = PeerMessage.objects.select_related(
            "origin_session", "delivered_to_session", "reply_to_message",
        )
   ```

3. In `src/twicc/asgi.py`, replace the snapshot queryset:

   ```python
                rows = PeerMessage.objects.select_related("origin_session", "delivered_to_session")
   ```

   with:

   ```python
                rows = PeerMessage.objects.select_related(
                    "origin_session", "delivered_to_session", "reply_to_message",
                )
   ```

4. In `src/twicc/cli/peer_message.py`, replace:

   ```python
        .select_related("peer", "origin_session", "delivered_to_session")
   ```

   with:

   ```python
        .select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
   ```

- [ ] **Step 7: Run Task 2 tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_messages.py tests/test_peer_updates_consumer.py tests/test_peer_cli.py -q`

Expected: PASS. Missing `reply_to_message` at `_serialize_for_broadcast` fails the receive regressions. Missing it at `_fresh_message` fails the delivery-envelope regressions in Task 3. Missing it at `_peer_messages_snapshot`, `peer_messages_list`, or `_load_message` fails its async regression. Missing it at `peer_message_cmd` violates the asserted one-query contract. A reversed local-end rule fails one of the two serializer directions.

- [ ] **Step 8: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/serializers.py src/twicc/core/services/peer_messages.py src/twicc/peer/owner_views.py src/twicc/asgi.py src/twicc/cli/peer_message.py tests/test_peer_messages.py tests/test_peer_cli.py tests/test_peer_updates_consumer.py`
Subject: `feat(peer): serialize message threading`
```

---

### Task 3: Add safe reply handles to delivery envelopes and protect legacy rows

**Files:**
- Modify: `src/twicc/core/services/peer_messages.py`
- Modify: `tests/test_peer_messages.py`

**Interfaces:**
- Consumes: Task 1's `PEER_MESSAGE_ID_PATTERN` and `reply_to_message` relation.
- Consumes: Task 2's `_fresh_message(...).select_related("reply_to_message")` guarantee.
- Produces: a conforming message's own id in the delivery header, byte-for-byte inside a Markdown code span.
- Produces: `in reply to your **“<title>”**` for an outbound parent and `in reply to their **“<title>”**` for an inbound parent.
- Produces: `_notify_status(...)` no-op for every stored id that fails `PEER_MESSAGE_ID_PATTERN.fullmatch`.
- Preserves: delivery text, attachments, recipient notes, and local status changes for legacy rows.

- [ ] **Step 1: Update the exact-envelope expectation to require the safe id**

In `test_deliver_to_existing_envelope_exact`, replace the first expected header fragment:

```python
        ":: peer message **“The \\*subject\\*”** from **alice** (`https://alice.example.com`)"
```

with:

```python
        f":: peer message **“The \\*subject\\*”** (`{message.message_id}`)"
        " from **alice** (`https://alice.example.com`)"
```

In `test_deliver_envelope_without_note`, replace:

```python
    assert text.startswith(":: peer message from **alice** (`https://alice.example.com`)")
```

with:

```python
    assert text.startswith(
        f":: peer message (`{message.message_id}`) from **alice** (`https://alice.example.com`)"
    )
```

- [ ] **Step 2: Add envelope direction, hostile-title, and legacy-id tests**

Insert after `test_deliver_envelope_without_note`:

```python
@pytest.mark.parametrize(
    ("parent_direction", "relation_text"),
    [
        (PeerMessageDirection.OUT, "in reply to your"),
        (PeerMessageDirection.IN, "in reply to their"),
    ],
)
def test_delivery_envelope_names_safe_handle_and_parent_direction(
        transactional_db, status_callbacks, parent_direction, relation_text):
    peer = _active_peer()
    parent_factory = _out_message if parent_direction == PeerMessageDirection.OUT else _in_message
    parent = parent_factory(
        peer,
        message_id="parent-safe",
        thread_id="parent-safe",
        title="Hostile\n*parent* `title`",
    )
    child = _in_message(
        peer,
        message_id="A._:-z",
        reply_to=parent.message_id,
        reply_to_message=parent,
        thread_id=parent.thread_id,
    )
    _, session = _make_target_session()

    success, envelope, errors = _run(peer_messages.mark_delivered(
        child, session_id=session.id,
    ))

    assert success and errors == []
    header = envelope.split("\n", 1)[0]
    assert "`A._:-z`" in header
    assert f"{relation_text} **“Hostile \\*parent\\* \\`title\\`”**" in header
    assert "\n" not in header


def test_delivery_envelope_omits_relation_when_legacy_parent_title_is_empty(
        transactional_db, status_callbacks):
    peer = _active_peer()
    parent = _out_message(peer, message_id="parent", thread_id="parent", title="")
    child = _in_message(
        peer,
        message_id="child",
        reply_to=parent.message_id,
        reply_to_message=parent,
        thread_id=parent.thread_id,
    )
    _, session = _make_target_session()
    success, envelope, errors = _run(peer_messages.mark_delivered(
        child, session_id=session.id,
    ))
    assert success and errors == []
    assert "in reply to" not in envelope.split("\n", 1)[0]


@pytest.mark.parametrize("legacy_id", [".", "..", "A\n"])
def test_legacy_unsafe_id_is_omitted_but_delivery_still_succeeds(
        transactional_db, status_callbacks, legacy_id):
    peer = _active_peer()
    message = _in_message(
        peer,
        message_id=legacy_id,
        thread_id=legacy_id,
        payload={"text": "legacy body", "images": [_image_block()], "documents": []},
    )
    _, session = _make_target_session()

    success, envelope, errors = _run(peer_messages.mark_delivered(
        message, session_id=session.id,
    ))

    assert success and errors == []
    assert "legacy body" in envelope
    assert f"`{legacy_id}`" not in envelope.split("\n", 1)[0]
    message.refresh_from_db()
    assert message.status == PeerMessageStatus.DELIVERED
    assert message.payload["images"]
    assert status_callbacks == []


@pytest.mark.parametrize("legacy_id", [".", "..", "A\n"])
def test_legacy_unsafe_id_refusal_skips_callback_but_resolves_locally(
        transactional_db, status_callbacks, legacy_id):
    peer = _active_peer()
    message = _in_message(peer, message_id=legacy_id, thread_id=legacy_id)

    success, errors = _run(peer_messages.refuse_peer_message(message))

    assert success and errors == []
    message.refresh_from_db()
    assert message.status == PeerMessageStatus.REFUSED
    assert status_callbacks == []
```

- [ ] **Step 3: Run the new envelope tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_messages.py -k 'delivery_envelope or legacy_unsafe or deliver_to_existing_envelope_exact or deliver_envelope_without_note' -q`

Expected: FAIL. Safe current ids are absent, reply-direction text is absent, and legacy rows still call `post_status`. This distinguishes all three required code changes.

- [ ] **Step 4: Add the safe own-id and parent segments to the envelope**

In `build_delivery_envelope`, replace this exact construction block:

```python
    header = ":: peer message"
    # Empty only on rows stored before the title became required — the segment
    # is omitted, never rendered as a blank subject.
    if title := inline_md(message.title, max_chars=PEER_MESSAGE_TITLE_MAX_CHARS):
        header += f" **“{title}”**"
    header += f" from **{inline_md(peer.name) or 'an unnamed peer'}** (`{inline_md(peer.base_url)}`)"
```

with:

```python
    header = ":: peer message"
    # Empty only on rows stored before the title became required — the segment
    # is omitted, never rendered as a blank subject.
    if title := inline_md(message.title, max_chars=PEER_MESSAGE_TITLE_MAX_CHARS):
        header += f" **“{title}”**"
    if PEER_MESSAGE_ID_PATTERN.fullmatch(message.message_id) is not None:
        header += f" (`{message.message_id}`)"
    header += f" from **{inline_md(peer.name) or 'an unnamed peer'}** (`{inline_md(peer.base_url)}`)"
    if message.reply_to_message is not None:
        parent_title = inline_md(
            message.reply_to_message.title,
            max_chars=PEER_MESSAGE_TITLE_MAX_CHARS,
        )
        if parent_title:
            relation = (
                "your"
                if message.reply_to_message.direction == PeerMessageDirection.OUT
                else "their"
            )
            header += f", in reply to {relation} **“{parent_title}”**"
```

Also replace this exact import boundary near the start of the function body:

```python
    from twicc.cli._drop_request.sender_header import inline_md

    origin = message.origin or {}
```

with:

```python
    from twicc.cli._drop_request.sender_header import inline_md
    from twicc.core.models import PeerMessageDirection

    origin = message.origin or {}
```

The current message id does not pass through `inline_md`. The grammar is the safety proof and exact copy-back requires byte identity. The parent title remains untrusted Markdown and must pass through `inline_md`.

- [ ] **Step 5: Guard the legacy status callback**

In `_notify_status`, replace this exact opening:

```python
async def _notify_status(peer, message_id: str, status: str) -> None:
    """Best-effort status callback — failure never blocks local resolution
    (design §4.1)."""
    from twicc.peer import outbound
```

with:

```python
async def _notify_status(peer, message_id: str, status: str) -> None:
    """Best-effort status callback — failure never blocks local resolution
    (design §4.1)."""
    if PEER_MESSAGE_ID_PATTERN.fullmatch(message_id) is None:
        logger.info(
            "[peer_status_callback] skipped unsafe legacy message id peer=%s",
            peer.id,
        )
        return
    from twicc.peer import outbound
```

Do not move this guard into `post_status`. The shared outbound function receives only validated current ids after this task, while local delivery/refusal intentionally accepts legacy rows.

- [ ] **Step 6: Extend the purge test to prove threading metadata survives**

In `test_purge_expired_attachment_bytes`, create this parent immediately before `resolved_old`:

```python
    parent = _out_message(peer, message_id="pm_parent", thread_id="pm_parent")
```

Add these constructor arguments to `resolved_old`:

```python
        reply_to=parent.message_id,
        reply_to_message=parent,
        thread_id=parent.thread_id,
```

After `resolved_old.refresh_from_db()`, add:

```python
    assert resolved_old.reply_to == parent.message_id
    assert resolved_old.reply_to_message_id == parent.pk
    assert resolved_old.thread_id == parent.thread_id
```

- [ ] **Step 7: Run Task 3 tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_messages.py -q`

Expected: PASS. If a legacy id enters the URL callback, `status_callbacks` is non-empty. If hostile parent text escapes the single header line, the exact escaped substring or newline assertion fails. If purge clears relation data, the three metadata assertions fail.

- [ ] **Step 8: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/services/peer_messages.py tests/test_peer_messages.py`
Subject: `feat(peer): expose safe reply handles`
```

---

### Task 4: Add `twicc peer-send --reply-to` and prove the agent surface

**Files:**
- Modify: `src/twicc/cli/peer_send.py`
- Modify: `tests/test_peer_cli.py`

**Interfaces:**
- Consumes: Task 1's `PEER_MESSAGE_ID_PATTERN` and authoritative send-service `reply_to` contract.
- Consumes: Task 2's four new `peer-message` output fields.
- Consumes: Task 3's envelope handle as the source agents copy.
- Produces: CLI option `--reply-to MESSAGE_ID`, default `None`, with the exact help text from spec §11.2.
- Produces: local CLI failures `invalid_reply_to` and `unknown_reply_to`, both exit code 1.
- Produces: transport payload `reply_to`, unchanged for a conforming value and `""` for omitted or empty input.
- Preserves: MCP exposure through the existing Click-tree derivation. No MCP code changes.

- [ ] **Step 1: Add CLI tests for local invalid and unknown values**

In `tests/test_peer_cli.py`, extend imports with:

```python
from django.db.models.query import QuerySet
```

Insert after `test_peer_send_precheck_errors`:

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "bad_reply",
    [".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]", "x" * 41],
)
def test_peer_send_reply_to_rejects_nonconforming_value_before_lookup(
        bad_reply, monkeypatch):
    _active_peer()
    original_filter = QuerySet.filter

    def _reject_peer_message_lookup(queryset, *args, **kwargs):
        if queryset.model is PeerMessage:
            raise AssertionError("invalid reply_to reached PeerMessage lookup")
        return original_filter(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "filter", _reject_peer_message_lookup)
    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    res = invoke(["peer-send", "alice", "Subject", "hello", "--reply-to", bad_reply])

    assert res.exit_code == 1
    assert res.result["status"] == "validation_error"
    assert res.result["errors"][0]["code"] == "invalid_reply_to"


@pytest.mark.django_db(transaction=True)
def test_peer_send_reply_to_rejects_unknown_and_cross_peer_ids(monkeypatch):
    _active_peer()
    other = _active_peer(
        name="bob", base_url="https://bob.example.com", token_ours=mint_token(),
    )
    PeerMessage.objects.create(
        peer=other,
        direction=PeerMessageDirection.IN,
        message_id="other-message",
        thread_id="other-message",
        payload={"text": "other", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )
    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)

    for reply_to in ("unknown", "other-message"):
        res = invoke(["peer-send", "alice", "Subject", "hello", "--reply-to", reply_to])
        assert res.exit_code == 1
        assert res.result["status"] == "validation_error"
        assert res.result["errors"][0]["code"] == "unknown_reply_to"
```

The first test makes any `PeerMessage` query fail. The invalid-token branch must return before that query. Other model lookups still use the original `QuerySet.filter`.

- [ ] **Step 2: Add root, conforming-boundary, failed-parent, and output tests**

Insert after `test_peer_send_end_to_end_in_process`:

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cli_args", [[], ["--reply-to", ""]])
def test_peer_send_omitted_or_empty_reply_to_creates_root(monkeypatch, cli_args):
    peer = _active_peer()
    calls = []

    async def _fake_post(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        calls.append(reply_to)
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            argv = ["peer-send", peer.id, "Subject", "hello", *cli_args]
            return await asyncio.to_thread(invoke, argv)
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0
    message = PeerMessage.objects.get()
    assert message.reply_to == ""
    assert message.reply_to_message_id is None
    assert message.thread_id == message.message_id
    assert calls == [""]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("reply_to", ["A", "x" * 40])
def test_peer_send_conforming_reply_to_reaches_transport_unchanged(
        monkeypatch, reply_to):
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id=reply_to,
        thread_id="root",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.REFUSED,
    )
    calls = []

    async def _fake_post(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        calls.append(reply_to)
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, [
                "peer-send", peer.id, "Subject", "hello", "--reply-to", reply_to,
            ])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0
    child = PeerMessage.objects.exclude(pk=parent.pk).get()
    assert child.reply_to == reply_to
    assert child.reply_to_message_id == parent.pk
    assert child.thread_id == "root"
    assert calls == [reply_to]


@pytest.mark.django_db(transaction=True)
def test_peer_send_accepts_failed_outbound_parent(monkeypatch):
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="failed-parent",
        thread_id="failed-parent",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.FAILED,
    )

    async def _fake_post(base_url, **kwargs):
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, [
                "peer-send", peer.id, "Follow-up", "hello",
                "--reply-to", parent.message_id,
            ])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0
    child = PeerMessage.objects.exclude(pk=parent.pk).get()
    assert child.reply_to_message_id == parent.pk


@pytest.mark.django_db(transaction=True)
def test_peer_message_cli_outputs_all_threading_fields():
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id="parent",
        thread_id="parent",
        title="Parent",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.DELIVERED,
    )
    child = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="child",
        reply_to="parent",
        reply_to_message=parent,
        thread_id="parent",
        payload={"text": "child", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )

    res = invoke(["peer-message", child.message_id])

    assert res.exit_code == 0
    assert res.result["thread_id"] == "parent"
    assert res.result["reply_to"] == "parent"
    assert res.result["reply_to_ref"]["message_id"] == "parent"
    assert res.result["reply_target"] is None
```

- [ ] **Step 3: Run the CLI tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_cli.py -k 'reply_to or threading_fields or failed_outbound_parent' -q`

Expected: FAIL because Typer rejects unknown option `--reply-to` with exit code 2. This catches a missing CLI and therefore missing auto-derived MCP option.

- [ ] **Step 4: Declare the option**

In `src/twicc/cli/peer_send.py`, replace this exact boundary between `prompt` and `attach`:

```python
    ),
    attach: list[str] = typer.Option(
```

with:

```python
    ),
    reply_to: str | None = typer.Option(
        None,
        "--reply-to",
        help=(
            "The peer message this one answers (pm_…), taken from the "
            "header of a delivered peer message."
        ),
    ),
    attach: list[str] = typer.Option(
```

The first `),` in the replacement closes the existing `prompt` option. Keep the existing `attach` body after its restored declaration.

- [ ] **Step 5: Add the fast local grammar and peer-scoped lookup**

In the lazy model import block, replace:

```python
    from twicc.core.models import Peer, PeerState
    from twicc.core.services.peer_messages import validate_title
```

with:

```python
    from twicc.core.models import Peer, PeerMessage, PeerState
    from twicc.core.services.peer_messages import PEER_MESSAGE_ID_PATTERN, validate_title
```

Replace this exact title-validation boundary:

```python
    if title_error is not None:
        emit_validation_errors([ValidationError("TITLE", title_error.code, title_error.message)])
        raise typer.Exit(1)

    try:
        text = resolve_prompt(prompt)
```

with:

```python
    if title_error is not None:
        emit_validation_errors([ValidationError("TITLE", title_error.code, title_error.message)])
        raise typer.Exit(1)

    clean_reply_to = reply_to or ""
    if clean_reply_to and PEER_MESSAGE_ID_PATTERN.fullmatch(clean_reply_to) is None:
        emit_validation_errors([ValidationError(
            "--reply-to",
            "invalid_reply_to",
            "reply_to must be a valid peer message id",
        )])
        raise typer.Exit(1)
    if clean_reply_to and not PeerMessage.objects.filter(
            peer=peer_row, message_id=clean_reply_to).exists():
        emit_validation_errors([ValidationError(
            "--reply-to",
            "unknown_reply_to",
            "No message with this id exists for the selected peer.",
        )])
        raise typer.Exit(1)

    try:
        text = resolve_prompt(prompt)
```

The grammar check must remain before the ORM lookup. The lookup deliberately ignores direction and status.

- [ ] **Step 6: Put the normalized value on every transport payload**

In the existing payload block, replace this exact opening:

```python
    payload = {
        "peer": peer_row.id,
        "title": clean_title,
        "text": text,
```

with:

```python
    payload = {
        "peer": peer_row.id,
        "title": clean_title,
        "reply_to": clean_reply_to,
        "text": text,
```

Do not omit the key for roots. The service still accepts a missing key for direct RPC compatibility and mixed-version callers.

- [ ] **Step 7: Run Task 4 tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_cli.py -q`

Expected: PASS. An invalid value that reaches a message lookup raises the sentinel assertion. Trimming a boundary token changes the recorded call and fails byte identity. Rejecting a failed parent fails the explicit status case.

- [ ] **Step 8: Verify the generated MCP schema includes the option**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active python - <<'PY'
from twicc.mcp.tools import build_mcp_registry

tool = build_mcp_registry()["peer-send"]
properties = tool.json_schema["properties"]
assert "reply_to" in properties, properties
assert properties["reply_to"]["type"] == "string"
print("peer-send reply_to schema: OK")
PY
```

Expected: `peer-send reply_to schema: OK`. A CLI-only manual wiring that bypasses the Click tree fails this schema assertion.

- [ ] **Step 9: Run the complete lot 1 verification suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_messages.py tests/test_peer_cli.py tests/test_peer_threading_migration.py tests/test_peer_updates_consumer.py tests/test_mcp_tools.py -q`

Expected: PASS. This command catches cross-task regressions across persistence, async serialization, delivery, CLI, and generated MCP metadata.

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active python -m django makemigrations --check --dry-run --settings=twicc.settings`

Expected: `No changes detected`. This final check catches schema drift introduced after Task 1.

- [ ] **Step 10: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/cli/peer_send.py tests/test_peer_cli.py`
Subject: `feat(peer): add reply option to peer send`
```

---

## Lot 1 completion gate

- All four task commits exist in order.
- `git status --short` contains no implementation residue.
- The complete lot 1 verification command passes.
- `makemigrations --check --dry-run` reports no changes.
- No frontend, documentation, skill, plugin, historical design, or CHANGELOG file changed.
- The implementer reminds the user that backend changes require a `devctl.py` restart. The implementer does not restart it.
- The implementer reminds the user that the new migration applies on their next `devctl.py start` or restart. The implementer never runs `migrate` directly.
