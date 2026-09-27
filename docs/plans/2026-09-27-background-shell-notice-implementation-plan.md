# Background shell notice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** when a background shell outlives the work that started it, TwiCC sends
the main agent one automatic `:: notice from TwiCC` message, 5 minutes after the
agent became really idle, naming each shell and its owner.

**Architecture:** one pure module (`agent/shell_notice.py`) holds the data
types, the concerned-shell selection, the message builder and the database
owner lookup. Each provider agent keeps richer per-shell records and the
episode state, and exposes them through two hooks (`shell_notice_state()`,
`shell_notice_lookups()`). The manager's existing 30 s timeout monitor runs one
new step per agent and sends the notice through a dedicated
`send_shell_notice()` (never `send_to_session`).

**Tech Stack:** Python 3.13, Django 6 ORM, asyncio, claude-agent-sdk, vendored
openai_codex, pytest + pytest-django; node:test for one frontend check.

**Spec:** [`2026-09-27-background-shell-notice-design.md`](2026-09-27-background-shell-notice-design.md).
The spec is the source of truth for *what* and *why*; this plan is *in what
order* and *where*. Where they disagree, the spec wins and this plan is wrong.
Every task cites the spec sections it implements: read them before the task.

## Global Constraints

- Delay: `SHELL_NOTICE_DELAY_SECONDS = 300` (spec §4).
- Unresolved-owner grace: `SHELL_NOTICE_RESOLUTION_GRACE_SECONDS = 60` (spec §5.5).
- Send staleness bound: `SHELL_NOTICE_STALE_SECONDS = 30` (spec §7 step 3).
- Command and path cut: 300 characters (spec §6.1).
- `_tool_use_parents` bound: last 1024 entries (spec §5.2).
- Header constant: `SHELL_NOTICE_HEADER = ":: notice from TwiCC"`, defined in
  `src/twicc/cli/_drop_request/sender_header.py` (spec §6.2).
- The notice text is English. It is a normal user message: no compute change,
  no compute version bump, **no CSS change**, no migration, no setting (spec §8).
- The notice is never sent through `send_to_session` (spec §7).
- Backend JSON uses `orjson`; immutable data uses `NamedTuple` (CLAUDE.md).
- Tests: `uv run pytest` (main repo) or `cd <worktree> && TWICC_DATA_DIR=$PWD uv run pytest`
  (worktree). Frontend: `cd frontend && npm test`. Lint: `uvx ruff check <files>`.
- **Test style: `pytest-asyncio` is NOT installed** (and adding a dependency is
  the user's call). Never use `@pytest.mark.asyncio`. An async test body uses
  the `@run_async` decorator from `tests/shell_notice_helpers.py` (created in
  Task 3), or runs with `asyncio.run(...)` inside a sync test, as in
  `tests/test_background_work.py`.
- Commits: Conventional Commit subject, descriptive body, `Co-Authored-By`
  trailer with the running model's exact name. List files explicitly in
  `git add`. Commit only when the user asked for commits during execution.
- Work on the current branch. Never create a branch or a worktree unless the
  user asks.

## Choices settled here (the spec leaves them open)

- **Codex shell record.** The spec says `_live_shells` values become the
  shell's data. They become a small `_TrackedShell(started_at, command)`
  `NamedTuple`; the full `ShellInfo` is built by `shell_notice_state()`. The
  owner fields depend on live sets that change after the shell starts, so they
  are never frozen into the stored value.
- **Lookup list.** The manager learns which shells need the database through a
  second hook, `shell_notice_lookups(keys, now) -> list[ShellLookup]`. It lists
  raw `UNRESOLVED` shells whose stored resolution is missing or not final, and,
  on Codex, every database-placed owner (run state refresh, spec §5.5 Storage).
- **Merge function.** One pure function, `merge_resolution()`, applies a stored
  `ShellResolution` to a raw `ShellInfo` for both providers; Claude passes its
  live owner sets, Codex passes none.
- **`drop_gone_shells(probed_at, keys=None)`.** The snapshot of spec §5.4 is an
  optional keyword argument, so `tests/test_background_work.py:318` keeps
  working unchanged; the manager passes the snapshot.
- **`_main_turn_open` on DEAD.** `ClaudeCodeAgent` overrides `_set_state` and
  resets the flag on every transition to DEAD, whatever the path;
  `shell_notice_state()` also returns `None` for a DEAD agent.
- **Codex `UNATTRIBUTED` is final.** Once a Codex shell turned `UNATTRIBUTED`
  (`known=False` for 60 s), `needs_lookup` returns `False` for it, as spec §5.5
  Storage says ("not looked up again").
- **Codex plan "implement" turn.** The call `await self._run_turn(_PLAN_IMPLEMENTATION_MESSAGE, None)`
  moves into a small method `_run_plan_implementation_turn()` that clears the
  notified set first, so the rule is unit-testable without the pending-request
  plumbing.
- **Spec §10 "WS delivery ack is sent".** The ack in `asgi.py:1242` is sent
  exactly when `send_to_session` returns a true value, and `CodexAgentManager`
  returns `agent.send()` as is (`manager.py:316`). The test of this rule is
  therefore "`CodexAgent.send()` returns `True` in `USER_TURN`" (Task 4).

## Review Focus

1. **A notice that never stops.** The agent answers the notice, becomes idle,
   and the same shell is notified again 5 minutes later. Expected: never
   without an external send. Pinned in Task 3 (Claude episode test) and
   Task 4 (Codex episode test).
2. **A notice while the agent works.** A CLI auto turn opens between the tick
   and the send. Expected: no notice queued into a working turn in the normal
   case. Pinned in Task 5 (`send_shell_notice` re-checks idle under the lock).
3. **A subagent that is still working.** A nested Claude subagent resumed by a
   task notification still owns its shell. Expected: not reported. Pinned in
   Task 2 (merge with live owners) and Task 3 (nested shell test).
4. **A monitor mistaken for a shell.** A nested Claude Monitor looks like a
   shell on the stream. Expected: never reported. Pinned in Task 2 (DB lookup
   and merge tests).
5. **One broken agent blocking the others.** An exception in one agent's step.
   Expected: logged, other agents still checked. Pinned in Task 5.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/twicc/cli/_drop_request/sender_header.py` | `SHELL_NOTICE_HEADER`; `has_sender_header` also matches it | 1 |
| `src/twicc/agent/shell_notice.py` (new) | types, selection, message builder, merge, DB lookup | 1, 2 |
| `src/twicc/agent/base_agent.py` | episode state, default hooks, resolution storage | 3 |
| `src/twicc/providers/claude_code/agent/agent.py` | Claude shell records, owner maps, turn tracking, hooks | 3 |
| `src/twicc/providers/codex/agent/agent.py` | Codex shell records, owner maps, turn tracking, hooks, `send()` return | 4 |
| `src/twicc/providers/codex/agent/manager.py` | reconciliation in the hold with a key snapshot; `_send_gate` | 4, 5 |
| `src/twicc/agent/base_manager.py` | tick step, `send_shell_notice`, `_send_gate` | 5 |
| `src/twicc/agent/system_prompt.py` | one sentence about the notice | 6 |
| `tests/test_shell_notice.py` (new) | Task 1 and 2 unit tests | 1, 2 |
| `tests/test_shell_notice_claude.py` (new) | Claude bookkeeping tests | 3 |
| `tests/test_shell_notice_codex.py` (new) | Codex bookkeeping tests | 4 |
| `tests/test_shell_notice_manager.py` (new) | tick and send tests | 5 |
| `tests/test_background_work.py`, `tests/test_claude_monitor_liveness.py`, `tests/test_codex_agent_runs_live_process.py` | stubs and value types updated | 3, 4 |
| `tests/test_sender_header.py`, `tests/test_system_prompt_spawned_by.py`, `frontend/src/utils/markdownColonBlocks.test.js` | small additions | 1, 6 |

---

### Task 1: Shared types, selection and message builder

Spec: §3.3, §4 (selection), §5.1, §6.1, §6.2.

**Files:**
- Modify: `src/twicc/cli/_drop_request/sender_header.py`
- Create: `src/twicc/agent/shell_notice.py`
- Create: `tests/test_shell_notice.py`
- Modify: `tests/test_sender_header.py`

**Interfaces:**
- Produces (in `twicc.agent.shell_notice`):
  - constants `SHELL_NOTICE_DELAY_SECONDS = 300`, `SHELL_NOTICE_RESOLUTION_GRACE_SECONDS = 60`,
    `SHELL_NOTICE_STALE_SECONDS = 30`, `SHELL_NOTICE_TEXT_MAX_CHARS = 300`;
  - `class ShellOwner(StrEnum)`: `MAIN`, `SUBAGENT`, `UNRESOLVED`, `UNATTRIBUTED`;
  - `class ShellInfo(NamedTuple)` (fields below);
  - `class ShellNoticeState(NamedTuple)`: `idle: bool`, `shells: list[ShellInfo]`,
    `any_subagent_running: bool`, `last_subagent_run_end: float`;
  - `select_concerned_shells(state: ShellNoticeState, *, idle_since: float, notified: Collection[str], now: float) -> list[ShellInfo]`;
  - `earliest_delay_start(shell: ShellInfo, idle_since: float) -> float`;
  - `code_span(value: str, *, max_chars: int = SHELL_NOTICE_TEXT_MAX_CHARS) -> str`;
  - `build_shell_notice(shells: Sequence[ShellInfo], *, now: float) -> str`.
- Produces (in `twicc.cli._drop_request.sender_header`): `SHELL_NOTICE_HEADER`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sender_header.py` — append:

```python
def test_has_sender_header_matches_a_twicc_notice():
    from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER
    assert SHELL_NOTICE_HEADER == ":: notice from TwiCC"
    assert has_sender_header(":: notice from TwiCC: background shell(s) still running\n\nbody")
    assert not has_sender_header(":: notice something else")
```

`tests/test_shell_notice.py` — create:

```python
"""Shared pieces of the background shell notice (spec §3.3, §4, §5.1, §6).

Pure functions only: no agent, no database.
"""

from twicc.agent.shell_notice import (
    SHELL_NOTICE_DELAY_SECONDS,
    ShellInfo,
    ShellNoticeState,
    ShellOwner,
    build_shell_notice,
    code_span,
    earliest_delay_start,
    select_concerned_shells,
)

NOW = 10_000.0


def shell(key="b1", *, owner=ShellOwner.MAIN, started_at=0.0, **extra):
    fields = {
        "key": key, "shell_id": key, "tool_use_id": None, "owner": owner, "owner_ref": None,
        "owner_label": None, "owner_spawner_ref": None, "owner_run_ended_at": None,
        "owner_running": False, "description": None, "command": None, "output_path": None,
        "started_at": started_at,
    }
    fields.update(extra)
    return ShellInfo(**fields)


def state(*shells, any_running=False, last_end=0.0):
    return ShellNoticeState(idle=True, shells=list(shells), any_subagent_running=any_running,
                            last_subagent_run_end=last_end)


# --- selection -------------------------------------------------------------

def test_own_shell_waits_300s_after_idle():
    s = shell(started_at=0.0)
    idle_since = NOW - SHELL_NOTICE_DELAY_SECONDS + 1
    assert select_concerned_shells(state(s), idle_since=idle_since, notified=(), now=NOW) == []
    assert select_concerned_shells(state(s), idle_since=NOW - SHELL_NOTICE_DELAY_SECONDS,
                                   notified=(), now=NOW) == [s]


def test_own_shell_waits_300s_after_its_own_start():
    s = shell(started_at=NOW - 100)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []


def test_running_owner_is_never_concerned():
    s = shell(owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_running=True)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []


def test_orphan_delay_starts_at_the_owner_run_end():
    s = shell(owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_run_ended_at=NOW - 200)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []
    s = s._replace(owner_run_ended_at=NOW - 300)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == [s]


def test_orphan_with_unknown_run_end_counts_it_as_zero():
    s = shell(owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_run_ended_at=None)
    assert earliest_delay_start(s, 5.0) == 5.0
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == [s]


def test_unattributed_is_held_back_while_any_subagent_runs():
    s = shell(owner=ShellOwner.UNATTRIBUTED)
    assert select_concerned_shells(state(s, any_running=True), idle_since=0.0, notified=(), now=NOW) == []
    assert select_concerned_shells(state(s, last_end=NOW - 10), idle_since=0.0, notified=(), now=NOW) == []
    assert select_concerned_shells(state(s, last_end=NOW - 300), idle_since=0.0, notified=(), now=NOW) == [s]


def test_unresolved_is_never_reported():
    s = shell(owner=ShellOwner.UNRESOLVED)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []


def test_notified_keys_are_skipped():
    s = shell()
    assert select_concerned_shells(state(s), idle_since=0.0, notified={"b1"}, now=NOW) == []


# --- code spans --------------------------------------------------------------

def test_code_span_keeps_the_command_verbatim():
    assert code_span('grep -n "a|b" $f') == '`grep -n "a|b" $f`'


def test_code_span_fence_is_longer_than_any_backtick_run():
    assert code_span("a `b` c") == "``a `b` c``"


def test_code_span_pads_when_value_starts_or_ends_with_a_backtick():
    assert code_span("echo `date`") == "`` echo `date` ``"
    assert code_span("`x`") == "`` `x` ``"


def test_code_span_flattens_whitespace_and_cuts_at_300():
    value = "a\n" + "b" * 400
    rendered = code_span(value)
    assert "\n" not in rendered
    inner = rendered.strip("`")
    assert len(inner) == 300
    assert inner.endswith("…")


# --- message -----------------------------------------------------------------

def test_message_for_an_orphan_subagent_shell():
    s = shell(
        "bg15gnr0j", owner=ShellOwner.SUBAGENT, owner_ref="ad3c71271142417e1",
        owner_label="Implement Task 5c: Codex live + parity",
        description="Wait for full suite to finish",
        command="until grep -qE x; do sleep 5; done",
        output_path="/tmp/tasks/bg15gnr0j.output",
        started_at=NOW - 7 * 60,
    )
    text = build_shell_notice([s], now=NOW)
    assert text == (
        ":: notice from TwiCC: background shell(s) still running\n\n"
        'Your subagent `ad3c71271142417e1` ("Implement Task 5c: Codex live + parity") has finished its work, '
        "but it still has a background shell running:\n\n"
        '- shell `bg15gnr0j`: "Wait for full suite to finish", running for 7 min\n'
        "  - command: `until grep -qE x; do sleep 5; done`\n"
        "  - output: `/tmp/tasks/bg15gnr0j.output`\n\n"
        "Ask your subagent what this shell is for, and tell it to stop the shell if it is not needed."
    )


def test_message_for_an_own_shell_alone():
    s = shell("b9t13uhf9", description="Run full backend suite", started_at=NOW - 6 * 60)
    text = build_shell_notice([s], now=NOW)
    assert text == (
        ":: notice from TwiCC: background shell(s) still running\n\n"
        "You still have a background shell of your own:\n\n"
        '- shell `b9t13uhf9`: "Run full backend suite", running for 6 min\n\n'
        "If it is a long-running process you started on purpose, nothing to do. Otherwise, check what it "
        "is doing, make sure it will end, and keep the user informed so they do not wait for nothing."
    )


def test_message_orders_subagents_then_unattributed_then_own():
    own = shell("m1", started_at=NOW - 60)
    sub = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a1", started_at=NOW - 60)
    una = shell("u1", owner=ShellOwner.UNATTRIBUTED, started_at=NOW - 60)
    text = build_shell_notice([own, una, sub], now=NOW)
    assert text.index("`s1`") < text.index("`u1`") < text.index("`m1`")
    assert "One of your subagents, which TwiCC cannot identify, still has a background shell running:" in text
    assert "You also still have a background shell of your own:" in text


def test_message_groups_shells_of_one_subagent_and_uses_the_plural():
    a = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a1", started_at=NOW - 60)
    b = shell("s2", owner=ShellOwner.SUBAGENT, owner_ref="a1", started_at=NOW - 120)
    text = build_shell_notice([a, b], now=NOW)
    assert text.count("Your subagent `a1`") == 1
    assert "still has 2 background shells running:" in text
    assert "Ask your subagent what these shells are for, and tell it to stop them if they are not needed." in text


def test_message_names_a_nested_owner_and_its_spawner():
    s = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a79858f65df89eb0a",
              owner_spawner_ref="a663305d8fe755548", started_at=NOW - 60)
    text = build_shell_notice([s], now=NOW)
    assert "Subagent `a79858f65df89eb0a`, started by subagent `a663305d8fe755548`, has finished its work" in text
    assert "Check with whichever of the two you can reach" in text


def test_message_escapes_labels_but_not_commands():
    s = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_label="fix *all* `x`",
              command="echo *", started_at=NOW - 60)
    text = build_shell_notice([s], now=NOW)
    assert '("fix \\*all\\* \\`x\\`")' in text
    assert "command: `echo *`" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_shell_notice.py tests/test_sender_header.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'twicc.agent.shell_notice'`,
and `ImportError` for `SHELL_NOTICE_HEADER`.

- [ ] **Step 3: Add the header constant**

In `src/twicc/cli/_drop_request/sender_header.py`, after `SENDER_HEADER_PREFIX`:

```python
# The opening of the notice TwiCC itself sends an agent (a background shell
# outliving its owner, see ``twicc.agent.shell_notice``). Defined here, next to
# the other header, so this module never imports the agent package.
SHELL_NOTICE_HEADER = ":: notice from TwiCC"
```

Replace `has_sender_header`'s docstring first sentence and its return:

```python
def has_sender_header(text: str) -> bool:
    """Tell whether ``text`` was written by an agent or by TwiCC, not the human.

    Covers inter-session messages (agent to agent) and TwiCC's own notices.
    The header always opens the message, so a prefix test is enough. It reads
    the text as stored on the ``SessionItem`` -- already scrubbed of the
    ``<twicc:context>`` / ``<twicc:instruction>`` blocks at ingestion (see
    :mod:`twicc.context_injection`), so the header really sits at the front.

    Two shapes are deliberately NOT covered, because they carry no header: a
    session messaging itself, and the initial prompt of a spawned session.
    """
    return text.lstrip().startswith((SENDER_HEADER_PREFIX, ":: message via ", SHELL_NOTICE_HEADER))
```

- [ ] **Step 4: Create `src/twicc/agent/shell_notice.py`**

```python
"""Background shell notice: TwiCC tells an idle agent about lingering shells.

Design: ``docs/plans/2026-09-27-background-shell-notice-design.md``. This
module is provider-agnostic: it never reads provider event shapes. Providers
describe their shells as :class:`ShellInfo` records through
``BaseAgent.shell_notice_state()``; the manager selects the concerned ones with
:func:`select_concerned_shells` and sends :func:`build_shell_notice`'s text.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from enum import StrEnum
from typing import NamedTuple

from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER, inline_md

SHELL_NOTICE_DELAY_SECONDS = 300
SHELL_NOTICE_RESOLUTION_GRACE_SECONDS = 60
SHELL_NOTICE_STALE_SECONDS = 30
SHELL_NOTICE_TEXT_MAX_CHARS = 300

_WHITESPACE_RUN_RE = re.compile(r"\s+")
_BACKTICK_RUN_RE = re.compile(r"`+")


class ShellOwner(StrEnum):
    MAIN = "main"
    SUBAGENT = "subagent"
    UNRESOLVED = "unresolved"
    UNATTRIBUTED = "unattributed"


class ShellInfo(NamedTuple):
    key: str  # Claude task_id; Codex "thread_id:process_id"
    shell_id: str  # the id the agent knows: Claude task_id, Codex process_id
    tool_use_id: str | None  # Claude: the Bash tool_use; Codex: None
    owner: ShellOwner
    owner_ref: str | None  # the id the main agent uses to reach the subagent
    owner_label: str | None
    owner_spawner_ref: str | None  # nested owner only
    owner_run_ended_at: float | None
    owner_running: bool
    description: str | None
    command: str | None
    output_path: str | None
    started_at: float  # epoch seconds


class ShellNoticeState(NamedTuple):
    idle: bool
    shells: list[ShellInfo]  # backgrounded shells only, owners merged
    any_subagent_running: bool
    last_subagent_run_end: float  # 0 when no subagent run ended


def earliest_delay_start(shell: ShellInfo, idle_since: float) -> float:
    """The earliest possible start of the 5-minute delay (spec §4 step 2)."""
    return max(idle_since, shell.started_at)


def _delay_start(shell: ShellInfo, state: ShellNoticeState, idle_since: float) -> float | None:
    """When the delay of ``shell`` starts, or ``None`` if it is not concerned (spec §3.3)."""
    start = earliest_delay_start(shell, idle_since)
    if shell.owner is ShellOwner.MAIN:
        return start
    if shell.owner is ShellOwner.SUBAGENT:
        if shell.owner_running:
            return None
        return max(start, shell.owner_run_ended_at or 0.0)
    if shell.owner is ShellOwner.UNATTRIBUTED:
        if state.any_subagent_running:
            return None
        return max(start, state.last_subagent_run_end)
    return None  # UNRESOLVED: never reported


def select_concerned_shells(
    state: ShellNoticeState, *, idle_since: float, notified: Collection[str], now: float,
) -> list[ShellInfo]:
    """The shells to report now, in the order ``state`` lists them."""
    concerned = []
    for shell in state.shells:
        if shell.key in notified:
            continue
        start = _delay_start(shell, state, idle_since)
        if start is not None and start <= now - SHELL_NOTICE_DELAY_SECONDS:
            concerned.append(shell)
    return concerned


def code_span(value: str, *, max_chars: int = SHELL_NOTICE_TEXT_MAX_CHARS) -> str:
    """Wrap a command or a path in a code span, verbatim (spec §6.1).

    No backslash escaping: inside a code span it would show literally and change
    the command the agent reads. The fence is one backtick longer than the
    longest backtick run in the value.
    """
    flattened = _WHITESPACE_RUN_RE.sub(" ", value).strip()
    if len(flattened) > max_chars:
        flattened = flattened[: max_chars - 1] + "…"
    longest = max((len(run) for run in _BACKTICK_RUN_RE.findall(flattened)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if flattened.startswith("`") or flattened.endswith("`") else ""
    return f"{fence}{pad}{flattened}{pad}{fence}"


def _shell_count(count: int) -> str:
    return "a background shell" if count == 1 else f"{count} background shells"


def _shell_lines(shells: Sequence[ShellInfo], now: float) -> str:
    lines = []
    for shell in sorted(shells, key=lambda s: s.started_at):
        minutes = max(0, int((now - shell.started_at) // 60))
        description = inline_md(shell.description)
        head = f"- shell {code_span(shell.shell_id)}"
        head += f': "{description}", running for {minutes} min' if description else f": running for {minutes} min"
        lines.append(head)
        if shell.command:
            lines.append(f"  - command: {code_span(shell.command)}")
        if shell.output_path:
            lines.append(f"  - output: {code_span(shell.output_path)}")
    return "\n".join(lines)


def _subagent_ask(count: int) -> str:
    if count == 1:
        return "Ask your subagent what this shell is for, and tell it to stop the shell if it is not needed."
    return "Ask your subagent what these shells are for, and tell it to stop them if they are not needed."


def _nested_ask(count: int) -> str:
    what = "this shell is" if count == 1 else "these shells are"
    it = "it" if count == 1 else "them"
    return (
        f"Check with whichever of the two you can reach what {what} for, "
        f"and have {it} stopped if not needed."
    )


def _unattributed_ask(count: int) -> str:
    what = "this shell" if count == 1 else "these shells"
    it = "it" if count == 1 else "them"
    return f"Find which subagent started {what}, and have {it} stopped if not needed."


_MAIN_ASK = (
    "If it is a long-running process you started on purpose, nothing to do. Otherwise, check what it "
    "is doing, make sure it will end, and keep the user informed so they do not wait for nothing."
)


def build_shell_notice(shells: Sequence[ShellInfo], *, now: float) -> str:
    """The notice text for ``shells`` (spec §6.1): subagents, unattributed, own."""
    parts = [f"{SHELL_NOTICE_HEADER}: background shell(s) still running"]

    by_owner: dict[str, list[ShellInfo]] = {}
    for shell in shells:
        if shell.owner is ShellOwner.SUBAGENT and shell.owner_ref:
            by_owner.setdefault(shell.owner_ref, []).append(shell)
    for owner_ref, group in by_owner.items():
        first = group[0]
        label = inline_md(first.owner_label)
        label_part = f' ("{label}")' if label else ""
        count = _shell_count(len(group))
        if first.owner_spawner_ref:
            who = (
                f"Subagent {code_span(owner_ref)}{label_part}, "
                f"started by subagent {code_span(first.owner_spawner_ref)},"
            )
            ask = _nested_ask(len(group))
        else:
            who = f"Your subagent {code_span(owner_ref)}{label_part}"
            ask = _subagent_ask(len(group))
        parts.append(f"{who} has finished its work, but it still has {count} running:")
        parts.append(_shell_lines(group, now))
        parts.append(ask)

    unattributed = [s for s in shells if s.owner is ShellOwner.UNATTRIBUTED]
    if unattributed:
        count = _shell_count(len(unattributed))
        parts.append(f"One of your subagents, which TwiCC cannot identify, still has {count} running:")
        parts.append(_shell_lines(unattributed, now))
        parts.append(_unattributed_ask(len(unattributed)))

    own = [s for s in shells if s.owner is ShellOwner.MAIN]
    if own:
        also = " also" if len(parts) > 1 else ""
        parts.append(f"You{also} still have {_shell_count(len(own))} of your own:")
        parts.append(_shell_lines(own, now))
        parts.append(_MAIN_ASK)

    return "\n\n".join(parts)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_shell_notice.py tests/test_sender_header.py -q`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff check src/twicc/agent/shell_notice.py src/twicc/cli/_drop_request/sender_header.py tests/test_shell_notice.py tests/test_sender_header.py
git add src/twicc/agent/shell_notice.py src/twicc/cli/_drop_request/sender_header.py tests/test_shell_notice.py tests/test_sender_header.py
git commit -m "feat(agent): add the background shell notice builder and selection"
```

---

### Task 2: Owner merge and database lookup

Spec: §5.5 (whole section), §3.3.

**Files:**
- Modify: `src/twicc/agent/shell_notice.py`
- Modify: `tests/test_shell_notice.py`

**Interfaces:**
- Consumes: Task 1 types and constants.
- Produces (in `twicc.agent.shell_notice`):
  - `class ShellLookup(NamedTuple)`: `key: str`, `tool_use_id: str | None`,
    `owner_id: str | None` (Codex thread id; `None` for Claude), `codex: bool`;
  - `class OwnerFacts(NamedTuple)`: `key`, `owner_id: str | None`,
    `tool_name: str | None`, `spawner_id: str | None`, `title: str | None`,
    `known: bool | None`, `running: bool | None`, `stopped_at: float | None`;
  - `class ShellResolution(NamedTuple)`: `owner_id`, `tool_name`, `spawner_id`,
    `title`, `known`, `running`, `stopped_at`, `first_attempt_at: float`;
  - `class ClaudeLiveOwners(NamedTuple)`: `labels: Mapping[str, str]`,
    `running: Collection[str]`, `ended: Mapping[str, float]`;
  - `merge_resolution(shell: ShellInfo, resolution: ShellResolution | None, *, now: float, claude_live: ClaudeLiveOwners | None) -> ShellInfo | None`
    (`None` = "not a shell");
  - `needs_lookup(resolution: ShellResolution | None, *, codex: bool, now: float) -> bool`;
  - `resolve_shell_owners(root_id: str, lookups: Sequence[ShellLookup]) -> list[OwnerFacts]`
    (sync, database only; the manager calls it through `sync_to_async`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shell_notice.py`:

```python
from datetime import UTC, datetime

import pytest

from twicc.agent.shell_notice import (
    SHELL_NOTICE_RESOLUTION_GRACE_SECONDS,
    ClaudeLiveOwners,
    OwnerFacts,
    ShellLookup,
    ShellResolution,
    merge_resolution,
    needs_lookup,
    resolve_shell_owners,
)
from twicc.core.agent_runs import AgentRunState
from twicc.core.models import AgentLink, Project, Session, SessionType, ToolResultLink


def resolution(**extra):
    fields = {"owner_id": None, "tool_name": None, "spawner_id": None, "title": None, "known": None,
              "running": None, "stopped_at": None, "first_attempt_at": NOW}
    fields.update(extra)
    return ShellResolution(**fields)


LIVE = ClaudeLiveOwners(labels={"a1": "Nested task"}, running=set(), ended={"a1": NOW - 400})


# --- merge -------------------------------------------------------------------

def test_merge_leaves_live_owners_alone():
    s = shell(owner=ShellOwner.MAIN)
    assert merge_resolution(s, resolution(owner_id="x"), now=NOW, claude_live=LIVE) is s


def test_merge_without_resolution_stays_unresolved():
    s = shell(owner=ShellOwner.UNRESOLVED)
    assert merge_resolution(s, None, now=NOW, claude_live=LIVE) is s


def test_merge_drops_a_nested_monitor():
    s = shell(owner=ShellOwner.UNRESOLVED)
    res = resolution(owner_id="a1", tool_name="Monitor")
    assert merge_resolution(s, res, now=NOW, claude_live=LIVE) is None


def test_merge_claude_uses_live_run_state_and_label():
    s = shell(owner=ShellOwner.UNRESOLVED)
    res = resolution(owner_id="a1", tool_name="Bash", spawner_id="a0", title="You are a COMMAND RUNNER")
    merged = merge_resolution(s, res, now=NOW, claude_live=LIVE)
    assert merged.owner is ShellOwner.SUBAGENT
    assert merged.owner_ref == "a1"
    assert merged.owner_label == "Nested task"
    assert merged.owner_spawner_ref == "a0"
    assert merged.owner_running is False
    assert merged.owner_run_ended_at == NOW - 400


def test_merge_claude_owner_resumed_by_a_task_notification_is_running():
    live = LIVE._replace(running={"a1"})
    merged = merge_resolution(shell(owner=ShellOwner.UNRESOLVED), resolution(owner_id="a1", tool_name="Bash"),
                              now=NOW, claude_live=live)
    assert merged.owner_running is True


def test_merge_claude_owner_never_seen_live_is_unattributed():
    merged = merge_resolution(shell(owner=ShellOwner.UNRESOLVED), resolution(owner_id="zz", tool_name="Bash"),
                              now=NOW, claude_live=LIVE)
    assert merged.owner is ShellOwner.UNATTRIBUTED


def test_merge_no_owner_turns_unattributed_after_the_grace():
    s = shell(owner=ShellOwner.UNRESOLVED)
    fresh = resolution(first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS + 1)
    old = resolution(first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS)
    assert merge_resolution(s, fresh, now=NOW, claude_live=LIVE).owner is ShellOwner.UNRESOLVED
    assert merge_resolution(s, old, now=NOW, claude_live=LIVE).owner is ShellOwner.UNATTRIBUTED


def test_merge_codex_uses_the_stored_run_state():
    s = shell(owner=ShellOwner.UNRESOLVED)
    res = resolution(owner_id="t1", known=True, running=False, stopped_at=NOW - 500, title="Nested")
    merged = merge_resolution(s, res, now=NOW, claude_live=None)
    assert (merged.owner, merged.owner_ref, merged.owner_running, merged.owner_run_ended_at, merged.owner_label) == (
        ShellOwner.SUBAGENT, "t1", False, NOW - 500, "Nested",
    )


def test_merge_codex_unknown_run_waits_then_turns_unattributed():
    s = shell(owner=ShellOwner.UNRESOLVED)
    fresh = resolution(owner_id="t1", known=False, first_attempt_at=NOW - 10)
    old = resolution(owner_id="t1", known=False, first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS)
    assert merge_resolution(s, fresh, now=NOW, claude_live=None).owner is ShellOwner.UNRESOLVED
    assert merge_resolution(s, old, now=NOW, claude_live=None).owner is ShellOwner.UNATTRIBUTED


def test_needs_lookup():
    assert needs_lookup(None, codex=False, now=NOW)
    assert not needs_lookup(resolution(owner_id="a1", tool_name="Bash"), codex=False, now=NOW)
    assert not needs_lookup(resolution(owner_id="a1", tool_name="Monitor"), codex=False, now=NOW)
    assert needs_lookup(resolution(first_attempt_at=NOW - 10), codex=False, now=NOW)
    assert not needs_lookup(resolution(first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS),
                            codex=False, now=NOW)
    assert needs_lookup(resolution(owner_id="t1", known=True, running=True), codex=True, now=NOW)
    assert needs_lookup(resolution(owner_id="t1", known=False, first_attempt_at=NOW - 10), codex=True, now=NOW)
    assert not needs_lookup(resolution(owner_id="t1", known=False,
                                       first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS),
                            codex=True, now=NOW)


# --- database lookup ----------------------------------------------------------

@pytest.fixture
def tree(db):
    project = Project.objects.create(id="-tmp-shell-notice", directory="/tmp/shell-notice")

    def make(session_id, parent=None, provider="claude_code", title=None):
        return Session.objects.create(
            id=session_id, project=project, provider=provider, file_path=f"{session_id}.jsonl",
            type=SessionType.SUBAGENT if parent else SessionType.SESSION, parent_session=parent, title=title,
        )

    root = make("root-1")
    first = make("a663305d8fe755548", parent=root, title="first level")
    nested = make("a79858f65df89eb0a", parent=root, title="You are a COMMAND RUNNER")
    AgentLink.objects.create(session=root, tool_use_line_num=1, tool_use_id="toolu_spawn_first",
                             agent_id=first.id)
    AgentLink.objects.create(session=first, tool_use_line_num=1, tool_use_id="toolu_spawn_nested",
                             agent_id=nested.id)
    for line, error in ((38, None), (45, "failed")):
        ToolResultLink.objects.create(session=nested, tool_use_line_num=30, tool_result_line_num=line,
                                      tool_use_id="toolu_bash", tool_name="Bash", error=error)
    ToolResultLink.objects.create(session=nested, tool_use_line_num=50, tool_result_line_num=51,
                                  tool_use_id="toolu_monitor", tool_name="Monitor")
    return root, first, nested


def test_lookup_claude_nested_shell(tree):
    root, first, nested = tree
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b5lhayncg", "toolu_bash", None, False)])
    assert facts == OwnerFacts(key="b5lhayncg", owner_id=nested.id, tool_name="Bash", spawner_id=first.id,
                               title="You are a COMMAND RUNNER", known=None, running=None, stopped_at=None)


def test_lookup_claude_first_level_owner_has_no_spawner(tree):
    root, first, _ = tree
    ToolResultLink.objects.create(session=first, tool_use_line_num=2, tool_result_line_num=3,
                                  tool_use_id="toolu_first_bash", tool_name="Bash")
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b1", "toolu_first_bash", None, False)])
    assert (facts.owner_id, facts.spawner_id) == (first.id, None)


def test_lookup_claude_nested_monitor(tree):
    root, _, _ = tree
    [facts] = resolve_shell_owners(root.id, [ShellLookup("bst40x3gu", "toolu_monitor", None, False)])
    assert facts.tool_name == "Monitor"


def test_lookup_claude_link_not_synced_yet(tree):
    root, _, _ = tree
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b9", "toolu_missing", None, False)])
    assert facts.owner_id is None


def test_lookup_ignores_links_outside_the_root_tree(tree):
    root, _, _ = tree
    other_root = Session.objects.create(id="root-2", project=root.project, provider="claude_code",
                                        file_path="root-2.jsonl", type=SessionType.SESSION)
    stranger = Session.objects.create(id="a-other", project=root.project, provider="claude_code",
                                      file_path="a-other.jsonl", type=SessionType.SUBAGENT,
                                      parent_session=other_root)
    ToolResultLink.objects.create(session=stranger, tool_use_line_num=1, tool_result_line_num=2,
                                  tool_use_id="toolu_other", tool_name="Bash")
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b1", "toolu_other", None, False)])
    assert facts.owner_id is None


def test_lookup_codex_reads_the_run_model(tree, monkeypatch):
    root, first, nested = tree
    calls = []

    def fake_states(root_session, agent_ids, *args, **kwargs):
        calls.append((root_session.id, set(agent_ids)))
        return {
            nested.id: AgentRunState(known=True, running=False, run_started_at=None, run_background=None,
                                     stopped_at=datetime(2026, 9, 27, 7, 0, tzinfo=UTC), runs=()),
        }

    monkeypatch.setattr("twicc.core.agent_runs.agent_run_states", fake_states)
    [facts] = resolve_shell_owners(root.id, [ShellLookup("t:1", None, nested.id, True)])
    assert calls == [(root.id, {nested.id})]
    assert (facts.owner_id, facts.spawner_id, facts.known, facts.running) == (nested.id, first.id, True, False)
    assert facts.stopped_at == datetime(2026, 9, 27, 7, 0, tzinfo=UTC).timestamp()


def test_lookup_codex_ended_run_without_stop_time(tree, monkeypatch):
    root, _, nested = tree
    monkeypatch.setattr(
        "twicc.core.agent_runs.agent_run_states",
        lambda root_session, agent_ids, *a, **k: {
            nested.id: AgentRunState(known=True, running=False, run_started_at=None, run_background=None,
                                     stopped_at=None, runs=()),
        },
    )
    [facts] = resolve_shell_owners(root.id, [ShellLookup("t:1", None, nested.id, True)])
    assert (facts.known, facts.running, facts.stopped_at) == (True, False, None)
    merged = merge_resolution(shell(owner=ShellOwner.UNRESOLVED),
                              resolution(owner_id=nested.id, known=True, running=False, stopped_at=None),
                              now=NOW, claude_live=None)
    assert merged.owner_run_ended_at is None
    assert select_concerned_shells(state(merged), idle_since=0.0, notified=(), now=NOW) == [merged]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_shell_notice.py -q`
Expected: FAIL — `ImportError: cannot import name 'ClaudeLiveOwners'`.

- [ ] **Step 3: Implement the merge and the lookup**

Append to `src/twicc/agent/shell_notice.py` (add `Mapping` to the
`collections.abc` import):

```python
class ShellLookup(NamedTuple):
    key: str
    tool_use_id: str | None  # Claude: the shell's Bash tool_use
    owner_id: str | None  # Codex: the owner's thread id
    codex: bool


class OwnerFacts(NamedTuple):
    """Database facts about one shell's owner (spec §5.5, database part)."""

    key: str
    owner_id: str | None
    tool_name: str | None  # Claude only
    spawner_id: str | None  # None for a first-level owner
    title: str | None
    known: bool | None  # Codex run model only
    running: bool | None
    stopped_at: float | None


class ShellResolution(NamedTuple):
    """Stored placement of one shell (spec §5.5, Storage)."""

    owner_id: str | None
    tool_name: str | None
    spawner_id: str | None
    title: str | None
    known: bool | None
    running: bool | None
    stopped_at: float | None
    first_attempt_at: float


class ClaudeLiveOwners(NamedTuple):
    """Claude live run data, every level (spec §5.5 step 4)."""

    labels: Mapping[str, str]  # agent id -> description; "seen live" test
    running: Collection[str]  # agent ids in _live_background_tasks
    ended: Mapping[str, float]  # agent id -> last run end


def _grace_over(resolution: ShellResolution, now: float) -> bool:
    return now - resolution.first_attempt_at >= SHELL_NOTICE_RESOLUTION_GRACE_SECONDS


def needs_lookup(resolution: ShellResolution | None, *, codex: bool, now: float) -> bool:
    """Whether a raw ``UNRESOLVED`` shell still needs the database part."""
    if resolution is None:
        return True
    if resolution.tool_name not in (None, "Bash"):
        return False  # not a shell: final
    if resolution.owner_id is None:
        return not _grace_over(resolution, now)
    if not codex:
        return False  # Claude reads the run state live
    # Codex re-reads the run state at each tick, except once UNATTRIBUTED
    # (still unknown after the grace): final (spec §5.5 Storage).
    return bool(resolution.known) or not _grace_over(resolution, now)


def merge_resolution(
    shell: ShellInfo,
    resolution: ShellResolution | None,
    *,
    now: float,
    claude_live: ClaudeLiveOwners | None,
) -> ShellInfo | None:
    """Apply a stored placement to a raw shell (spec §5.5 outcome table).

    ``claude_live`` is given for Claude (run state from live data) and ``None``
    for Codex (run state from the stored run model facts). Returns ``None``
    for "not a shell".
    """
    if shell.owner is not ShellOwner.UNRESOLVED or resolution is None:
        return shell
    if resolution.tool_name not in (None, "Bash"):
        return None
    if resolution.owner_id is None:
        return shell._replace(owner=ShellOwner.UNATTRIBUTED) if _grace_over(resolution, now) else shell
    owner_id = resolution.owner_id
    if claude_live is not None:
        if owner_id not in claude_live.labels:
            return shell._replace(owner=ShellOwner.UNATTRIBUTED)
        running = owner_id in claude_live.running
        ended = claude_live.ended.get(owner_id)
        label = claude_live.labels.get(owner_id) or resolution.title
    else:
        if not resolution.known:
            return shell._replace(owner=ShellOwner.UNATTRIBUTED) if _grace_over(resolution, now) else shell
        running = bool(resolution.running)
        ended = resolution.stopped_at
        label = resolution.title
    return shell._replace(
        owner=ShellOwner.SUBAGENT,
        owner_ref=owner_id,
        owner_label=label,
        owner_spawner_ref=resolution.spawner_id,
        owner_running=running,
        owner_run_ended_at=ended,
    )


def resolve_shell_owners(root_id: str, lookups: Sequence[ShellLookup]) -> list[OwnerFacts]:
    """Database part of spec §5.5. Sync; reads the database only, never an agent."""
    from twicc.core import agent_runs
    from twicc.core.models import AgentLink, Session, ToolResultLink

    owner_by_key: dict[str, str] = {}
    tool_by_key: dict[str, str] = {}

    claude = [lookup for lookup in lookups if not lookup.codex and lookup.tool_use_id]
    if claude:
        sessions_by_tool: dict[str, set[str]] = {}
        names_by_tool: dict[str, set[str]] = {}
        rows = ToolResultLink.objects.filter(
            tool_use_id__in=[lookup.tool_use_id for lookup in claude],
            session__parent_session_id=root_id,
        ).values_list("tool_use_id", "session_id", "tool_name").distinct()
        for tool_use_id, session_id, tool_name in rows:
            sessions_by_tool.setdefault(tool_use_id, set()).add(session_id)
            names_by_tool.setdefault(tool_use_id, set()).add(tool_name)
        for lookup in claude:
            sessions = sessions_by_tool.get(lookup.tool_use_id, set())
            if len(sessions) != 1:
                continue  # zero: not synced yet; several: treated as not found
            owner_by_key[lookup.key] = next(iter(sessions))
            names = names_by_tool.get(lookup.tool_use_id, set())
            non_bash = sorted(name for name in names if name != "Bash")
            tool_by_key[lookup.key] = non_bash[0] if non_bash else "Bash"

    codex = [lookup for lookup in lookups if lookup.codex and lookup.owner_id]
    for lookup in codex:
        owner_by_key[lookup.key] = lookup.owner_id

    owner_ids = set(owner_by_key.values())
    spawners: dict[str, str] = {}
    titles: dict[str, str | None] = {}
    if owner_ids:
        for agent_id, session_id in AgentLink.objects.filter(agent_id__in=owner_ids).values_list(
            "agent_id", "session_id",
        ):
            if session_id != root_id and session_id != agent_id:
                spawners.setdefault(agent_id, session_id)
        titles = dict(Session.objects.filter(id__in=owner_ids).values_list("id", "title"))

    run_states = {}
    codex_ids = {lookup.owner_id for lookup in codex}
    if codex_ids:
        root = Session.objects.get(id=root_id)
        run_states = agent_runs.agent_run_states(root, codex_ids)

    facts = []
    for lookup in lookups:
        owner_id = owner_by_key.get(lookup.key)
        known = running = stopped_at = None
        if lookup.codex and owner_id is not None:
            state = run_states.get(owner_id)
            known = bool(state and state.known)
            running = bool(state and state.running)
            stopped_at = state.stopped_at.timestamp() if state and state.stopped_at else None
        facts.append(OwnerFacts(
            key=lookup.key,
            owner_id=owner_id,
            tool_name=tool_by_key.get(lookup.key),
            spawner_id=spawners.get(owner_id) if owner_id else None,
            title=titles.get(owner_id) if owner_id else None,
            known=known,
            running=running,
            stopped_at=stopped_at,
        ))
    return facts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_shell_notice.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff check src/twicc/agent/shell_notice.py tests/test_shell_notice.py
git add src/twicc/agent/shell_notice.py tests/test_shell_notice.py
git commit -m "feat(agent): resolve background shell owners from the database"
```

---

### Task 3: Base agent state and Claude Code bookkeeping

Spec: §3.1 (Claude predicate, `idle_since`), §3.2, §5.2 (whole section), §5.3.

**Files:**
- Modify: `src/twicc/agent/base_agent.py`
- Modify: `src/twicc/providers/claude_code/agent/agent.py`
- Create: `tests/shell_notice_helpers.py` (not collected: no `test_` prefix)
- Create: `tests/test_shell_notice_claude.py`
- Modify: `tests/test_background_work.py`, `tests/test_claude_monitor_liveness.py`

**Interfaces:**
- Consumes: Task 1 and 2 types and functions.
- Produces (on `BaseAgent`):
  - attributes `_shell_notice_idle_since: float | None`, `_shell_notice_notified: set[str]`,
    `_shell_notice_resolutions: dict[str, ShellResolution]`, `_shell_notice_task: asyncio.Task | None`;
  - `shell_notice_state() -> ShellNoticeState | None` (default `None`);
  - `shell_notice_lookups(keys: Collection[str], now: float) -> list[ShellLookup]` (default `[]`);
  - `store_shell_resolutions(facts: Sequence[OwnerFacts], now: float) -> None`;
  - `mark_shells_noticed(keys: Iterable[str]) -> None`;
  - `_shell_notice_live_keys() -> set[str]` (default `set()`);
  - `_note_main_turn_opening()`, `_note_external_send()`, `_drop_shell_notice_key(key)`;
  - `_init_shell_notice_state()` (called by `__init__`; test stubs built with `__new__` call it).
- Produces (on `ClaudeCodeAgent`, module `twicc.providers.claude_code.agent.agent`):
  - `send(text, *, images=None, documents=None, shell_notice=False)`;
  - `_ShellTask` (mutable record, fields `backgrounded`, `tool_use_id`, `owner`, `owner_ref`,
    `description`, `command`, `output_path`, `started_at`);
  - `_init_claude_shell_notice_state()`, `_forget_shell_task(task_id) -> _ShellTask | None`,
    `_note_shell_notice_stream(msg) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/shell_notice_helpers.py` (shared by Tasks 3, 4 and 5):

```python
"""Helpers for the background shell notice tests.

``pytest-asyncio`` is not installed: an async test body runs through
:func:`run_async`, which keeps the body's signature so pytest still injects
fixtures (``monkeypatch``, ...).
"""

import asyncio
import contextlib
import inspect


def run_async(fn):
    """Turn ``async def test_x(...)`` into a sync test that runs it with ``asyncio.run``."""
    def wrapper(**kwargs):
        asyncio.run(fn(**kwargs))

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    wrapper.__module__ = fn.__module__
    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


def first_step(coro) -> None:
    """Run a coroutine up to its first suspension (or error), then close it.

    Used to check what a method does in its very first statements without
    driving the rest of it (which needs a live SDK).
    """
    with contextlib.suppress(BaseException):
        coro.send(None)
    with contextlib.suppress(BaseException):
        coro.close()
```

Create `tests/test_shell_notice_claude.py`:

```python
"""Claude Code bookkeeping for the background shell notice (spec §3.1, §3.2, §5.2)."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, UserMessage
from claude_agent_sdk.types import StreamEvent, ToolResultBlock, ToolUseBlock

from tests.shell_notice_helpers import run_async
from twicc.agent.base_agent import BaseAgent
from twicc.agent.shell_notice import OwnerFacts, ShellOwner
from twicc.agent.states import AgentState
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent


def make_agent():
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.session_id = "root-1"
    agent.state = AgentState.USER_TURN
    agent._live_background_tasks = {}
    agent._live_monitor_tasks = set()
    agent._live_shell_tasks = {}
    agent._listed_background_tasks = set()
    agent._wakeup_refresh_handle = None
    agent._pending_wakeup_at = None
    agent._waiting_label_active = False
    agent._pending_requests = {}
    agent._broadcast_process_label = AsyncMock()
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._background_work_broadcast_failures = 0
    agent._dead_event = asyncio.Event()
    agent._init_shell_notice_state()
    agent._init_claude_shell_notice_state()
    return agent


def system(subtype, **data):
    return SystemMessage(subtype=subtype, data={"subtype": subtype, **data})


def bash_use(tool_use_id, command, parent=None):
    return AssistantMessage(content=[ToolUseBlock(id=tool_use_id, name="Bash", input={"command": command})],
                            model="m", parent_tool_use_id=parent)


async def feed(agent, *messages):
    for msg in messages:
        agent._note_shell_notice_stream(msg)
        if isinstance(msg, SystemMessage):
            await agent._update_live_tasks(msg)


@run_async
async def test_main_agent_shell():
    agent = make_agent()
    await feed(agent,
               bash_use("toolu_b", "sleep 999"),
               system("task_started", task_id="b1", task_type="local_bash", tool_use_id="toolu_b",
                      description="Sleep", is_backgrounded=True))
    [shell] = agent.shell_notice_state().shells
    assert (shell.owner, shell.command, shell.description, shell.tool_use_id) == (
        ShellOwner.MAIN, "sleep 999", "Sleep", "toolu_b",
    )


@run_async
async def test_first_level_subagent_shell_at_spawn_and_after_resume():
    agent = make_agent()
    await feed(agent,
               system("task_started", task_id="a1", task_type="local_agent", tool_use_id="toolu_agent",
                      description="Implement"),
               bash_use("toolu_b", "sleep 1", parent="toolu_agent"),
               system("task_started", task_id="b1", task_type="local_bash", tool_use_id="toolu_b",
                      is_backgrounded=True, owned_by_subagent=True),
               # SendMessage resume: new tool_use id, messages still carry the Agent id.
               system("task_started", task_id="a1", task_type="local_agent", tool_use_id="toolu_send",
                      description="Implement"),
               bash_use("toolu_b2", "sleep 2", parent="toolu_agent"),
               system("task_started", task_id="b2", task_type="local_bash", tool_use_id="toolu_b2",
                      is_backgrounded=True, owned_by_subagent=True))
    shells = {s.key: s for s in agent.shell_notice_state().shells}
    assert shells["b1"].owner is ShellOwner.SUBAGENT and shells["b1"].owner_ref == "a1"
    assert shells["b2"].owner is ShellOwner.SUBAGENT and shells["b2"].owner_ref == "a1"
    assert shells["b1"].owner_running is True
    assert shells["b1"].owner_label == "Implement"


@run_async
async def test_nested_subagent_shell_is_unresolved_then_placed():
    agent = make_agent()
    await feed(agent,
               # A nested resume has no tool_use_id key: skipped for the map, kept for labels.
               system("task_started", task_id="a79858f65df89eb0a", task_type="local_agent",
                      description="Prepare review copy"),
               system("task_started", task_id="b5lhayncg", task_type="local_bash",
                      tool_use_id="toolu_01Tojv846z1a6TkUh8k7H8jT", is_backgrounded=True,
                      owned_by_subagent=True))
    assert agent._agent_by_tool_use == {}
    [raw] = agent.shell_notice_state().shells
    assert raw.owner is ShellOwner.UNRESOLVED
    [lookup] = agent.shell_notice_lookups({"b5lhayncg"}, time.time())
    assert (lookup.tool_use_id, lookup.codex) == ("toolu_01Tojv846z1a6TkUh8k7H8jT", False)
    agent.store_shell_resolutions([OwnerFacts("b5lhayncg", "a79858f65df89eb0a", "Bash", "a663305d8fe755548",
                                              "title", None, None, None)], time.time())
    [placed] = agent.shell_notice_state().shells
    assert (placed.owner, placed.owner_ref, placed.owner_label, placed.owner_running) == (
        ShellOwner.SUBAGENT, "a79858f65df89eb0a", "Prepare review copy", True,
    )
    assert agent.shell_notice_lookups({"b5lhayncg"}, time.time()) == []


@run_async
async def test_foreground_shells_are_left_out_until_backgrounded():
    agent = make_agent()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=False))
    assert agent.shell_notice_state().shells == []
    await feed(agent, system("task_updated", task_id="b1", patch={"is_backgrounded": True}))
    assert [s.key for s in agent.shell_notice_state().shells] == ["b1"]


@run_async
async def test_output_path_from_the_tool_result():
    agent = make_agent()
    await feed(agent,
               bash_use("toolu_b", "sleep 1"),
               system("task_started", task_id="b1", task_type="local_bash", tool_use_id="toolu_b",
                      is_backgrounded=True))
    agent._note_shell_notice_stream(UserMessage(content=[ToolResultBlock(
        tool_use_id="toolu_b",
        content="Command running in background with ID: b1. Output is being written to: /tmp/x/b1.output. You will",
    )]))
    assert agent.shell_notice_state().shells[0].output_path == "/tmp/x/b1.output"


@run_async
async def test_every_removal_site_drops_the_notice_state():
    agent = make_agent()
    for task_id in ("b1", "b2", "b3"):
        await feed(agent, system("task_started", task_id=task_id, task_type="local_bash",
                                 tool_use_id=f"t{task_id}", is_backgrounded=True))
    agent.mark_shells_noticed(["b1", "b2", "b3"])
    await feed(agent, system("task_notification", task_id="b1"))
    await feed(agent, system("background_tasks_changed", tasks=[{"task_id": "b2"}, {"task_id": "b3"}]))
    await feed(agent, system("background_tasks_changed", tasks=[{"task_id": "b3"}]))
    assert agent._shell_notice_notified == {"b3"}
    # Third site: a Monitor start removes its local_bash record.
    await agent._update_live_monitor_tasks(UserMessage(
        content=[ToolResultBlock(tool_use_id="tb3", content="Monitor started (task b3, timeout 600s)")],
        tool_use_result={"taskId": "b3"},
    ))
    assert "b3" not in agent._live_shell_tasks
    assert agent._shell_notice_notified == set()


@run_async
async def test_mark_shells_noticed_keeps_only_live_shells():
    agent = make_agent()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=True))
    agent.mark_shells_noticed(["b1", "gone"])
    assert agent._shell_notice_notified == {"b1"}


@run_async
async def test_subagent_run_end_is_recorded_at_both_sites():
    agent = make_agent()
    agent._client = AsyncMock()
    await feed(agent,
               system("task_started", task_id="a1", task_type="local_agent", tool_use_id="x", description="d"),
               system("task_started", task_id="a2", task_type="local_agent", tool_use_id="y", description="d"),
               system("task_notification", task_id="a1"))
    await agent.stop_subagent("a2")
    assert set(agent._subagent_run_ended_at) == {"a1", "a2"}
    assert agent.shell_notice_state().last_subagent_run_end == max(agent._subagent_run_ended_at.values())


@run_async
async def test_main_turn_tracking_and_idle_predicate():
    agent = make_agent()
    assert agent.shell_notice_state().idle is True
    agent._shell_notice_idle_since = 1.0
    await feed(agent, system("init"))
    assert agent._main_turn_open is True and agent._shell_notice_idle_since is None
    assert agent.shell_notice_state().idle is False
    agent._note_shell_notice_stream(ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                                  is_error=False, num_turns=1, session_id="root-1"))
    assert agent.shell_notice_state().idle is True
    # A subagent's message does not open a main turn.
    agent._note_shell_notice_stream(bash_use("t", "x", parent="toolu_agent"))
    assert agent._main_turn_open is False
    agent._note_shell_notice_stream(StreamEvent(uuid="u", session_id="root-1", event={}))
    assert agent._main_turn_open is True


@run_async
async def test_monitor_wakeup_and_pending_request_are_not_idle():
    agent = make_agent()
    agent._live_monitor_tasks = {"m1"}
    assert agent.shell_notice_state().idle is False
    agent._live_monitor_tasks = set()
    agent._pending_wakeup_at = time.time() + 600
    assert agent.shell_notice_state().idle is False
    agent._pending_wakeup_at = None
    agent._pending_requests = {"r": SimpleNamespace(created_at=0.0)}
    assert agent.shell_notice_state().idle is False
    agent._pending_requests = {}
    agent.state = AgentState.STARTING
    assert agent.shell_notice_state().idle is False


def test_dead_transition_closes_the_main_turn():
    agent = make_agent()
    agent._main_turn_open = True
    agent._set_state(AgentState.DEAD)
    assert agent._main_turn_open is False


def test_hybrid_agents_keep_the_default_hooks():
    assert HybridClaudeAgent.shell_notice_state is BaseAgent.shell_notice_state
    assert HybridClaudeAgent.shell_notice_lookups is BaseAgent.shell_notice_lookups


@run_async
async def test_external_send_restarts_the_delay():
    agent = make_agent()
    agent._client = AsyncMock()
    agent._build_query_prompt = AsyncMock(return_value="prompt")
    agent._notify_state_change = AsyncMock()
    agent._clear_waiting_label = AsyncMock()
    agent._shell_notice_idle_since = 1.0
    await agent.send("ok")
    agent._note_shell_notice_stream(ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                                  is_error=False, num_turns=1, session_id="root-1"))
    # The short turn reset the delay: the next tick starts it again from now (Task 5).
    assert agent._shell_notice_idle_since is None


@run_async
async def test_external_send_clears_the_set_and_the_notice_send_does_not():
    agent = make_agent()
    agent._client = AsyncMock()
    agent._build_query_prompt = AsyncMock(return_value="prompt")
    agent._notify_state_change = AsyncMock()
    agent._clear_waiting_label = AsyncMock()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=True))
    agent.mark_shells_noticed(["b1"])
    assert await agent.send("notice", shell_notice=True) is True
    assert agent._shell_notice_notified == {"b1"}
    agent.state = AgentState.USER_TURN
    assert await agent.send("hello") is True
    assert agent._shell_notice_notified == set()


@run_async
async def test_cli_auto_turn_does_not_clear_the_set():
    agent = make_agent()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=True))
    agent.mark_shells_noticed(["b1"])
    await feed(agent, system("init"))
    agent._note_shell_notice_stream(StreamEvent(uuid="u", session_id="root-1", event={}))
    assert agent._shell_notice_notified == {"b1"}


@run_async
async def test_resolution_is_not_stored_for_a_shell_that_ended():
    agent = make_agent()
    agent.store_shell_resolutions([OwnerFacts("gone", "a1", "Bash", None, None, None, None, None)], time.time())
    assert agent._shell_notice_resolutions == {}


def test_dead_or_ephemeral_agent_has_no_state():
    agent = make_agent()
    agent.state = AgentState.DEAD
    assert agent.shell_notice_state() is None
    agent.state = AgentState.USER_TURN
    agent.ephemeral = True
    assert agent.shell_notice_state() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_shell_notice_claude.py -q`
Expected: FAIL — `AttributeError: ... '_init_shell_notice_state'`.

- [ ] **Step 3: Base agent state and default hooks**

In `src/twicc/agent/base_agent.py`, add imports (with the other `twicc` imports):

```python
from collections.abc import Collection, Iterable, Sequence

from twicc.agent.shell_notice import OwnerFacts, ShellLookup, ShellNoticeState, ShellResolution
```

There is no import cycle: `twicc.cli._drop_request.sender_header` loads no
`twicc.agent` module (checked in plan review).

At the end of `BaseAgent.__init__` (after `self._work_dirs: list[str] = []`):

```python
        self._init_shell_notice_state()
```

Add a new section after the "Background work" section:

```python
    # ------------------------------------------------------------------
    # Background shell notice (docs/plans/2026-09-27-background-shell-notice-design.md)
    # ------------------------------------------------------------------

    def _init_shell_notice_state(self) -> None:
        """Episode state of the notice (spec §5.3). Separate so test stubs built
        with ``__new__`` can call it."""
        self._shell_notice_idle_since: float | None = None
        self._shell_notice_notified: set[str] = set()
        self._shell_notice_resolutions: dict[str, ShellResolution] = {}
        self._shell_notice_task: asyncio.Task[None] | None = None

    def shell_notice_state(self) -> ShellNoticeState | None:
        """Provider hook: idle flag and backgrounded shells, owners merged.

        ``None`` skips the agent (no shell tracking, DEAD, ephemeral).
        """
        return None

    def shell_notice_lookups(self, keys: Collection[str], now: float) -> list[ShellLookup]:
        """Provider hook: the shells among ``keys`` that need the database part."""
        return []

    def _shell_notice_live_keys(self) -> set[str]:
        """Provider hook: keys of the live shell records."""
        return set()

    def _note_main_turn_opening(self) -> None:
        """A main turn opens, whatever its source (spec §3.1): restart the delay."""
        self._shell_notice_idle_since = None

    def _note_external_send(self) -> None:
        """An external send reached the agent (spec §3.2): new notice episode."""
        self._shell_notice_notified.clear()

    def _drop_shell_notice_key(self, key: str) -> None:
        """Forget a shell that ended (spec §5.3)."""
        self._shell_notice_notified.discard(key)
        self._shell_notice_resolutions.pop(key, None)

    def mark_shells_noticed(self, keys: Iterable[str]) -> None:
        """Record a sent notice, only for shells still alive (spec §7 step 5)."""
        live = self._shell_notice_live_keys()
        self._shell_notice_notified.update(key for key in keys if key in live)

    def store_shell_resolutions(self, facts: Sequence[OwnerFacts], now: float) -> None:
        """Store the database part's facts, only for shells still alive (spec §5.5)."""
        live = self._shell_notice_live_keys()
        for fact in facts:
            if fact.key not in live:
                continue
            previous = self._shell_notice_resolutions.get(fact.key)
            self._shell_notice_resolutions[fact.key] = ShellResolution(
                owner_id=fact.owner_id,
                tool_name=fact.tool_name,
                spawner_id=fact.spawner_id,
                title=fact.title,
                known=fact.known,
                running=fact.running,
                stopped_at=fact.stopped_at,
                first_attempt_at=previous.first_attempt_at if previous else now,
            )
```

- [ ] **Step 4: Claude shell records and maps**

In `src/twicc/providers/claude_code/agent/agent.py`:

Imports: add `import re` if absent, `from dataclasses import dataclass`,
`from collections import OrderedDict`, and from the SDK types `ToolUseBlock`,
`ToolResultBlock`, `StreamEvent`, `AssistantMessage`, `ResultMessage` (reuse the
existing import lines; add only the missing names). Add:

```python
from twicc.agent.shell_notice import (
    ClaudeLiveOwners,
    ShellInfo,
    ShellLookup,
    ShellNoticeState,
    ShellOwner,
    merge_resolution,
    needs_lookup,
)
```

Module level, near `_MONITOR_STARTED_RE`:

```python
# "Output is being written to: <path>." in a background Bash tool result.
_BASH_OUTPUT_PATH_RE = re.compile(r"Output is being written to: (\S+?)\.?(?:\s|$)")
# Bound of the tool_use id -> (parent, command) map (spec §5.2).
_TOOL_USE_PARENTS_MAX = 1024


@dataclass(slots=True)
class _ShellTask:
    """One live Claude shell task (spec §5.2). Mutable: backgrounding and the
    output path arrive after the start."""

    backgrounded: bool
    tool_use_id: str | None
    owner: ShellOwner
    owner_ref: str | None
    description: str | None
    command: str | None
    output_path: str | None
    started_at: float
```

In `__init__`, change the `_live_shell_tasks` annotation and add the new state
right after `self._listed_background_tasks`:

```python
        self._live_shell_tasks: dict[str, _ShellTask] = {}
```

```python
        self._init_claude_shell_notice_state()
```

Add the method (near `_update_live_tasks`):

```python
    def _init_claude_shell_notice_state(self) -> None:
        """Claude maps of the background shell notice (spec §5.2)."""
        # Open between a main turn's first sign (init, main message, send) and
        # its ResultMessage; the idle predicate reads it.
        self._main_turn_open = False
        self._agent_by_tool_use: dict[str, str] = {}
        self._agent_labels: dict[str, str] = {}
        self._tool_use_parents: OrderedDict[str, tuple[str | None, str | None]] = OrderedDict()
        self._subagent_run_ended_at: dict[str, float] = {}

    def _open_main_turn(self) -> None:
        if not self._main_turn_open:
            self._main_turn_open = True
        self._note_main_turn_opening()

    def _forget_shell_task(self, task_id: str) -> _ShellTask | None:
        """The only way a record leaves ``_live_shell_tasks`` (spec §5.2)."""
        record = self._live_shell_tasks.pop(task_id, None)
        self._drop_shell_notice_key(task_id)
        return record

    def _shell_notice_live_keys(self) -> set[str]:
        return set(self._live_shell_tasks)
```

Rewrite the `local_agent` and `local_bash` branches of `task_started` in
`_update_live_tasks`:

```python
            if task_type == "local_agent":
                self._live_background_tasks[task_id] = data.get("description") or ""
                self._agent_labels[task_id] = data.get("description") or ""
                agent_tool_use_id = data.get("tool_use_id")
                if isinstance(agent_tool_use_id, str) and agent_tool_use_id:
                    self._agent_by_tool_use[agent_tool_use_id] = task_id
                self._logger.debug(
                    "Session %s: background agent %s started (%d live)",
                    self.session_id, task_id, len(self._live_background_tasks),
                )
                self._schedule_background_work_refresh()
            elif task_type == "local_bash" and task_id not in self._live_monitor_tasks:
                backgrounded = data.get("is_backgrounded") is True
                self._live_shell_tasks[task_id] = self._new_shell_task(task_id, data, backgrounded)
                if backgrounded:
                    self._logger.debug(
                        "Session %s: background shell %s started",
                        self.session_id, task_id,
                    )
                    self._schedule_background_work_refresh()
            return
```

Add the builder:

```python
    def _new_shell_task(self, task_id: str, data: dict, backgrounded: bool) -> _ShellTask:
        """Owner resolution at the shell ``task_started`` (spec §5.2)."""
        tool_use_id = data.get("tool_use_id") if isinstance(data.get("tool_use_id"), str) else None
        parent, command = self._tool_use_parents.pop(tool_use_id, (None, None)) if tool_use_id else (None, None)
        agent_id = self._agent_by_tool_use.get(parent) if parent else None
        if agent_id:
            owner, owner_ref = ShellOwner.SUBAGENT, agent_id
        elif data.get("owned_by_subagent") is True:
            owner, owner_ref = ShellOwner.UNRESOLVED, None
        else:
            owner, owner_ref = ShellOwner.MAIN, None
        description = data.get("description")
        return _ShellTask(
            backgrounded=backgrounded,
            tool_use_id=tool_use_id,
            owner=owner,
            owner_ref=owner_ref,
            description=description if isinstance(description, str) and description else None,
            command=command,
            output_path=None,
            started_at=time.time(),
        )
```

In the `task_updated` backgrounding branch, replace
`self._live_shell_tasks.get(task_id) is False` / `self._live_shell_tasks[task_id] = True` with:

```python
            record = self._live_shell_tasks.get(task_id)
            if (
                isinstance(patch, dict)
                and patch.get("is_backgrounded") is True
                and record is not None
                and not record.backgrounded
            ):
                record.backgrounded = True
```

(keep the existing debug log and `_schedule_background_work_refresh()` inside the `if`).

Replace the terminal pop:

```python
        record = self._forget_shell_task(task_id)
        if record is not None and record.backgrounded:
            self._logger.debug(
                "Session %s: background shell %s stopped",
                self.session_id, task_id,
            )
            self._schedule_background_work_refresh()
```

After `released = self._live_background_tasks.pop(task_id, None) is not None`, add:

```python
        if released:
            self._subagent_run_ended_at[task_id] = time.time()
```

(merge it into the existing `if released:` block that logs, before the log call).

In `_reconcile_background_shells`, iterate records and remove through the helper:

```python
        gone = [
            task_id for task_id, record in self._live_shell_tasks.items()
            if record.backgrounded and task_id in self._listed_background_tasks and task_id not in listed
        ]
        for task_id in gone:
            self._forget_shell_task(task_id)
```

In `_update_live_monitor_tasks`, replace `self._live_shell_tasks.pop(task_id, None)` with
`self._forget_shell_task(task_id)`.

In `stop_subagent`, inside `if self._live_background_tasks.pop(subagent_id, None) is not None:`,
add first: `self._subagent_run_ended_at[subagent_id] = time.time()`.

In `current_background_work`, count records:

```python
            shells=sum(1 for record in self._live_shell_tasks.values() if record.backgrounded),
```

- [ ] **Step 5: Claude stream tracking, send, start, hooks**

Add the stream method:

```python
    def _note_shell_notice_stream(self, msg: object) -> None:
        """Main turn openings, tool_use parents and output paths (spec §5.2)."""
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            self._open_main_turn()
        elif isinstance(msg, (AssistantMessage, StreamEvent)) and msg.parent_tool_use_id is None:
            self._open_main_turn()
        elif isinstance(msg, ResultMessage):
            self._main_turn_open = False

        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    command = block.input.get("command") if block.name == "Bash" else None
                    self._tool_use_parents[block.id] = (
                        msg.parent_tool_use_id, command if isinstance(command, str) else None,
                    )
                    while len(self._tool_use_parents) > _TOOL_USE_PARENTS_MAX:
                        self._tool_use_parents.popitem(last=False)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if not isinstance(block, ToolResultBlock):
                    continue
                # A string, or a list of {"type": "text", "text": ...} parts.
                if isinstance(block.content, str):
                    text = block.content
                elif isinstance(block.content, list):
                    text = "\n".join(part.get("text", "") for part in block.content if isinstance(part, dict))
                else:
                    text = ""
                match = _BASH_OUTPUT_PATH_RE.search(text)
                if match is None:
                    continue
                for record in self._live_shell_tasks.values():
                    if record.tool_use_id == block.tool_use_id:
                        record.output_path = match.group(1)
```

Call it at the top of the message loop, right after the `if msg is None: continue`
guard (`agent.py` ~line 1945):

```python
                self._note_shell_notice_stream(msg)
```

In `send()`: add the keyword argument `shell_notice: bool = False` to the
signature, document it ("``True`` for TwiCC's background shell notice: the
notice does not start a new notice episode"), and right after the two state
checks (before the `try`):

```python
        if not shell_notice:
            self._note_external_send()
        self._open_main_turn()
```

In `start()`, right after `self._state_change_callback = on_state_change`:

```python
        self._note_external_send()
        self._open_main_turn()
```

Override `_set_state` (no override exists today in this class), so every path
to DEAD closes the main turn:

```python
    def _set_state(self, new_state: AgentState) -> None:
        super()._set_state(new_state)
        if new_state == AgentState.DEAD:
            self._main_turn_open = False
```

Add the two hooks:

```python
    def _shell_notice_idle(self) -> bool:
        """Spec §3.1, Claude predicate."""
        return (
            self.state in (AgentState.USER_TURN, AgentState.ASSISTANT_TURN)
            and not self._main_turn_open
            and not self._live_monitor_tasks
            and not self._has_pending_wakeup()
            and not self.pending_requests
        )

    def shell_notice_state(self) -> ShellNoticeState | None:
        if self.state == AgentState.DEAD or getattr(self, "ephemeral", False):
            return None
        now = time.time()
        live = ClaudeLiveOwners(
            labels=self._agent_labels,
            running=self._live_background_tasks.keys(),
            ended=self._subagent_run_ended_at,
        )
        shells: list[ShellInfo] = []
        for task_id, record in self._live_shell_tasks.items():
            if not record.backgrounded:
                continue
            owner_ref = record.owner_ref
            raw = ShellInfo(
                key=task_id,
                shell_id=task_id,
                tool_use_id=record.tool_use_id,
                owner=record.owner,
                owner_ref=owner_ref,
                owner_label=self._agent_labels.get(owner_ref) if owner_ref else None,
                owner_spawner_ref=None,
                owner_run_ended_at=self._subagent_run_ended_at.get(owner_ref) if owner_ref else None,
                owner_running=bool(owner_ref) and owner_ref in self._live_background_tasks,
                description=record.description,
                command=record.command,
                output_path=record.output_path,
                started_at=record.started_at,
            )
            merged = merge_resolution(raw, self._shell_notice_resolutions.get(task_id), now=now, claude_live=live)
            if merged is not None:
                shells.append(merged)
        return ShellNoticeState(
            idle=self._shell_notice_idle(),
            shells=shells,
            any_subagent_running=bool(self._live_background_tasks),
            last_subagent_run_end=max(self._subagent_run_ended_at.values(), default=0.0),
        )

    def shell_notice_lookups(self, keys: Collection[str], now: float) -> list[ShellLookup]:
        lookups = []
        for task_id in keys:
            record = self._live_shell_tasks.get(task_id)
            if record is None or record.owner is not ShellOwner.UNRESOLVED:
                continue
            if needs_lookup(self._shell_notice_resolutions.get(task_id), codex=False, now=now):
                lookups.append(ShellLookup(key=task_id, tool_use_id=record.tool_use_id, owner_id=None, codex=False))
        return lookups
```

(`Collection` comes from `collections.abc`; add it to the imports.)

- [ ] **Step 6: Update the existing tests**

`tests/test_background_work.py`:
- In `_claude_agent()`, before `return`: `agent._pending_requests = {}`,
  `agent._init_shell_notice_state()`, `agent._init_claude_shell_notice_state()`.
- In `_codex_agent()`, before `return`: `agent._init_shell_notice_state()` (Task 4 adds
  its own init call too).
- Every direct write of a bool into `_live_shell_tasks` (`:116`, `:632`, `:714`,
  and any other `agent._live_shell_tasks[...] = True/False` found by
  `grep -n "_live_shell_tasks\[" tests/test_background_work.py`) becomes a record.
  (`:395`, `:404`, `:491`, `:509` are Codex `_live_shells` float writes: Task 4
  handles them, not this task.)
  Add this helper at the top of the file and use it:

```python
from twicc.agent.shell_notice import ShellOwner
from twicc.providers.claude_code.agent.agent import _ShellTask


def _shell(backgrounded: bool = True) -> _ShellTask:
    return _ShellTask(backgrounded=backgrounded, tool_use_id=None, owner=ShellOwner.MAIN, owner_ref=None,
                      description=None, command=None, output_path=None, started_at=time.time())
```

  e.g. `agent._live_shell_tasks["b1"] = True` → `agent._live_shell_tasks["b1"] = _shell()`,
  `= False` → `_shell(False)`. Assertions comparing `_live_shell_tasks` to bool dicts
  compare `{k: v.backgrounded for k, v in agent._live_shell_tasks.items()}` instead.

`tests/test_claude_monitor_liveness.py:39`: the `_agent` stub gets the same three
lines (`_pending_requests = {}`, both `_init_*` calls); any bool written into
`_live_shell_tasks` becomes a `_ShellTask` built the same way.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_shell_notice_claude.py tests/test_background_work.py tests/test_claude_monitor_liveness.py tests/test_claude_subagent_lifecycle.py -q`
Expected: PASS.

Then the whole suite: `uv run pytest -q -p no:cacheprovider` — Expected: PASS
(any other test that builds a `ClaudeCodeAgent` with `__new__` and reaches the
changed code fails with an `AttributeError`; give it the same stub lines).

- [ ] **Step 8: Lint and commit**

```bash
uvx ruff check src/twicc/agent/base_agent.py src/twicc/providers/claude_code/agent/agent.py tests/shell_notice_helpers.py tests/test_shell_notice_claude.py tests/test_background_work.py tests/test_claude_monitor_liveness.py
git add src/twicc/agent/base_agent.py src/twicc/providers/claude_code/agent/agent.py tests/shell_notice_helpers.py tests/test_shell_notice_claude.py tests/test_background_work.py tests/test_claude_monitor_liveness.py
git commit -m "feat(claude): track background shell owners and idle episodes"
```

---

### Task 4: Codex bookkeeping

Spec: §3.1 (Codex predicate), §3.2, §5.4 (whole section), §5.5 (Codex run state).

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py`
- Modify: `src/twicc/providers/codex/agent/manager.py`
- Create: `tests/test_shell_notice_codex.py`
- Modify: `tests/test_background_work.py`, `tests/test_codex_agent_runs_live_process.py`,
  `tests/test_codex_subagent_hold.py`, `tests/test_codex_subagent_wait_label.py`,
  `tests/test_ephemeral_providers.py`

**Interfaces:**
- Consumes: Task 1-3 (`BaseAgent` hooks, `merge_resolution`, `needs_lookup`).
- Produces (on `CodexAgent`): `send()` returns `True` in `USER_TURN`; `send(..., shell_notice=True)`
  accepted; `drop_gone_shells(probed_at, keys=None)`; `live_shell_keys() -> set[tuple[str, str]]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shell_notice_codex.py`:

```python
"""Codex bookkeeping for the background shell notice (spec §3.1, §3.2, §5.4)."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests.shell_notice_helpers import first_step, run_async
from twicc.agent.shell_notice import OwnerFacts, ShellOwner
from twicc.agent.states import AgentState
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.manager import CodexAgentManager


def make_agent():
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "codex-root"
    agent.state = AgentState.USER_TURN
    agent._live_subagents = {}
    agent._live_shells = {}
    agent._first_shell_started_at = None
    agent._recently_ended_shells = {}
    agent._goal_continuation_active = False
    agent._subagent_hold_active = False
    agent._manual_compaction = False
    agent._current_turn = None
    agent._pending_requests = {}
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._background_work_broadcast_failures = 0
    agent._subagent_set_lock = asyncio.Lock()
    agent._dead_event = asyncio.Event()
    agent._init_shell_notice_state()
    agent._init_codex_shell_notice_state()
    return agent


def command_event(method, process_id, command="sleep 60"):
    item = SimpleNamespace(type="commandExecution", process_id=process_id, command=command)
    return method, SimpleNamespace(thread_id="codex-root", item=item, started_at_ms=None)


def activity(kind, thread_id, path):
    return SimpleNamespace(agent_thread_id=thread_id, kind=kind, agent_path=path)


def test_own_shell_keeps_its_command():
    agent = make_agent()
    agent._note_command_execution(*command_event("item/started", "7"))
    [shell] = agent.shell_notice_state().shells
    assert (shell.key, shell.shell_id, shell.owner, shell.command) == ("codex-root:7", "7", ShellOwner.MAIN, "sleep 60")


@run_async
async def test_first_level_owner_path_survives_the_run_end():
    agent = make_agent()
    agent._note_sub_agent_activity(activity("started", "t1", "/root/review"))
    await agent.notify_shells_started("t1", {"9": time.time()})
    [shell] = agent.shell_notice_state().shells
    assert (shell.owner, shell.owner_ref, shell.owner_running) == (ShellOwner.SUBAGENT, "/root/review", True)
    agent._note_sub_agent_activity(activity("completed", "t1", "/root/review"))
    [shell] = agent.shell_notice_state().shells
    assert shell.owner_running is False and shell.owner_run_ended_at is not None
    assert agent._subagent_paths == {"t1": "/root/review"}


@run_async
async def test_unknown_thread_is_unresolved_and_needs_the_run_model():
    agent = make_agent()
    await agent.notify_shells_started("nested-1", {"4": time.time()})
    [shell] = agent.shell_notice_state().shells
    assert shell.owner is ShellOwner.UNRESOLVED
    [lookup] = agent.shell_notice_lookups({"nested-1:4"}, time.time())
    assert (lookup.owner_id, lookup.codex) == ("nested-1", True)
    agent.store_shell_resolutions([OwnerFacts("nested-1:4", "nested-1", None, "t1", "Nested", True, False, 5.0)],
                                  time.time())
    [placed] = agent.shell_notice_state().shells
    assert (placed.owner, placed.owner_ref, placed.owner_spawner_ref, placed.owner_run_ended_at) == (
        ShellOwner.SUBAGENT, "nested-1", "t1", 5.0,
    )
    # Codex re-reads the run state at each tick.
    assert len(agent.shell_notice_lookups({"nested-1:4"}, time.time())) == 1


def test_idle_predicate():
    agent = make_agent()
    assert agent.shell_notice_state().idle is True
    agent.state = AgentState.ASSISTANT_TURN
    assert agent.shell_notice_state().idle is False
    agent._subagent_hold_active = True
    assert agent.shell_notice_state().idle is True
    agent._goal_continuation_active = True
    assert agent.shell_notice_state().idle is False
    agent._goal_continuation_active = False
    agent._manual_compaction = True
    assert agent.shell_notice_state().idle is False


@run_async
async def test_send_returns_true_in_user_turn_and_clears_only_on_external_send():
    agent = make_agent()
    agent._notify_state_change = AsyncMock()
    agent._schedule_turn = lambda text, images: None
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    assert await agent.send("notice", shell_notice=True) is True
    assert agent._shell_notice_notified == {"codex-root:7"}
    agent.state = AgentState.USER_TURN
    assert await agent.send("hello") is True
    assert agent._shell_notice_notified == set()


@run_async
async def test_hold_break_and_steer_are_external_sends():
    agent = make_agent()
    agent._broadcast_process_label = AsyncMock()
    agent._schedule_turn = lambda text, images: None
    agent._note_command_execution(*command_event("item/started", "7"))
    # Hold break: ASSISTANT_TURN parked in the hold, no active turn.
    agent.state = AgentState.ASSISTANT_TURN
    agent._subagent_hold_active = True
    agent.mark_shells_noticed(["codex-root:7"])
    assert await agent.send("hello") is True
    assert agent._shell_notice_notified == set()
    # Steer: a real turn is running.
    agent._subagent_hold_active = False
    agent._current_turn_ready = asyncio.Event()
    agent._current_turn_ready.set()
    agent._current_turn = MagicMock(steer=AsyncMock())
    agent._build_turn_input = AsyncMock(return_value="input")
    agent.mark_shells_noticed(["codex-root:7"])
    assert await agent.send("more") is True
    assert agent._shell_notice_notified == set()


@run_async
async def test_hardcoded_command_clears_the_set():
    agent = make_agent()
    agent.run_plan_command = AsyncMock()
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    await agent.run_hardcoded_command(SimpleNamespace(name="plan", args="do it"))
    assert agent._shell_notice_notified == set()


def test_forget_shell_drops_the_notice_state():
    agent = make_agent()
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    agent._note_command_execution(*command_event("item/completed", "7"))
    assert agent._shell_notice_notified == set()


def test_drop_gone_shells_only_drops_the_snapshot():
    agent = make_agent()
    agent._track_shell(("codex-root", "1"), 100.0)
    agent._track_shell(("t1", "2"), 100.0)
    snapshot = {("codex-root", "1")}
    assert agent.drop_gone_shells(1000.0, keys=snapshot) is True
    assert set(agent._live_shells) == {("t1", "2")}


def test_subagent_run_end_recorded_at_every_pop_site():
    agent = make_agent()
    agent._note_sub_agent_activity(activity("started", "t1", "/root/a"))
    agent._note_sub_agent_activity(activity("interrupted", "t1", "/root/a"))
    assert "t1" in agent._subagent_run_ended_at


@run_async
async def test_run_end_recorded_by_the_prune(monkeypatch):
    agent = make_agent()
    agent._live_subagents = {"t1": "/root/a", "t2": "/root/b"}
    monkeypatch.setattr("twicc.providers.codex.agent.agent._stopped_subagent_ids",
                        lambda root_id, session_ids: ["t1"])
    await agent._prune_finished_subagents()
    assert set(agent._subagent_run_ended_at) == {"t1"}
    agent.ephemeral = True
    agent._ephemeral_finished_subagents = AsyncMock(return_value=["t2"])
    await agent._prune_finished_subagents()
    assert set(agent._subagent_run_ended_at) == {"t1", "t2"}


@run_async
async def test_run_end_recorded_by_the_stop_relay(monkeypatch):
    agent = make_agent()
    agent._live_subagents = {"t1": "/root/a"}
    monkeypatch.setattr("twicc.providers.codex.agent.agent._stopped_subagent_ids",
                        lambda root_id, session_ids: ["t1"])
    await agent._apply_subagents_stopped(["t1"], retry_on_error=False)
    assert "t1" in agent._subagent_run_ended_at


@run_async
async def test_plan_implementation_turn_is_an_external_send():
    from twicc.providers.codex.agent.agent import _PLAN_IMPLEMENTATION_MESSAGE

    agent = make_agent()
    agent._run_turn = AsyncMock()
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    await agent._run_plan_implementation_turn()
    assert agent._shell_notice_notified == set()
    agent._run_turn.assert_awaited_once_with(_PLAN_IMPLEMENTATION_MESSAGE, None)


def test_turn_openings_restart_the_delay():
    for opener in (
        lambda agent: agent._run_turn("x", None),
        lambda agent: agent._run_goal_continuation(MagicMock()),
        lambda agent: agent.compact(),
    ):
        agent = make_agent()
        agent._shell_notice_idle_since = 1.0
        first_step(opener(agent))
        assert agent._shell_notice_idle_since is None


def _probe_manager(monkeypatch):
    manager = CodexAgentManager.__new__(CodexAgentManager)
    manager._state_based_timeout = lambda agent, now: None
    monkeypatch.setattr("twicc.providers.codex.agent.manager.command_processes_may_run",
                        lambda pid, since: False)
    return manager


def _probed_agent(state, hold):
    agent = MagicMock()
    agent.state = state
    agent.in_subagent_hold.return_value = hold
    agent.has_live_shells.return_value = True
    agent.shell_probe.return_value = (123, 0.0)
    agent.live_shell_keys.return_value = {("t1", "2")}
    agent.drop_gone_shells.return_value = False
    return agent


def test_reconciliation_runs_in_user_turn_and_in_the_hold_with_a_snapshot(monkeypatch):
    manager = _probe_manager(monkeypatch)
    for state, hold in ((AgentState.USER_TURN, False), (AgentState.ASSISTANT_TURN, True)):
        agent = _probed_agent(state, hold)
        asyncio.run(manager._check_agent_timeout(agent, time.time()))
        assert agent.drop_gone_shells.call_args.kwargs == {"keys": {("t1", "2")}}


def test_reconciliation_skips_a_working_turn(monkeypatch):
    manager = _probe_manager(monkeypatch)
    agent = _probed_agent(AgentState.ASSISTANT_TURN, False)
    asyncio.run(manager._check_agent_timeout(agent, time.time()))
    agent.drop_gone_shells.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_shell_notice_codex.py -q`
Expected: FAIL — `AttributeError: ... '_init_codex_shell_notice_state'`.

- [ ] **Step 3: Codex records, maps and hooks**

In `src/twicc/providers/codex/agent/agent.py`:

Imports:

```python
from collections.abc import Collection

from twicc.agent.shell_notice import (
    ShellInfo,
    ShellLookup,
    ShellNoticeState,
    ShellOwner,
    merge_resolution,
    needs_lookup,
)
```

Module level:

```python
class _TrackedShell(NamedTuple):
    """One live unified-exec process (spec §5.4)."""

    started_at: float
    command: str | None
```

(`NamedTuple` from `typing`; add to the imports if missing.)

`__init__`: `self._live_shells: dict[tuple[str, str], _TrackedShell] = {}` and,
after the `_live_subagents` block, `self._init_codex_shell_notice_state()`.

```python
    def _init_codex_shell_notice_state(self) -> None:
        """Codex maps of the background shell notice (spec §5.4)."""
        self._subagent_paths: dict[str, str] = {}
        self._subagent_run_ended_at: dict[str, float] = {}

    def _note_subagent_run_end(self, thread_id: str) -> None:
        self._subagent_run_ended_at[thread_id] = time.time()
```

`_note_sub_agent_activity`: in the add branch, after setting `_live_subagents[thread_id]`:
`self._subagent_paths[thread_id] = self._live_subagents[thread_id]`. In the pop branch:

```python
            if self._live_subagents.pop(thread_id, None) is not None:
                self._note_subagent_run_end(thread_id)
                self._schedule_background_work_refresh()
```

`_prune_finished_subagents`: after each `self._live_subagents.pop(child_id, None)`
(`:1355`) and `self._live_subagents.pop(session_id, None)` (`:1366`), call
`self._note_subagent_run_end(<id>)` when the pop returned a value:

```python
                for child_id in stopped:
                    if self._live_subagents.pop(child_id, None) is not None:
                        self._note_subagent_run_end(child_id)
```

```python
            for session_id in stopped:
                if self._live_subagents.pop(session_id, None) is not None:
                    self._note_subagent_run_end(session_id)
```

`_apply_subagents_stopped` (`:1481`):

```python
            for agent_id in stopped:
                if self._live_subagents.pop(agent_id, None) is not None:
                    self._note_subagent_run_end(agent_id)
                    changed = True
```

`notify_subagents_resumed` (`:1559`): after `self._live_subagents[agent_id] = paths[agent_id]`,
add `self._subagent_paths[agent_id] = paths[agent_id]`.

`_note_command_execution`: pass the command:

```python
            command = getattr(inner, "command", None)
            self._track_shell(key, started_at, command if isinstance(command, str) else None)
```

`_forget_shell` / `_track_shell`:

```python
    def _forget_shell(self, key: tuple[str, str]) -> bool:
        self._drop_shell_notice_key(f"{key[0]}:{key[1]}")
        return self._live_shells.pop(key, None) is not None

    def _track_shell(self, key: tuple[str, str], started_at: float, command: str | None = None) -> None:
        self._live_shells[key] = _TrackedShell(started_at, command)
        if self._first_shell_started_at is None or started_at < self._first_shell_started_at:
            self._first_shell_started_at = started_at
```

`drop_gone_shells`:

```python
    def drop_gone_shells(self, probed_at: float, keys: Collection[tuple[str, str]] | None = None) -> bool:
        """... (keep the docstring) ``keys`` is the snapshot of the tracked keys
        taken before the probe (spec §5.4): a shell tracked while the probe ran
        is never dropped."""
        cutoff = probed_at - _SHELL_RECONCILE_MIN_AGE_SECONDS
        dropped = [
            key for key, tracked in list(self._live_shells.items())
            if tracked.started_at <= cutoff and (keys is None or key in keys)
        ]
        for key in dropped:
            self._forget_shell(key)
        ...  # keep the existing log and return
```

Add:

```python
    def live_shell_keys(self) -> set[tuple[str, str]]:
        return set(self._live_shells)

    def _shell_notice_live_keys(self) -> set[str]:
        return {f"{thread_id}:{process_id}" for thread_id, process_id in self._live_shells}

    def _shell_notice_idle(self) -> bool:
        """Spec §3.1, Codex predicate."""
        return (
            (self.state == AgentState.USER_TURN or self.in_subagent_hold())
            and not self.in_goal_continuation()
            and not self._manual_compaction
            and not self.pending_requests
        )

    def shell_notice_state(self) -> ShellNoticeState | None:
        if self.state == AgentState.DEAD or getattr(self, "ephemeral", False):
            return None
        now = time.time()
        shells: list[ShellInfo] = []
        for (thread_id, process_id), tracked in self._live_shells.items():
            key = f"{thread_id}:{process_id}"
            if thread_id == self.session_id:
                owner, owner_ref, running, ended = ShellOwner.MAIN, None, False, None
            elif thread_id in self._subagent_paths:
                owner = ShellOwner.SUBAGENT
                owner_ref = self._subagent_paths[thread_id] or thread_id
                running = thread_id in self._live_subagents
                ended = self._subagent_run_ended_at.get(thread_id)
            else:
                owner, owner_ref, running, ended = ShellOwner.UNRESOLVED, None, False, None
            raw = ShellInfo(
                key=key, shell_id=process_id, tool_use_id=None, owner=owner, owner_ref=owner_ref,
                owner_label=None, owner_spawner_ref=None, owner_run_ended_at=ended, owner_running=running,
                description=None, command=tracked.command, output_path=None, started_at=tracked.started_at,
            )
            merged = merge_resolution(raw, self._shell_notice_resolutions.get(key), now=now, claude_live=None)
            if merged is not None:
                shells.append(merged)
        return ShellNoticeState(
            idle=self._shell_notice_idle(),
            shells=shells,
            any_subagent_running=bool(self._live_subagents),
            last_subagent_run_end=max(self._subagent_run_ended_at.values(), default=0.0),
        )

    def shell_notice_lookups(self, keys: Collection[str], now: float) -> list[ShellLookup]:
        lookups = []
        for thread_id, process_id in self._live_shells:
            key = f"{thread_id}:{process_id}"
            if key not in keys or thread_id == self.session_id or thread_id in self._subagent_paths:
                continue
            if needs_lookup(self._shell_notice_resolutions.get(key), codex=True, now=now):
                lookups.append(ShellLookup(key=key, tool_use_id=None, owner_id=thread_id, codex=True))
        return lookups
```

- [ ] **Step 4: Codex turn openings, external sends and `send()` return**

- `send()`: at the very top (before the DEAD check is fine too; put it after it):

```python
        if not kwargs.get("shell_notice"):
            self._note_external_send()
        self._note_main_turn_opening()
```

  and at the end of the `USER_TURN` branch, after `self._schedule_turn(text, images)`:
  `return True`.
- `run_hardcoded_command()`: first line `self._note_external_send()`.
- `_prompt_plan_implementation()`: replace `await self._run_turn(_PLAN_IMPLEMENTATION_MESSAGE, None)`
  with `await self._run_plan_implementation_turn()`, and add:

```python
    async def _run_plan_implementation_turn(self) -> None:
        """The plan prompt's "implement" answer: a human decision, so an external send (spec §3.2)."""
        self._note_external_send()
        await self._run_turn(_PLAN_IMPLEMENTATION_MESSAGE, None)
```
- `start()`: first line of the body `self._note_external_send()`.
- `_run_turn()`: first line of the body `self._note_main_turn_opening()`.
- `_run_goal_continuation()`: first line of the body `self._note_main_turn_opening()`.
- `compact()`: first line of the body `self._note_main_turn_opening()`.

- [ ] **Step 5: Reconciliation in the hold, with a snapshot**

In `src/twicc/providers/codex/agent/manager.py`, `_check_agent_timeout`:

```python
        in_idle_state = agent.state == AgentState.USER_TURN or agent.in_subagent_hold()
        if in_idle_state and agent.has_live_shells():
            try:
                probe = agent.shell_probe()
                if probe is not None:
                    keys = agent.live_shell_keys()
                    probed_at = time.time()
                    may_run = await asyncio.to_thread(command_processes_may_run, *probe)
                    if may_run is False and agent.drop_gone_shells(probed_at, keys=keys):
                        agent._schedule_background_work_refresh()
```

(keep the `except` and the return unchanged; update the docstring: "an idle
agent — `USER_TURN` or the subagent hold — …").

- [ ] **Step 6: Update the existing tests**

- `tests/test_background_work.py`: `_codex_agent()` also sets
  `agent._subagent_hold_active = False`, `agent._manual_compaction = False`,
  `agent._pending_requests = {}` and calls `agent._init_codex_shell_notice_state()`.
  Every float written into `_live_shells` (`:301` asserts `940.0`; grep
  `_live_shells\[` in the file) becomes `_TrackedShell(<float>, None)`
  (import it from `twicc.providers.codex.agent.agent`), and assertions read
  `.started_at`. `:318` (`drop_gone_shells(now)`) stays as is.
- `tests/test_codex_agent_runs_live_process.py:133` (`make_agent`): add
  `agent._init_shell_notice_state()` and `agent._init_codex_shell_notice_state()`.
- `tests/test_codex_subagent_hold.py` (`_agent`, ~`:51`): add `agent._pending_requests = {}`,
  `agent._init_shell_notice_state()` and `agent._init_codex_shell_notice_state()`
  (it calls `_note_sub_agent_activity` and `send()`).
- `tests/test_codex_subagent_wait_label.py`: the `_agent` stub (~`:55`) **and**
  the inline stub of `TestPruningAgainstTheWatcher::test_only_stopped_children_are_dropped`
  (~`:217`): add `agent._init_shell_notice_state()` and
  `agent._init_codex_shell_notice_state()` (they reach `_note_sub_agent_activity`
  and `_prune_finished_subagents`).
- `tests/test_ephemeral_providers.py::test_codex_ephemeral_hold_uses_runtime_status_without_watcher`:
  its inline `CodexAgent.__new__` stub (~`:197`) gets the same two calls (it
  reaches `_apply_subagents_stopped`).

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_shell_notice_codex.py tests/test_background_work.py tests/test_codex_agent_runs_live_process.py tests/test_codex_subagent_hold.py tests/test_codex_subagent_wait_label.py tests/test_ephemeral_providers.py -q`
Expected: PASS. Then `uv run pytest -q -p no:cacheprovider` — Expected: PASS
(fix any other `__new__` stub the same way).

- [ ] **Step 8: Lint and commit**

```bash
uvx ruff check src/twicc/providers/codex/agent/agent.py src/twicc/providers/codex/agent/manager.py tests/test_shell_notice_codex.py tests/test_background_work.py tests/test_codex_agent_runs_live_process.py tests/test_codex_subagent_hold.py tests/test_codex_subagent_wait_label.py tests/test_ephemeral_providers.py
git add src/twicc/providers/codex/agent/agent.py src/twicc/providers/codex/agent/manager.py tests/test_shell_notice_codex.py tests/test_background_work.py tests/test_codex_agent_runs_live_process.py tests/test_codex_subagent_hold.py tests/test_codex_subagent_wait_label.py tests/test_ephemeral_providers.py
git commit -m "feat(codex): track background shell owners and idle episodes"
```

The commit body must mention the `send()` fix: a Codex send in `USER_TURN` now
returns `True`, so the WS delivery ack is sent (spec §5.4).

---

### Task 5: Manager tick step and `send_shell_notice`

Spec: §4 (whole section), §7 (whole section).

**Files:**
- Modify: `src/twicc/agent/base_manager.py`
- Modify: `src/twicc/providers/codex/agent/manager.py`
- Create: `tests/test_shell_notice_manager.py`

**Interfaces:**
- Consumes: Tasks 1-4 hooks; `resolve_shell_owners`; `select_concerned_shells`; `build_shell_notice`.
- Produces (on `BaseAgentManager`): `async _shell_notice_step(agent: BaseAgent) -> None`,
  `async send_shell_notice(agent: BaseAgent, now: float) -> None`,
  `_send_gate(session_id: str) -> AbstractAsyncContextManager`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shell_notice_manager.py`:

```python
"""Manager side of the background shell notice (spec §4, §7)."""

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock

from tests.shell_notice_helpers import run_async
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.shell_notice import (
    SHELL_NOTICE_DELAY_SECONDS,
    ShellInfo,
    ShellLookup,
    ShellNoticeState,
    ShellOwner,
)
from twicc.agent.states import AgentState


def shell(key="b1", owner=ShellOwner.MAIN, started_at=0.0):
    return ShellInfo(key=key, shell_id=key, tool_use_id=None, owner=owner, owner_ref=None, owner_label=None,
                     owner_spawner_ref=None, owner_run_ended_at=None, owner_running=False, description=None,
                     command=None, output_path=None, started_at=started_at)


class FakeAgent:
    def __init__(self, shells, *, idle=True, lookups=()):
        self.session_id = "s1"
        self.state = AgentState.USER_TURN
        self._state = ShellNoticeState(idle=idle, shells=list(shells), any_subagent_running=False,
                                       last_subagent_run_end=0.0)
        self._lookups = list(lookups)
        self._shell_notice_idle_since = None
        self._shell_notice_notified = set()
        self._shell_notice_task = None
        self.stored = []
        self.send = AsyncMock(return_value=True)

    def shell_notice_state(self):
        return self._state

    def shell_notice_lookups(self, keys, now):
        return [lookup for lookup in self._lookups if lookup.key in keys]

    def store_shell_resolutions(self, facts, now):
        self.stored.extend(facts)

    def mark_shells_noticed(self, keys):
        self._shell_notice_notified.update(keys)


def make_manager(agent):
    manager = BaseAgentManager.__new__(BaseAgentManager)
    manager._agents = {agent.session_id: agent}
    manager._lock = asyncio.Lock()
    return manager


async def run_step(manager, agent):
    await manager._shell_notice_step(agent)
    if agent._shell_notice_task is not None:
        await agent._shell_notice_task


@run_async
async def test_first_idle_tick_only_starts_the_delay():
    agent = FakeAgent([shell()])
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert agent._shell_notice_idle_since is not None
    agent.send.assert_not_awaited()


@run_async
async def test_sends_once_after_the_delay():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    manager = make_manager(agent)
    await run_step(manager, agent)
    agent.send.assert_awaited_once()
    text = agent.send.await_args.args[0]
    assert text.startswith(":: notice from TwiCC")
    assert agent.send.await_args.kwargs == {"shell_notice": True}
    assert agent._shell_notice_notified == {"b1"}
    agent._shell_notice_task = None
    await run_step(manager, agent)
    agent.send.assert_awaited_once()


@run_async
async def test_not_idle_resets_idle_since_and_sends_nothing():
    agent = FakeAgent([shell()], idle=False)
    agent._shell_notice_idle_since = 1.0
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert agent._shell_notice_idle_since is None
    agent.send.assert_not_awaited()


@run_async
async def test_failed_send_adds_nothing_and_retries():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    agent.send = AsyncMock(return_value=False)
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert agent._shell_notice_notified == set()
    agent._shell_notice_task = None
    agent.send = AsyncMock(side_effect=RuntimeError("boom"))
    await run_step(manager, agent)
    assert agent._shell_notice_notified == set()


@run_async
async def test_replaced_agent_gets_nothing():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    manager = make_manager(agent)
    manager._agents = {"s1": object()}
    await run_step(manager, agent)
    agent.send.assert_not_awaited()


@run_async
async def test_stale_task_does_not_send():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)
    await manager.send_shell_notice(agent, time.time() - 31)
    agent.send.assert_not_awaited()


@run_async
async def test_database_part_runs_only_after_the_cheap_filter(monkeypatch):
    calls = []
    monkeypatch.setattr("twicc.agent.base_manager.resolve_shell_owners",
                        lambda root_id, lookups: calls.append(list(lookups)) or [])
    fresh = shell("b1", owner=ShellOwner.UNRESOLVED, started_at=time.time())
    agent = FakeAgent([fresh], lookups=[ShellLookup("b1", "t", None, False)])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert calls == []
    old = shell("b1", owner=ShellOwner.UNRESOLVED, started_at=0.0)
    agent._state = agent._state._replace(shells=[old])
    await run_step(manager, agent)
    assert calls == [[ShellLookup("b1", "t", None, False)]]


@run_async
async def test_running_task_skips_the_step(monkeypatch):
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    agent._shell_notice_task = asyncio.get_running_loop().create_future()
    manager = make_manager(agent)
    await manager._shell_notice_step(agent)
    agent.send.assert_not_awaited()
    agent._shell_notice_task.cancel()


@run_async
async def test_one_agent_error_does_not_stop_the_others():
    broken = FakeAgent([shell()])
    broken.session_id = "broken"
    broken.shell_notice_state = MagicMock(side_effect=RuntimeError("boom"))
    healthy = FakeAgent([shell()])
    manager = make_manager(healthy)
    # The broken agent comes first, so the healthy one is checked after the error.
    manager._agents = {"broken": broken, healthy.session_id: healthy}
    manager._check_agent_timeout = AsyncMock(return_value=None)
    await manager.check_and_stop_timed_out_agents()
    assert healthy._shell_notice_idle_since is not None


@run_async
async def test_send_rechecks_idle_under_the_lock():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)
    # A turn opened between the tick and the send.
    agent._state = agent._state._replace(idle=False)
    await manager.send_shell_notice(agent, time.time())
    agent.send.assert_not_awaited()


@run_async
async def test_a_shell_concerned_later_gets_its_own_single_notice():
    agent = FakeAgent([shell("b1")])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    manager = make_manager(agent)
    await run_step(manager, agent)
    agent._shell_notice_task = None
    agent._state = agent._state._replace(shells=[shell("b1"), shell("b2")])
    await run_step(manager, agent)
    agent._shell_notice_task = None
    await run_step(manager, agent)
    assert agent.send.await_count == 2
    assert "`b2`" in agent.send.await_args_list[1].args[0]
    assert "`b1`" not in agent.send.await_args_list[1].args[0]


@run_async
async def test_gate_is_taken_before_the_lock():
    order = []
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)

    @contextlib.asynccontextmanager
    async def gate(session_id):
        order.append("gate")
        assert not manager._lock.locked()
        yield

    manager._send_gate = gate
    await manager.send_shell_notice(agent, time.time())
    assert order == ["gate"]
    agent.send.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_shell_notice_manager.py -q`
Expected: FAIL — `AttributeError: 'BaseAgentManager' object has no attribute '_shell_notice_step'`.

- [ ] **Step 3: Implement the manager side**

In `src/twicc/agent/base_manager.py`, imports:

```python
import contextlib

from twicc.agent.shell_notice import (
    SHELL_NOTICE_DELAY_SECONDS,
    SHELL_NOTICE_STALE_SECONDS,
    build_shell_notice,
    earliest_delay_start,
    resolve_shell_owners,
    select_concerned_shells,
)
```

In `check_and_stop_timed_out_agents`, replace the `if decision is None: continue` with:

```python
            if decision is None:
                try:
                    await self._shell_notice_step(agent)
                except Exception:
                    logger.warning(
                        "Background shell notice step failed for session %s",
                        session_id, exc_info=True,
                    )
                continue
```

Add, after `check_and_stop_timed_out_agents`:

```python
    def _send_gate(self, session_id: str) -> contextlib.AbstractAsyncContextManager:
        """Per-session gate taken before ``self._lock`` by a send (spec §7 step 1).

        None by default; the Codex manager returns its migration gate, in the
        same order as ``send_to_session``.
        """
        return contextlib.nullcontext()

    async def _shell_notice_step(self, agent: BaseAgent) -> None:
        """One tick of the background shell notice for ``agent`` (spec §4)."""
        state = agent.shell_notice_state()
        if state is None:
            return
        now = time.time()
        if not state.idle:
            agent._shell_notice_idle_since = None
            return
        if agent._shell_notice_idle_since is None:
            agent._shell_notice_idle_since = now
        task = agent._shell_notice_task
        if task is not None and not task.done():
            return
        idle_since = agent._shell_notice_idle_since
        notified = agent._shell_notice_notified
        candidates = {
            shell.key for shell in state.shells
            if shell.key not in notified
            and earliest_delay_start(shell, idle_since) <= now - SHELL_NOTICE_DELAY_SECONDS
        }
        if not candidates:
            return
        lookups = agent.shell_notice_lookups(candidates, now)
        if lookups:
            facts = await sync_to_async(resolve_shell_owners)(agent.session_id, lookups)
            agent.store_shell_resolutions(facts, now)
            state = agent.shell_notice_state()
            now = time.time()
            if state is None or not state.idle:
                return
        if not select_concerned_shells(state, idle_since=idle_since, notified=notified, now=now):
            return
        agent._shell_notice_task = asyncio.create_task(
            self.send_shell_notice(agent, now),
            name=f"shell-notice-{agent.session_id}",
        )

    async def send_shell_notice(self, agent: BaseAgent, now: float) -> None:
        """Send the notice to ``agent`` if it still applies (spec §7).

        Never ``send_to_session``: no agent start, no settings, no steer.
        """
        async with self._send_gate(agent.session_id):
            async with self._lock:
                if self._agents.get(agent.session_id) is not agent:
                    return
                if time.time() - now > SHELL_NOTICE_STALE_SECONDS:
                    return
                state = agent.shell_notice_state()
                idle_since = agent._shell_notice_idle_since
                if state is None or not state.idle or idle_since is None:
                    return
                current = time.time()
                shells = select_concerned_shells(
                    state, idle_since=idle_since, notified=agent._shell_notice_notified, now=current,
                )
                if not shells:
                    return
                text = build_shell_notice(shells, now=current)
                keys = [shell.key for shell in shells]
                try:
                    result = await agent.send(text, shell_notice=True)
                except Exception:
                    logger.warning(
                        "Background shell notice failed for session %s (%s)",
                        agent.session_id, ", ".join(keys), exc_info=True,
                    )
                    return
                if result is False:
                    logger.warning(
                        "Background shell notice not delivered for session %s (%s)",
                        agent.session_id, ", ".join(keys),
                    )
                    return
                agent.mark_shells_noticed(keys)
                logger.info(
                    "Background shell notice sent to session %s (%s)",
                    agent.session_id, ", ".join(keys),
                )
```

In `src/twicc/providers/codex/agent/manager.py`, add to `CodexAgentManager`:

```python
    def _send_gate(self, session_id: str):
        """Same order as ``send_to_session``: the migration gate, then the lock."""
        return gate_for(session_id)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_shell_notice_manager.py tests/test_background_work.py -q`
Expected: PASS. Then `uv run pytest -q -p no:cacheprovider` — Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff check src/twicc/agent/base_manager.py src/twicc/providers/codex/agent/manager.py tests/test_shell_notice_manager.py
git add src/twicc/agent/base_manager.py src/twicc/providers/codex/agent/manager.py tests/test_shell_notice_manager.py
git commit -m "feat(agent): send the background shell notice from the timeout monitor"
```

---

### Task 6: Agent awareness and display check

Spec: §6.3, §8.

**Files:**
- Modify: `src/twicc/agent/system_prompt.py`
- Modify: `tests/test_system_prompt_spawned_by.py`
- Modify: `frontend/src/utils/markdownColonBlocks.test.js`

**Interfaces:** none new.

- [ ] **Step 1: Write the failing tests**

`tests/test_system_prompt_spawned_by.py` — append:

```python
@pytest.mark.django_db
def test_the_live_environment_explains_twicc_notices():
    settings = AgentSettings(**{field: None for field in AgentSettings._fields})
    block = build_dynamic_block(
        provider="claude_code", project_id="-home-twidi-project", resolved_settings=settings,
        session_id="child-id",
    )
    assert "`:: notice from TwiCC`" in block
```

`frontend/src/utils/markdownColonBlocks.test.js` — append:

```javascript
test('a TwiCC notice header renders as a notice line block', () => {
    const md = makeMd()
    const html = md.render(':: notice from TwiCC: background shell(s) still running\n\nbody')
    assert.match(html, /<div class="md-line md-line-notice">notice from TwiCC: background shell\(s\) still running<\/div>/)
})
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_system_prompt_spawned_by.py -q` — Expected: FAIL (sentence missing).
Run: `cd frontend && npm test` — Expected: PASS (the renderer already does this;
the test pins it).

- [ ] **Step 3: Add the sentence**

In `src/twicc/agent/system_prompt.py`, `_LIVE_ENVIRONMENT_INTRO`, append a
paragraph before the closing `"""`:

```text

A user message that starts with `:: notice from TwiCC` is written by TwiCC
itself, not by the user: TwiCC tells you about something it noticed, such as a
background shell still running after its work ended.
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_system_prompt_spawned_by.py -q` — Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff check src/twicc/agent/system_prompt.py tests/test_system_prompt_spawned_by.py
git add src/twicc/agent/system_prompt.py tests/test_system_prompt_spawned_by.py frontend/src/utils/markdownColonBlocks.test.js
git commit -m "feat(agent): tell agents that TwiCC notices come from TwiCC"
```

---

### Task 7: Manual end-to-end check

Spec: §2, §4, §6.1.

No code. Needs a running instance with the change (the user restarts the
backend; never restart it yourself unless the user asks).

- [ ] **Step 1: Own shell.** In a test session, ask the agent: "run `sleep 900`
  in the background with run_in_background, then end your turn". Wait 5 to 6
  minutes. Expected: a `:: notice from TwiCC` message appears with the shell id,
  the agent answers it, and no second notice comes while you send nothing.
- [ ] **Step 2: Subagent shell.** Ask the agent: "spawn a subagent that runs
  `sleep 900` with run_in_background and then finishes". Wait until the subagent
  ends, then 5 to 6 minutes. Expected: the notice names the subagent id and its
  description and asks to check with it.
- [ ] **Step 3: Re-notice.** Send any message; after the agent answers, wait 5 to
  6 minutes. Expected: exactly one new notice for the same shell.
- [ ] **Step 4: Codex.** Repeat step 1 in a Codex session (a background command
  through the unified exec). Expected: the same notice, with the process id.
- [ ] **Step 5: Log check.** `grep "Background shell notice" <data_dir>/logs/backend.log`
  shows one `sent` line per notice and no `failed` line.

---

## Self-review notes

- Spec coverage: §3.1 → Tasks 3, 4 (predicates) and 5 (`idle_since`); §3.2 →
  Tasks 3, 4; §3.3 → Task 1; §4 → Task 5; §5.1 → Task 1; §5.2 → Task 3; §5.3 →
  Task 3; §5.4 → Task 4; §5.5 → Tasks 2, 3, 4; §6.1-§6.2 → Task 1; §6.3 →
  Task 6; §7 → Task 5; §8 → Task 6 (display check; no CSS by design); §9 →
  nothing to build.
- Spec §10 → tests:
  - builder, selection, code spans → Task 1;
  - merge, `needs_lookup`, database part (two rows, no link, Monitor,
    first-level, nested, Codex run states incl. `stopped_at = None`) → Task 2;
  - episode rules: notice send keeps the set, external send clears it, CLI
    auto turn keeps it (Task 3); external send + short turn restarts the delay
    (Task 3 `test_external_send_restarts_the_delay` + Task 5
    `test_first_idle_tick_only_starts_the_delay`); a shell concerned later is
    notified once (Task 5);
  - Claude: owner resolution, `_main_turn_open` incl. `init` and DEAD, idle
    predicate incl. `STARTING`, the three removal sites, backgrounded filter,
    nested `UNRESOLVED`, `tool_use_id`-less events, `stop_subagent` run end,
    `mark_shells_noticed` live filter, hybrid default hooks → Task 3;
  - Codex: key owner, `_subagent_paths`, unknown thread, idle predicate,
    reconciliation in the hold with the snapshot (unit + manager), `send()`
    clears on steer and returns `True` (= the WS ack, see "Choices settled"),
    `run_hardcoded_command`, the plan "implement" turn, turn openings, every
    `_subagent_run_ended_at` write site → Task 4;
  - manager: cheap filter before the database, error isolation, one task per
    agent, staleness, gate before lock, replaced agent, re-check under the
    lock, failed send → Task 5.
- Type names used across tasks: `ShellInfo`, `ShellNoticeState`, `ShellOwner`,
  `ShellLookup`, `OwnerFacts`, `ShellResolution`, `ClaudeLiveOwners`,
  `merge_resolution`, `needs_lookup`, `resolve_shell_owners`,
  `select_concerned_shells`, `earliest_delay_start`, `build_shell_notice`,
  `code_span`, `_ShellTask`, `_TrackedShell`, `_forget_shell_task`,
  `_forget_shell`, `shell_notice_state`, `shell_notice_lookups`,
  `store_shell_resolutions`, `mark_shells_noticed`, `_note_main_turn_opening`,
  `_note_external_send`, `_drop_shell_notice_key`, `_shell_notice_live_keys`,
  `_send_gate`, `_shell_notice_step`, `send_shell_notice`.
