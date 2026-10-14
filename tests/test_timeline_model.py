import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from twicc.core.models import Project, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision
from twicc.core.timeline import PAYLOAD_TYPES, STARTED_OF, inherit_started_fields, validate_data


def test_every_kind_has_a_payload_entry():
    assert set(PAYLOAD_TYPES) == set(TimelineKind.values)


def test_started_of_pairs_each_ended_kind_with_a_started_kind_that_it_repeats():
    assert set(STARTED_OF) == {
        "awaiting_input.ended", "process.ended", "pinned.ended", "backend.ended",
    }
    for ended, started in STARTED_OF.items():
        assert started == ended.replace(".ended", ".started")
        # The ended payload has every field of the started payload, plus its own.
        assert set(PAYLOAD_TYPES[started]._fields) <= set(PAYLOAD_TYPES[ended]._fields)


def test_inherit_started_fields_fills_only_what_the_caller_left_out():
    started = {"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True, "via": "cli"}
    merged = inherit_started_fields("process.ended", {"error": "boom", "provider": "claude_code"}, started)
    assert merged == {"error": "boom", "provider": "claude_code", "cause": "resume", "twicc_pid": 7, "hybrid": True}
    assert inherit_started_fields("process.ended", {"error": "boom"}, None) == {"error": "boom"}
    assert inherit_started_fields("pinned.ended", None, {"scope": "all"}) == {"scope": "all"}


def test_kind_names_follow_subject_verb():
    for value in TimelineKind.values:
        subject, _, verb = value.partition(".")
        assert subject and verb and "." not in verb, value


def test_validate_data_fills_defaults_and_keeps_known_keys():
    assert validate_data("session.created", {"origin": "sync"}) == {
        "origin": "sync", "provider": None, "settings": None, "spawned_by": None,
    }
    assert validate_data("title.changed", {"source": "user", "new": "B"}) == {"source": "user", "new": "B", "old": None}
    assert validate_data("process.ended", None) == {
        "provider": None, "cause": None, "twicc_pid": None, "hybrid": None, "error": None, "upper_bound": None,
    }
    assert validate_data("turn.ended", None) == {"background_work": None}


def test_validate_data_rejects_unknown_and_missing_keys():
    with pytest.raises(ValueError):
        validate_data("session.created", {"origin": "sync", "nope": 1})
    with pytest.raises(ValueError):
        validate_data("session.created", {})


def test_validate_data_rejects_payload_on_payloadless_kind():
    assert validate_data("turn.started", None) == {}
    with pytest.raises(ValueError):
        validate_data("turn.started", {"x": 1})


def test_validate_data_allows_the_reserved_via_key_on_every_kind():
    assert validate_data("turn.started", {"via": "cli"}) == {"via": "cli"}
    with pytest.raises(ValueError):
        validate_data("turn.started", {"via": "carrier-pigeon"})


@pytest.mark.django_db
def test_dedupe_key_is_unique_per_session_only_when_set():
    now = timezone.now()
    base = {"kind": TimelineKind.SESSION_CREATED, "occurred_at": now, "actor": TimelineActor.SYSTEM}
    TimelineEvent.objects.create(session_id="s1", dedupe_key="k", **base)
    TimelineEvent.objects.create(session_id="s2", dedupe_key="k", **base)
    TimelineEvent.objects.create(session_id="s1", **base)
    TimelineEvent.objects.create(session_id="s1", **base)
    with pytest.raises(IntegrityError), transaction.atomic():
        TimelineEvent.objects.create(session_id="s1", dedupe_key="k", **base)


@pytest.mark.django_db
def test_defaults_and_project_fk():
    project = Project.objects.create(id="-tl-model", directory="/tmp/tl-model")
    event = TimelineEvent.objects.create(
        session_id="s1", project=project, kind=TimelineKind.TURN_STARTED,
        occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    event.refresh_from_db()
    assert event.precision == TimelinePrecision.EXACT
    assert event.data == {} and event.reason == "" and event.caused_by_id is None
    assert event.recorded_at is not None
    assert project.timeline_events.count() == 1
