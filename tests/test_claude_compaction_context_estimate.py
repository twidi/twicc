"""Context usage estimate on a Claude Code ``compact_boundary`` line.

Right after a compaction no assistant ``usage`` exists yet, so the session
used to keep its pre-compaction context usage (e.g. 95 %) until the next
assistant message. The ``compact_boundary`` line now carries an estimate:
``compactMetadata.postTokens`` plus a baseline taken from the session's first
API call (minus the text of the first user prompt).
"""

from __future__ import annotations

import queue

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute


def _line(payload: dict) -> str:
    return orjson.dumps({"timestamp": "2026-01-01T12:00:00.000Z", **payload}).decode()


def _user(content, **extra) -> dict:
    return {"type": "user", "message": {"role": "user", "content": content}, **extra}


def _assistant(input_tokens: int, cache_read: int = 0, output_tokens: int = 0) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "id": f"msg_{input_tokens}_{cache_read}",
            "model": "claude-opus-4-5",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "output_tokens": output_tokens,
            },
        },
    }


def _boundary(post_tokens: int | None = None) -> dict:
    metadata = {"trigger": "manual", "preTokens": 950_000}
    if post_tokens is not None:
        metadata["postTokens"] = post_tokens
    return {"type": "system", "subtype": "compact_boundary", "compactMetadata": metadata}


@pytest.fixture
def claude_session(db):
    project = Project.objects.create(id="test-project-claude-compaction")
    return Session.objects.create(
        id="test-session-claude-compaction",
        project=project,
        provider=Provider.CLAUDE_CODE,
    )


def _store(session: Session, payloads: list[dict]) -> None:
    for line_num, payload in enumerate(payloads, start=1):
        SessionItem.objects.create(session=session, line_num=line_num, content=_line(payload))


def _estimate(session: Session, payload: dict, line_num: int) -> int | None:
    """Run the live hook on a boundary line that is not stored yet."""
    item = SessionItem(session=session, line_num=line_num, content=_line(payload))
    ClaudeCodeSessionCompute().compute_item_cost_and_usage(item, payload, set(), None)
    return item.context_usage


class TestBoundaryEstimate:
    def test_post_tokens_plus_baseline(self, claude_session):
        # First prompt: 400 text chars (=100 tokens) + an image block ignored.
        prompt = [
            {"type": "text", "text": "x" * 400},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ]
        _store(claude_session, [
            _user("<meta>" * 1000, isMeta=True),  # meta lines are not the prompt
            _user(prompt),
            _assistant(input_tokens=100, cache_read=40_000, output_tokens=50),
            _assistant(input_tokens=900_000),
        ])
        # baseline = 40_150 - 100 = 40_050
        assert _estimate(claude_session, _boundary(post_tokens=12_000), 5) == 52_050

    def test_string_prompt_is_counted(self, claude_session):
        _store(claude_session, [_user("y" * 4000), _assistant(input_tokens=30_000)])
        assert _estimate(claude_session, _boundary(post_tokens=5_000), 3) == 34_000

    def test_zero_usage_lines_are_skipped(self, claude_session):
        # A synthetic zero-usage assistant line must not be taken as the first call.
        _store(claude_session, [_user("hi"), _assistant(input_tokens=0), _assistant(input_tokens=20_000)])
        assert _estimate(claude_session, _boundary(post_tokens=1_000), 4) == 21_000

    def test_no_post_tokens_uses_baseline_alone(self, claude_session):
        _store(claude_session, [_user("hi"), _assistant(input_tokens=30_000)])
        assert _estimate(claude_session, _boundary(), 3) == 30_000

    def test_no_baseline_uses_post_tokens_alone(self, claude_session):
        _store(claude_session, [_user("hi")])
        assert _estimate(claude_session, _boundary(post_tokens=9_000), 2) == 9_000

    def test_nothing_known_leaves_usage_unset(self, claude_session):
        _store(claude_session, [_user("hi")])
        assert _estimate(claude_session, _boundary(), 2) is None


class TestBatchRecompute:
    def test_session_context_usage_is_the_estimate(self, claude_session):
        _store(claude_session, [
            _user("z" * 800),
            _assistant(input_tokens=45_200),
            _assistant(input_tokens=950_000),
            _boundary(post_tokens=15_000),
        ])

        compute = ClaudeCodeSessionCompute()
        result_queue = queue.Queue()
        compute.compute_session_metadata(claude_session.id, result_queue, run_id=1)
        while True:
            try:
                msg = orjson.loads(result_queue.get_nowait())
            except queue.Empty:
                break
            if msg.get("type") == "session_complete":
                compute.apply_session_complete(msg)

        claude_session.refresh_from_db()
        # 45_200 - 800 // 4 + 15_000
        assert claude_session.context_usage == 60_000
        assert SessionItem.objects.get(session=claude_session, line_num=4).context_usage == 60_000
