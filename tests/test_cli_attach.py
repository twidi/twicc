"""--attach of send-message, send-messages and create-session: resolve, stage, send refs.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.4. The transport
is faked (no backend); the commands, the session rows and the staging store are real.
"""

import base64
import os

import orjson
import pytest
from typer.testing import CliRunner

from twicc.cli import app
from twicc.cli._drop_request import attach_sources, bootstrap_local, transport, whoami
from twicc.cli._drop_request.polling import PollOutcome
from twicc.cli._output import _capture, _Sink
from twicc.core.models import Project, Session
from twicc.core.services.attachments import inline, staging
from twicc.core.services.attachments.staging import AttachmentError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


@pytest.fixture
def sessions(db, tmp_path):
    project = Project.objects.create(id="attach-project", directory=str(tmp_path))
    for sid, provider in (("alpha", "claude_code"), ("beta", "codex")):
        Session.objects.create(
            id=sid, project=project, provider=provider, file_path=f"{sid}.jsonl",
            created_at="2026-10-07T10:00:00Z", user_message_count=1,
        )
    return project


@pytest.fixture
def submitted(monkeypatch, data_dir, sessions):
    """The submitted ``(kind, payload)`` pairs; every request answers ``sent`` / ``created``."""
    calls: list = []

    class _Submission:
        def __init__(self, kind, payload):
            self.request_uuid = f"req-{len(calls)}"
            self.status = "created" if kind == "session:create" else "sent"
            self.session_id = payload.get("session_id", "new-session")

        def outcome(self):
            return PollOutcome(self.status, {
                "session_id": self.session_id, "provider": "claude_code", "project_id": "attach-project",
                "last_line": 3,
            }, True)

        def poll(self):
            return self.outcome()

        def was_received(self):
            return True

        def cleanup(self):
            pass

    def submit(payload, *, kind):
        calls.append((kind, payload))
        return _Submission(kind, payload)

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", submit)
    monkeypatch.setattr(transport, "wait", lambda sub, timeout_seconds: sub.outcome())
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    # The empty data dir has no settings.json: without ``disabledProviders``, create-session
    # refuses every provider with ``no_provider_configured`` (validation.py:52-57).
    monkeypatch.setattr(bootstrap_local, "read_synced_settings", lambda: {
        "disabledProviders": [], "defaultProvider": "claude_code",
    })
    return calls


def _invoke(*args):
    return CliRunner().invoke(app, list(args))


def _file(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _uri(data: bytes, header: str = "text/plain;base64") -> str:
    return f"data:{header},{base64.b64encode(data).decode()}"


def _staged(payload):
    return [staging.load_entry(staging.validate_ref(item)) for item in payload["attachments"]]


def _staging_entries():
    root = staging.get_composer_attachments_dir()
    if not root.exists():
        return []
    return [entry for bucket in root.iterdir() for entry in bucket.iterdir()]


# ── attach_sources ───────────────────────────────────────────────────────────


def test_resolution_errors_are_validation_errors(tmp_path, data_dir):
    missing = str(tmp_path / "missing.txt")
    sources, errors = attach_sources.resolve(
        [str(tmp_path), missing, "remote:/srv/x.bin", "data:text/plain;base64,aGk"],
        hint=inline.INLINE_TOO_LARGE_HINT,
    )
    assert sources == []
    assert [(e.field, e.code) for e in errors] == [
        (f"--attach {tmp_path}", "not_a_file"),
        (f"--attach {missing}", "not_a_file"),
        ("--attach remote:/srv/x.bin", "remote_requires_remote"),
        ("--attach data:text/plain", "invalid_data_uri"),
    ]


def test_a_relative_path_is_refused_over_the_api(data_dir):
    token = _capture.set(_Sink())
    try:
        _sources, errors = attach_sources.resolve(["notes.txt"], hint=inline.INLINE_TOO_LARGE_HINT)
    finally:
        _capture.reset(token)
    assert [e.code for e in errors] == ["relative_path"]


def test_a_data_uri_without_name_is_named_by_its_position(tmp_path, data_dir):
    path = _file(tmp_path, "a.txt", b"a")
    sources, errors = attach_sources.resolve(
        [str(path), _uri(PNG, "image/png;base64"), _uri(b"x", "text/plain;name=n%C3%A9.txt;base64")],
        hint=inline.INLINE_TOO_LARGE_HINT,
    )
    assert errors == []
    assert [(s.name, s.path, s.data) for s in sources] == [
        ("a.txt", str(path), None), ("attachment-2.png", None, PNG), ("né.txt", None, b"x"),
    ]


def test_only_inline_data_counts_toward_the_limit(tmp_path, data_dir, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    big_path = _file(tmp_path, "big.bin", b"x" * 20)
    sources, errors = attach_sources.resolve(
        [str(big_path), _uri(b"y" * 6), _uri(b"z" * 6)], hint=inline.INLINE_TOO_LARGE_HINT,
    )
    assert [s.name for s in sources] == ["big.bin", "attachment-2.txt"]
    [error] = errors
    assert error.code == "attachments_too_large"
    assert error.message.endswith(inline.INLINE_TOO_LARGE_HINT)


def test_peer_counting_includes_the_paths(tmp_path, data_dir, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    path = _file(tmp_path, "a.bin", b"x" * 6)
    _sources, errors = attach_sources.resolve(
        [str(path), _uri(b"y" * 6)], hint=inline.PEER_TOO_LARGE_HINT, count_paths=True,
    )
    assert [e.code for e in errors] == ["attachments_too_large"]
    assert errors[0].message.endswith(inline.PEER_TOO_LARGE_HINT)


def test_stage_keeps_the_order_and_the_origin(tmp_path, data_dir):
    sources, _errors = attach_sources.resolve(
        [str(_file(tmp_path, "one.txt", b"1")), _uri(b"2", "text/plain;name=two.txt;base64")],
        hint=inline.INLINE_TOO_LARGE_HINT,
    )
    refs, errors = attach_sources.stage(sources, bucket=attach_sources.new_request_bucket())
    assert errors == []
    assert [staging.load_entry(ref).filename for ref in refs] == ["one.txt", "two.txt"]
    assert all(ref.bucket.startswith("cli-") for ref in refs)
    assert attach_sources.as_payload(refs) == [{"bucket": ref.bucket, "id": ref.id} for ref in refs]


def test_the_origin_is_api_inside_the_backend(monkeypatch):
    monkeypatch.setattr(transport, "_in_backend", lambda: True)
    assert attach_sources.staging_origin() == "api"
    assert attach_sources.new_request_bucket().startswith("api-")


# ── send-message ─────────────────────────────────────────────────────────────


def test_send_message_stages_any_file_and_sends_refs(tmp_path, submitted):
    files = [
        _file(tmp_path, "shot.png", PNG),
        _file(tmp_path, "report.pdf", b"%PDF-1.4 x"),
        _file(tmp_path, "notes.txt", b"hello"),
        _file(tmp_path, "clip.mp4", os.urandom(64)),
        _file(tmp_path, "empty.bin", b""),
    ]
    result = _invoke("send-message", "alpha", "look", *[arg for f in files for arg in ("--attach", str(f))])
    assert result.exit_code == 0, result.output
    [(kind, payload)] = submitted
    assert kind == "session:send_message"
    assert "images" not in payload and "documents" not in payload
    staged = _staged(payload)
    assert [e.filename for e in staged] == ["shot.png", "report.pdf", "notes.txt", "clip.mp4", "empty.bin"]
    for entry, source in zip(staged, files, strict=True):
        assert entry.path.read_bytes() == source.read_bytes()
        assert os.stat(entry.path).st_ino != os.stat(source).st_ino
    assert all(e.ref.bucket.startswith("cli-") for e in staged)


def test_a_local_file_above_the_inline_limit_is_accepted(tmp_path, submitted, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    result = _invoke("send-message", "alpha", "--attach", str(_file(tmp_path, "big.bin", b"x" * 20)))
    assert result.exit_code == 0, result.output
    assert _staged(submitted[0][1])[0].size == 20


def test_attachments_alone_need_no_prompt(tmp_path, submitted):
    result = _invoke("send-message", "alpha", "--attach", str(_file(tmp_path, "a.txt", b"a")))
    assert result.exit_code == 0, result.output
    assert submitted[0][1]["text"] == ""


def test_no_prompt_and_no_attach_is_missing_prompt(submitted):
    result = _invoke("send-message", "alpha")
    assert result.exit_code == 1
    assert orjson.loads(result.output)["errors"][0]["code"] == "missing_prompt"


def test_attach_errors_stage_nothing_and_submit_nothing(tmp_path, submitted):
    missing = str(tmp_path / "missing.txt")
    result = _invoke("send-message", "alpha", "hi", "--attach", str(_file(tmp_path, "a.txt", b"a")),
                     "--attach", missing)
    assert result.exit_code == 1
    output = orjson.loads(result.output)
    assert output["status"] == "validation_error"
    assert output["errors"] == [{
        "field": f"--attach {missing}", "code": "not_a_file",
        "message": f"file {missing!r} does not exist or is not a regular file",
    }]
    assert submitted == []
    assert _staging_entries() == []


def test_a_staging_failure_discards_the_earlier_entries(tmp_path, submitted, monkeypatch):
    real_stage_path = staging.stage_path
    calls = []

    def failing(source, **kwargs):
        calls.append(source)
        if len(calls) == 2:
            raise AttachmentError("attachment_stage_failed", "Cannot stage 'b.txt': No space left on device")
        return real_stage_path(source, **kwargs)

    monkeypatch.setattr(staging, "stage_path", failing)
    result = _invoke("send-message", "alpha", "hi",
                     "--attach", str(_file(tmp_path, "a.txt", b"a")), "--attach", str(_file(tmp_path, "b.txt", b"b")))
    assert result.exit_code == 1
    assert orjson.loads(result.output)["errors"][0]["code"] == "attachment_stage_failed"
    assert submitted == []
    assert _staging_entries() == []


def test_an_interruption_while_staging_discards_and_submits_nothing(tmp_path, submitted, monkeypatch):
    real_stage_path = staging.stage_path
    calls = []

    def interrupted(source, **kwargs):
        calls.append(source)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_stage_path(source, **kwargs)

    monkeypatch.setattr(staging, "stage_path", interrupted)
    files = [_file(tmp_path, f"{n}.txt", b"x") for n in "abc"]
    result = _invoke("send-message", "alpha", "hi", *[arg for f in files for arg in ("--attach", str(f))])
    assert result.exit_code != 0
    assert submitted == []
    assert _staging_entries() == []


def test_a_data_uri_name_never_escapes_the_entry(submitted):
    result = _invoke(
        "send-message", "alpha", "hi",
        "--attach", _uri(b"1", "text/plain;name=..%2F..%2Fescape.txt;base64"),
        "--attach", _uri(b"2", "text/plain;name=.twicc-upload-x;base64"),
    )
    assert result.exit_code == 0, result.output
    staged = _staged(submitted[0][1])
    assert [e.filename for e in staged] == [".._.._escape.txt", "_.twicc-upload-x"]
    for entry in staged:
        assert entry.path.parent == staging.entry_dir(entry.ref) / "file"


# ── send-messages ────────────────────────────────────────────────────────────


def test_a_bad_attach_is_one_global_validation_error(tmp_path, submitted):
    result = _invoke("send-messages", "alpha", "beta", "--message", "hi", "--attach", str(tmp_path / "nope"))
    assert result.exit_code == 1
    output = orjson.loads(result.output)
    assert output["status"] == "validation_error"
    assert output["errors"][0]["code"] == "not_a_file"
    assert submitted == []


def test_each_recipient_gets_its_own_copy_whatever_its_provider(tmp_path, submitted):
    source = _file(tmp_path, "clip.mp4", os.urandom(32))
    result = _invoke("send-messages", "alpha", "beta", "--message", "hi", "--attach", str(source))
    assert result.exit_code == 0, result.output
    assert [payload["session_id"] for _kind, payload in submitted] == ["alpha", "beta"]
    [alpha], [beta] = (_staged(payload) for _kind, payload in submitted)
    assert alpha.ref != beta.ref
    assert os.stat(alpha.path).st_ino != os.stat(beta.path).st_ino
    for _kind, payload in submitted:
        assert "images" not in payload and "documents" not in payload


def test_the_no_file_branch_writes_no_legacy_keys(submitted):
    result = _invoke("send-messages", "alpha", "--message", "hi")
    assert result.exit_code == 0, result.output
    assert set(submitted[0][1]) == {"session_id", "_send_origin", "_send_request_id", "text"}


def test_a_staging_failure_is_a_per_id_error_and_keeps_earlier_recipients(tmp_path, submitted, monkeypatch):
    real_stage_path = staging.stage_path
    calls = []

    def failing_for_beta(source, **kwargs):
        calls.append(source)
        if len(calls) == 2:
            raise AttachmentError("attachment_stage_failed", "Cannot stage 'a.txt': Permission denied")
        return real_stage_path(source, **kwargs)

    monkeypatch.setattr(staging, "stage_path", failing_for_beta)
    result = _invoke("send-messages", "alpha", "beta", "--message", "hi",
                     "--attach", str(_file(tmp_path, "a.txt", b"a")))
    output = orjson.loads(result.output)
    assert output["results"]["alpha"]["status"] == "sent"
    assert output["results"]["beta"]["status"] == "validation_error"
    assert output["results"]["beta"]["errors"][0]["code"] == "attachment_stage_failed"
    assert [payload["session_id"] for _kind, payload in submitted] == ["alpha"]
    assert len(_staged(submitted[0][1])) == 1  # alpha's copy is kept for its send


# ── create-session ───────────────────────────────────────────────────────────


def test_create_session_stages_refs(tmp_path, submitted):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = _invoke("create-session", "--project", str(project_dir), "--provider", "claude_code",
                     "--attach", str(_file(tmp_path, "notes.txt", b"n")), "hello")
    assert result.exit_code == 0, result.output
    [(kind, payload)] = submitted
    assert kind == "session:create"
    assert "images" not in payload and "documents" not in payload
    assert [e.filename for e in _staged(payload)] == ["notes.txt"]


def test_create_session_hidden_constraint_stays_a_local_error_and_stages_nothing(tmp_path, submitted):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = _invoke("create-session", "--project", str(project_dir), "--provider", "claude_code",
                     "--hidden", "--question-widget", "--attach", str(_file(tmp_path, "a.txt", b"a")), "hello")
    assert result.exit_code == 1
    output = orjson.loads(result.output)
    assert output["status"] == "validation_error"
    # The hidden constraint itself, not an earlier provider error (``no_provider_configured``).
    # A non-interactive-mode error may come with it (the trust floor decides the mode).
    assert "hidden_incompatible_with_question_widget" in [e["code"] for e in output["errors"]]
    assert submitted == []
    assert _staging_entries() == []


# ── help ─────────────────────────────────────────────────────────────────────


def test_the_attach_help_states_the_limit_and_the_hint():
    from twicc.cli._drop_request.help_strings import ATTACH_EVERY_MESSAGE_HELP, ATTACH_HELP

    for text in (ATTACH_HELP, ATTACH_EVERY_MESSAGE_HELP):
        assert "any type" in text
        assert "name=" in text
        assert "50 MB" in text
        assert text.endswith(inline.INLINE_TOO_LARGE_HINT)
    assert "per recipient" in ATTACH_EVERY_MESSAGE_HELP
