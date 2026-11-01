"""Pure, transactional ordering of Claude title decisions and transcript evidence.

The caller serializes database writes, stages coordinator changes in that same
transaction, and calls ``stage.commit`` from its successful on-commit callback.
Provider I/O and transcript reads belong to the caller, outside this module.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Literal, NamedTuple
from uuid import uuid4


class SourceCursor(NamedTuple):
    source_session_id: str
    storage_incarnation: str
    history_epoch: int
    line_num: int


class TitleObservation(NamedTuple):
    cursor: SourceCursor
    target_session_id: str
    kind: Literal["title", "rename_command", "command_success", "command_error"]
    title: str | None
    message_id: str | None
    parent_message_id: str | None


class TitleDecision(NamedTuple):
    session_id: str
    token: str
    title: str
    process_token: str | None


class ReplayRange(NamedTuple):
    start: SourceCursor
    upper: SourceCursor


class ObservationResult(NamedTuple):
    apply: tuple[TitleObservation, ...] = ()
    confirmed: tuple[str, ...] = ()
    replay: tuple[ReplayRange, ...] = ()


class _Dispatch(NamedTuple):
    decision: TitleDecision
    boundary: SourceCursor | None
    acknowledged: bool = False
    bound_initial: bool = False
    baseline_equal: bool = False


class _Session(NamedTuple):
    process_token: str | None = None
    hybrid: bool = False
    decision: TitleDecision | None = None
    phase: str = "queued"
    dispatch: _Dispatch | None = None
    hazard: bool = False
    inflight: TitleDecision | None = None
    source: tuple[str, str, int] | None = None
    retired_sources: frozenset[tuple[str, str, int]] = frozenset()
    floor: int = -1
    protection: SourceCursor | None = None
    upper: int = -1
    effective: TitleObservation | None = None
    evidence: tuple[TitleObservation, ...] = ()
    commands: tuple[TitleObservation, ...] = ()
    replay: ReplayRange | None = None


def _source(cursor: SourceCursor) -> tuple[str, str, int]:
    return cursor[:3]


def _cursor(source: tuple[str, str, int], line: int) -> SourceCursor:
    return SourceCursor(*source, line)


class TitleSyncStage:
    """An immutable-record overlay, with explicit root commit and savepoints."""

    def __init__(self, coordinator, states, version):
        self._coordinator = coordinator
        self._states = states
        self._version = version
        self._closed = False

    def checkpoint(self) -> object:
        return (self, self._states.copy())

    def restore(self, checkpoint: object) -> None:
        owner, states = checkpoint
        if owner is not self or self._closed:
            raise RuntimeError("Checkpoint does not belong to this active stage")
        self._states = states.copy()

    def commit(self) -> None:
        """Publish only after the associated database transaction commits.

        This can run after the staging context exits, as an on-commit callback.
        Concurrent root writers must use the caller's existing write lane.
        """
        coordinator = self._coordinator
        with coordinator._lock:
            if self._closed:
                raise RuntimeError("Stage is already closed")
            if coordinator._version != self._version:
                raise RuntimeError("Concurrent title stage; serialize root writers")
            coordinator._states = self._states.copy()
            coordinator._version += 1
            self._closed = True


class TitleSyncCoordinator:
    """Process-local state. Mutators require ``staged_state()``.

    A hybrid dispatch creates an independent delivery hazard. Neither an ACK
    nor transcript convergence resolves that hazard. Only process retirement
    resolves it with the present evidence contract.
    """

    EVIDENCE_LIMIT = 128

    def __init__(self):
        self._lock = RLock()
        self._states: dict[str, _Session] = {}
        self._version = 0
        self._stage: ContextVar[TitleSyncStage | None] = ContextVar(
            f"claude_title_stage_{id(self)}", default=None,
        )

    @contextmanager
    def staged_state(self):
        existing = self._stage.get()
        if existing is not None:
            checkpoint = existing.checkpoint()
            try:
                yield existing
            except BaseException:
                existing.restore(checkpoint)
                raise
            return
        with self._lock:
            stage = TitleSyncStage(self, self._states.copy(), self._version)
        token = self._stage.set(stage)
        try:
            yield stage
        except BaseException:
            stage._closed = True
            raise
        finally:
            self._stage.reset(token)

    def _states_for_write(self):
        stage = self._stage.get()
        if stage is None or stage._closed:
            raise RuntimeError("Title mutations require an open title stage")
        return stage._states

    def _read(self, session_id):
        stage = self._stage.get()
        states = stage._states if stage is not None else self._states
        return states.get(session_id, _Session())

    def current_decision(self, session_id: str) -> TitleDecision | None:
        with self._lock:
            return self._read(session_id).decision

    def unresolved_evidence_count(self, session_id: str) -> int:
        """Bounded-state diagnostic; excludes the constant-size replay range."""
        with self._lock:
            state = self._read(session_id)
            return len(state.evidence) + len(state.commands)

    def attach_process(self, session_id: str, process_token: str, *, hybrid: bool) -> None:
        with self._lock:
            states = self._states_for_write()
            state = states.get(session_id, _Session())
            if state.process_token == process_token:
                return
            # Old completion tokens never become valid for the replacement.
            states[session_id] = state._replace(
                process_token=process_token, hybrid=hybrid, dispatch=None,
                hazard=False, inflight=None, evidence=(), commands=(), replay=None,
                phase="failed" if state.dispatch else state.phase,
            )

    def accept_decision(self, session_id: str, title: str) -> TitleDecision:
        with self._lock:
            states = self._states_for_write()
            state = states.get(session_id, _Session())
            decision = TitleDecision(session_id, str(uuid4()), title, state.process_token)
            # Preserve an outstanding dispatch/hazard independently of its successor.
            states[session_id] = state._replace(
                decision=decision, phase="queued",
                protection=_cursor(state.source, max(state.floor, state.upper)) if state.source else None,
                dispatch=None if state.dispatch and state.dispatch.acknowledged else state.dispatch,
                evidence=() if state.dispatch and state.dispatch.acknowledged else state.evidence,
            )
            return decision

    def start_dispatch(self, decision: TitleDecision, boundary: SourceCursor | None) -> bool:
        with self._lock:
            states = self._states_for_write()
            state = states.get(decision.session_id, _Session())
            if (state.decision != decision or state.process_token != decision.process_token
                    or state.process_token is None or state.hazard or state.inflight is not None
                    or state.dispatch is not None or state.replay is not None):
                return False
            if boundary is None:
                if state.source is not None or state.hybrid:
                    return False
            else:
                if boundary.source_session_id != decision.session_id:
                    return False
                if state.source is not None and (
                    state.source != _source(boundary)
                    or boundary.line_num < max(state.upper, state.floor)
                ):
                    return False
                state = self._set_source(state, boundary)
            equal = bool(
                boundary is not None and state.effective is not None
                and _source(state.effective.cursor) == _source(boundary)
                and state.effective.cursor.line_num <= boundary.line_num
                and state.effective.title == decision.title
            )
            states[decision.session_id] = state._replace(
                dispatch=_Dispatch(decision, boundary, baseline_equal=equal), protection=boundary,
                hazard=state.hybrid, inflight=decision, phase="dispatched", evidence=(),
            )
            return True

    def cancel_dispatch_before_delivery(self, decision: TitleDecision) -> ObservationResult:
        """Release an exact dispatch only when its caller proves no I/O attempt.

        This is not a failed ACK. The publication worker must call it only
        before permitting a hybrid paste or attempting an SDK control request.
        Once delivery is possible, use acknowledge instead and retain hazards.
        """
        with self._lock:
            states = self._states_for_write()
            state = states.get(decision.session_id, _Session())
            if state.process_token != decision.process_token or state.inflight != decision:
                return ObservationResult()
            if state.dispatch is not None and (
                state.dispatch.decision != decision or state.dispatch.acknowledged
            ):
                return ObservationResult()
            states[decision.session_id] = state._replace(
                dispatch=None, inflight=None, hazard=False, evidence=(),
                phase="queued" if state.decision else "superseded",
            )
            return ObservationResult(replay=(state.replay,) if state.replay else ())

    def bind_initial_source(self, decision: TitleDecision, first_cursor: SourceCursor) -> bool:
        with self._lock:
            states = self._states_for_write()
            state = states.get(decision.session_id, _Session())
            dispatch = state.dispatch
            if (dispatch is None or dispatch.decision != decision or state.decision != decision
                    or state.process_token != decision.process_token
                    or dispatch.boundary is not None or dispatch.bound_initial
                    or first_cursor.source_session_id != decision.session_id
                    or state.source is not None):
                return False
            # The first real record is eligible. No synthetic boundary is made.
            states[decision.session_id] = state._replace(
                source=_source(first_cursor),
                dispatch=dispatch._replace(bound_initial=True),
            )
            return True

    def _set_source(self, state, cursor):
        source = _source(cursor)
        if state.source == source:
            return state
        if source in state.retired_sources:
            return state
        if (state.source is not None and source[:2] == state.source[:2]
                and source[2] < state.source[2]):
            return state
        if state.source is None:
            return state._replace(source=source)
        retired = state.retired_sources | {state.source}
        # Incomparable storage invalidates confirmation, but never delivery risk.
        return state._replace(
            source=source, retired_sources=retired, floor=-1, upper=-1, protection=None,
            effective=None, evidence=(), commands=(), replay=None,
            decision=None, dispatch=None, phase="failed",
        )

    def observe(self, records: tuple[TitleObservation, ...]) -> ObservationResult:
        with self._lock:
            states = self._states_for_write()
            applied, confirmed = [], []
            touched = set()
            for record in records:
                sid = record.target_session_id
                touched.add(sid)
                state = states.get(sid, _Session())
                if state.process_token and record.cursor.source_session_id != sid:
                    continue
                # An unbound create must be bound by the ingestion coordinator.
                if state.dispatch and state.dispatch.boundary is None and not state.dispatch.bound_initial:
                    continue
                state = self._set_source(state, record.cursor)
                if state.source != _source(record.cursor):
                    continue
                line = record.cursor.line_num
                replaying = bool(state.replay and state.replay.start.line_num <= line <= state.replay.upper.line_num)
                if line <= state.floor or (line <= state.upper and not replaying):
                    states[sid] = state
                    continue
                if state.replay and not replaying and not (
                    record.kind == "command_success" and record.title is not None
                ):
                    if line > state.replay.upper.line_num:
                        state = state._replace(replay=state.replay._replace(upper=record.cursor))
                    states[sid] = state._replace(upper=max(state.upper, line))
                    continue
                state = state._replace(upper=max(state.upper, line))
                if replaying:
                    state = state._replace(replay=(
                        ReplayRange(_cursor(state.source, line + 1), state.replay.upper)
                        if line < state.replay.upper.line_num else None
                    ))
                state, result = self._observe_one(state, record)
                applied.extend(result.apply)
                confirmed.extend(result.confirmed)
                states[sid] = state
            return ObservationResult(tuple(applied), tuple(confirmed), tuple(
                states[sid].replay for sid in sorted(touched) if sid in states and states[sid].replay
            ))

    def _observe_one(self, state, record):
        dispatch = state.dispatch
        if record.kind == "rename_command":
            commands = tuple(item for item in state.commands if item.message_id != record.message_id)
            state = state._replace(commands=commands + (record,))
        elif record.kind in ("command_success", "command_error"):
            parent = next((item for item in state.commands if item.message_id == record.parent_message_id), None)
            state = state._replace(commands=tuple(
                item for item in state.commands if item.message_id != record.parent_message_id
            ))
            value = record.title if record.title is not None else parent.title if parent else None
            if (record.kind == "command_success" and value is not None
                    and not (state.protection is not None
                             and _source(state.protection) == _source(record.cursor)
                             and record.cursor.line_num <= state.protection.line_num)):
                effective = record._replace(title=value)
                # Explicit outcomes are authoritative even during uncertain own delivery.
                state = state._replace(
                    decision=None, phase="superseded", evidence=(), replay=None,
                    floor=record.cursor.line_num, effective=effective,
                )
                return state, ObservationResult((effective,))
        elif record.kind == "title" and record.title is not None:
            # Provider state is separate from accepted database state. Preserve
            # it so a safe SDK retry can recognize a real equal-title no-op.
            state = state._replace(effective=record)
            if (dispatch is not None and dispatch.boundary is not None
                    and record.cursor.line_num <= dispatch.boundary.line_num
                    and record.title != dispatch.decision.title):
                # Capturing a file boundary does not ingest its complete suffix.
                # Late ingestion can invalidate the earlier equal-title baseline.
                dispatch = dispatch._replace(baseline_equal=False)
                state = state._replace(dispatch=dispatch)
            if dispatch is None and state.decision is None:
                return state._replace(
                    floor=record.cursor.line_num, effective=record,
                ), ObservationResult((record,))
            if dispatch is not None:
                eligible = (
                    dispatch.bound_initial if dispatch.boundary is None else
                    _source(record.cursor) == _source(dispatch.boundary)
                    and record.cursor.line_num > dispatch.boundary.line_num
                )
                if eligible:
                    state = state._replace(evidence=state.evidence + (record,))
                    if dispatch.acknowledged and not state.hybrid:
                        return self._resolve_sdk(state)
        if len(state.evidence) + len(state.commands) > self.EVIDENCE_LIMIT:
            unresolved = state.evidence + state.commands
            start = min(unresolved, key=lambda item: item.cursor.line_num).cursor
            upper = _cursor(state.source, state.upper)
            state = state._replace(evidence=(), commands=(), replay=ReplayRange(start, upper))
        return state, ObservationResult()

    def _resolve_sdk(self, state, *, noop_boundary=None):
        dispatch = state.dispatch
        match = next((item for item in state.evidence if item.title == dispatch.decision.title), None)
        noop = (
            match is None and dispatch.baseline_equal and noop_boundary is not None
            and dispatch.boundary is not None
            and _source(noop_boundary) == _source(dispatch.boundary)
            and noop_boundary.line_num >= dispatch.boundary.line_num
            and state.replay is None
            and not any(item.title != dispatch.decision.title
                        and item.cursor.line_num <= noop_boundary.line_num for item in state.evidence)
        )
        if match is None and not noop:
            return state, ObservationResult()
        floor = match.cursor.line_num if match is not None else dispatch.boundary.line_num
        later = tuple(item for item in state.evidence if item.cursor.line_num > floor)
        # A newer queued decision keeps protection until its own publication.
        current = state.decision == dispatch.decision
        state = state._replace(
            decision=None if current else state.decision,
            dispatch=None, phase="confirmed" if current else "queued",
            floor=max(state.floor, floor), evidence=(),
            effective=match if match is not None else state.effective,
        )
        applied = ()
        if current and later:
            applied = later
            state = state._replace(floor=later[-1].cursor.line_num, effective=later[-1])
        return state, ObservationResult(applied, (dispatch.decision.token,) if current else ())

    def acknowledge(self, decision: TitleDecision, *, success: bool,
                    boundary: SourceCursor | None) -> ObservationResult:
        with self._lock:
            states = self._states_for_write()
            state = states.get(decision.session_id, _Session())
            if state.process_token != decision.process_token:
                return ObservationResult()
            if state.inflight == decision:
                state = state._replace(inflight=None)
                states[decision.session_id] = state
            dispatch = state.dispatch
            if dispatch is None or dispatch.decision != decision:
                return ObservationResult()
            if boundary is not None and (
                state.source != _source(boundary)
                or dispatch.boundary is not None and boundary.line_num < dispatch.boundary.line_num
            ):
                return ObservationResult()
            if not success:
                states[decision.session_id] = state._replace(
                    dispatch=None, phase="failed", evidence=(),
                )
                return ObservationResult(replay=(state.replay,) if state.replay else ())
            if state.decision != decision:
                states[decision.session_id] = state._replace(
                    dispatch=None, evidence=(),
                    phase="queued" if state.decision else "superseded",
                )
                return ObservationResult(replay=(state.replay,) if state.replay else ())
            state = state._replace(dispatch=dispatch._replace(acknowledged=True), phase="acknowledged")
            result = ObservationResult()
            if not state.hybrid:
                state, result = self._resolve_sdk(state, noop_boundary=boundary)
            states[decision.session_id] = state
            return result._replace(replay=(state.replay,) if state.replay else ())

    def retire_process(self, session_id: str, process_token: str) -> TitleDecision | None:
        with self._lock:
            states = self._states_for_write()
            state = states.get(session_id, _Session())
            if state.process_token != process_token:
                return None
            states[session_id] = state._replace(
                process_token=None, dispatch=None, inflight=None, hazard=False, evidence=(), commands=(),
            )
            return state.decision
