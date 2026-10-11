"""File downloads from the Files, Artifacts and Git trees.

Two surfaces:
- ``?download=1`` on the raw-serving routes (project scope and standalone
  scope), which must serve an attachment AND bypass the artifact broker wrap.
- ``git-file-download`` / ``git-diff-download``, which resolve the bytes from
  the working tree or from a revision.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import AsyncClient
from django.utils import timezone

from twicc.core.models import Project, Session, SessionType

pytestmark = pytest.mark.django_db(transaction=True)


def _run(coro):
    return asyncio.run(coro)


def _b64(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def _body(response) -> bytes:
    return b"".join(response.streaming_content)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


# ── ?download=1 on the raw routes ────────────────────────────────────────────


@pytest.fixture
def project(tmp_path, transactional_db):
    directory = tmp_path / "proj"
    directory.mkdir()
    return Project.objects.create(id="-tmp-dl-proj", directory=str(directory))


def test_project_route_serves_an_attachment(client, project):
    target = Path(project.directory) / "notes.txt"
    target.write_bytes(b"hello")

    url = f"/api/projects/{project.id}/file-raw{target}?download=1"
    resp = _run(client.get(url))

    assert resp.status_code == 200
    assert resp["Content-Disposition"] == 'attachment; filename="notes.txt"'
    assert _body(resp) == b"hello"


def test_project_route_stays_inline_without_the_flag(client, project):
    target = Path(project.directory) / "notes.txt"
    target.write_bytes(b"hello")

    resp = _run(client.get(f"/api/projects/{project.id}/file-raw{target}"))

    assert resp.status_code == 200
    assert "attachment" not in resp.get("Content-Disposition", "")


def test_download_bypasses_the_artifact_broker_wrap(client, project):
    """An ``<a download>`` click is a navigation, so the browser sends
    ``Sec-Fetch-Dest: document``. The file must still arrive as written on
    disk — not shim-injected and CSP-gated."""
    target = Path(project.directory) / "page.html"
    target.write_bytes(b"<html><head></head></html>")

    url = f"/api/projects/{project.id}/file-raw{target}?download=1"
    resp = _run(client.get(url, headers={"sec-fetch-dest": "document"}))

    assert resp.status_code == 200
    assert _body(resp) == b"<html><head></head></html>"
    assert "Content-Security-Policy" not in resp

    # Same request without the flag goes through the broker.
    wrapped = _run(client.get(url.removesuffix("?download=1"), headers={"sec-fetch-dest": "document"}))
    assert b"<script" in wrapped.content


def test_standalone_route_serves_an_attachment(client, tmp_path):
    root = tmp_path / "artifacts-root"
    root.mkdir()
    target = root / "report.md"
    target.write_bytes(b"# Report")

    resp = _run(client.get(f"/api/file-raw/{_b64(str(root))}{target}?download=1"))

    assert resp.status_code == 200
    assert resp["Content-Disposition"] == 'attachment; filename="report.md"'
    assert _body(resp) == b"# Report"


def test_download_outside_the_confinement_root_is_refused(client, tmp_path):
    root = tmp_path / "artifacts-root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"nope")

    resp = _run(client.get(f"/api/file-raw/{_b64(str(root))}{outside}?download=1"))

    assert resp.status_code in (403, 404)


# ── Git downloads ────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture(scope="module")
def repo_template(tmp_path_factory):
    """A repo with two commits, then uncommitted work, built once per module.

    HEAD~1 adds ``kept.txt`` and ``gone.txt``; HEAD modifies ``kept.txt`` and
    deletes ``gone.txt``. The working tree then modifies ``kept.txt`` again and
    adds an untracked file.

    Building it takes a dozen ``git`` processes; each test gets its own copy
    (see :func:`repo`), so tests that commit or delete files never see each
    other's changes.
    """
    directory = tmp_path_factory.mktemp("repo-template") / "repo"
    directory.mkdir()
    _git(directory, "init", "-q", "-b", "main")
    _git(directory, "config", "user.email", "test@example.com")
    _git(directory, "config", "user.name", "Test")

    (directory / "kept.txt").write_text("v1\n")
    (directory / "gone.txt").write_text("doomed\n")
    _git(directory, "add", "-A")
    _git(directory, "commit", "-q", "-m", "first")
    first = _git(directory, "rev-parse", "HEAD")

    (directory / "kept.txt").write_text("v2\n")
    (directory / "gone.txt").unlink()
    _git(directory, "add", "-A")
    _git(directory, "commit", "-q", "-m", "second")
    second = _git(directory, "rev-parse", "HEAD")

    (directory / "kept.txt").write_text("v3\n")
    (directory / "fresh.txt").write_text("brand new\n")
    return {"dir": directory, "first": first, "second": second}


@pytest.fixture
def repo(tmp_path, transactional_db, repo_template):
    """A private copy of :func:`repo_template`, with its project and session."""
    directory = tmp_path / "repo"
    shutil.copytree(repo_template["dir"], directory, symlinks=True)
    first, second = repo_template["first"], repo_template["second"]

    project = Project.objects.create(
        id="-tmp-dl-repo", directory=str(directory), git_root=str(directory)
    )
    now = timezone.now()
    session = Session.objects.create(
        id="sess-dl-repo", project=project, provider="claude_code",
        file_path="sess-dl-repo.jsonl", type=SessionType.SESSION, title="sess-dl-repo",
        created_at=now, last_new_content_at=now, user_message_count=1,
        git_directory=str(directory),
    )
    return {"dir": directory, "project": project, "session": session,
            "first": first, "second": second}


def _download(client, repo, endpoint, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/projects/{repo['project'].id}/sessions/{repo['session'].id}/{endpoint}/?{query}"
    return _run(client.get(url))


def test_index_download_serves_the_working_tree(client, repo):
    resp = _download(client, repo, "git-file-download", path="kept.txt", ref="index")

    assert resp.status_code == 200
    assert resp["Content-Disposition"] == 'attachment; filename="kept.txt"'
    assert _body(resp) == b"v3\n"


def test_index_download_of_a_deleted_file_falls_back_to_head(client, repo):
    (repo["dir"] / "kept.txt").unlink()

    resp = _download(client, repo, "git-file-download", path="kept.txt", ref="index")

    assert resp.status_code == 200
    assert _body(resp) == b"v2\n"


def test_commit_download_serves_that_revision(client, repo):
    resp = _download(client, repo, "git-file-download", path="kept.txt", ref=repo["first"])

    assert resp.status_code == 200
    assert _body(resp) == b"v1\n"


def test_commit_download_of_a_deleted_file_falls_back_to_the_parent(client, repo):
    resp = _download(client, repo, "git-file-download", path="gone.txt", ref=repo["second"])

    assert resp.status_code == 200
    assert _body(resp) == b"doomed\n"


def test_download_of_an_unknown_path_is_404(client, repo):
    resp = _download(client, repo, "git-file-download", path="nope.txt", ref=repo["first"])

    assert resp.status_code == 404


def test_download_of_a_directory_is_404(client, repo):
    (repo["dir"] / "sub").mkdir()
    (repo["dir"] / "sub" / "a.txt").write_text("a\n")
    _git(repo["dir"], "add", "-A")
    _git(repo["dir"], "commit", "-q", "-m", "sub")
    head = _git(repo["dir"], "rev-parse", "HEAD")

    resp = _download(client, repo, "git-file-download", path="sub", ref=head)

    assert resp.status_code == 404


def test_download_outside_the_repo_is_refused(client, repo):
    resp = _download(client, repo, "git-file-download", path="../escape.txt", ref="index")

    assert resp.status_code in (400, 404)


def test_a_path_with_a_newline_is_refused(client, repo):
    resp = _download(client, repo, "git-file-download", path="kept.txt%0AHEAD:gone.txt", ref="index")

    assert resp.status_code == 400


def test_an_arbitrary_ref_is_refused(client, repo):
    resp = _download(client, repo, "git-file-download", path="kept.txt", ref="HEAD")

    assert resp.status_code == 400


def test_index_patch_covers_the_working_tree(client, repo):
    resp = _download(client, repo, "git-diff-download", path="kept.txt", ref="index")

    assert resp.status_code == 200
    assert resp["Content-Disposition"] == 'attachment; filename="kept.txt.patch"'
    patch = _body(resp).decode()
    assert "-v2" in patch
    assert "+v3" in patch


def test_index_patch_of_an_untracked_file_diffs_against_nothing(client, repo):
    resp = _download(client, repo, "git-diff-download", path="fresh.txt", ref="index")

    assert resp.status_code == 200
    patch = _body(resp).decode()
    assert "+brand new" in patch


def test_commit_patch_covers_that_commit(client, repo):
    resp = _download(client, repo, "git-diff-download", path="kept.txt", ref=repo["second"])

    assert resp.status_code == 200
    patch = _body(resp).decode()
    assert "-v1" in patch
    assert "+v2" in patch


def test_root_commit_patch_is_not_empty(client, repo):
    resp = _download(client, repo, "git-diff-download", path="kept.txt", ref=repo["first"])

    assert resp.status_code == 200
    assert "+v1" in _body(resp).decode()
