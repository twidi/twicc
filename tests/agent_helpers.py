"""Initialized agent contracts for lifecycle tests without provider processes."""

from twicc.agent.base_agent import BaseAgent
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings


class InitializedAgent(BaseAgent):
    provider = Provider.CODEX

    def __init__(self, session_id, project_id, *, provider=Provider.CODEX):
        super().__init__(session_id, project_id, '/tmp', AgentSettings())
        self.provider = provider

    def get_pid(self):
        return None
