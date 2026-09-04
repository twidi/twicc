"""
Send notifications to external services (via Apprise) on agent events.

Mirrors the browser notifications fired by the frontend
(``notifyProcessStateChange()`` in ``frontend/src/composables/useWebSocket.js``):
"agent finished working" (transition to USER_TURN) and "agent needs your
attention" (a new pending request appeared). It also carries the single peer
event — an incoming message or an incoming pairing request, both meaning
another human waits on you. Targets are user-configured
Apprise URLs (https://appriseit.com) stored in the synced settings
(``externalNotificationTargets``); the whole pipeline is outbound-only and
fire-and-forget — a notification failure must never affect the broadcast path.

Each target can opt into presence gating via its ``awayOnly`` flag: an
away-only target is held while a human looks present at any TwiCC client and
sent only once they are away (see :mod:`twicc.presence` and ``_deferred_send``).
Targets without the flag (the legacy default) always send.

Design doc: docs/plans/2026-06-11-external-notifications-apprise-design.md
"""

import asyncio
import logging
import time
from typing import NamedTuple

from twicc import presence
from twicc.agent.states import AgentInfo, AgentState
from twicc.core.services.public_origin import usable_public_origin
from twicc.providers.helpers import get_provider_helpers
from twicc.synced_settings import read_synced_settings
from twicc.usage import format_extra_usage_amount

logger = logging.getLogger(__name__)

# Last *seen broadcast* per session: (state, pending_requests count).
# Broadcasts fire without a state transition too (e.g. a pending request is
# added or resolved while the state stays ASSISTANT_TURN), so event detection
# must compare against the previous broadcast — the same approach as the
# frontend's ``previousState`` store — rather than ``AgentInfo.previous_state``,
# which only tracks actual state-machine transitions.
_last_seen: dict[str, tuple[AgentState, int]] = {}

_TEST_TITLE = "TwiCC test notification"

# Strong references to in-flight send tasks: asyncio only keeps weak refs to
# tasks, so a fire-and-forget task could be garbage-collected mid-send.
_send_tasks: set[asyncio.Task] = set()


def _truncate(text: str | None, max_length: int, fallback: str = "Unknown") -> str:
    """Mirror the frontend's ``truncateTitle()`` (frontend/src/utils/truncate.js)."""
    if not text:
        return fallback
    return text[:max_length] + "…" if len(text) > max_length else text


def _build_body(
    session_title: str | None,
    project_name: str | None,
    project_parent_name: str | None,
    session_url: str | None,
) -> str:
    """Mirror the frontend's ``buildNotificationBody()``, plus an optional deep link."""
    if project_parent_name:
        project = f"{_truncate(project_parent_name, 40)} › {_truncate(project_name, 40)}"
    else:
        project = _truncate(project_name, 50)
    body = f"Project: {project}\nSession: {_truncate(session_title, 50)}"
    if session_url:
        body += f"\n\n{session_url}"
    return body


def _build_session_url(settings: dict, project_id: str, session_id: str) -> str | None:
    base = usable_public_origin(settings.get("publicBaseUrl"))
    if not base:
        return None
    return f"{base}/project/{project_id}/session/{session_id}"


def notify_agent_event(
    info: AgentInfo,
    session_title: str | None,
    project_name: str | None,
    project_parent_name: str | None,
    mute_on_user_turn: bool = False,
) -> None:
    """Detect notification-worthy events on a process-state broadcast and fire sends.

    Called from ``broadcast_process_state()`` (after its hidden-session
    early-return, so hidden sessions never reach this point). Synchronous and
    cheap on the no-event path; actual sends run in a fire-and-forget task.
    Never raises — a notification failure (including malformed hand-edited
    settings) must not affect the broadcast path.

    The display names are passed down from the broadcast (which already
    resolved them) instead of being re-queried here.
    """
    try:
        _detect_and_send(
            info,
            session_title,
            project_name,
            project_parent_name,
            mute_on_user_turn,
        )
    except Exception:
        logger.exception("External notification dispatch failed for session %s", info.session_id)


def _detect_and_send(
    info: AgentInfo,
    session_title: str | None,
    project_name: str | None,
    project_parent_name: str | None,
    mute_on_user_turn: bool,
) -> None:
    previous = _last_seen.get(info.session_id)
    pending_count = len(info.pending_requests)
    # Keep the baseline current even when no target is configured, so enabling
    # targets later starts from fresh state instead of replaying old events.
    if info.state == AgentState.DEAD:
        _last_seen.pop(info.session_id, None)
    else:
        _last_seen[info.session_id] = (info.state, pending_count)

    # Sync call on the async broadcast path: fine in practice — the settings
    # cache is warmed long before any agent broadcast (bootstrap / WS connect
    # both read it), so this never does file I/O here.
    settings = read_synced_settings()
    # Only enabled targets whose last test succeeded receive real
    # notifications — an untested or failing URL is never sent to.
    targets = [
        target
        for target in settings.get("externalNotificationTargets") or []
        if isinstance(target, dict) and target.get("enabled") and target.get("url") and target.get("tested") is True
    ]
    if not targets:
        return

    label = get_provider_helpers(info.provider).LABEL or str(info.provider)
    # (title, per-target opt-in key) — each target chooses its events via the
    # ``notifyUserTurn`` / ``notifyPendingRequest`` flags (absent = opted in).
    events: list[tuple[str, str]] = []

    # --- Transition to USER_TURN: "<Provider> finished working" ---
    if (
        not mute_on_user_turn
        and info.state == AgentState.USER_TURN
        and (previous is None or previous[0] != AgentState.USER_TURN)
    ):
        events.append((f"{label} finished working", "notifyUserTurn"))

    # --- Pending request count grew: "<Provider> needs your attention" ---
    previous_pending_count = previous[1] if previous else 0
    if pending_count > previous_pending_count:
        latest = info.pending_requests[-1]
        if latest.request_type == "ask_user_question":
            events.append((f"{label} has a question for you", "notifyPendingRequest"))
        else:
            events.append((f"{label} needs your approval", "notifyPendingRequest"))

    if not events:
        return

    body = _build_body(
        session_title,
        project_name,
        project_parent_name,
        _build_session_url(settings, info.project_id, info.session_id),
    )
    # Presence snapshot for this broadcast: whether a human is at any TwiCC
    # client right now, and the activity timestamp to compare against when a
    # deferred send later checks whether the user came back.
    present = presence.is_user_present()
    baseline = presence.latest_activity()
    for title, opt_in_key in events:
        eligible = [target for target in targets if target.get(opt_in_key, True)]
        if not eligible:
            continue
        # ``awayOnly`` (absent / false = always send — the default for targets
        # configured before this flag existed; true = only when the user is
        # away) splits the eligible targets into two delivery paths.
        always_urls = [t["url"] for t in eligible if not t.get("awayOnly")]
        away_urls = [t["url"] for t in eligible if t.get("awayOnly")]
        if always_urls:
            _spawn(_send(always_urls, title, body))
        if away_urls:
            if present:
                # Defer: hold while the user looks present; the deferred task
                # cancels itself if they show fresh activity before the grace
                # elapses (they will have seen the event in-app).
                _spawn(_deferred_send(away_urls, title, body, baseline))
            else:
                _spawn(_send(away_urls, title, body))


def notify_extra_usage_started(provider, snapshot, settings: dict) -> None:
    """Push an "extra usage started" notification to opted-in external targets.

    Called from ``twicc.usage_task`` on the rising edge of extra-usage
    consumption, and only when the master ``notifyOnExtraUsageStart`` switch is
    on (the caller already gated on it and passes the resolved ``settings``).
    Mirrors the process-state push: enabled + tested targets that opted into
    ``notifyExtraUsageStart``, split by ``awayOnly`` with the same
    presence-aware deferral. Never raises — a notification failure must not
    affect the usage broadcast path.
    """
    try:
        _send_extra_usage_started(provider, snapshot, settings)
    except Exception:
        logger.exception("Extra-usage external notification dispatch failed for provider %s", provider)


def _build_extra_usage_body(snapshot, label: str, base_url: str | None) -> str:
    """Body for the extra-usage push: a sentence, the credit figure, the app URL.

    Same lead sentence used by the in-app toast and the browser notification.
    The credit figure has two display modes, like the sidebar ring:
    Anthropic-style providers report used/limit credits (``utilization`` set),
    Codex-style providers report only a remaining balance. When the snapshot
    carries a currency, the used/limit figures are money rather than credits.
    Unlike the process-state push there is no session to deep-link to, so when
    a public base URL is configured we append it raw (nothing after it).
    """
    body = f"{label} is currently drawing from your extra usage credit, billed on top of your plan."
    if snapshot.extra_usage_utilization is not None:
        decimals = snapshot.extra_usage_decimal_places
        currency = snapshot.extra_usage_currency
        used_amount = format_extra_usage_amount(snapshot.extra_usage_used_credits or 0, decimals)
        limit_amount = format_extra_usage_amount(snapshot.extra_usage_monthly_limit, decimals)
        if currency and used_amount is not None:
            unit = currency
            used, limit = used_amount, limit_amount
        else:
            unit = "credits"
            used = snapshot.extra_usage_used_credits or 0
            limit = snapshot.extra_usage_monthly_limit
        if limit is not None:
            body += f"\n{used} of {limit} {unit} used."
        else:
            body += f"\n{used} {unit} used."
    elif snapshot.extra_usage_remaining_credits is not None:
        body += f"\n{round(snapshot.extra_usage_remaining_credits)} credits remaining."
    if base_url:
        body += f"\n\n{base_url}"
    return body


def _send_extra_usage_started(provider, snapshot, settings: dict) -> None:
    targets = [
        target
        for target in settings.get("externalNotificationTargets") or []
        if isinstance(target, dict) and target.get("enabled") and target.get("url") and target.get("tested") is True
    ]
    # ``notifyExtraUsageStart`` absent = opted in (consistent with the other
    # per-target event flags; the master switch is the real off-ramp).
    eligible = [target for target in targets if target.get("notifyExtraUsageStart", True)]
    if not eligible:
        return

    label = get_provider_helpers(provider).LABEL or str(provider)
    title = f"{label} — Extra usage started"
    # No session to deep-link to: append the bare public base URL when set,
    # matching the same publicBaseUrl handling as the process-state push.
    base_url = usable_public_origin(settings.get("publicBaseUrl")) or None
    body = _build_extra_usage_body(snapshot, label, base_url)

    present = presence.is_user_present()
    baseline = presence.latest_activity()
    always_urls = [t["url"] for t in eligible if not t.get("awayOnly")]
    away_urls = [t["url"] for t in eligible if t.get("awayOnly")]
    if always_urls:
        _spawn(_send(always_urls, title, body))
    if away_urls:
        if present:
            _spawn(_deferred_send(away_urls, title, body, baseline))
        else:
            _spawn(_send(away_urls, title, body))


class PeerRouting(NamedTuple):
    """Where a peer message counts, for the push body: the session it is
    about (its own, or the one its thread names) and the project, named like
    the process-state push (worktree under its main repository). Any part
    may be None; ``session_id``/``project_id`` feed the deep link."""
    session_id: str | None
    session_title: str | None
    project_id: str | None
    project_name: str | None
    project_parent_name: str | None


def peer_message_routing(serialized: dict) -> PeerRouting | None:
    """Resolve the push routing of a serialized peer message (sync: one
    Project query). Same reading as the inbox row: the message's own local
    session, else ``effective_session`` (the thread's), else the bare
    ``effective_project``. None when nothing names a project."""
    from twicc.asgi import _get_project_display_name
    from twicc.core.models import Project

    local = (
        serialized.get("delivered_to_session")
        if serialized.get("direction") == "in"
        else serialized.get("origin_session")
    )
    session = local or serialized.get("effective_session") or None
    project_id = (session or {}).get("project_id") or (serialized.get("effective_project") or {}).get("id")
    if not session and not project_id:
        return None
    project = Project.objects.select_related("worktree_of").filter(id=project_id).first() if project_id else None
    return PeerRouting(
        session_id=(session or {}).get("id"),
        session_title=(session or {}).get("title"),
        project_id=project_id,
        project_name=_get_project_display_name(project) if project else None,
        project_parent_name=(
            _get_project_display_name(project.worktree_of) if project and project.worktree_of else None
        ),
    )


def notify_peer_message(message, routing: PeerRouting | None = None) -> None:
    """Push an incoming peer message to opted-in external targets.

    Called from ``broadcast_peer_message_received``, so it fires exactly when
    the in-app toast does — and only for a genuinely new message (a replayed
    ``message_id`` returns before the broadcast). Never raises.

    ``routing`` (see ``peer_message_routing``) appends where the message
    counts — ``Project:`` / ``Session:`` lines like the process-state push —
    and, when a session is known, deep-links to it instead of the bare app
    URL: on a phone, that lands on the conversation the message is about.
    """
    try:
        title = f"{'Reply' if message.reply_to_message_id else 'Message'} from {_peer_label(message.peer)}"
        preview = _truncate((message.payload or {}).get("text"), 120, fallback="")
        parts = [f'"{message.title}"', preview]
        if routing is not None:
            if routing.project_name:
                project = (
                    f"{_truncate(routing.project_parent_name, 40)} › {_truncate(routing.project_name, 40)}"
                    if routing.project_parent_name
                    else _truncate(routing.project_name, 50)
                )
                parts.append(f"Project: {project}")
            if routing.session_id:
                parts.append(f"Session: {_truncate(routing.session_title, 40)}")
        body = "\n".join(part for part in parts if part)
        session_ref = (
            (routing.project_id, routing.session_id)
            if routing is not None and routing.session_id and routing.project_id
            else None
        )
        _send_peer_event(title, body, session_ref=session_ref)
    except Exception:
        logger.exception("Peer-message external notification dispatch failed")


def notify_peer_request(peer) -> None:
    """Push an incoming pairing request to opted-in external targets.

    The same event as an incoming message, by decision: both mean another
    human waits on you, and a pairing cannot advance until you read your
    verification code to them. Never raises.
    """
    try:
        claimed = peer.name or peer.remote_display_name or "An instance"
        _send_peer_event(
            f"Peer request from {claimed}",
            f"{claimed} wants to pair with your instance.\nAddress: {peer.base_url}",
        )
    except Exception:
        logger.exception("Peer-request external notification dispatch failed")


def _peer_label(peer) -> str:
    """Name the peer the way the owner does — their local name first."""
    return peer.name or peer.remote_display_name or peer.base_url


def _send_peer_event(title: str, body: str, *, session_ref: tuple[str, str] | None = None) -> None:
    """Deliver one peer event, mirroring the process-state push.

    Enabled + tested targets that opted into ``notifyPeer`` (absent = opted
    in, like every other event flag), split by ``awayOnly`` with the same
    presence-aware deferral. The inbox is a dialog, not a route, so the body
    ends with the session the event is about when one is known
    (``session_ref`` = ``(project_id, session_id)``), else the bare app URL —
    when a public URL is configured at all.
    """
    settings = read_synced_settings()
    targets = [
        target
        for target in settings.get("externalNotificationTargets") or []
        if isinstance(target, dict) and target.get("enabled") and target.get("url") and target.get("tested") is True
    ]
    eligible = [target for target in targets if target.get("notifyPeer", True)]
    if not eligible:
        return

    link = _build_session_url(settings, *session_ref) if session_ref else None
    link = link or usable_public_origin(settings.get("publicBaseUrl")) or None
    if link:
        body = f"{body}\n\n{link}"

    present = presence.is_user_present()
    baseline = presence.latest_activity()
    always_urls = [t["url"] for t in eligible if not t.get("awayOnly")]
    away_urls = [t["url"] for t in eligible if t.get("awayOnly")]
    if always_urls:
        _spawn(_send(always_urls, title, body))
    if away_urls:
        if present:
            _spawn(_deferred_send(away_urls, title, body, baseline))
        else:
            _spawn(_send(away_urls, title, body))


def _spawn(coro) -> None:
    """Fire-and-forget a send coroutine, holding a strong task ref until it completes."""
    task = asyncio.create_task(coro)
    _send_tasks.add(task)
    task.add_done_callback(_send_tasks.discard)


async def _deferred_send(urls: list[str], title: str, body: str, baseline: float) -> None:
    """Hold an away-only notification while the user looks present, then send.

    The user was present when the event fired. We wait until their presence
    would expire (``present_until``); if any fresh activity arrives in the
    meantime (``latest_activity`` advances past the ``baseline`` captured at
    event time), the user came back and will have seen the event in-app, so the
    notification is cancelled. Otherwise the user never returned and it is sent
    once the grace window elapses. Never raises.
    """
    try:
        deadline = presence.present_until()
        if deadline is not None:
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))
            if presence.latest_activity() > baseline:
                return  # user came back before the grace elapsed → cancel
    except Exception:
        logger.exception("Deferred external notification failed (title=%r)", title)
        return
    await _send(urls, title, body)


async def _send(urls: list[str], title: str, body: str) -> None:
    """Send one notification to every URL; failures are logged, never raised."""
    try:
        import apprise  # Lazy: keep the (large) plugin registry off the startup path.

        apobj = apprise.Apprise()
        for url in urls:
            if not apobj.add(url):
                logger.warning("External notification: invalid Apprise URL skipped")
        if not len(apobj):
            return
        ok = await apobj.async_notify(title=title, body=body)
        if not ok:
            logger.warning("External notification failed for at least one target (title=%r)", title)
    except Exception:
        logger.exception("External notification send failed (title=%r)", title)


async def test_notification_urls(urls: list[str]) -> list[dict]:
    """Send a test notification to each URL individually and report per-URL results.

    Backs the ``POST /api/external-notifications/test/`` endpoint. One Apprise
    object per URL so each line gets its own verdict. ``url_masked`` is
    Apprise's privacy-masked rendering, so secrets don't round-trip in clear.
    """
    import apprise

    # Shape the test like a real TwiCC notification (same body format,
    # including the deep link when a public base URL is configured).
    settings = read_synced_settings()
    base = usable_public_origin(settings.get("publicBaseUrl"))
    body = _build_body(
        f"Test sent at {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "TwiCC",
        None,
        base or None,
    )
    results: list[dict] = []
    for url in urls:
        apobj = apprise.Apprise()
        if not apobj.add(url):
            results.append({"url_masked": None, "ok": False, "error": "Invalid Apprise URL"})
            continue
        masked = next(iter(apobj)).url(privacy=True)
        try:
            ok = await apobj.async_notify(title=_TEST_TITLE, body=body)
            error = None if ok else "The service rejected the notification"
        except Exception as e:
            ok, error = False, str(e)
        results.append({"url_masked": masked, "ok": bool(ok), "error": error})
    return results
