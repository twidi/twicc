"""Tests for the ``<twicc:insert-screenshot />`` substitution pipeline.

Covers the shared helpers in ``twicc.providers.compute_base``:

- :func:`is_base64_image` — Claude SDK / front-end shape detector.
- :func:`substitute_insert_screenshot_tags` — text rewrite + idempotent
  disk save + missing-image placeholder.
- :data:`INSERT_SCREENSHOT_TAG_RE` — accepts the documented attribute
  shapes (no offset, ``offset="N"``) and tolerates inner whitespace.

Plus the provider overrides of ``iter_tool_result_image_refs``, which
yield ``(tool_use_id, media_type, data)`` triples from each provider's
native layout: Claude Code's ``tool_result`` blocks
(``source = {type, media_type, data}``) and Codex's
``function_call_output`` / ``custom_tool_call_output`` segments
(``input_image`` data URLs).

These tests are pure unit tests — no DB access, no Django models — so
they run fast and cover the rewrite logic in isolation. End-to-end DB
behaviour (``iter_images_backward``, ``image_candidate_queryset``,
watcher integration) is covered by separate higher-level tests.
"""

from __future__ import annotations

import base64
from datetime import datetime, UTC

import pytest

from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.compute_base import (
    INSERT_SCREENSHOT_MISSING_PLACEHOLDER,
    INSERT_SCREENSHOT_TAG_RE,
    ImageHit,
    _artifact_filename,
    _parse_tag_attrs,
    is_base64_image,
    substitute_insert_screenshot_tags,
)


# A tiny but real PNG header — enough to verify base64 round-tripping
# without bloating the test file with a full image.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()

_TS = datetime(2026, 6, 2, 11, 30, 45, tzinfo=UTC)


def _hit(
    *,
    tool_use_id: str = "toolu_abc",
    media_type: str = "image/png",
    data: str = _PNG_B64,
    line_num: int = 42,
    timestamp: datetime | None = _TS,
) -> ImageHit:
    """Build an :class:`ImageHit` with sensible defaults."""
    return ImageHit(
        tool_use_id=tool_use_id,
        media_type=media_type,
        data=data,
        source_line_num=line_num,
        source_timestamp=timestamp,
    )


class TestIsBase64Image:
    def test_claude_source_shape(self):
        assert is_base64_image(
            {"type": "base64", "media_type": "image/jpeg", "data": "QUFB"}
        ) == ("image/jpeg", "QUFB")

    def test_text_type_rejected(self):
        # type='text' is the explicit carve-out in the front-end shape
        # detector — text content with a media_type field would
        # otherwise (incorrectly) match.
        assert is_base64_image(
            {"type": "text", "media_type": "image/png", "data": "X"}
        ) is None

    def test_non_image_media_type_rejected(self):
        assert is_base64_image(
            {"type": "base64", "media_type": "application/pdf", "data": "X"}
        ) is None

    @pytest.mark.parametrize("missing", ["type", "media_type", "data"])
    def test_missing_field(self, missing):
        blob = {"type": "base64", "media_type": "image/png", "data": "X"}
        del blob[missing]
        assert is_base64_image(blob) is None

    @pytest.mark.parametrize(
        "value", [None, "string", 42, ["list"], {"no": "shape"}]
    )
    def test_non_matching_shapes(self, value):
        assert is_base64_image(value) is None

    def test_accepts_unknown_image_subtype(self):
        # Pass-through any ``image/*`` media type — even subtypes the
        # extension map doesn't know about. The artifacts backend's 404
        # filter is the second line of defence.
        assert is_base64_image(
            {"type": "base64", "media_type": "image/avif", "data": "QQ=="}
        ) == ("image/avif", "QQ==")


class TestInsertScreenshotTagRe:
    # ``group(1)`` is the *attribute blob* — a (possibly empty) string of
    # ``\s+name="value"`` repetitions that ``_parse_tag_attrs`` then
    # decodes. The tests below assert on that contract rather than on
    # specific characters of the blob so they survive future whitespace
    # tweaks.

    def test_default_tag(self):
        m = INSERT_SCREENSHOT_TAG_RE.search("a <twicc:insert-screenshot /> b")
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (0, None)

    def test_with_offset(self):
        m = INSERT_SCREENSHOT_TAG_RE.search('<twicc:insert-screenshot offset="3" />')
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (3, None)

    def test_with_title(self):
        m = INSERT_SCREENSHOT_TAG_RE.search(
            '<twicc:insert-screenshot title="Login page" />'
        )
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (0, "Login page")

    def test_offset_then_title(self):
        m = INSERT_SCREENSHOT_TAG_RE.search(
            '<twicc:insert-screenshot offset="2" title="Earlier" />'
        )
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (2, "Earlier")

    def test_title_then_offset(self):
        # Attribute order is irrelevant: the parser walks them in source
        # order but only the resulting ``(offset, title)`` matters.
        m = INSERT_SCREENSHOT_TAG_RE.search(
            '<twicc:insert-screenshot title="Earlier" offset="2" />'
        )
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (2, "Earlier")

    def test_tolerates_inner_whitespace(self):
        # The trailing ``\s*`` before ``/>`` and the ``\s+`` between
        # attributes allow the agent's serialiser to insert any spacing
        # without breaking the match.
        m = INSERT_SCREENSHOT_TAG_RE.search(
            '<twicc:insert-screenshot   offset="0"   title="x"   />'
        )
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (0, "x")

    def test_finds_multiple_tags(self):
        text = '<twicc:insert-screenshot /> mid <twicc:insert-screenshot offset="1" />'
        matches = list(INSERT_SCREENSHOT_TAG_RE.finditer(text))
        assert len(matches) == 2
        assert [_parse_tag_attrs(m.group(1)) for m in matches] == [
            (0, None),
            (1, None),
        ]

    def test_unknown_attribute_silently_ignored(self):
        # Forward-compat: a future ``caption=""`` (or any unknown name)
        # must not break older deployments — the parser just drops it.
        m = INSERT_SCREENSHOT_TAG_RE.search(
            '<twicc:insert-screenshot caption="x" offset="1" />'
        )
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (1, None)

    def test_negative_offset_clamped_to_zero(self):
        # The regex now accepts any quoted value, but the parser clamps
        # negative offsets to 0 (Python would otherwise index from the
        # tail of the hits list, which is semantically wrong here).
        m = INSERT_SCREENSHOT_TAG_RE.search(
            '<twicc:insert-screenshot offset="-1" />'
        )
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (0, None)

    def test_non_integer_offset_falls_back_to_zero(self):
        m = INSERT_SCREENSHOT_TAG_RE.search(
            '<twicc:insert-screenshot offset="abc" />'
        )
        assert m is not None
        assert _parse_tag_attrs(m.group(1)) == (0, None)

    def test_namespaced_tag_does_not_collide(self):
        # Confidence check: the namespace prefix means the regex won't
        # accidentally match other markdown / XML the agent might emit.
        assert (
            INSERT_SCREENSHOT_TAG_RE.search("<insert-screenshot />") is None
        )
        assert (
            INSERT_SCREENSHOT_TAG_RE.search(
                "<twicc:insert-something-else />"
            )
            is None
        )


class TestArtifactFilename:
    def test_standard_filename(self):
        # Timestamp + tool_use_id + offset are concatenated into a
        # deterministic, ASCII-safe filename so the artifacts backend
        # accepts it and re-processing the same SessionItem regenerates
        # the same path (enables idempotent disk save).
        hit = _hit(timestamp=_TS, tool_use_id="toolu_abc", media_type="image/jpeg")
        assert _artifact_filename(hit, 0) == "2026-06-02-11-30-45-toolu_abc-off0.jpeg"

    def test_filename_includes_offset(self):
        hit = _hit()
        assert _artifact_filename(hit, 0) != _artifact_filename(hit, 1)

    def test_filename_without_timestamp(self):
        hit = _hit(timestamp=None)
        assert _artifact_filename(hit, 0).startswith("unknowntime-")

    def test_filename_without_tool_use_id(self):
        hit = _hit(tool_use_id="")
        # Empty tool_use_id falls back to a stable sentinel — never an
        # empty segment that would yield ``--off0`` with a stray dash.
        assert "-unknown-" in _artifact_filename(hit, 0)

    @pytest.mark.parametrize(
        "media_type,expected_ext",
        [
            ("image/jpeg", "jpeg"),
            ("image/png", "png"),
            ("image/webp", "webp"),
            ("image/gif", "gif"),
            ("image/svg+xml", "svg"),
            ("image/avif", "avif"),  # falls through to subtype
        ],
    )
    def test_extension_for_known_and_unknown_media_types(
        self, media_type, expected_ext
    ):
        hit = _hit(media_type=media_type)
        assert _artifact_filename(hit, 0).endswith(f".{expected_ext}")


class TestSubstituteInsertScreenshotTags:
    def test_no_tag_returns_unchanged(self, tmp_path):
        text = "Nothing to substitute here."
        # Provider should never even be invoked when there is no tag.
        out = substitute_insert_screenshot_tags(
            text,
            session_id="sess",
            images_provider=lambda _needed: pytest.fail(
                "images_provider must not be called when no tag is present"
            ),
            artifacts_dir=tmp_path,
        )
        assert out == text

    def test_single_default_tag_resolves_to_latest(self, tmp_path):
        hit = _hit()
        out = substitute_insert_screenshot_tags(
            "before <twicc:insert-screenshot /> after",
            session_id="sess",
            images_provider=lambda _needed: iter([hit]),
            artifacts_dir=tmp_path,
        )
        expected_filename = _artifact_filename(hit, 0)
        assert out == (
            f"before ![screenshot](/artifacts/sess/{expected_filename}) after"
        )
        target = tmp_path / "sess" / expected_filename
        assert target.exists()
        assert target.read_bytes() == _PNG_BYTES

    def test_explicit_offsets_resolve_independently(self, tmp_path):
        a = _hit(tool_use_id="toolu_a", media_type="image/jpeg", data="QUFB")
        b = _hit(
            tool_use_id="toolu_b",
            media_type="image/png",
            data=_PNG_B64,
            line_num=30,
            timestamp=_TS,
        )
        out = substitute_insert_screenshot_tags(
            '<twicc:insert-screenshot /> + <twicc:insert-screenshot offset="1" />',
            session_id="sess",
            images_provider=lambda _needed: iter([a, b]),
            artifacts_dir=tmp_path,
        )
        # Both tags must produce distinct markdown links pointing to
        # their respective offset-resolved artifacts.
        assert _artifact_filename(a, 0) in out
        assert _artifact_filename(b, 1) in out

    def test_missing_image_yields_placeholder(self, tmp_path):
        out = substitute_insert_screenshot_tags(
            'gap <twicc:insert-screenshot offset="5" />',
            session_id="sess",
            images_provider=lambda _needed: iter([]),  # nothing available
            artifacts_dir=tmp_path,
        )
        assert out == f"gap {INSERT_SCREENSHOT_MISSING_PLACEHOLDER}"
        # No directory should be created when no image is saved.
        assert not (tmp_path / "sess").exists()

    def test_partial_missing_still_resolves_others(self, tmp_path):
        hit = _hit()
        out = substitute_insert_screenshot_tags(
            '<twicc:insert-screenshot /> // <twicc:insert-screenshot offset="3" />',
            session_id="sess",
            images_provider=lambda _needed: iter([hit]),
            artifacts_dir=tmp_path,
        )
        # First tag at offset=0 resolves, second tag at offset=3 misses.
        assert _artifact_filename(hit, 0) in out
        assert INSERT_SCREENSHOT_MISSING_PLACEHOLDER in out

    def test_idempotent_save(self, tmp_path):
        # Running the substitution twice with the same hits should not
        # rewrite the artifact file on disk — the deterministic
        # filename short-circuits the second save. Critical for
        # re-compute correctness (the file may already be referenced by
        # earlier rewrites that are still being served to clients).
        hit = _hit()
        text = "<twicc:insert-screenshot />"
        substitute_insert_screenshot_tags(
            text,
            session_id="sess",
            images_provider=lambda _needed: iter([hit]),
            artifacts_dir=tmp_path,
        )
        target = tmp_path / "sess" / _artifact_filename(hit, 0)
        mtime_before = target.stat().st_mtime_ns

        substitute_insert_screenshot_tags(
            text,
            session_id="sess",
            images_provider=lambda _needed: iter([hit]),
            artifacts_dir=tmp_path,
        )
        assert target.stat().st_mtime_ns == mtime_before

    def test_provider_called_with_max_offset_plus_one(self, tmp_path):
        # The substitution helper must ask for ``max(offsets) + 1`` images
        # — fewer would leave a higher-offset tag unresolved even when
        # the provider could in fact yield it.
        recorded: list[int] = []

        def provider(needed: int):
            recorded.append(needed)
            return iter([])

        substitute_insert_screenshot_tags(
            '<twicc:insert-screenshot offset="2" /><twicc:insert-screenshot />',
            session_id="sess",
            images_provider=provider,
            artifacts_dir=tmp_path,
        )
        # max(0, 2) + 1 == 3
        assert recorded == [3]

    def test_title_used_as_alt_text(self, tmp_path):
        hit = _hit()
        out = substitute_insert_screenshot_tags(
            '<twicc:insert-screenshot title="Login page" />',
            session_id="sess",
            images_provider=lambda _needed: iter([hit]),
            artifacts_dir=tmp_path,
        )
        expected_filename = _artifact_filename(hit, 0)
        assert out == f"![Login page](/artifacts/sess/{expected_filename})"

    def test_title_with_special_chars_escaped(self, tmp_path):
        # ``[`` and ``]`` would open / close the alt segment early
        # (unmatched ``[`` also triggers nested-link parsing that bails
        # out and renders the raw markdown as plain text), and ``\``
        # would half-escape the next character. All three must be
        # escaped in the rendered markdown.
        hit = _hit()
        out = substitute_insert_screenshot_tags(
            '<twicc:insert-screenshot title="bad [ ] chars \\here" />',
            session_id="sess",
            images_provider=lambda _needed: iter([hit]),
            artifacts_dir=tmp_path,
        )
        assert "![bad \\[ \\] chars \\\\here](" in out

    def test_title_in_missing_placeholder(self, tmp_path):
        # When a tag with a title misses, the placeholder names the
        # title so the reader can tell which expected image is missing.
        out = substitute_insert_screenshot_tags(
            '<twicc:insert-screenshot title="Diff view" offset="5" />',
            session_id="sess",
            images_provider=lambda _needed: iter([]),
            artifacts_dir=tmp_path,
        )
        assert out == "*[no screenshot available: Diff view]*"

    def test_missing_placeholder_without_title_is_generic(self, tmp_path):
        # Sanity check: the title-aware placeholder doesn't trigger when
        # the tag has no title at all.
        out = substitute_insert_screenshot_tags(
            '<twicc:insert-screenshot offset="5" />',
            session_id="sess",
            images_provider=lambda _needed: iter([]),
            artifacts_dir=tmp_path,
        )
        assert out == INSERT_SCREENSHOT_MISSING_PLACEHOLDER

    def test_offset_and_title_combine(self, tmp_path):
        a = _hit(tool_use_id="toolu_a", media_type="image/jpeg", data="QUFB")
        b = _hit(
            tool_use_id="toolu_b",
            media_type="image/png",
            data=_PNG_B64,
            line_num=30,
            timestamp=_TS,
        )
        out = substitute_insert_screenshot_tags(
            (
                '<twicc:insert-screenshot title="Now" /> + '
                '<twicc:insert-screenshot offset="1" title="Before" />'
            ),
            session_id="sess",
            images_provider=lambda _needed: iter([a, b]),
            artifacts_dir=tmp_path,
        )
        assert f"![Now](/artifacts/sess/{_artifact_filename(a, 0)})" in out
        assert f"![Before](/artifacts/sess/{_artifact_filename(b, 1)})" in out


class TestClaudeCodeIterToolResultImageRefs:
    @pytest.fixture
    def cc(self) -> ClaudeCodeSessionCompute:
        return ClaudeCodeSessionCompute()

    def test_yields_image_from_tool_result(self, cc):
        parsed = {
            "type": "user",
            "sessionId": "sess",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_abc",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": "QUFB",
                                },
                            }
                        ],
                    }
                ],
            },
        }
        assert list(cc.iter_tool_result_image_refs(parsed)) == [
            ("toolu_abc", "image/jpeg", "QUFB"),
        ]

    def test_yields_multiple_images_in_reverse_document_order(self, cc):
        # When a tool_result carries several images (e.g. an MCP
        # ``browser_batch`` that produced two screenshots in one call),
        # they are yielded in REVERSE document order so the
        # chronologically later image lands at ``offset=0`` in the
        # walker's hits list — matching the "offset=0 = most recent"
        # contract.
        parsed = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_x",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": "A",  # taken first
                                },
                            },
                            {"type": "text", "text": "caption"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "B",  # taken second — most recent
                                },
                            },
                        ],
                    }
                ],
            },
        }
        refs = list(cc.iter_tool_result_image_refs(parsed))
        assert refs == [
            ("toolu_x", "image/png", "B"),    # most recent first
            ("toolu_x", "image/jpeg", "A"),   # then the older one
        ]

    @pytest.mark.parametrize(
        "parsed",
        [
            # Non-user entry — no tool_result block to walk.
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
            # User entry but no tool_result inside.
            {"type": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
            # Tool_result with no image content.
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_x",
                            "content": [{"type": "text", "text": "plain"}],
                        }
                    ]
                },
            },
            # Image content but missing source (defensive).
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_x",
                            "content": [{"type": "image"}],
                        }
                    ]
                },
            },
        ],
    )
    def test_yields_nothing_for_non_image_entries(self, cc, parsed):
        assert list(cc.iter_tool_result_image_refs(parsed)) == []

    def test_falls_back_to_empty_tool_use_id(self, cc):
        # When ``tool_use_id`` is missing the helper still yields the
        # image (with an empty string id); the artifact filename uses
        # ``unknown`` as the placeholder so the disk save is still
        # deterministic.
        parsed = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": _PNG_B64,
                                },
                            }
                        ],
                    }
                ]
            },
        }
        refs = list(cc.iter_tool_result_image_refs(parsed))
        assert refs == [("", "image/png", _PNG_B64)]


def _codex_image_segment(data: str = _PNG_B64, media: str = "image/png") -> dict:
    """Build an ``input_image`` segment in the Codex data-URL shape."""
    return {
        "type": "input_image",
        "image_url": f"data:{media};base64,{data}",
        "detail": "auto",
    }


class TestCodexIterToolResultImageRefs:
    @pytest.fixture
    def cx(self) -> CodexSessionCompute:
        return CodexSessionCompute()

    def test_yields_image_from_code_mode_output(self, cx):
        # 5.6 code-mode exec cell: the aggregated output mixes the status
        # header, nested-tool text and the screenshot as an input_image
        # data URL. Real wire shape (custom_tool_call_output.output is a
        # plain list, not a JSON-encoded string).
        parsed = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call_abc",
                "output": [
                    {"type": "input_text", "text": "Script completed\nOutput:\n"},
                    {"type": "input_text", "text": "Took a screenshot."},
                    _codex_image_segment(),
                ],
            },
        }
        assert list(cx.iter_tool_result_image_refs(parsed)) == [
            ("call_abc", "image/png", _PNG_B64),
        ]

    def test_yields_image_from_function_call_output(self, cx):
        # Direct tool result (pre-5.6 MCP screenshot, view_image, ...).
        parsed = {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_def",
                "output": [_codex_image_segment(media="image/jpeg", data="QUFB")],
            },
        }
        assert list(cx.iter_tool_result_image_refs(parsed)) == [
            ("call_def", "image/jpeg", "QUFB"),
        ]

    def test_yields_multiple_images_in_reverse_document_order(self, cx):
        # Same contract as Claude: in a multi-image output (a code-mode
        # cell taking two screenshots), the chronologically later image
        # lands at offset=0.
        parsed = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call_x",
                "output": [
                    _codex_image_segment(data="A"),
                    {"type": "input_text", "text": "between"},
                    _codex_image_segment(data="B", media="image/jpeg"),
                ],
            },
        }
        assert list(cx.iter_tool_result_image_refs(parsed)) == [
            ("call_x", "image/jpeg", "B"),  # most recent first
            ("call_x", "image/png", "A"),
        ]

    @pytest.mark.parametrize(
        "image_url",
        [
            "https://example.com/shot.png",       # remote reference, no bytes
            "data:image/png,rawpercentencoded",   # not base64
            "data:application/pdf;base64,QUFB",   # not an image
            "data:image/png;base64,",             # empty payload
            42,                                   # not even a string
        ],
    )
    def test_non_base64_image_urls_skipped(self, cx, image_url):
        parsed = {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_x",
                "output": [{"type": "input_image", "image_url": image_url}],
            },
        }
        assert list(cx.iter_tool_result_image_refs(parsed)) == []

    @pytest.mark.parametrize(
        "parsed",
        [
            # String output (the common text-only tool result).
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_x",
                    "output": "plain text result",
                },
            },
            # McpToolCall item — carries the same image bytes as
            # the paired aggregated output (raw base64 in
            # ``result.content``) and is deliberately NOT harvested:
            # walking both sources would duplicate the screenshot at two
            # consecutive offsets.
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "thread_id": "thread-1",
                    "turn_id": "turn-1",
                    "completed_at_ms": 0,
                    "item": {
                        "type": "McpToolCall",
                        "id": "exec-uuid",
                        "server": "demo",
                        "tool": "screenshot",
                        "arguments": {},
                        "status": "completed",
                        "result": {
                            "content": [
                                {"type": "image", "data": _PNG_B64, "mimeType": "image/png"},
                            ]
                        },
                    },
                },
            },
            # User message with an attached image — an attachment, not a
            # tool result (same scope as the Claude Code override).
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [_codex_image_segment()],
                },
            },
        ],
    )
    def test_yields_nothing_for_non_tool_result_shapes(self, cx, parsed):
        assert list(cx.iter_tool_result_image_refs(parsed)) == []

    def test_falls_back_to_empty_call_id(self, cx):
        parsed = {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": [_codex_image_segment()],
            },
        }
        assert list(cx.iter_tool_result_image_refs(parsed)) == [
            ("", "image/png", _PNG_B64),
        ]
