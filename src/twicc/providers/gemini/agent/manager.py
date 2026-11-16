"""Agent manager for Gemini CLI sessions."""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import sync_to_async

from twicc.agent.base_agent import BaseAgent
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.exceptions import SendDeliveryError
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message
from twicc.providers.helpers import AgentSettings

from .agent import GeminiAgent

logger = logging.getLogger(__name__)

def _refuse_unsupported(*, ephemeral: bool = False, images=None, documents=None) -> None:
    """Refuse what the provider does not deliver, before anything starts.

    Composer attachments go through the attachment plan (delivered as files);
    the legacy inline ``images`` / ``documents`` payloads have no Gemini path.
    """
    if ephemeral:
        raise SendDeliveryError("Ephemeral runs are not available for Gemini", code="ephemeral_unsupported",
                                ui_message=ui_message("backend.gemini.ephemeralUnsupported"))
    if images or documents:
        raise SendDeliveryError("Inline attachments are not available for Gemini: attach them as files", code="attachments_unsupported",
                                ui_message=ui_message("backend.gemini.attachmentsUnsupported"))


def _content_kwargs(content) -> dict:
    return {"content": content} if content is not None else {}


class GeminiAgentManager(BaseAgentManager):
    """Owns the live ``gemini --acp`` processes, one per running session."""

    provider = Provider.GEMINI

    async def _resolve_launch_settings(self, project_id: str, settings: AgentSettings) -> tuple[AgentSettings, bool]:
        """Clamp the approval mode in untrusted projects; return ``(settings, trusted)``."""
        from twicc.core.services.trust import clamp_permission_mode_for_untrusted, project_is_untrusted

        untrusted = await sync_to_async(project_is_untrusted)(project_id)
        if untrusted:
            mode = await sync_to_async(clamp_permission_mode_for_untrusted)(Provider.GEMINI, settings.permission_mode)
            settings = settings._replace(permission_mode=mode)
        return settings, not untrusted

    @staticmethod
    def _cli_model(settings: AgentSettings) -> str | None:
        from twicc.providers.helpers import get_provider_helpers

        return get_provider_helpers(Provider.GEMINI).resolve_cli_model(settings.selected_model)

    async def _create_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        *,
        resume: bool,
        settings: AgentSettings,
        ephemeral: bool = False,
        prepared_attachments=None,
        initial_text: str = "",
        **kwargs: Any,
    ) -> GeminiAgent:
        """Spawn ``gemini --acp`` and open the session.

        A new session gets its canonical id from ``session/new`` (the draft
        ``session_id`` is only a frontend key); a resume loads the canonical
        ``session_id``. Attachments of a new session's first message
        (``prepared_attachments``) are finished here for the canonical id,
        before ``session_bound``, and handed to the agent's first turn.
        """
        _refuse_unsupported(ephemeral=ephemeral)
        if resume and prepared_attachments is not None:
            raise ValueError("prepared_attachments are only finished by a new session, never by a resume")
        settings, trusted = await self._resolve_launch_settings(project_id, settings)
        agent = GeminiAgent(session_id, project_id, cwd, settings, model=self._cli_model(settings), trusted=trusted)
        await agent.open(resume=resume)
        if prepared_attachments is not None:
            from twicc.core.services.attachments import committer

            try:
                agent._initial_content = committer.finish_attachments(
                    prepared_attachments, session_id=agent.session_id, text=initial_text,
                )
            except BaseException:
                await agent.interrupt_or_kill("startup-failed")
                raise
        return agent

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        settings: AgentSettings,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
        ephemeral: bool = False,
        ephemeral_admission=None,
        attachment_plan=None,
        completion_token: str | None = None,
        completion_cursor: int | None = None,
    ) -> str:
        """Start a brand-new Gemini session; return its canonical id."""
        _refuse_unsupported(ephemeral=ephemeral, images=images, documents=documents)
        has_attachments = attachment_plan is not None and bool(attachment_plan.entries)
        if not text and not has_attachments:
            raise SendDeliveryError("Cannot start a new agent without a message", code="send_failed",
                                    ui_message=ui_message("backend.gemini.messageRequired"))
        prepared = None
        if has_attachments:
            # The canonical id only exists once ``session/new`` returns: prepare
            # now, finish in ``_create_agent`` (same flow as Codex).
            self._check_ephemeral_readonly(session_id, ephemeral_admission)
            prepared = await self._prepare_attachment_plan(attachment_plan, session_id=None)
        try:
            async with self._lock:
                self._check_ephemeral_readonly(session_id, ephemeral_admission)
                return await self._start_agent(
                    session_id, project_id, cwd, text, resume=False, settings=settings,
                    ephemeral_admission=ephemeral_admission,
                    **({"prepared_attachments": prepared} if prepared is not None else {}),
                )
        finally:
            if prepared is not None:
                from twicc.core.services.attachments import committer

                committer.discard_prepared(prepared)

    async def send_to_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        settings: AgentSettings,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
        attachment_plan=None,
        completion_token: str | None = None,
        completion_cursor: int | None = None,
        completion_admission: dict | None = None,
        **_kwargs: Any,
    ) -> bool:
        """Deliver a message (or only new settings when there is nothing to send).

        - no live agent: resume the session with ``session/load``;
        - idle agent: apply the settings, then prompt;
        - working agent: queue the message for right after the current turn;
        - starting agent: refuse (the frontend retries).

        Returns ``False`` for a settings-only update.
        """
        _refuse_unsupported(images=images, documents=documents)
        self._check_ephemeral_readonly(session_id)
        content = None
        if attachment_plan is not None and attachment_plan.entries:
            content = await self._commit_attachment_plan(attachment_plan, session_id=session_id, text=text)
        has_input = bool(text) or content is not None
        async with self._lock:
            self._check_ephemeral_readonly(session_id)
            agent = self._agents.get(session_id)
            if agent is not None and agent.state == AgentState.DEAD:
                del self._agents[session_id]
                agent = None

            if agent is not None:
                assert isinstance(agent, GeminiAgent)
                if agent.state == AgentState.STARTING:
                    raise SendDeliveryError(
                        f"Cannot send message: agent is in state {agent.state}", code="agent_starting",
                        ui_message=ui_message("backend.gemini.agentStateCannotSend", {"state": str(agent.state)}),
                    )
                if agent.state == AgentState.USER_TURN:
                    settings, _trusted = await self._resolve_launch_settings(project_id, settings)
                    await agent.apply_agent_settings(settings, model=self._cli_model(settings))
                if not has_input:
                    return False
                return await agent.send(text, **_content_kwargs(content))

            if not has_input:
                raise SendDeliveryError("Cannot start a new agent without a message", code="send_failed",
                                        ui_message=ui_message("backend.gemini.messageRequired"))
            await self._start_agent(
                session_id, project_id, cwd, text, resume=True, settings=settings, **_content_kwargs(content),
            )
            return True

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        return self._state_based_timeout(agent, current_time)


def get_gemini_agent_manager() -> GeminiAgentManager:
    """The registry's Gemini manager (single instance per process)."""
    from twicc.agent.registry import get_agent_manager_registry

    return get_agent_manager_registry().get(Provider.GEMINI)
