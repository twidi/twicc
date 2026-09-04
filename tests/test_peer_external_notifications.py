"""The single peer push: an incoming message, or an incoming pairing request.

Both are one event on purpose (design discussion of 2026-09-01): they say the
same thing — another human waits on you — and every other peer event can wait
for the user's next visit. These tests pin the content and the target gating;
the delivery machinery (``_send`` / presence deferral) is shared with the
process-state push and covered there.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from twicc import external_notifications


def _target(**kw):
    target = {"id": "t1", "url": "json://example.com", "enabled": True, "tested": True}
    target.update(kw)
    return target


def _settings(*targets, public_base_url="https://twicc.example.com"):
    return {
        "externalNotificationTargets": list(targets),
        "publicBaseUrl": public_base_url,
    }


def _peer(**kw):
    defaults = {
        "name": "Alice",
        "remote_display_name": "alice-instance",
        "base_url": "https://alice.example.com",
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _message(**kw):
    defaults = {
        "peer": _peer(),
        "title": "Deploy the staging box",
        "payload": {"text": "Can you run the migration before the release?"},
        "reply_to_message_id": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _capture(settings):
    """Run a notification with the sends stubbed out, and return the mock."""
    send = Mock()
    return send, patch.multiple(
        external_notifications,
        read_synced_settings=Mock(return_value=settings),
        _send=send,
        _spawn=Mock(),
    )


def test_incoming_message_names_the_peer_and_quotes_the_subject():
    send, patched = _capture(_settings(_target()))
    with patched:
        external_notifications.notify_peer_message(_message())

    urls, title, body = send.call_args.args
    assert urls == ["json://example.com"]
    assert title == "Message from Alice"
    assert body == (
        '"Deploy the staging box"\n'
        "Can you run the migration before the release?\n\n"
        "https://twicc.example.com"
    )


def test_a_reply_says_so_in_the_title():
    send, patched = _capture(_settings(_target()))
    with patched:
        external_notifications.notify_peer_message(_message(reply_to_message_id=42))

    assert send.call_args.args[1] == "Reply from Alice"


def test_the_local_name_wins_over_the_name_the_peer_claims():
    send, patched = _capture(_settings(_target()))
    with patched:
        external_notifications.notify_peer_message(_message(peer=_peer(name="")))

    assert send.call_args.args[1] == "Message from alice-instance"


def test_a_long_message_is_truncated_to_a_preview():
    send, patched = _capture(_settings(_target()))
    with patched:
        external_notifications.notify_peer_message(
            _message(payload={"text": "x" * 400}),
        )

    body = send.call_args.args[2]
    assert "x" * 120 + "…" in body
    assert "x" * 121 not in body


def test_a_message_without_text_keeps_the_subject_alone():
    send, patched = _capture(_settings(_target(), public_base_url=""))
    with patched:
        external_notifications.notify_peer_message(_message(payload={}))

    assert send.call_args.args[2] == '"Deploy the staging box"'


def test_a_pairing_request_carries_the_address():
    send, patched = _capture(_settings(_target()))
    with patched:
        external_notifications.notify_peer_request(_peer(name=""))

    urls, title, body = send.call_args.args
    assert title == "Peer request from alice-instance"
    assert body == (
        "alice-instance wants to pair with your instance.\n"
        "Address: https://alice.example.com\n\n"
        "https://twicc.example.com"
    )


def test_a_target_opted_out_of_peer_events_gets_nothing():
    send, patched = _capture(_settings(_target(notifyPeer=False)))
    with patched:
        external_notifications.notify_peer_message(_message())
        external_notifications.notify_peer_request(_peer())

    send.assert_not_called()


def test_an_absent_flag_means_opted_in():
    """Consistent with every other event flag — targets predate this one."""
    send, patched = _capture(_settings(_target(notifyUserTurn=False)))
    with patched:
        external_notifications.notify_peer_message(_message())

    send.assert_called_once()


def test_untested_and_disabled_targets_are_skipped():
    send, patched = _capture(_settings(
        _target(id="t1", tested=None),
        _target(id="t2", tested=False),
        _target(id="t3", enabled=False),
        _target(id="t4", url=""),
    ))
    with patched:
        external_notifications.notify_peer_message(_message())

    send.assert_not_called()


def test_a_broken_notification_never_reaches_the_caller():
    """The push rides the receive path: a failure must not break delivery."""
    with patch.object(
        external_notifications, "read_synced_settings", side_effect=RuntimeError("boom"),
    ):
        external_notifications.notify_peer_message(_message())
        external_notifications.notify_peer_request(_peer())


def test_routing_adds_project_and_session_lines_and_deep_links_to_the_session():
    send, patched = _capture(_settings(_target()))
    routing = external_notifications.PeerRouting(
        session_id="sess-1",
        session_title="Backend update, the long version that keeps going and going",
        project_id="-repo-wt",
        project_name="feature-x",
        project_parent_name="repo",
    )
    with patched:
        external_notifications.notify_peer_message(_message(), routing)

    _, title, body = send.call_args.args
    assert title == "Message from Alice"
    assert body == (
        '"Deploy the staging box"\n'
        "Can you run the migration before the release?\n"
        "Project: repo › feature-x\n"
        "Session: Backend update, the long version that ke…\n\n"
        "https://twicc.example.com/project/-repo-wt/session/sess-1"
    )


def test_routing_with_a_bare_project_keeps_the_app_link():
    send, patched = _capture(_settings(_target()))
    routing = external_notifications.PeerRouting(
        session_id=None, session_title=None, project_id="-repo", project_name="repo", project_parent_name=None,
    )
    with patched:
        external_notifications.notify_peer_message(_message(), routing)

    _, _, body = send.call_args.args
    assert body == (
        '"Deploy the staging box"\n'
        "Can you run the migration before the release?\n"
        "Project: repo\n\n"
        "https://twicc.example.com"
    )


@pytest.mark.django_db(transaction=True)
def test_peer_message_routing_reads_the_thread_session_and_names_the_worktree_under_its_repo():
    from twicc.core.models import Project

    main = Project.objects.create(id="-pr-main", directory="/tmp/pr-main", name="Main repo")
    Project.objects.create(id="-pr-wt", directory="/tmp/pr-main-wt", worktree_of=main)

    inherited = external_notifications.peer_message_routing({
        "direction": "in",
        "delivered_to_session": None,
        "effective_session": {"id": "sess-wt", "title": "Backend update", "project_id": "-pr-wt"},
        "effective_project": {"id": "-pr-wt", "source": "conversation"},
    })
    assert inherited == external_notifications.PeerRouting(
        "sess-wt", "Backend update", "-pr-wt", "pr-main-wt", "Main repo",
    )

    own = external_notifications.peer_message_routing({
        "direction": "out",
        "origin_session": {"id": "sess-main", "title": "Own", "project_id": "-pr-main"},
        "effective_session": None,
        "effective_project": {"id": "-pr-main", "source": "session"},
    })
    assert own == external_notifications.PeerRouting("sess-main", "Own", "-pr-main", "Main repo", None)

    bare = external_notifications.peer_message_routing({
        "direction": "in", "delivered_to_session": None, "effective_session": None,
        "effective_project": {"id": "-pr-main", "source": "attached"},
    })
    assert bare == external_notifications.PeerRouting(None, None, "-pr-main", "Main repo", None)

    assert external_notifications.peer_message_routing({
        "direction": "in", "delivered_to_session": None, "effective_session": None, "effective_project": None,
    }) is None
