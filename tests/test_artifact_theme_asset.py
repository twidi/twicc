"""The artifact theme stylesheets are served from the built static dir, confined to it."""

import asyncio

import pytest
from django.http import Http404
from django.test import RequestFactory

from twicc import views
from twicc.artifacts.broker_html import ARTIFACT_THEME_URL, artifact_html_response
from twicc.share.html import share_artifact_doc_response


@pytest.fixture
def theme_dir(tmp_path, settings):
    settings.PACKAGE_DIR = tmp_path
    base = tmp_path / "static" / "artifact-theme"
    base.mkdir(parents=True)
    (base / "theme.css").write_text(":root{--twicc-surface:white}")
    (tmp_path / "static" / "secret.css").write_text("nope")
    return base


def _get(asset, method="get"):
    request = getattr(RequestFactory(), method)(f"/_twicc/artifact-theme/{asset}")
    return asyncio.run(views.artifact_theme_asset(request, asset))


def test_serves_a_built_stylesheet(theme_dir):
    response = _get("theme.css")
    assert response.status_code == 200
    assert b"--twicc-surface" in b"".join(response.streaming_content)


@pytest.mark.parametrize("asset", ["kit.css", "../secret.css"])
def test_missing_or_escaping_assets_are_404(theme_dir, asset):
    with pytest.raises(Http404):
        _get(asset)


def test_only_get_and_head(theme_dir):
    assert _get("theme.css", "post").status_code == 405


@pytest.mark.parametrize("respond", [artifact_html_response, share_artifact_doc_response])
def test_owner_and_shared_artifact_documents_link_the_theme(respond):
    response = respond(b"<html><head></head><body>x</body></html>")
    assert f'<link rel="stylesheet" href="{ARTIFACT_THEME_URL}">'.encode() in response.content
