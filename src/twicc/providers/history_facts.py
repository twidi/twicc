"""Exact, prior-line readers for computed session history facts and raw items."""

from collections.abc import Iterator, Sequence
from typing import NamedTuple

from twicc.core.models import HistoryFactKind, Session, SessionHistoryFact, SessionItem
from twicc.providers.helpers import get_provider_helpers

__all__ = [
    "HistoryFact", "HistoryFactContext", "HistoryFactKind", "append_history_facts", "history_facts_are_current",
    "iter_history_facts", "iter_history_items", "iter_resolver_items", "replace_history_facts",
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


def iter_resolver_items(
    session_id: str, kind: str | None, key: str | None = None, *, before_line: int = 2**63 - 1,
) -> Iterator[SessionItem]:
    """Read source candidates, with authoritative indexed absence for current sessions.

    Read readiness afresh for each operation, never from a long-lived provider cache.
    A stale session uses raw pages exclusively, even when partial facts exist.
    ``kind=None`` explicitly requests unsupported legacy evidence (Claude queues).
    Provider predicates still validate each source item.
    """
    session = Session.objects.only("provider", "compute_version").filter(id=session_id).first()
    if session is None:
        return
    if kind is not None and history_facts_are_current(session):
        if key is not None:
            lines = (fact.line_num for fact in iter_history_facts(session_id, kind, key, before_line=before_line))
        else:
            # Spawn recovery needs all calls. Deduplicate several blocks on one
            # source line, while paging by line rather than by fact occurrence.
            def source_lines():
                cursor = before_line
                while True:
                    page = list(SessionHistoryFact.objects.filter(
                        session_id=session_id, kind=kind, line_num__lt=cursor,
                    ).order_by("-line_num").values_list("line_num", flat=True).distinct()[:_FACT_PAGE_SIZE])
                    yield from page
                    if len(page) < _FACT_PAGE_SIZE:
                        return
                    cursor = page[-1]
            lines = source_lines()
        for line in lines:
            item = SessionItem.objects.filter(session_id=session_id, line_num=line).first()
            if item is not None:
                yield item
        return
    cursor = before_line
    while True:
        rows = list(SessionItem.objects.filter(
            session_id=session_id, line_num__lt=cursor,
        ).order_by("-line_num")[:_FACT_PAGE_SIZE])
        yield from rows
        if len(rows) < _FACT_PAGE_SIZE:
            return
        cursor = rows[-1].line_num


class HistoryFactContext:
    """Read-only extraction view over earlier records; registration belongs to the caller.

    Keep references to parsed call records, never copies of scripts or outputs.
    Persisted lookup uses the normal compute version to select facts or raw pages.
    """

    def __init__(self, provider, *, session_id: str | None = None):
        self.provider = provider
        self.session_id = session_id
        self.facts: list[HistoryFact] = []
        self._record_evidence: tuple[int, dict] | None = None
        self._calls: dict[str, list[tuple[dict, int]]] = {}
        self._cells: dict[str, list[tuple[str, int]]] = {}

    def set_record_evidence(self, line_num: int, evidence: dict | None) -> None:
        """Supply existing analysis for exactly one record, without replaying it."""
        self._record_evidence = (line_num, evidence) if evidence is not None else None

    def record_evidence(self, *, line_num: int) -> dict:
        if self._record_evidence is not None and self._record_evidence[0] == line_num:
            return self._record_evidence[1]
        return {}

    def register(self, parsed: dict, facts: Sequence[HistoryFact], *, line_num: int) -> None:
        """Publish a complete record only after its extractor returns."""
        self._record_evidence = None
        self.facts.extend(facts)
        calls = dict(self._tool_calls(parsed))
        for fact in facts:
            if fact.kind == HistoryFactKind.TOOL_CALL and fact.key in calls:
                self._calls.setdefault(fact.key, []).append((calls[fact.key], line_num))
            elif fact.kind == HistoryFactKind.CODE_CELL:
                self._cells.setdefault(fact.key, []).append((fact.data["call_id"], line_num))

    def _tool_calls(self, parsed: dict):
        from twicc.core.enums import Provider

        if self.provider == Provider.CODEX:
            from twicc.providers.codex.history_facts import tool_calls
        else:
            from twicc.providers.claude_code.history_facts import tool_calls
        return tool_calls(parsed)

    def _source_items(self, kind: str, key: str, *, before_line: int):
        import orjson

        if self.session_id is None:
            return
        for item in iter_resolver_items(self.session_id, kind, key, before_line=before_line):
            try:
                parsed = orjson.loads(item.content)
            except orjson.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield item.line_num, parsed

    def lookup_tool_call(self, call_id: str, *, before_line: int) -> tuple[dict, int] | None:
        """Return the newest matching call strictly before the requested line."""
        for payload, line in reversed(self._calls.get(call_id, ())):
            if line < before_line:
                return payload, line
        for line, parsed in self._source_items(HistoryFactKind.TOOL_CALL, call_id, before_line=before_line):
            payload = dict(self._tool_calls(parsed)).get(call_id)
            if payload is not None:
                return payload, line
        return None

    def lookup_code_cell(self, cell_id: str, *, before_line: int) -> tuple[str, int] | None:
        """Return the custom-output announcement, never a wait's repeated header."""
        from twicc.providers.codex.history_facts import code_cell

        for call_id, line in reversed(self._cells.get(cell_id, ())):
            if line < before_line:
                return call_id, line
        for line, parsed in self._source_items(HistoryFactKind.CODE_CELL, cell_id, before_line=before_line):
            cell = code_cell(parsed)
            if cell is not None and cell[0] == cell_id:
                return cell[1], line
        return None
