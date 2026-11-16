"""Pure functions over Gemini CLI session records.

A Gemini CLI session file (``<home>/.gemini/tmp/<slug>/chats/session-*.jsonl``)
is append-only JSONL. Its records:

- line 1, the header: ``{sessionId, projectHash, startTime, lastUpdated, kind}``;
- message records: ``{id, timestamp, type, content, ...}`` with ``type`` in
  ``user`` / ``gemini`` / ``info`` / ``warning`` / ``error``. ``gemini`` records
  add ``thoughts``, ``tokens``, ``model`` and ``toolCalls``;
- control records: ``{"$set": {...}}``, ``{"$patch": {...}}``,
  ``{"$rewindTo": id}``.

The CLI re-appends a whole message record, with the same ``id``, each time it
changes. :class:`RecordDeltaTracker` reduces those repeats to what they add
(see docs/plans/2026-11-16-gemini-provider-design.md).

No Django import here: shared by the compute, the helpers and the tests.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

TYPE_USER = "user"
TYPE_GEMINI = "gemini"
TYPE_ERROR = "error"
TYPE_INFO = "info"
TYPE_WARNING = "warning"

# Marker set by TwiCC on a re-appended record reduced to its delta.
REPEAT_KEY = "twicc_gemini_repeat"

# The environment description the CLI injects as the first user record of
# every session. Not something the user typed.
SESSION_CONTEXT_PREFIX = "<session_context>"

TERMINAL_TOOL_STATUSES = frozenset({"success", "error", "cancelled"})


def is_header(record: dict) -> bool:
    """Line 1 of a session file."""
    return isinstance(record.get("sessionId"), str) and isinstance(record.get("projectHash"), str)


def is_control(record: dict) -> bool:
    """``$set`` / ``$patch`` / ``$rewindTo`` records."""
    return any(key in record for key in ("$set", "$patch", "$rewindTo"))


def is_message(record: dict) -> bool:
    return isinstance(record.get("id"), str) and isinstance(record.get("type"), str) and not is_control(record)


def _parts(content: Any) -> list:
    if isinstance(content, list):
        return content
    if isinstance(content, dict):
        return [content]
    return []


def content_text(content: Any) -> str:
    """Join the text of a record ``content`` (string, part or list of parts)."""
    if isinstance(content, str):
        return content
    texts = [part.get("text") for part in _parts(content) if isinstance(part, dict)]
    return "".join(text for text in texts if isinstance(text, str))


def function_responses(record: dict) -> list[dict]:
    """The ``functionResponse`` payloads of a user record (tool result echoes)."""
    responses = []
    for part in _parts(record.get("content")):
        if isinstance(part, dict) and isinstance(part.get("functionResponse"), dict):
            responses.append(part["functionResponse"])
    return responses


def is_tool_result_echo(record: dict) -> bool:
    """A ``user`` record that only carries tool results."""
    return record.get("type") == TYPE_USER and bool(function_responses(record)) and not content_text(
        record.get("content")
    ).strip()


def user_text(record: dict) -> str | None:
    """Text typed by the user, ``None`` for anything else (context, tool results)."""
    if record.get("type") != TYPE_USER:
        return None
    text = content_text(record.get("content"))
    if not text.strip() or text.lstrip().startswith(SESSION_CONTEXT_PREFIX):
        return None
    return text


def attachment_count(record: dict) -> int:
    """Entries of the attachments manifest the ingestion stored on a user record."""
    attachments = record.get("twicc_attachments")
    entries = attachments.get("entries") if isinstance(attachments, dict) else None
    return len(entries) if isinstance(entries, list) else 0


# Gemini CLI agents receive their task wrapped in their own prompt template;
# the ``invoke_agent`` prompt sits between these tags (0.68 local agents).
_OBJECTIVE_RE = re.compile(r"<objective>\n?(.*?)\n?</objective>", re.DOTALL)
INVOKE_AGENT_TOOL = "invoke_agent"


def subagent_task(text: str) -> str:
    """The delegated prompt inside a subagent's first message, else ``text``."""
    match = _OBJECTIVE_RE.search(text)
    return match.group(1).strip() if match else text


def assistant_text(record: dict) -> str | None:
    """Visible text of a ``gemini`` record, ``None`` when it has none."""
    if record.get("type") != TYPE_GEMINI:
        return None
    text = content_text(record.get("content"))
    return text if text.strip() else None


def thoughts(record: dict) -> list[dict]:
    value = record.get("thoughts")
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def tool_calls(record: dict) -> list[dict]:
    value = record.get("toolCalls")
    return [call for call in value if isinstance(call, dict) and isinstance(call.get("id"), str)] if isinstance(
        value, list
    ) else []


def is_terminal_tool_call(call: dict) -> bool:
    return call.get("status") in TERMINAL_TOOL_STATUSES


def tool_call_error(call: dict) -> str | None:
    """Error text of a finished tool call, ``None`` when it succeeded."""
    for result in _parts(call.get("result")):
        response = result.get("functionResponse", {}).get("response") if isinstance(result, dict) else None
        if isinstance(response, dict) and response.get("error"):
            error = response["error"]
            return error if isinstance(error, str) else str(error)
    if call.get("status") in ("error", "cancelled"):
        return str(call.get("status"))
    return None


def tokens(record: dict) -> dict | None:
    value = record.get("tokens")
    return value if isinstance(value, dict) else None


class _Shown(NamedTuple):
    """What the UI already received for one message record id."""
    thought_count: int
    content: str
    finished_tool_calls: frozenset[str]
    has_tokens: bool


class RecordDeltaTracker:
    """Reduce re-appended message records to their delta.

    One tracker per session. :meth:`reduce` mutates a record in place so it
    only carries what was not shown yet, and returns whether it changed.
    Rules for a ``gemini`` record:

    - ``thoughts``: only the ones past the count already shown;
    - ``content``: dropped when identical to what was shown;
    - ``toolCalls``: only the calls that reached a terminal status and were
      not shown finished yet (running calls are shown by the live stream);
    - ``tokens``: dropped once counted (the CLI sets them once per record,
      every later copy repeats them).

    Any other message type that repeats its id is reduced to nothing (only
    ``gemini`` records are updated in place by the CLI).
    """

    def __init__(self) -> None:
        self._shown: dict[str, _Shown] = {}

    def snapshot(self) -> dict[str, _Shown]:
        return dict(self._shown)

    def restore(self, snapshot: dict[str, _Shown]) -> None:
        self._shown = dict(snapshot)

    def reduce(self, record: dict) -> bool:
        if not is_message(record):
            return False
        record_id = record["id"]
        previous = self._shown.get(record_id)
        is_gemini = record.get("type") == TYPE_GEMINI

        all_thoughts = thoughts(record)
        content = content_text(record.get("content")) if is_gemini else ""
        calls = tool_calls(record)
        finished = frozenset(call["id"] for call in calls if is_terminal_tool_call(call))
        has_tokens = tokens(record) is not None

        if record.get(REPEAT_KEY):
            # Already reduced (a recompute reads the stored copy, a cold start
            # replays stored items): it only holds what it added, so fold it in.
            if previous is None:
                previous = _Shown(0, "", frozenset(), False)
            self._shown[record_id] = _Shown(
                previous.thought_count + len(all_thoughts),
                content or previous.content,
                previous.finished_tool_calls | finished,
                previous.has_tokens or has_tokens,
            )
            return False

        changed = False
        if previous is None:
            if is_gemini and len(finished) != len(calls):
                # Running calls are shown by the stream, and stored once finished.
                record["toolCalls"] = [call for call in calls if call["id"] in finished]
                changed = True
            self._shown[record_id] = _Shown(len(all_thoughts), content, finished, has_tokens)
            return changed

        changed = True
        record[REPEAT_KEY] = True
        if not is_gemini:
            for key in ("content", "displayContent"):
                record.pop(key, None)
            return changed

        new_thoughts = all_thoughts[previous.thought_count:]
        if new_thoughts:
            record["thoughts"] = new_thoughts
        else:
            record.pop("thoughts", None)
        if content == previous.content:
            record.pop("content", None)
        new_calls = [call for call in calls if call["id"] in finished and call["id"] not in previous.finished_tool_calls]
        if new_calls:
            record["toolCalls"] = new_calls
        else:
            record.pop("toolCalls", None)
        if previous.has_tokens:
            record.pop("tokens", None)

        self._shown[record_id] = _Shown(
            max(previous.thought_count, len(all_thoughts)),
            content if content else previous.content,
            previous.finished_tool_calls | finished,
            previous.has_tokens or has_tokens,
        )
        return changed


def is_empty_repeat(record: dict) -> bool:
    """A reduced repeat that adds nothing visible."""
    if not record.get(REPEAT_KEY):
        return False
    return not (thoughts(record) or tool_calls(record) or content_text(record.get("content")).strip())
