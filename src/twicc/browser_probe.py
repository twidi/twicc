"""Advisory reachability / embeddability probe for the session Browser pane.

GET /api/browser-frame-check/?url=<http(s) URL>

The pane cannot observe a cross-origin iframe: a page that refuses framing
(X-Frame-Options / CSP frame-ancestors) and a dev server that is simply down
both render as a silent blank frame. This endpoint checks the URL server-side
and reports what the browser will not tell us. Advisory only — the iframe is
attempted regardless; a wrong verdict costs a dismissed banner, nothing else.

Reuses the artifact-broker primitives: DNS resolution + IP classification
(only the cloud metadata address is blocked — same invariant as the broker)
and IP pinning. Redirects are not followed (a redirect target would escape the
pin); a 3xx simply reports "reachable".
"""

import httpx
from django.http import JsonResponse

from twicc.core.ui_messages import ui_message_text
from twicc.artifacts.proxy import ResolutionError, resolve_target

PROBE_TIMEOUT_SECONDS = 10.0


def _probe_response(payload: dict, **kwargs):
    for field in ("error", "reason"):
        descriptor = getattr(payload.get(field), "ui_message", None)
        if descriptor is not None:
            payload["ui_message" if field == "error" else "reason_ui_message"] = descriptor
    return JsonResponse(payload, **kwargs)


def _frame_verdict(headers: httpx.Headers) -> tuple[bool, str | None]:
    """Best-effort: whether a cross-origin iframe would be allowed to render."""
    xfo = (headers.get("x-frame-options") or "").strip().lower()
    if xfo in ("deny", "sameorigin") or xfo.startswith("allow-from"):
        return False, f"X-Frame-Options: {xfo}"
    csp = headers.get("content-security-policy") or ""
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.lower().startswith("frame-ancestors"):
            sources = [s.lower() for s in directive.split()[1:]]
            # Anything but a wildcard almost certainly excludes this TwiCC
            # origin — report the directive as-is (heuristic, advisory only).
            if "*" not in sources:
                return False, f"CSP {directive}"
    return True, None


def _host_header(url: httpx.URL) -> str:
    if url.port is None:
        return url.host
    return f"{url.host}:{url.port}"


async def _probe(client: httpx.AsyncClient, method: str, url: httpx.URL, pinned_ip: str) -> httpx.Response:
    """One pinned request; headers only — the body is never read."""
    pinned = url.copy_with(host=pinned_ip)
    request = client.build_request(method, pinned)
    request.headers["host"] = _host_header(url)
    request.extensions["sni_hostname"] = url.host
    response = await client.send(request, follow_redirects=False, stream=True)
    await response.aclose()
    return response


async def browser_frame_check(request):
    """GET /api/browser-frame-check/ — see module docstring."""
    if request.method != "GET":
        return _probe_response({"error": ui_message_text("GET required", 'backend.browser_probe.get_required')}, status=405)
    raw_url = request.GET.get("url") or ""
    try:
        url = httpx.URL(raw_url)
    except Exception:
        return _probe_response({"error": ui_message_text("invalid url", 'backend.browser_probe.invalid_url')}, status=400)
    if url.scheme not in ("http", "https") or not url.host:
        return _probe_response({"error": ui_message_text("invalid url", 'backend.browser_probe.invalid_url')}, status=400)
    port = url.port or (443 if url.scheme == "https" else 80)

    try:
        target = await resolve_target(url.host, port)
    except ResolutionError:
        return _probe_response({"reachable": False, "reason": ui_message_text("hostname does not resolve", 'backend.browser_probe.unresolved_hostname')})
    except OSError:
        return _probe_response({"reachable": False, "reason": ui_message_text("DNS lookup failed", 'backend.browser_probe.dns_failed')})
    if target.kind == "metadata":
        return _probe_response({"error": ui_message_text("blocked target", 'backend.browser_probe.blocked_target')}, status=403)

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS)) as client:
        response = None
        for method in ("HEAD", "GET"):
            try:
                response = await _probe(client, method, url, target.ip)
            except httpx.HTTPError as exc:
                last_error = exc
                response = None
                continue
            if response.status_code != 405:  # some servers reject HEAD → retry as GET
                break
        if response is None:
            return _probe_response({"reachable": False, "reason": type(last_error).__name__})

    embeddable, reason = _frame_verdict(response.headers)
    return _probe_response(
        {"reachable": True, "status": response.status_code, "embeddable": embeddable, "reason": reason}
    )
