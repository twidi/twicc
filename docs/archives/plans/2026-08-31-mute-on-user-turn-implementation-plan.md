# Per-session Mute on User Turn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mutable per-session flag that suppresses only the finished-working notification family.

**Architecture:** Store `mute_on_user_turn` on `Session`. Read it with `hidden` for every process-state broadcast, then send it to the frontend and Apprise dispatcher. Keep question, approval, death, usage, read-tracking, unread, ordering, and process-state behavior unchanged.

**Tech Stack:** Django 6, Channels, SQLite, Typer, the generated RPC/MCP registry, Vue 3, Pinia, Web Awesome, and Node's built-in test runner.

**Spec:** [`docs/plans/2026-08-12-mute-on-user-turn-design.md`](2026-08-12-mute-on-user-turn-design.md)

## Global Constraints

- `mute_on_user_turn=True` is the only muted value. False, a missing key, or a missing row means unmuted.
- The flag is not part of `AgentSettings`, presets, project defaults, or global settings.
- The flag has no database index and no relation to `hidden` validation.
- A muted session still emits every `process_state` message.
- Mute only suppresses the user-turn toast, sound, browser notification, and Apprise `notifyUserTurn` event.
- Mute never suppresses question or approval notifications.
- Mute never suppresses death, extra-usage, unread, ordering, active-process, or read-tracking behavior.
- Existing global channel settings stay authoritative. Mute can only remove a user-turn notification.
- Do not add a changelog entry without a separate explicit request.
- Do not add dependencies.
- Do not apply the migration to the user's running instance.
- Do not restart the user's development servers.

## Dependency Order

1. Persist and mutate the flag.
2. Carry and enforce it on backend notification paths.
3. Enforce it on frontend notification paths.
4. Add the session-header control.
5. Preserve it through session creation.
6. Expose singular, batch, RPC, and MCP mutations.
7. Update documentation and the plugin bundle.
8. Run the complete verification matrix.

## Files and Responsibilities

- `src/twicc/core/models.py` — durable flag and its model-level documentation.
- `src/twicc/core/migrations/0131_session_mute_on_user_turn.py` — schema migration.
- `src/twicc/core/serializers.py` — REST, WebSocket, CLI, and MCP session representation.
- `src/twicc/core/services/session_update.py` — shared write helper and drop-request mutation service.
- `src/twicc/views.py` — HTTP PATCH validation and combined `session_updated` broadcast.
- `src/twicc/asgi.py` — one-row broadcast lookup and `process_state` enrichment.
- `src/twicc/external_notifications.py` — Apprise user-turn gate with baseline preservation.
- `frontend/src/utils/processStateNotifications.js` — pure notification-effect calculation.
- `frontend/src/utils/processStateNotifications.test.js` — missing-key, mute-scope, and pending-request regression tests.
- `frontend/src/composables/useWebSocket.js` — apply the calculated frontend effects.
- `frontend/src/stores/data.js` — optimistic per-session mutation.
- `frontend/src/components/session/detail/SessionHeader.vue` — bell toggle, tooltip, and marked state.
- `src/twicc/cli/create_session/command.py` — `--mute-on-user-turn` creation flag.
- `src/twicc/core/services/session_creation.py` — creation payload extraction and pending-buffer write.
- `src/twicc/pending_session_attributes.py` — pre-row structural attribute.
- `src/twicc/providers/sessions_watcher.py` — pending attribute to new row.
- `src/twicc/agent/base_manager.py` — draft-id to canonical-id re-key.
- `src/twicc/cli/update_session/mute_command.py` — singular `mute` and `notify` commands.
- `src/twicc/cli/update_session/command.py` — singular command registration.
- `src/twicc/cli/update_sessions/command.py` — batch `mute` and `notify` commands.
- `src/twicc/drop_requests_watcher.py` — drop-request kind registration.
- `tests/test_mute_on_user_turn_api.py` — model, serializer, service, and HTTP tests.
- `tests/test_mute_on_user_turn_notifications.py` — backend broadcast and Apprise tests.
- `tests/test_mute_on_user_turn_creation.py` — creation, watcher, and Codex re-key tests.
- `tests/test_drop_transport.py` — in-process drop transport round trip.
- `tests/test_mcp_tools.py` — generated RPC/MCP surface.
- `SKILLS-AND-CLI.md` — public CLI reference.
- `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md` — creation guidance.
- `src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md` — singular mutation guidance.
- `src/twicc/agent/plugin/twicc/skills/twicc-update-sessions/SKILL.md` — batch mutation guidance.
- `src/twicc/agent/plugin/twicc/skills/twicc-orchestration/SKILL.md` — visibility and notification policy.
- `src/twicc/agent/plugin/twicc/skills/twicc-orchestration-leader/SKILL.md` — leader spawn guidance.
- `src/twicc/agent/plugin/twicc/skills/twicc-orchestration-manager/SKILL.md` — manager spawn guidance.
- `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` — plugin cache version.
- `AGENTS.md` and `CLAUDE.md` — database-model architecture notes.

---

## Task 1: Persist and Mutate the Session Flag

**Files:**

- Modify: `src/twicc/core/models.py`
- Create: `src/twicc/core/migrations/0131_session_mute_on_user_turn.py`
- Modify: `src/twicc/core/serializers.py`
- Modify: `src/twicc/core/services/session_update.py`
- Modify: `src/twicc/views.py`
- Create: `tests/test_mute_on_user_turn_api.py`

**Interfaces:**

- Produces `Session.mute_on_user_turn: bool` with a database default of `False`.
- Produces serialized key `mute_on_user_turn` on every session payload.
- Accepts `PATCH {"mute_on_user_turn": boolean}` on the existing session-detail endpoint.
- Produces `apply_session_mute_on_user_turn_change(session, value)` for HTTP and drop-request writers.

### Steps

- [ ] **Write the failing model, serializer, and HTTP tests.**

Create `tests/test_mute_on_user_turn_api.py` with these cases:

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import serialize_session


def _run(coro):
    return asyncio.run(coro)


def _make_session(*, muted=False):
    project = Project.objects.create(id="-mute-api", directory="/tmp/mute-api")
    return Session.objects.create(
        id="mute-api-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        mute_on_user_turn=muted,
    )


@pytest.mark.django_db
def test_session_defaults_to_unmuted_and_serializes_the_flag():
    session = _make_session()
    assert session.mute_on_user_turn is False
    assert serialize_session(session)["mute_on_user_turn"] is False


@pytest.mark.django_db(transaction=True)
def test_patch_rejects_a_non_boolean_mute_value():
    session = _make_session()
    response = _run(AsyncClient().patch(
        f"/api/projects/{session.project_id}/sessions/{session.id}/",
        data=orjson.dumps({"mute_on_user_turn": "true"}),
        content_type="application/json",
    ))
    assert response.status_code == 400
    assert response.json() == {"error": "mute_on_user_turn must be a boolean"}


@pytest.mark.django_db(transaction=True)
def test_patch_persists_and_broadcasts_mute_on_user_turn():
    session = _make_session()
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.views.get_channel_layer", return_value=layer):
        response = _run(AsyncClient().patch(
            f"/api/projects/{session.project_id}/sessions/{session.id}/",
            data=orjson.dumps({"mute_on_user_turn": True}),
            content_type="application/json",
        ))

    assert response.status_code == 200
    assert response.json()["mute_on_user_turn"] is True
    session.refresh_from_db()
    assert session.mute_on_user_turn is True
    payload = layer.group_send.await_args.args[1]["data"]
    assert payload["type"] == "session_updated"
    assert payload["session"]["mute_on_user_turn"] is True
```

- [ ] **Run the new tests and confirm the expected failure.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_mute_on_user_turn_api.py -q
```

Expected: collection or execution fails because `Session` has no `mute_on_user_turn` field.

- [ ] **Add the model field next to `hidden`.**

Add this field in the user-controlled block:

```python
# Suppress only the finished-working notification family for this session.
# Questions, approvals, failures, usage alerts, and process-state broadcasts
# remain active. This is TwiCC UI behavior, not an AgentSettings field.
mute_on_user_turn = models.BooleanField(default=False)
```

Do not add `db_index=True`.

- [ ] **Create the migration.**

Create `src/twicc/core/migrations/0131_session_mute_on_user_turn.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0130_project_icon"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="mute_on_user_turn",
            field=models.BooleanField(default=False),
        ),
    ]
```

- [ ] **Serialize the flag with the other user-controlled fields.**

Add this entry to `serialize_session()`:

```python
"mute_on_user_turn": session.mute_on_user_turn,
```

- [ ] **Add the shared write helper.**

Add this beside `apply_session_pinned_change()` in `session_update.py`:

```python
async def apply_session_mute_on_user_turn_change(session, mute_on_user_turn: bool) -> None:
    """Persist the per-session finished-working notification gate."""
    session.mute_on_user_turn = mute_on_user_turn
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["mute_on_user_turn"])
    )
```

The helper does not broadcast. Each caller owns one combined broadcast.

- [ ] **Add strict HTTP validation and reuse the shared helper.**

Add this branch before layout handling in `session_detail()`:

```python
if "mute_on_user_turn" in data:
    mute_on_user_turn = data["mute_on_user_turn"]
    if not isinstance(mute_on_user_turn, bool):
        return JsonResponse(
            {"error": "mute_on_user_turn must be a boolean"},
            status=400,
        )
    from twicc.core.services.session_update import (
        apply_session_mute_on_user_turn_change,
    )
    await apply_session_mute_on_user_turn_change(session, mute_on_user_turn)
    needs_broadcast = True
```

Keep the existing hidden-session broadcast guard unchanged.

- [ ] **Run the focused tests.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_mute_on_user_turn_api.py -q
```

Expected: all tests pass.

- [ ] **Check migration drift.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run python -m django makemigrations --check --dry-run --settings=twicc.settings
```

Expected: `No changes detected`.

- [ ] **Commit Task 1.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git add src/twicc/core/models.py src/twicc/core/migrations/0131_session_mute_on_user_turn.py src/twicc/core/serializers.py src/twicc/core/services/session_update.py src/twicc/views.py tests/test_mute_on_user_turn_api.py && git commit -m "feat: persist session notification mute" -m "Add the per-session mute flag, serialize it, and expose a strict HTTP mutation path with a shared locked write helper.

Co-Authored-By: Codex GPT-5.6 <codex@openai.com>"
```

---

## Task 2: Gate Backend Notifications Without Losing State

**Files:**

- Modify: `src/twicc/asgi.py`
- Modify: `src/twicc/external_notifications.py`
- Create: `tests/test_mute_on_user_turn_notifications.py`

**Interfaces:**

- `process_state` gains `mute_on_user_turn: bool`.
- `notify_agent_event()` gains `mute_on_user_turn: bool = False`.
- `_last_seen` updates on muted and unmuted broadcasts.
- Pending-request Apprise detection stays independent.

### Steps

- [ ] **Write the failing broadcast and Apprise tests.**

Create `tests/test_mute_on_user_turn_notifications.py`. Use a small `AgentInfo` factory with stable timestamps:

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from twicc import external_notifications
from twicc.agent.states import AgentInfo, AgentState, PendingRequest
from twicc.asgi import broadcast_process_state
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType


def _info(state, *, session_id="mute-notification", pending_requests=()):
    return AgentInfo(
        session_id=session_id,
        project_id="-mute-notification",
        provider=Provider.CODEX,
        state=state,
        previous_state=AgentState.ASSISTANT_TURN,
        started_at=1.0,
        state_changed_at=2.0,
        last_activity=2.0,
        pending_requests=pending_requests,
    )


@pytest.fixture(autouse=True)
def _reset_external_notification_baseline():
    external_notifications._last_seen.clear()
    yield
    external_notifications._last_seen.clear()


@pytest.mark.django_db(transaction=True)
def test_process_state_payload_carries_the_current_mute_flag():
    project = Project.objects.create(
        id="-mute-notification", directory="/tmp/mute-notification"
    )
    Session.objects.create(
        id="mute-notification",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        mute_on_user_turn=True,
    )
    layer = SimpleNamespace(group_send=AsyncMock())
    helpers = SimpleNamespace(enrich_agent_state=AsyncMock())
    notify = Mock()
    with patch("twicc.asgi.get_channel_layer", return_value=layer), \
            patch("twicc.asgi.get_provider_helpers", return_value=helpers), \
            patch(
                "twicc.asgi.get_session_and_project_display",
                new=AsyncMock(return_value=("Session", "Project", None)),
            ), \
            patch("twicc.asgi.notify_agent_event", notify):
        asyncio.run(broadcast_process_state(_info(AgentState.USER_TURN)))

    message = layer.group_send.await_args.args[1]["data"]
    assert message["mute_on_user_turn"] is True
    assert notify.call_args.args[-1] is True


@pytest.mark.django_db(transaction=True)
def test_missing_session_row_is_explicitly_unmuted():
    layer = SimpleNamespace(group_send=AsyncMock())
    helpers = SimpleNamespace(enrich_agent_state=AsyncMock())
    with patch("twicc.asgi.get_channel_layer", return_value=layer), \
            patch("twicc.asgi.get_provider_helpers", return_value=helpers), \
            patch(
                "twicc.asgi.get_session_and_project_display",
                new=AsyncMock(return_value=(None, None, None)),
            ), \
            patch("twicc.asgi.notify_agent_event"):
        asyncio.run(broadcast_process_state(_info(AgentState.USER_TURN)))

    message = layer.group_send.await_args.args[1]["data"]
    assert message["mute_on_user_turn"] is False


@pytest.mark.django_db(transaction=True)
def test_hidden_session_still_emits_no_process_state_or_apprise_event():
    project = Project.objects.create(
        id="-mute-notification", directory="/tmp/mute-notification"
    )
    Session.objects.create(
        id="mute-notification",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        hidden=True,
        mute_on_user_turn=True,
    )
    layer = SimpleNamespace(group_send=AsyncMock())
    notify = Mock()
    with patch("twicc.asgi.get_channel_layer", return_value=layer), \
            patch("twicc.asgi.notify_agent_event", notify):
        asyncio.run(broadcast_process_state(_info(AgentState.USER_TURN)))

    assert layer.group_send.await_count == 0
    assert notify.call_count == 0


def _notification_settings():
    return {
        "externalNotificationTargets": [{
            "url": "json://example.test",
            "enabled": True,
            "tested": True,
            "awayOnly": False,
            "notifyUserTurn": True,
            "notifyPendingRequest": True,
        }]
    }


def test_apprise_baseline_advances_during_a_muted_user_turn():
    spawned = []
    with patch.object(
        external_notifications, "read_synced_settings",
        return_value=_notification_settings(),
    ), patch.object(
        external_notifications, "get_provider_helpers",
        return_value=SimpleNamespace(LABEL="Codex"),
    ), patch.object(
        external_notifications, "_send",
        side_effect=lambda urls, title, body: (urls, title, body),
    ), patch.object(external_notifications, "_spawn", spawned.append):
        external_notifications.notify_agent_event(
            _info(AgentState.USER_TURN), "Session", "Project", None, True,
        )
        external_notifications.notify_agent_event(
            _info(AgentState.USER_TURN), "Session", "Project", None, False,
        )

    assert spawned == []


def test_muted_session_still_sends_pending_request_apprise_event():
    request = PendingRequest(
        request_id="request-1",
        request_type="ask_user_question",
        tool_name="request_user_input",
        tool_input={},
        created_at=3.0,
    )
    spawned = []
    with patch.object(
        external_notifications, "read_synced_settings",
        return_value=_notification_settings(),
    ), patch.object(
        external_notifications, "get_provider_helpers",
        return_value=SimpleNamespace(LABEL="Codex"),
    ), patch.object(
        external_notifications, "_send",
        side_effect=lambda urls, title, body: (urls, title, body),
    ), patch.object(external_notifications, "_spawn", spawned.append):
        external_notifications.notify_agent_event(
            _info(AgentState.ASSISTANT_TURN, pending_requests=(request,)),
            "Session", "Project", None, True,
        )

    assert len(spawned) == 1
    assert spawned[0][1] == "Codex has a question for you"
```

- [ ] **Run the tests and confirm the expected failures.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_mute_on_user_turn_notifications.py -q
```

Expected: the payload key and function parameter are missing.

- [ ] **Widen the existing row lookup without adding a query.**

Replace the single-field lookup in `broadcast_process_state()` with:

```python
session_flags = await sync_to_async(
    lambda: Session.objects.filter(pk=info.session_id)
    .values_list("hidden", "mute_on_user_turn")
    .first()
)()
is_hidden, mute_on_user_turn = session_flags or (False, False)
if is_hidden:
    return
```

After `message["type"] = "process_state"`, add:

```python
message["mute_on_user_turn"] = mute_on_user_turn is True
```

Pass the same explicit boolean to `notify_agent_event()` as its last argument.

- [ ] **Add the Apprise parameter and gate only the user-turn event append.**

Use a default to preserve compatibility with direct callers:

```python
def notify_agent_event(
    info: AgentInfo,
    session_title: str | None,
    project_name: str | None,
    project_parent_name: str | None,
    mute_on_user_turn: bool = False,
) -> None:
```

Forward it to `_detect_and_send()`. Keep `_last_seen` mutation before target and event selection.

Change only the user-turn append condition:

```python
if (
    not mute_on_user_turn
    and info.state == AgentState.USER_TURN
    and (previous is None or previous[0] != AgentState.USER_TURN)
):
    events.append((f"{label} finished working", "notifyUserTurn"))
```

Do not return early when muted. Leave pending-request detection below this condition.

- [ ] **Run the focused tests.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_mute_on_user_turn_notifications.py -q
```

Expected: all tests pass.

- [ ] **Run the related hidden-session tests.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_agent_hidden_broadcast_gate.py tests/test_hidden_sessions_snapshot.py -q
```

Expected: hidden sessions still emit no process or stream events.

- [ ] **Commit Task 2.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git add src/twicc/asgi.py src/twicc/external_notifications.py tests/test_mute_on_user_turn_notifications.py && git commit -m "feat: gate finished notifications per session" -m "Carry the current mute flag on process-state events and suppress only user-turn Apprise events while preserving the transition baseline and pending requests.

Co-Authored-By: Codex GPT-5.6 <codex@openai.com>"
```

---

## Task 3: Gate Frontend Notification Effects

**Files:**

- Create: `frontend/src/utils/processStateNotifications.js`
- Create: `frontend/src/utils/processStateNotifications.test.js`
- Modify: `frontend/src/composables/useWebSocket.js`

**Interfaces:**

- Produces a pure `getProcessStateNotificationEffects()` function.
- Consumes `process_state.mute_on_user_turn` with explicit-true mute semantics.
- Keeps `forceNotifySessionViewed` outside the mute gate.
- Keeps all pending-request effects outside the mute gate.

### Steps

- [ ] **Write the failing pure-effect tests.**

Create `frontend/src/utils/processStateNotifications.test.js`:

```javascript
import test from 'node:test'
import assert from 'node:assert/strict'

import { getProcessStateNotificationEffects } from './processStateNotifications.js'


const options = {
    isViewingSession: false,
    userTurnBrowserEnabled: true,
    pendingRequestBrowserEnabled: true,
}


test('a missing mute key keeps every user-turn notification effect enabled', () => {
    const effects = getProcessStateNotificationEffects(
        { state: 'user_turn', pending_requests: [] },
        { state: 'assistant_turn', pending_requests: [] },
        options,
    )

    assert.equal(effects.showUserTurnToast, true)
    assert.equal(effects.playUserTurnSound, true)
    assert.equal(effects.sendUserTurnBrowser, true)
})


test('mute suppresses user-turn effects but preserves pending-request effects', () => {
    const effects = getProcessStateNotificationEffects(
        {
            state: 'user_turn',
            mute_on_user_turn: true,
            pending_requests: [{ request_id: 'request-1' }],
        },
        { state: 'assistant_turn', pending_requests: [] },
        options,
    )

    assert.equal(effects.showUserTurnToast, false)
    assert.equal(effects.playUserTurnSound, false)
    assert.equal(effects.sendUserTurnBrowser, false)
    assert.equal(effects.showPendingRequestToast, true)
    assert.equal(effects.playPendingRequestSound, true)
    assert.equal(effects.sendPendingRequestBrowser, true)
})


test('mute does not suppress read tracking for a viewed session', () => {
    const effects = getProcessStateNotificationEffects(
        { state: 'user_turn', mute_on_user_turn: true },
        { state: 'assistant_turn' },
        { ...options, isViewingSession: true },
    )

    assert.equal(effects.markViewed, true)
})
```

- [ ] **Run the test and confirm the expected module-not-found failure.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && cd frontend && npm test -- --test-name-pattern="mute|missing mute"
```

Expected: the new utility does not exist.

- [ ] **Implement the pure effect calculation.**

Create `frontend/src/utils/processStateNotifications.js`:

```javascript
export function getProcessStateNotificationEffects(msg, previousState, options) {
    const enteredUserTurn = msg.state === 'user_turn'
        && previousState?.state !== 'user_turn'
    const userTurnEnabled = enteredUserTurn && msg.mute_on_user_turn !== true
    const newPendingCount = msg.pending_requests?.length || 0
    const previousPendingCount = previousState?.pending_requests?.length || 0
    const pendingRequestGrew = newPendingCount > previousPendingCount

    return {
        markViewed: enteredUserTurn && options.isViewingSession,
        showUserTurnToast: userTurnEnabled && !options.isViewingSession,
        playUserTurnSound: userTurnEnabled,
        sendUserTurnBrowser: userTurnEnabled && options.userTurnBrowserEnabled,
        showPendingRequestToast: pendingRequestGrew && !options.isViewingSession,
        playPendingRequestSound: pendingRequestGrew,
        sendPendingRequestBrowser: pendingRequestGrew
            && options.pendingRequestBrowserEnabled,
        newPendingCount,
    }
}
```

- [ ] **Use the effect calculation in `notifyProcessStateChange()`.**

Import the utility from `../utils/processStateNotifications.js`.

At the start of the function, calculate `isViewingSession` and the effect object:

```javascript
const isViewingSession = route?.params?.sessionId === sessionId
const effects = getProcessStateNotificationEffects(msg, previousState, {
    isViewingSession,
    userTurnBrowserEnabled: settings.notifUserTurnBrowser,
    pendingRequestBrowserEnabled: settings.notifPendingRequestBrowser,
})
```

Then apply each effect independently:

- `effects.markViewed` calls `forceNotifySessionViewed`.
- `effects.showUserTurnToast` runs the existing deduplicated user-turn toast block.
- `effects.playUserTurnSound` calls `playNotificationSound(settings.notifUserTurnSound)`.
- `effects.sendUserTurnBrowser` sends the existing browser notification.
- `effects.showPendingRequestToast` shows the existing question or approval toast.
- `effects.playPendingRequestSound` calls the existing pending sound.
- `effects.sendPendingRequestBrowser` sends the existing pending browser notification.
- Use `effects.newPendingCount` to select the freshest request.

Do not put one outer mute condition around `notifyProcessStateChange()`.

- [ ] **Run the frontend tests.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && cd frontend && npm test
```

Expected: all frontend tests pass.

- [ ] **Commit Task 3.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git add frontend/src/utils/processStateNotifications.js frontend/src/utils/processStateNotifications.test.js frontend/src/composables/useWebSocket.js && git commit -m "feat: mute frontend user-turn alerts" -m "Calculate notification effects in a tested pure helper so per-session mute suppresses finished-working alerts without suppressing read tracking or pending requests.

Co-Authored-By: Codex GPT-5.6 <codex@openai.com>"
```

---

## Task 4: Add the Session Header Toggle

**Files:**

- Modify: `frontend/src/stores/data.js`
- Modify: `frontend/src/components/session/detail/SessionHeader.vue`

**Interfaces:**

- Produces `setSessionMuteOnUserTurn(projectId, sessionId, value)`.
- Produces one non-draft header toggle between pin and archive.
- Uses `bell` for unmuted and `bell-slash` for muted.

### Steps

- [ ] **Add the optimistic Pinia action.**

Model it on `setSessionPinMode()`:

```javascript
async setSessionMuteOnUserTurn(projectId, sessionId, value) {
    const session = this.sessions[sessionId]
    const oldValue = session?.mute_on_user_turn

    if (session) {
        session.mute_on_user_turn = value
    }

    try {
        const response = await apiFetch(
            `/api/projects/${projectId}/sessions/${sessionId}/`,
            {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mute_on_user_turn: value }),
            },
        )
        if (!response.ok) {
            const data = await response.json()
            throw new Error(data.error || 'Failed to update session notifications')
        }
        const updatedSession = await response.json()
        this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }
    } catch (error) {
        if (session && oldValue !== undefined) {
            session.mute_on_user_turn = oldValue
        }
        throw error
    }
},
```

- [ ] **Add the header handler and exact tooltip text.**

Add these script bindings near the pin bindings:

```javascript
const muteTooltip = computed(() => session.value?.mute_on_user_turn
    ? 'Muted — click to restore the "finished working" notification'
    : 'Notifications on — click to mute the "finished working" notification')

function handleMuteToggle() {
    if (!session.value || session.value.draft) return
    store.setSessionMuteOnUserTurn(
        session.value.project_id,
        props.sessionId,
        !session.value.mute_on_user_turn,
    )
}
```

- [ ] **Insert the control between pin and archive.**

Add this template block after the pin tooltip:

```vue
<wa-button
    v-if="!session.draft"
    :id="`session-header-${sessionId}-mute-button`"
    :variant="session.mute_on_user_turn ? 'warning' : 'neutral'"
    appearance="plain"
    size="small"
    :class="['mute-button', 'reduced-height', {
        'mute-button--active': session.mute_on_user_turn,
    }]"
    @click="handleMuteToggle"
>
    <wa-icon
        :name="session.mute_on_user_turn ? 'bell-slash' : 'bell'"
        :label="session.mute_on_user_turn ? 'Muted' : 'Notifications on'"
    ></wa-icon>
</wa-button>
<AppTooltip
    v-if="!session.draft"
    :for="`session-header-${sessionId}-mute-button`"
>{{ muteTooltip }}</AppTooltip>
```

Add `.mute-button` to the existing action-button selector and hover selector. Add a distinct active rule:

```css
.mute-button.mute-button--active {
    opacity: 1;

    &::part(base) {
        color: var(--wa-color-warning-60);
    }
}
```

Do not add a list indicator or an agent-settings control.

- [ ] **Run frontend unit tests and the production build.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && cd frontend && npm test
cd /home/twidi/dev/twicc-poc/.worktrees/mute && cd frontend && npm run build
```

Expected: both commands succeed. No new Web Awesome import is needed because `wa-button` and `wa-icon` are already registered.

- [ ] **Perform the manual visual check.**

Verify these cases in the session header:

- Unmuted session: neutral `bell`, exact unmuted tooltip.
- Muted session: marked `bell-slash`, exact muted tooltip.
- The muted state differs from the pin button's active brand state.
- The button remains visible on an archived session.
- The button is absent from a draft.
- Clicking updates immediately, survives reload, and rolls back after a forced HTTP failure.

- [ ] **Commit Task 4.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git add frontend/src/stores/data.js frontend/src/components/session/detail/SessionHeader.vue && git commit -m "feat: add session notification mute toggle" -m "Add an optimistic session-header bell control with explicit muted styling, exact tooltips, and persistence through the shared session PATCH endpoint.

Co-Authored-By: Codex GPT-5.6 <codex@openai.com>"
```

---

## Task 5: Preserve Mute Through Session Creation

**Files:**

- Modify: `src/twicc/cli/create_session/command.py`
- Modify: `src/twicc/core/services/session_creation.py`
- Modify: `src/twicc/pending_session_attributes.py`
- Modify: `src/twicc/providers/sessions_watcher.py`
- Modify: `src/twicc/agent/base_manager.py`
- Create: `tests/test_mute_on_user_turn_creation.py`

**Interfaces:**

- Accepts `twicc create-session --mute-on-user-turn`.
- Carries the value in the `session:create` payload.
- Preserves the value before the session row exists.
- Preserves the value when Codex replaces the draft id with a canonical id.
- Defaults discovered sessions to unmuted.

### Steps

- [ ] **Write the failing creation-service, watcher, and re-key tests.**

Create `tests/test_mute_on_user_turn_creation.py` with three focused cases:

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc.agent.base_manager import BaseAgentManager
from twicc.core.enums import Provider
from twicc.core.models import Project, SessionType
from twicc.core.services.session_creation import create_session_from_payload
from twicc.pending_agent_settings import pop_pending_agent_settings
from twicc.pending_session_attributes import (
    get_pending_session_attributes,
    pop_pending_session_attributes,
    set_pending_session_attributes,
)
from twicc.providers.helpers import AgentSettings
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile


class _Compute:
    provider = Provider.CODEX
    compute_version = 1


class _Watcher(BaseSessionsWatcher):
    def get_compute(self):
        return _Compute()


class _Manager(BaseAgentManager):
    provider = Provider.CODEX

    async def _create_agent(self, session_id, project_id, cwd, **kwargs):
        return SimpleNamespace(
            session_id="canonical-id",
            interrupt_or_kill=AsyncMock(),
        )


@pytest.mark.django_db(transaction=True)
def test_creation_service_puts_mute_in_the_pending_buffer(tmp_path):
    project = Project.objects.create(
        id="-mute-create", directory=str(tmp_path)
    )
    manager = SimpleNamespace(create_session=AsyncMock(return_value="draft-id"))
    registry = SimpleNamespace(get=lambda provider: manager)
    payload = {
        "session_id": "draft-id",
        "project_id": project.id,
        "provider": Provider.CODEX.value,
        "text": "Work",
        "layout": {},
        "mute_on_user_turn": True,
    }

    try:
        with patch(
            "twicc.core.services.session_creation.ensure_provider_running"
        ), patch(
            "twicc.agent.registry.get_agent_manager_registry",
            return_value=registry,
        ):
            result = asyncio.run(create_session_from_payload(payload))
        assert result.success is True
        assert get_pending_session_attributes("draft-id").mute_on_user_turn is True
    finally:
        pop_pending_agent_settings("draft-id")
        pop_pending_session_attributes("draft-id")


@pytest.mark.django_db
def test_watcher_copies_mute_and_discovered_sessions_default_to_unmuted(tmp_path):
    project = Project.objects.create(
        id="-mute-watch", directory=str(tmp_path)
    )
    watcher = _Watcher()
    set_pending_session_attributes("created-id", mute_on_user_turn=True)
    created = watcher.create_session_sync(ParsedSessionFile(
        project.id, "created-id", SessionType.SESSION, "created.jsonl"
    ), project)
    discovered = watcher.create_session_sync(ParsedSessionFile(
        project.id, "discovered-id", SessionType.SESSION, "discovered.jsonl"
    ), project)

    assert created.mute_on_user_turn is True
    assert discovered.mute_on_user_turn is False


def test_canonical_id_rekey_preserves_mute_on_user_turn():
    manager = _Manager()
    manager.notify_session_bound = AsyncMock()
    manager._register_and_start = AsyncMock()
    set_pending_session_attributes("draft-id", mute_on_user_turn=True)

    try:
        canonical_id = asyncio.run(manager._start_agent(
            "draft-id",
            "-mute-create",
            "/tmp/mute-create",
            "Work",
            False,
            settings=AgentSettings(),
        ))
        pending = get_pending_session_attributes("canonical-id")
        assert canonical_id == "canonical-id"
        assert pending.mute_on_user_turn is True
    finally:
        pop_pending_session_attributes("draft-id")
        pop_pending_session_attributes("canonical-id")
```

- [ ] **Run the tests and confirm the expected failures.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_mute_on_user_turn_creation.py -q
```

Expected: the pending tuple and creation path do not accept the new field.

- [ ] **Add the plain creation flag and payload key.**

Add this Typer option next to `hidden`:

```python
mute_on_user_turn: bool = typer.Option(
    False,
    "--mute-on-user-turn",
    help=(
        "Suppress this session's finished-working toast, sound, browser "
        "notification, and Apprise user-turn event. Questions, approvals, "
        "failures, and usage alerts remain enabled."
    ),
),
```

Add this payload entry:

```python
"mute_on_user_turn": mute_on_user_turn,
```

Do not add a `--no-mute-on-user-turn` option.

- [ ] **Extract the creation payload value explicitly.**

In `create_session_from_payload()` add:

```python
mute_on_user_turn = payload.get("mute_on_user_turn") is True
```

Pass it to `set_pending_session_attributes()`.

- [ ] **Extend the pending structural tuple and setter.**

Add `mute_on_user_turn: bool` directly after `hidden`. Add a keyword argument with default `False`, then pass it into `PendingSessionAttributes`.

Include the field in the debug log. Keep the field outside `AgentSettings` and `compose_addendum()`.

- [ ] **Copy the pending value into the new row.**

In `BaseSessionsWatcher.create_session_sync()` add this unconditional copy beside `hidden`:

```python
kwargs["mute_on_user_turn"] = pending.mute_on_user_turn
```

No pending entry means the model default remains `False`.

- [ ] **Forward the value during draft-id to canonical-id re-key.**

Add this argument to the existing `set_pending_session_attributes()` call in `BaseAgentManager._start_agent()`:

```python
mute_on_user_turn=pending_attrs.mute_on_user_turn,
```

Do not introduce an agent cache or a runtime agent setting.

- [ ] **Run the focused creation tests.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_mute_on_user_turn_creation.py tests/test_work_dirs.py -q
```

Expected: all tests pass, including the existing pending-attribute consumer.

- [ ] **Commit Task 5.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git add src/twicc/cli/create_session/command.py src/twicc/core/services/session_creation.py src/twicc/pending_session_attributes.py src/twicc/providers/sessions_watcher.py src/twicc/agent/base_manager.py tests/test_mute_on_user_turn_creation.py && git commit -m "feat: support mute at session creation" -m "Expose a creation flag and preserve it through the pending structural buffer, watcher row creation, and Codex draft-to-canonical re-key.

Co-Authored-By: Codex GPT-5.6 <codex@openai.com>"
```

---

## Task 6: Add CLI, Batch, Drop-request, RPC, and MCP Mutations

**Files:**

- Create: `src/twicc/cli/update_session/mute_command.py`
- Modify: `src/twicc/cli/update_session/command.py`
- Modify: `src/twicc/cli/update_sessions/command.py`
- Modify: `src/twicc/core/services/session_update.py`
- Modify: `src/twicc/drop_requests_watcher.py`
- Modify: `tests/test_drop_transport.py`
- Modify: `tests/test_mcp_tools.py`

**Interfaces:**

- Produces `update-session <ID> mute` and `update-session <ID> notify`.
- Produces `update-sessions mute` and `update-sessions notify` with existing selectors.
- Produces drop kind `session:update_mute_on_user_turn`.
- Produces four generated MCP tools.
- Produces a boolean `mute_on_user_turn` field in the generated `create_session` schema.

### Steps

- [ ] **Write the failing dispatcher and generated-tool tests.**

Add this round-trip case to `tests/test_drop_transport.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType


@pytest.mark.django_db(transaction=True)
def test_execute_mute_on_user_turn_roundtrip():
    project = Project.objects.create(id="-mute-drop", directory="/tmp/mute-drop")
    session = Session.objects.create(
        id="mute-drop-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
    )
    layer = SimpleNamespace(group_send=AsyncMock())
    payload = {
        "session_id": session.id,
        "mute_on_user_turn": True,
    }

    with patch(
        "twicc.core.services.session_update.get_channel_layer",
        return_value=layer,
    ):
        status = asyncio.run(execute_drop_payload(
            payload, "session:update_mute_on_user_turn"
        ))

    assert status["status"] == "updated"
    session.refresh_from_db()
    assert session.mute_on_user_turn is True
    message = layer.group_send.await_args.args[1]["data"]
    assert message["session"]["mute_on_user_turn"] is True


@pytest.mark.django_db(transaction=True)
def test_execute_mute_rejects_a_non_boolean_value():
    project = Project.objects.create(
        id="-mute-drop-invalid", directory="/tmp/mute-drop-invalid"
    )
    session = Session.objects.create(
        id="mute-drop-invalid-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
    )

    status = asyncio.run(execute_drop_payload(
        {"session_id": session.id, "mute_on_user_turn": "true"},
        "session:update_mute_on_user_turn",
    ))

    assert status["status"] == "rejected"
    assert status["errors"][0]["code"] == "invalid_mute_on_user_turn"
    session.refresh_from_db()
    assert session.mute_on_user_turn is False


@pytest.mark.django_db(transaction=True)
def test_execute_mute_accepts_hidden_session_without_broadcasting():
    project = Project.objects.create(
        id="-mute-drop-hidden", directory="/tmp/mute-drop-hidden"
    )
    session = Session.objects.create(
        id="mute-drop-hidden-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        hidden=True,
    )
    layer = SimpleNamespace(group_send=AsyncMock())

    with patch(
        "twicc.core.services.session_update.get_channel_layer",
        return_value=layer,
    ):
        status = asyncio.run(execute_drop_payload(
            {"session_id": session.id, "mute_on_user_turn": True},
            "session:update_mute_on_user_turn",
        ))

    assert status["status"] == "updated"
    session.refresh_from_db()
    assert session.mute_on_user_turn is True
    assert layer.group_send.await_count == 0
```

Extend `test_tool_names_are_mcp_safe_and_bijective()` in `tests/test_mcp_tools.py`:

```python
for name in (
    "update_session_mute",
    "update_session_notify",
    "update_sessions_mute",
    "update_sessions_notify",
):
    assert name in names
```

Extend `test_schemas_and_descriptions()`:

```python
create_properties = by_name["create_session"].inputSchema["properties"]
assert create_properties["mute_on_user_turn"]["type"] == "boolean"
assert "finished-working" in create_properties["mute_on_user_turn"]["description"]
```

- [ ] **Run the focused tests and confirm the expected failures.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_drop_transport.py tests/test_mcp_tools.py -q
```

Expected: the drop kind and generated command tools are absent.

- [ ] **Add the backend mutation service.**

Add `update_session_mute_on_user_turn_from_payload()` beside the pinned service. It must:

1. Require `session_id`.
2. Require `mute_on_user_turn` to exist.
3. Reject every non-boolean with code `invalid_mute_on_user_turn`.
4. Call `_lookup_session_for_update()`.
5. Call `apply_session_mute_on_user_turn_change()`.
6. Broadcast serialized `session_updated` unless `session.hidden` is true.
7. Return the existing result shape:

```python
return UpdateSessionResult(
    success=True,
    session_id=session_id,
    provider=provider.value,
    project_id=session.project_id,
    errors=None,
)
```

Use an object sentinel. Do not compare the value with a string sentinel because a caller can submit that string.

- [ ] **Register the drop kind.**

Add this entry to `_KIND_HANDLERS`:

```python
"session:update_mute_on_user_turn": (
    "twicc.core.services.session_update",
    "update_session_mute_on_user_turn_from_payload",
    "updated",
),
```

- [ ] **Create the singular commands.**

Create `src/twicc/cli/update_session/mute_command.py` from the pinned command's transport pattern.

The shared helper submits:

```python
payload = {
    "session_id": resolved.session_id,
    "mute_on_user_turn": mute_on_user_turn,
}
sub = transport.submit(
    payload,
    kind="session:update_mute_on_user_turn",
)
```

Expose these commands:

- `update_mute_cmd`: sends `True`. Help states that only finished-working notifications are suppressed.
- `update_notify_cmd`: sends `False`. Help states that existing global notification settings still apply.

Use the same server check, session lookup, timeout, exit statuses, and cleanup as `pinned_command.py`.

- [ ] **Register the singular commands.**

Import both functions in `src/twicc/cli/update_session/command.py`. Register names `mute` and `notify` on `update_session_app`.

Update the module docstring and app help. Do not add them to `settings`.

- [ ] **Add the batch commands.**

Add two functions in `src/twicc/cli/update_sessions/command.py`. Each takes the existing shared selector and timeout options.

The only differing expression is the boolean:

```python
run_batch(
    session_ids or [],
    kind="session:update_mute_on_user_turn",
    prepare=lambda resolved: {
        "session_id": resolved.session_id,
        "mute_on_user_turn": True,
    },
    timeout=timeout,
    spawned_by=spawned_by,
    descendants=descendants,
    annotation=annotation,
)
```

Use `False` for `notify`. Update the top-level help and docstring lists.

- [ ] **Run the focused tests.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_drop_transport.py tests/test_mcp_tools.py -q
```

Expected: all tests pass. The MCP tools appear without hand-written MCP code.

- [ ] **Inspect the generated CLI and schemas.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && uv run twicc create-session --help
cd /home/twidi/dev/twicc-poc/.worktrees/mute && uv run twicc update-session --help
cd /home/twidi/dev/twicc-poc/.worktrees/mute && uv run twicc update-sessions --help
```

Expected: creation shows `--mute-on-user-turn`; singular and batch help show `mute` and `notify`.

- [ ] **Commit Task 6.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git add src/twicc/cli/update_session/mute_command.py src/twicc/cli/update_session/command.py src/twicc/cli/update_sessions/command.py src/twicc/core/services/session_update.py src/twicc/drop_requests_watcher.py tests/test_drop_transport.py tests/test_mcp_tools.py && git commit -m "feat: expose session mute controls" -m "Add singular and batch CLI mutations through the shared drop-request service. Let the generated RPC and MCP registries expose the same commands automatically.

Co-Authored-By: Codex GPT-5.6 <codex@openai.com>"
```

---

## Task 7: Update Public and Agent Documentation

**Files:**

- Modify: `SKILLS-AND-CLI.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-update-sessions/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-orchestration/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-orchestration-leader/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-orchestration-manager/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

**Interfaces:**

- Documents every new flag and command.
- Gives agents the correct mute-versus-hidden orchestration rule.
- Bumps the plugin bundle from `0.65.1` to `0.66.0`.

### Steps

- [ ] **Update `SKILLS-AND-CLI.md`.**

Add `--mute-on-user-turn` under creation metadata or behavior. State its exact scope.

Add `mute` and `notify` to singular and batch subcommand lists. State that `notify` restores the per-session path but does not override global settings.

- [ ] **Update `twicc-create-session`.**

Add `--mute-on-user-turn` as a separate session-behavior option, not an agent setting.

The description must tell an agent when to choose it. It suppresses finished-working notifications for a session the agent controls. It preserves questions and approvals.

Add one visible, muted creation example. Update the frontmatter description only if needed for accurate skill selection.

- [ ] **Update singular and batch mutation skills.**

For `twicc-update-session`:

- Change the subcommand count from nine to eleven.
- Add `mute` and `notify` to frontmatter, lead, use cases, usage, examples, and related batch guidance.
- State that no agent restart occurs.

For `twicc-update-sessions`:

- Change the subcommand count from eight to ten.
- Add `mute` and `notify` to frontmatter, lead, use cases, usage, and examples.
- Show `update-sessions mute --spawned-by self` as the orchestration example.

- [ ] **Add a separate orchestration notification section.**

In `twicc-orchestration/SKILL.md`, add a new section after visibility guidance. Use this order:

1. `--hidden` and `--mute-on-user-turn` are independent.
2. Hide a child when the user has no reason to read it.
3. Keep a child visible, muted, and `--no-question-widget` when the controlling agent reads and handles its result.
4. Keep a child unmuted when the user is expected to read and act on its result.
5. Questions and approvals still notify when the child is muted.

Replace the current strong hidden recommendation with the new visibility rule. Preserve user override and permission propagation rules.

- [ ] **Update leader and manager restatements only.**

In the leader and manager skills, change only the paragraphs that already restate spawn flags. Refer back to the shared orchestration skill for the full policy.

Do not duplicate the full explanation in both role skills.

- [ ] **Update the architecture notes.**

In both `AGENTS.md` and `CLAUDE.md`, extend the `Session` bullet with these facts:

- `mute_on_user_turn` is mutable per-session UI state.
- It suppresses only the finished-working family.
- It is independent of `hidden`.
- It is outside the closed `AgentSettings` bundle.

- [ ] **Bump the plugin bundle minor version.**

Change:

```json
"version": "0.65.1"
```

to:

```json
"version": "0.66.0"
```

The minor bump is required because existing skills gain new flags and options.

- [ ] **Review documentation consistency.**

Run these checks:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && grep -RIn -- '--mute-on-user-turn\|update-session.*mute\|update-sessions.*mute' SKILLS-AND-CLI.md src/twicc/agent/plugin/twicc/skills
cd /home/twidi/dev/twicc-poc/.worktrees/mute && grep -n 'mute_on_user_turn' AGENTS.md CLAUDE.md
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git diff --check
```

Expected: every public surface appears, both architecture files mention the field, and no whitespace errors exist.

- [ ] **Confirm the changelog remains untouched.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git diff --name-only | grep '^CHANGELOG.md$' && exit 1 || true
```

Expected: no output.

- [ ] **Commit Task 7.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git add SKILLS-AND-CLI.md src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-update-sessions/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-orchestration/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-orchestration-leader/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-orchestration-manager/SKILL.md src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json AGENTS.md CLAUDE.md && git commit -m "docs: document session notification mute" -m "Document creation and mutation commands, define the orchestration visibility and mute policy, update architecture notes, and bump the bundled TwiCC plugin minor version.

Co-Authored-By: Codex GPT-5.6 <codex@openai.com>"
```

---

## Task 8: Complete Verification and Handoff

**Files:** No planned file changes.

### Steps

- [ ] **Run every focused backend regression.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest tests/test_mute_on_user_turn_api.py tests/test_mute_on_user_turn_notifications.py tests/test_mute_on_user_turn_creation.py tests/test_drop_transport.py tests/test_mcp_tools.py tests/test_agent_hidden_broadcast_gate.py tests/test_hidden_sessions_snapshot.py tests/test_work_dirs.py -q
```

- [ ] **Run the complete backend test suite.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run pytest
```

- [ ] **Run Python lint.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && uvx ruff check .
```

- [ ] **Run every frontend test.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && cd frontend && npm test
```

- [ ] **Build the frontend.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && cd frontend && npm run build
```

- [ ] **Check migration drift and diff hygiene.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && TWICC_DATA_DIR=$PWD uv run python -m django makemigrations --check --dry-run --settings=twicc.settings
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git diff --check
cd /home/twidi/dev/twicc-poc/.worktrees/mute && git status --short
```

- [ ] **Run the manual behavior matrix.**

Use one visible test session and one Apprise target configured as enabled and tested.

1. Unmuted user-turn: toast, configured sound, configured browser notification, and opted-in Apprise event follow global settings.
2. Muted user-turn: none of those four finished-working effects occurs.
3. Muted question widget: in-app toast, pending sound, pending browser notification, and opted-in Apprise pending event still occur.
4. Muted permission request: the same pending-request channels still occur.
5. Muted session viewed at completion: `last_viewed_at` advances and no stale unread badge remains.
6. Unmute while already at `user_turn`: no delayed Apprise event occurs.
7. Hidden and muted session: no process-state notification occurs. Mutation through CLI still succeeds.
8. Archived session: the button remains visible and mutable.
9. Create with `--mute-on-user-turn`: the created row is muted for Claude Code and Codex.
10. Batch `mute --spawned-by self`, then `notify --spawned-by self`: every eligible child changes state.

- [ ] **Review the final diff against the spec.**

Confirm each item in design section 6 has an automated or named manual observation. Search for accidental scope expansion:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mute && grep -RIn 'mute_on_user_turn' src frontend tests AGENTS.md CLAUDE.md SKILLS-AND-CLI.md
```

The flag must not appear in `AgentSettings`, provider setting categories, presets, project defaults, or the system-prompt addendum.

- [ ] **Report user-owned follow-up operations.**

Tell the user to restart the backend through `devctl.py` after integration. The restart applies the migration automatically. Do not run `migrate` or restart the server as part of implementation.

## Execution Choices

1. **Current session:** use `superpowers:subagent-driven-development` and complete one reviewed task at a time.
2. **Separate session:** open a fresh implementation session and use `superpowers:executing-plans` with this file.
