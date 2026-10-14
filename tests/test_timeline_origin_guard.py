"""Source guards of the origin work (design 2026-10-14-timeline-actor-origin, sections 5-7)."""

import ast
from pathlib import Path

import twicc

SRC = Path(twicc.__file__).resolve().parent

#: Calls that omit both ``actor`` and ``origin`` are request-caused: they read the carrier. Exactly these
#: functions are allowed to write that way. A new system writer that forgets its explicit ``actor=``
#: shows up here instead of silently becoming ``user`` / ``ui``.
#: Calls spread with ``**kwargs`` are NOT covered by this guard: the pin-scope ``common`` dict in
#: ``session_update``, ``record_session_created`` / ``record_title_change`` (``who``), ``timeline_boot``
#: (``common`` with an explicit system actor) and the ``record_event(kind, **kwargs)`` forwarder in
#: ``session_timeline.py`` (a pure forwarder to ``record_event_sync``). They are covered by
#: ``tests/test_timeline_origin_writers.py`` and ``tests/test_timeline_origin_titles_creation.py`` and by
#: the explicit system actors.
REQUEST_CAUSED_WRITERS = {
    ("agent/timeline_hooks.py", "record_process_started"),
    ("core/services/session_update.py", "_save_and_record"),  # archive / unarchive and mute flips
    ("core/services/session_visibility.py", "_apply_visibility_flag"),
}


def test_no_writer_hard_codes_the_user_actor():
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if path != SRC / "core" / "models.py" and "TimelineActor.USER" in path.read_text()
    ]
    assert offenders == []


class _Collector(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.found = set()

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        keywords = self._recording_keywords(node)
        if keywords is not None and None not in keywords and not ({"actor", "origin"} & keywords):
            self.found.add(self.stack[-1] if self.stack else "<module>")
        self.generic_visit(node)

    @staticmethod
    def _recording_keywords(node):
        """Keyword names of a ``record_event[_sync](...)`` call, also when it is the target of ``to_thread``."""
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in {"record_event_sync", "record_event"}:
            return {keyword.arg for keyword in node.keywords}
        if (
            name == "to_thread"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in {"record_event_sync", "record_event"}
        ):
            return {keyword.arg for keyword in node.keywords}
        return None


def test_only_the_known_request_caused_writers_omit_the_actor_and_the_origin():
    found = set()
    for path in SRC.rglob("*.py"):
        collector = _Collector()
        collector.visit(ast.parse(path.read_text()))
        found |= {(path.relative_to(SRC).as_posix(), function) for function in collector.found}
    assert found == REQUEST_CAUSED_WRITERS
