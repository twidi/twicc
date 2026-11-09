"""The live state of a project directory, and its recreation from a session banner."""

from __future__ import annotations

import os
from unittest.mock import patch

import orjson
import pytest
from asgiref.sync import async_to_sync
from django.test import RequestFactory

from twicc import views
from twicc.core.models import Project
from twicc.projects import project_directory_status


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()

    monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", _passthrough)


@pytest.fixture(autouse=True)
def _no_broadcast(monkeypatch):
    async def _broadcast(project_id):
        return None

    monkeypatch.setattr("twicc.projects._broadcast_project_updated", _broadcast)


def call(view, method, project_id):
    request = getattr(RequestFactory(), method)(f"/api/projects/{project_id}/")
    response = async_to_sync(view)(request, project_id)
    return response.status_code, orjson.loads(response.content)


def test_an_existing_directory_is_ok(tmp_path):
    assert project_directory_status(str(tmp_path)) == "ok"


def test_an_unknown_directory_is_ok():
    assert project_directory_status(None) == "ok"


def test_a_missing_directory_can_be_recreated(tmp_path):
    assert project_directory_status(str(tmp_path / "gone" / "deeper")) == "missing"


def test_a_file_at_the_path_blocks_the_recreation(tmp_path):
    path = tmp_path / "target"
    path.write_text("x")
    assert project_directory_status(str(path)) == "blocked"


def test_a_broken_symlink_at_the_path_blocks_the_recreation(tmp_path):
    path = tmp_path / "target"
    path.symlink_to(tmp_path / "nowhere")
    assert project_directory_status(str(path)) == "blocked"


def test_a_file_as_a_parent_blocks_the_recreation(tmp_path):
    parent = tmp_path / "parent"
    parent.write_text("x")
    assert project_directory_status(str(parent / "child")) == "blocked"


def test_an_ancestor_without_write_access_blocks_the_recreation(tmp_path):
    with patch("twicc.projects.os.access", return_value=False):
        assert project_directory_status(str(tmp_path / "gone")) == "blocked"


@pytest.mark.django_db(transaction=True)
def test_the_status_endpoint_writes_nothing(tmp_path):
    gone = tmp_path / "gone"
    Project.objects.create(id="-status", directory=str(gone), stale=False)

    assert call(views.project_directory_status_view, "get", "-status") == (200, {"status": "missing"})
    assert Project.objects.get(id="-status").stale is False


@pytest.mark.django_db(transaction=True)
def test_the_status_endpoint_reports_an_unknown_project():
    status, _body = call(views.project_directory_status_view, "get", "-absent")
    assert status == 404


@pytest.mark.django_db(transaction=True)
def test_recreate_creates_an_empty_directory_and_clears_the_stored_flag(tmp_path):
    gone = tmp_path / "gone" / "deeper"
    Project.objects.create(id="-recreate", directory=str(gone), stale=True)

    status, body = call(views.project_recreate_directory, "post", "-recreate")

    assert status == 200
    assert body["stale"] is False
    assert os.path.isdir(gone)
    assert os.listdir(gone) == []
    assert Project.objects.get(id="-recreate").stale is False


@pytest.mark.django_db(transaction=True)
def test_recreate_refuses_a_file_at_the_path_and_leaves_it(tmp_path):
    path = tmp_path / "target"
    path.write_text("x")
    Project.objects.create(id="-file", directory=str(path), stale=True)

    status, body = call(views.project_recreate_directory, "post", "-file")

    assert status == 400
    assert body["ui_message"]["key"] == "backend.projects.directory_create_failed"
    assert path.read_text() == "x"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("method", ["get", "put"])
def test_recreate_only_accepts_post(tmp_path, method):
    Project.objects.create(id="-method", directory=str(tmp_path / "gone"))
    status, _body = call(views.project_recreate_directory, method, "-method")
    assert status == 405
    assert not (tmp_path / "gone").exists()


@pytest.mark.django_db(transaction=True)
def test_recreate_reports_an_unknown_project():
    status, _body = call(views.project_recreate_directory, "post", "-absent")
    assert status == 404
