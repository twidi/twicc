"""Title-suggestion model routing at the WebSocket boundary."""

from asgiref.sync import async_to_sync
import pytest

from twicc.asgi import WSConsumer
from twicc.core.enums import Provider


class _TitleHelpers:
    def __init__(self, provider: Provider, first_message: str):
        self.provider = provider
        self.first_message = first_message

    def get_first_user_message(self, _session_id: str) -> str:
        return self.first_message

    async def generate_title(self, prompt: str, _system_prompt: str) -> str:
        return f"{self.provider.value}: {prompt}"


def _suggest_title_frames(monkeypatch, *, session_provider: Provider, title_model=None):
    helpers = {
        Provider.CLAUDE_CODE: _TitleHelpers(Provider.CLAUDE_CODE, "Claude prompt"),
        Provider.CODEX: _TitleHelpers(Provider.CODEX, "Codex prompt"),
    }
    frames = []

    async def send_json(frame):
        frames.append(frame)

    monkeypatch.setattr("twicc.asgi.get_provider_helpers", helpers.__getitem__)
    monkeypatch.setattr("twicc.asgi.ensure_provider_running", lambda _provider: None)

    consumer = WSConsumer()
    consumer.send_json = send_json
    payload = {
        "sessionId": "session-1",
        "provider": session_provider.value,
        "systemPrompt": "Summarize: {text}",
    }
    if title_model is not None:
        payload["titleSuggestionModel"] = title_model

    async_to_sync(consumer._handle_suggest_title)(payload)
    return frames


def test_missing_title_model_uses_the_session_provider(monkeypatch):
    frames = _suggest_title_frames(monkeypatch, session_provider=Provider.CODEX)

    assert frames == [{
        "type": "title_suggested",
        "sessionId": "session-1",
        "suggestion": "codex: Codex prompt",
        "sourcePrompt": "Codex prompt",
    }]


@pytest.mark.parametrize(
    ("session_provider", "title_model", "expected_suggestion", "expected_prompt"),
    [
        (Provider.CODEX, "haiku", "claude_code: Codex prompt", "Codex prompt"),
        (Provider.CLAUDE_CODE, "luna", "codex: Claude prompt", "Claude prompt"),
    ],
)
def test_fixed_title_model_overrides_generation_without_changing_prompt_source(
    monkeypatch,
    session_provider,
    title_model,
    expected_suggestion,
    expected_prompt,
):
    frames = _suggest_title_frames(
        monkeypatch,
        session_provider=session_provider,
        title_model=title_model,
    )

    assert frames == [{
        "type": "title_suggested",
        "sessionId": "session-1",
        "suggestion": expected_suggestion,
        "sourcePrompt": expected_prompt,
    }]
