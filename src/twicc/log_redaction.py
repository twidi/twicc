"""Keep request arguments loggable: a data URI in ``--attach`` can be tens of MB.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.6, §4.7.
"""

LOG_VALUE_MAX_CHARS = 512
LOG_VALUE_KEEP_CHARS = 64


def redact_for_log(value: object) -> object:
    """*value* with every string longer than 512 characters cut to ``<first 64>…<N chars>``.

    Walks lists, tuples and dicts. Looks at no prefix: an argv token may be ``--attach=data:…``
    or a bare ``data:…`` after ``--attach``.
    """
    if isinstance(value, str):
        if len(value) > LOG_VALUE_MAX_CHARS:
            return f"{value[:LOG_VALUE_KEEP_CHARS]}…<{len(value)} chars>"
        return value
    if isinstance(value, list):
        return [redact_for_log(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_for_log(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_for_log(item) for key, item in value.items()}
    return value
