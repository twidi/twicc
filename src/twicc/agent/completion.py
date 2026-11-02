"""Pure logical-turn completion decisions using caller-supplied monotonic time."""

from uuid import uuid4

from twicc.agent.completion_types import CompletionCandidate, CompletionSnapshot, NativeMessageRef

COMPLETION_DELAY_SECONDS = 1.0


class CompletionController:
    def __init__(self, *, run_id: str, delay_seconds: float = COMPLETION_DELAY_SECONDS) -> None:
        self.run_id = run_id
        self.delay_seconds = delay_seconds
        self._snapshot = CompletionSnapshot("", run_id, 0, 0, None, (), None)
        self._ended = False
        self._holds: frozenset[str] = frozenset()
        self._awaiting = False
        self._inputs: dict[str, str] = {}
        self._input_end_serials: dict[str, int] = {}
        self._end_serial = 0
        self._candidate: CompletionCandidate | None = None

    def snapshot(self) -> CompletionSnapshot:
        return self._snapshot

    @property
    def is_open(self) -> bool:
        return self._snapshot.ordinal > 0 and self._snapshot.outcome is None

    def _changed(self, now: float) -> None:
        if not self.is_open:
            return
        self._snapshot = self._snapshot._replace(revision=self._snapshot.revision + 1)
        self._candidate = None
        if self._ended and not self._holds and not self._awaiting and not any(
            status in ("pending", "uncertain") for status in self._inputs.values()
        ):
            self._candidate = CompletionCandidate(
                self._snapshot.turn_id, self._snapshot.revision, now + self.delay_seconds,
            )

    def activity(self, now: float, *, first_ref: NativeMessageRef | None = None) -> None:
        if (self.is_open and not self._ended
                and (first_ref is None or self._snapshot.first_ref is not None)):
            # Token deltas keep the same active fact. They do not create a
            # revision or a writer admission until eligibility or identity changes.
            return
        if not self.is_open:
            self._snapshot = CompletionSnapshot(
                str(uuid4()), self.run_id, self._snapshot.ordinal + 1, 0, None, (), None,
            )
        self._ended = False
        if first_ref is not None and self._snapshot.first_ref is None:
            self._snapshot = self._snapshot._replace(first_ref=first_ref)
        self._changed(now)

    def provider_ended(self, now: float, *, final_ref: NativeMessageRef | None = None) -> None:
        if not self.is_open:
            return
        refs = self._snapshot.final_refs
        if self._ended and (final_ref is None or final_ref in refs):
            return
        if final_ref is not None and final_ref not in refs:
            self._snapshot = self._snapshot._replace(final_refs=(*refs, final_ref))
        self._ended = True
        self._end_serial += 1
        self._changed(now)

    def set_holds(self, holds: frozenset[str], now: float) -> None:
        if self._holds != holds:
            self._holds = holds
            self._changed(now)

    def set_awaiting(self, awaiting: bool, now: float) -> None:
        if self._awaiting != awaiting:
            self._awaiting = awaiting
            self._changed(now)

    def input_pending(self, token: str, now: float) -> None:
        if self._inputs.get(token) == "pending":
            return
        self._inputs[token] = "pending"
        self._input_end_serials[token] = self._end_serial
        self._changed(now)

    def input_settled(self, token: str, status: str, now: float) -> None:
        if status not in ("accepted", "rejected", "uncertain"):
            raise ValueError("Invalid input delivery status")
        if token not in self._inputs or self._inputs[token] == status:
            return
        self._inputs[token] = status
        if status == "accepted" and self._input_end_serials[token] == self._end_serial:
            # Acceptance invalidates only a result from before this admission.
            # A fast result can arrive before durable acceptance returns.
            self._ended = False
        self._changed(now)

    def candidate(self) -> CompletionCandidate | None:
        return self._candidate

    def claim_due(self, candidate: CompletionCandidate, now: float) -> CompletionSnapshot | None:
        if self._candidate != candidate or now < candidate.deadline or not self.is_open:
            return None
        return self.terminate("normal", now)

    def terminate(self, outcome: str, now: float) -> CompletionSnapshot | None:
        if outcome not in ("normal", "ended", "provider_error"):
            raise ValueError("Invalid completion outcome")
        if not self.is_open:
            return None
        self._candidate = None
        self._snapshot = self._snapshot._replace(revision=self._snapshot.revision + 1, outcome=outcome)
        # Future input belongs to the next interval. Closed identities remain immutable.
        self._inputs = {}
        self._input_end_serials = {}
        return self._snapshot
