"""The --remote forwarder: named data URIs, inline size checks, timeouts (phase 2 design §4.4.3)."""

import base64
import os

import httpx
import orjson
import pytest

from twicc.cli import _remote
from twicc.core.services.attachments import inline

URL = "http://box:3501"


def _inline(argv):
    return _remote.inline_attachments(list(argv), _remote.resolve_command(list(argv)))


@pytest.fixture
def posted(monkeypatch):
    """Every HTTP call of the forwarder: ``("timeout", httpx.Timeout)`` then the decoded body."""
    calls: list = []

    class FakeClient:
        def __init__(self, *, timeout):
            calls.append(("timeout", timeout))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, *, content, headers):
            calls.append(orjson.loads(content))
            return httpx.Response(200, json={"exit_code": 0, "result": {"ok": True}, "error": None})

    monkeypatch.setattr(_remote.httpx, "Client", FakeClient)
    return calls


def test_a_local_file_becomes_a_named_data_uri(tmp_path):
    path = tmp_path / "capture (1).mp4"
    path.write_bytes(b"\x00\x01")
    out = _inline(["send-message", "sid", "hi", "--attach", str(path)])
    assert out[-1] == "data:video/mp4;name=capture%20%281%29.mp4;base64,AAE="


def test_an_undecodable_local_name_is_sent_as_valid_utf8(tmp_path):
    path = tmp_path / os.fsdecode(b"bad\xff.bin")
    path.write_bytes(b"x")
    out = _inline(["send-message", "sid", "hi", "--attach", str(path)])
    assert out[-1] == "data:application/octet-stream;name=bad%EF%BF%BD.bin;base64,eA=="


def test_the_local_name_repair_ignores_the_locale(tmp_path, monkeypatch):
    path = tmp_path / "café.txt"
    path.write_bytes(b"x")
    monkeypatch.setattr(os, "fsencode", lambda name: name.encode("latin-1", "surrogateescape"))
    out = _inline(["send-message", "sid", "hi", "--attach", str(path)])
    assert out[-1] == "data:text/plain;name=caf%C3%A9.txt;base64,eA=="


def test_an_upper_case_data_uri_is_forwarded_as_is_and_counted(monkeypatch, posted):
    uri = "DATA:text/plain;base64," + base64.b64encode(b"y" * 6).decode()
    assert _inline(["send-message", "sid", "hi", "--attach", uri])[-1] == uri
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    with pytest.raises(_remote.RemoteUsageError) as exc:
        _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", uri])
    assert "the limit is" in str(exc.value)
    assert posted == []


def test_a_huge_value_read_as_a_path_never_echoes_the_value(posted):
    payload = "A" * 100_000
    for value in (f" data:text/plain;base64,{payload}", f"remote:{payload}"):
        with pytest.raises(_remote.RemoteUsageError) as exc:
            _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", value])
        assert payload not in str(exc.value) and len(str(exc.value)) < 600
    assert posted == []


def test_data_and_remote_values_keep_their_meaning():
    uri = "data:text/plain;base64,aGk="
    out = _inline(["send-message", "sid", "hi", "--attach", uri, "--attach=remote:/srv/x.bin"])
    assert out[4] == uri
    assert out[5] == "--attach=/srv/x.bin"


def test_the_local_total_above_the_limit_is_refused_before_any_http(tmp_path, monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 6)
    uri = "data:text/plain;base64," + base64.b64encode(b"y" * 6).decode()
    with pytest.raises(_remote.RemoteUsageError) as exc:
        _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", str(path), "--attach", uri])
    assert exc.value.exit_code == 2
    assert inline.INLINE_TOO_LARGE_HINT in str(exc.value)
    assert posted == []


def test_the_peer_send_hint_names_only_a_url(tmp_path, monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 6)
    with pytest.raises(_remote.RemoteUsageError) as exc:
        _remote.forward(URL, "tok", ["peer-send", "alice", "T", "text", "--attach", str(path)])
    assert str(exc.value).endswith(inline.PEER_TOO_LARGE_HINT)
    assert "remote:" not in str(exc.value)
    assert posted == []


def test_remote_values_do_not_count(monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    assert _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", "remote:/srv/huge.bin"]) == 0
    assert posted[1]["argv"][-1] == "/srv/huge.bin"


def test_a_missing_local_attach_is_still_a_usage_error(tmp_path, posted):
    for value in (str(tmp_path / "missing.bin"), str(tmp_path)):
        with pytest.raises(_remote.RemoteUsageError) as exc:
            _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", value])
        assert "attachment not found" in str(exc.value)
    assert posted == []


def test_a_body_above_the_request_cap_is_refused_before_the_post(monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_REQUEST_BYTES", 200)
    with pytest.raises(_remote.RemoteUsageError) as exc:
        _remote.forward(URL, "tok", ["send-message", "sid", "x" * 500])
    assert exc.value.exit_code == 2
    assert "once encoded" in str(exc.value)
    assert posted == []


def test_the_read_timeout_is_the_effective_timeout_plus_the_margin():
    resolved = _remote.resolve_command(["send-message", "sid", "hi", "--timeout", "90"])
    assert _remote._request_timeout(resolved).read == 90 + _remote._WAIT_TIMEOUT_MARGIN
    resolved = _remote.resolve_command(["send-message", "sid", "hi"])
    assert _remote._request_timeout(resolved).read == 30 + _remote._WAIT_TIMEOUT_MARGIN


def test_a_read_command_keeps_the_default_timeout():
    resolved = _remote.resolve_command(["sessions"])
    assert _remote._request_timeout(resolved).read == _remote._DEFAULT_TIMEOUT


def test_peer_send_with_files_gets_the_long_timeout(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"a")
    argv = ["peer-send", "alice", "T", "text", "--attach", str(path)]
    argv2, resolved = _remote.apply_peer_send_timeout(argv, _remote.resolve_command(argv))
    assert argv2[:2] == ["peer-send", f"--timeout={inline.PEER_SEND_TIMEOUT_WITH_FILES}"]
    assert _remote._request_timeout(resolved).read == inline.PEER_SEND_TIMEOUT_WITH_FILES + _remote._WAIT_TIMEOUT_MARGIN


@pytest.mark.parametrize("argv", [
    ["peer-send", "alice", "T", "text", "--attach", "remote:/srv/a.bin", "--timeout", "40"],
    ["peer-send", "alice", "T", "text"],
])
def test_an_explicit_timeout_or_no_file_keeps_the_peer_send_argv(argv):
    resolved = _remote.resolve_command(argv)
    argv2, resolved2 = _remote.apply_peer_send_timeout(argv, resolved)
    assert argv2 == argv
    assert resolved2 == resolved


def test_the_forwarder_no_longer_imports_the_legacy_module():
    assert not hasattr(_remote, "_sniff_mime")
