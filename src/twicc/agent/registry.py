"""
Registry of provider-specific agent managers.

Aggregates per-provider managers (Claude Code, Codex, ...) and exposes a
unified interface for callers that don't need to know which provider owns a
given session. Provider-specific operations (e.g. ``send_to_session`` with
Claude-only settings) still go through the provider's own manager directly,
obtained via ``registry.get(<provider_key>)``.

Following the same declarative pattern as
``twicc.asgi.WSConsumer.PROVIDER_HANDLERS``, the set of providers is declared
statically as a class attribute. Each provider's manager is instantiated once
when the registry singleton is created.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import ClassVar

from twicc.core.enums import Provider
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.codex.agent.manager import CodexAgentManager

from .base_manager import BaseAgentManager, BroadcastCallback
from .states import AgentInfo, AgentState

logger = logging.getLogger(__name__)


class AgentManagerRegistry:
    """Singleton-of-singletons holding one ``BaseAgentManager`` per provider.

    Mirrors the public surface of ``BaseAgentManager`` for the operations
    that make sense across providers (``get_active_agents``, ``kill_agent``,
    ``touch_agent_activity``, ``set_broadcast_callback``, ``shutdown``), so
    callers can use the registry as a drop-in for "any session, any provider"
    use cases.
    """

    PROVIDER_MANAGERS: ClassVar[dict[Provider, type[BaseAgentManager]]] = {
        Provider.CLAUDE_CODE: ClaudeCodeAgentManager,
        Provider.CODEX: CodexAgentManager,
    }

    def __init__(self) -> None:
        self._managers: dict[Provider, BaseAgentManager] = {
            key: cls() for key, cls in self.PROVIDER_MANAGERS.items()
        }

    # ------------------------------------------------------------------
    # Direct provider access
    # ------------------------------------------------------------------

    def get(self, provider: Provider) -> BaseAgentManager:
        """Return the manager for ``provider``. Raises ``KeyError`` if unknown."""
        return self._managers[provider]

    def items(self) -> list[tuple[Provider, BaseAgentManager]]:
        """Return ``(provider, manager)`` pairs for every registered provider."""
        return list(self._managers.items())

    @asynccontextmanager
    async def work_dirs_cleanup_guard(self) -> AsyncIterator[None]:
        """Exclude agent starts/resumes while their work folders are cleaned."""
        async with AsyncExitStack() as stack:
            for manager in self._managers.values():
                await stack.enter_async_context(manager._lock)
            yield

    def get_active_work_dirs(self) -> list[str]:
        """Include work folders granted before a new session reaches the DB."""
        return [
            path
            for manager in self._managers.values()
            for agent in list(manager._agents.values())
            if agent.state != AgentState.DEAD
            for path in agent._work_dirs
        ]

    # ------------------------------------------------------------------
    # Aggregate operations (mirror BaseAgentManager API)
    # ------------------------------------------------------------------

    def get_active_agents(self) -> list[AgentInfo]:
        """Snapshot of every non-dead agent across every provider."""
        return [
            info
            for mgr in self._managers.values()
            for info in mgr.get_active_agents()
        ]

    def get_agent_info(self, session_id: str) -> AgentInfo | None:
        """Look up a non-dead agent across all providers; first match wins.

        DEAD agents are filtered out — once an agent has stopped, it has no
        useful runtime state to expose, and dispatching control operations to
        it is a no-op.
        """
        for mgr in self._managers.values():
            info = mgr.get_agent_info(session_id)
            if info is not None and info.state != AgentState.DEAD:
                return info
        return None

    def find_manager_for_session(self, session_id: str) -> BaseAgentManager | None:
        """Return the manager that currently owns a non-dead ``session_id``.

        Session IDs are globally unique across providers, so the first match
        is also the only match. DEAD agents are skipped — see
        ``get_agent_info`` for the rationale.
        """
        for mgr in self._managers.values():
            info = mgr.get_agent_info(session_id)
            if info is not None and info.state != AgentState.DEAD:
                return mgr
        return None

    async def kill_agent(self, session_id: str, reason: str = "manual") -> bool:
        """Kill an agent regardless of which provider owns it."""
        mgr = self.find_manager_for_session(session_id)
        if mgr is None:
            return False
        return await mgr.kill_agent(session_id, reason=reason)

    async def hard_kill_agent(self, session_id: str, reason: str = "force") -> bool:
        """Hard-kill an agent (force, no grace) regardless of its provider."""
        mgr = self.find_manager_for_session(session_id)
        if mgr is None:
            return False
        return await mgr.hard_kill_agent(session_id, reason=reason)

    async def interrupt_agent(self, session_id: str) -> bool:
        """Interrupt an agent's current turn (keep it alive) regardless of provider."""
        mgr = self.find_manager_for_session(session_id)
        if mgr is None:
            return False
        return await mgr.interrupt_agent(session_id)

    def touch_agent_activity(self, session_id: str) -> bool:
        """Refresh ``last_activity`` regardless of which provider owns the session."""
        mgr = self.find_manager_for_session(session_id)
        if mgr is None:
            return False
        return mgr.touch_agent_activity(session_id)

    def set_session_hidden(self, session_id: str, hidden: bool) -> bool:
        """Push a ``hidden`` flip to the live agent, whichever provider owns it."""
        mgr = self.find_manager_for_session(session_id)
        if mgr is None:
            return False
        return mgr.set_session_hidden(session_id, hidden)

    def set_broadcast_callback(self, callback: BroadcastCallback) -> None:
        """Register the same broadcast callback on every manager."""
        for mgr in self._managers.values():
            mgr.set_broadcast_callback(callback)


_registry: AgentManagerRegistry | None = None


def get_agent_manager_registry() -> AgentManagerRegistry:
    """Return the global ``AgentManagerRegistry`` singleton (lazy-initialized)."""
    global _registry
    if _registry is None:
        _registry = AgentManagerRegistry()
    return _registry
