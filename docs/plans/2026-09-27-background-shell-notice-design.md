# Background shell notice — design

**Date:** 2026-09-27
**Status:** draft
**Scope:** Claude Code + Codex, backend only (the notice reuses the existing
`::` banner). No setting, no Stop button.

Paths are relative to `src/twicc/` unless they start with `frontend/`,
`docs/` or `tests/`.

## 1. Problem

A background shell can outlive the work that started it:

- A **subagent** starts a background shell, finishes its run, and sends its
  final answer to the parent. The subagent run model
  (`docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md`) marks
  it as ended. The shell still runs.
- The **main agent** ends its turn with a background shell still running.

TwiCC shows "background shell still running" (`BackgroundWorkStatus.vue`),
but it does not say which shell, or who owns it. The main agent does not
know either: it did not start a subagent's shell. The user asks "what is
still running?", and the agent must search its subagents, turn after turn.

Two real cases on 2026-09-27:

| Session | Owner | Shell | Why it never ended |
|---|---|---|---|
| `9492c933-b5db-4e4a-b40d-0d54e6a0cae6` | subagent `ad3c71271142417e1` | `bg15gnr0j`, `until grep -qE "passed\|failed\|error" <(tail -2 $F); do sleep 5; done` | the summary line was 3rd from the end, so `tail -2` never saw it |
| `2992a814-2ce5-4622-a145-219d5e2fd203` | subagent (Agent tool_use `toolu_01Gqb8vh38BxpJwYHCGm2twU`) | `bzevk3ug2`, `grep -n "…" $f` with `$f` empty | `grep` read stdin, a socket that never closes |

In both cases a foreground Bash call past its 120 s timeout, or a
`run_in_background: true` call, becomes a background shell with no time
limit.

## 2. Goal

TwiCC already knows the shells. TwiCC resolves the owner itself, from the
data it already has, with no agent turn. Then, **5 minutes** after the
situation starts, TwiCC sends **one** automatic message to the main agent.
The message names each shell and its owner, so the agent can act in one
turn.

Non-goals:

- A Stop button per shell (separate work).
- Any user setting. The feature is always on.

## 3. Definitions

### 3.1 Idle

**Idle** means the main agent's own turn is finished. This is the *real*
state, not the displayed one: TwiCC displays `ASSISTANT_TURN` while it holds
for running background subagents, but the main agent does nothing.

| Provider | Idle predicate |
|---|---|
| Claude Code | `state in (USER_TURN, ASSISTANT_TURN)` **and** `not _main_turn_open` (§5.2) **and** `not _live_monitor_tasks` **and** `not _has_pending_wakeup()` **and** `not pending_requests` |
| Codex | (`state == USER_TURN` **or** `in_subagent_hold()`, `providers/codex/agent/agent.py:1414`) **and** `not in_goal_continuation()` **and** `not _manual_compaction` **and** `not pending_requests` |

The Claude predicate covers three displayed states:

- `USER_TURN`.
- `ASSISTANT_TURN` held for background subagents (`hold_for_background`,
  `providers/claude_code/agent/agent.py:2220`).
- `ASSISTANT_TURN` set by a resumed subagent's traffic while the main agent
  does nothing (`_is_turn_activity`, `:2276-2283`).

Not idle, so no notice:

- A Claude hold for a live **Monitor** or a pending **ScheduledWakeup**: the
  agent waits on its own mechanism on purpose.
- A Codex `/goal` continuation or a manual compaction.
- `STARTING`, `DEAD`, or any agent with `pending_requests` (it waits on a
  user click).

`idle_since` is the start of the current idle period. The agent keeps
`_shell_notice_idle_since: float | None`:

- It is set to `None` by every **main turn opening** (§5.2, §5.4), whatever
  its source: an external send, a CLI auto turn, a Codex internal turn, the
  notice turn.
- It is set to `now` by the first tick (§4) that sees the idle predicate
  true while it is `None`.
- It is set to `None` by a tick that sees the predicate false.

So a turn shorter than one tick still restarts the delay. The 30 s tick
adds at most 30 s to the delay.

### 3.2 Notice episode

A **notice episode** is the time between two **external sends**. An
external send is any message that reaches the main agent from outside and
is not the notice itself:

- a UI message, `send-message` / `send-messages`, a cron restart or
  renewal, a settings-only update with text;
- a Codex hardcoded command (`/compact`, `/goal`, `/plan`), which reaches
  the agent through `run_hardcoded_command`, not `send()` (§5.4);
- the Codex plan prompt's "implement" answer (§5.4);
- the first prompt at agent start.

Each external send clears the episode's notified set. Nothing else clears
it:

- **Not** a CLI auto turn (Claude opens a main turn on its own after a
  task notification, a cron firing, a wake-up).
- **Not** a Codex internal turn (auto-review retry, `/goal` continuation,
  hold release).
- **Not** the notice's own turn.

Why: a CLI auto turn is not new work asked by anyone. If it cleared the
set, this loop would run without end: the notice asks the agent to check
with its subagent → the agent sends `SendMessage` and ends its turn → the
subagent answers and ends, keeping its legitimate shell → the CLI opens an
auto turn → idle again → a second notice 5 minutes later → and so on.

This rule narrows the product decision "re-notify when the agent works
again and becomes idle again with the shell still there": only work asked
by an external send counts. The user chose this rule on 2026-09-27, over a
time-based cap (at most one notice per shell per 30 min) and a count-based
cap (at most 2 or 3 notices per shell). The trade-off is in §9.

### 3.3 Concerned shell

A **concerned shell** is a live background shell in one of three cases:

| Case | Condition | Delay start |
|---|---|---|
| **Own shell** | owned by the main agent | `max(idle_since, shell start)` |
| **Orphan subagent shell** | owned by a subagent, of any level, whose run has ended | `max(idle_since, shell start, owner run end)` |
| **Unattributed shell** | owned by a subagent that TwiCC cannot place with a known run state (§5.5), **and** no subagent run is live | `max(idle_since, shell start, last subagent run end)` |

A shell owned by a subagent that still runs is **not** concerned: the
subagent is working, and it gets its own shell notification (the CLI resumes
it when its shell ends, §5.2). A shell whose owner is not resolved yet
(§5.5, the watcher has not synced it) is not concerned either, until the
next tick.

A missing "owner run end" or "last subagent run end" counts as `0`.

## 4. Trigger

The manager's timeout monitor already visits every live agent every 30 s
(`BaseAgentManager._run_timeout_monitor`, `agent/base_manager.py:1296`,
`TIMEOUT_MONITOR_INTERVAL = 30`). `check_and_stop_timed_out_agents`
(`agent/base_manager.py:1328-1347`) loops over `self._agents`. A new step
runs in that loop, for each agent, **after** `_check_agent_timeout`
returned `None` (no kill). This order keeps Codex's dead-shell
reconciliation first (§5.4). Claude's `_check_agent_timeout` returns early
for sessions with crons (`providers/claude_code/agent/manager.py:696-722`),
but it returns `None`, so the step still runs.

A new `BaseAgent` hook, `shell_notice_state() -> ShellNoticeState | None`,
returns `None` by default. `ShellNoticeState` is a `NamedTuple` defined in
`agent/shell_notice.py`:

```python
class ShellNoticeState(NamedTuple):
    idle: bool                        # the §3.1 predicate
    shells: list[ShellInfo]           # live shells with backgrounded == True only
    any_subagent_running: bool        # Claude: _live_background_tasks; Codex: _live_subagents
    last_subagent_run_end: float      # max of _subagent_run_ended_at, 0 when empty
```

`shells` holds **only backgrounded** shells. Claude's `_live_shell_tasks`
also holds foreground Bash calls (`backgrounded` false); they are left out.

Hybrid Claude agents and any provider without shell tracking keep the
default and are skipped. Ephemeral agents return `None` too (they report no
background work, `agent/base_agent.py:613`).

At each tick, for each agent (`now` is `time.time()` read for **this**
agent at the start of its step, then read again after the step 3 database
part; that second value is the one passed to `send_shell_notice`, so a slow
tick over many agents never makes a later agent's task fail the §7
staleness check):

1. Read `shell_notice_state()`. If `None`, stop. Update
   `_shell_notice_idle_since` (§3.1). If not idle, stop.
2. If a notice task is still running for this agent (step 6), stop.
   Keep the shells not in the notified set whose earliest possible delay
   start, `max(idle_since, started_at)`, is at least
   `SHELL_NOTICE_DELAY_SECONDS = 300` in the past. If none, stop. These
   cheap checks run before any database read.
3. If some of them have an `UNRESOLVED` merged owner (§5.1, §5.5), or a
   Codex owner placed from the database (its run state comes from the run
   model), call the §5.5 database part once, through `sync_to_async`, then
   store its facts on the loop and re-read `shell_notice_state()`.
4. Collect the concerned shells (§3.3) whose delay start is at least 300 s
   in the past.
5. If the list is empty, stop.
6. Start the send (§7) as a task, so the monitor loop never waits on a
   manager lock. At most one notice task runs per agent (checked at step
   2). On success, the task adds the shell keys to the notified set.

The whole step, for one agent, runs inside its own `try/except` that logs
the error at `warning` with the session id. An error for one agent never
stops the timeout checks of the other agents on the same tick.

Consequences:

- One message can group several shells, of several owners.
- A shell is notified at most once per notice episode. A shell the agent
  keeps on purpose (a dev server) gets one notice per external send, not
  one every 5 minutes.
- A shell that becomes concerned later in the same episode (a second
  subagent ends) gets its own notice later.
- The real delay is 5 to 6 minutes (30 s tick, plus up to 30 s for
  `idle_since`).
- An external send followed by a short turn does not re-notify at once: the
  turn opening reset `idle_since`, so the delay restarts.

## 5. Data per provider

### 5.1 Shell record

Both providers expose, per live background shell, one `NamedTuple`:

```python
class ShellInfo(NamedTuple):
    key: str                     # Claude task_id ("bg15gnr0j"); Codex "thread_id:process_id"
    shell_id: str                # the id the agent knows: Claude task_id, Codex process_id
    tool_use_id: str | None      # Claude: the Bash tool_use; Codex: None
    owner: ShellOwner            # MAIN, SUBAGENT, UNRESOLVED, UNATTRIBUTED
    owner_ref: str | None        # the id the main agent uses to reach the subagent
    owner_label: str | None      # subagent description / agent path
    owner_spawner_ref: str | None  # nested owner only: the subagent that spawned it
    owner_run_ended_at: float | None
    owner_running: bool
    description: str | None
    command: str | None
    output_path: str | None
    started_at: float            # epoch seconds
```

`ShellOwner` is a `StrEnum`:

| Value | Meaning | Set by |
|---|---|---|
| `MAIN` | the main agent's shell | provider, live |
| `SUBAGENT` | a subagent's shell, owner and run state known | provider live (first-level subagents), or §5.5 |
| `UNRESOLVED` | a subagent's shell, owner not known from live data | provider; §5.5 turns it into `SUBAGENT`, keeps it `UNRESOLVED`, makes it `UNATTRIBUTED`, or marks it "not a shell" |
| `UNATTRIBUTED` | a subagent's shell that §5.5 could not place | §5.5 |

The selection never reports an `UNRESOLVED` shell. A record that §5.5
found to be "not a shell" (a nested Monitor) is left out of
`ShellNoticeState.shells` by the merge.

The shared module `agent/shell_notice.py` (new) holds `ShellInfo`,
`ShellOwner`, the concerned-shell selection (a pure function over a
`ShellInfo` list, `idle_since`, the last subagent run end, the "any subagent
running" flag, the notified set and `now`), and the message builder. It
never reads provider event shapes.

### 5.2 Claude Code

**Facts from the SDK stream** (checked in
`~/.twicc/logs/sdk/claude_code/9492c933-b5db-4e4a-b40d-0d54e6a0cae6.jsonl`):

- A shell `task_started` (`task_type == "local_bash"`) carries `task_id`,
  `tool_use_id` (the Bash tool_use), `description`, `is_backgrounded`, and
  `owned_by_subagent: true` for a subagent's shell. The field is absent for
  a main-agent shell.
- A subagent `task_started` (`task_type == "local_agent"`) carries
  `task_id` (the agent id, e.g. `ad3c71271142417e1`) and `tool_use_id`.
  The agent id is also the subagent `Session.id` and the `SendMessage` `to`
  value. The `tool_use_id` is the `Agent` tool_use at spawn, and the
  `SendMessage` tool_use at a resume (`toolu_01UqYqEXq58BwAXbmvpd4MJd` for
  `ad3c71271142417e1` at 07:27:02Z).
- Every message of a subagent on the stream carries `parent_tool_use_id` =
  the **original** `Agent` tool_use id, even after a `SendMessage` resume.
- For the main agent and **first-level** subagents, the Bash `tool_use`
  block reaches the stream **before** the shell `task_started`.
- **Nested** subagents exist (a subagent's `Agent` call). In
  `~/.twicc/logs/sdk/claude_code/9de655b7-f86a-4d7c-af59-045697e346db.jsonl`
  more than 10 nested agents run under the first-level `Agent`
  `toolu_01AyCifzctWnzBnDS5sTj9hQ`. Their Bash `tool_use` blocks **never**
  reach the stream (26 shells, e.g. `b5lhayncg`), but their shell
  `task_started` does, with `owned_by_subagent: true`. Their `local_agent`
  `task_started` also reaches the stream, so they appear in
  `_live_background_tasks`. Some of them have **no** `tool_use_id` key:
  CLI auto-resumes after a task notification (see §5.5).
- When a subagent's own shell ends, the CLI resumes that subagent: a new
  `task_started` for the same agent id (07:24:07Z after `boe12cy2i` ended,
  and 08:13:15Z after `bg15gnr0j` was killed).
- Every CLI turn opens with a `SystemMessage` of subtype `init` and ends
  with a `ResultMessage`, including the auto turns (95 `init` / 95 `result`
  in that log). Every `stream_event` belongs to the main agent.

**Shell records.** Today `_live_shell_tasks: dict[str, bool]` keeps only
the backgrounded flag (`providers/claude_code/agent/agent.py:228-240`). It
becomes `dict[str, _ShellTask]`, a small mutable record holding
`backgrounded`, the shell's `tool_use_id` (to match the tool result for
`output_path`), and the `ShellInfo` fields. Every site that reads the bool
changes with it:

| Site | Today | After |
|---|---|---|
| `current_background_work` (`:451-457`) | counts true values | counts records with `backgrounded` |
| `_update_live_tasks`, `task_started` (`:1506-1514`) | stores the flag | builds the record (owner, description, command) |
| `_update_live_tasks`, `task_updated` (`:1526-1536`) | `.get(task_id) is False` | `record.backgrounded is False` |
| `_update_live_tasks`, terminal (`:1540`) | `if self._live_shell_tasks.pop(task_id, None):` | `_forget_shell_task(task_id)`, then test `record.backgrounded` |
| `_reconcile_background_shells` (`:1580-1585`) | iterates `(task_id, backgrounded)`, then `del` | iterates records, tests `backgrounded`, then `_forget_shell_task(task_id)` |
| Monitor start (`:1632`) | pops the task | `_forget_shell_task(task_id)` |

A new helper `_forget_shell_task(task_id) -> _ShellTask | None`, like
Codex `_forget_shell`, is the **only** way a record leaves
`_live_shell_tasks`. It also drops the shell key from the notified set and
from `_shell_notice_resolutions`. The three sites above call it.

**New in-memory maps** in `ClaudeCodeAgent`:

| Map | Filled from | Used for |
|---|---|---|
| `_agent_by_tool_use: dict[str, str]` (tool_use id → agent id) | each `local_agent` `task_started` (`tool_use_id` → `task_id`); an event without a `tool_use_id` is skipped | owner lookup |
| `_agent_labels: dict[str, str]` (agent id → description) | **every** `local_agent` `task_started`, with or without a `tool_use_id` (`task_id` → `description`) | `owner_label`; also the "seen live" test of §5.5, so it is never pruned |
| `_tool_use_parents: dict[str, tuple[str \| None, str \| None]]` (tool_use id → (`parent_tool_use_id`, Bash command or `None`)) | every `tool_use` block of every `AssistantMessage` | owner and command at shell start |
| `_subagent_run_ended_at: dict[str, float]` (agent id → epoch) | each pop of `_live_background_tasks` (`_update_live_tasks`, `:1547`; `stop_subagent`, `:1288`) | orphan delay start; the max is the "last subagent run end" |

`_tool_use_parents` is bounded: it keeps the last 1024 entries. An entry is
removed once a shell starts for it.

The other new maps (`_agent_by_tool_use`, `_agent_labels`,
`_subagent_run_ended_at`, and Codex `_subagent_paths`) grow by one entry
per subagent spawn or resume, for the agent's lifetime, and are not
pruned. That is small: the largest observed log has 176 `local_agent`
`task_started` events. The per-shell state (`_shell_notice_resolutions`,
which holds the first attempt time, and the notified set) is dropped with
the shell record: `_forget_shell_task` on Claude, `_forget_shell` on
Codex.

**Owner resolution** at the shell `task_started`:

1. `(parent, command) = _tool_use_parents.get(tool_use_id, (None, None))`.
2. If `parent` is set and `_agent_by_tool_use.get(parent)` finds an agent
   id: owner = `SUBAGENT`, `owner_ref` = that agent id.
3. Else, if `owned_by_subagent` is true: owner = `UNRESOLVED`; §5.5
   resolves it from the database. This happens for every shell of a
   **nested** subagent (its Bash `tool_use` never reaches the stream), and
   rarely when the entry was evicted or the agent never saw the spawn.
4. Else: owner = `MAIN`.

A `local_bash` task that is a main or first-level subagent's **Monitor**
gets a record for a moment, then `_update_live_monitor_tasks` pops it
(`:1632`). It never reaches the 5-minute delay. A nested agent's Monitor is
not detected live (its tool result does not reach the stream), so its
record stays `UNRESOLVED`; §5.5 recognizes it from the database
(`tool_name = 'Monitor'`) and never reports it.

`output_path` comes from the Bash tool result text (`Output is being
written to: <path>.`), matched by `tool_use_id`. It is optional.

**Subagent run state.** A subagent's run is live when its agent id is in
`_live_background_tasks`. This is the live set that drives the hold. It
flips back to live when the CLI resumes the subagent. For a `SUBAGENT`
owner resolved live, `owner_running` and `owner_run_ended_at` come from
this set and `_subagent_run_ended_at`. Nested agents are in this set too,
so `any_subagent_running` covers every level.

**`_main_turn_open: bool`** (new). Each change to `True` is a **main turn
opening** (§3.1) and sets `_shell_notice_idle_since = None`:

- `True` in `start()` and `send()`, any origin.
- `True` on the `SystemMessage` of subtype `init` (the CLI opens every
  turn with it, auto turns included). This closes the window between the
  CLI opening an auto turn and its first main message (0.2 to 6.7 s in the
  log).
- `True` on an `AssistantMessage` or `StreamEvent` whose
  `parent_tool_use_id` is `None`.
- `False` on `ResultMessage`.
- `False` when the agent goes `DEAD`.

**External send.** `send()` gets a keyword argument
`shell_notice: bool = False`. When it is `False`, and in `start()`, the
agent clears the notified set.

### 5.3 Episode state (both providers)

Each agent keeps:

- `_shell_notice_idle_since: float | None` (§3.1).
- `_shell_notice_notified: set[str]` — shell keys notified since the last
  external send.

There is no other episode state. The set is cleared only by an external
send (§3.2). The notice send does not clear it. Keys of shells that ended
are removed from the set when the shell record goes away, so the set stays
small.

### 5.4 Codex

**Shell records.** `_live_shells: dict[(thread_id, process_id), float]`
(`providers/codex/agent/agent.py:550-574`) becomes
`dict[(thread_id, process_id), ShellInfo]`. `thread_id` is already the
owner: `self.session_id` for an own shell, or the owning subagent's session
id (= thread id) for a relayed one (`notify_shells_started`, `:1168`).
Every site that reads the float changes with it:

| Site | Today | After |
|---|---|---|
| `_track_shell` (`:1158-1161`) | stores `started_at` | builds the `ShellInfo` |
| `_note_command_execution` (`:1125-1152`) | tracks `(session_id, process_id)` | also reads `command` from the `commandExecution` item (`CommandExecutionThreadItem.command`, `src/openai_codex/generated/v2_all.py:9649`) |
| `notify_shells_started` (`:1168-1192`) | tracks with the announce time | same; `command` and `description` stay `None` |
| `drop_gone_shells` (`:1233-1253`) | compares the float | compares `ShellInfo.started_at` |
| `current_background_work` (`:1255-1260`) | `len(self._live_shells)` | unchanged |

**Owner placement.** Every Codex shell carries its owner's id: the
`thread_id` of its key (= the owner's `Session.id`).

| Owner | How to recognize it | `ShellInfo.owner` |
|---|---|---|
| Main agent | `thread_id == self.session_id` | `MAIN` |
| First-level subagent seen by this agent | `thread_id` in `_subagent_paths` (below) | `SUBAGENT`, `owner_ref` = the agent path; running when in `_live_subagents`, run end from `_subagent_run_ended_at` |
| Any other subagent: nested, or first-level but spawned before this agent instance started | any other `thread_id` | `UNRESOLVED`; §5.5 places it from the database, with the thread id as the agent id |

A thread id is a valid agent reference for `send_message` /
`followup_task` (subagent runs design §4.2).

- `_live_subagents: dict[str, str]` (`:539`) maps a first-level subagent's
  thread id to its agent path, and pops the entry at the run end. A new map
  `_subagent_paths: dict[str, str]` keeps every path ever seen. It is
  filled at the two sites that add to `_live_subagents`:
  `_note_sub_agent_activity` (`:1119`) and `notify_subagents_resumed`
  (`:1559`).
- A new `_subagent_run_ended_at: dict[str, float]` is written at every site
  that pops `_live_subagents`: `_note_sub_agent_activity` (`:1122`),
  `_prune_finished_subagents` (`:1355`, `:1366`; also reached from the
  `wait_agent` label refresh) and `_apply_subagents_stopped` (`:1481`).

Nested owners are real. Codex anchors every descendant session to the root
(`providers/sessions_watcher.py:658-661`, `resolve_flat_parent_id`), so the
watcher relays a nested subagent's shells to the root agent, keyed by the
nested subagent's thread id (`providers/codex/sessions_watcher.py:295-299`).
`_live_subagents` holds only first-level subagents, but the run model used
by §5.5 covers the whole tree.

**Dead-shell reconciliation.** Codex's `_check_agent_timeout`
(`providers/codex/agent/manager.py:921-950`) reconciles tracked shells
against the process table, but only in `USER_TURN`. The condition becomes
"`USER_TURN` or `in_subagent_hold()`", so a shell killed without an end
event is dropped before the notice step reads it (limit in §9).

In the hold, subagents are active, so a shell can be tracked while the
probe runs in its thread. A relayed shell is tracked with
`started_at = announced_at - _RELAYED_SHELL_START_MARGIN_SECONDS`, which can
fall before the probe's cutoff, so `drop_gone_shells` could drop a shell
tracked after the probe started. The reconciliation therefore takes a
snapshot of the tracked keys before the probe, and `drop_gone_shells`
drops only keys from that snapshot.

**Main turn openings** (they set `_shell_notice_idle_since = None`,
§3.1): `send()` (any origin), the entry of `_run_turn`, the entry of the
`/goal` continuation turn, and the entry of `compact()`.

**External send.**

- `CodexAgent.send()` already accepts `**kwargs` (`:734-740`). It reads
  `shell_notice`. When it is `False`, the agent clears the notified set at
  the top of `send()`, before any branch: this covers a new turn, a steer
  and a hold break.
- The hardcoded commands do not go through `send()`: the manager routes
  them to `run_hardcoded_command` (`providers/codex/agent/manager.py:280-287`,
  `providers/codex/agent/agent.py:1078`). That method clears the set too,
  for every command (`/compact`, `/goal`, `/plan`).
- The plan prompt's "implement" answer is a human decision: the
  implement turn it starts (`_prompt_plan_implementation`,
  `providers/codex/agent/agent.py:2024`, `_run_turn(_PLAN_IMPLEMENTATION_MESSAGE)`
  at `:2086`) clears the set before `_run_turn`.
- `start()` clears it.
- Internal turns (auto-review retry, `/goal` continuation turns) do not
  clear it.

**`send()` return value.** In `USER_TURN`, `CodexAgent.send()` ends with
`self._schedule_turn(text, images)` and returns `None`
(`providers/codex/agent/agent.py:831-835`), although its docstring says
`True`. That branch gets `return True`, so the notice send can tell success
from failure (§7).

This also changes existing callers, as a fix. `CodexAgentManager` returns
`agent.send()` as is (`providers/codex/agent/manager.py:316`). With `None`,
a Codex send in `USER_TURN` today gets no WS delivery ack (`asgi.py:1242`
sends it only when `delivered` is true). With `True`, the ack is sent, as
for Claude. (`asgi.py:967` also reads `delivered` for ephemeral
admissions, but an ephemeral agent dies at its first `USER_TURN`, so that
path is practically never reached.)

### 5.5 Owner resolution from the database (both providers)

An `UNRESOLVED` shell is placed from TwiCC's own database, with no agent
turn. Both providers anchor every subagent session, at any level, to the
root session (`Session.parent_session_id` = root): Claude through its
`subagents/agent-<id>.jsonl` files (`providers/claude_code/sessions_watcher.py:128-144`),
Codex through `resolve_flat_parent_id` (`providers/sessions_watcher.py:658-661`).

The work splits in two parts:

- **Database part.** One function in `agent/shell_notice.py`, run through
  `sync_to_async` at tick step 3 (§4). It takes the root `Session` id and
  the shells to look up, reads the database only, and returns database
  facts only: owner agent id, tool name, spawner, `Session.title`, and the
  Codex run states. It never reads an agent attribute.
- **Loop part.** Back on the event loop, the tick stores those facts in
  `_shell_notice_resolutions` (Storage, below). `shell_notice_state()`
  then merges each stored placement into its `ShellInfo`, with the run
  state (Claude: live; Codex: the stored `ShellResolution` run state) and
  the label order (steps 3 and 4). The tick and
  `send_shell_notice` always read the **merged** owner.

1. **Owner agent id and tool name.**
   - Codex: the shell's `thread_id`.
   - Claude: the `ToolResultLink` rows whose `tool_use_id` is the shell's
     `tool_use_id` and whose `session` has `parent_session_id` = root
     (index `idx_tool_result_link_by_tool`, `core/models.py:856-870`).
     Several rows can exist for one tool_use (e.g. two for
     `toolu_01Tojv846z1a6TkUh8k7H8jT`, the second with `error='failed'`),
     so the function reads the **distinct** `session_id` values. Exactly one
     value is the owner's agent id; zero values mean "not found yet"; more
     than one is treated as not found. A `run_in_background` Bash call gets
     its tool result at once (`bg15gnr0j`: shell start 07:23:36.422, link
     `tool_result_at` 07:23:36.430). A foreground call moved to the
     background gets it when the CLI moves it, at its 120 s timeout, still
     well before the 300 s filter.
   - Checked on 2026-09-27: shell `b5lhayncg` of
     `9de655b7-f86a-4d7c-af59-045697e346db` (tool_use
     `toolu_01Tojv846z1a6TkUh8k7H8jT`) resolves to the nested agent
     `a79858f65df89eb0a`.
   - Claude: the same rows give `tool_name`. A nested agent's **Monitor**
     looks exactly like a nested shell on the stream (`local_bash`,
     `owned_by_subagent: true`, `is_backgrounded: true`), but its link
     says `tool_name = 'Monitor'` (e.g. `bst40x3gu`, tool_use
     `toolu_01UWyvqfGDmFQEMYVPikVcMV`, session `a3cd54e6d5f4bca36`; the
     real shell `b5lhayncg` says `'Bash'`). Any `tool_name` other than
     `'Bash'` gives the outcome **not a shell** (below).
2. **Spawner.** The `AgentLink` rows whose `agent_id` is the owner
   (`core/models.py:876-914`). Their `session_id` is the session that
   spawned it. When it is the root, the owner is first-level; otherwise
   `owner_spawner_ref` = that session id (for `a79858f65df89eb0a`: the
   first-level agent `a663305d8fe755548`). No `AgentLink` row leaves
   `owner_spawner_ref` empty.
3. **Label** (loop part). Claude: `_agent_labels` (the live `description`
   of the owner's `local_agent` `task_started`, every level) first; the
   owner `Session.title` only as a fallback (a Claude subagent title is the
   head of its prompt). Codex: the owner `Session.title`.
4. **Run state** (Claude: loop part; Codex: database part).
   - **Claude: live data, never the database.** The owner is running when
     its agent id is in `_live_background_tasks`, and its run end comes
     from `_subagent_run_ended_at`. The live set covers every level and
     every CLI auto-resume. The run model does not: a nested agent resumed
     by a task notification opens no run (subagent runs design §3.3,
     "Wake-ups caused by no tool"). In `9de655b7-f86a-4d7c-af59-045697e346db`,
     27 nested `local_agent` `task_started` events are such resumes (no
     `tool_use_id` key, `spawn_depth: 2`, prompt starting with
     `<task-notification>`). An owner agent id **never seen** live (not in
     `_agent_labels`) has no live run state: the shell becomes
     `UNATTRIBUTED`.
   - **Codex: the run model.** One call to `agent_run_states(root,
     owner_ids)` (`core/agent_runs.py:215`) for all owners. `running` fills
     `owner_running`; `stopped_at` (a `datetime`) is converted to epoch
     seconds for `owner_run_ended_at` (`None` counts as `0`, §3.3). Codex
     has no CLI auto-resume of a subagent; its resumes are
     `send_message` / `followup_task` calls, which the model counts.

Outcome per shell:

| Case | Result |
|---|---|
| Claude link with a `tool_name` other than `'Bash'` (a nested Monitor) | **not a shell**: never reported, never looked up again |
| owner found, run state known (Claude: owner seen live; Codex: `known=True`) | `SUBAGENT`, with the run state |
| owner found, Claude owner never seen live | `UNATTRIBUTED` |
| not placed yet (no owner id, or Codex `known=False`), less than 60 s after the first attempt for this shell | stays `UNRESOLVED`; retried at the next tick |
| not placed yet, 60 s or more after the first attempt | `UNATTRIBUTED` |

`UNATTRIBUTED` shells are held back while any subagent runs (§3.3), so a
wrong guess delays a notice, it never sends one early while subagents work.

**Storage.** The agent keeps `_shell_notice_resolutions: dict[str,
ShellResolution]` (shell key → owner id, spawner, `Session.title`, final
owner kind, first attempt time, and for a Codex owner its run state —
`running` and run end — refreshed at each tick's `agent_run_states`
call). The tick writes it on the loop after the
database part returns, and **only for keys whose shell record still
exists**: a shell can end during the `await`. The agent drops an entry
with its shell record. A shell once placed as `SUBAGENT`, `UNATTRIBUTED`
or "not a shell" is not looked up again. Its run state is re-read at each
tick: live for Claude, through the same `agent_run_states` call for Codex.

The database follows the watcher, a few seconds behind the stream. That is
negligible against 5 minutes. The call runs only for shells that passed the
cheap filter of tick step 2, so an idle agent with a fresh shell causes no
database read.

## 6. Message

### 6.1 Format

The notice uses the existing `::` header line, the one TwiCC already uses
for messages the human did not type (`cli/_drop_request/sender_header.py`).
The header is one line; the body is plain markdown. English, because the
reader is the agent.

```text
:: notice from TwiCC: background shell(s) still running

Your subagent `ad3c71271142417e1` ("Implement Task 5c: Codex live + parity") has finished its work, but it still has a background shell running:

- shell `bg15gnr0j`: "Wait for full suite to finish", running for 7 min
  - command: `F=/tmp/…/boe12cy2i.output; until grep -qE "passed|failed|error" <(tail -2 $F); do sleep 5; done; tail -12 $F`
  - output: `/tmp/…/tasks/bg15gnr0j.output`

Ask your subagent what this shell is for, and tell it to stop the shell if it is not needed.

You also still have a background shell of your own:

- shell `b9t13uhf9`: "Run full backend suite", running for 6 min

If it is a long-running process you started on purpose, nothing to do. Otherwise, check what it is doing, make sure it will end, and keep the user informed so they do not wait for nothing.
```

Rules:

- One block per `SUBAGENT` owner, then one block for `UNATTRIBUTED`
  shells, then one block for `MAIN` shells. A block with no shell is
  omitted.
- The subagent reference is `owner_ref`: the Claude agent id (for
  `SendMessage`), or the Codex agent path, else the thread id (for
  `send_message` / `followup_task`).
- A nested owner (`owner_spawner_ref` set) block names both ids: "Subagent
  `<owner_ref>`, started by subagent `<owner_spawner_ref>`, has finished
  its work, but …". It asks the agent to check with whichever of the two it
  can reach.
- The `UNATTRIBUTED` block reads "One of your subagents, which TwiCC cannot
  identify, still has a background shell running:", lists the shells, and
  asks the agent to find which subagent started it and have it stopped if
  not needed.
- Each shell line: id, description when known, age in whole minutes.
  `command` and `output` sub-lines only when known.
- **Escaping.** A description or an owner label goes through
  `inline_md()` (`cli/_drop_request/sender_header.py:48-64`), which
  flattens newlines, truncates and escapes markdown specials. A command or
  a path does **not**: backslash escapes show literally inside a code span
  and would change the command the agent reads. A command or a path has
  its whitespace runs flattened, is cut at 300 characters, and is wrapped
  in a code span whose backtick fence is one longer than the longest
  backtick run it contains (with a space padding when it starts or ends
  with a backtick).

### 6.2 Header detection

- A new constant `SHELL_NOTICE_HEADER = ":: notice from TwiCC"` lives in
  `cli/_drop_request/sender_header.py`, next to `SENDER_HEADER_PREFIX`.
  The builder in `agent/shell_notice.py` imports it with `inline_md`. The
  constant stays in `sender_header.py` so that module never imports
  `agent/shell_notice.py` (no import cycle).
- `has_sender_header()` (`cli/_drop_request/sender_header.py:108-119`)
  also matches this prefix. The composer history picker then skips the
  notice (`views.py:940-978`), like other messages not typed by the human.
  Its docstring is updated to say it covers TwiCC notices too.

### 6.3 Agent awareness

The system prompt addendum (`agent/system_prompt.py`) gets one short
sentence: a user message that starts with `:: notice from TwiCC` comes from
TwiCC, not from the user. It follows the existing `<twicc:context>`
explanation (`:218-225`). The addendum explains why and when, not what a
skill does.

## 7. Sending

The notice does **not** use `send_to_session`. That path is wrong here:

- If the agent died in the meantime, it starts a new agent with the notice
  as its first prompt (`providers/claude_code/agent/manager.py:170-173,
  284-289`; `providers/codex/agent/manager.py:281-286, 355-359`).
- If a real turn started in the meantime, it queues the notice into it
  (Claude) or steers it into it (Codex, `:319-343`).
- It applies the settings from the `Session` row, which can kill and
  restart a Claude agent with pending startup settings (`:201-230`).
- On Claude, its default `cancel_cron_restart=True` cancels a pending cron
  restart.

A new manager method, `BaseAgentManager.send_shell_notice(agent, now)`,
does the send:

1. Take the per-session send gate, then the manager lock, in the same
   order as `send_to_session`. On Codex the gate is `gate_for(session_id)`
   (`providers/codex/migration_gate.py:67`), taken **before** `self._lock`
   (`providers/codex/agent/manager.py:215` then `:270`); the reverse order
   can deadlock against a user send. `BaseAgentManager` gets an overridable
   hook `_send_gate(session_id)`, an async context manager that does
   nothing by default; the Codex manager returns `gate_for(session_id)`.
2. Check that `self._agents.get(agent.session_id) is agent`. If not, stop.
3. Re-read `shell_notice_state()` and re-run the §4 selection (idle,
   concerned, not notified). Owner placements and Codex run-model states
   from the §5.5 call of the same tick are reused; no database call under
   the lock. If more than 30 s passed since the tick's `now` (passed as the
   `now` argument; the task waited on the gate), stop: the next tick starts
   over with fresh data. If the list is now empty, stop.
4. Build the message and call `agent.send(text, shell_notice=True)`
   directly. No settings, no agent start, no steer: step 3 found the agent
   idle under the lock, and on Claude `_main_turn_open` already flips at the
   `init` message of a CLI auto turn (§5.2). A CLI auto turn that opens
   in the same instant can still take the notice as a queued message; that
   is harmless.
5. Success is "no exception, and the result is not `False`". Claude
   returns `False` on failure (`providers/claude_code/agent/agent.py:1859-1864`);
   Codex returns `True` once the `USER_TURN` branch is fixed (§5.4). On
   success, add to the notified set the keys whose shell record still
   exists (a shell that ended during the send is not added), and log at
   `info` with the session id and the shell keys. On `False` or any exception, add nothing
   and log at `warning`: the next tick retries while the conditions hold.

Holding `self._lock` while calling `agent.send()` is safe: both
`send_to_session` implementations already do it.

Behavior of `send()` in the idle states:

- Claude: `send()` accepts `USER_TURN` and `ASSISTANT_TURN`
  (`providers/claude_code/agent/agent.py:1811-1864`). In a
  `hold_for_background` hold no turn runs, so the CLI opens one right away.
- Codex: in the subagent hold, `send()` breaks the hold and schedules a
  turn (`providers/codex/agent/agent.py:780-792`). In `USER_TURN`, it
  schedules a turn.
- A pending `<twicc:context>` delta is prepended, as for any message
  (`context_injection.py:235-258`). This is expected.

## 8. Display

- The notice is a normal `USER_MESSAGE` (`ALWAYS`). No compute change, no
  compute version bump.
- The markdown renderer turns the header into
  `<div class="md-line md-line-notice">`
  (`frontend/src/utils/markdownColonBlocks.js:70,125-127,151-154`; the first
  word after `::` becomes the class).
- **No CSS change.** Every `::` line already renders as a tinted banner
  with a left accent bar (`--md-tint-fill`, `border-inline-start`,
  `frontend/src/components/ui/MarkdownContent.vue:939-960`). That banner is
  how the user already recognizes a message not typed by them (a
  `send-message` header, a peer message). The per-type classes are left
  unstyled on purpose (comment at `:935-938`: "one look per shape, not per
  type"). The notice follows that rule.
- Side effects, the same as `send-message` messages today: the notice
  counts in `user_message_count`, is indexed by search, and is a stop for
  chat navigation. The agent's reply follows the normal unread and
  end-of-turn notification rules.

## 9. Limits

- **Re-notice only after an external send.** A shell is notified at most
  once between two external sends (§3.2). CLI auto turns do not count as
  new work. Example: in `9492c933-b5db-4e4a-b40d-0d54e6a0cae6`, the agent
  worked for hours through 87 CLI auto turns and 8 external sends. A shell
  left running through that would get at most one notice per external
  send. This is the price of avoiding the §3.2 loop.
- **Unattributed shells wait for all subagents.** An `UNATTRIBUTED` shell
  (the three causes are in the §5.5 outcome table: no owner found after
  60 s, a Claude owner never seen live, a Codex owner still `known=False`
  after 60 s) is reported only once no subagent runs. If its real owner ended earlier, it is reported late.
  This should be rare: nested subagents, the common case without live
  data, are placed from the database.
- **Replacement shells.** The episode rule (§3.2) blocks a repeat for the
  **same** shell key only. If each notice turn leads a subagent to start a
  new shell that never ends either, each one is a new key, and gets its own
  notice 5 to 6 minutes later. This is a known limit of the episode rule.
- **Codex "no subagent runs" sees first-level subagents only.** For an
  `UNATTRIBUTED` Codex shell (only after `known=False` for 60 s),
  `any_subagent_running` and the last subagent run end come from
  `_live_subagents` and `_subagent_run_ended_at`, which hold first-level
  subagents only. A nested Codex subagent that outlives its spawner is not
  seen by this check. This case is rare.
- **Nested Claude Monitors** already count as background shells in the
  displayed count today; this feature does not change that count.
- **Claude workflow agents.** Workflow subagent sessions
  (`wf_…:<agent>`) have `parent_session_id` = root but no `AgentLink` row.
  No SDK log with a workflow run exists, so whether their shells reach the
  stream is not verified. If they do, their owner is found in the database
  but never seen live, so the shell is `UNATTRIBUTED` (§5.5): held back
  while any subagent runs, but possibly reported while the workflow still
  runs.
- **Reaching a nested subagent.** The notice names a nested owner and its
  spawner. Whether the main agent can message a nested subagent directly is
  provider behavior outside TwiCC; the notice gives both ids so the agent
  can go through the spawner.
- **Codex dead shells.** The process-table reconciliation drops tracked
  shells only when **no** command process runs at all (`drop_gone_shells`).
  While another process runs (a dev server), a subagent shell killed
  without an end event stays tracked, and it can be notified. The agent's
  check then finds it gone.
- **Waiters on the session.** A notice turn writes a new final message.
  `session wait-reply --wait-background` ignores a final message while a
  shell runs, and returns the first final message after the shell ends.
  That can be the reply to the notice ("I stopped the shell"), not the
  earlier answer. This is accepted: that reply is the last word of the
  session.
- **Hybrid Claude sessions** report no shells (`HybridClaudeAgent` does not
  override `current_background_work`), so they never get a notice.
- **Ephemeral sessions** report no background work
  (`agent/base_agent.py:613`) and die at their first `USER_TURN`. No
  notice.
- **Backend restart.** All state is in memory. Agents do not survive a
  backend restart, and their shells die with them.
- **Race with the human.** A UI send that arrives right after the notice
  is queued behind it. Both reach the agent; the notice is harmless.

## 10. Tests

Backend (`uv run pytest`):

- `agent/shell_notice.py`:
  - message builder: own only, subagent only, unattributed only, nested
    Codex owner, all together, several subagents, missing command / output
    / description; `inline_md` on labels; code-span fencing for a command
    with backticks; command cut at 300 characters;
  - concerned-shell selection as a pure function: delay start per case,
    the 300 s threshold, the notified set, a shell of a running owner left
    out, an `UNATTRIBUTED` shell held back while a subagent runs.
- Episode rules: the notice send does not clear the set; an external send
  clears it; a CLI auto turn does not clear it; a shell concerned later in
  the same episode is notified once; an external send followed by a short
  turn (both ticks see the agent idle) gives no notice before 300 s.
- Claude, from recorded SDK events: owner resolution (main, subagent at
  spawn, subagent after a `SendMessage` resume, unattributed);
  `_main_turn_open` transitions, including the `init` message;
  `idle_since` reset at each turn opening; the idle predicate with each
  hold reason; the `_live_shell_tasks` call sites (count, backgrounding,
  terminal pop, reconcile); only backgrounded shells in
  `shell_notice_state()`; a nested-agent shell (`owned_by_subagent`, no
  Bash `tool_use` seen) gives `UNRESOLVED`; a `local_agent` event with
  `tool_use_id: null` is skipped; `stop_subagent` writes
  `_subagent_run_ended_at`.
- Codex: owner from the `_live_shells` key; `_subagent_paths` kept after
  the run end; an unknown thread id gives `UNRESOLVED`; idle in the
  subagent hold; not idle in a `/goal` continuation or a manual compaction
  even with the hold flag set; the reconciliation also runs in the hold;
  `send()` clears the set on a steer; `run_hardcoded_command` clears it
  (`/plan <prompt>`); the plan "implement" answer clears it; `send()`
  returns `True` in `USER_TURN`; a notice sent in `USER_TURN` adds its
  keys.
- §5.5 database resolution (fixtures with a root, a first-level and a
  nested subagent, `ToolResultLink` and `AgentLink` rows): Claude owner
  from the `ToolResultLink`, with two rows for one tool_use; Codex owner
  from the thread id; spawner from `AgentLink`; no `AgentLink` row; label
  from `_agent_labels` before `Session.title`; Claude run state from the
  live set (a nested owner resumed by a task notification is running and
  its shell is not concerned; an owner never seen live gives
  `UNATTRIBUTED`); Codex run state from `agent_run_states` (running, ended
  with `stopped_at` converted to epoch, ended with `stopped_at = None`,
  `known=False` retried then `UNATTRIBUTED` after 60 s); no link yet →
  retried, then `UNATTRIBUTED` after 60 s; `_shell_notice_resolutions`
  dropped with the shell record; a nested `local_bash` whose link has
  `tool_name = 'Monitor'` is "not a shell" and never concerned; a shell
  that ends during the database `await` gets no resolution entry; the
  database part reads no agent attribute (it runs in a worker thread).
- Codex reconciliation: a shell tracked while the probe runs is not
  dropped (snapshot).
- Codex `send()` in `USER_TURN`: returns `True`, and the WS delivery ack
  is sent.
- Claude: each of the three record-removal sites goes through
  `_forget_shell_task` and drops the key from the notified set and the
  resolutions; `_main_turn_open` goes `False` when the agent goes `DEAD`;
  `_agent_labels` is filled by a `local_agent` event without a
  `tool_use_id`.
- Manager tick: the running-task guard and the cheap filter run before any
  database read; one agent's error is logged and the other agents are
  still checked; one notice task at most per agent; a task that waited
  more than 30 s after the §5.5 call does not send.
- Codex: `idle_since` reset at `_run_turn`, the `/goal` continuation and
  `compact()`; `_subagent_run_ended_at` written at each pop site.
- `send_shell_notice`: a shell that ends during the send is not added to
  the notified set.
- `send_shell_notice`: sends once; takes the gate before the lock; does
  nothing when the agent was replaced or is no longer idle; does not add
  keys after a failed send and retries at the next tick; skips agents with
  `pending_requests`, `STARTING`, a Monitor or a wake-up hold, and hybrid /
  ephemeral agents.
- Notified set: a key is removed when its shell record goes away.
- Existing tests that write the old value types into the maps change with
  them: `tests/test_background_work.py` (`:116`, `:301`, `:318` (the
  `drop_gone_shells` signature), `:395`, `:404`,
  `:491`, `:509`, `:632`, `:714`), `tests/test_claude_monitor_liveness.py:39`,
  `tests/test_codex_agent_runs_live_process.py:133`. The agent stubs built
  with `__new__` get the new attributes: `_claude_agent` / `_codex_agent`
  in `test_background_work.py`, `_agent` in
  `test_claude_monitor_liveness.py`, `make_agent` in
  `test_codex_agent_runs_live_process.py`.
- `has_sender_header` matches the new prefix (`tests/test_sender_header.py`).

Frontend (`cd frontend && npm test`): a `markdownColonBlocks.test.js` case
for `:: notice from TwiCC: …` producing `md-line-notice`.
