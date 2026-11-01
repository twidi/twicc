"""JSONL-derived state signals for hybrid sessions.

Produced by the Claude Code sessions watcher (one per ingest batch with
fresh lines, hybrid sessions only) and consumed by
``ClaudeCodeAgentManager.handle_hybrid_jsonl_signals``.

Also home to ``HybridHookOutcome``, the routing verdict the manager returns
to the hooks watcher for each event file.
"""

from enum import StrEnum
from typing import NamedTuple

from twicc.providers.claude_code.title_sync import TitleObservation

# Ordered title command evidence has a separate channel from boolean turn signals.
HybridRenameEvidence = TitleObservation


class HybridHookOutcome(StrEnum):
    """What the hooks watcher should do with an event file after routing.

    UNHANDLED: stale event (no live hybrid agent) or unrouted event name —
        delete the file, never retry.
    HANDLED: routed, but the file is no longer needed — delete it.
    OWNED: routed and the agent registered a pending request keyed on the
        event's nonce — the drop file must STAY on disk (the agent deletes
        it at resolution/clear/death; the boot scan re-feeds survivors after
        a TwiCC restart, restoring the GUI-answer widget).
    """

    UNHANDLED = "unhandled"
    HANDLED = "handled"
    OWNED = "owned"


class HybridJsonlSignals(NamedTuple):
    # A real user prompt landed (computed ItemKind.USER_MESSAGE — excludes
    # meta lines, tool results, system XML and slash-command echoes) → the
    # agent entered a turn.
    user_message: bool = False
    # A ``system``/``turn_duration`` line landed, or an interruption marker
    # ("[Request interrupted by user…]") — either way the turn is over.
    turn_end: bool = False
    # New tool_result content landed → any pending TUI prompt was answered.
    tool_results: bool = False
    # A slash-command echo landed (USER_MESSAGE carrying <command-name>):
    # a local command was submitted; its <local-command-stdout> ack follows.
    # Only relevant when no real turn is running — see the agent handler.
    command_message: bool = False
    # A <local-command-stdout> ack landed. It ends a command-started "turn"
    # ONLY; during a real assistant turn it is plain noise (a local command
    # executes inline mid-turn) and MUST NOT end the turn nor reap pendings
    # (a /rename ack once denied a live permission prompt that way).
    local_command_ack: bool = False
