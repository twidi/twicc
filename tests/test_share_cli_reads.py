"""CLI share list/show behaviour (direct-DB reads). Extended later by the
redaction task; here: the §8 cross-kind filter repair."""

import asyncio

import pytest
from django.utils import timezone as djtz

from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType
from twicc.core.services import share_mutation


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-reads", directory="/tmp/reads")


@pytest.fixture
def session(project):
    now = djtz.now()
    return Session.objects.create(
        id="sess-reads", project=project, provider="claude_code",
        file_path="sess-reads.jsonl", type=SessionType.SESSION,
        created_at=now, last_line=5,
    )


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock", _passthrough,
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def one_share_each(session, bookmark, tmp_path, monkeypatch):
    """One session share + one artifact share of the same session/project."""
    from twicc import paths
    data_dir = tmp_path / "data"
    (data_dir / "artifacts" / session.id / "demo").mkdir(parents=True)
    (data_dir / "artifacts" / session.id / "demo" / "index.html").write_bytes(b"<html/>")
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    s1 = _run(share_mutation.create_share("session", session=session, options={}))
    s2 = _run(share_mutation.create_share("artifact", bookmark=bookmark, options={}))
    assert s1.success and s2.success
    return s1.share_id, s2.share_id


def _list(**kwargs):
    """Rows of ``share list``. The listing emits through ``emit_list`` (the
    pagination-aware wrapper); ``show`` still goes through ``emit_json``."""
    from twicc.cli import share as cli_share
    captured = []
    import twicc.cli.share
    orig = twicc.cli.share.emit_list
    twicc.cli.share.emit_list = lambda items, **_: captured.append(items)
    try:
        cli_share.list_main(**kwargs)
    finally:
        twicc.cli.share.emit_list = orig
    return captured[0]


def _show(share_id):
    from twicc.cli import share as cli_share
    captured = []
    import twicc.cli.share
    orig = twicc.cli.share.emit_json
    twicc.cli.share.emit_json = captured.append
    try:
        cli_share.show_main(share_id)
    finally:
        twicc.cli.share.emit_json = orig
    return captured[0]


class _FakeCaller:
    id = "agent-1"


@pytest.fixture
def as_agent(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: _FakeCaller())


@pytest.fixture
def as_human(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)


@pytest.fixture
def settings_state(monkeypatch):
    state = {"allowAgentSessionShares": False, "allowAgentArtifactShares": False,
             "shareBaseUrl": "share.example.com"}
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings",
                        lambda: dict(state))
    return state


def test_session_filter_returns_both_kinds(one_share_each, session):
    rows = _list(session=session.id)
    assert {r["kind"] for r in rows} == {"session", "artifact"}


def test_project_filter_returns_both_kinds(one_share_each, project):
    rows = _list(project=project.id)
    assert {r["kind"] for r in rows} == {"session", "artifact"}


def test_project_filter_expands_downward_to_worktree_for_both_kinds(
        one_share_each, project):
    """A main project includes child worktrees; a worktree stays local."""
    from twicc import paths

    worktree = Project.objects.create(
        id="-tmp-reads-wt", directory="/tmp/reads-wt", worktree_of=project,
    )
    now = djtz.now()
    wt_session = Session.objects.create(
        id="sess-reads-wt", project=worktree, provider="claude_code",
        file_path="sess-reads-wt.jsonl", type=SessionType.SESSION,
        created_at=now, last_line=8,
    )
    wt_bookmark = ArtifactBookmark.objects.create(
        session=wt_session, project=worktree,
        relative_path="demo/index.html", name="Worktree demo", scope=PinMode.PROJECT,
    )
    source = paths.get_data_dir() / "artifacts" / wt_session.id / "demo" / "index.html"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"<html>worktree</html>")
    wt_session_result = _run(
        share_mutation.create_share("session", session=wt_session, options={}))
    wt_artifact_result = _run(
        share_mutation.create_share("artifact", bookmark=wt_bookmark, options={}))
    assert wt_session_result.success and wt_artifact_result.success
    worktree_ids = {wt_session_result.share_id, wt_artifact_result.share_id}

    main_rows = _list(project=project.id)
    assert worktree_ids <= {row["id"] for row in main_rows}
    worktree_rows = _list(project=worktree.id)
    assert {row["id"] for row in worktree_rows} == worktree_ids
    assert {row["kind"] for row in worktree_rows} == {"session", "artifact"}


def test_unrelated_session_filter_returns_nothing(one_share_each):
    assert _list(session="other-session") == []


def test_agent_list_redacts_kind_with_setting_off(one_share_each, as_agent, settings_state):
    settings_state["allowAgentSessionShares"] = True   # artifact stays off
    rows = _list()
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["session"]["token"] and by_kind["session"]["url"].startswith("https://")
    assert "redacted" not in by_kind["session"]
    art = by_kind["artifact"]
    assert art["token"] is None and art["url"] is None and art["url_path"] is None
    assert art["redacted"] is True
    assert art["id"]  # the row itself is never dropped


def test_agent_show_redacts_too(one_share_each, as_agent, settings_state):
    _, artifact_share_id = one_share_each
    data = _show(artifact_share_id)
    assert data["id"] == artifact_share_id
    assert data["token"] is None
    assert data["url"] is None
    assert data["url_path"] is None
    assert data["redacted"] is True
    settings_state["allowAgentArtifactShares"] = True
    data = _show(artifact_share_id)
    assert data["id"] == artifact_share_id
    assert data["token"]
    assert data["url"].startswith("https://")
    assert data["url_path"].startswith("/share/")
    assert "redacted" not in data


@pytest.mark.parametrize("operation", ["list", "show"])
def test_read_uses_one_settings_snapshot_per_invocation(
        operation, one_share_each, as_agent, monkeypatch):
    reads = []

    def read_settings():
        reads.append(None)
        if len(reads) == 1:
            return {
                "allowAgentSessionShares": True,
                "allowAgentArtifactShares": False,
                "shareBaseUrl": "first.example.com",
            }
        return {
            "allowAgentSessionShares": False,
            "allowAgentArtifactShares": False,
            "shareBaseUrl": "second.example.com",
        }

    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", read_settings)
    session_share_id, _ = one_share_each

    if operation == "list":
        data = _list(kind="session")[0]
    else:
        data = _show(session_share_id)

    assert len(reads) == 1
    assert data["url"].startswith("https://first.example.com/share/")
    assert "redacted" not in data


def test_human_never_redacted(one_share_each, as_human, settings_state):
    rows = _list()
    assert all(r["token"] for r in rows)
    assert not any("redacted" in r for r in rows)
    shown = _show(one_share_each[1])
    assert shown["token"]
    assert shown["url"].startswith("https://")
    assert shown["url_path"].startswith("/share/")
    assert "redacted" not in shown


def test_create_then_show_yields_url(session, as_human, settings_state):
    """Service/read composition: a created share id can be passed to the real
    CLI show path, which yields the absolute URL. Task 6 separately proves the
    final CLI result formatter; Task 16 proves that formatter through MCP."""
    from twicc.drop_requests_watcher import execute_drop_payload
    status = _run(execute_drop_payload({
        "kind": "share:create", "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None, "expires_at": None,
    }, "share:create"))
    assert status["status"] == "created"
    assert status["share_id"]
    assert "url" not in status and "token" not in status
    data = _show(status["share_id"])
    assert data["url"] == "https://share.example.com" + data["url_path"]


def test_session_self_finds_artifact_created_by_caller(
        one_share_each, session, settings_state, monkeypatch):
    """§14 List filters: --session self composes keyword resolution with
    the real cross-kind filter and finds the caller's artifact share."""
    from typer.testing import CliRunner

    from twicc.cli import app
    from twicc.core.models import Share

    _session_share_id, artifact_share_id = one_share_each
    Share.objects.filter(id=artifact_share_id).update(created_by_session=session)
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: session,
    )
    settings_state["allowAgentArtifactShares"] = True
    captured = []
    monkeypatch.setattr(
        "twicc.cli.share.emit_list", lambda items, **_: captured.append(items),
    )
    result = CliRunner().invoke(app, ["share", "--session", "self"])
    assert result.exit_code == 0
    assert any(row["id"] == artifact_share_id for row in captured[0])
