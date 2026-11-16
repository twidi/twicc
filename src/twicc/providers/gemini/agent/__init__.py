"""Gemini CLI agent module (ACP-driven sessions)."""

from .agent import GeminiAgent
from .manager import GeminiAgentManager, get_gemini_agent_manager

__all__ = [
    "GeminiAgent",
    "GeminiAgentManager",
    "get_gemini_agent_manager",
]
