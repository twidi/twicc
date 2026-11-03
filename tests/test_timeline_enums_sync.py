"""Pin the copies of the timeline enum values that cannot be derived from one source."""

from twicc.core.models import TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision
from twicc.core.timeline import PAYLOAD_TYPES, STARTED_OF, validate_data


def _choice_values(field_name):
    return [value for value, _label in TimelineEvent._meta.get_field(field_name).choices]


def test_model_choices_equal_the_enum_members():
    assert _choice_values("kind") == list(TimelineKind.values)
    assert _choice_values("precision") == list(TimelinePrecision.values)
    assert _choice_values("actor") == list(TimelineActor.values)


def test_precision_default_is_a_member():
    assert TimelineEvent._meta.get_field("precision").default in TimelinePrecision.values


def test_registry_and_enum_have_the_same_members():
    assert set(PAYLOAD_TYPES) == set(TimelineKind)
    assert len(PAYLOAD_TYPES) == len(TimelineKind)
    for kind, payload_type in PAYLOAD_TYPES.items():
        if payload_type is None:
            assert validate_data(kind, None) == {}  # a known kind: no "unknown kind" ValueError


def test_started_of_keys_and_values_are_kinds_of_the_expected_shape():
    for ended, started in STARTED_OF.items():
        assert ended in TimelineKind.values and started in TimelineKind.values
        assert ended.endswith(".ended") and started.endswith(".started")


def test_every_interval_end_has_a_matching_start_and_vice_versa():
    values = set(TimelineKind.values)
    for value in values:
        subject, _, verb = value.partition(".")
        if verb in ("started", "ended"):
            other = f"{subject}.{'ended' if verb == 'started' else 'started'}"
            assert other in values, value


def test_enum_values_fit_their_column_widths():
    for field_name, enum in (("kind", TimelineKind), ("precision", TimelinePrecision), ("actor", TimelineActor)):
        max_length = TimelineEvent._meta.get_field(field_name).max_length
        assert all(len(value) <= max_length for value in enum.values), field_name


def test_message_payload_and_optional_turn_references():
    refs = [{'kind': 'codex_item', 'native_id': 'item', 'turn_id': 'turn'}]
    for kind in ('user_message.sent', 'assistant_message.sent'):
        assert validate_data(kind, {'text': 'body', 'source_refs': refs, 'source_line': 4}) == {
            'text': 'body', 'source_refs': refs, 'source_line': 4}
    assert validate_data('turn.started', {'message_refs': refs}) == {'message_refs': refs}
    assert validate_data('turn.ended', {'message_refs': refs}) == {'background_work': None, 'message_refs': refs}
