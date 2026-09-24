"""Per-session context injection — the ``<twicc:context>`` channel.

A small, provider-agnostic facility for handing a running agent ``key: value``
facts it cannot otherwise learn at launch, by folding them into its next user
message as a single ``<twicc:context>`` block. Today the only use is Codex's
canonical ``session_id``: Codex mints it inside ``thread_start``, *after* the
system-prompt addendum has been frozen, so it cannot live in the addendum the
way it does for Claude Code (which knows its id up front).

The facility is generic across providers, in four parts:

1. **Registry** (:func:`inject_context`) — any code, at any time, queues fields
   for a session. Same in-memory, keyed-by-session-id shape as
   :mod:`twicc.pending_titles` & friends; touched only from the agent event
   loop, so no locking.

2. **Send-time fold** (:func:`apply_pending_context`) — each provider calls
   this where it composes an outgoing user message, to prepend one merged
   ``<twicc:context>`` block built from everything queued, then clear it
   (one-shot). Each provider folds in the one method every outgoing user
   message passes through, so a queued injection lands on the next message
   whether it opens a fresh turn or is sent mid-turn (steer / queued). The one
   exception is a slash-command message (``/compact`` & co.): prepending a block
   would push the command off the start of the text and the CLI would treat the
   whole thing as plain text, so the fold is skipped and the fields stay queued
   for the next non-command message. Current
   integration points — add one when a new provider lands:

     - Codex:       ``CodexAgent._build_turn_input`` (a normal turn and a steer)
     - Claude Code: ``ClaudeCodeAgent._build_query_prompt`` (start and send)

   One-shot is enough: the block lands on the next user message only; the agent
   then carries it in its own conversation history (and, for Codex, its
   replayed rollout), and the ``twicc-whoami`` skill stays the runtime fallback
   — so the block is not repeated on every turn. :func:`clear_context` runs from
   the shared agent teardown (``BaseAgent._transition_to_dead``) to drop
   anything an agent that died before its first message never consumed.

3. **Ingestion strip** (:func:`strip_context_blocks_in_place`) — the watcher /
   batch compute scrubs the block from the copy TwiCC persists
   (``SessionItem.content``) in ``providers/compute_base.py``, generically for
   every provider, so the UI, full-text search, session title and message
   browser never show it. The agent still saw the block in its turn input and
   replayed rollout; only the stored copy is cleaned.

4. **Environment reconciliation** (:func:`seed_baseline` / :func:`reset_baseline`
   / :func:`reconcile`) — the dynamic ``### Context`` block of the start-time
   addendum is only a snapshot; parts of it drift after launch (agent settings,
   workspaces, project name, interaction mode, hidden, annotations). Each agent
   keeps an in-memory *baseline* of the snapshot it has already seen; at every
   outgoing user message it recomputes the snapshot from the source of truth and
   queues only the delta (via :func:`inject_context_fields`, with agent-settings
   keys prefixed ``Agent settings: `` so a flat ``key: value`` channel mirrors
   the addendum's indented ``agent settings (resolved):`` block). A fresh start
   seeds the baseline (so the first message has no delta — the values are
   already in the addendum); a resume resets it, so the whole snapshot is
   re-stated — the frozen addendum reflects launch-time state and is never
   re-sent, so we cannot otherwise know what the resumed agent still believes.
   The recompute runs at the same chokepoint as the send-time fold
   (``BaseAgent._reconcile_context`` called from ``_build_turn_input`` /
   ``_build_query_prompt``); the strip in part 3 scrubs the result from the
   stored copy like any other block.

The block carries arbitrary ``key: value`` lines, so a new context field is a
one-line change at the injection site and needs no change here.

A second, simpler block shares part 3's strip: :func:`apply_goal_instruction`
*appends* a static ``<twicc:instruction>`` block to a ``/goal <args>`` slash
command (Claude Code only today) telling the agent that a later goal
complements/overrides the earlier one(s). It folds in at the same send-time
chokepoint and is scrubbed by the same ingestion strip — but, being appended
inside the command's args rather than prepended, it is matched unanchored.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

logger = logging.getLogger(__name__)

CONTEXT_TAG_NAME = "twicc:context"
INSTRUCTION_TAG_NAME = "twicc:instruction"

# Cheap membership pre-checks callers run before the (rarely-needed) strip, so
# the common case — an item without the tag — never pays for a regex or a walk.
CONTEXT_BLOCK_MARKER = f"<{CONTEXT_TAG_NAME}>"
INSTRUCTION_BLOCK_MARKER = f"<{INSTRUCTION_TAG_NAME}>"

# Matches a leading ``<twicc:context> ... </twicc:context>`` block plus the
# whitespace that separates it from the real message. Anchored at the start so
# a tag the user happens to type mid-message is never touched; DOTALL so a
# multi-line block (one ``key: value`` per line) is captured whole; non-greedy
# so back-to-back blocks are removed one at a time rather than as one span.
_CONTEXT_BLOCK_RE = re.compile(
    rf"^\s*<{re.escape(CONTEXT_TAG_NAME)}>.*?</{re.escape(CONTEXT_TAG_NAME)}>\s*",
    re.DOTALL,
)

# Matches the ``<twicc:instruction> ... </twicc:instruction>`` block appended to
# a ``/goal`` command, plus the whitespace that joins it to the command. Unlike
# the context block this one is *not* anchored: the CLI folds it into the
# command's ``<command-args>``, so by ingestion time it sits mid-string, not at
# the start. DOTALL + non-greedy for the same reasons as above.
_INSTRUCTION_BLOCK_RE = re.compile(
    rf"\s*<{re.escape(INSTRUCTION_TAG_NAME)}>.*?</{re.escape(INSTRUCTION_TAG_NAME)}>\s*",
    re.DOTALL,
)


# --------------------------------------------------------------------------
# Block format (shared by the injection side and the ingestion strip)
# --------------------------------------------------------------------------

def build_context_block(fields: Mapping[str, str]) -> str:
    """Compose the ``<twicc:context>`` block for ``fields`` (insertion order kept).

    Returns the block with no trailing separator — the caller joins it to the
    user text with its own spacing.
    """
    lines = "\n".join(f"{key}: {value}" for key, value in fields.items())
    return f"<{CONTEXT_TAG_NAME}>\n{lines}\n</{CONTEXT_TAG_NAME}>"


def strip_context_blocks(text: str) -> str:
    """Remove the internal TwiCC blocks from ``text`` (no-op when absent).

    Two blocks are scrubbed so neither reaches the UI, full-text search, session
    title or message browser: the leading ``<twicc:context>`` block, and the
    ``<twicc:instruction>`` block appended to ``/goal`` commands (which, riding
    inside the command args, can sit anywhere in the string — hence unanchored).
    """
    if CONTEXT_BLOCK_MARKER in text:
        text = _CONTEXT_BLOCK_RE.sub("", text)
    if INSTRUCTION_BLOCK_MARKER in text:
        text = _INSTRUCTION_BLOCK_RE.sub("", text)
    return text


def strip_context_blocks_in_place(parsed: object) -> bool:
    """Strip the internal TwiCC blocks from every string within a parsed JSONL item.

    Walks ``parsed`` (the dict/list decoded from one JSONL line) and rewrites
    any string value carrying a ``<twicc:context>`` (leading) or
    ``<twicc:instruction>`` (anywhere) block. Provider-agnostic: it does not
    need to know which field carries the user text — the regexes only bite the
    blocks themselves. Returns ``True`` when anything changed, so the caller
    re-serialises ``SessionItem.content``.
    """
    changed = False

    def transform(value: object) -> object:
        nonlocal changed
        if isinstance(value, str):
            stripped = strip_context_blocks(value)
            if stripped != value:
                changed = True
            return stripped
        if isinstance(value, dict):
            for key, child in value.items():
                value[key] = transform(child)
            return value
        if isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = transform(child)
            return value
        return value

    transform(parsed)
    return changed


# --------------------------------------------------------------------------
# Pending registry (one-shot, keyed by session id)
# --------------------------------------------------------------------------

# session_id -> ordered {key: value} of fields awaiting the next user message.
_pending: dict[str, dict[str, str]] = {}


def inject_context(session_id: str, /, **fields: object) -> None:
    """Queue context ``fields`` for the session's next user message.

    ``session_id`` is positional-only so a field may itself be named
    ``session_id`` without colliding with the registry key — e.g. the canonical
    use ``inject_context(thread_id, session_id=thread_id)``.

    Merges into anything already queued for the session (last write wins per
    key). Values are coerced to ``str`` so callers can pass ids/numbers without
    ceremony. An empty call is a no-op. Thin ``**kwargs`` wrapper over
    :func:`inject_context_fields`.
    """
    inject_context_fields(session_id, fields)


def inject_context_fields(session_id: str, fields: Mapping[str, object]) -> None:
    """Queue context ``fields`` (arbitrary keys) for the session's next user message.

    Like :func:`inject_context` but takes a mapping, so keys that are not valid
    Python identifiers can be queued — e.g. the ``"Agent settings: permission_mode"``
    keys the environment reconciliation produces. Merges into anything already
    queued (last write wins per key); values coerced to ``str``; empty is a no-op.
    """
    if not fields:
        return
    bucket = _pending.setdefault(session_id, {})
    for key, value in fields.items():
        bucket[key] = str(value)
    logger.debug(
        "Queued context injection for %s: keys=%s",
        session_id, sorted(bucket.keys()),
    )


def consume_context(session_id: str) -> dict[str, str] | None:
    """Return and remove the queued fields for a session, or ``None``.

    One-shot: after this call the session has nothing pending until the next
    :func:`inject_context`.
    """
    return _pending.pop(session_id, None)


def clear_context(session_id: str) -> None:
    """Drop any queued fields for a session (best-effort teardown cleanup).

    Covers the rare case of a session that gets a queued injection but dies
    before its first turn ever consumes it; the normal path consumes the entry
    on the first turn, so this is usually a no-op.
    """
    _pending.pop(session_id, None)


def apply_pending_context(session_id: str, text: str) -> str:
    """Fold any queued context for ``session_id`` into the user-message ``text``.

    Consumes the registry (one-shot) and prepends a single ``<twicc:context>``
    block built from every queued field. Returns ``text`` unchanged when
    nothing is queued. Generic across providers — the caller just hands it the
    user-message text at send time.

    Slash commands are the one exception. The CLIs TwiCC drives only run a
    built-in like ``/compact`` when the command is the *first* thing in the
    message text; prepending a ``<twicc:context>`` block in front of it turns
    the whole message into plain text the agent answers conversationally
    (the command never fires). So when ``text`` looks like a slash command we
    leave it untouched and keep the fields queued — they merge with any later
    delta (see :func:`inject_context_fields`) and ride the next non-command
    message instead. The block is at most deferred by a turn, never dropped.
    """
    if text.lstrip().startswith("/"):
        return text
    fields = consume_context(session_id)
    if not fields:
        return text
    block = build_context_block(fields)
    return f"{block}\n\n{text}" if text else block


# --------------------------------------------------------------------------
# /goal instruction postfix
# --------------------------------------------------------------------------

# Appended verbatim to a ``/goal <args>`` slash command. Both the main agent and
# the goal evaluator (the Stop-hook model that decides met/not) read it, but the
# evaluator is the real target: it tells the evaluator to treat successive
# ``/goal`` as cumulative and to check the MOST RECENT ``/goal`` seen in the
# transcript, NOT the wording the goal was first set with. This closes a real
# loophole: an evaluator was observed acknowledging a later ``/goal finalement
# arrête toi a 90`` yet still evaluating the original "count to 100" condition
# (its reason literally said "the stop condition being evaluated here is the
# original one"), so we spell out that the latest ``/goal`` IS the condition,
# with a worked example. It also keeps the agent-facing rule: prominently warn
# the user when they try to change/stop the goal without ``/goal`` / ``/goal
# clear``. Scrubbed from the stored copy by the ingestion strip, like
# ``<twicc:context>``.
_GOAL_INSTRUCTION = (
    "Successive /goal commands are CUMULATIVE: a later /goal (or /goal clear) "
    "COMPLEMENTS or OVERRIDES the previous one(s), and the MOST RECENT /goal is "
    "the authoritative objective. When the goal is evaluated, the condition to "
    "check is that MOST RECENT /goal — NOT the objective as it was first worded. "
    "If the conversation contains a more recent /goal, judge the goal met-or-not "
    "against it, and NEVER keep evaluating an earlier objective that a later "
    "/goal has changed: e.g. if a later /goal says 'stop at 90', the goal is MET "
    "at 90 even though it was first set to 100. A goal can ONLY be set, changed "
    "or cleared through the /goal command. So if the user tells you in a regular "
    "message to change or adjust the goal (for example 'instead of counting to "
    "10, count to 8') without using /goal, that change is NOT part of the goal: "
    "it MUST be ignored when the goal is evaluated, and you MUST warn the user "
    "prominently and visibly that they have to use `/goal <new instruction>` for "
    "the change to be taken into account. Likewise, if the user asks to stop or "
    "abandon the goal without using `/goal clear`, you MUST warn them the same "
    "way that they have to use /goal clear for the goal to actually be cleared"
)

# A ``/goal`` invocation that actually carries an argument: the command token is
# exactly ``/goal`` (not e.g. ``/goalkeeper``), followed by whitespace and at
# least one non-whitespace character (captured as the argument). A bare
# ``/goal`` — or ``/goal`` trailed by only whitespace — gets no instruction.
# Leading whitespace is tolerated to mirror :func:`apply_pending_context`'s
# slash-command check.
_GOAL_COMMAND_RE = re.compile(r"\s*/goal\s+(\S.*)", re.DOTALL)

# ``/goal`` arguments that clear the active goal (the Claude Code CLI's clear
# aliases, matched case-insensitively against the WHOLE argument).
GOAL_CLEAR_ARGS = frozenset({"clear", "stop", "off", "reset", "none", "cancel"})


def apply_goal_instruction(text: str) -> str:
    """Append the ``<twicc:instruction>`` block when ``text`` is ``/goal <args>``.

    Returns ``text`` unchanged for anything that is not a ``/goal`` command with
    a non-empty argument (see :data:`_GOAL_COMMAND_RE`), and for a clear
    (``/goal clear`` and its aliases, see :data:`GOAL_CLEAR_ARGS`): the CLI only
    recognizes a clear when the argument is exactly an alias, so an appended
    block would turn it into a new goal whose condition is "clear". Provider-
    agnostic; the caller folds it in at the same send-time chokepoint as
    :func:`apply_pending_context`. The block rides this single message only; the
    ingestion strip keeps it out of the stored copy. See the module docstring.
    """
    match = _GOAL_COMMAND_RE.match(text)
    if match is None or match.group(1).strip().lower() in GOAL_CLEAR_ARGS:
        return text
    return f"{text}\n<{INSTRUCTION_TAG_NAME}>{_GOAL_INSTRUCTION}</{INSTRUCTION_TAG_NAME}>"


# --------------------------------------------------------------------------
# Environment reconciliation (keep the agent's view of the dynamic ``### Context``
# block fresh as it drifts: agent settings, workspaces, project name,
# interaction mode, hidden, annotations)
# --------------------------------------------------------------------------

# session_id -> the mutable Context snapshot ({label: value}) the agent has
# already seen. Compared against the freshly-computed snapshot at each outgoing
# user message; only the delta is queued. Touched only from the agent event
# loop (seed/reset at start, reconcile at each message), so no locking.
_baseline: dict[str, dict[str, str]] = {}


def seed_baseline(session_id: str, fields: Mapping[str, str]) -> None:
    """Record the snapshot the agent already knows — no injection follows.

    Called once at a FRESH start with the values that went into the addendum,
    so the first :func:`reconcile` finds no delta. A resume calls
    :func:`reset_baseline` instead, so the first reconcile re-states everything.
    """
    _baseline[session_id] = dict(fields)


def reset_baseline(session_id: str) -> None:
    """Forget the session's baseline → next :func:`reconcile` re-injects everything.

    Used on resume (the frozen addendum reflects launch-time state and is never
    re-sent, so we cannot know what the resumed agent still believes) and on
    teardown (best-effort cleanup, paired with :func:`clear_context`).
    """
    _baseline.pop(session_id, None)


def reconcile(session_id: str, current: Mapping[str, str]) -> None:
    """Queue the delta between ``current`` and the session's baseline.

    ``current`` is the freshly-computed mutable Context snapshot. Any key whose
    value differs from the baseline — which is every key when the baseline was
    reset or never seeded, i.e. right after a resume — is queued via
    :func:`inject_context_fields` so it lands in the next ``<twicc:context>``
    block; the baseline is then advanced to ``current``. The snapshot always
    carries the same labels (empty states rendered as explicit sentinels), so a
    key never silently disappears and a plain value comparison is enough.
    """
    baseline = _baseline.get(session_id) or {}
    changed = {key: value for key, value in current.items() if baseline.get(key) != value}
    _baseline[session_id] = dict(current)
    if changed:
        inject_context_fields(session_id, changed)
