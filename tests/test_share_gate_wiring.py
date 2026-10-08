"""End-to-end agent gate wiring and §7.1 precedence for all six wrappers."""

import asyncio
from datetime import datetime, UTC
from types import SimpleNamespace

import orjson
import pytest
from django.utils import timezone as djtz

from twicc import paths
from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType, Share
from twicc.core.services import share_mutation
from twicc.providers.helpers import get_provider_helpers


def _run(coro):
    return asyncio.run(coro)


def _mk(project, sid, *, spawned_by=None, spawn_root=None, parent_session=None):
    now = djtz.now()
    return Session.objects.create(
        id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
        type=SessionType.SUBAGENT if parent_session else SessionType.SESSION,
        spawned_by=spawned_by, spawn_root=spawn_root, parent_session=parent_session,
        created_at=now, last_new_content_at=now, last_line=21,
        compute_version=get_provider_helpers("claude_code").current_compute_version,
    )


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-gate", directory="/tmp/gate")


@pytest.fixture
def tree(project):
    parent = _mk(project, "parent")
    parent.spawn_root = parent
    parent.save(update_fields=["spawn_root"])
    caller = _mk(project, "caller", spawned_by=parent, spawn_root=parent)
    child = _mk(project, "child", spawned_by=caller, spawn_root=parent)
    sibling = _mk(project, "sibling", spawned_by=parent, spawn_root=parent)
    unrelated = _mk(project, "unrelated")
    subagent = _mk(project, "subagent", parent_session=caller)
    return SimpleNamespace(
        parent=parent, caller=caller, child=child, sibling=sibling,
        unrelated=unrelated, subagent=subagent,
    )


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture
def bookmarks(tree, project, artifacts_root):
    out = {}
    for session in (tree.child, tree.unrelated):
        path = artifacts_root / session.id / "demo" / "index.html"
        path.parent.mkdir(parents=True)
        path.write_bytes(f"<html>{session.id}</html>".encode())
        out[session.id] = ArtifactBookmark.objects.create(
            session=session, project=project, relative_path="demo/index.html",
            name=session.id, scope=PinMode.PROJECT,
        )
    return out


@pytest.fixture
def settings_state(monkeypatch):
    state = {
        "allowAgentSessionShares": False,
        "allowAgentArtifactShares": False,
        "shareBaseUrl": "share.example.com",
    }
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: dict(state))
    return state


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock", _passthrough,
    )


def _create_payload(target, caller, **over):
    payload = {
        "kind": "share:create", "caller_session_id": caller.id,
        "kind_target": "session", "session_id": target.id,
        "label": "", "password": None, "expires_at": None, "options": {},
    }
    payload.update(over)
    return payload


def _human_share(target, *, creator=None, snapshot=False):
    result = _run(share_mutation.create_share(
        "session", session=target,
        options={"mode": "snapshot" if snapshot else "live"},
        created_by_session=creator,
    ))
    assert result.success
    return Share.objects.select_related("session", "created_by_session").get(id=result.share_id)


def _managed_call(op, share, caller, *, fields=None):
    payload = {
        "kind": f"share:{op}", "caller_session_id": caller.id,
        "share_id": share.id,
    }
    if op == "update":
        payload["fields"] = fields if fields is not None else {"label": "updated"}
    fn = {
        "update": share_mutation.update_share_from_payload,
        "revoke": share_mutation.revoke_share_from_payload,
        "unrevoke": share_mutation.unrevoke_share_from_payload,
        "delete": share_mutation.delete_share_from_payload,
        "propagate": share_mutation.propagate_share_from_payload,
    }[op]
    return _run(fn(payload))


def _share_for_op(op, target, *, creator=None):
    share = _human_share(target, creator=creator, snapshot=op == "propagate")
    if op == "unrevoke":
        share.revoked_at = djtz.now()
        share.save(update_fields=["revoked_at"])
    return share


def _error(result):
    assert not result.success and result.errors
    return result.errors[0]


def test_gate_off_rejects_create(tree, settings_state):
    err = _error(_run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller))))
    assert (err.field, err.code) == ("settings", "agent_sharing_disabled")
    assert "session" in err.message and "Settings → Sharing" in err.message


@pytest.mark.parametrize("op", ["update", "revoke", "unrevoke", "delete", "propagate"])
def test_gate_off_rejects_each_loaded_operation(op, tree, settings_state):
    share = _share_for_op(op, tree.caller)
    err = _error(_managed_call(op, share, tree.caller))
    assert (err.field, err.code) == ("settings", "agent_sharing_disabled")


def test_kind_settings_are_independent(tree, bookmarks, settings_state):
    settings_state["allowAgentSessionShares"] = True
    assert _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller))).success
    artifact = {
        "kind": "share:create", "caller_session_id": tree.caller.id,
        "kind_target": "artifact", "bookmark_id": bookmarks[tree.child.id].id,
        "label": "", "password": None, "expires_at": None, "options": {},
    }
    assert _error(_run(share_mutation.create_share_from_payload(artifact))).code == "agent_sharing_disabled"
    settings_state["allowAgentSessionShares"] = False
    settings_state["allowAgentArtifactShares"] = True
    assert _run(share_mutation.create_share_from_payload(artifact)).success
    assert _error(_run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))).code == "agent_sharing_disabled"


def test_human_and_unknown_caller_bypass_gate(tree, settings_state):
    human = _create_payload(
        tree.caller, tree.caller,
        options={"max_display_mode": "debug"}, notify_on_view=True,
    )
    del human["caller_session_id"]
    created = _run(share_mutation.create_share_from_payload(human))
    assert created.success
    share = Share.objects.get(id=created.share_id)
    assert share.options["mode"] == "live"
    assert share.options["max_display_mode"] == "debug"
    assert _managed_call("update", share, SimpleNamespace(id="ghost")).success
    assert _managed_call("revoke", share, SimpleNamespace(id="ghost")).success
    ghost = _create_payload(tree.caller, SimpleNamespace(id="ghost"))
    assert _run(share_mutation.create_share_from_payload(ghost)).success


@pytest.mark.parametrize(
    ("target_name", "allowed"),
    [("caller", True), ("child", True), ("parent", False), ("sibling", False),
     ("unrelated", False), ("subagent", False)],
)
def test_create_scope(target_name, allowed, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    result = _run(share_mutation.create_share_from_payload(
        _create_payload(getattr(tree, target_name), tree.caller)))
    assert result.success is allowed
    if not allowed:
        assert (_error(result).field, _error(result).code) == ("session_id", "out_of_scope")


def test_artifact_scope(tree, bookmarks, settings_state):
    settings_state["allowAgentArtifactShares"] = True
    for session, allowed in ((tree.child, True), (tree.unrelated, False)):
        result = _run(share_mutation.create_share_from_payload({
            "kind": "share:create", "caller_session_id": tree.caller.id,
            "kind_target": "artifact", "bookmark_id": bookmarks[session.id].id,
            "label": "", "password": None, "expires_at": None, "options": {},
        }))
        assert result.success is allowed
        if not allowed:
            assert (_error(result).field, _error(result).code) == ("bookmark_id", "out_of_scope")


@pytest.mark.parametrize("op", ["update", "unrevoke", "delete", "propagate"])
@pytest.mark.parametrize("creator_name", [None, "unrelated"])
def test_managed_ops_refuse_null_or_foreign_provenance(
        op, creator_name, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    creator = getattr(tree, creator_name) if creator_name else None
    share = _share_for_op(op, tree.caller, creator=creator)
    err = _error(_managed_call(op, share, tree.caller))
    assert (err.field, err.code) == ("share_id", "out_of_scope")
    assert err.message == (
        "this share was created outside your spawn subtree (or by the user); "
        "you can manage only shares created by yourself or any session in your spawn subtree"
    )


@pytest.mark.parametrize("op", ["update", "unrevoke", "delete", "propagate"])
@pytest.mark.parametrize("creator_name", ["caller", "child"])
def test_managed_ops_allow_subtree_provenance(op, creator_name, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    share = _share_for_op(op, tree.caller, creator=getattr(tree, creator_name))
    assert _managed_call(op, share, tree.caller).success


def test_revoke_ignores_provenance_but_not_setting(tree, settings_state):
    share = _human_share(tree.caller)
    settings_state["allowAgentSessionShares"] = True
    assert _managed_call("revoke", share, tree.caller).success
    share.revoked_at = None
    share.save(update_fields=["revoked_at"])
    settings_state["allowAgentSessionShares"] = False
    assert _error(_managed_call("revoke", share, tree.caller)).code == "agent_sharing_disabled"


def test_create_layer_two_precedence(tree, settings_state):
    missing = _create_payload(
        SimpleNamespace(id="missing"), tree.caller,
        options={"max_display_mode": "debug"},
    )
    result = _run(share_mutation.create_share_from_payload(missing))
    assert (_error(result).field, _error(result).code) == (
        "session_id", "not_found")
    existing = _create_payload(
        tree.caller, tree.caller, options={"max_display_mode": "debug"})
    assert _error(_run(share_mutation.create_share_from_payload(existing))).code == "agent_sharing_disabled"
    settings_state["allowAgentSessionShares"] = True
    outside = _create_payload(
        tree.parent, tree.caller, options={"max_display_mode": "debug"})
    assert _error(_run(share_mutation.create_share_from_payload(outside))).code == "out_of_scope"
    final = _run(share_mutation.create_share_from_payload(existing))
    assert (_error(final).field, _error(final).code) == (
        "max_display_mode", "display_mode_forbidden")


def test_update_layer_two_precedence(tree, settings_state):
    missing = Share(id="shr_missing")
    result = _run(share_mutation.update_share_from_payload({
        "kind": "share:update", "caller_session_id": tree.caller.id,
        "share_id": missing.id, "fields": {"password": ""},
    }))
    assert (_error(result).field, _error(result).code) == ("share_id", "not_found")
    disabled = _human_share(tree.caller)
    assert _error(_managed_call(
        "update", disabled, tree.caller, fields={"password": ""})).code == "agent_sharing_disabled"
    settings_state["allowAgentSessionShares"] = True
    outside = _human_share(tree.caller, creator=tree.unrelated)
    assert _error(_managed_call(
        "update", outside, tree.caller, fields={"password": ""})).code == "out_of_scope"
    allowed = _human_share(tree.caller, creator=tree.caller)
    final = _managed_call(
        "update", allowed, tree.caller, fields={"password": ""})
    assert (_error(final).field, _error(final).code) == (
        "password", "field_forbidden")


def test_agent_frozen_default_explicit_live_and_human_live(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    frozen = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))
    frozen_row = Share.objects.get(id=frozen.share_id)
    assert frozen_row.options["mode"] == "snapshot"
    assert frozen_row.options["frozen_at_line"] == tree.caller.last_line
    live = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller, options={"mode": "live"})))
    assert Share.objects.get(id=live.share_id).options["mode"] == "live"
    human = _create_payload(tree.caller, tree.caller)
    del human["caller_session_id"]
    human_result = _run(share_mutation.create_share_from_payload(human))
    assert Share.objects.get(id=human_result.share_id).options["mode"] == "live"


def test_agent_expiry_errors_do_not_widen(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    before = Share.objects.count()
    bad_create = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller, expires_at="not-a-date")))
    assert (_error(bad_create).field, _error(bad_create).code) == ("expires_at", "invalid")
    assert Share.objects.count() == before
    share = _human_share(tree.caller, creator=tree.caller)
    share.expires_at = datetime(2030, 1, 1, tzinfo=UTC)
    share.save(update_fields=["expires_at"])
    bad_update = _managed_call(
        "update", share, tree.caller, fields={"expires_at": "garbage"})
    assert _error(bad_update).code == "invalid"
    share.refresh_from_db()
    assert share.expires_at == datetime(2030, 1, 1, tzinfo=UTC)


def test_share_host_and_attribution(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    settings_state["shareBaseUrl"] = ""
    agent = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))
    assert (_error(agent).field, _error(agent).code) == ("share_base_url", "share_host_unset")
    human = _create_payload(tree.caller, tree.caller)
    del human["caller_session_id"]
    assert _run(share_mutation.create_share_from_payload(human)).success
    settings_state["shareBaseUrl"] = "share.example.com"
    attributed = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))
    assert Share.objects.get(id=attributed.share_id).created_by_session_id == tree.caller.id
    assert Share.objects.get(id=_run(
        share_mutation.create_share_from_payload(human)).share_id).created_by_session_id is None


@pytest.mark.parametrize("bad", [["x"], {"id": "x"}, True])
def test_wrong_types_reject_instead_of_fail(bad, tree, settings_state):
    from twicc.drop_requests_watcher import execute_drop_payload
    payload = _create_payload(tree.caller, tree.caller)
    payload["caller_session_id"] = bad
    status = _run(execute_drop_payload(payload, "share:create"))
    assert status["status"] == "rejected"
    assert status["errors"][0]["code"] == "field_forbidden"


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("share:create", {"kind_target": "session", "session_id": ["bad"]}),
        ("share:create", {"kind_target": "artifact", "bookmark_id": {"bad": 1}}),
        ("share:update", {"share_id": {"bad": 1}, "fields": {}}),
        ("share:delete", {"share_id": True}),
    ],
)
def test_wrong_target_id_types_reject_through_transport(
        kind, payload, tree, settings_state):
    from twicc.drop_requests_watcher import execute_drop_payload

    envelope = {"kind": kind, "caller_session_id": tree.caller.id, **payload}
    status = _run(execute_drop_payload(envelope, kind))
    assert status["status"] == "rejected"
    assert len(status["errors"]) == 1
    assert status["errors"][0]["code"] == "field_forbidden"


@pytest.mark.parametrize(
    "bad_options",
    [["not-an-object"], {"show_title": "false"}],
)
def test_bad_options_fail_before_target_resolution(
        bad_options, tree, settings_state, monkeypatch):
    async def must_not_resolve(payload):
        raise AssertionError("target resolution ran before Layer-1 shape validation")

    monkeypatch.setattr(share_mutation, "_resolve_target_from_payload", must_not_resolve)
    result = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller, options=bad_options)))
    assert _error(result).code == "field_forbidden"


@pytest.mark.parametrize("password_case", ["absent", "null", "empty", "set"])
def test_agent_create_password_storage(password_case, tree, settings_state):
    from twicc.auth.hashers import verify_password

    settings_state["allowAgentSessionShares"] = True
    payload = _create_payload(tree.caller, tree.caller)
    if password_case == "absent":
        del payload["password"]
    elif password_case == "null":
        payload["password"] = None
    elif password_case == "empty":
        payload["password"] = ""
    else:
        payload["password"] = "secret"
    result = _run(share_mutation.create_share_from_payload(payload))
    assert result.success
    stored = Share.objects.get(id=result.share_id).password_hash
    if password_case == "set":
        assert verify_password("secret", stored)
    else:
        assert stored == ""


def test_agent_create_valid_expiry_is_stored(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    result = _run(share_mutation.create_share_from_payload(_create_payload(
        tree.caller, tree.caller,
        expires_at="2031-02-03T04:05:06+00:00",
    )))
    assert result.success
    assert Share.objects.get(id=result.share_id).expires_at == datetime(
        2031, 2, 3, 4, 5, 6, tzinfo=UTC,
    )


@pytest.mark.parametrize("display_mode", ["conversation", "simplified", "normal"])
def test_each_non_debug_display_mode_creates(
        display_mode, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    result = _run(share_mutation.create_share_from_payload(_create_payload(
        tree.caller, tree.caller,
        options={"max_display_mode": display_mode},
    )))
    assert result.success
    assert Share.objects.get(id=result.share_id).options["max_display_mode"] == display_mode


def test_agent_update_expiry_set_absent_and_clear(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    share = _human_share(tree.caller, creator=tree.caller)
    set_result = _managed_call(
        "update", share, tree.caller,
        fields={"expires_at": "2031-02-03T04:05:06+00:00"},
    )
    assert set_result.success
    share.refresh_from_db()
    stored = datetime(2031, 2, 3, 4, 5, 6, tzinfo=UTC)
    assert share.expires_at == stored

    absent = _run(share_mutation.update_share_from_payload({
        "kind": "share:update", "caller_session_id": tree.caller.id,
        "share_id": share.id,
    }))
    assert absent.success
    share.refresh_from_db()
    assert share.expires_at == stored

    cleared = _managed_call(
        "update", share, tree.caller, fields={"expires_at": None})
    assert cleared.success
    share.refresh_from_db()
    assert share.expires_at is None


def test_human_invalid_expiry_precedes_malformed_options(tree):
    payload = _create_payload(
        tree.caller, tree.caller, expires_at="not-a-date", options=7)
    del payload["caller_session_id"]
    result = _run(share_mutation.create_share_from_payload(payload))
    assert (_error(result).field, _error(result).code) == ("expires_at", "invalid")


def test_step_six_conflict_returns_one_applicable_error(
        tree, settings_state):
    """User decision: no precedence contract exists inside §7.1 step 6."""
    settings_state["allowAgentSessionShares"] = True
    settings_state["shareBaseUrl"] = ""
    result = _run(share_mutation.create_share_from_payload(_create_payload(
        tree.caller, tree.caller, expires_at="not-a-date",
        options={"max_display_mode": "debug"},
    )))
    assert not result.success
    assert len(result.errors) == 1
    assert result.errors[0].code in {
        "display_mode_forbidden", "share_host_unset", "invalid",
    }


def test_both_real_transport_envelopes(tree, settings_state, tmp_path, monkeypatch):
    from twicc.cli import share_mutation as cli_share_mutation
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.drop_requests_watcher import execute_drop_payload

    settings_state["allowAgentSessionShares"] = True
    captured = []
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: tree.caller)
    monkeypatch.setattr(
        "twicc.cli.share_mutation._run_drop",
        lambda payload, **kwargs: captured.append(payload),
    )
    cli_share_mutation.run_create_session(
        session_id=tree.caller.id, label="", password=None, expires_at=None,
        mode=None, options={}, timeout=30,
    )
    payload = captured[0]

    def backend_cli_side(candidate):
        transport.ensure_server_available()
        submission = transport.submit(candidate, kind="share:create")
        outcome = transport.wait(submission, timeout_seconds=10)
        submission.cleanup()
        return outcome

    async def through_backend_transport(candidate):
        loop = asyncio.get_running_loop()
        token = transport.backend_loop.set(loop)
        try:
            # Match MCP: the synchronous CLI transport runs in a worker thread
            # while execute_drop_payload runs on the backend event loop.
            return await asyncio.to_thread(backend_cli_side, candidate)
        finally:
            transport.backend_loop.reset(token)

    valid_backend = _run(through_backend_transport(payload))
    assert valid_backend.status == "created"
    invalid_backend = _run(through_backend_transport(payload | {"extra": 1}))
    assert invalid_backend.status == "rejected"
    assert invalid_backend.data["errors"][0]["code"] == "field_forbidden"

    drop_dir = tmp_path / "drops"
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: drop_dir)
    for candidate, expected in (
        (payload, "created"), (payload | {"extra": 1}, "rejected"),
    ):
        dropped = write_drop_file(candidate, kind="share:create")
        envelope = orjson.loads(dropped.path.read_bytes())
        assert envelope["payload"]["kind"] == "share:create"
        result = _run(execute_drop_payload(envelope["payload"], "share:create"))
        assert result["status"] == expected
        if expected == "rejected":
            assert result["errors"][0]["code"] == "field_forbidden"


def test_update_value_rules_end_to_end(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    share = _human_share(tree.caller, creator=tree.caller)
    old_hash = share.password_hash
    assert _managed_call(
        "update", share, tree.caller, fields={"password": "new-password"}).success
    share.refresh_from_db()
    assert share.password_hash and share.password_hash != old_hash
    assert _managed_call(
        "update", share, tree.caller, fields={"label": "allowed"}).success
    forbidden = _managed_call(
        "update", share, tree.caller, fields={"options": {"mode": "live"}})
    assert (_error(forbidden).field, _error(forbidden).code) == (
        "options", "field_forbidden")
