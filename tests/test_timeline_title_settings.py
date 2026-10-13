from unittest.mock import patch

import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.session_update import persist_session_settings, update_session_title_from_payload
from tests.test_title_automation import env, session  # noqa: F401  (fixtures reused below)
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-title", directory="/tmp/tl-title")


def make(project, **kw):
    return Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex", **kw)


def test_user_title_update_records_source_user(project):
    make(project, title="Old")
    with patch("twicc.core.services.session_update.ensure_provider_running"):
        run_with_writer(lambda: update_session_title_from_payload({"session_id": "s1", "title": "New"}))
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "title.changed" and row.actor == "user"
    assert row.data == {"source": "user", "new": "New", "old": "Old"}


def test_user_title_update_with_the_same_text_records_nothing(project):
    make(project, title="Same")
    with patch("twicc.core.services.session_update.ensure_provider_running"):
        run_with_writer(lambda: update_session_title_from_payload({"session_id": "s1", "title": "Same"}))
    assert kinds("s1") == []


def test_rest_title_patch_records_source_user(project):
    from django.test import RequestFactory

    from twicc.views import session_detail

    make(project, title="Old")
    request = RequestFactory().patch("/", data={"title": "New"}, content_type="application/json")
    with patch("twicc.views.ensure_provider_running"):
        response = run_with_writer(lambda: session_detail(request, project.id, "s1"))
    assert response.status_code == 200
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "user", "new": "New", "old": "Old"}


def test_placeholder_title_records_source_auto(project):
    from twicc.providers.compute_base import BaseSessionCompute

    make(project)
    assert BaseSessionCompute.apply_placeholder_title("s1", "First words")
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "auto", "new": "First words", "old": None}


def test_provider_title_overwrite_records_source_agent(project):
    from twicc.providers.compute_base import BaseSessionCompute

    make(project, title="Before", title_origin="user")
    assert BaseSessionCompute._write_provider_title("s1", "From provider")
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "agent", "new": "From provider", "old": "Before"}


def test_settings_change_records_only_changedbundle_fields(project):
    make(project, selected_model="sonnet", effort="medium")
    persist_session_settings("s1", {"selected_model": "opus", "effort": "medium"})
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "settings.changed" and row.data == {"changes": {"selected_model": ["sonnet", "opus"]}}


def test_non_bundle_columns_are_not_recorded(project):
    make(project, selected_model="sonnet")
    persist_session_settings("s1", {"title_checked_at": None})
    assert kinds("s1") == []


def test_settings_update_without_a_bundle_change_records_nothing(project):
    make(project, selected_model="sonnet")
    persist_session_settings("s1", {"selected_model": "sonnet"})
    assert kinds("s1") == []


def test_settings_actor_and_reason_are_passed_through(project):
    make(project, selected_model="sonnet")
    persist_session_settings("s1", {"selected_model": "opus"}, actor="system", reason="model_retired")
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.actor == "system" and row.reason == "model_retired"


def test_retire_job_records_a_system_settings_change(project):
    from twicc.providers import db_writer

    make(project, selected_model="old-model")
    job = db_writer._RetireSessionsJob(provider=Provider.CODEX, updates={"s1": {"selected_model": "new-model"}}, future=None)
    db_writer._apply_retire_sessions_job(job)
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.actor == "system" and row.reason == "model_retired"
    assert row.data == {"changes": {"selected_model": ["old-model", "new-model"]}}


def test_set_mode_suggestion_records_a_user_settings_change_once(project):
    from twicc.providers.claude_code.ws import update_session_permission_mode

    make(project, permission_mode="default", hidden=True)  # hidden: no broadcast needed
    run_with_writer(lambda: update_session_permission_mode("s1", "acceptEdits"))
    run_with_writer(lambda: update_session_permission_mode("s1", "acceptEdits"))
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.data == {"changes": {"permission_mode": ["default", "acceptEdits"]}}
    assert row.actor == "user" and row.reason == "set_mode"


def test_hybrid_switch_resets_claude_in_chrome_as_system(project):
    make(project, claude_in_chrome=True)
    persist_session_settings("s1", {"claude_in_chrome": False}, actor="system", reason="switch-hybrid")
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.data == {"changes": {"claude_in_chrome": [True, False]}}
    assert row.actor == "system" and row.reason == "switch-hybrid"


def test_automatic_title_check_records_source_auto(env, session):  # noqa: F811
    """Uses the ``env`` / ``session`` fixtures of ``tests/test_title_automation.py``."""
    from asgiref.sync import async_to_sync

    from tests.test_title_automation import NOW
    from twicc.title_cadence import TitleCheckState

    async_to_sync(env.module._apply_check)(
        session, TitleCheckState(7, NOW), title="New title", now=NOW, closing=False, succeeded=True)
    row = TimelineEvent.objects.get(session_id=session.id, kind="title.changed")
    assert row.data == {"source": "auto", "new": "New title", "old": session.title} and row.actor == "agent"


def test_codex_title_import_records_auto_for_a_null_title_and_agent_otherwise(project):
    from twicc.providers.codex.titles import SyncSessionTitlesJob, _apply_sync_session_titles_job

    make(project, title=None)
    Session.objects.create(id="s2", file_path="s2.jsonl", project=project, provider="codex", title="Old")
    _apply_sync_session_titles_job(SyncSessionTitlesJob({"s1": "Imported", "s2": "Renamed"}, None))
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "auto", "new": "Imported", "old": None}
    assert TimelineEvent.objects.get(session_id="s2").data == {"source": "agent", "new": "Renamed", "old": "Old"}


def test_pending_title_flush_records_source_user(project):
    from types import SimpleNamespace

    from twicc.agent.base_manager import BaseAgentManager

    make(project, title=None)
    manager = SimpleNamespace(
        _broadcast_session_updated=lambda sid: BaseAgentManager._broadcast_session_updated(None, sid),
    )
    run_with_writer(lambda: BaseAgentManager._try_flush_pending_title(
        manager, SimpleNamespace(session_id="s1", provider="codex"), "Chosen"))
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "user", "new": "Chosen", "old": None}


def test_record_title_change_never_raises(project, monkeypatch):
    from twicc.core.services.session_timeline import record_title_change

    make(project)
    monkeypatch.setattr("twicc.core.services.session_timeline.Session.objects",
                        type("M", (), {"filter": staticmethod(lambda **k: (_ for _ in ()).throw(RuntimeError("db")))})())
    assert record_title_change("s1", "New", source="user") is None


def test_settings_write_survives_a_failing_timeline_lookup(project, monkeypatch):
    make(project, permission_mode="default")
    monkeypatch.setattr("twicc.core.services.session_update.project_id_of",
                        lambda sid: (_ for _ in ()).throw(RuntimeError("db")))
    persist_session_settings("s1", {"permission_mode": "acceptEdits"})
    assert Session.objects.get(id="s1").permission_mode == "acceptEdits"
    assert kinds("s1") == []
