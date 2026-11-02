"""Async client for OpenRouter's chat completions: one prompt in, the answer's text out.

Two modes: :func:`stream_openrouter_text` yields the answer's text as it comes,
:func:`complete_openrouter_text` returns it whole. The provider is forced: OpenRouter
may not route the call to another one, and an answer from another one is a failure.
Every failure is an :class:`OpenRouterTextError` with a short reason.

The key is read through :func:`twicc.paths.get_env_secret` only. One log line per
call holds the model, the provider, the outcome, the times and the cost: never the
prompt, the answer nor the key.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import NamedTuple

import httpx
import orjson

from twicc.paths import OPENROUTER_API_KEY_ENV, get_env_secret

logger = logging.getLogger(__name__)

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# A text answer starts fast, but a long one streams for a while between two chunks at most.
REQUEST_TIMEOUT = httpx.Timeout(10.0, read=60.0)

# ``finish_reason`` values that mean the model or the provider declined to answer.
DECLINED_FINISH_REASONS = frozenset({"content_filter", "error"})


class TextModel(NamedTuple):
    """A model to call, the only provider allowed to serve it, and extra body fields."""

    model: str
    provider: str
    options: dict


QWEN_MODEL = TextModel("qwen/qwen3.8-27b", "Cerebras", {"reasoning": {"effort": "none"}, "max_tokens": 8000})


class OpenRouterTextError(Exception):
    """A text call failed.

    ``reason`` is one of ``no_key``, ``http``, ``network``, ``timeout``, ``wrong_provider``,
    ``cut``, ``declined``, ``stream_error``, ``bad_answer``; ``status`` is the HTTP status
    for ``http``, else None.
    """

    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason if status is None else f"{reason} ({status})")
        self.reason = reason
        self.status = status


class _CallLog:
    """The facts of one call, written as one log line at its end."""

    def __init__(self, model: TextModel, mode: str):
        self.model = model
        self.mode = mode
        self.started = time.monotonic()
        self.first_ms: int | None = None
        self.cost: float | None = None
        self.outcome: str | None = None

    def _elapsed_ms(self) -> int:
        return round((time.monotonic() - self.started) * 1000)

    def mark_first(self) -> None:
        if self.first_ms is None:
            self.first_ms = self._elapsed_ms()

    def read_usage(self, payload: dict) -> None:
        usage = payload.get("usage")
        if isinstance(usage, dict):
            cost = usage.get("cost")
            if isinstance(cost, int | float) and not isinstance(cost, bool):
                self.cost = cost

    def fail(self, error: OpenRouterTextError) -> None:
        # The status tells a wrong key (401), no credits (402) and a retired model (404) apart.
        self.outcome = error.reason if error.status is None else f"{error.reason}_{error.status}"

    def write(self) -> None:
        # A stop is the caller's choice, not a failure.
        log = logger.info if self.outcome in ("ok", "cancelled") else logger.warning
        log(
            "OpenRouter text call: model=%s provider=%s mode=%s outcome=%s first_ms=%s total_ms=%s cost=%s",
            self.model.model,
            self.model.provider,
            self.mode,
            self.outcome,
            self.first_ms,
            self._elapsed_ms(),
            self.cost,
        )


def _make_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """The single client factory (tests replace it with a mock transport)."""
    return httpx.AsyncClient(timeout=timeout)


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def openrouter_key_set() -> bool:
    """Whether the OpenRouter key is set (its value never leaves this module's calls)."""
    return get_env_secret(OPENROUTER_API_KEY_ENV) is not None


def _api_key() -> str:
    api_key = get_env_secret(OPENROUTER_API_KEY_ENV)
    if api_key is None:
        raise OpenRouterTextError("no_key")
    return api_key


def _build_payload(model: TextModel, prompt: str, system_prompt: str | None, *, stream: bool) -> dict:
    messages = []
    if system_prompt is not None:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return {
        "model": model.model,
        "messages": messages,
        "stream": stream,
        "provider": {"order": [model.provider], "allow_fallbacks": False},
        "usage": {"include": True},
        **model.options,
    }


def _check_provider(model: TextModel, payload: dict) -> None:
    """An answer that names another provider than the forced one is a failure."""
    provider = payload.get("provider")
    if provider is not None and provider != model.provider:
        raise OpenRouterTextError("wrong_provider")


def _check_finish(choice: dict, refusal: object) -> None:
    """A cut answer, or one the model or the provider declined, is a failure."""
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise OpenRouterTextError("cut")
    if finish_reason in DECLINED_FINISH_REASONS or refusal:
        raise OpenRouterTextError("declined")


def _first_choice(payload: dict) -> dict | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    return choices[0]


def _parse_json(raw: bytes | str) -> dict:
    try:
        payload = orjson.loads(raw)
    except orjson.JSONDecodeError:
        raise OpenRouterTextError("bad_answer") from None
    if not isinstance(payload, dict):
        raise OpenRouterTextError("bad_answer")
    return payload


async def stream_openrouter_text(
    model: TextModel, prompt: str, *, system_prompt: str | None = None
) -> AsyncIterator[str]:
    """Yield the answer's text deltas as they come.

    No time limit of its own: the caller bounds each step. Closing the generator, or
    cancelling the task that reads it, ends the HTTP request.
    """
    call_log = _CallLog(model, "stream")
    try:
        api_key = _api_key()
        payload = _build_payload(model, prompt, system_prompt, stream=True)
        try:
            async with (
                _make_client(REQUEST_TIMEOUT) as client,
                client.stream("POST", CHAT_URL, json=payload, headers=_auth_headers(api_key)) as response,
            ):
                if response.status_code != 200:
                    raise OpenRouterTextError("http", response.status_code)
                # Set by ``[DONE]`` or a ``finish_reason``: a stream that stops before is partial.
                finished = False
                async for line in response.aiter_lines():
                    # Blank lines, ``:`` comments and other SSE fields carry no data.
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    chunk = _parse_json(data)
                    if "error" in chunk:
                        raise OpenRouterTextError("stream_error")
                    _check_provider(model, chunk)
                    call_log.read_usage(chunk)
                    choice = _first_choice(chunk)
                    if choice is None:
                        continue
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        delta = {}
                    _check_finish(choice, delta.get("refusal"))
                    if choice.get("finish_reason") is not None:
                        finished = True
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        call_log.mark_first()
                        yield content
                if not finished:
                    raise OpenRouterTextError("stream_error")
        except httpx.HTTPError:
            raise OpenRouterTextError("network") from None
        call_log.outcome = "ok"
    except OpenRouterTextError as error:
        call_log.fail(error)
        raise
    except (GeneratorExit, asyncio.CancelledError):
        call_log.outcome = "cancelled"
        raise
    except BaseException as error:
        call_log.outcome = type(error).__name__
        raise
    finally:
        call_log.write()


async def _request_whole_answer(model: TextModel, prompt: str, system_prompt: str | None, call_log: _CallLog) -> str:
    api_key = _api_key()
    payload = _build_payload(model, prompt, system_prompt, stream=False)
    try:
        async with _make_client(REQUEST_TIMEOUT) as client:
            response = await client.post(CHAT_URL, json=payload, headers=_auth_headers(api_key))
    except httpx.HTTPError:
        raise OpenRouterTextError("network") from None
    if response.status_code != 200:
        raise OpenRouterTextError("http", response.status_code)
    body = _parse_json(response.content)
    call_log.read_usage(body)
    _check_provider(model, body)
    choice = _first_choice(body)
    if choice is None:
        raise OpenRouterTextError("bad_answer")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise OpenRouterTextError("bad_answer")
    _check_finish(choice, message.get("refusal"))
    content = message.get("content")
    if not isinstance(content, str):
        raise OpenRouterTextError("bad_answer")
    # As in stream mode, ``first_ms`` is set only when the call gives text.
    call_log.mark_first()
    return content


async def complete_openrouter_text(
    model: TextModel, prompt: str, *, system_prompt: str | None = None, timeout_seconds: float
) -> str:
    """The whole answer, within *timeout_seconds* (else an ``OpenRouterTextError("timeout")``)."""
    call_log = _CallLog(model, "complete")
    try:
        try:
            async with asyncio.timeout(timeout_seconds):
                text = await _request_whole_answer(model, prompt, system_prompt, call_log)
        except TimeoutError:
            raise OpenRouterTextError("timeout") from None
        call_log.outcome = "ok"
        return text
    except OpenRouterTextError as error:
        call_log.fail(error)
        raise
    except asyncio.CancelledError:
        call_log.outcome = "cancelled"
        raise
    except BaseException as error:
        call_log.outcome = type(error).__name__
        raise
    finally:
        call_log.write()
