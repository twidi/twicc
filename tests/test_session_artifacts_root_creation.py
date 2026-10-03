"""The session artifacts folder is created on the first write into it.

Covers :func:`twicc.views.ensure_session_artifacts_root` and its two callers
(standalone file create, standalone upload scope check).
"""

from __future__ import annotations

import asyncio

import orjson
import pytest
from django.test import AsyncClient

from twicc import paths
from twicc.views import ensure_session_artifacts_root


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


def test_creates_a_missing_session_artifacts_folder(artifacts_root):
    root = paths.get_session_artifacts_dir("s1")
    assert not root.exists()
    ensure_session_artifacts_root(str(root), str(root))
    assert root.is_dir()


def test_leaves_an_existing_folder_untouched(artifacts_root):
    root = paths.get_session_artifacts_dir("s1")
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("x")
    ensure_session_artifacts_root(str(root), str(root))
    assert (root / "keep.txt").read_text() == "x"


@pytest.mark.parametrize("relative", ["s1/sub", "s1/../other/x", "a/b/c"])
def test_ignores_anything_but_the_session_root_itself(artifacts_root, relative):
    root = paths.get_session_artifacts_dir("s1")
    target = artifacts_root / relative
    ensure_session_artifacts_root(str(target), str(root))
    assert not target.exists()


def test_ignores_a_root_outside_the_artifacts_folder(artifacts_root, tmp_path):
    other = tmp_path / "elsewhere"
    ensure_session_artifacts_root(str(other), str(other))
    assert not other.exists()


def test_ignores_a_path_that_is_not_the_given_root(artifacts_root):
    root = paths.get_session_artifacts_dir("s1")
    ensure_session_artifacts_root(str(paths.get_session_artifacts_dir("s2")), str(root))
    assert not paths.get_session_artifacts_dir("s2").exists()
    ensure_session_artifacts_root(str(root), "")
    assert not root.exists()


def test_standalone_create_makes_the_missing_root(artifacts_root, settings):
    settings.TWICC_PASSWORD_HASH = ""
    root = paths.get_session_artifacts_dir("s1")
    response = asyncio.run(
        AsyncClient().post(
            "/api/file-create/",
            data=orjson.dumps({"parent_dir": str(root), "root": str(root), "name": "note.md", "kind": "file"}),
            content_type="application/json",
        )
    )
    assert response.status_code == 200, response.content
    assert (root / "note.md").is_file()
