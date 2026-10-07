"""Inline attachment data: data URIs, the 50 MB budget, legacy peer blocks.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.2.
"""

import base64
from types import SimpleNamespace

import pytest

from twicc.core.services.attachments import inline
from twicc.core.services.attachments.staging import AttachmentError

MIB = 1024 * 1024


def _uri(data: bytes, header: str = "text/plain;base64") -> str:
    return f"data:{header},{base64.b64encode(data).decode()}"


def test_constants_match_the_design():
    assert inline.INLINE_MAX_BYTES == 50 * MIB
    assert inline.INLINE_MAX_REQUEST_BYTES == 72 * MIB
    # base64 of 50 MB fits in the cap with room for JSON, names and text.
    assert 4 * -(-inline.INLINE_MAX_BYTES // 3) == 69_905_068 < inline.INLINE_MAX_REQUEST_BYTES
    assert inline.PEER_SEND_TIMEOUT_WITH_FILES == 468
    assert inline.PEER_SEND_TIMEOUT_WITH_FILES < 600


def test_transfer_timeout_grows_with_the_body():
    assert inline.transfer_timeout(0) == 30.0
    assert inline.transfer_timeout(256 * 1024) == 31.0
    assert inline.transfer_timeout(inline.INLINE_MAX_REQUEST_BYTES) == 318.0


def test_hints():
    assert "file storage service" in inline.INLINE_TOO_LARGE_HINT
    assert "remote:" in inline.INLINE_TOO_LARGE_HINT
    assert "absolute server path" in inline.INLINE_TOO_LARGE_HINT
    assert "file storage service" in inline.PEER_TOO_LARGE_HINT
    assert "remote:" not in inline.PEER_TOO_LARGE_HINT


def test_data_uri_without_name():
    assert inline.parse_data_uri(_uri(b"hello")) == inline.DataUri(None, "text/plain", 5, b"hello")


def test_data_uri_with_a_percent_encoded_name():
    uri = inline.parse_data_uri(_uri(b"x", "video/mp4;name=capture%20%281%29.mp4;base64"))
    assert (uri.name, uri.media_type, uri.data) == ("capture (1).mp4", "video/mp4", b"x")


@pytest.mark.parametrize("header", [
    "text/plain;charset=utf-8;name=a.txt;base64",
    "text/plain;base64;name=a.txt",
    "text/plain;name=a.txt;BASE64",
    " text/plain ; Name=a.txt ; Base64 ",
])
def test_data_uri_parameters_in_any_order_and_case(header):
    uri = inline.parse_data_uri(_uri(b"hi", header))
    assert (uri.media_type, uri.name, uri.data) == ("text/plain", "a.txt", b"hi")


def test_an_empty_name_parameter_is_no_name():
    assert inline.parse_data_uri(_uri(b"x", "text/plain;name=;base64")).name is None


def test_a_badly_encoded_name_is_refused():
    with pytest.raises(AttachmentError) as exc:
        inline.parse_data_uri(_uri(b"x", "text/plain;name=%FF.txt;base64"))
    assert exc.value.code == "invalid_data_uri"


@pytest.mark.parametrize("spec", [
    "data:text/plain,aGk=",          # no base64 parameter
    "data:text/plain;base64",        # no comma
    "data:;base64,aGk",              # length not a multiple of 4
    "data:;base64,a===",             # three padding characters
    "data:;base64,QUJD!!!!",         # not base64
])
def test_malformed_data_uris_are_refused(spec):
    with pytest.raises(AttachmentError) as exc:
        inline.parse_data_uri(spec)
    assert exc.value.code == "invalid_data_uri"


def test_an_empty_payload_is_a_zero_byte_file():
    assert inline.parse_data_uri("data:application/octet-stream;base64,") == inline.DataUri(
        None, "application/octet-stream", 0, b"",
    )


def test_whitespace_inside_the_base64_is_ignored():
    assert inline.parse_data_uri("data:text/plain;base64,aG\nVs bG8=").data == b"hello"


def test_a_missing_media_type_is_octet_stream():
    assert inline.parse_data_uri("data:;base64,aGk=").media_type == "application/octet-stream"


def test_the_label_is_never_the_uri():
    big = _uri(b"x" * 10_000, "image/png;name=shot%201.png;base64")
    assert inline.data_uri_label(big) == "data:image/png shot 1.png"
    assert inline.data_uri_label(_uri(b"x", "image/png;base64")) == "data:image/png"
    assert inline.data_uri_label("data:" + "y" * 100) == "data:" + "y" * 35 + "…"
    assert inline.data_uri_label("data:" + "m" * 1000 + ";base64,AAAA") == "data:" + "m" * 255


def test_the_label_uses_the_normalized_name():
    assert inline.data_uri_label("data:text/plain;name=..%2Fetc%2Fpasswd;base64,") == "data:text/plain .._etc_passwd"
    assert inline.data_uri_label("data:text/plain;name=%FF;base64,") == "data:text/plain"


def test_data_uri_size_does_not_decode():
    assert inline.data_uri_size(_uri(b"x" * 10)) == 10
    assert inline.data_uri_size("data:text/plain;base64") is None
    assert inline.data_uri_size("data:;base64,abc") is None


@pytest.mark.parametrize(("media_type", "n", "expected"), [
    ("text/plain", 1, "attachment-1.txt"),
    ("image/png", 3, "attachment-3.png"),
    ("TEXT/PLAIN; charset=utf-8", 2, "attachment-2.txt"),
    ("application/x-no-such-type", 2, "attachment-2.bin"),
    ("", 1, "attachment-1.bin"),
])
def test_default_name(media_type, n, expected):
    assert inline.default_name(media_type, n) == expected


def test_budget_accepts_exactly_the_limit_and_refuses_one_more_byte():
    budget = inline.InlineBudget(inline.INLINE_TOO_LARGE_HINT)
    budget.add(inline.INLINE_MAX_BYTES)
    assert budget.total == inline.INLINE_MAX_BYTES
    with pytest.raises(AttachmentError) as exc:
        budget.add(1)
    assert exc.value.code == "attachments_too_large"
    assert "the limit is 50 MB" in str(exc.value)
    assert str(exc.value).endswith(inline.INLINE_TOO_LARGE_HINT)


def test_the_peer_budget_carries_the_peer_hint():
    budget = inline.InlineBudget(inline.PEER_TOO_LARGE_HINT, limit=3)
    with pytest.raises(AttachmentError) as exc:
        budget.add(4)
    assert str(exc.value).endswith(inline.PEER_TOO_LARGE_HINT)


def test_the_size_is_refused_before_decoding(monkeypatch):
    decoded = []
    monkeypatch.setattr(inline, "base64", SimpleNamespace(b64decode=lambda *args, **kwargs: decoded.append(args)))
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    budget = inline.InlineBudget(inline.INLINE_TOO_LARGE_HINT)
    with pytest.raises(AttachmentError) as exc:
        inline.parse_data_uri("data:text/plain;base64,aGVsbG8=", budget=budget)
    assert exc.value.code == "attachments_too_large"
    assert decoded == []


def test_legacy_blocks_become_ordered_wire_entries():
    png = base64.b64encode(b"\x89PNG").decode()
    entries = inline.entries_from_legacy_blocks(
        [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}}],
        [
            {"type": "document", "title": "notes.md",
             "source": {"type": "text", "media_type": "text/plain", "data": "hé"}},
            {"type": "document", "name": "clip.mp4",
             "source": {"type": "base64", "media_type": "video/mp4", "data": "AAAA"}},
            {"type": "document", "source": {"type": "base64", "media_type": "application/x-odd", "data": "AAAA"}},
        ],
    )
    assert entries == [
        {"name": "attachment-1.png", "media_type": "image/png", "data": png},
        {"name": "notes.md", "media_type": "text/plain", "data": base64.b64encode("hé".encode()).decode()},
        {"name": "clip.mp4", "media_type": "video/mp4", "data": "AAAA"},
        {"name": "attachment-4.bin", "media_type": "application/x-odd", "data": "AAAA"},
    ]


def test_a_text_block_without_media_type_is_text_plain():
    [entry] = inline.entries_from_legacy_blocks(None, [{"source": {"type": "text", "data": "x"}}])
    assert entry["media_type"] == "text/plain"
    assert entry["name"] == "attachment-1.txt"


@pytest.mark.parametrize(("value", "expected"), [
    ("image/png", "image/png"),
    ("text/x-diff", "text/x-diff"),
    ("application/vnd.api+json", "application/vnd.api+json"),
    ("text/plain; charset=utf-8", "application/octet-stream"),
    ("not a type", "application/octet-stream"),
    ("a/" + "b" * 300, "application/octet-stream"),
    (None, "application/octet-stream"),
    (42, "application/octet-stream"),
])
def test_sanitize_media_type(value, expected):
    assert inline.sanitize_media_type(value) == expected
