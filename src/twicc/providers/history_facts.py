"""Exact, prior-line readers for computed session history facts and raw items."""

from collections.abc import Iterator, Sequence
from typing import NamedTuple

from twicc.core.models import HistoryFactKind, Session, SessionHistoryFact, SessionItem
from twicc.providers.helpers import get_provider_helpers

__all__ = [
    "HistoryFact", "HistoryFactKind", "append_history_facts", "history_facts_are_current",
    "iter_history_facts", "iter_history_items", "replace_history_facts",
]


class HistoryFact(NamedTuple):
    line_num: int
    kind: str
    key: str
    data: dict


_FACT_PAGE_SIZE = 128
_CONTEXT_KINDS = frozenset({
    HistoryFactKind.TURN_CONTEXT, HistoryFactKind.PLAN_MARKER,
    HistoryFactKind.GOAL_CONTEXT, HistoryFactKind.GOAL_UPDATE,
    HistoryFactKind.TOKEN_USAGE,
})


def iter_history_facts(session_id: str, kind: str, key: str, *, before_line: int) -> Iterator[HistoryFact]:
    """Yield exact-key facts newest first, strictly before a source line."""
    cursor = before_line
    while True:
        rows = list(
            SessionHistoryFact.objects.filter(
                session_id=session_id, kind=kind, key=key, line_num__lt=cursor,
            ).order_by("-line_num").values_list("line_num", "kind", "key", "data")[:_FACT_PAGE_SIZE]
        )
        for line_num, row_kind, row_key, data in rows:
            yield HistoryFact(line_num, row_kind, row_key, data)
        if len(rows) < _FACT_PAGE_SIZE:
            return
        cursor = rows[-1][0]


def iter_history_items(
    session_id: str, *, before_line: int, page_size: int = 128,
) -> Iterator[tuple[int, str]]:
    """Yield raw item content through descending line-number keyset pages."""
    if page_size < 1:
        raise ValueError("page_size must be positive")

    cursor = before_line
    while True:
        rows = list(
            SessionItem.objects.filter(session_id=session_id, line_num__lt=cursor)
            .order_by("-line_num").values_list("line_num", "content")[:page_size]
        )
        yield from rows
        if len(rows) < page_size:
            return
        cursor = rows[-1][0]


def replace_history_facts(session_id: str, facts: Sequence[HistoryFact]) -> None:
    """Replace one session's complete fact set inside the caller's transaction."""
    rows = _fact_rows(session_id, facts)
    SessionHistoryFact.objects.filter(session_id=session_id).delete()
    _insert_rows(rows)


def append_history_facts(session_id: str, facts: Sequence[HistoryFact]) -> None:
    """Insert fact occurrences idempotently inside the caller's transaction."""
    _insert_rows(_fact_rows(session_id, facts))


def _fact_rows(session_id: str, facts: Sequence[HistoryFact]) -> list[SessionHistoryFact]:
    """Validate the complete input before any row changes."""
    rows = []
    for fact in facts:
        if type(fact.line_num) is not int or fact.line_num < 1:
            raise ValueError("history fact line_num must be a positive integer")
        if fact.kind not in HistoryFactKind.values:
            raise ValueError(f"unsupported history fact kind: {fact.kind!r}")
        if not isinstance(fact.key, str) or not fact.key.strip():
            raise ValueError("history fact key must be a nonempty string")
        if fact.kind in _CONTEXT_KINDS and fact.key != "context":
            raise ValueError(f"history fact {fact.kind} requires the context key")
        if fact.kind == HistoryFactKind.CODE_EXEC_TARGET and fact.key not in {"patch", "mcp"}:
            raise ValueError("code execution target key must be patch or mcp")
        if not isinstance(fact.data, dict):
            raise TypeError("history fact data must be a dictionary")
        rows.append(SessionHistoryFact(
            session_id=session_id, line_num=fact.line_num, kind=fact.kind,
            key=fact.key, data=fact.data,
        ))
    return rows


def _insert_rows(rows: list[SessionHistoryFact]) -> None:
    """Ignore duplicate source tuples after all other required fields pass validation."""
    SessionHistoryFact.objects.bulk_create(
        rows,
        ignore_conflicts=True,
        batch_size=_FACT_PAGE_SIZE,
    )


def history_facts_are_current(session: Session) -> bool:
    """Use the provider's normal compute version as the fact readiness gate."""
    current = get_provider_helpers(session.provider).current_compute_version
    return current is not None and session.compute_version == current
