"""``send-messages --wait-reply`` — the wiring between the batch and the wait.

The wait loop has its own tests, and the batch runner has its own. What had
none is the hook that joins them: which recipients get waited on, with which
cursor, and whether the flags reach the loop at all. Six mutants survived the
whole suite until this file existed — the same shape found three times over in
this work, where the mechanism was covered and its caller was not.
"""

from __future__ import annotations

import orjson
import pytest

from twicc.cli import send_messages
from twicc.core.models import Project, Session


@pytest.fixture
def two_sessions(db, tmp_path):
    project = Project.objects.create(id="smw-project", directory=str(tmp_path))
    for sid in ("alpha", "beta"):
        Session.objects.create(
            id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
            created_at="2026-09-18T10:00:00Z", user_message_count=1,
        )
    return project


@pytest.fixture
def batch(monkeypatch, two_sessions):
    """A transport that answers every drop instantly, and a recorded wait.

    Everything between the two — `run_batch`'s submit/poll loop, the hook, the
    summary — stays real: that is what is under test.
    """
    from twicc.cli._drop_request import transport

    statuses: dict = {}

    class _Sub:
        def __init__(self, payload):
            self.sid = payload["session_id"]
            self.request_uuid = f"uuid-{self.sid}"

        def was_received(self):
            return True

        def poll(self):
            return statuses[self.sid]

        def cleanup(self):
            pass

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", lambda payload, *, kind: _Sub(payload))

    seen: dict = {}

    def fake_wait(cursors, *, timeout, want_text, stop_when_blocked, first):
        seen.update(cursors=dict(cursors), timeout=timeout, want_text=want_text,
                    blocked=stop_when_blocked, first=first)
        return {
            sid: {"outcome": "replied", "line_num": 9, "text": "ok"}
            for sid in cursors
        }

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", fake_wait)
    return {"statuses": statuses, "seen": seen}


def sent(session_id, last_line):
    from twicc.cli._drop_request.polling import PollOutcome

    return PollOutcome("sent", {
        "session_id": session_id, "provider": "claude_code",
        "project_id": "smw-project", "last_line": last_line,
    }, True)


def run(capsysbinary, **kwargs):
    kwargs.setdefault("timeout", 5)
    kwargs.setdefault("wait_reply", True)
    kwargs.setdefault("no_expand", False)
    kwargs.setdefault("attach", [])
    kwargs.setdefault("spawned_by", None)
    kwargs.setdefault("descendants", None)
    kwargs.setdefault("siblings", None)
    kwargs.setdefault("annotation", None)
    kwargs.setdefault("wait_timeout", None)
    kwargs.setdefault("no_reply_text", False)
    kwargs.setdefault("wait_blocked", False)
    kwargs.setdefault("wait_first", False)
    import typer

    try:
        send_messages.send_messages_cmd(["alpha", "beta"], message="go", **kwargs)
    except typer.Exit as exit_:
        # Exit 0 / 6 are the batch's own endings; anything else is a refusal
        # the caller wants to see.
        if exit_.exit_code not in (0, 6):
            raise
    return orjson.loads(capsysbinary.readouterr().out)


def test_each_recipient_is_waited_on_from_its_own_cursor(batch, capsysbinary):
    """The cursor is what keeps the previous turn's closing message from
    answering for this one — per recipient, since each has its own."""
    batch["statuses"].update(alpha=sent("alpha", 11), beta=sent("beta", 23))

    run(capsysbinary)

    assert batch["seen"]["cursors"] == {"alpha": 11, "beta": 23}


def test_a_recipient_that_was_not_sent_to_is_not_waited_on(batch, capsysbinary):
    """A rejected send has no turn to answer it. Waiting on one would burn the
    shared budget for a reply that cannot come."""
    from twicc.cli._drop_request.polling import PollOutcome

    batch["statuses"].update(
        alpha=sent("alpha", 11),
        beta=PollOutcome("rejected", {"errors": [{"code": "x", "message": "no"}]}, True),
    )

    run(capsysbinary)

    assert list(batch["seen"]["cursors"]) == ["alpha"]


def test_the_replies_are_attached_per_entry(batch, capsysbinary):
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    payload = run(capsysbinary)

    assert payload["results"]["alpha"]["reply"]["text"] == "ok"
    assert payload["summary"]["replied"] == 2
    assert payload["summary"]["all_replied"] is True


def test_nothing_is_waited_on_without_the_flag(batch, capsysbinary):
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    payload = run(capsysbinary, wait_reply=False)

    assert "cursors" not in batch["seen"]
    assert "reply" not in payload["results"]["alpha"]
    assert "replied" not in payload["summary"]


@pytest.mark.parametrize("flag, key", [("wait_first", "first"), ("wait_blocked", "blocked")])
def test_the_wait_flags_reach_the_loop(batch, capsysbinary, flag, key):
    """A flag that stops at the command is a flag that does nothing, and the
    loop's own tests cannot see it."""
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    run(capsysbinary, **{flag: True})

    assert batch["seen"][key] is True


def test_the_shared_deadline_reaches_the_loop(batch, capsysbinary):
    """One budget for the batch, not N times it."""
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    run(capsysbinary, wait_timeout=12.0)

    assert batch["seen"]["timeout"] == 12.0


def test_no_reply_text_reaches_the_loop(batch, capsysbinary):
    """Recording an argument is not asserting it: `want_text` was captured and
    never checked, so the flag could be ignored with the suite green."""
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    run(capsysbinary, no_reply_text=True)

    assert batch["seen"]["want_text"] is False


def test_the_text_is_kept_by_default(batch, capsysbinary):
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    run(capsysbinary)

    assert batch["seen"]["want_text"] is True


@pytest.mark.parametrize("kwargs, message", [
    ({"wait_timeout": 5.0}, "--wait-timeout requires --wait-reply."),
    ({"no_reply_text": True}, "--no-reply-text requires --wait-reply."),
    ({"wait_blocked": True}, "--wait-blocked requires --wait-reply."),
    ({"wait_first": True}, "--wait-first requires --wait-reply."),
])
def test_the_modifiers_are_refused_without_the_wait(batch, capsysbinary, kwargs, message):
    """Four flags that only mean something under `--wait-reply`, and a whole
    validation block that no test exercised."""
    import typer

    with pytest.raises(typer.Exit) as exc:
        run(capsysbinary, wait_reply=False, **kwargs)

    assert exc.value.exit_code == 1
    payload = orjson.loads(capsysbinary.readouterr().out)
    assert payload["status"] == "validation_error"
    assert message in [e["message"] for e in payload["errors"]]


def test_a_non_positive_wait_budget_is_refused(batch, capsysbinary):
    import typer

    with pytest.raises(typer.Exit) as exc:
        run(capsysbinary, wait_timeout=0)

    assert exc.value.exit_code == 1


def test_the_counters_describe_the_answers_not_the_sends(batch, capsysbinary, monkeypatch):
    """A mixed batch is what separates them: with every entry replying, a
    counter that counts sends and one that counts answers give the same
    number, and both mutants live."""
    def mixed(cursors, **kwargs):
        outcomes = {"alpha": "replied", "beta": "timeout"}
        return {sid: {"outcome": outcomes[sid], "line_num": None} for sid in cursors}

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", mixed)
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    payload = run(capsysbinary)

    assert payload["summary"]["replied"] == 1
    assert payload["summary"]["all_replied"] is False


def test_the_batch_result_survives_a_broken_wait(batch, capsysbinary, monkeypatch):
    """The sends already succeeded. A locked database or a Ctrl-C mid-wait
    must not swallow which recipients got the message — the singular commands
    degrade the same way, and a batch has more to lose."""
    def explode(cursors, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", explode)
    batch["statuses"].update(alpha=sent("alpha", 7), beta=sent("beta", 9))

    payload = run(capsysbinary)

    assert payload["summary"]["succeeded"] == 2
    assert payload["results"]["alpha"]["last_line"] == 7
    assert payload["results"]["alpha"]["reply"]["outcome"] == "wait_failed"
    assert "database is locked" in payload["results"]["alpha"]["reply"]["error"]
    assert payload["results"]["beta"]["reply"]["since_line_num"] == 9


def test_a_degraded_batch_entry_keeps_the_singular_shape(batch, capsysbinary, monkeypatch):
    """The batch goes through ``degraded_reply`` so it cannot drift from the
    singular — but only a call that exercises the batch's own site proves it.

    An exception with an empty ``str()`` is the one that separates them: a
    re-inlined copy without the guard prints a trailing colon, and the
    previous test's ``RuntimeError`` has a message, so it could not see it.
    """
    def interrupted(cursors, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", interrupted)
    batch["statuses"].update(alpha=sent("alpha", 1), beta=sent("beta", 1))

    payload = run(capsysbinary)

    assert payload["results"]["alpha"]["reply"]["error"] == "KeyboardInterrupt"


def test_an_empty_wait_is_not_a_success(batch, capsysbinary, monkeypatch):
    """`all_replied` on a batch where nothing was waited on must be false.

    Reachable when every send was rejected: the summary would otherwise say
    every recipient answered, on a batch where none was even asked.
    """
    from twicc.cli._drop_request.polling import PollOutcome

    rejected = PollOutcome("rejected", {"errors": [{"code": "x", "message": "no"}]}, True)
    batch["statuses"].update(alpha=rejected, beta=rejected)

    payload = run(capsysbinary)

    assert payload["summary"]["replied"] == 0
    assert payload["summary"]["all_replied"] is False


@pytest.mark.parametrize("status", ["timeout", "failed"])
def test_only_a_sent_recipient_is_waited_on(batch, capsysbinary, status):
    """`rejected` was the only status covered, and it is the easy one.

    A `timeout` send is the awkward case the docstring exists to explain: the
    message may well have been delivered, but no `last_line` came back, so a
    wait could only start from 0 and hand back the previous turn's answer.
    """
    from twicc.cli._drop_request.polling import PollOutcome

    batch["statuses"].update(
        alpha=sent("alpha", 11),
        beta=PollOutcome(status, {"error": "nope"}, True),
    )

    run(capsysbinary)

    assert list(batch["seen"]["cursors"]) == ["alpha"]
