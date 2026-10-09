"""Serve an HTML *artifact document* with the network broker wired in.

Two jobs, applied only to the **top-level** artifact HTML document (never its
sub-assets), per design 2026-06-18 §7 (CSP) + §8.3 (shim injection):

1. Inject the broker shim ``<script>`` as the first child of ``<head>`` so it
   runs before any artifact script and transparently routes ``fetch``/XHR
   through the host (the shim is DX; the CSP below is the real boundary).
   The TwiCC theme stylesheet follows it: ``--twicc-*`` custom properties only,
   so it never changes how a page renders until the page uses them.
2. Set the strict Content-Security-Policy **response header** — page-immutable,
   covers WebSockets — that makes ``connect-src`` the iframe's egress lock.
"""

from __future__ import annotations

import re

import orjson
from django.http import HttpResponse
from django.utils.cache import add_never_cache_headers

# Stable same-origin URL the injected tag points at; the built shim bundle is
# served here (route + bundle: phase 3c).
BROKER_SHIM_URL = "/_twicc/artifact-broker-shim.js"

# The TwiCC design tokens (`--twicc-*`), injected right after the shim. Built by
# vite.config.artifact-theme.js; the opt-in kit.css sits next to it.
ARTIFACT_THEME_URL = "/_twicc/artifact-theme/theme.css"

# The dedicated artifact page (`/artifacts/<id>/`) serves a trusted *shell*
# (below) that iframes the artifact at this sentinel sub-path and serves its
# relative assets from the sibling paths. The name is deliberately reserved-
# looking so it can't collide with a real artifact asset (design §5).
ARTIFACT_INNER_DOC_PATH = "__twicc_doc__"

# Stable same-origin URLs for the built shell bundle (Vue + the shared broker
# composable/prompt). Served from static/artifact-shell/ like the shim; the dir
# is gitignored — produced by `npm run build`.
ARTIFACT_SHELL_JS_URL = "/_twicc/artifact-shell/shell.js"
ARTIFACT_SHELL_CSS_URL = "/_twicc/artifact-shell/shell.css"

_SHELL_DATA_ID = "twicc-shell-data"

# Egress lockdown (design §7). `connect-src 'none'` blocks every network call
# (fetch/XHR/WebSocket/EventSource/sendBeacon/<a ping>) — the iframe's only exit
# is postMessage to the host. `'unsafe-inline'` for script/style is intentional:
# the artifact *is* the untrusted script; CSP here controls egress, not the
# widget's own markup. Child contexts (about:blank/srcdoc/blob/data) inherit this,
# and `worker-src 'none'` forbids an un-patched worker network scope.
ARTIFACT_CSP = "; ".join(  # noqa: FLY002 — one directive per line beats a 300-char string
    [
        "default-src 'none'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "media-src 'self' blob:",
        "frame-src 'self'",
        "worker-src 'none'",
        "connect-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ]
)

_HEAD_RE = re.compile(rb"<head[^>]*>", re.IGNORECASE)
_HTML_RE = re.compile(rb"<html[^>]*>", re.IGNORECASE)

# Request `Sec-Fetch-Dest` values that mean "this byte stream is loaded AS a
# page/frame" — the only ones we wrap (shim + CSP). Sub-resources (`script`,
# `style`, `image`, `font`, `empty` for fetch/XHR) keep streaming raw, so the
# artifact's own relative assets are never double-injected.
_DOCUMENT_FETCH_DESTS = frozenset({"iframe", "document"})


def is_artifact_document_request(sec_fetch_dest: str | None) -> bool:
    """Whether a raw-file request is loading the top-level artifact document
    (vs. a sub-resource), per the browser's ``Sec-Fetch-Dest`` header. Absent
    (non-browser client) → ``False``: serve raw, never guess."""
    return sec_fetch_dest in _DOCUMENT_FETCH_DESTS


def inject_broker_shim(html: bytes) -> bytes:
    """Insert the shim ``<script>`` and the theme ``<link>`` as the first children of ``<head>``.

    Falls back to right after ``<html>`` (browsers synthesize a ``<head>``), or
    prepends to the document when neither tag is present. Byte-level so it never
    re-encodes the artifact's payload. A missed insertion is harmless — the CSP
    still blocks that artifact's direct egress (the shim is DX, not the boundary).
    """
    tag = f'<script src="{BROKER_SHIM_URL}"></script><link rel="stylesheet" href="{ARTIFACT_THEME_URL}">'.encode()
    match = _HEAD_RE.search(html) or _HTML_RE.search(html)
    if match:
        return html[: match.end()] + tag + html[match.end() :]
    return tag + html


def artifact_html_response(html: bytes) -> HttpResponse:
    """Build the response for a top-level artifact HTML document: shim injected,
    strict CSP set, and the same nosniff + never-cache headers the raw byte path
    uses (the preview rewrites these files in place — a cached doc would run
    stale)."""
    response = HttpResponse(inject_broker_shim(html), content_type="text/html; charset=utf-8")
    response["Content-Security-Policy"] = ARTIFACT_CSP
    response["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(response)
    response["CDN-Cache-Control"] = "no-store"
    return response


def _shell_html(bookmark_id: int, allowed_hosts: dict | None, denied_hosts: dict | None) -> bytes:
    """The trusted shell page's markup. It carries, for its Vue bundle to read,
    the inner-doc URL to iframe, the bookmark id, and the persisted allow/deny
    lists."""
    data = {
        "innerDocUrl": f"/artifacts/{bookmark_id}/{ARTIFACT_INNER_DOC_PATH}",
        "bookmarkId": bookmark_id,
        "allowedHosts": allowed_hosts or {},
        "deniedHosts": denied_hosts or {},
    }
    # Embed as JSON in a <script type="application/json">. Escaping '<' to its
    # JSON unicode form is enough to neutralize a "</script>" breakout while
    # staying valid JSON (the allowlist is normalized server-side, but escape
    # defensively all the same).
    payload = orjson.dumps(data).decode().replace("<", "\\u003c")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Artifact</title>\n"
        f'<link rel="stylesheet" href="{ARTIFACT_SHELL_CSS_URL}">\n'
        f'<script type="application/json" id="{_SHELL_DATA_ID}">{payload}</script>\n'
        f'<script type="module" src="{ARTIFACT_SHELL_JS_URL}"></script>\n'
        "</head>\n"
        '<body>\n<div id="app"></div>\n</body>\n'
        "</html>\n"
    ).encode()


def artifact_shell_response(
    *, bookmark_id: int, allowed_hosts: dict | None, denied_hosts: dict | None = None
) -> HttpResponse:
    """Build the trusted **shell** page for the dedicated artifact tab
    (``/artifacts/<id>/``). The shell is TwiCC's own code: it iframes the
    artifact's inner document (served, sandboxed + strict-CSP, at the
    :data:`ARTIFACT_INNER_DOC_PATH` route) and mounts the *same* broker host +
    consent prompt the in-SPA preview uses, so an artifact behaves identically in
    both contexts (design §5/§9).

    No artifact CSP here and no shim injection: this page runs trusted TwiCC code
    only; the untrusted artifact stays locked down in its own iframe."""
    response = HttpResponse(
        _shell_html(bookmark_id, allowed_hosts, denied_hosts), content_type="text/html; charset=utf-8"
    )
    response["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(response)
    response["CDN-Cache-Control"] = "no-store"
    return response
