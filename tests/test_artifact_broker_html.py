"""Phase 3 — the "serve an HTML artifact document" helper: inject the broker
shim as the first child of <head> and lock egress down with the CSP header
(design 2026-06-18 §7 + §8.3)."""

import orjson
import pytest

from twicc.artifacts.broker_html import (
    ARTIFACT_CSP,
    ARTIFACT_INNER_DOC_PATH,
    ARTIFACT_SHELL_CSS_URL,
    ARTIFACT_SHELL_JS_URL,
    ARTIFACT_THEME_URL,
    BROKER_SHIM_URL,
    artifact_html_response,
    artifact_shell_response,
    inject_broker_shim,
    is_artifact_document_request,
)

_TAG = f'<script src="{BROKER_SHIM_URL}"></script>'.encode()
_THEME = f'<link rel="stylesheet" href="{ARTIFACT_THEME_URL}">'.encode()


def test_inject_as_first_child_of_head():
    out = inject_broker_shim(b"<html><head><title>x</title></head><body>hi</body></html>")
    assert _TAG in out
    # First child of <head>: before any existing head content (the <title>).
    assert out.index(_TAG) < out.index(b"<title>")


def test_inject_adds_theme_link_right_after_the_shim():
    # The tokens load before the page's own styles, which can override them.
    out = inject_broker_shim(b"<html><head><style>:root{}</style></head></html>")
    assert out.index(_TAG) + len(_TAG) == out.index(_THEME)
    assert out.index(_THEME) < out.index(b"<style>")


def test_inject_handles_head_attributes_and_case():
    out = inject_broker_shim(b"<HTML><HEAD data-x='1'><meta charset='utf-8'></HEAD></HTML>")
    assert _TAG in out
    assert out.index(_TAG) < out.index(b"<meta")


def test_inject_falls_back_to_html_when_no_head():
    out = inject_broker_shim(b"<html><body>hi</body></html>")
    assert _TAG in out
    assert out.index(_TAG) < out.index(b"<body>")


def test_inject_prepends_when_no_head_or_html():
    out = inject_broker_shim(b"<div>fragment</div>")
    assert out.startswith(_TAG)


def test_csp_locks_down_egress():
    assert "default-src 'none'" in ARTIFACT_CSP
    assert "connect-src 'none'" in ARTIFACT_CSP   # no fetch/XHR/WS/EventSource/beacon
    assert "worker-src 'none'" in ARTIFACT_CSP    # no separately-policed worker scope
    assert "script-src 'self' 'unsafe-inline'" in ARTIFACT_CSP  # shim + the artifact itself


def test_artifact_html_response_injects_and_sets_csp():
    resp = artifact_html_response(b"<html><head></head><body></body></html>")
    assert resp["Content-Security-Policy"] == ARTIFACT_CSP
    assert resp["Content-Type"].startswith("text/html")
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert _TAG in resp.content


@pytest.mark.parametrize(
    "dest, expected",
    [
        ("iframe", True),     # the in-SPA preview loads the artifact in an <iframe>
        ("document", True),   # the dedicated page opens it as a top-level document
        ("script", False),    # a sub-resource — never wrap/CSP it
        ("style", False),
        ("image", False),
        ("empty", False),     # a fetch()/XHR
        (None, False),        # header absent (non-browser client) → treat as raw
    ],
)
def test_is_artifact_document_request(dest, expected):
    assert is_artifact_document_request(dest) is expected


# ── The dedicated-page trusted shell (phase 5, design §5/§9) ───────────────────


def _shell_data(resp):
    """Extract the JSON the shell injects for its frontend bundle to read."""
    body = resp.content.decode()
    start = body.index(">", body.index('id="twicc-shell-data"')) + 1
    end = body.index("</script>", start)
    return orjson.loads(body[start:end])


def test_artifact_shell_response_is_trusted_page_not_the_artifact():
    resp = artifact_shell_response(bookmark_id=7, allowed_hosts={})
    # The shell is TwiCC's own trusted page: it must NOT carry the artifact CSP
    # nor inject the artifact shim — the untrusted artifact stays locked in its
    # own iframe (served at the inner-doc route).
    assert not resp.has_header("Content-Security-Policy")
    assert BROKER_SHIM_URL.encode() not in resp.content
    assert resp["Content-Type"].startswith("text/html")
    assert resp["X-Content-Type-Options"] == "nosniff"


def test_artifact_shell_response_references_bundle_and_mount():
    resp = artifact_shell_response(bookmark_id=7, allowed_hosts={})
    body = resp.content.decode()
    assert ARTIFACT_SHELL_JS_URL in body   # loads the shared Vue broker bundle
    assert ARTIFACT_SHELL_CSS_URL in body
    assert 'id="app"' in body              # Vue mount point


def test_artifact_shell_response_embeds_inner_doc_and_allowlist():
    allowed = {"https://api.example.com:443": {"kind": "public"}}
    resp = artifact_shell_response(bookmark_id=7, allowed_hosts=allowed)
    data = _shell_data(resp)
    assert data["bookmarkId"] == 7
    assert data["innerDocUrl"] == f"/artifacts/7/{ARTIFACT_INNER_DOC_PATH}"
    assert data["allowedHosts"] == allowed


def test_artifact_shell_response_embeds_denied_hosts():
    denied = {"http://localhost:9000": {"kind": "loopback"}}
    resp = artifact_shell_response(bookmark_id=1, allowed_hosts={}, denied_hosts=denied)
    assert _shell_data(resp)["deniedHosts"] == denied


def test_artifact_shell_response_escapes_script_breakout():
    # A value containing "</script>" must not terminate the data <script> tag.
    resp = artifact_shell_response(
        bookmark_id=1, allowed_hosts={"https://x</script><b>:443": {"kind": "public"}}
    )
    assert b"</script><b>" not in resp.content        # raw breakout neutralized
    assert _shell_data(resp)["allowedHosts"]          # still valid JSON, round-trips
