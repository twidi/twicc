"""The CLI-layer enrichment every session-emitting command applies."""

from __future__ import annotations

import orjson
import pytest
from django.utils import timezone

from twicc import projects
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import sessions_get as cli_sessions_get
from twicc.cli._session_payload import CLI_ENRICHED_KEYS, cli_session_payloads
from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import serialize_session
from twicc.paths import get_session_artifacts_dir, get_session_scratch_dir

SYNCED = {
    "claudeCodeDefaultModel": "opus", "claudeCodeDefaultEffort": "high",
    "claudeCodeDefaultPermissionMode": "default", "claudeCodeDefaultThinking": True,
    "claudeCodeDefaultClaudeInChrome": False, "claudeCodeDefaultFastMode": False,
    "claudeCodeDefaultContextMax": 200000,
    "codexDefaultModel": "gpt-sol", "codexDefaultEffort": "medium",
    "codexDefaultPermissionMode": "read_only", "codexDefaultContextMax": 272000,
    "codexDefaultFastMode": False,
}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    # resolve_agent_settings imports read_synced_settings at call time.
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: dict(SYNCED))
    monkeypatch.setattr(projects, "_project_directories", {})
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)
    monkeypatch.setattr("twicc.cli.sessions_get._PLACEHOLDER_TEMPLATE", None)


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-enrich", directory="/tmp/enrich")


def make(project, sid, **extra):
    values = {
        "id": sid, "project": project, "provider": "claude_code", "file_path": f"{sid}.jsonl",
        "type": SessionType.SESSION, "created_at": timezone.now(), "last_line": 3,
        "user_message_count": 1,
    }
    return Session.objects.create(**(values | extra))


def _rows(capsysbinary):
    payload = orjson.loads(capsysbinary.readouterr().out)
    return payload["items"] if isinstance(payload, dict) else payload


def test_paths_and_directory(project):
    [row] = cli_session_payloads([make(project, "e1")])
    assert row["project_directory"] == "/tmp/enrich"
    assert row["artifacts_dir"] == str(get_session_artifacts_dir("e1")), "a path even with no artifact"
    assert row["has_artifacts"] is False
    assert row["scratch_dir"] == str(get_session_scratch_dir("e1"))
    assert row["orchestration_scratch_dir"] is None


def test_orchestration_scratch_dir_comes_from_the_annotation(project):
    session = make(project, "e2", annotations={"scratch_dir": "/shared/x"})
    assert cli_session_payloads([session])[0]["orchestration_scratch_dir"] == "/shared/x"


def test_null_settings_are_resolved_and_question_widget_true(project):
    [row] = cli_session_payloads([make(project, "e3")])
    assert (row["selected_model"], row["effort"], row["permission_mode"]) == ("opus", "high", "default")
    assert row["thinking_enabled"] is True
    assert row["question_widget"] is True


def test_stored_settings_win_and_false_stays_false(project):
    session = make(project, "e4", selected_model="sonnet", effort="low", question_widget=False)
    [row] = cli_session_payloads([session])
    assert (row["selected_model"], row["effort"], row["question_widget"]) == ("sonnet", "low", False)


def test_a_subagent_keeps_its_stored_values(project):
    parent = make(project, "e5")
    sub = make(project, "e5-sub", type=SessionType.SUBAGENT, parent_session=parent)
    [row] = cli_session_payloads([sub])
    assert row["selected_model"] is None
    assert row["question_widget"] is None
    assert row["artifacts_dir"] == str(get_session_artifacts_dir("e5-sub"))


def test_a_codex_row_keeps_null_for_unsupported_fields(project):
    [row] = cli_session_payloads([make(project, "e6", provider="codex")])
    assert row["thinking_enabled"] is None
    assert row["claude_in_chrome"] is None
    assert row["selected_model"] == "gpt-sol"


def test_one_query_for_a_cold_page_none_for_a_warm_one(project, django_assert_num_queries):
    other = Project.objects.create(id="-tmp-enrich-2", directory="/tmp/enrich-2")
    rows = [make(project, "q1"), make(project, "q2"), make(other, "q3")]
    with django_assert_num_queries(1):
        cli_session_payloads(rows)
    with django_assert_num_queries(0):
        cli_session_payloads(rows)


def test_the_serializer_is_untouched(project):
    session = make(project, "e7")
    before = serialize_session(session)
    cli_session_payloads([session])
    assert serialize_session(session) == before
    assert "question_widget" not in before
    assert "project_directory" not in before


def test_enriched_keys_are_the_ones_the_serializer_lacks(project):
    session = make(project, "e8")
    added = set(cli_session_payloads([session])[0]) - set(serialize_session(session))
    assert added == set(CLI_ENRICHED_KEYS)


def test_the_three_commands_carry_the_enrichment(project, capsysbinary):
    parent = make(project, "c1")
    make(project, "c1-sub", type=SessionType.SUBAGENT, parent_session=parent)
    cli_sessions.main(project=project.id, full=True)
    assert all(set(CLI_ENRICHED_KEYS) <= set(r) for r in _rows(capsysbinary))
    cli_sessions_get.main(["c1", "nope"], full=True)
    known, placeholder = _rows(capsysbinary)
    assert set(known) == set(placeholder)
    assert all(placeholder[k] is None for k in CLI_ENRICHED_KEYS)
    assert placeholder["artifacts_dir"] is None
    cli_session.agents("c1", full=True)
    [sub] = _rows(capsysbinary)
    assert sub["selected_model"] is None and sub["process"] is None


def test_the_empty_database_placeholder_has_the_enriched_keys(db, capsysbinary):
    """Review focus 2: the `{"id": None}` fallback of the template."""
    cli_sessions_get.main(["ghost"], full=True)
    [placeholder] = _rows(capsysbinary)
    assert set(CLI_ENRICHED_KEYS) <= set(placeholder)
    assert placeholder["known"] is False
