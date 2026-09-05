"""PID-ancestry lookup against ``ProcessRun.agent_pid``.

Powers ``twicc whoami`` and the silent auto-fill of ``spawned_by`` in
``twicc create-session``. The strategy is intentionally cheap:

1. One DB read returns every non-DEAD ``ProcessRun`` with its
   ``agent_pid`` and ``session_id``.
2. We then walk the local PID chain (``os.getpid() → ppid → … → 1``)
   and stop on the first match — that's the closest live agent.

The closest-match semantics matter for nested cases: if a session A
spawns session B, and a Bash tool inside B calls ``twicc``, the chain
is ``twicc → bash → claude(B) → backend Python → …``. ``agent_pid``
of B is closer than A in the chain, so we resolve to B. Each level
of nesting works the same way.

Returns the resolved ``Session`` (full row, so callers can serialise
the same shape as ``twicc session <ID>``) or ``None`` when no match
is found in the ancestry (e.g. a human running ``twicc`` from a
plain terminal).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextvars import ContextVar

import psutil

logger = logging.getLogger(__name__)

# Out-of-band caller identity. The MCP endpoint (and any future in-backend
# invoker that knows who is calling) sets this before running a command
# in-process; PID ancestry is meaningless there (the "caller" is the backend
# itself). ``None`` = unset → fall back to the PID walk.
forced_session_id: ContextVar[str | None] = ContextVar("twicc_forced_session_id", default=None)


def _walk_ppids() -> Iterator[int]:
    """Yield successive parent PIDs starting at ``os.getpid()`` up to PID 1."""
    pid = os.getpid()
    while pid > 1:
        try:
            ppid = psutil.Process(pid).ppid()
        except psutil.Error:
            # Process gone / permission error: stop the walk.
            return
        if ppid is None or ppid <= 0:
            return
        yield ppid
        pid = ppid


def resolve_current_session():
    """Return the ``Session`` of the closest live agent in the PID ancestry.

    Returns ``None`` when no ``ProcessRun.agent_pid`` matches any
    ancestor — typically the case for a human invoking ``twicc`` from
    a plain shell.

    The caller must have run ``django.setup()`` before this — the
    function does not bootstrap Django itself, to keep cold-start
    paths optional.
    """
    from twicc.agent.states import AgentState
    from twicc.core.models import ProcessRun, Session

    from twicc.mcp.identity import external_caller
    if external_caller.get() is not None:
        return None
    forced = forced_session_id.get()
    if forced is not None:
        return Session.objects.filter(pk=forced).first()

    # One DB read returns every live agent_pid → session_id pair.
    pid_to_session_id = dict(
        ProcessRun.objects.exclude(state=AgentState.DEAD)
        .exclude(agent_pid__isnull=True)
        .values_list("agent_pid", "session_id")
    )
    if not pid_to_session_id:
        return None

    for ppid in _walk_ppids():
        sid = pid_to_session_id.get(ppid)
        if sid is not None:
            try:
                return Session.objects.get(pk=sid)
            except Session.DoesNotExist:
                # ProcessRun outlived the Session row (e.g. session deleted
                # while process still alive). Fall through; nothing else to
                # match in the ancestry.
                return None

    logger.debug(
        "resolve_current_session: no matching agent in PID ancestry "
        "from %d (checked %d candidate PIDs)",
        os.getpid(), len(pid_to_session_id),
    )
    return None


def resolve_spawned_by_filter(value: str | None) -> str | None:
    """Translate a ``--spawned-by`` CLI value into a session_id filter.

    - ``None``  → ``None`` (no filter)
    - ``"self"`` → current session's id (filter to my direct children)
    - ``"parent"`` → current session's ``spawned_by_id`` (filter to the
      sessions my parent spawned — my siblings, myself included). Raises
      ``RuntimeError`` if the current session has no spawner.
    - any other string → use it verbatim as a session_id

    Both keywords resolve via PID ancestry (``resolve_current_session``).
    Any internal failure is wrapped in ``RuntimeError`` so the typer
    wrappers that call this can surface a clean human error + non-zero
    exit code instead of leaking a raw traceback.
    """
    if value is None:
        return None
    if value in ("self", "parent"):
        try:
            session = resolve_current_session()
        except Exception as e:
            raise RuntimeError(
                f"--spawned-by {value}: could not resolve the current "
                f"session: {type(e).__name__}: {e}",
            ) from e
        if session is None:
            raise RuntimeError(
                f"--spawned-by {value}: no TwiCC session found in PID ancestry. "
                f"This flag is only meaningful from inside an active session.",
            )
        if value == "self":
            return session.id
        # value == "parent"
        if session.spawned_by_id is None:
            raise RuntimeError(
                f"--spawned-by parent: current session {session.id!r} has no "
                f"spawned_by — it was not created via `twicc create-session` "
                f"from a parent agent.",
            )
        return session.spawned_by_id
    return value


def resolve_spawn_tree_filter(value: str | None) -> str | None:
    """Translate a ``--spawn-tree`` CLI value into a session_id filter.

    The CLI flag describes the user-facing concept ("the tree containing
    this session"); internally we return the id of that tree's root,
    because that's what the downstream filter
    ``qs.filter(spawn_root_id=...)`` keys on (the DB column name stays
    ``spawn_root`` even though the CLI flag is ``--spawn-tree``).

    - ``None``  → ``None`` (no filter)
    - ``"self"`` → resolve via whoami; returns the current session's
      ``spawn_root_id`` if set, else its own id (the "I am my own root"
      fallback — matches the invariant established by ``twicc topology``).
      Raises ``RuntimeError`` on any failure.
    - any other string → looked up as a session_id and resolved to the
      id of its tree's root (``s.spawn_root_id or s.id``), so the
      downstream filter returns the whole tree regardless of where in
      the tree the caller pointed (any id in the tree works — root,
      middle, or leaf). Unknown ids are returned verbatim — the
      downstream filter then matches nothing, which is the same
      silent-no-match behavior as ``--spawned-by`` / ``--descendants``.

    Any internal failure is wrapped in ``RuntimeError`` so the typer
    wrappers that call this can surface a clean human error + non-zero
    exit code instead of leaking a raw traceback.
    """
    if value is None:
        return None
    if value == "parent":
        raise RuntimeError(
            "--spawn-tree parent is not supported; use --spawn-tree self or "
            "an explicit session_id from the tree, but self is enough as they"
            "all ids from the tree resolve to the same tree.",
        )
    if value == "self":
        try:
            session = resolve_current_session()
        except Exception as e:
            raise RuntimeError(
                "--spawn-tree self: could not resolve the current "
                f"session: {type(e).__name__}: {e}",
            ) from e
        if session is None:
            raise RuntimeError(
                "--spawn-tree self: no TwiCC session found in PID ancestry. "
                "This flag is only meaningful from inside an active session.",
            )
        return session.spawn_root_id or session.id
    from twicc.core.models import Session

    row = Session.objects.filter(pk=value).values("id", "spawn_root_id").first()
    if row is None:
        return value
    return row["spawn_root_id"] or row["id"]


def resolve_descendants_filter(value: str | None) -> set[str] | None:
    """Translate a ``--descendants`` CLI value into a session_id set filter.

    - ``None``  → ``None`` (no filter at all).
    - ``"self"`` → descendants of the current session (resolved via whoami).
    - ``"parent"`` → descendants of the current session's parent (= my
      siblings, their subtrees, and my own subtree; the parent itself is
      excluded). Raises ``RuntimeError`` if the current session has no
      spawner.
    - any other string → looked up as a session_id; an unknown id resolves
      to an empty set (same silent-no-match behavior as ``--spawned-by`` /
      ``--spawn-tree`` for unknown ids).

    The returned set contains the *proper* descendants of the resolved
    target — the target itself is never included. An empty set means
    "the target has no descendants"; callers must treat that as a strict
    match against an empty pool (returns nothing), not as "no filter".

    Algorithm:
    1. Resolve ``target_id`` from ``value``: the current session for
       ``"self"``, its spawner for ``"parent"``, or the explicit value.
    2. Delegate the common lookup and BFS to
       ``core.services.spawn_scope.descendant_ids(target_id)``.
    """
    if value is None:
        return None

    if value in ("self", "parent"):
        try:
            session = resolve_current_session()
        except Exception as e:
            raise RuntimeError(
                f"--descendants {value}: could not resolve the current "
                f"session: {type(e).__name__}: {e}",
            ) from e
        if session is None:
            raise RuntimeError(
                f"--descendants {value}: no TwiCC session found in PID ancestry. "
                f"This flag is only meaningful from inside an active session.",
            )
        if value == "self":
            target_id = session.id
        else:  # value == "parent"
            if session.spawned_by_id is None:
                raise RuntimeError(
                    f"--descendants parent: current session {session.id!r} has "
                    f"no spawned_by — it was not created via "
                    f"`twicc create-session` from a parent agent.",
                )
            target_id = session.spawned_by_id
    else:
        target_id = value

    from twicc.core.services.spawn_scope import descendant_ids

    return descendant_ids(target_id)


def resolve_siblings_filter(value: str | None) -> set[str] | None:
    """Translate a ``--siblings`` CLI value into a session_id set filter.

    The siblings of a target session S are the *other* sessions spawned by
    the same parent (sharing S's ``spawned_by_id``). It is pure: their
    subtrees are NOT included — use ``--descendants`` for whole branches.

    - ``None``  → ``None`` (no filter).
    - ``"self"`` → siblings of the current session (resolved via whoami).
      Raises ``RuntimeError`` if the current session has no spawner: a root
      session has no parent, so the sibling scope is undefined (same spirit
      as ``--spawned-by parent`` / ``--descendants parent`` on a rootless
      session).
    - ``"parent"`` → rejected. ``--siblings`` is inherently relative to a
      node's own parent; "siblings of my parent" (aunts/uncles) is not a
      supported concept and would clash with the "scope relative to parent"
      meaning ``parent`` carries on the other filters.
    - any other string → looked up as a session_id; its siblings. An unknown
      id, or a target that has no spawner (a root, hence no siblings),
      resolves to an empty set — the same silent-no-match behavior as
      ``--descendants`` for unknown ids.

    The target itself is ALWAYS excluded from the returned set (to include
    yourself, use ``--spawned-by parent`` where allowed). An empty set means
    "the target has no siblings"; callers must treat that as a strict match
    against an empty pool (returns nothing), not as "no filter".
    """
    if value is None:
        return None

    from twicc.core.models import Session

    if value == "parent":
        raise RuntimeError(
            "--siblings parent is not supported; siblings are relative to a "
            "node's own parent. Use --siblings self for the current session's "
            "siblings, or an explicit session_id.",
        )

    if value == "self":
        try:
            session = resolve_current_session()
        except Exception as e:
            raise RuntimeError(
                "--siblings self: could not resolve the current "
                f"session: {type(e).__name__}: {e}",
            ) from e
        if session is None:
            raise RuntimeError(
                "--siblings self: no TwiCC session found in PID ancestry. "
                "This flag is only meaningful from inside an active session.",
            )
        if session.spawned_by_id is None:
            raise RuntimeError(
                f"--siblings self: current session {session.id!r} has no "
                f"spawned_by — it is a root session, so it has no siblings.",
            )
        target_id = session.id
        parent_id = session.spawned_by_id
    else:
        row = (
            Session.objects.filter(pk=value)
            .values("id", "spawned_by_id")
            .first()
        )
        if row is None or row["spawned_by_id"] is None:
            # Unknown id, or a root with no parent → no siblings. Silent
            # no-match, consistent with --descendants for unknown ids.
            return set()
        target_id = row["id"]
        parent_id = row["spawned_by_id"]

    return set(
        Session.objects.filter(type="session", spawned_by_id=parent_id)
        .exclude(id=target_id)
        .values_list("id", flat=True)
    )
