"""Layer-1 shape contract for agent share payloads (design §7.2).
Pure-module tests — the ORM-dependent gate wiring is tested in
tests/test_share_gate_wiring.py.
"""

from copy import deepcopy

import pytest

from twicc.core.services import share_agent_gate as gate


def _create_session_payload(**over):
    p = {
        "kind": "share:create",
        "caller_session_id": "caller-1",
        "kind_target": "session",
        "session_id": "sess-1",
        "label": "",
        "password": None,
        "expires_at": None,
        "options": {
            "max_display_mode": "normal",
            "include_subagents": True,
            "show_title": True,
            "display_title": "",
        },
    }
    p.update(over)
    return p


def _codes(errors):
    return [(e.field, e.code) for e in errors]


# ── caller typing (§7.1 step 1) ──────────────────────────────


def test_caller_absent_is_human():
    assert gate.caller_type_error({"kind": "share:create"}) is None


@pytest.mark.parametrize("bad", [["x"], {"id": "x"}, True, False, 3, None])
def test_caller_wrong_type_rejected(bad):
    err = gate.caller_type_error({"caller_session_id": bad})
    assert (
        err is not None
        and err.field == "caller_session_id"
        and err.code == "field_forbidden"
    )


# ── create: genuine payloads pass ──────────────────────────────────────────


def test_genuine_session_create_passes():
    assert gate.validate_create(_create_session_payload()) == []


def test_genuine_artifact_create_passes():
    p = {
        "kind": "share:create",
        "caller_session_id": "c",
        "kind_target": "artifact",
        "bookmark_id": 3,
        "label": "",
        "password": None,
        "expires_at": None,
        "options": {"show_title": False, "display_title": "T"},
    }
    assert gate.validate_create(p) == []


def test_absent_optional_keys_pass():
    p = {
        "kind": "share:create",
        "caller_session_id": "c",
        "kind_target": "session",
        "session_id": "s",
    }
    assert gate.validate_create(p) == []


def test_mode_live_and_snapshot_pass_absent_mode_passes():
    for opts in ({}, {"mode": "live"}, {"mode": "snapshot"}):
        assert gate.validate_create(_create_session_payload(options=opts)) == []


# ── create: unknown / server-owned keys, any value ──────────────────────────


def test_one_extra_top_level_key_fails():
    errors = gate.validate_create(_create_session_payload(extra=1))
    assert ("extra", "field_forbidden") in _codes(errors)


def test_legacy_share_kind_alias_rejected_for_agents():
    """§7.2: the alias dies as an UNKNOWN KEY — the error names share_kind
    itself, not only the missing kind_target."""
    p = _create_session_payload()
    del p["kind_target"]
    p["share_kind"] = "session"
    errors = gate.validate_create(p)
    assert ("share_kind", "field_forbidden") in _codes(errors)
    assert ("kind_target", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize(
    "value",
    [True, False, "false", 1, 37, None, "", 0, [1], {}],
)
def test_frozen_at_line_rejected_whatever_value(value):
    errors = gate.validate_create(
        _create_session_payload(options={"frozen_at_line": value})
    )
    assert ("frozen_at_line", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("value", [True, False, "false", 1, None, "", 0, [1], {}])
def test_notify_on_view_rejected_whatever_value(value):
    errors = gate.validate_create(_create_session_payload(notify_on_view=value))
    assert ("notify_on_view", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("key", ["snapshot_at", "show_timestamps"])
@pytest.mark.parametrize("value", [True, False, "false", 1, None, "", 0, [1], {}])
def test_other_server_owned_option_keys_rejected(key, value):
    errors = gate.validate_create(_create_session_payload(options={key: value}))
    assert (key, "field_forbidden") in _codes(errors)


# ── create: wrong JSON types ────────────────────────────────────────────


@pytest.mark.parametrize("bad", [["s"], {"id": "s"}, True, 5])
def test_session_id_wrong_type(bad):
    errors = gate.validate_create(_create_session_payload(session_id=bad))
    assert ("session_id", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("bad", [["3"], "3", True, False, None, {}])
def test_bookmark_id_wrong_type_incl_bool(bad):
    p = {
        "kind": "share:create",
        "caller_session_id": "c",
        "kind_target": "artifact",
        "bookmark_id": bad,
    }
    errors = gate.validate_create(p)
    assert ("bookmark_id", "field_forbidden") in _codes(errors)


def test_non_object_options_rejected():
    errors = gate.validate_create(_create_session_payload(options=["x"]))
    assert ("options", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("key", ["include_subagents", "show_title"])
def test_boolean_options_reject_boolean_looking_strings(key):
    errors = gate.validate_create(_create_session_payload(options={key: "false"}))
    assert (key, "field_forbidden") in _codes(errors)


def test_display_title_non_string_rejected():
    errors = gate.validate_create(_create_session_payload(options={"display_title": 5}))
    assert ("display_title", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("bad", [123, [], True, False, 0, {}])
def test_create_password_non_string_rejected(bad):
    errors = gate.validate_create(_create_session_payload(password=bad))
    assert ("password", "field_forbidden") in _codes(errors)


def test_create_password_none_and_empty_pass():
    assert gate.validate_create(_create_session_payload(password=None)) == []
    assert gate.validate_create(_create_session_payload(password="")) == []


def test_create_expires_empty_and_none_pass():
    assert gate.validate_create(_create_session_payload(expires_at="")) == []
    assert gate.validate_create(_create_session_payload(expires_at=None)) == []


# ── create: kind_target handling ───────────────────────────────────────────


def test_missing_kind_target_rejected():
    p = {"kind": "share:create", "caller_session_id": "c", "session_id": "s"}
    errors = gate.validate_create(p)
    assert ("kind_target", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize(
    ("kind_target", "target_key"),
    [("session", "session_id"), ("artifact", "bookmark_id")],
)
def test_missing_target_id_rejected(kind_target, target_key):
    errors = gate.validate_create(
        {
            "kind": "share:create",
            "caller_session_id": "c",
            "kind_target": kind_target,
        }
    )
    assert (target_key, "field_forbidden") in _codes(errors)


def test_non_string_kind_target_rejected():
    errors = gate.validate_create(_create_session_payload(kind_target=5))
    assert ("kind_target", "field_forbidden") in _codes(errors)


def test_unknown_kind_target_shape_clean_is_left_to_resolution():
    """A shape-clean unknown value reaches the kind/invalid resolver."""
    p = {"kind": "share:create", "caller_session_id": "c", "kind_target": "bogus"}
    assert gate.validate_create(p) == []


def test_unknown_kind_target_still_validates_union_shape():
    p = {
        "kind": "share:create",
        "caller_session_id": "c",
        "kind_target": "bogus",
        "extra": 1,
        "options": {"notify_on_view": True},
    }
    errors = gate.validate_create(p)
    assert ("extra", "field_forbidden") in _codes(errors)
    assert ("notify_on_view", "field_forbidden") in _codes(errors)


# ── update ────────────────────────────────────────────────────────────────────


def _update_payload(**fields):
    return {
        "kind": "share:update",
        "caller_session_id": "c",
        "share_id": "shr_1",
        "fields": fields,
    }


def test_genuine_update_passes():
    assert (
        gate.validate_update(_update_payload(label="x", password="pw", expires_at=None))
        == []
    )


def test_update_fields_options_and_notify_rejected():
    for key in ("options", "notify_on_view"):
        errors = gate.validate_update(_update_payload(**{key: {}}))
        assert (key, "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("bad", [None, False, 0, [], {}])
def test_update_password_non_string_is_layer_one_error(bad):
    errors = gate.validate_update(_update_payload(password=bad))
    assert ("password", "field_forbidden") in _codes(errors)


def test_update_expires_empty_string_rejected_null_passes():
    errors = gate.validate_update(_update_payload(expires_at=""))
    assert ("expires_at", "field_forbidden") in _codes(errors)
    assert gate.validate_update(_update_payload(expires_at=None)) == []


@pytest.mark.parametrize("bad", [["id"], {}, True, 4])
def test_update_share_id_wrong_type(bad):
    p = {
        "kind": "share:update",
        "caller_session_id": "c",
        "share_id": bad,
        "fields": {},
    }
    errors = gate.validate_update(p)
    assert ("share_id", "field_forbidden") in _codes(errors)


def test_absent_update_fields_is_valid_shape():
    assert (
        gate.validate_update(
            {
                "kind": "share:update",
                "caller_session_id": "c",
                "share_id": "shr_1",
            }
        )
        == []
    )


# ── simple ops ─────────────────────────────────────────────────────────────


def test_simple_op_genuine_passes_and_extra_key_fails():
    p = {"kind": "share:revoke", "caller_session_id": "c", "share_id": "shr_1"}
    assert gate.validate_simple(p) == []
    p["surprise"] = 1
    errors = gate.validate_simple(p)
    assert ("surprise", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize(
    ("validator", "payload", "missing", "accepted"),
    [
        (
            gate.validate_create,
            {"caller_session_id": "c", "kind_target": "session", "session_id": "s"},
            "kind",
            "a JSON string",
        ),
        (
            gate.validate_create,
            {"kind": "share:create", "kind_target": "session", "session_id": "s"},
            "caller_session_id",
            "a JSON string",
        ),
        (
            gate.validate_create,
            {"kind": "share:create", "caller_session_id": "c", "session_id": "s"},
            "kind_target",
            'the JSON string "session" or "artifact"',
        ),
        (
            gate.validate_create,
            {
                "kind": "share:create",
                "caller_session_id": "c",
                "kind_target": "session",
            },
            "session_id",
            "a JSON string",
        ),
        (
            gate.validate_create,
            {
                "kind": "share:create",
                "caller_session_id": "c",
                "kind_target": "artifact",
            },
            "bookmark_id",
            "a JSON integer",
        ),
        (
            gate.validate_update,
            {"caller_session_id": "c", "share_id": "shr_1", "fields": {}},
            "kind",
            "a JSON string",
        ),
        (
            gate.validate_update,
            {"kind": "share:update", "share_id": "shr_1", "fields": {}},
            "caller_session_id",
            "a JSON string",
        ),
        (
            gate.validate_update,
            {"kind": "share:update", "caller_session_id": "c", "fields": {}},
            "share_id",
            "a JSON string",
        ),
        (
            gate.validate_simple,
            {"caller_session_id": "c", "share_id": "shr_1"},
            "kind",
            "a JSON string",
        ),
        (
            gate.validate_simple,
            {"kind": "share:revoke", "share_id": "shr_1"},
            "caller_session_id",
            "a JSON string",
        ),
        (
            gate.validate_simple,
            {"kind": "share:revoke", "caller_session_id": "c"},
            "share_id",
            "a JSON string",
        ),
    ],
)
def test_required_envelope_keys(validator, payload, missing, accepted):
    errors = validator(payload)
    assert (missing, "field_forbidden") in _codes(errors)
    err = next(e for e in errors if e.field == missing)
    assert missing in err.message
    assert accepted in err.message


@pytest.mark.parametrize(
    ("field", "bad"),
    [("kind", 3), ("label", False), ("expires_at", []), ("expires_at", {})],
)
def test_create_common_field_types(field, bad):
    assert (field, "field_forbidden") in _codes(
        gate.validate_create(_create_session_payload(**{field: bad}))
    )


@pytest.mark.parametrize("field", ["mode", "max_display_mode"])
def test_session_string_options_reject_non_strings(field):
    errors = gate.validate_create(_create_session_payload(options={field: False}))
    assert (field, "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("field", ["mode", "max_display_mode", "include_subagents"])
def test_artifact_options_reject_session_only_fields(field):
    p = {
        "kind": "share:create",
        "caller_session_id": "c",
        "kind_target": "artifact",
        "bookmark_id": 3,
        "options": {field: "live" if field == "mode" else True},
    }
    assert (field, "field_forbidden") in _codes(gate.validate_create(p))


def test_non_object_update_fields_rejected():
    p = {
        "kind": "share:update",
        "caller_session_id": "c",
        "share_id": "shr_1",
        "fields": [],
    }
    assert ("fields", "field_forbidden") in _codes(gate.validate_update(p))


@pytest.mark.parametrize(
    ("field", "bad"),
    [("label", False), ("expires_at", False), ("expires_at", [])],
)
def test_update_field_types(field, bad):
    assert (field, "field_forbidden") in _codes(
        gate.validate_update(_update_payload(**{field: bad}))
    )


@pytest.mark.parametrize("bad", [["shr_1"], {}, True, 4, None])
def test_simple_share_id_wrong_type(bad):
    p = {"kind": "share:revoke", "caller_session_id": "c", "share_id": bad}
    assert ("share_id", "field_forbidden") in _codes(gate.validate_simple(p))


@pytest.mark.parametrize("op", ["revoke", "unrevoke", "delete", "propagate"])
def test_every_simple_envelope_passes(op):
    p = {"kind": f"share:{op}", "caller_session_id": "c", "share_id": "shr_1"}
    assert gate.validate_simple(p) == []


@pytest.mark.parametrize(
    ("validator", "payload"),
    [
        (gate.validate_create, _create_session_payload(kind="share:bogus")),
        (gate.validate_create, _create_session_payload(kind="share:update")),
        (gate.validate_update, _update_payload() | {"kind": "share:bogus"}),
        (gate.validate_update, _update_payload() | {"kind": "share:create"}),
        (
            gate.validate_simple,
            {"kind": "share:bogus", "caller_session_id": "c", "share_id": "shr_1"},
        ),
        (
            gate.validate_simple,
            {"kind": "share:create", "caller_session_id": "c", "share_id": "shr_1"},
        ),
    ],
)
def test_operation_values_are_exact(validator, payload):
    assert ("kind", "field_forbidden") in _codes(validator(payload))


def _assert_all_wrong_json_classes_rejected(validator, base, path, bad_values):
    for bad in bad_values:
        payload = deepcopy(base)
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = bad
        assert (path[-1], "field_forbidden") in _codes(validator(payload)), (path, bad)


def test_every_allowed_field_rejects_all_wrong_json_classes():
    bad_string = [None, True, 1, [], {}]
    bad_nullable_string = [True, 1, [], {}]
    bad_object = [None, "x", True, 1, []]
    bad_boolean = [None, "false", 0, 1, [], {}]
    bad_integer = [None, "3", True, False, [], {}]

    session_create = _create_session_payload()
    for field in ("kind", "caller_session_id", "kind_target", "session_id", "label"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, session_create, (field,), bad_string
        )
    for field in ("password", "expires_at"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, session_create, (field,), bad_nullable_string
        )
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, session_create, ("options",), bad_object
    )
    for field in ("mode", "max_display_mode", "display_title"):
        with_field = _create_session_payload(
            options=session_create["options"] | {field: "normal"}
        )
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, with_field, ("options", field), bad_string
        )
    for field in ("include_subagents", "show_title"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, session_create, ("options", field), bad_boolean
        )

    artifact_create = {
        "kind": "share:create",
        "caller_session_id": "c",
        "kind_target": "artifact",
        "bookmark_id": 3,
        "label": "",
        "password": None,
        "expires_at": None,
        "options": {"show_title": True, "display_title": "T"},
    }
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, artifact_create, ("bookmark_id",), bad_integer
    )
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, artifact_create, ("options", "show_title"), bad_boolean
    )
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, artifact_create, ("options", "display_title"), bad_string
    )

    update = _update_payload(label="x", password="pw", expires_at=None)
    for field in ("kind", "caller_session_id", "share_id"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_update, update, (field,), bad_string
        )
    _assert_all_wrong_json_classes_rejected(
        gate.validate_update, update, ("fields",), bad_object
    )
    for field in ("label", "password"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_update, update, ("fields", field), bad_string
        )
    _assert_all_wrong_json_classes_rejected(
        gate.validate_update, update, ("fields", "expires_at"), bad_nullable_string
    )

    simple = {"kind": "share:revoke", "caller_session_id": "c", "share_id": "shr_1"}
    for field in ("kind", "caller_session_id", "share_id"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_simple, simple, (field,), bad_string
        )


def test_non_empty_password_and_expiry_strings_pass_shape():
    assert (
        gate.validate_create(
            _create_session_payload(
                password="secret", expires_at="2030-01-01T00:00:00+00:00"
            ),
        )
        == []
    )
    assert (
        gate.validate_update(
            _update_payload(password="secret", expires_at="2030-01-01T00:00:00+00:00"),
        )
        == []
    )


def test_error_messages_name_the_key_and_accepted_shape():
    errors = gate.validate_create(_create_session_payload(extra=1))
    err = next(e for e in errors if e.field == "extra")
    assert "extra" in err.message and "accepted" in err.message.lower()


@pytest.mark.parametrize('value', [True, False])
def test_inline_artifact_option_accepts_literal_boolean(value):
    assert gate.validate_create(_create_session_payload(options={'include_inline_artifacts': value})) == []


@pytest.mark.parametrize('value', [None, 0, 1, 'false', [], {}])
def test_inline_artifact_option_rejects_non_boolean(value):
    errors = gate.validate_create(_create_session_payload(options={'include_inline_artifacts': value}))
    assert ('include_inline_artifacts', 'field_forbidden') in _codes(errors)


@pytest.mark.parametrize('key', ['inline_artifact_exports', 'captured', 'publications', 'src', 'copy_id', 'initialized', 'ready'])
def test_inline_export_metadata_is_server_owned(key):
    errors = gate.validate_create(_create_session_payload(options={key: {}}))
    assert (key, 'field_forbidden') in _codes(errors)
