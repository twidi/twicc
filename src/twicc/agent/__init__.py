"""
Agent module for managing Claude processes.

This module provides the infrastructure for running Claude processes that enable
bidirectional communication with Claude Code via the SDK.
"""

from .manager import ProcessManager, get_process_manager, shutdown_process_manager
from .process import ClaudeProcess
from .states import (
    ProcessInfo,
    ProcessState,
    format_bytes,
    get_process_memory,
    serialize_process_info,
)

__all__ = [
    "ClaudeProcess",
    "ProcessInfo",
    "ProcessManager",
    "ProcessState",
    "format_bytes",
    "get_process_manager",
    "get_process_memory",
    "serialize_process_info",
    "shutdown_process_manager",
]
