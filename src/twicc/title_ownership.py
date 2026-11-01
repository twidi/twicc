"""Runtime authorization for automatic titles, scoped to one local agent run."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, NamedTuple

from twicc.agent.states import AgentState
from twicc.core.enums import Provider

if TYPE_CHECKING:
    from twicc.agent.base_agent import BaseAgent
    from twicc.agent.base_manager import BaseAgentManager


class TitleCheckRun(NamedTuple):
    session_id: str
    provider: Provider
    manager: BaseAgentManager
    agent: BaseAgent
    final: bool = False


_UNSET = object()
_current_run = ContextVar("automatic_title_run", default=_UNSET)


def capture_title_run(session_id: str, provider: Provider | None = None) -> TitleCheckRun | None:
    """Only a live local registration grants ordinary automatic generation."""
    from twicc.agent.registry import get_agent_manager_registry

    registry = get_agent_manager_registry()
    for candidate in (provider,) if provider is not None else Provider:
        manager = registry.get(candidate)
        agent = getattr(manager, "_agents", {}).get(session_id)
        if agent is not None and agent.state != AgentState.DEAD and not getattr(agent, "ephemeral", False):
            return TitleCheckRun(session_id, candidate, manager, agent)
    return None


def run_for_check(session_id: str, provider: Provider | None = None) -> TitleCheckRun | None:
    """Keep queued requests bound, including an explicit absence of authority."""
    run = _current_run.get()
    return capture_title_run(session_id, provider) if run is _UNSET else run


def title_run_is_current(run: TitleCheckRun | None) -> bool:
    if run is None:
        return False
    current = run.manager._agents.get(run.session_id)
    if run.final:
        return (
            current is None
            and getattr(run.manager, "_final_title_checks", {}).get(run.session_id) is run
        )
    return current is run.agent and run.agent.state != AgentState.DEAD


def authorize_final_title_check(manager, agent) -> TitleCheckRun | None:
    """Reserve a final check before DEAD cleanup; a new registration revokes it."""
    if manager._agents.get(agent.session_id) is not agent or agent.state != AgentState.DEAD:
        return None
    run = TitleCheckRun(agent.session_id, agent.provider, manager, agent, True)
    manager._final_title_checks[agent.session_id] = run
    return run


def release_final_title_check(run: TitleCheckRun | None) -> None:
    if run is not None and run.final and run.manager._final_title_checks.get(run.session_id) is run:
        run.manager._final_title_checks.pop(run.session_id)


@contextmanager
def title_check_scope(run: TitleCheckRun | None):
    token = _current_run.set(run)
    try:
        yield
    finally:
        _current_run.reset(token)
