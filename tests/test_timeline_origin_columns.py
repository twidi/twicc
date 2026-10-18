"""The origin columns and their plumbing through every write path (design sections 3, 4, 8)."""

import pytest
from django.utils import timezone

from tests.timeline_helpers import ORIGIN_MAPPING, columns
from twicc.core.models import Project, Session, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services import session_timeline
from twicc.core.services.session_timeline import EventSpec, record_event_sync, record_events_bulk_sync
from twicc.core.services.timeline_backfill import write_creation_rows_sync
from twicc.request_origin import (
    ACTOR_NAME_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, DEFAULT_ORIGIN, SYSTEM_ORIGIN, VIA_MAX_LENGTH, VIA_VALUES,
    RequestOrigin, agent_cli_origin, mcp_session_origin, origin_scope, system_marker,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-cols", directory="/tmp/tl-origin-cols")


# --- The model --------------------------------------------------------------------------------------------


def test_widths_nullability_and_constants_are_pinned():
    meta = TimelineEvent._meta
    for name, width in (("via", VIA_MAX_LENGTH), ("actor_ref", ACTOR_REF_MAX_LENGTH),
                        ("actor_name", ACTOR_NAME_MAX_LENGTH)):
        field = meta.get_field(name)
        assert field.max_length == width and field.null is True and field.db_index is False, name
        assert not any(name in index.fields for index in meta.indexes), name
    assert (VIA_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, ACTOR_NAME_MAX_LENGTH) == (20, 255, 80)
    assert VIA_VALUES == {"ui", "cli", "rpc", "mcp_internal", "mcp_external"}
    assert all(len(value) <= VIA_MAX_LENGTH for value in VIA_VALUES)


def test_actor_values_still_are_the_four_of_the_spec():
    assert set(TimelineActor.values) == {"user", "agent", "system", "external"}


# --- The single-event path --------------------------------------------------------------------------------


@pytest.mark.parametrize(("origin", "expected"), ORIGIN_MAPPING)
def test_every_mapping_row_is_stored_as_given(origin, expected):
    row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", origin=origin)
    row.refresh_from_db()
    assert columns(row) == expected


def test_no_actor_no_origin_and_no_carrier_is_the_default():
    row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == ("user", "ui", None, None)


def test_the_carrier_is_read_when_neither_actor_nor_origin_is_given():
    with origin_scope(agent_cli_origin("sess-A")):
        row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == ("agent", "cli", "sess-A", None)


def test_an_explicit_actor_wins_over_the_carrier_and_leaves_the_origin_fields_null():
    with origin_scope(agent_cli_origin("sess-A")):
        row = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
        system = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.SYSTEM)
    assert columns(row) == ("agent", None, None, None)
    assert columns(system) == ("system", None, None, None)


def test_the_system_marker_gives_system_with_null_fields_never_the_default():
    with system_marker():
        row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == ("system", None, None, None)


def test_a_captured_origin_wins_over_the_carrier_and_over_an_actor_and_keeps_all_four_fields():
    captured = RequestOrigin("external", "mcp_external", "conn-1", "ChatGPT")
    with origin_scope(agent_cli_origin("sess-A")):
        row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor="system", origin=captured)
    assert columns(row) == tuple(captured)


def test_an_invalid_via_is_stored_as_null_and_the_event_is_still_written():
    row = record_event_sync(
        TimelineKind.ARCHIVED_STARTED, session_id="s1", origin=RequestOrigin("user", "carrier-pigeon", "ref", "name"),
    )
    assert row is not None
    assert columns(TimelineEvent.objects.get(pk=row.pk)) == ("user", None, "ref", "name")


@pytest.mark.parametrize("bad_via", [["x"], {"a": 1}], ids=["list", "dict"])
def test_an_unhashable_via_is_stored_as_null_and_the_event_is_still_written(bad_via):
    row = record_event_sync(
        TimelineKind.ARCHIVED_STARTED, session_id="s1", origin=RequestOrigin("user", bad_via, "ref", "name"),
    )
    assert row is not None
    assert columns(TimelineEvent.objects.get(pk=row.pk)) == ("user", None, "ref", "name")


@pytest.mark.parametrize("bad_via", [["x"], {"a": 1}], ids=["list", "dict"])
def test_an_unhashable_via_is_stored_as_null_on_the_batch_path_too(bad_via):
    count = record_events_bulk_sync([
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s1", origin=RequestOrigin("user", bad_via, "ref", "name")),
    ])
    assert count == 1
    assert columns(TimelineEvent.objects.get(session_id="s1")) == ("user", None, "ref", "name")


def test_a_long_ref_and_name_are_truncated_not_rejected():
    row = record_event_sync(
        TimelineKind.ARCHIVED_STARTED, session_id="s1",
        origin=RequestOrigin("external", "mcp_external", "r" * 300, "n" * 100),
    )
    stored = TimelineEvent.objects.get(pk=row.pk)
    assert stored.actor_ref == "r" * 255 and stored.actor_name == "n" * 80


def test_an_unreadable_carrier_falls_back_to_the_default_and_the_event_is_written(monkeypatch):
    def boom():
        raise RuntimeError("unreadable")

    monkeypatch.setattr("twicc.request_origin.capture_origin", boom)
    row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == tuple(DEFAULT_ORIGIN)


def test_an_ended_row_never_copies_the_origin_fields_of_its_started_row():
    record_event_sync(
        TimelineKind.PROCESS_STARTED, session_id="s1", origin=agent_cli_origin("sess-A"), data={"provider": "codex"},
    )
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", origin=SYSTEM_ORIGIN, reason="error")
    assert columns(ended) == ("system", None, None, None)
    assert ended.data["provider"] == "codex"  # the payload is still inherited
    other = record_event_sync(TimelineKind.PROCESS_STARTED, session_id="s2", origin=mcp_session_origin("sess-B"),
                              data={"provider": "codex"})
    user_end = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s2", origin=RequestOrigin("user", "ui"),
                                 reason="manual")
    assert columns(other) != columns(user_end) and columns(user_end) == ("user", "ui", None, None)


# --- The batch path and its row-by-row fallback ------------------------------------------------------------


def test_a_batch_writes_one_origin_on_every_row():
    origin = mcp_session_origin("sess-A")
    count = record_events_bulk_sync(
        [EventSpec(TimelineKind.ARCHIVED_STARTED, session_id=f"s{i}", origin=origin) for i in range(3)]
    )
    assert count == 3
    assert {columns(row) for row in TimelineEvent.objects.all()} == {tuple(origin)}


def test_a_batch_spec_with_only_an_actor_has_null_origin_fields():
    record_events_bulk_sync([EventSpec(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)])
    assert columns(TimelineEvent.objects.get(session_id="s1")) == ("agent", None, None, None)


def test_the_batch_path_truncates_and_nulls_like_the_single_path():
    record_events_bulk_sync([
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="a",
                  origin=RequestOrigin("external", "mcp_external", "r" * 300, "n" * 100)),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="b", origin=RequestOrigin("user", "bad-via", "ref")),
    ])
    first, second = (TimelineEvent.objects.get(session_id=s) for s in ("a", "b"))
    assert columns(first) == ("external", "mcp_external", "r" * 255, "n" * 80)
    assert columns(second) == ("user", None, "ref", None)


def test_the_batch_fallback_carries_the_same_columns_as_the_batch_path(monkeypatch):
    specs = [
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="a",
                  origin=RequestOrigin("external", "mcp_external", "r" * 300, "n" * 100)),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="b", origin=RequestOrigin("user", "bad-via", "ref")),
        EventSpec(TimelineKind.TURN_STARTED, session_id="c", actor=TimelineActor.AGENT),
    ]
    record_events_bulk_sync(specs)
    expected = {s: columns(TimelineEvent.objects.get(session_id=s)) for s in ("a", "b", "c")}
    TimelineEvent.objects.all().delete()

    def boom(rows):
        raise RuntimeError("batch insert failed")

    monkeypatch.setattr(session_timeline, "insert_event_rows", boom)
    assert record_events_bulk_sync(specs) == 3
    assert {s: columns(TimelineEvent.objects.get(session_id=s)) for s in ("a", "b", "c")} == expected


# --- Backfill and old rows ---------------------------------------------------------------------------------


def test_the_backfill_writes_null_origin_fields(project):
    Session.objects.create(
        id="s1", file_path="s1.jsonl", project=project, provider="codex", created_at=timezone.now(), hidden=True,
    )
    write_creation_rows_sync("s1")
    rows = list(TimelineEvent.objects.filter(session_id="s1"))
    assert [row.kind for row in rows] == ["session.created"]
    assert {columns(row) for row in rows} == {("system", None, None, None)}


def test_rows_created_without_the_columns_read_as_null():
    row = TimelineEvent.objects.create(
        session_id="old", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    row.refresh_from_db()
    assert (row.via, row.actor_ref, row.actor_name) == (None, None, None)
