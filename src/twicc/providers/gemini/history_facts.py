"""Compact historical evidence from Gemini CLI records (tool calls)."""

from twicc.providers.history_facts import HistoryFact, HistoryFactContext, HistoryFactKind

from . import records


def tool_calls(parsed: dict):
    """Yield ``(call id, call)`` for the tool calls a record carries, in order."""
    if parsed.get("type") != records.TYPE_GEMINI:
        return
    for call in records.tool_calls(parsed):
        yield call["id"], call


def extract_history_facts(parsed: dict, *, line_num: int, history: HistoryFactContext) -> list[HistoryFact]:
    facts = []
    for index, (identifier, call) in enumerate(tool_calls(parsed)):
        name = call.get("name") if isinstance(call.get("name"), str) else ""
        facts.append(HistoryFact(line_num, HistoryFactKind.TOOL_CALL, identifier, {
            "source_line": line_num,
            "blocks": [index],
            "tool_name": name,
            "is_background": False,
            "is_agent": name == records.INVOKE_AGENT_TOOL,
        }))
    return facts
