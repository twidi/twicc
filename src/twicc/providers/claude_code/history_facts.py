"""Compact historical evidence from normalized Claude records."""

from twicc.providers.history_facts import HistoryFact, HistoryFactContext, HistoryFactKind


def _blocks(parsed: dict):
    from .compute import get_message_content_list

    content = get_message_content_list(parsed, "assistant")
    for index, block in enumerate(content or ()):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        identifier = block.get("id")
        if isinstance(identifier, str) and identifier.strip():
            yield index, identifier, block


def tool_calls(parsed: dict):
    """Yield source blocks in order, including duplicate identifiers."""
    for _, identifier, block in _blocks(parsed):
        yield identifier, block


def extract_history_facts(parsed: dict, *, line_num: int, history: HistoryFactContext) -> list[HistoryFact]:
    from .compute import AGENT_TOOL_NAMES

    grouped = {}
    for index, identifier, block in _blocks(parsed):
        inputs = block.get("input")
        name = block.get("name")
        name = name if isinstance(name, str) else ""
        previous = grouped.get(identifier)
        blocks = previous["blocks"] if previous else []
        blocks.append(index)
        # Existing call maps select the last block. Keep every source index
        # so consumers with first-block semantics can inspect the original.
        grouped[identifier] = {
            "source_line": line_num,
            "blocks": blocks,
            "tool_name": name,
            "is_background": bool(isinstance(inputs, dict) and inputs.get("run_in_background")),
            "is_agent": name in AGENT_TOOL_NAMES,
        }
    return [HistoryFact(line_num, HistoryFactKind.TOOL_CALL, key, data) for key, data in grouped.items()]
