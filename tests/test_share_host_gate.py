"""The common public-origin gate (peer-origin-routing design §9-§11): /share/
is served ONLY on the Share hostname, /peer/ ONLY on the Peer authority, and a
dedicated Peer authority serves nothing else."""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import orjson
import pytest

from twicc.origin_gate import PublicOriginGate, ShareOnlyApp


def _run(coro):
    return asyncio.run(coro)


class Recorder:
    """A stub inner app that answers 200 and records that it was reached."""
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"full-app"})


async def _drive(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(m):
        sent.append(m)

    await app(scope, receive, send)
    return sent


def _http(path, host):
    return {"type": "http", "path": path, "headers": [(b"host", host.encode("latin1"))]}


def _ws(path, host):
    return {"type": "websocket", "path": path, "headers": [(b"host", host.encode("latin1"))]}


def _status(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


def _assert_plain_404(sent, context=None):
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1, context
    assert starts[0]["status"] == 404, context
    assert (b"content-type", b"text/plain") in starts[0]["headers"], context
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert body == b"Not found", context


def _ws_close_code(sent):
    for m in sent:
        if m["type"] == "websocket.close":
            return m.get("code")
    return None


def _location(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            for name, value in m["headers"]:
                if name == b"location":
                    return value.decode("latin1")
    return None


@pytest.fixture
def set_origins(monkeypatch):
    def _set(public="", share="", peer=""):
        monkeypatch.setattr(
            "twicc.synced_settings.read_routing_settings",
            lambda: SimpleNamespace(
                settings={"publicBaseUrl": public, "shareBaseUrl": share, "peerBaseUrl": peer},
                available=True,
            ),
        )
    return _set


def _gate():
    full = Recorder()
    return PublicOriginGate(full, ShareOnlyApp(full)), full


def test_real_asgi_application_has_the_gate_above_blacknoise():
    from blacknoise import BlackNoise
    from twicc.asgi import application

    assert isinstance(application, PublicOriginGate)
    assert isinstance(application.full_app, BlackNoise)
    assert isinstance(application.share_only_app, ShareOnlyApp)
    assert application.share_only_app.inner is application.full_app


# ── Share host unset → sharing disabled everywhere ──────────────────────────

def test_unset_host_404s_share_everywhere(set_origins):
    set_origins()
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "app.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_unset_host_serves_working_app(set_origins):
    set_origins()
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


@pytest.mark.parametrize("content", [None, b"{}"])
def test_missing_or_empty_settings_use_the_valid_default_policy(content, tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    if content is not None:
        path.write_bytes(content)
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
        assert _status(sent) == 200
        assert full.called
        assert ss.read_routing_settings().available is True
    finally:
        ss._cache.clear()


def test_cached_valid_settings_ignore_later_manual_edits_until_restart(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(b'{"peerBaseUrl":"https://peer.example"}')
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        initial = ss.read_routing_settings()
        assert initial.available is True
        assert initial.settings["peerBaseUrl"] == "https://peer.example"

        path.write_bytes(b"{")
        unchanged = ss.read_routing_settings()
        assert unchanged == initial

        # Clearing the cache simulates process initialization after restart.
        ss._cache.clear()
        assert ss.read_routing_settings().available is False
    finally:
        ss._cache.clear()


def test_manual_repair_requires_cache_reinitialization(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(b"{")
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_routing_settings().available is False
        path.write_bytes(b'{"peerBaseUrl":"https://peer.example"}')
        assert ss.read_routing_settings().available is False

        # A restart creates a new cache and observes the repaired file.
        ss._cache.clear()
        repaired = ss.read_routing_settings()
        assert repaired.available is True
        assert repaired.settings["peerBaseUrl"] == "https://peer.example"
    finally:
        ss._cache.clear()


def test_general_and_routing_first_reads_share_one_source_observation(monkeypatch):
    import twicc.synced_settings as ss

    class CoordinatedPath:
        def __init__(self):
            self.calls = 0
            self._count_lock = threading.Lock()
            self._second_entered = threading.Event()

        def read_bytes(self):
            with self._count_lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                # An unlocked second read enters before this returns. A locked
                # second read waits and then sees the populated cache. The lock
                # makes this window a fixed cost, so keep it short.
                self._second_entered.wait(timeout=0.05)
                return b"{"
            self._second_entered.set()
            return b'{"peerBaseUrl":"https://peer.example"}'

    path = CoordinatedPath()
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    ready = threading.Barrier(3)

    def read_general():
        ready.wait()
        return ss.read_synced_settings()

    def read_routing():
        ready.wait()
        return ss.read_routing_settings()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            general_future = pool.submit(read_general)
            routing_future = pool.submit(read_routing)
            ready.wait()
            general = general_future.result(timeout=2)
            snapshot = routing_future.result(timeout=2)
        assert path.calls == 1
        assert general == snapshot.settings
        assert snapshot.available is False
    finally:
        ss._cache.clear()


@pytest.mark.parametrize("failure", ["malformed", "non_object", "unreadable"])
def test_unavailable_routing_settings_never_reach_either_delegate(failure, tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    if failure == "malformed":
        path.write_bytes(b"{")
    elif failure == "non_object":
        path.write_bytes(b"[]")
    else:
        class UnreadableSettingsPath:
            def read_bytes(self):
                raise PermissionError("unreadable settings")

        path = UnreadableSettingsPath()
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        # The unavailable state survives when a non-routing caller initializes
        # the shared cache before the gate.
        ss.read_synced_settings()
        assert ss.read_routing_settings().available is False
        for scope in (
            _http("/api/sessions/", "former-peer.example"),
            _http("/share/tok/", "former-share.example"),
            _ws("/ws/share/tok/", "former-share.example"),
        ):
            full = Recorder()
            share = Recorder()
            gate = PublicOriginGate(full, share)
            sent = _run(_drive(gate, scope))
            if scope["type"] == "http":
                _assert_plain_404(sent)
            else:
                assert _ws_close_code(sent) == 4404
            assert not full.called
            assert not share.called
    finally:
        ss._cache.clear()


@pytest.mark.parametrize("failure", ["read", "build"])
def test_routing_read_or_policy_build_exception_never_reaches_either_delegate(failure, monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_routing_settings",
        lambda: SimpleNamespace(settings={}, available=True),
    )
    if failure == "read":
        def fail_read():
            raise OSError("read failed")

        monkeypatch.setattr("twicc.synced_settings.read_routing_settings", fail_read)
    else:
        def fail_build(_settings):
            raise ValueError("build failed")

        monkeypatch.setattr("twicc.origin_gate.get_origin_policy", fail_build)
    for scope in (
        _http("/api/sessions/", "app.example"),
        _http("/share/tok/", "share.example"),
        _ws("/ws/share/tok/", "share.example"),
    ):
        full = Recorder()
        share = Recorder()
        gate = PublicOriginGate(full, share)
        sent = _run(_drive(gate, scope))
        if scope["type"] == "http":
            _assert_plain_404(sent)
        else:
            assert _ws_close_code(sent) == 4404
        assert not full.called
        assert not share.called


def test_settings_cli_envelope_restores_routing_after_invalid_load(tmp_path, monkeypatch):
    import twicc.synced_settings as ss
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.drop_requests_watcher import execute_drop_payload

    path = tmp_path / "settings.json"
    path.write_bytes(b"{")
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    drop_dir = tmp_path / "drop-requests"
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir",
        lambda: drop_dir,
    )
    ss._cache.clear()
    try:
        assert ss.read_routing_settings().available is False
        for scope in (
            _http("/peer/messages/", "peer.example"),
            _ws("/ws/", "peer.example"),
        ):
            full = Recorder()
            share = Recorder()
            gate = PublicOriginGate(full, share)
            sent = _run(_drive(gate, scope))
            if scope["type"] == "http":
                _assert_plain_404(sent)
            else:
                assert _ws_close_code(sent) == 4404
            assert not full.called
            assert not share.called

        dropped = write_drop_file(
            {
                "patch": {"peerBaseUrl": "https://peer.example"},
                "broadcast": False,
            },
            kind="settings:update",
        )
        assert dropped.path.exists()
        envelope = orjson.loads(dropped.path.read_bytes())
        assert envelope["payload"]["kind"] == "settings:update"
        result = _run(execute_drop_payload(
            envelope["payload"],
            envelope["payload"]["kind"],
        ))
        assert result["status"] == "updated"
        assert ss.read_routing_settings().available is True

        full = Recorder()
        share = Recorder()
        gate = PublicOriginGate(full, share)
        sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
        assert _status(sent) == 200
        assert full.called
        assert not share.called
    finally:
        ss._cache.clear()


def test_invalid_share_setting_disables_sharing_and_quarantines(set_origins):
    set_origins(share="ftp://share.example.com")
    gate, full = _gate()
    # The recognizable hostname is quarantined: nothing is served there.
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called
    sent = _run(_drive(gate, _http("/api/sessions/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called


# ── On the share host → only the share surface ──────────────────────────────

def test_share_host_serves_share(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    assert _status(sent) == 200
    assert full.called


def test_share_host_404s_non_share_paths(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_share_host_404s_peer_paths(set_origins):
    set_origins(
        public="https://app.example",
        share="https://share.example.com",
        peer="https://peer.example:8443",
    )
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_share_host_allows_shared_assets(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/_twicc/artifact-shell/shell.js", "share.example.com")))
    assert _status(sent) == 200
    sent = _run(_drive(gate, _http("/_twicc/artifact-theme/theme.css", "share.example.com")))
    assert _status(sent) == 200


def test_share_host_favicon_204(set_origins):
    set_origins(share="share.example.com")
    gate, _full = _gate()
    sent = _run(_drive(gate, _http("/favicon.ico", "share.example.com")))
    assert _status(sent) == 204


def test_share_host_root_redirects_to_share_temporarily(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/", "share.example.com")))
    # 302 (temporary), NOT 301/308 — a real homepage could live here later.
    assert _status(sent) == 302
    assert _status(sent) not in (301, 308)
    assert _location(sent) == "/share/"
    assert not full.called


def test_share_hostname_matches_any_request_port(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    # Share routing compares only the hostname (§5.3).
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com:9999")))
    assert _status(sent) == 200
    assert full.called


def test_bracketed_ipv6_share_host_selects_share_boundary(set_origins):
    set_origins(share="https://[::1]:8443")
    gate, full = _gate()
    # An expanded request spelling canonicalizes to the same Share hostname (§13.3).
    sent = _run(_drive(gate, _http("/share/tok/", "[0:0:0:0:0:0:0:1]:8443")))
    assert _status(sent) == 200
    assert full.called
    sent = _run(_drive(gate, _http("/api/sessions/", "[::1]:8443")))
    _assert_plain_404(sent)


def test_working_origin_root_serves_app(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    # On the working origin, / is the SPA — never redirected to /share/.
    sent = _run(_drive(gate, _http("/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


# ── On the working origin → share surface invisible ─────────────────────────

def test_working_origin_404s_share(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "app.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_working_origin_serves_app(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


def test_full_url_share_base_extracts_hostname(set_origins):
    set_origins(share="https://share.example.com/")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    assert _status(sent) == 200
    assert full.called


# ── WebSocket ───────────────────────────────────────────────────────────────

def test_ws_share_closed_on_working_origin(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", "app.example.com")))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_ws_non_share_closed_on_share_host(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", "share.example.com")))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_ws_share_reaches_inner_on_share_host(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", "share.example.com")))
    assert _ws_close_code(sent) is None
    assert full.called


def test_ws_app_reaches_inner_on_working_origin(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    _run(_drive(gate, _ws("/ws/", "app.example.com")))
    assert full.called


def test_lifespan_passes_through(set_origins):
    set_origins()
    gate, full = _gate()
    _run(_drive(gate, {"type": "lifespan"}))
    assert full.called


# ── Peer routing table (§10) ────────────────────────────────────────────────

def test_dedicated_peer_serves_only_peer_http(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    for path, expected_status, expect_inner in [
        ("/peer/messages/", 200, True),
        ("/peer/handshake/request/", 200, True),
        ("/", 404, False),
        ("/api/sessions/", 404, False),
        ("/static/assets/app.js", 404, False),
        ("/mcp", 404, False),
        ("/rpc/sessions/", 404, False),
        ("/artifacts/abc/", 404, False),
        ("/share/tok/", 404, False),
        ("/favicon.ico", 404, False),
    ]:
        gate, full = _gate()
        sent = _run(_drive(gate, _http(path, "peer.example:8443")))
        if expected_status == 404:
            _assert_plain_404(sent, path)
        else:
            assert _status(sent) == expected_status, path
        assert full.called is expect_inner, path


def test_dedicated_peer_closes_every_websocket(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    for path in ("/ws/", "/ws/share/tok/", "/ws/terminal/1/"):
        gate, full = _gate()
        sent = _run(_drive(gate, _ws(path, "peer.example:8443")))
        assert _ws_close_code(sent) == 4404, path
        assert not full.called, path


def test_shared_peer_routing_table(set_origins):
    set_origins(public="https://x.example", peer="https://x.example")
    for path, expected_status, expect_inner in [
        ("/peer/messages/", 200, True),
        ("/api/sessions/", 200, True),
        ("/share/tok/", 404, False),
    ]:
        gate, full = _gate()
        sent = _run(_drive(gate, _http(path, "x.example")))
        if expected_status == 404:
            _assert_plain_404(sent, path)
        else:
            assert _status(sent) == expected_status, path
        assert full.called is expect_inner, path

    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", "x.example")))
    assert _ws_close_code(sent) is None
    assert full.called

    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", "x.example")))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_other_authorities_never_serve_peer(set_origins):
    set_origins(public="https://app.example", share="share.example.com", peer="https://peer.example:8443")
    for host in ("app.example", "localhost:3501", "192.168.1.42:3501", "tunnel.example"):
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/peer/messages/", host)))
        _assert_plain_404(sent, host)
        assert not full.called, host
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/api/sessions/", host)))
        assert _status(sent) == 200, host
        assert full.called, host


def test_empty_peer_hides_peer_everywhere(set_origins):
    set_origins(public="https://app.example", share="share.example.com")
    for host in ("app.example", "share.example.com", "localhost:3501"):
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/peer/messages/", host)))
        _assert_plain_404(sent, host)
        assert not full.called, host


def test_peer_hostname_without_port_is_not_the_peer_authority(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    gate, full = _gate()
    # Same hostname, no port: just another authority — app yes, /peer/ no.
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "peer.example")))
    assert _status(sent) == 200


def test_default_port_host_does_not_match_portless_peer_authority(set_origins):
    set_origins(peer="https://peer.example")
    gate, full = _gate()
    # `Host: peer.example:443` matches only a configured `peer.example:443` (§8).
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example:443")))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    assert _status(sent) == 200
    assert full.called


def test_ambiguous_scheme_only_difference_disables_peer(set_origins):
    set_origins(public="https://x.example", peer="http://x.example")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "x.example")))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    # The exact External authority keeps the app (§11 precedence).
    sent = _run(_drive(gate, _http("/api/sessions/", "x.example")))
    assert _status(sent) == 200


# ── Live setting changes (§12) ──────────────────────────────────────────────

def test_live_setting_change_routes_next_request(monkeypatch):
    state = {"publicBaseUrl": "https://app.example", "shareBaseUrl": "", "peerBaseUrl": ""}
    monkeypatch.setattr(
        "twicc.synced_settings.read_routing_settings",
        lambda: SimpleNamespace(settings=dict(state), available=True),
    )
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    _assert_plain_404(sent)
    state["peerBaseUrl"] = "https://peer.example"
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    assert _status(sent) == 200
    assert full.called


# ── Request-authority boundaries (§8, §13.2) ────────────────────────────────

def test_missing_host_rejects_whole_request(set_origins):
    set_origins(public="https://app.example")
    gate, full = _gate()
    sent = _run(_drive(gate, {"type": "http", "path": "/api/sessions/", "headers": []}))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    sent = _run(_drive(gate, {"type": "websocket", "path": "/ws/", "headers": []}))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_duplicate_host_rejects_whole_request(set_origins):
    set_origins(public="https://app.example")
    scope = {"type": "http", "path": "/api/sessions/",
             "headers": [(b"host", b"app.example"), (b"host", b"app.example")]}
    gate, full = _gate()
    sent = _run(_drive(gate, scope))
    _assert_plain_404(sent)
    assert not full.called
    ws_scope = {"type": "websocket", "path": "/ws/",
                "headers": [(b"host", b"app.example"), (b"host", b"app.example")]}
    gate, full = _gate()
    sent = _run(_drive(gate, ws_scope))
    assert _ws_close_code(sent) == 4404
    assert not full.called


@pytest.mark.parametrize("host", [
    "",
    "app example",
    "exämple.com",
    "%65xample.com",
    "xn--.example",
    "XN--.example",
    "xn--a-ecp.example",
    "xn--e28h.example",
    "a..example",
    "app.example.",
    "-a.example",
    "a-.example",
    "my_host.example",
    "999.1.2.3",
    "1.2.3",
    "192.168.001.1",
    "::1",
    "[::1",
    "[1.2.3.4]",
    "[fe80::1%eth0]",
    "[fe80::1%25eth0]",
    "app.example:",
    "app.example:bad",
    "app.example:70000",
    "exa\tmple.com",
    "app.example:8\t0",
    "app.example\n",
    f"app.example:{'9' * 5000}",
])
def test_malformed_host_rejects_whole_request(set_origins, host):
    set_origins(public="https://app.example")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", host)))
    _assert_plain_404(sent, host)
    assert not full.called, host
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", host)))
    assert _ws_close_code(sent) == 4404, host
    assert not full.called, host


def test_rejections_log_a_debug_diagnostic_without_changing_the_response(set_origins, caplog, monkeypatch):
    # The operator gets a reason in the log; the response never reveals one
    # (design §11). Debug level on purpose: one record per rejected request at
    # warning level would flood an internet-facing gate.
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    # The test settings disable existing loggers; re-enable this one to observe it.
    monkeypatch.setattr(logging.getLogger("twicc.origin_gate"), "disabled", False)
    caplog.set_level("DEBUG", logger="twicc.origin_gate")

    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "my_host.example")))
    _assert_plain_404(sent)
    assert not full.called
    assert any("my_host.example" in record.getMessage() for record in caplog.records)

    caplog.clear()
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "peer.example:8443")))
    _assert_plain_404(sent)
    assert not full.called
    assert any("peer.example:8443" in record.getMessage() for record in caplog.records)
    assert {record.levelname for record in caplog.records} == {"DEBUG"}

    caplog.clear()
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", "peer.example:8443")))
    assert _ws_close_code(sent) == 4404
    assert not full.called
    assert {record.levelname for record in caplog.records} == {"DEBUG"}


def test_uppercase_and_alabel_hosts_canonicalize(set_origins):
    set_origins(public="https://app.example", peer="https://xn--fa-hia.de")
    gate, full = _gate()
    # `Host: XN--FA-HIA.DE` is accepted as routing authority `xn--fa-hia.de`.
    sent = _run(_drive(gate, _http("/peer/messages/", "XN--FA-HIA.DE")))
    assert _status(sent) == 200
    assert full.called
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "APP.example")))
    assert _status(sent) == 200
    assert full.called


def test_dns_length_boundaries_in_host(set_origins):
    set_origins(public="https://app.example")
    label63 = "a" * 63
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", f"{label63}.example")))
    assert _status(sent) == 200
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", f"{'a' * 64}.example")))
    _assert_plain_404(sent)
    host253 = ".".join([label63] * 3 + ["a" * 61])
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", host253)))
    assert _status(sent) == 200
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", host253 + "a")))
    _assert_plain_404(sent)


def test_ipv6_host_spellings_canonicalize_to_one_authority(set_origins):
    set_origins(public="https://app.example", peer="https://[::1]:8443")
    for host in ("[::1]:8443", "[0:0:0:0:0:0:0:1]:8443"):
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/peer/messages/", host)))
        assert _status(sent) == 200, host
        assert full.called, host


def test_explicit_request_port_is_preserved(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example:8443")))
    assert _status(sent) == 200
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example:8444")))
    _assert_plain_404(sent)


# ── Runtime-invalid interaction basis (§11, §13.2) ──────────────────────────

def _assert_quarantined(gate_factory, host):
    """Every HTTP request → plain 404, every WebSocket → 4404, no inner call."""
    for path in ("/", "/api/sessions/", "/share/tok/", "/peer/messages/", "/static/assets/app.js"):
        gate, full = gate_factory()
        sent = _run(_drive(gate, _http(path, host)))
        _assert_plain_404(sent, (host, path))
        assert not full.called, (host, path)
    gate, full = gate_factory()
    sent = _run(_drive(gate, _ws("/ws/", host)))
    assert _ws_close_code(sent) == 4404, host
    assert not full.called, host


def _assert_serves_app(gate_factory, host):
    gate, full = gate_factory()
    sent = _run(_drive(gate, _http("/api/sessions/", host)))
    assert _status(sent) == 200, host
    assert full.called, host
    gate, full = gate_factory()
    _run(_drive(gate, _ws("/ws/", host)))
    assert full.called, host


def _assert_disabled_surfaces(gate_factory, host):
    """Share-exclusive and /peer/ paths answer their defined gate responses."""
    for path in ("/share/tok/", "/_twicc/share/x.js", "/peer/messages/"):
        gate, full = gate_factory()
        sent = _run(_drive(gate, _http(path, host)))
        _assert_plain_404(sent, (host, path))
        assert not full.called, (host, path)
    gate, full = gate_factory()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", host)))
    assert _ws_close_code(sent) == 4404, host
    assert not full.called, host


def test_interaction_case_1_valid_external_invalid_share_invalid_peer(set_origins):
    # Disabled: Share and Peer. Surviving quarantine: share.example and
    # peer.example. Exact External exception: app.example.
    set_origins(public="https://app.example", share="ftp://share.example",
                peer="https://peer.example/forbidden")
    _assert_quarantined(_gate, "share.example")
    _assert_quarantined(_gate, "peer.example")
    _assert_serves_app(_gate, "app.example")
    _assert_disabled_surfaces(_gate, "app.example")
    _assert_serves_app(_gate, "other.example")
    _assert_disabled_surfaces(_gate, "other.example")


def test_interaction_case_2_invalid_external_valid_share_conflicting_peer(set_origins):
    # The recognizable invalid Peer operand joins the Share-and-Peer conflict
    # and disables the otherwise valid Share. Disabled: Share and Peer
    # (including Peer classification). Surviving quarantine: share.example.
    # No External exception (External is invalid).
    set_origins(public="https://", share="https://share.example",
                peer="https://share.example/forbidden")
    _assert_quarantined(_gate, "share.example")
    _assert_serves_app(_gate, "other.example")
    _assert_disabled_surfaces(_gate, "other.example")


def test_interaction_case_3_peer_conflicts_with_external(set_origins):
    # Before precedence the quarantine candidates are share.example and
    # app.example; valid External precedence removes only app.example.
    set_origins(public="https://app.example", share="ftp://share.example",
                peer="https://app.example/forbidden")
    _assert_quarantined(_gate, "share.example")
    _assert_serves_app(_gate, "app.example")
    _assert_disabled_surfaces(_gate, "app.example")
    _assert_serves_app(_gate, "other.example")
    _assert_disabled_surfaces(_gate, "other.example")


def test_runtime_share_external_conflict_disables_share_but_keeps_external(set_origins):
    # A manual conflict disables Share. Valid External precedence keeps the
    # application on the exact External authority.
    set_origins(public="https://app.example", share="https://app.example")
    _assert_serves_app(_gate, "app.example")
    _assert_disabled_surfaces(_gate, "app.example")


def test_invalid_external_quarantines_valid_peer_authority(set_origins):
    # §11 last row: invalid non-empty External disables Peer classification and
    # quarantines the configured Peer authority when recognizable.
    set_origins(public="https://", peer="https://peer.example")
    _assert_quarantined(_gate, "peer.example")
    _assert_serves_app(_gate, "other.example")


def test_unrecognizable_invalid_settings_disable_without_quarantine(set_origins):
    # A setting too malformed to name an authority disables its surface but
    # cannot quarantine an unknown host; other authorities keep the app.
    set_origins(public="https://app.example", share="https://", peer="https://")
    _assert_serves_app(_gate, "app.example")
    _assert_serves_app(_gate, "tunnel.example")
    _assert_disabled_surfaces(_gate, "tunnel.example")


@pytest.mark.parametrize('path', [
    '/share/tok/api/inline-artifacts/',
    '/share/tok/inline-artifacts/root/widget/index.html',
    '/share/tok/inline-artifacts/root/widget/data/',
    '/share/tok/api/inline-artifacts/root/widget/retry/',
    '/share/tok/api/inline-artifacts/root/widget/proxy/',
])
def test_inline_routes_require_dedicated_share_host(set_origins, path):
    set_origins(public='https://app.example.com', share='https://share.example.com')
    gate, full = _gate()
    sent = _run(_drive(gate, _http(path, 'app.example.com')))
    _assert_plain_404(sent)
    assert not full.called
    sent = _run(_drive(gate, _http(path, 'share.example.com')))
    assert _status(sent) == 200 and full.called
