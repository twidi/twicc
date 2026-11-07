"""REST presentation metadata keeps the existing English API contract intact."""

import asyncio
from types import SimpleNamespace

import orjson
import pytest
from django.test import AsyncRequestFactory

from twicc import views
from twicc.core.ui_messages import ui_message, ui_message_text


def payload(response):
    return orjson.loads(response.content)


@pytest.mark.parametrize("body,error,key", [
    (b"{", "Invalid JSON", "backend.request.invalid_json"),
    (b"{}", "directory is required", "backend.projects.directory_required"),
])
def test_create_project_validation_retains_english(body, error, key):
    response = asyncio.run(views._create_project(
        AsyncRequestFactory().post("/", body, content_type="application/json")
    ))
    assert response.status_code == 400
    assert payload(response) == {"error": error, "ui_message": ui_message(key)}


def test_missing_project_directory_retains_recovery_code(tmp_path):
    response = asyncio.run(views._create_project(AsyncRequestFactory().post(
        "/", orjson.dumps({"directory": str(tmp_path / "missing")}), content_type="application/json"
    )))
    assert response.status_code == 400
    assert payload(response) == {
        "error": "Directory does not exist",
        "code": "directory_not_found",
        "ui_message": ui_message("backend.projects.directory_missing"),
    }


def test_project_directory_os_error_keeps_raw_diagnostic(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("device unavailable")
    monkeypatch.setattr(views.os, "makedirs", fail)
    response = asyncio.run(views._create_project(AsyncRequestFactory().post(
        "/", orjson.dumps({"directory": str(tmp_path / "missing"), "create_directory": True}),
        content_type="application/json",
    )))
    assert response.status_code == 400
    assert payload(response) == {
        "error": "Failed to create directory: device unavailable",
        "ui_message": ui_message("backend.projects.directory_create_failed", {"detail": "device unavailable"}),
    }


def test_limit_descriptor_carries_named_bound():
    response = asyncio.run(views.all_sessions(AsyncRequestFactory().get("/?limit=invalid")))
    assert response.status_code == 400
    assert payload(response) == {
        "error": f"Invalid 'limit' parameter: must be an integer between 1 and {views.SESSIONS_PAGE_SIZE}",
        "ui_message": ui_message("backend.sessions.invalid_limit", {"maximum": views.SESSIONS_PAGE_SIZE}),
    }


def test_worktree_service_error_publishes_identity(monkeypatch):
    from twicc.core.services import worktree_creation
    message = ui_message_text("Branch is required.", "backend.worktrees.branch_required")
    async def reject(**kwargs):
        return SimpleNamespace(success=False, errors=[SimpleNamespace(message=message, code="branch_required")])
    monkeypatch.setattr(worktree_creation, "create_worktree_from_source", reject)
    response = asyncio.run(views.project_worktrees(AsyncRequestFactory().post(
        "/", b"{}", content_type="application/json"
    ), "project"))
    assert response.status_code == 400
    assert payload(response) == {"error": "Branch is required.", "ui_message": message.ui_message}
    assert orjson.loads(orjson.dumps({"error": message})) == {"error": "Branch is required."}


def test_file_service_error_metadata_survives_json(monkeypatch, tmp_path):
    from twicc import file_content
    file = tmp_path / "sample.txt"
    file.write_text("text")
    message = ui_message_text("File too large", "backend.files.too_large", {"maximum": 5})
    monkeypatch.setattr(file_content, "get_file_content", lambda path: {"error": message, "too_large": True})
    response = asyncio.run(views.standalone_file_content(AsyncRequestFactory().get("/", {"path": str(file)})))
    assert response.status_code == 400
    assert payload(response) == {"error": "File too large", "too_large": True, "ui_message": message.ui_message}


def test_git_exception_descriptor_keeps_diagnostic(monkeypatch):
    from twicc import git
    message = ui_message_text("Git log failed: fatal", "backend.git.command_failed", {"command": "git log", "detail": "fatal"})
    async def resolve(*args, **kwargs):
        return "/repo"
    def fail(*args, **kwargs):
        raise git.GitError(message)
    monkeypatch.setattr(views, "_resolve_session_git_directory", resolve)
    monkeypatch.setattr(git, "get_current_branch", lambda path: "main")
    monkeypatch.setattr(git, "get_git_log", fail)
    response = asyncio.run(views.git_log(AsyncRequestFactory().get("/"), "project"))
    assert response.status_code == 500
    assert payload(response) == {"error": "Git log failed: fatal", "ui_message": message.ui_message}


def test_notification_results_publish_per_url_metadata(monkeypatch):
    from twicc import external_notifications
    message = ui_message_text("Notification failed", "backend.notifications.failed")
    async def test_urls(urls):
        return [{"url_masked": "service://***", "ok": False, "error": message}]
    monkeypatch.setattr(external_notifications, "test_notification_urls", test_urls)
    response = asyncio.run(views.external_notifications_test(AsyncRequestFactory().post(
        "/", orjson.dumps({"urls": ["service://private"]}), content_type="application/json",
    )))
    assert response.status_code == 200
    assert payload(response)["results"] == [{
        "url_masked": "service://***", "ok": False, "error": "Notification failed", "ui_message": message.ui_message,
    }]


def test_plain_external_text_does_not_gain_presentation_metadata():
    external = {"error": "An external diagnostic", "code": "external"}
    assert views._with_ui_message(external) is external
    assert views._with_ui_message(None) is None
