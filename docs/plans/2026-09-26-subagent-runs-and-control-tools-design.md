# Subagent runs and agent-control tools — design

Date: 2026-09-26. Status: design, not implemented.

## 1. Problem

TwiCC tracks a subagent's running state from **one** tool call: the call that
spawned it (Claude `Agent`/`Task`, Codex `spawn_agent`). The spawn call counts
its tool results; once the count is reached, the agent is stopped for good.

Both providers can **wake a finished agent** and **stop a running one** with
other tools. TwiCC ignores those tools:

- A resumed agent shows as stopped everywhere (spawn card, subagent tab,
  subagent header, Orchestration agent tree and its "x working" count).
- An explicit stop is only seen when some other signal arrives.
- The cards of those tools are generic: no agent name, no "View Agent" button,
  no running indicator, no Stop button.

## 2. Goals

1. **Runs.** An agent can run several times. Each spawn **or wake-up** opens a
   run; the agent is running while at least one run is open. Explicit stops
   close the runs.
2. **Widget.** Every tool call that targets one agent shows the same widget as
   the spawn card: the agent's name, the "View Agent" button with its running
   indicator, and (Claude only) the Stop button.
3. **One state everywhere.** Spawn cards, control cards, subagent tabs, the
   subagent header and the Orchestration agent tree read the same per-agent
   state, **computed by the backend**.

The design extends the existing model (result counting per call). It does not
replace it.

## 3. Scope

### 3.1 In scope

| Provider | Tool | Role |
|---|---|---|
| Claude Code | `SendMessage` | message an agent; wakes it when it is idle |
| Claude Code | `TaskStop` (when `task_id` is an agent) | stop an agent |
| Claude Code | `TaskOutput` (when `task_id` is an agent) | read an agent's output — widget only |
| Codex (multi-agent v2, namespace `collaboration`) | `followup_task` | give an agent a new task; wakes it when it is idle |
| Codex v2 | `send_message` | message an agent |
| Codex v2 | `interrupt_agent` | stop an agent |

`TaskOutput` on an agent is not in the transcripts still on disk (all 28
calls there target shells), but the DB holds 117 such calls (25 sessions,
2026-01-07 → 2026-08-24, from transcripts since pruned; the compute-version
bump recomputes them). It gets the widget, never a state effect.

A message from a subagent to another subagent of the tree (31 Codex
`send_message` events at review time: 27 sibling → sibling, 2 nested agent
→ parent subagent, 2 subagent → its own nested child) is in scope: the
target is an agent, so the card gets the widget; it has no state effect.

### 3.2 Out of scope

- Codex multi-agent v1 tools (`send_input`, `resume_agent`, `close_agent`):
  4 calls in total, all in May 2026 rollouts. They keep the generic card.
- Tools with no single target: Claude `ListAgents`, Codex `list_agents` and
  `wait_agent`.
- Any call whose target is the **root** session (Codex `send_message` to
  `/root` or `"parent"` from a first-level agent) or is outside the tree
  (Claude `"main"`, a peer session): no agent link exists for it.

### 3.3 Planned follow-ups (not in this design, to revisit later)

- The CLI `session <id> agents` command: it hard-codes `process: null` for
  subagents (`src/twicc/cli/session.py:413-418`) and exposes no run state.
- Subagent crash detection (a child that dies on its own). No signal exists
  today. (A child stopped by its owner's interrupted turn **is** covered:
  Codex by the owner turn abort, Claude by the child's own interrupt
  marker, §5.2.)
- Wake-ups caused by no tool: a Claude agent woken by its own background child
  finishing (`origin.kind == "task-notification"` in its file), or an orphan
  re-wake. These open no run in this design.

## 4. Verified facts

All shapes come from real transcripts (`~/.claude/projects`,
`~/.codex/sessions`), the dev-mode SDK logs
(`~/.twicc/logs/sdk/{claude_code,codex}/`) and the local DB, checked on
2026-09-26. Counts are unique tool_use ids (compaction duplicates some lines).
Claude counts are from the transcripts **on disk**; the DB also holds
sessions whose transcripts were pruned (e.g. 307 `SendMessage` calls, 6
`TaskStop` on an agent, 117 `TaskOutput` on an agent, 3 more root-side
resumed results without `resumedAgentId` in `8d4d7304-…` lines 2231, 2278,
3322). The rules below hold on both (the resume rule of §6.1 is correct on
all 307 DB calls).

### 4.1 Claude Code

**`SendMessage`** (178 calls).

- Input: `{to, summary, message}` plus legacy fields `recipient` (= `to`),
  `content` (a truncated preview of `message`, cut at a word boundary),
  `type: "message"`. On disk, `to` is always an agent id (`a` + 16 hex
  digits); in the DB, 1 failed call has `to: "parent"`.
- Result, two shapes:
  - agent was idle → **resumed** (165 calls). Root-side `toolUseResult`
    (161): `{"success":true,"message":"Resuming agent aab447f","resumedAgentId":"aab447fdc0f856db3","pin":{"id":…,"name":…,"ref":…}}`.
    When the caller is a subagent (4), `toolUseResult` is absent and the
    `tool_result` text holds the JSON, 3 times **without `resumedAgentId`**:
    `{"success":true,"message":"Resuming agent af26957","pin":{"id":"af2695778cf6c742f",…}}`.
    The resumed `message` has other wordings too (4 root-side calls,
    session `966a4ec8`, lines 407/480/531/550):
    `Agent "a2543ae19d2adf6ad" had no active task; resumed from transcript…`
    and `…was stopped (completed); resumed it…` (all with `resumedAgentId`).
    Stable rule: a successful result whose `message` does **not** start with
    `"Message queued"` is a resume.
  - agent was running → **queued** (13 calls: 10 root-side, 3
    subagent-side): `{"success":true,"message":"Message queued for delivery to … at its next tool round.","pin":{…}}`,
    no `resumedAgentId`.
- **End of a resumed run:** a `<task-notification>` whose `<tool-use-id>` is
  the `SendMessage` id (165 of 165 resumed calls; none for queued calls).
  Three arrival forms:
  - a user message, rewritten into a `tool_result` on that id by
    `claude_code/compute.py:1301-1335`;
  - an `attachment` `queued_command` line, rewritten by `:1425-1467`;
  - a `queue-operation` `enqueue`, read by `extract_queue_completion`.

  For a `SendMessage` made by the root, the notification is in the root file
  and gives the call a second `ToolResultLink`. For one made by a subagent, it
  may land in the **root** file only, and then the call gets no second
  `ToolResultLink` (batch pairing needs the tool_use in the same session,
  `compute_base.py:2700-2720`). Of the 4 subagent-made resumes, 2 have their
  notification in the caller's own file too (`attachment` `queued_command`,
  `agent-a663305d8fe755548.jsonl` lines 199 and 246; 2 `ToolResultLink` rows
  each in the DB), and 2 only in the root file
  (`toolu_01USQDKRXhX9zuv97Uxq46U2`, `toolu_018Cq7XQxXUKwt6tBMafr2Ka`; one
  `ToolResultLink` each).
- SDK stream: every resume emits `system/task_started` with
  `task_type: "local_agent"` and `tool_use_id = <SendMessage id>` (165), then
  a `task_notification`. The live agent's `_update_live_tasks`
  (`claude_code/agent/agent.py:1465-1563`) already adds `local_agent` tasks on
  `task_started`: the parent process already counts a resumed agent in its
  background work.
- Receiver side: a `user` line with `isMeta: true`, string content and
  `origin: {"kind": "coordinator"}` (the main thread) or
  `{"kind": "peer", "from": <agent id>, …}` (any agent, a nested agent's own
  parent agent included, e.g. `agent-a3100dd8b750cc1d5.jsonl` line 26). The
  design does not read `origin`.

**`TaskStop`** (75 calls; 4 on an agent).

- Input: `{task_id}`: an agent id (`a` + 16 hex; legacy `a` + 6 hex in 73
  of the 117 DB `TaskOutput`-on-agent calls), a shell id (`b` + 8 chars),
  or a workflow id (`w…`, 3 DB `TaskStop` calls). The tree rule (§5.1)
  sorts them; no shape is parsed.
- Success on an agent:
  `{"message":"Successfully stopped task: <id> (<description>)","task_id":…,"task_type":"local_agent","command":<description>}`;
  on a shell: `task_type: "local_bash"`.
- Failure: `<tool_use_error>Task … is not running (status: completed)</tool_use_error>`.

**`TaskOutput`** (28 calls, all on shells). Input `{task_id, block, timeout}`;
result `{"retrieval_status", "task": {"task_id", "task_type", "status",
"description", "output", "exitCode"}}`.

### 4.2 Codex multi-agent v2

Calls are `response_item` `function_call` rows with
`namespace: "collaboration"`; TwiCC's qualified name is
`collaboration__<name>` (`_qualified_function_call_name`,
`codex/compute.py:1077`; constants at `:490-507`).

| Tool | Arguments | Output |
|---|---|---|
| `followup_task` | `{"target": <agent ref>, "message": "gAAAAA…"}` | `""` |
| `send_message` | `{"target": <agent ref>, "message": "gAAAAA…"}` | `""` |
| `interrupt_agent` | `{"target": <agent ref>}` | `{"previous_status": "running"}` |

- `<agent ref>` takes several shapes: an agent path (`/root/task_01_impl`), a
  bare task name (`t07_implement`), a thread UUID, `"parent"`, `"/root"`. The
  design never parses it: the target comes from the event below.
- `message` is a Fernet ciphertext. The receiver's `NEW_TASK` / `MESSAGE`
  envelope is encrypted too (`codex/compute.py:531-551`). Only `FINAL_ANSWER`
  is plaintext.
- **`SubAgentActivity` events** in the caller's rollout (`event_msg`
  `item_completed`, `item` =
  `{"type": "SubAgentActivity", "id": …, "kind": …, "agent_thread_id": <subagent session id>, "agent_path": "/root/<task>"}`):

  | `kind` | `id` | Emitted by |
  |---|---|---|
  | `started` | the `spawn_agent` call_id | spawn |
  | `interacted` | the `send_message` / `followup_task` call_id | message, follow-up |
  | `interrupted` | the `interrupt_agent` call_id | interrupt |
  | `completed` | `subagent-completed-<child turn_id>` (not a call id; the suffix equals the child's `task_started` `turn_id` in all but 2 events, both in flat-timestamp files; not used by this design) | the agent finished a run |

  In rollout `2026/08/11/rollout-2026-08-11T15-36-50-019ff10a-….jsonl`:
  `started` ← `spawn_agent` 43/43, `interacted` ← `send_message` 16/16 and
  `followup_task` 7/7, `interrupted` ← `interrupt_agent` 1/1. 4 `interacted`
  events in forked rollouts (2026/08/12, e.g. `call_tan2hioACpKEriHAI8pzRJcL`)
  have no matching call in their file.
- **`completed`** exists from 2026-08-27T13:03:18Z onward (980 events
  before 2026-09-26; the count grows with every new rollout). Most
  runs get one; 13 later runs end with a `FINAL_ANSWER` only.
- **End of a run:** the `completed` event and/or the subagent's
  `FINAL_ANSWER`; before 2026-08-27, `FINAL_ANSWER` only. A run cut by
  `interrupt_agent` before its end gets neither.
- **`completed` → `FINAL_ANSWER` lag** (same agent): median 0.031 s, p90 14 s,
  p99 213 s, max 1588 s. The parent keeps making calls inside that gap.
- **Follow-up on a running agent.** Over all rollouts: 352 follow-ups on an
  idle agent (an interrupted agent counts as idle: 4 follow-ups come a few
  seconds after an `interrupt_agent`), 4 on a running one.
  - 3 of the 4 **merge** into the running run: one `completed` and one
    `FINAL_ANSWER` in total. Example:
    `2026/08/31/rollout-2026-08-31T01-43-04-01a0550e-….jsonl`, agent
    `/root/task2_backend_notifications`: spawn line 1285 (`started` 1286),
    `followup_task` 1307 (`interacted` 1308), one `completed` 1318, one
    `FINAL_ANSWER` 1322.
  - 1 of the 4 runs **after** the current run:
    `2026/09/06/rollout-2026-09-06T13-14-17-01a0766d-….jsonl`, agent
    `/root/reaudit_backend`: spawn 4117, `followup_task` 4241, `completed`
    4246 + `FINAL_ANSWER` 4251 (first run), then `completed` 4293 +
    `FINAL_ANSWER` 4300 (follow-up, 2 min 17 s later). Nothing in the parent
    rollout tells the two cases apart at the call.
- `send_message`: over all rollouts, 24 sends to an idle agent (22 outside
  the flat-timestamp files, §10) and about 1680 to a running one (e.g. 08-11
  rollout, task_03, line 382); none produced an extra end signal or a new
  child turn. `send_message` never wakes an agent.
- **Live app-server stream:** the parent sees `subAgentActivity` items of all
  four kinds (about 950 distinct `completed` items in the SDK logs, each emitted
  twice: `item/started` and `item/completed`), and
  `collabAgentToolCall` items **only for `wait`**. No live item names
  `followup_task` or `send_message`.

### 4.3 Current TwiCC state (what this design changes)

- `AgentLink` (`src/twicc/core/models.py:872-906`): one row per spawn;
  `(session, tool_use_line_num, tool_use_id, agent_id, is_background, started_at)`.
  No state column.
- Stop detection: `check_agent_naturally_stopped`
  (`src/twicc/providers/compute_base.py:1893-1943`) finds the `AgentLink` of
  the tool_use that just got a result and stops the agent when the count
  reaches `2 if is_background else 1`; it stamps `last_stopped_at` through a
  guard on `last_updated_at` (`.exclude(last_updated_at__gt=stop)`) and emits
  `AgentStoppedUpdate` only when the row changed. The child's own sync
  (`compute_base.py:3900-3901`) and the recompute (`:2962-2972`) write
  `last_stopped_at` without that guard. "Monotonic guard" below means this
  `last_updated_at` guard.
  Batch equivalent: `compute_base.py:2861-2873`.
- Queue completions: live `apply_queue_completion`
  (`compute_base.py:2242-2260`) stamps `last_stopped_at` and emits an
  `AgentStoppedUpdate` for any in-tree child, whatever the tool_use id. The
  snapshot (`build_subagents_state`, `src/twicc/core/session_queries.py:199-237`)
  scans the root's `queue-operation` items on every call
  (`ClaudeCodeHelpers.get_queue_completions`, `claude_code/helpers.py:223-234`)
  and applies them to spawn links only.
- Codex:
  - `_parse_sub_agent_activity_started` (`codex/compute.py:1735-1763`) drops
    the other kinds; `is_tool_result_item` (`:3971`, its `SubAgentActivity`
    check at `:4026-4028`) only accepts
    `started` among `SubAgentActivity` events.
  - `FINAL_ANSWER` is rebound to the **newest** spawn for its `agent_path`:
    batch via `_agent_id_to_spawn_call_id` (accessor `_agent_id_map`,
    `codex/compute.py:2218-2226`; filled at `:4970`, read in
    `_resolve_tool_result_id` `:2441-2448`), live via
    `_lookup_spawn_call_id_for_agent_path` (`:2772-2801`).
  - The child's idle boundary (`subagent_turn_boundary`,
    `codex/compute.py:4808-4828`) counts as a stop: backend
    `subagent_idle_trusted` (`providers/helpers.py:279-280`, `True` in
    `codex/helpers.py:180`), frontend `agentRunEndsOnSubagentIdle`
    (`baseHelpers.js:1516`, `codex/toolHelpers.js:1860`).
  - Live agent: `_note_sub_agent_activity` (`codex/agent/agent.py:1047-1068`)
    ignores `interacted`; its docstring says `completed` never reaches the
    parent stream, which the SDK logs contradict. `_prune_finished_subagents`
    (`:1278-1303`) drops every child whose `last_stopped_at` is set
    (`_stopped_subagent_ids`, `:350-367`); ephemeral runs poll `thread_read`
    instead (`:1256-1276`).
- Snapshot: `serialize_agent_links` (`session_queries.py:240-299`) computes
  `running` per spawn link.
- Frontend: every consumer of the current stop state is listed in §8.1.

## 5. Model

### 5.1 New table `AgentInteraction`

One row per control-tool call that targets one agent.

| Field | Type | Meaning |
|---|---|---|
| `session` | FK `Session`, `related_name="agent_interactions"` | the session holding the call (root or subagent), like `AgentLink.session` |
| `tool_use_line_num` | positive int | line of the call |
| `event_line_num` | positive int | line where the row is decided: Codex, the `SubAgentActivity` `interacted` / `interrupted` line; Claude, the call line (the row is created there; `opens_run` is decided later, at the first result line, §6.1). Every "before line L" / "file-open at line X" test of §5.6 and §6.2 reads this stored line, in live and batch alike (live cannot find a past event line otherwise, and a `completed` can land between a call and its event) |
| `tool_use_id` | char 255 | the call id |
| `agent_id` | char 255 | the targeted session id |
| `kind` | char, choices `message` / `resume` / `stop` / `output` | the action |
| `opens_run` | bool, default `False` | the call started a new run of the agent |
| `started_at` | datetime, nullable | Claude: timestamp of the call's item at creation; when `opens_run` turns true it is **replaced** by the first result's `tool_result_at` (the resume ack, written after the resume really starts; never null for agent results, §5.4), live and batch alike, and the `agent_interaction` re-broadcast carries it. The call line is written before the tool runs, so a stop written between the call and the resume — a `TaskStop` earlier in the same assistant message, a Stop button that returns while the `SendMessage` waits on a hook or a prompt — would otherwise close (rule 2) the run it did not stop. Codex: timestamp of the `interacted` / `interrupted` event, where the row and its `opens_run` are decided — so rule 5 never compares a new follow-up run with the previous run's child turn end, which can fall between the call and that event (`01a0796d-72bd…`: parent `completed` line 210 and child `task_complete` both at 01:20:11.573; call → `interacted` gaps up to 1.6 s in `01a08171…`) |

- Unique constraint `(session, tool_use_id)`; index `(agent_id)`.
- Duplicates (compaction-duplicated lines, live-vs-batch races): the
  **first** line wins, in both paths. Batch keys its dict by `tool_use_id`
  with first-wins (`setdefault`) — unlike the batch `task_tool_use_map`
  (`compute_base.py:2687-2688`) and `all_agent_links` (`:2744`), which
  overwrite on each copy; live keeps the
  first row it created. The apply step re-checks existence inside the
  atomic writer (as `compute_base.py:3171-3183` does for links). Same line
  in both paths matters: the frozen-share filter and the display-ceiling
  check read it.
- **Write-time rule:** a row is written for every in-scope call whose target
  is neither the owner session nor the owner's root (`parent_session_id or
  id`; this skips about 2200 Codex `send_message` → owner/root events). Claude:
  `SendMessage` (target `input.to`),
  `TaskStop` / `TaskOutput` (target `input.task_id`). Codex: every
  `interacted` / `interrupted` event whose `id` matches a call in the file
  (no call → no row). The writer does **not** check that the target is a
  known agent: the target's spawn link may be applied later (the background
  compute processes sessions by `-mtime`,
  `providers/background_compute_task.py:612-619`; Codex has its own loader,
  `providers/codex/background_compute.py:163-170`), and a finished session is
  not recomputed until the next compute-version bump.
- **Read-time rule (the "tree rule"):** an interaction counts — for state,
  snapshot, WS and widget — only when its `agent_id` has an `AgentLink` in the
  same root tree (`tree_agent_links`, `session_queries.py:151-156`: links
  owned by the root or by a session whose `parent_session_id` is the root;
  both providers store subagents flat under the root, nested ones included)
  **and** its owner `session` is the root or a session whose
  `parent_session_id` is the root — the same owner filter as
  `tree_agent_links` — so an interaction written outside the tree (e.g. a
  Claude CLI fork that copied `SendMessage` / `TaskStop` calls under a new
  root id, or a colliding legacy 7-character agent id) never opens or
  closes a run of this tree's agent (the same filter applies to run
  interactions, stop records and the snapshot's `interactions`).
  This rejects shell `task_id`s, `"main"`, peers and the root, whatever the
  write order.
- New indexes for the per-agent queries of §5.4: `AgentLink(agent_id)` and
  `ToolResultLink(session, tool_use_id)` (the existing lookup index is
  `(session, tool_use_line_num, tool_use_id)`, `models.py:857-860`).
- `AgentLink` gets no new field and no new row kind (only the
  `agent_id` index above). The frontend indexes one spawn link per agent
  (`agentLinkIndex`); adding interaction rows to `AgentLink` would overwrite
  the spawn's owner and display name and break the agent tree.

### 5.2 New table `AgentRunEnd` (end evidence that is not a tool result)

One row per run-end signal that must not, or cannot, become a
`ToolResultLink` of the run's call:

- **Claude:** a `<task-notification>` in any of its three forms (§4.1), in
  any file of the tree. It is written when it has a `<task-id>` and a
  `<tool-use-id>`, and either its status is terminal (`completed`,
  `failed`, `stopped`, `killed`, `cancelled`, `canceled` — `killed` is the
  status of a `TaskStop`'d agent, which `parse_queue_completion`,
  `claude_code/notifications.py:160-181`, drops today) or it has **no**
  status and `is_task_result` is true (old payload-only completions,
  `:74-134`). A notification with a non-terminal status (e.g. `running`
  with a payload) is never written, as `parse_queue_completion` already
  refuses it (`tests/test_subagents_tree_endpoint.py:66-67`). For the
  `queue-operation` form, only `operation == "enqueue"` lines count; the
  `remove` lines carry the same XML and would duplicate rows. Monitor and
  shell notifications also match; the tree rule filters them out at read
  time (their `task_id` has no agent link).
- **Codex:** a `SubAgentActivity` `completed` event, attributed to one call
  by §5.6. It is **not** a `ToolResultLink`: that would reach the spawn card's
  expected count (2, `codex/toolHelpers.js:993`) before the `FINAL_ANSWER`,
  and the card's result fetch (`ToolUseContent.vue:208-217`, `:265-268`)
  would stop polling before the `FINAL_ANSWER` row exists.
- **Codex, owner turn abort:** an `event_msg` `turn_aborted` with
  `reason == "interrupted"` (`{"turn_id", "reason": "interrupted"}`; the
  only reason in the data, and other reasons do not cut, §10) or a
  `task_complete` whose
  `error.codex_error_info` is `usage_limit_exceeded` (the only error kind
  observed with running children; other kinds — `server_overloaded`,
  `cyber_policy`, `other` — never had one and do **not** cut, §10), in the
  rollout that **owns** runs (the root, or a subagent
  that spawned / followed up its own children). The running children stop
  with that turn and write no end signal: over all 232 root aborts and error
  turn ends, none of the children running at that moment completed that
  turn; only 3 of these events had running children — 2 interrupts and 1
  usage-limit error (e.g. `01a08171…` line 17910, `turn_aborted` at 06:45:10 while the
  `t09_implement` resume run is open; `01a0796d…` line 704, usage-limit
  `error`, three `review_*_spec` resume runs open). For each run owned by
  that file that is **file-open** (§5.6) at that line **and whose call line
  is after the aborted turn's `task_started`** (the runs of that turn: all
  observed cut runs were opened inside the aborted turn, while children of
  an earlier turn are known to outlive a normal turn end, the t20 case).
  The aborted turn's `task_started` is found by the event's `turn_id`
  (both `turn_aborted` and `task_complete` carry it, e.g. `01a08171…` lines
  6336 → 17910, `01a0796d…` lines 4 → 704; live, a DB lookup in the same
  session, since it is usually in an earlier batch; batch, the Codex
  per-session `turn_id → task_started line` map of §6.2); if none is found,
  no run is cut. One row with
  `tool_use_id = <run call id>`, `agent_id` = that run's agent (its
  `AgentLink` / `AgentInteraction` `agent_id`; the abort event carries no
  `agent_thread_id`), `status = "owner_turn_aborted"`, `ended_at` = the
  event's time. The root cutoff does not cover this: the
  root's `last_stopped_at` is only stamped live when its process dies
  (`agent/base_manager.py:665-666`); the batch recompute sets it to the file
  mtime (`compute_base.py:2968-2972`), which is not the abort time either.
- **Codex, agent-level turn end:** an `event_msg` `task_complete` in a
  **subagent's own** rollout (the boundary `subagent_turn_boundary` already
  reads, `codex/compute.py:4808-4828`). It is written with
  `agent_id = <that subagent>` and `tool_use_id = ""` (empty: it ends the
  agent's current turn, not one call). It exists because, when the parent is
  idle, the child's end reaches the parent rollout only at the parent's next
  turn, with no `completed` event: rollout `01a08171-a93c-…`, agent
  `/root/t20_implementation` (spawn call line 76275, `started` 76277) —
  parent turn ends 01:18:35Z,
  child `task_complete` 03:50:03Z, next parent turn and `FINAL_ANSWER`
  06:32:24Z (line 76417). Without this row the agent would show running for
  2 h 42 min. Unlike `Session.last_stopped_at`, which the batch overwrites
  with the file mtime (`compute_base.py:2968-2972`), this row carries the
  real boundary time in both paths (except the flat-timestamp files of
  §10, whose line times are themselves wrong).
  - **Copied history is skipped.** A child spawned with fork history
    (`session_meta` has `forked_from_id`) starts with a copy of the parent's
    turns, whose `task_complete` lines carry the fork time (e.g.
    `2026/09/08/…-01a08112-b173…`, `subagent_history_start_ordinal` 54,
    copied `task_complete` lines 10-49 at 12:51:21.085, 43 ms after the
    spawn). **Only in a forked child** (its `session_meta` has
    `forked_from_id`), lines whose `ordinal` is below
    `subagent_history_start_ordinal` write no row. (In some older forks —
    cli 0.146.0 v2, cli 0.130/0.131 v1 — the child's own real turn ends also
    sit below the field; see §10.) Non-fork
    children also
    carry that field (549 rollouts, cli 0.130.0–0.153.4) with their own real
    turn end below it (e.g. `01a05541-635c…`: field 232, `task_complete` at
    231), so the field alone is not the gate. Without this filter, ~18 forked
    agents would show stopped for their whole first run (up to 3 h 51 min).
    Every live batch, the first included, reads both fields from the
    child's line-1 `SessionItem` (`session_meta`) in the DB: the items are
    bulk-created (`compute_base.py:3694`) before the second pass that runs
    the hook (`:3700-3701`). Batch reads them from the same line,
    kept in the Codex per-session state of §6.2 when the loop passes it.

- **Claude, agent-level interrupt:** a `user` line in a **subagent's own**
  file whose only content is exactly `[Request interrupted by user]` (not
  `isMeta`, no `origin`; the text quoted inside a prompt or a coordinator
  message does not match). A user interrupt of the root turn kills the
  running background agents without writing any notification: agent
  `aa6d89b7469be1a2c` (root `0e7ecdbb-…`) — the root got
  `[Request interrupted by user]` at 10:10:03.799 (SDK `task_updated`
  `killed`, `task_notification` `stopped` at the same moment), no transcript
  carries a notification, the child file ends with the marker (line 39,
  10:10:03.806), and its run stayed open 3 h 42 min. Over all subagent
  files the strict marker occurs 4 times, always as the file's last line
  (the agent never acts again without a new run). Written with
  `agent_id = <that subagent>`, `tool_use_id = ""`,
  `status = "interrupted"`, `ended_at` = the line's time; it closes the runs
  started before it (rule 5, §5.4).
- **TwiCC Stop button (Claude), safety net:** a successful
  `manager.stop_subagent` (`asgi.py:1384-1441` →
  `claude_code/agent/agent.py:1262-1291`, SDK `stop_task`). A real
  Stop-button stop does write a `killed` notification (e.g.
  `ab35d70110fd1e813`, same root, root lines 1494/1498), which rule 4
  already reads; the handler still writes one row itself, so the state of
  the runs started before its `ended_at` never depends on that line (a run
  resumed after `ended_at` and then killed by this stop closes only
  through its own `killed` notification, §10): `session` = the root, `line_num` = null,
  `agent_id` = the stopped agent, `tool_use_id = ""`,
  `status = "ui_stopped"`, `source = "ui"`, `ended_at` = the time the
  handler **sends** the stop request, read just before
  `await manager.stop_subagent(...)` (`asgi.py:1435`), outside the lock.
  The row itself is written only when that call succeeds, **inside** the
  DB write lock this design adds, in the same critical section as the
  §6.3 stop step and its broadcasts. A resume whose ack lands after that time — while
  the stop IPC round trip is in flight, or during the lock wait — starts
  after it (a Claude run starts at its ack, §5.1), so rule 2 does not close
  it; if the kill then stops that resumed run, its `killed` notification
  names the resuming `SendMessage` and closes it (rule 4). Then it runs the
  generic stop step of §6.3 for that agent and sends its output through the
  same shared broadcast helper as the watcher (§6.3): `agent_run_state`,
  the child `session_updated` and `agent_stopped` when stamped — sent
  **before** it releases the lock, after the transaction, as the watcher
  does, so a watcher batch cannot broadcast a newer `agent_run_state`
  between the handler's read and its send. The `_after_agents_stopped`
  hook is skipped on this path: it is a watcher method, the handler has no
  watcher instance, and only Claude has a Stop button, whose hook is the
  base no-op (`providers/sessions_watcher.py:339-354`).

| Field | Type | Meaning |
|---|---|---|
| `session` | FK `Session`, `related_name="agent_run_ends"` | the file holding the signal (the root for a `ui` row) |
| `line_num` | positive int, nullable | its line (null for a `ui` row) |
| `source` | char, `"transcript"` (default) / `"ui"` | `ui` rows come from the (Claude-only) Stop button, not from a file; batch diffs never delete them |
| `agent_id` | char 255 | Claude notification: `<task-id>`; Claude `interrupted`: that subagent; `ui_stopped`: the stopped agent; Codex `completed`: `agent_thread_id`; Codex `turn_complete`: that subagent; `owner_turn_aborted`: the cut run's agent |
| `tool_use_id` | char 255 | Claude notification: `<tool-use-id>`; Codex `completed`: the attributed call id (§5.6); `owner_turn_aborted`: the cut run's call id; `""` for the agent-level rows (`turn_complete`, `interrupted`, `ui_stopped`) |
| `ended_at` | datetime, nullable | the signal item's timestamp; for a `ui` row, the time the handler sends the stop request (read before `manager.stop_subagent`, see above) |
| `status` | char, nullable | Claude notification status, `"interrupted"` (agent-level) or `"ui_stopped"`; Codex `"completed"`, `"turn_complete"` (agent-level) or `"owner_turn_aborted"` |

- Unique `(session, line_num, tool_use_id)` for `transcript` rows (a
  conditional constraint; `ui` rows have no line); index
  `(agent_id, tool_use_id)`.
- `transcript` rows are written by the compute of the session holding the
  signal, live and batch (diffed per session like the other link tables,
  over `source = "transcript"` rows only). `ui` rows are written by the
  Stop-button handler only. No FK to the run: a row is valid whatever the
  order in which sessions are computed.
- It replaces the snapshot's per-call scan of `queue-operation` items: rule 4
  of §5.4 reads this table.

### 5.3 Kinds

| Kind | Tools | `opens_run` |
|---|---|---|
| `message` | Claude `SendMessage` | true when the first result is the resumed shape (§6.1) |
| `message` | Codex `send_message` | always false |
| `resume` | Codex `followup_task` | true when the agent has **no file-open run** (§5.6) at the call's `interacted` line; false otherwise ("merged") |
| `stop` | Claude `TaskStop` on an agent, Codex `interrupt_agent` | false |
| `output` | Claude `TaskOutput` on an agent | false |

### 5.4 Runs

- A **run** is a spawn (`AgentLink`) or an `AgentInteraction` with
  `opens_run = true` that passes the tree rule.
- Required results: spawn → `2 if is_background else 1` (unchanged);
  interaction run → `2` (ack + end signal).
- A run is **closed** when any of these holds:
  1. its **result count** reaches its required count. The result count is
     the number of **distinct `tool_result_at`** values among its
     `ToolResultLink` rows `(owner session, tool_use_id)`, not the number of
     rows: compaction copies a result row with its original timestamp, and
     counting rows closes runs early (in the DB, 95 agent calls have more
     rows than distinct timestamps; counting rows would close 69 runs early,
     median 11 min, max 44 min — e.g. the `aa6d89b7469be1a2c` spawn, whose
     ack is copied at lines 10228 and 13306, both 10:08:30.546). No agent
     result row has a null `tool_result_at`;
  2. an explicit stop of the agent (§5.5) has a stop time at or after the
     run's `started_at` (a run with a null `started_at` is closed by any
     stop of the agent). **Tie-break:** when the stop time **equals** the
     run's `started_at`, and the stop record and the run's opening result
     are in the same owner file, line order decides: a stop whose first
     non-error result line is **before** the run's opening line (a Claude
     run's first-result line, a Codex run's `event_line_num`) does not
     close it — e.g. `TaskStop(A)` then `SendMessage(to=A)` in one
     assistant message whose two results carry the same millisecond (36
     pairs of distinct Claude calls share a `tool_result_at` in the DB). A
     `ui_stopped` row has no line, so the tie-break does not apply to it:
     an equal time closes the run (§5.2). The tie-break does not apply to
     a **spawn** run (`AgentLink`) either: an equal time closes it (a stop
     cannot target an agent before its spawn exists);
  3. its `started_at` is before the root's cutoff (existing rule,
     `Session.cutoff`, `models.py:676-683`); a null `started_at` counts as
     before the cutoff when a cutoff exists, as today
     (`session_queries.py:272-274`);
  4. an `AgentRunEnd` exists for `(agent_id, run tool_use_id)`;
  5. an agent-level `AgentRunEnd` (`tool_use_id = ""`, status
     `turn_complete` — Codex — or `interrupted` — Claude, §5.2) of that
     agent has `ended_at` **after** the run's `started_at` (a null
     `started_at` or `ended_at`: rule 5 does not apply). (`ui_stopped` rows
     are stop records, rule 2, §5.5.)
- An agent is **running** when at least one run is open.
- Rule 5 only feeds the running state. It never feeds the result
  attribution of §5.6 (which stays parent-file only), so the round-trip race
  between the child file and the parent file cannot misattribute a signal.
  An older turn end never closes a newer run.
- The **subagent's** `Session.last_stopped_at` is **not** a run input, for
  either provider (the batch overwrites it with the file mtime). It stays a
  display value (the tree's "finished" time). The root's cutoff (rule 3)
  keeps reading the root's own `last_started_at` / `last_stopped_at`, as
  today.
- For Claude, rule 5 reads only the `interrupted` marker, never the child's
  `end_turn`: the `end_turn` precedes the notification only slightly
  (strict `stop_reason == "end_turn"`, 262 runs: median 0.029 s, p90
  0.169 s, max 2.18 s), so the notification already closes the run in
  time, and a Claude agent can be re-woken without a tool (§3.3).
- A shared backend helper
  `agent_run_states(root, agent_ids, frozen_at_line=None, exclude=None)`
  implements this section on DB rows. `frozen_at_line`, for frozen shares
  (§7.2), limits both the root's evidence **and its runs**: a root-owned run
  interaction is a run only when its call line (`tool_use_line_num`) **and**
  the line that decided `opens_run` are at or before `frozen_at_line` — for
  Claude the call's first `ToolResultLink` line in the root, for Codex its
  `event_line_num` (the `interacted` line); otherwise it counts as not a
  run (`opens_run` false in the snapshot's `interactions`). The call-line
  part is exactly the link filter, `session_queries.py:209-216`; runs owned
  by a visible agent are kept, and runs owned by an agent that is **not**
  visible (spawned after the freeze, dropped by the same link filter,
  `visible_tree_agent_ids`, `session_queries.py:119`, called by the link
  filter at `:210-217`) are dropped — `agent_run_states` selects interactions by
  target, so without this owner rule a post-freeze agent's `SendMessage`
  resume of a visible agent would show it running. The same owner rule
  applies to stop records. Likewise a root-owned **stop record** counts
  only when its `event_line_num` (for Codex, the `interrupted` line; for
  Claude, the call line) **and** its non-error result line are both at or
  before `frozen_at_line` (a Codex stop's result can come before its
  `interrupted` line, §5.6; at the freeze no stop row existed yet). A root
  `ui` row (no line) counts only when its `ended_at` is at or before the
  freeze time (the rule of §7.2).
  `exclude` (used only by the stop step, §6.3)
  is a set of explicit row ids that the live pass collects while it writes
  them (no row carries a batch identity):
  - created `AgentLink` rows, and created interactions plus interactions
    whose `opens_run` turned true in this batch (the Claude first result):
    their runs are treated as **absent** (created in this batch);
  - interactions whose target had **no** in-tree `AgentLink` before this
    batch and gets one in it: their runs are treated as absent too (they
    first pass the tree rule in this batch). Only `AgentLink` row
    **creations** count here — an `is_background` upgrade of an existing
    link (an `AgentLinkUpdate`, `compute_base.py:2002-2019`, `:2110-2115`,
    also broadcast as `agent_link_created`) does not, and a new link for
    an agent that already had one in the tree does not either, so an
    already-closed run is never re-closed;
  - stop interactions that **became** stop records in this batch (row
    created, or first non-error result written): left out as stop
    records — e.g. a Codex stop row created at its `interrupted` line
    whose result was written in an earlier batch;
  - written `ToolResultLink` and `AgentRunEnd` rows, plus a `ui` row: left
    out as evidence.
  It returns, per agent:
  `{known, running, run_started_at, run_background, stopped_at, runs: [{owner_session_id, tool_use_id, started_at, open, closed_at}]}`
  (`closed_at` = the run's close time below, null while open)
  where `known` says the agent has at least one run (a spawn link or a run
  interaction), `run_started_at` / `run_background` describe the newest open
  run (`run_background`: spawn `is_background`, or `true` for an interaction
  run), and `stopped_at` is the **max of the non-null `closed_at` of its
  closed runs** (null when no closed run has a non-null `closed_at` — the
  same rule as the §6.3 stop time), where a run's close time (`closed_at`) is the **earliest
  non-null time** among the evidence that closes it (the result that made
  it reach its count, a stop record's stop time, an `AgentRunEnd.ended_at`,
  the cutoff); it is null only when every closing piece has a null time.
  Evidence with a later time than the run's close time does not move it,
  whatever the arrival order (the earliest wins): e.g. t20, closed by its
  child's turn end at 03:50:03Z (rule 5), keeps that time when its
  `FINAL_ANSWER` lands at 06:32:24Z.
  It is the **only** place that decides whether an agent runs (§6.3, §6.4,
  §7), for sessions with watcher rows. Two live child-set paths of a Codex
  root stay outside it (§6.4): the SDK-stream updates of
  `_note_sub_agent_activity` (`started` adds, `interrupted` / `completed`
  pop, `codex/agent/agent.py:1047-1068`; for an ephemeral agent also the
  `interacted` re-add, §6.4), and an ephemeral parent's
  `thread_read` path; the Claude parent's `_live_background_tasks`, fed
  by the SDK `task_started` / `task_notification` stream
  (`claude_code/agent/agent.py:1465-1563`, §4.1, §6.4 "no change"), also
  stays outside it. It filters links, interactions, results and ends by the given
  `agent_ids` (trees reach 413 links and 15 000+ `ToolResultLink` rows on the
  root), never by the whole tree.

### 5.5 Explicit stops

- **Record:** an `AgentInteraction` with `kind = stop` (passing the tree rule)
  whose call has at least one `ToolResultLink` with `error IS NULL`; **or**
  a `ui_stopped` `AgentRunEnd` (§5.2, TwiCC Stop button).
- **Stop time:** the earliest such `ToolResultLink.tool_result_at`; for a
  `ui_stopped` row, its `ended_at`.
- **Effect:** closes every run of the agent whose `started_at` is at or
  before the stop time (rule 2 of §5.4, with its same-time line-order
  tie-break); a run with a null `started_at` is closed by any stop.

### 5.6 Codex result attribution

- **End signals** of one run: a `completed` event (stored as an
  `AgentRunEnd`, §5.2) and a `FINAL_ANSWER` (stored as a `ToolResultLink`, as
  today). Each is attributed to one **candidate** call.
- **Candidates** for agent A in owner session S, **relative to a reference
  line L** (the signal's line for attribution; the `interacted` line for
  `opens_run`; the abort event line for the owner turn abort; the stop's
  `event_line_num` for the stop-in-line-order exclusion below): the spawn
  of A owned by S (its call line, `AgentLink.tool_use_line_num`, before L;
  no signal of A can come before its spawn's `started` event, so the call
  line is enough) and every `resume` interaction owned by S targeting A
  with `opens_run = true`, whose stored **`event_line_num`** (its
  `interacted` line, §5.1) is before L — the same in live and batch —
  ordered by call line. Merged
  follow-ups (`opens_run = false`), runs **stopped in line order** by a
  `stop` interaction of the same file — the run was file-open at the stop's
  stored `event_line_num` (its `interrupted` line), and both that
  `event_line_num` and the stop's non-error result line are before L (a
  Codex stop's result can come before its `interrupted` line; live has no
  stop row before that line) (the tree rule is not applied here, stops from other
  files are ignored, and a run that already has its `completed` stays a
  candidate for its own `FINAL_ANSWER`, so attribution stays file-local and
  line-ordered; §5.5 still closes those runs by time for the running state)
  and runs
  cut by an owner turn abort whose abort event line is before L
  (`AgentRunEnd` with status `owner_turn_aborted`, §5.2, read by its stored
  line — batch from `batch_state`, earlier lines only; live from the DB
  rows below L — so both exclude the same runs) are **not** candidates.
- For a candidate: "has `completed`" = an `AgentRunEnd` with status
  `completed` exists for its call;
  "has `FINAL_ANSWER`" = its result count (distinct `tool_result_at`, rule 1
  of §5.4) reached 2 (ack + `FINAL_ANSWER`), counting result lines before
  L. Live reads the DB rows; batch reads the `BatchAgentState` view of the
  base loop's dicts built so far (§6.2; the loop runs in line order and
  fills `all_tool_result_links` before the next line's
  `remap_tool_result_id`, `compute_base.py:2694-2717`), so the Codex batch
  attribution uses the same count as live, never an "a `FINAL_ANSWER` was
  rebound here" shortcut. The batch `FINAL_ANSWER` rebind
  (`remap_tool_result_id`, `compute_base.py:1310`) gains a keyword
  parameter `batch_state` carrying that view, since it also needs the
  in-pass interactions (`opens_run`, `event_line_num`), `completed` rows,
  stop records and abort rows.
- **Rules:**
  - `completed` → the oldest candidate with **neither** signal; else the
    oldest candidate that has a `FINAL_ANSWER` but no `completed`. (Neither
    first: after a run that ended with a `FINAL_ANSWER` only, the next
    follow-up's `completed` must land on that follow-up — e.g. `01a08171…`,
    agent t20: spawn ends with `FINAL_ANSWER` only at line 76417,
    `followup_task` at 76422, its `completed` at 76508 → the follow-up, its
    `FINAL_ANSWER` at 76517 → the follow-up; next `followup_task` at 76532,
    `completed` 76583 → that one.)
  - `FINAL_ANSWER` → the oldest candidate that has `completed` but no
    `FINAL_ANSWER`; else the oldest candidate with neither signal.
  - No candidate matches (all already ended) → the newest candidate, as an
    extra signal.
  - No candidate at all → for `completed`: the newest spawn of
    `agent_thread_id` in the file, and no row when the file has none; for
    `FINAL_ANSWER`: the newest spawn for
    the sender path (today's rule). (Not hit in the full replay.)
- **File-open run** (used by `opens_run`, §5.3, by the owner turn abort,
  §5.2, and by the stop-in-line-order exclusion above): a candidate,
  relative to the `interacted` line, the abort event line or the stop's
  `event_line_num` as L, with neither signal before L (candidates already exclude runs
  stopped, or cut by an abort, before L). It depends only on evidence inside the same file (the
  call's own results, the file's `AgentRunEnd` rows and stop records); never
  on the root
  cutoff or on evidence from other files. Live and batch compute it the same
  way, so the persisted `opens_run` never flips between them (except the
  never-observed spawn-call → `started` gap, §10).
- The agent is found from the event: `agent_thread_id` for `completed`; the
  sender path for `FINAL_ANSWER`, resolved through the `SubAgentActivity` rows
  of the file (the newest `started` row announcing the path, as today).
- The rules depend only on line order inside one rollout, so live and batch
  agree (except the never-observed spawn-call → `started` gap, §10). They cover: a long `completed` → `FINAL_ANSWER` gap with a follow-up
  inside it (the `FINAL_ANSWER` still goes to the run that got the
  `completed`); a merged follow-up (never a candidate); a follow-up after an
  `interrupt_agent` (the interrupted run, stopped in line order, is not a
  candidate; one that got its `completed` first stays a candidate, see the
  §9 race test; the follow-up
  opens a run and gets the next signals); runs ending with `FINAL_ANSWER`
  only, and the follow-ups after them (the t20 sequence above). Not covered
  (never observed, §10): a `completed` arriving **after** its run's
  `FINAL_ANSWER` while a newer run has neither signal.
- **Known limitation** (1 of 356 follow-ups, §4.2): a follow-up on a running
  agent that actually runs after the current run
  (`2026/09/06/…01a0766d…`, `/root/reaudit_backend`). It is recorded as
  merged, so the agent shows stopped between the first run's end and the
  follow-up's end (2 min 17 s), and the follow-up's end signals land on the
  spawn as extra signals (the spawn is the only candidate).
- Consequence: the `FINAL_ANSWER` of a follow-up that opened a run lands on
  the `followup_task` card, not on the spawn card.

## 6. Signals per provider

### 6.1 Claude

- **Creation, at the `tool_use`.** Live: inside the Claude
  `apply_agent_run_signals` hook (§6.2), the single live entry point for
  every Claude transcript signal of this design (the Stop-button
  `ui_stopped` row is written by the handler, §5.2) — interaction creation at the
  `tool_use`, the first-result `opens_run` update, end notifications, the
  interrupt marker. Batch: the Claude `collect_agent_run_signals` hook
  (§6.2), the single batch entry point for the same signals, called by the
  loop that fills `task_tool_use_map`. The widget appears at once when the
  tree rule already passes.
- **First result of a `SendMessage`:** parse `toolUseResult`, else the JSON
  text of the `tool_result`. `opens_run = true` when `resumedAgentId` is
  present, **or** `success` is true and `message` does not start with
  `"Message queued"` (covers every resumed wording of §4.1, including the
  subagent-side text without `resumedAgentId`). `success` false → no run.
  Only the first
  result updates the row: the hook (which runs after the line's result
  link is written, live and batch) treats the line as the first result
  when the call's result rows have exactly one distinct `tool_result_at`,
  this line's included (a compaction copy of the first result re-decides
  the same value; `opens_run`'s default `false` cannot mark "not decided
  yet"). When it sets `opens_run = true`, it also sets `started_at` to
  this result's `tool_result_at` (§5.1).
- **`TaskStop`:** its result makes it a stop record (§5.5).
- **Interrupt marker:** in a **subagent** file, a `user` line whose content
  is exactly `[Request interrupted by user]` (a string, or a single text
  block), not `isMeta`, with no `origin` — the same rule as §5.2 — writes
  the agent-level `interrupted` `AgentRunEnd` of §5.2. Live
  through `apply_agent_run_signals` on the child file's lines, batch in the
  child's own compute; the line is not rewritten at ingest (it is only
  classified `SYSTEM`, `claude_code/compute.py:1682-1684`), so both read it
  as is. The existing `is_interruption_marker` / `INTERRUPTION_MARKER_PREFIX`
  (`:94`, `:188-191`) also match the `… for tool use]` variant; this rule takes the
  plain form only (the variant never occurs as a subagent line).
- **End notifications:** every notification matching §5.2 writes an
  `AgentRunEnd`, in the three forms, in **every** session type (root and
  subagent files; the live queue path today runs for `SessionType.SESSION`
  only, `compute_base.py:3758`).
  - **Parse input, same rule live and batch:** the user / attachment forms
    are rewritten at ingest (`claude_code/compute.py:1301-1335`,
    `:1425-1467`), and the live second pass receives the same rewritten dict
    (`transform_inline` mutates it in the first pass,
    `compute_base.py:3540-3548`). So the parser reads the preserved original:
    `twiccOriginalContent` for the user form (`claude_code/compute.py:1315`,
    `:1341`, `:1372`, `:1396`), `twiccOriginalEntry` for the attachment form
    (`:1463`, `:1499`, `:1546`), the raw content for a `queue-operation` line
    (never rewritten), to get `<task-id>`, `<tool-use-id>` and `<status>`.
  - The batch output is the `all_agent_run_ends` dict of §7.1.
  - The existing rewrites and the link recovery of `apply_queue_completion`
    keep working as today; its stamping and `AgentStoppedUpdate` move into
    the generic step of §6.3. The guard in
    `create_agent_link_from_tool_result` that refuses to flip the spawn link
    on a `SendMessage` notification (`compute_base.py:1983-2019`) is
    unchanged.

### 6.2 Codex

- **Creation, at the `SubAgentActivity` `interacted` / `interrupted` event.**
  A new parser next to `_parse_sub_agent_activity_started` decodes those
  kinds and `completed`.
  - Batch: a new provider hook, the batch twin of the live one below:
    `collect_agent_run_signals(session_id, item, parsed, batch_state)`
    (default: no rows). The base batch loop calls it for **every** item,
    after that line's `analyze_content` (`compute_base.py:2591-2593`),
    tool-result handling (`:2690-2734`) and agent-link block
    (`:2735-2745`) — so, like the live hook, it sees the current line's
    result link and the agent link created from that line's result or ack
    (`create_agent_link_from_tool_result` live, the `agent_info` block in
    batch); neither hook needs a link created from a `tool_use` on the same
    line (live creates that one after the hook, `compute_base.py:3770`) —
    and adds the rows it returns
    (created interactions, `opens_run` updates, `AgentRunEnd` rows) to
    `all_agent_interactions` / `all_agent_run_ends` (§7.1) before the next
    line. `batch_state` is a read-only `BatchAgentState` NamedTuple over
    the loop's dicts **as built so far** (earlier lines, plus the current
    line's tool-result link, written before the hook runs,
    `compute_base.py:2716-2722`; `remap_tool_result_id` sees earlier lines
    only, since it runs before that link is written):
    `tool_use_map`, `all_tool_result_links` (`:2458`), `all_agent_links`
    (`:2459`), `all_agent_interactions`, `all_agent_run_ends`. Two Codex
    inputs are file facts, not rows, and live in the Codex compute's
    per-session state (set up in `begin_session_compute`,
    `codex/compute.py:2245`, cleared in `end_session_compute`, `:2260`,
    like `_agent_id_map`, `:2218`), filled by the hook as the loop runs:
    a `turn_id → task_started line` map (for the owner-abort cut, §5.2)
    and the line-1 `session_meta` fork fields `forked_from_id` /
    `subagent_history_start_ordinal` (for the copied-history gate, §5.2;
    each line's own `ordinal` comes from `parsed`). Live reads the same
    facts from `SessionItem` rows (§5.2). So every
    batch decision of this design — `opens_run`, the `completed`
    attribution, the owner-abort cut, the child turn end, and Claude's
    interactions, first-result `opens_run`, notifications and interrupt
    marker (§6.1) — reads the same evidence, in the same line order, as
    the live hook reads from the DB (except the never-observed spawn-call
    → `started` gap, §10). `analyze_content` and `ContentAnalysis`
    are unchanged; the Codex `SubAgentActivity` branch
    (`codex/compute.py:4954-4985`) keeps only its current `started` work.
  - Live: the base live second pass (`compute_base.py:3700-3775`) calls a new
    provider hook, `apply_agent_run_signals(session_id, item, parsed)`, for
    **every** item (not only `is_tool_result_item` lines, which exclude these
    events). Claude implements the same hook for its interactions and
    `AgentRunEnd` rows. For each line it runs **after** that line's
    `create_tool_result_link_live` (`compute_base.py:3733-3746`), the same
    position as the batch hook. The hook writes its rows **immediately**,
    line by line, like `create_tool_result_link_live`, so a later line of the same
    batch sees them; it returns the updates for the broadcast.
- **Kind:** from the qualified name of the function call whose `call_id` is
  the event `id`: `collaboration__followup_task` → `resume`,
  `collaboration__send_message` → `message`, `collaboration__interrupt_agent`
  → `stop`; **any other qualified name** (v1 `send_input` / `resume_agent` /
  `close_agent`, `collaboration__wait_agent`, …) → no row, the same in live
  and batch (batch drops `wait_agent` from `tool_use_map`, live reads
  `SessionItem` rows and could find it). Batch: `tool_use_map`. Live: a new helper next to
  `_lookup_tool_call_payload` (`codex/compute.py:2742-2770`) that returns
  the payload **and** the call's `line_num`; `_lookup_tool_call_payload`
  keeps its signature (5 callers use its dict result). No call → no row.
- **Target:** `agent_thread_id`.
- **`opens_run` for `resume`:** true when the agent has no **file-open run**
  (§5.6) at the event line. Batch: from `batch_state` (the loop's dicts
  built so far). Live: from the
  DB rows of the same session whose stored lines (`event_line_num`, result
  and `AgentRunEnd` lines) are below the event line (earlier lines of the
  same file are already applied). Never from `agent_run_states`, which also
  applies the root cutoff and other files' evidence.
- **End signals:**
  - `completed` → an `AgentRunEnd` on the call chosen by §5.6 (batch:
    returned by `collect_agent_run_signals`; live: written by
    `apply_agent_run_signals`). `is_tool_result_item`
    (`codex/compute.py:3971`, check at `:4026-4028`) does **not** accept it;
    it creates no
    `ToolResultLink`.
  - `FINAL_ANSWER` → a `ToolResultLink`, as today, but rebound by §5.6:
    batch in `remap_tool_result_id` / `_resolve_tool_result_id`
    (`codex/compute.py:2384-2448`), live in `_resolve_tool_result_id_live`
    (replacing `_lookup_spawn_call_id_for_agent_path`). Only the
    `FINAL_ANSWER` (v2) branch changes. The `_agent_id_map` lookup stays for
    the v1 `<subagent_notification>` (the map is also filled for v1,
    `:5238`, and both share the branch at `:2441-2448`, which is split); the
    live v1 path (`:2662-2669`) is untouched.
  - So a spawn or run card keeps one ack row plus one `FINAL_ANSWER` row
    per run it ended (the extra-signal cases of §5.6 add more), and
    `completed` never adds a row; `transformDisplayResult` is unchanged. The card's
    fetch, polling and short-display rules change for every agent card,
    spawn cards included (§8.3).
- **Child turn end:** each `task_complete` in a subagent's own rollout
  (except the copied-history lines of a forked child, §5.2) writes
  an agent-level `AgentRunEnd` (§5.2), live through `apply_agent_run_signals`
  on the child's file, batch in the child's own compute. It closes the runs
  started before it (rule 5 of §5.4). The old gates that trusted
  `last_stopped_at` — `subagent_idle_trusted` (backend) and
  `agentRunEndsOnSubagentIdle` (frontend) — are removed (§8.1).
- **A `followup_task` from a session that did not spawn the agent** (0 cases
  observed): it opens a run owned by that session; if its end signals land in
  another file, rules 2, 3 and 5 of §5.4 still close it (rule 5 through the
  child's own turn end). Accepted.

### 6.3 Stop check and state broadcast (both providers)

This step runs inside the live sync, under the DB write lock (the whole
`sync_and_broadcast` runs under `run_under_db_write_lock`,
`sessions_watcher.py:963-965`). It runs **once per batch, after** the synced
session's in-memory `session.save` (`compute_base.py:3960`), so that save
cannot overwrite the stamp when the synced file is the stopped child itself
(the rule-5 case). Today's check runs per result line; the per-batch step
replaces it. The broadcasts are sent after the transaction, still under the
lock, inside the existing broadcast block (gated by `new_line_nums` and
`user_message_count > 0 or is_subagent`), like `agent_stopped` today.

After each live batch, the sync collects the **affected agents**: agents
whose link was created, whose interaction was created or changed, whose run
or stop call got a result, or whose `AgentRunEnd` was written (a child file's
turn end makes the child itself affected). It also collects the **closing
evidence** of the batch: each new result that makes a run reach its required
count (distinct `tool_result_at`, rule 1 — a compaction copy of an existing
result never counts), each stop interaction that became a stop record in
this batch (row created, or first non-error result written — the same set
as in `exclude`, §5.4), each new `AgentRunEnd` (call-level or agent-level). The batch **closes a run** when
that run is open if evaluated **without all of this batch's evidence** and
closed after the batch (so two pieces of evidence closing the same run in
one batch — a Claude notification line that is both the second result and
an `AgentRunEnd`, a Codex `completed` + `FINAL_ANSWER` — still close it
once), or when the run was **created already closed** in this batch (e.g. a
subagent's `SendMessage` whose root-file `AgentRunEnd` was applied before
the caller's file synced). A run already closed before the batch is never
closed again (a late `FINAL_ANSWER` after rule 5: no re-stamp, no second
`agent_stopped`). A run that first passes the tree rule in this batch (its
target had no in-tree link before and gets its first one now, §5.4
`exclude`), and an interaction whose `opens_run` turned true
in this batch, count as created in this batch. For the
affected agents the step calls `agent_run_states` (§5.4) **twice**: once
with `exclude` = this batch's collected rows (§5.4: the state before it;
runs created in the batch are absent, its evidence and new stop records
are ignored) and once in full (the state after).
A run is closed by the batch when it is open (or absent) in the first
result and closed in the second. Then:

(The Stop-button handler runs the same step for its one agent, with the
`ui_stopped` row as closing evidence, under the same DB write lock, outside
any file sync. The step's output is sent by one **shared broadcast helper**,
extracted from the watcher's block (`providers/sessions_watcher.py:870-889`),
called by the watcher inside its broadcast block and by the handler, both
under the lock, after the transaction: `agent_run_state`, the child
`session_updated` and `agent_stopped` when stamped, and — watcher path
only — the `_after_agents_stopped` hook under the step-2 condition (the
handler skips it, §5.2).)

1. broadcasts `agent_run_state` (§7.3) for each affected agent that is
   `known` (has a run, so passes the tree rule; shell and Monitor
   `AgentRunEnd` rows never produce a broadcast) — always, even when nothing
   is stamped;
2. for each agent for which this batch **closes a run** (definition above)
   **and** that is not running after it: stamps `last_stopped_at` through
   the existing monotonic guard (`.exclude(last_updated_at__gt=stop_time)`,
   stop time = the max of the `closed_at` of the runs this batch closed,
   from the second call — each run's earliest evidence, §5.4) and returns
   an `AgentStoppedUpdate` carrying a new `stamped`
   flag (true when the guarded update changed the row). The broadcast
   sends the child `session_updated` and `agent_stopped` **only when
   `stamped`** (nothing changed otherwise), and fires
   `_after_agents_stopped` **always** — even when the guard refused the
   stamp (`providers/sessions_watcher.py:339-354`, `:870-889`; today it
   fires only for stamped agents, since `check_agent_naturally_stopped`
   returns `None` when the guarded update changes nothing,
   `compute_base.py:1930-1943`).
   When the stop time is null (every run this batch closed has a null
   `closed_at`, i.e. its closing evidence has a null timestamp; a batch
   that closes a run with no new evidence of its own — a late tree rule, a
   run created already closed — uses that run's `closed_at` like any
   other): no stamp (as
   `check_agent_naturally_stopped` does today, `compute_base.py:1917-1919`);
   the step still returns an `AgentStoppedUpdate` with `stopped_at = None`
   and `stamped = False`, so the one carrier (`agent_stopped_updates`, from
   which the hook's id list is built, `providers/sessions_watcher.py:885-889`)
   still fires `_after_agents_stopped` for that agent — the hook fires for
   every agent for which this batch closes a run and that is no longer running, whatever
   the stamp or timestamp, so a Codex root never keeps it in
   `_live_subagents` — while `session_updated` / `agent_stopped` are
   skipped (`stamped` false); the `agent_run_state` broadcast of step 1 is
   still sent. The Stop-button path uses the same carrier through the
   shared helper, minus the hook (§5.2).
   An owner turn abort (§5.2) is closing evidence like the others: its
   children leave the root's `_live_subagents` through the stop hook. The
   order between the root's own turn end (SDK `turn/completed`) and the
   watcher reading the `turn_aborted` line depends on watcher latency; both
   orders are handled. If the turn end comes first, a hold may arm for a
   moment (`_try_arm_subagent_hold`, called at `codex/agent/agent.py:1002`,
   defined at `:1305`) and the
   stop hook then releases it (`notify_subagents_stopped`); otherwise the
   children are already gone and no hold arms.

The stop and resume hooks receive the **tree root's** id
(`session.parent_session_id or session.id`), not the synced file's id. The
Codex manager looks the live agent up by that id
(`codex/agent/manager.py:453-454`), and only the root has a live process. This
matters for rule 5: its evidence always comes from the **child's** file, and
with the synced file's id the root's `_live_subagents`, its subagent hold
(`_try_arm_subagent_hold` / `in_subagent_hold`, `codex/agent/agent.py:1305-1356`,
released by `notify_subagents_stopped`, `:1358-1410`) and `background_work_in_progress`
(fed by `current_background_work()`, `:1200-1205`) would stay stale in the
idle-parent case rule 5 exists for.
The root agent ignores agent ids it does not track.

`check_agent_naturally_stopped` and the stop part of `apply_queue_completion`
become this generic step.

The batch pass keeps its current `last_stopped_at` stamping unchanged: from
its own in-pass spawn runs (`compute_base.py:2861-2873`) and from the tree's
queue completions (`:2816-2824`), applied with the existing guard
(`:3324-3337`), so a batch stamp never moves a newer live value back. It adds
no stamping for interaction runs (the kept queue-completion path already
stamps every in-tree child's completion, a `SendMessage` resume's included).
This stamp is a display value only; no running decision reads it.

### 6.4 Live parent processes

- **Claude:** no change (§4.1).
- **Codex (watcher-backed sessions):**
  - The live stream cannot tell `followup_task` from `send_message` (§4.2).
    `sessions_watcher` gains an
    `_after_agents_resumed(session_id, agents: list[tuple[agent_id, agent_path]])`
    hook, fired when an interaction is created with `opens_run = true`,
    mirroring `_after_agents_stopped`. The Codex override (same pattern as
    `codex/sessions_watcher.py:210-228`) calls a new
    `notify_subagents_resumed` on the manager.
  - `notify_subagents_resumed` re-checks `agent_run_states` and re-adds only
    the children still running **and** tracked by the root stream today:
    first-level children, whose spawn link is owned by the root (the root's
    SDK stream carries no nested `agentPath` item, so a nested agent was
    never in `_live_subagents` and is not added). The hook passes
    `(agent_id, agent_path)` pairs; `agent_path` comes from the `interacted`
    event that created the interaction. Resume hooks are fired before stop
    hooks in the same batch. Both relays are fire-and-forget tasks
    (`codex/sessions_watcher.py:221-228`), so across batches they can run
    out of order. The root agent serializes `notify_subagents_resumed`,
    `notify_subagents_stopped` and the prune (`_prune_finished_subagents`,
    `codex/agent/agent.py:1278-1303`) through one per-agent
    `asyncio.Lock` (not re-entrant; none of the three calls another — the
    prune is called by `wait` and the hold arm, `:1227`, `:1320`). For a
    **watcher-backed** agent (not `ephemeral`), each
    of them reads `agent_run_states` **inside** that lock and acts on the
    state it reads, never on the relay's payload alone: the resume relay
    adds only children that are running (and, when it adds one, calls
    `_schedule_background_work_refresh()` and refreshes the status label
    whenever `_subagent_wait_label_active` or `_subagent_hold_active` —
    the in-turn `wait_agent` label and the hold label, as
    `notify_subagents_stopped` does, `codex/agent/agent.py:1374-1381`,
    `:1397-1400`), the stop relay and the prune pop
    only children that are `known` and not running. (An **ephemeral**
    agent has no watcher rows, so `agent_run_states` knows none of its
    children: its `notify_subagents_stopped` pops the ids
    `_watch_ephemeral_subagents` (`:1266-1276`) gives it — the children
    `thread_read` reports idle — and its prune pops the children its own
    `thread_read` reports idle (`:1287-1291`), both as today; neither reads
    the DB; they take the same lock. This
    keeps the only hold-release path of an ephemeral agent working.) Each
    read happens after the commit of the batch that
    created the relay, so whatever order the tasks run in, the last one
    to take the lock applies the latest committed state: a resume and its
    end (in one batch or in two), or an old stop relay running after a
    newer resume, never leave a stale child nor drop a running one.
  - `_stopped_subagent_ids` becomes "children that are **known** and not
    running" per `agent_run_states` (`known` and not `running`). A child
    with no run in the DB yet (spawned, link not yet written by the watcher)
    is kept, as today's `last_stopped_at IS NOT NULL` query keeps it. A
    resumed child is not pruned while its run is open.
  - `_note_sub_agent_activity` keeps its current behaviour: `started` adds,
    `interrupted` / `completed` pop (`codex/agent/agent.py:1047-1068`),
    `interacted` changes nothing (watcher-backed sessions).
  - The stale claim "`completed` never reaches the parent stream" is
    corrected everywhere it appears: `_note_sub_agent_activity`,
    `_stopped_subagent_ids` (`:350-367`), `notify_subagents_stopped`,
    `codex/agent/manager.py:447-450`, `codex/sessions_watcher.py:213-219`,
    `providers/sessions_watcher.py:348-351`, and the `_live_subagents`
    comment (`codex/agent/agent.py:486-489`).
  - The subagent hold (`_try_arm_subagent_hold`, `codex/agent/agent.py:1305`)
    is not re-armed after the parent's turn has ended; the child then counts
    in `background_work_in_progress.subagents` only.
- **Codex ephemeral sessions** (no watcher rows): only when the agent is
  `ephemeral`, `_note_sub_agent_activity` re-adds the child on `interacted`;
  the existing `thread_read` paths (`codex/agent/agent.py:1256-1276`: the
  watch loop's `notify_subagents_stopped` call and the prune) remove it
  when its thread is idle, without any `agent_run_states` read (above).
  No watcher relay ever fires for an ephemeral agent (no rows).

## 7. Backend data flow

### 7.1 Compute and apply

- Live: the rows themselves are written immediately by the hook (§6.2).
  Three new lists carry what the watcher broadcasts, built like
  `agent_link_updates` (`compute_base.py:3483`) and **appended at the end**
  of the live pass's result tuple, so the existing positional indexes
  (`result[2]`, `result[5]`, … in the tests) do not shift:
  - `agent_interaction_updates`: the `agent_interaction` payloads of §7.3
    (created rows, `opens_run` changes);
  - `agent_run_state_updates`: the `agent_run_state` payloads computed by
    the stop step of §6.3 for the `known` affected agents;
  - `agents_resumed`: `(agent_id, agent_path)` pairs for
    `_after_agents_resumed` (§6.4; Codex only, empty for Claude).

  `AgentRunEnd` rows need no list of their own: they reach the frontend
  only through `agent_run_state`. The watcher's unpacking of that tuple
  into named variables (`providers/sessions_watcher.py:684`) gains the
  three new names. **Every** return of `sync_session_items_from_file`
  returns the extended tuple, the four early returns included
  (`compute_base.py:3390`, `:3399`, `:3425`, `:3434`, each today
  `[], [], [], [], [], [], False`), which gain three trailing empty lists —
  otherwise the watcher's unpacking fails on a "no new content" or "file
  missing" sync.
- Batch: `all_agent_interactions` and `all_agent_run_ends`, diffed into
  `…_to_create / _update / _delete` message keys and applied next to the
  agent-link keys (`compute_base.py:3171-3205`). The run-end diff covers
  `source = "transcript"` rows only.
- Compute versions: bump `CLAUDE_CODE_COMPUTE_VERSION` (`settings.py:412`)
  and `CODEX_COMPUTE_VERSION`, so the background compute rebuilds history.
- Codex history replacement: `_begin_replace_codex_history`
  (`providers/codex/rollout_migration.py:349-353`) deletes the session's
  `ToolResultLink`, `AgentLink` and `SessionItem` rows before a re-ingest. It
  also deletes the session's `AgentInteraction` and `transcript`
  `AgentRunEnd` rows (a Codex session never has `ui` rows: Codex has no
  Stop button, `codex/helpers.js:197`);
  otherwise stale rows keep old line numbers (first line wins) and keep
  closing runs through rule 4.
- Migration: `CreateModel` for `AgentInteraction` and `AgentRunEnd`, and
  `AddIndex` for `AgentLink(agent_id)` and `ToolResultLink(session,
  tool_use_id)`. No data migration.

### 7.2 Snapshot `/subagents/`

`build_subagents_state` / `serialize_agent_links` keep the array shape (one
entry per spawn link). Each entry:

- `running`, `run_started_at`, `run_background`, `runs` (each run's
  `{owner_session_id, tool_use_id, started_at, open, closed_at}`): from
  `agent_run_states` (per agent). `trust_agent_stopped` goes away.
- `stopped_at`: `agent_run_states`' `stopped_at` (the max over runs of each
  run's close time, §5.4; display: the tree's "finished" time). It uses the
  same evidence as the live stamp of §6.3, so a reload shows the same value
  as live **once the agent is not running** (while it runs, the snapshot
  may carry the close time of an earlier run that live never sent; the tree
  shows "working" then, not this time) — except: for runs closed only by
  the root cutoff (rule 3), which live stamps only when the batch creates
  them (or they first pass the tree rule in it, or their `opens_run` turns
  true in it, §6.3), at the cutoff time,
  and never when the cutoff moves; when the files sync out of order (e.g.
  the root's `FINAL_ANSWER` closes a run live before the child's earlier
  rule-5 row syncs: live keeps the later stamp, a reload shows the earlier
  close time); when the monotonic guard refused the live stamp (§6.3
  step 2: no `agent_stopped`, the live display keeps its older time); and
  when a Stop-button `ui` row is processed before the `killed`
  notification of an **earlier, different** stop (e.g. a model `TaskStop`
  whose notification syncs after the `ui` row; the notification of the
  Stop button's own kill always comes after its `ended_at`, read before
  the request): live keeps the handler's `ended_at`, a reload shows the
  earlier notification time, the earliest evidence.
  Display only; the running state is the same.
- New `interactions`: every `AgentInteraction` targeting that agent that
  passes the tree rule (target link **and** owner in the tree, §5.1:
  owned by the root or by a subagent of the tree), as
  `{owner_session_id, tool_use_id, tool_use_line_num, kind, opens_run, started_at}`.
- The frozen-share filter (`frozen_at_line`) applies to interactions exactly
  as it does to links (root-owned: call line ≤ frozen line; owned by a
  visible agent: kept); a kept root-owned interaction whose `opens_run` was
  decided after the frozen line is listed with `opens_run` false (§5.4),
  and `agent_run_states` only reads evidence up to the frozen line for the
  root. A root `ui` row has no line: it is kept when its
  `ended_at` is at or before the freeze time = the newest non-null item
  `timestamp` at or before the frozen line; dropped otherwise, and all `ui`
  rows are dropped when no such timestamp exists.
- The **share** snapshot also drops the interactions whose call item's
  display level is above the share's display ceiling, with the same check
  the live
  `share_agent_interaction` relay applies (§7.3), so a live viewer and a
  reloaded one see the same control-card widgets.

### 7.3 WebSocket

- `agent_interaction`, on creation and on an `opens_run` change, when the tree
  rule passes:
  `{root_session_id, owner_session_id, agent_session_id, tool_use_id, tool_use_line_num, kind, opens_run, started_at, project_id}`.
- **Late tree rule:** when the watcher broadcasts `agent_link_created` for an
  agent, it then (after it, in this order) broadcasts `agent_interaction` for
  every existing interaction **targeting** that agent that now passes the
  tree rule (owner is the root or a session whose `parent_session_id` is
  the root; indexed query on `agent_id`), because those rows failed the
  tree rule until now, and for
  every existing interaction **owned** by that agent that passes the tree
  rule (query on `session_id`),
  because the share relay dropped them while their owner was not yet in
  `descendant_ids`. The order matters for the share relay:
  `agent_link_created` adds the agent to `descendant_ids`, which
  `share_agent_interaction` requires for both the target and the owner.
- `agent_run_state`, per affected agent (§6.3):
  `{root_session_id, agent_session_id, running, run_started_at, run_background, runs, project_id}`
  (`runs` as in the snapshot, §7.2).
- `agent_link_created`, `agent_stopped` and `tool_state` are unchanged.
- Share relay (`share/consumer.py`). Today: `agent_link_created` is relayed
  when `include_subagents` is on and the root matches, and it **adds** the
  agent to `descendant_ids` (`:179-199`); `agent_stopped` checks the root only
  (`:201-209`); a child `session_updated` becomes `share_agent_idle` for a
  known descendant (`:211-222`). New:
  - `share_agent_run_state`: `include_subagents` on and root match, like
    `share_agent_stopped`.
  - `share_agent_interaction`: `include_subagents` on, root match, the target
    agent in `descendant_ids`, the owner is the shared session or in
    `descendant_ids`, and the call passes the display ceiling for **any**
    owner, as `tool_state` does. The check reads the display level of the
    item at the payload's `(owner_session_id, tool_use_line_num)` through a
    new helper; `_tool_use_visible` (`:111-129`) cannot be reused, because it
    finds the line through a `ToolResultLink` and returns false before the
    call has a result. `_load_descendants` (`share/consumer.py:90-101`,
    called at connect and on `share_updated`, `:247`) also adds the tree's
    link agent ids (`tree_agent_links(root)`), not only `Session` rows: an
    agent whose link synced before its child `Session` row existed (e.g. a
    Codex `started` event) would otherwise never enter the set for a
    viewer that connects in that window (the late-tree-rule rebroadcast
    fired before it connected), and its interactions would be dropped.
  - `share_agent_idle` stays as is (display only).
  - Callbacks `onAgentInteraction` / `onAgentRunState` in
    `share-session/shims/shareLive.js`, wired in `ShareSessionApp.vue`.
  - **Share reconnect: unchanged, out of scope.** Today the viewer
    re-fetches nothing when its socket reopens: items, links and tool
    states are fetched only at mount (`ShareSessionApp.vue:89-98`,
    `ShareItemsList.vue:43`), and the consumer sends no `share_meta` on
    connect (`share/consumer.py:32-63`). This design keeps that: the run
    states, interactions, result counts and cutoff missed during an outage
    come back at the next page load (§10).
  - **`include_subagents` in the share viewer.** The shim exposes
    `runStatesAvailable` = `include_subagents` was on at setup **and** has
    stayed on since: once a live `share_meta` turns it off, it stays false
    until the page reloads (like `openSubagent`, provided only at setup,
    `ShareSessionApp.vue:90-91`). The app store's `runStatesAvailable` is
    always true. While it is false, the cards and the shim read no run
    state: `isAgentRunning` is false, `ownRunOpen` is false,
    `spawnAwaitingRun` is false, and the `pendingCall` owner gate counts
    every subagent owner as not running, with or without an
    `agentRunStates` entry (§8.3). (The spawn-pending spinner is
    not gated on `runStatesAvailable` itself; it reads run state only
    through `pendingCall` while `count` is 0, §8.3: a **root-owned** spawn
    keeps today's spinner, which ends when the root's `tool_state` reaches
    the expected count; a **subagent-owned** spawn with `count` 0 stops,
    as its owner counts as not running.) This is needed because the
    relay then stops sending `share_agent_run_state`, so stored entries
    would freeze (a stale robot, open-run or awaiting-run polling with no
    end; deleting them instead would make `spawnAwaitingRun` true
    forever). When a live `share_meta` turns it off, the shim also bumps
    the root's fetch generation, so a setup snapshot still in flight is
    discarded. Accepted consequence: in such a viewer, an acknowledged
    spawn card short of its display count shows "No result available" (no
    run state says it still runs) until its next row lands, which the
    `count` watcher fetches — and, for a root-owned spawn with no link
    (e.g. `include_subagents` off from setup), at the same time as its
    header spawn-pending spinner, which keeps today's behaviour (no link,
    `count > 0`, no error, the helper still short of its expected count);
    both show together for the whole run, accepted.

## 8. Frontend

The frontend no longer derives an agent's running state from tool counts,
stop times or idle times. It stores the backend's per-agent state and applies
only the root cutoff (moved by the root's own `session_updated` in the app,
by `share_meta` in the share viewer, `ShareSessionApp.vue:123-129`).

### 8.1 Current consumers of the stop state, and their new rule

| Consumer | Today | New rule |
|---|---|---|
| `utils/agentLinkIndex.js` `setAgentLink` (`:16`, `:19-21`, `:28-30`) | merges `stoppedAt`, carries / forces `running` | keeps `stoppedAt` as a display time; no `running` field |
| `agentLinkIndex.js` `markAgentStopped` (`:46-55`) | `running = false` for good | records the display stop time only |
| `agentLinkIndex.js` `markAgentIdle` (`:56-65`) | sets `agentStoppedAt`, clears `running` | sets `agentStoppedAt` only (display) |
| `agentLinkIndex.js` `applyAgentSnapshot` (`:69-100`) | copies per-link `running` | also fills `agentRunStates` and `agentInteractions` (§8.2) |
| `agentLinkIndex.js` `handleAgentEvent` (`:152-164`) | creates the synthetic state unless `stoppedAt` or `running === false` | link identity only; no synthetic decision |
| `agentLinkIndex.js` `staleSyntheticAgentIds` (`:112-116`) | walks linked agents | walks the root's `agentRunStates` entries |
| `stores/data.js` `markAgentStopped` (`:4387-4390`) | always removes the synthetic state | display stop time only |
| `data.js` `setSyntheticProcessState` (`:4508-4545`) | refuses on `stoppedAt` / reported idle / cutoff | only writes; called by `applyAgentRunState` |
| `data.js` `updateSession` (`:1578-1600`) | removes the child's synthetic state on `last_stopped_at` | `markAgentIdle` (display) only; the root branch keeps `_cleanStaleChildSynthetics` and, when the root's cutoff changes, calls `applyAgentRunState` for every `agentRunStates` entry of that root (the "root cutoff change" trigger of §8.2) **after** the `$patch` that stores the new session (`applyAgentRunState` reads the cutoff from `sessions[root]`; called next to `_cleanStaleChildSynthetics`, `:1586-1589`, which runs before the patch, it would read the old cutoff and re-set a state `_clean` just removed) |
| `data.js` `fetchSubagentsState` (`:4603-4631`) | sets/removes synthetic states per link; removes the synthetic state of agents gone from the snapshot (`:4609-4613`) | after a response that applied, `applyAgentRunState` for **every** `agentRunStates` entry of that root (not only the agents the snapshot listed: an entry kept because its live stamp beat the token is re-applied too), as the failure path does (§8.2); an `agentRunStates` entry deleted by the snapshot (§8.2) also has its synthetic state removed, so no phantom "working" indicator is left; a response discarded by a generation change is re-issued once (§8.2 "Reconnect") |
| `data.js` `setActiveProcesses` (`:4839-4868`) | `processStates = {}` on every (re)connect, dropping synthetic states | at its end, `applyAgentRunState` for every `agentRunStates` entry, so synthetic states survive a reconnect and an `active_processes` that lands after the snapshot |
| `data.js` `unloadSession` (`:2966-2993`) | calls `clearAgentLinks`, removes the session's own synthetic state and those of its subagents | also drops the `agentRunStates` and `agentInteractions` whose `rootSessionId` is the unloaded session (both live as long as their root; the snapshot is fetched only for a root — its load, `SessionItemsList.vue:713`, the Orchestration tab's refresh, `OrchestrationPanel.vue:214`, and `refreshSessionToolStates`, `data.js:4659`, on the compute flip and on reconnect — never when an agent's transcript loads), and removes the synthetic state of every dropped run-state entry (`removeSyntheticProcessState`), since the existing loop (`:2987-2992`) only reaches agents whose session row is loaded; when the unloaded session is itself an agent with an `agentRunStates` entry (reached through `removeSession`, `:1639`, or reconciliation, `useReconciliation.js:136`), calls `applyAgentRunState` for it again after the cleanup, so its cards keep the running robot and Stop button; and, when that agent's root still has its items loaded, re-fetches the root's snapshot (`fetchSubagentsState`), because `clearAgentLinks(agent)` dropped the links the agent owns (its nested spawns) and nothing else brings them back when its transcript loads again |
| `data.js` `clearAgentLinks` / `agentLinkIndex.js` `clearAgentLinks` (`:36-45`) | drops the owner's links, bumps the fetch generations | unchanged. `agentInteractions` and `agentRunStates` are not touched here: they live as long as their root, and an agent's transcript reload fetches no snapshot by itself (the root snapshot is re-fetched by `unloadSession(agent)` only when the root's items are loaded), so dropping them would turn its control cards back into generic cards whenever that re-fetch does not run; `unloadSession` of the root drops them |
| `composables/useWebSocket.js` dispatcher (`:1568-1571`) | `agent_link_created` / `agent_stopped` | adds `agent_interaction` and `agent_run_state` cases |
| `composables/useWebSocket.js` `tool_state` (`:1585-1597`) | stops the agent when the spawn reaches its count | tool state only; no agent decision |
| `ToolUseContent.vue` `agentReportedIdle` / `isAgentRunning` (`:845-862`) | card's own result count, `stoppedAt`, child idle | `isAgentRunning(agentId)` (§8.3) |
| `SessionHeader.vue` `canStopAgent` (`:255-266`) | synthetic state + `!link.stoppedAt` | `isAgentRunning(agentId)` + `runBackground` |
| `AgentTreeNode.vue` `finishedAt` (`:124`) | `stoppedAt ?? agentStoppedAt` | unchanged (display) |
| `providers/baseHelpers.js` `agentRunEndsOnSubagentIdle` (`:1502-1518`) and its Codex override (`codex/toolHelpers.js:1860-1870`) | idle gate | removed |
| `share-session/ShareSessionApp.vue` `onAgentIdle` / `onAgentStopped` (`:139-140`) | feed the shim's stop state | display only; new `onAgentInteraction` / `onAgentRunState` |
| `share-session/shims/dataStoreShim.js` `markAgentStopped` / `markAgentIdle` / link seeding (`:288-303`) | per-link `running` / `stoppedAt` | same changes as the store; `agentRunStates` from snapshot and `share_agent_run_state` |
| `providers/helpers.py:279-280` (`subagent_idle_trusted`) | backend idle gate | removed |
| `SessionHeader.vue:843-853` (turn duration from the synthetic `state_changed_at`) | spawn time | the current run's start (`runStartedAt`): intended |
| `data.js:3049-3051`, `:3166-3180` (`recomputeVisualItems` working placeholder of the subagent transcript) | reads the synthetic state | unchanged; follows the synthetic state |
| `ToolUseContent.vue:898-905` (`stoppingAgent` watch), `:994`, `:1006` (Stop button, tooltip) | `isAgentRunning` + `agentLink.isBackground` | `isAgentRunning(agentId)` + `runBackground` (§8.3) |
| `ShareSessionApp.vue:89-98` (setup snapshot fetch) | links only | also fills `agentRunStates` / `agentInteractions` |
| stale comments `data.js:4579-4581`, `data.js:4650-4652`, `SessionItemsList.vue:707`, `codex/toolHelpers.js:984-992`, `agentLinkIndex.js:17-18`, `useWebSocket.js:1589` | describe the old rule | updated; and `ToolUseContent.vue:825-830` (the View Agent link comment) names control cards and the interaction fallback for `agentId` |

Note: `updateSession` calls `markAgentIdle` only when the child is new to
the store or its `last_stopped_at` changes (`data.js:1582`), not on every
`session_updated`.

Existing tests that encode the replaced behaviour and must be updated:
`frontend/src/utils/agentLinkIndex.test.js`,
`frontend/src/utils/nestedAgentComponents.test.js`,
`frontend/src/stores/nestedAgentState.test.js`,
`frontend/src/share-session/shims/shareLiveNested.test.js`,
`tests/test_subagents_tree_endpoint.py` (`:63-96`),
`tests/test_claude_subagent_lifecycle.py`,
`tests/test_codex_subagent_links.py`,
`tests/test_nested_agent_compute.py`: `:274-284` encodes the old queue-stop
rule; `:264-271` has no `AgentLink`, so it becomes vacuous, and a new test
**with** a link proves the §6.3 reversal (stop update even when the guard
refuses the stamp). Its positional indexes (`:79-87`, `:142-159`, `:261`,
`:301`, `:311`) do not shift: the new lists are appended at the end of the
tuple (§7.1),
`tests/test_share_nested_subagents.py:46-51` (its `queue()` helper,
`tests/test_subagents_tree_endpoint.py:43-47`, seeds only a
`queue-operation` item, but the snapshot now reads `AgentRunEnd`; seed an
`AgentRunEnd` at line 150 so the frozen-line check still runs),
`tests/test_codex_subagent_wait_label.py` (`:183-218` drives the real
`_stopped_subagent_ids` prune path, which §6.4 changes).
`frontend/src/utils/nestedAgentComponents.test.js` slices source on
`'const agentReportedIdle'` (`:14`, `:50`, `:72`), which §8.1 removes: those
tests are rewritten, not re-pointed. `nestedAgentState.test.js:6-11` and
`shareLiveNested.test.js:59` slice by marker strings too; their markers
follow the renamed code.

Existing tests expected to pass unchanged (check during implementation):
`tests/test_share_nested_subagents.py` (`:77-108` `agent_stopped` relay,
`:131-152` `share_agent_idle` relay),
`tests/test_nested_subagents_watcher.py` (`:16-57`).
`tests/test_codex_subagent_hold.py` must be **updated**: its tests at
`:129-205` (e.g. `test_last_child_releases_to_user_turn`, `:132`) call
`notify_subagents_stopped` on a non-ephemeral mocked agent with no DB
rows and expect the pop, while §6.4 makes a watcher-backed agent pop only
children `agent_run_states` reports `known` and not running, under the
per-agent lock; patch `agent_run_states` (and give the mock the lock).

### 8.2 Store

`agentLinkState()` gains:

- `agentInteractions[owner][toolUseId] = {agentId, rootSessionId, kind, opensRun, startedAt, toolUseLineNum, ownerSessionId, toolUseId}`
  (`rootSessionId` from the WS payload / the snapshot's root, used by the
  deletion rule below);
- `agentRunStates[agentId] = {running, runStartedAt, runBackground, rootSessionId, runs}`
  with `runs[owner:toolUseId] = {open, startedAt}` (per-run state, read by the cards,
  §8.3; the payload's `closed_at` is not stored, no card reads it), filled
  by the snapshot and by `agent_run_state` (both carry the root). The project is read from the root session in the store (the
  snapshot carries none). An entry may exist before the agent's link.
  Entries are stored for every `agent_run_state` / `agent_interaction`
  received, whatever the root's load state, exactly like links
  (`handleAgentEvent`, `agentLinkIndex.js:152-164`); a root that is never
  loaded keeps its entries for the tab's lifetime (accepted, as for
  links). No synthetic process state comes from them while the root's
  items are not loaded (`applyAgentRunState`'s gate, below), and the
  root's load replaces them with its snapshot.
- **Ordering:** two stamp maps, `agentRunRevisions[agentId]` (written only by
  `agent_run_state`) and `agentInteractionRevisions[owner:toolUseId]`
  (written only by `agent_interaction`). Like `agentRevisions`, they store a
  **stamp of the global counter** (`++state.agentRevision`), so they compare
  with the fetch token that `beginAgentFetch` records (`agentLinkIndex.js:66-68`).
  They are separate from `agentRevisions`, which `markAgentIdle` also stamps.
  - In `applyAgentSnapshot`, the run-state and interaction fill runs
    **before and independently of** the per-agent link skip (`:80`
    `continue`), so an idle or link event never suppresses it.
  - A live stamp newer than the token beats the snapshot; otherwise the
    snapshot wins.
  - Entries absent from a snapshot of their root (run states and
    interactions whose `rootSessionId` is that root) are deleted when their
    stamp is not newer than the token, like links (`:72-77`).
  - No "link exists" condition.
- **Reconnect:** `setActiveProcesses` re-applies the stored run states
  (§8.1); the existing `refreshAllLoadedToolStates` (`data.js:4675-4686`)
  re-fetches the snapshot of every loaded root, which replaces states missed
  during the outage, then bumps a new `connectionEpoch` counter (read by
  the cards, §8.3 rule 6). A snapshot response discarded because its fetch
  generation changed (any `clearAgentLinks` bumps every root,
  `agentLinkIndex.js:43-44`, checked at `:70`) makes `fetchSubagentsState`
  re-issue it once — only when the root's items are still loaded
  (`localState.sessions[root]?.itemsFetched`, read at the discard): a
  discard caused by
  `unloadSession(root)` itself must not refill the entries that unload
  just dropped. A failed snapshot (HTTP error, `data.js:4606`, or
  network error), or a second discard, calls `applyAgentRunState` for
  every `agentRunStates` entry of that root, so the stored states (from
  before the outage, or received while the root was not loaded) are
  applied as they are — the robot, the Stop button and the cards'
  `ownRunOpen` agree — until the next `agent_run_state` of that agent or
  the root's next load (§10).

Store action `applyAgentRunState(agentId)`: running when
`agentRunStates[agentId].running` and `runStartedAt` is not before the root
cutoff (a null `runStartedAt` counts as before the cutoff when the cutoff is
> 0, like the backend's rule 3). The cutoff is the frontend's existing `getSessionCutoffMs`
(`utils/sessions.js:12`; 0 when the root has no `last_started_at`, then no
frontend cutoff applies — the backend state already applied `Session.cutoff`,
`models.py:676-683`). It sets the synthetic process state with
`started_at = runStartedAt` — so `_cleanStaleChildSynthetics` compares the
newest run, and a resume after a root restart is not pruned as stale —
parent = the entry's `rootSessionId`, project = `sessions[root].project_id`
(passed as the `projectId` parameter of `setSyntheticProcessState`,
`data.js:4508`), provider from the root session (`:4520-4525`), or removes
it.
It acts only when the root's items are loaded — defined once, here and
everywhere this design says "items loaded" (§8.1, the re-issue rule above,
§10): `localState.sessions[root]?.itemsFetched`; otherwise it removes
any synthetic state, and the root's load fetches the snapshot, which
re-applies it (the Orchestration tab of a root whose items are not loaded
yet shows its agents stopped meanwhile, §10). It runs on `agent_run_state`, on the snapshot, on a root cutoff
change, at the end of `setActiveProcesses` (every entry), in
`unloadSession` (for an unloaded agent, §8.1), and when a root's snapshot
fetch fails or is discarded a second time (every entry of that root,
"Reconnect" above).

The synthetic process state stays the one "working" signal for subagent tabs
(`views/SessionView.vue:2317`), the Orchestration tree
(`AgentTreeNode.vue:55`) and its count (`OrchestrationPanel.vue:59-61`).

### 8.3 Cards

- New getter `isAgentRunning(agentId)`: main store = synthetic process state
  present; share shim = `agentRunStates` + cutoff (the shim's
  `getProcessState` always returns null, `dataStoreShim.js:70`), false
  while `runStatesAvailable` is false (§7.3).
- `ToolUseContent.vue` shows the agent widget when the card is a spawn card
  (`isTask`, unchanged) **or** `getAgentInteraction(sessionId, toolId)`
  returns an interaction (store and shim). New computed
  `isAgentCard = isTask || !!agentInteraction`.
- `agentId` (`:831-832`) = the spawn link's `agentId` ?? the interaction's
  `agentId`. `navigateToSubagent` (`:910`) and `handleStopAgent`
  (`:944-948`) use it, so they work on control cards.
- The generic spinner's `isToolRunning` (`:812-822`) starts with, in this
  order: `if (transcriptFrozen) return false` (as today, `:814`);
  `if (isTask) return false` (as today, `:815`);
  `if (agentInteraction) return count === 0 && pendingCall` (a control
  card spins only while its call is pending, with the same term as its
  Result section, so both stop together); then today's `isStaleToolUse` /
  helper checks for every other card.
- The "Agent running for …" tooltip (`:979-981`) uses the agent's
  `runStartedAt` instead of the card's `toolStartedAt`.
- **Expected result count** of a control card: 2 when its interaction has
  `opensRun` (ack + end signal), else 1 — for Codex `followup_task`
  (`codex/toolHelpers.js` `getExpectedResultCount`, `:956-1019`) and for
  Claude `SendMessage` (`claude_code/toolHelpers.js:562-568`, today 1, so a
  resumed run's second row would never be fetched live). It drives fetching
  only. The **display** count (`getRequiredResultCountForDisplay`,
  `claude_code/toolHelpers.js:570-573`, and its Codex twin, which today
  return the expected count) stays 1 for control cards, so the ack
  ("Resuming agent …") shows at once, as today, instead of being hidden
  behind "Result not yet available" for the whole run.
- **The card's own run.** A card whose call is a run (a spawn, or an
  interaction with `opensRun`) reads its run's state from
  `agentRunStates[agentId].runs[sessionId:toolId]` (§8.2). `ownRunOpen` =
  its `open` **and** its `startedAt` not before the root cutoff (null counts
  as before a cutoff > 0; the same
  frontend cutoff as `applyAgentRunState`, so a run cut by a root restart
  closes on the card without waiting for a new snapshot); for a card whose
  call is not a run, `ownRunOpen` = false. `ownRunOpen` is also false
  while `runStatesAvailable` is false (share viewer, §7.3), and when
  `transcriptFrozen` (a frozen share snapshot: the evidence after the frozen
  line is dropped, `share/session_views.py:120-130`, `:168-170`, `:225-226`,
  so a run open at the freeze would stay open forever). The cutoff is always taken from
  the root named by `agentRunStates[agentId].rootSessionId`, never from the
  card's own `rootSessionId` computed (`ToolUseContent.vue:99`, from the
  `parentSessionId` prop; in the share drawer that prop is the agent,
  `SharedSubagentView.vue:32`, whose cutoff is 0). The share shim
  computes it the same way.
- **Result fetch on an agent card.** Today the card fetches on open and
  every 3 s, and stops when the tool is no longer running and it holds the
  required rows (`ToolUseContent.vue:209-217`); nothing re-fetches results
  on `tool_state` (the handler, `useWebSocket.js:1585-1597`, updates the
  tool state and, today, stops the agent). A run can close (Codex
  `completed` / child turn end, rule 4/5) long before its `FINAL_ANSWER`
  row (p90 14 s, up to hours). New rules, all keyed on the card's **own**
  run, never on the agent:
  - **Why rows are never compared with `toolState.resultCount`.** That
    count covers every `ToolResultLink` row of the `tool_use_id`
    (`aggregate_tool_states`, `session_queries.py:62-89`, with
    `"result_count": Count("id")` in `TOOL_STATE_ANNOTATIONS`, `:23-28`),
    while the card's fetch returns only the rows of its own
    `tool_use_line_num` (`tool_results_payload`, `:91-117`). Compaction
    duplicates the `tool_use` line with its results (97 agent-card calls in
    8 sessions span several lines, e.g. `9de655b7`
    `toolu_01AyCifzctWnzBnDS5sTj9hQ`: `resultCount` 4, 2 rows per card), so
    such a card would never "catch up".
  - **The fetch pipeline of a card** (replaces the fetch/poll logic of
    `ToolUseContent.vue:156-345` for **every** card, because a card can turn
    into an agent card while it polls — e.g. a Codex `followup_task` whose
    `interacted` event arrives after the Result section opened; only the
    polling predicate differs by card type). Terms:
    - `rows` = rows the card holds; `count` = `toolState?.resultCount ?? 0`;
    - `fetchedAtCount` = the `count` read at Start by the last request that
      **settled** as current with a success, or with an error once
      `errorStreak` ≥ 3 (rule 3), same `?? 0` normalisation; `null` until
      then.
      `countChanged` = `fetchedAtCount !== null && count !== fetchedAtCount`
      ("a result arrived that no request has seen yet"); while it is null
      it is false, and the `'idle'` / `'loading'` state drives the first
      fetch;
    - `treeCutoff` = the cutoff of the tree root: the root named by
      `agentRunStates[agentId].rootSessionId`, else by
      `getAgentLinkInfo(props.sessionId)?.rootSessionId`, else: in the app,
      the card's `rootSessionId` computed (`props.parentSessionId ||
      props.sessionId`, `:99`, the root there); in the share viewer, the
      shared session id (`meta.session_id`, provided by
      `ShareSessionApp.vue` as a new `provide('sharedSessionId', …)` and
      injected by the card with a `null` default in the app; reached e.g.
      when a drawer opens from a `#agent=` hash before the snapshot lands)
      — never the card's `rootSessionId` computed in the
      share viewer, which is the agent itself in the share drawer
      (`SharedSubagentView.vue:32`);
    - `pendingCall` (agent cards) = `count === 0`, `!toolState?.error` (the
      call's own error; a **fetch** error never affects it), not
      `transcriptFrozen`, the call not before `treeCutoff`, and — in the
      share viewer while `runStatesAvailable` is false, **every** subagent
      owner (`props.parentSessionId` set), with or without an
      `agentRunStates` entry, counts as not running, so its calls are not
      pending; otherwise, when the call's owner is an agent with a run
      state (`props.parentSessionId` set **and** an
      `agentRunStates[props.sessionId]` entry; an owner that is never an
      agent run, e.g. a Claude workflow agent tab `<run_id>:<agent_id>`,
      `SessionView.vue:2366-2371`, has no entry and skips this gate) —
      that owner
      still running (`isAgentRunning(props.sessionId)`, store or shim) **and
      the call not before an open run of the owner** (some open run in
      `agentRunStates[props.sessionId].runs` has `startedAt` ≤
      `props.timestamp`; the owner can have two open runs): the call has no
      result yet and can still get one (a subagent stopped while blocked in
      `TaskOutput` / `SendMessage` leaves a call that never resolves while
      the root runs on, and a later resume of that subagent does not revive
      it). Times are compared as parsed milliseconds; a null
      `props.timestamp` never counts as "before" (as `isStaleAgentUse`,
      `:790-793`, does today); a null `startedAt` of an owner run passes
      that run's comparison; a 0 `treeCutoff` passes the cutoff comparison;
    - `needsMoreRows` (agent cards) = `countChanged` **or**
      (`count > 0` and `ownRunOpen` and `rows` < the expected count —
      with `count` 0 only `pendingCall` drives the card, so the Result
      text and the header spinner stop together) **or** `pendingCall`
      **or** `spawnAwaitingRun`: a spawn card with `count > 0` (with
      `count` 0, `pendingCall` and its owner gate drive the card, as for
      the own-run term) whose own run has **no entry
      yet** in `agentRunStates[agentId].runs` (no agent link yet counts as
      no entry), `!toolState?.error` (a spawn that failed, e.g. an
      `Unknown error` result on a Claude background `Agent`, will never
      get a link), `runStatesAvailable` (§7.3; always true in the app), not
      `transcriptFrozen`, the call not before `treeCutoff`,
      and the helper's `isToolRunning` true (the checks of today's
      `isAgentSpawnPending`, `:877-883`, with its stale check taken from
      `treeCutoff` instead of `isStaleAgentUse`, which reads the card's own
      `rootSessionId`, the agent itself in the share drawer). The ack's
      `tool_state` can land before the link, and the link
      (`agent_link_created`) lands before the run state (`agent_run_state`,
      sent later in the same broadcast block, §6.3), so both gaps keep the
      card polling, never "No result available" in between. The spinners
      use the same `treeCutoff`: the control-card spinner through
      `pendingCall`, the spawn-pending spinner through today's
      `isAgentSpawnPending` with the same stale-check change and, while
      `count` is 0, the `pendingCall` condition (so a spawn whose subagent
      owner stopped before the ack stops spinning with its Result
      section), and, once `count > 0`, `!toolState?.error` (a failed
      Claude background spawn — `Unknown error`, 1 of 2 results, no link
      ever — stops spinning with its Result section: the Claude helper
      ignores the error for this count, `claude_code/toolHelpers.js:575-587`);
      its "no agent link yet" condition is unchanged;
    - **polling predicate** = agent card: `needsMoreRows`; other card:
      `isToolRunning` **or** (`rows` < display count **and** not
      `lastSettleFailed` **and** not `isStaleToolUse`) **or** `countChanged` —
      today's test (`:209-217`, run only after a success), the count term
      (so a final row landing during a request, or while the section was
      closed, is still fetched), no retry of a finished tool after an error
      (today the `catch` stops polling, `:221-228`), and no endless polling
      of a tool cut by a restart with no result (stale, 0 rows: the
      endpoint returns an empty list, `session_queries.py:108-109`, so the
      card ends on "No result available"); both false when
      `transcriptFrozen`. Visible changes from today on non-agent cards
      (all intended):
      - a card whose tool still runs retries after a fetch error, every
        3 s, until the tool stops (today the error stops polling for good);
      - reopening the Result section, or reactivating the card with its
        section open, after an error retries once (rule 6; today
        `ensureResultFetched`, `:265-271`, never refetches an `'error'`
        card, and the reactivation resume, `:342-348`, runs only for a card
        that was polling);
      - a card whose last fetch failed, with its section open, retries once
        when the connection comes back (`connectionEpoch`, rule 6), and
        when its `count` changes (the `count` watcher; today the error
        stops all fetching, `:221-228`);
      - a failed refresh of a card that already shows rows keeps the rows
        (rule 3) instead of replacing them with the error text;
      - a hung request is stale-killed and retried (rule 4; today it stays
        in flight with no timeout, `:167-226`, and the card keeps "Loading
        result…");
      - a running tool whose section is reopened, or whose card is
        reactivated, with its display rows already complete polls again
        (today `ensureResultFetched`, `:265-266`, and the reactivation
        resume, `:345`, skip it when the rows are complete), so its
        progressive output keeps refreshing;
      - a tool "running" from before the session's cutoff stops polling,
        with or without rows (`isToolRunning` includes `isStaleToolUse`,
        and the rows term excludes it; today's `fetchResult` reads the
        helper directly, `:210-211`, and polls while rows are short);
      - a row that landed while the section was closed is fetched at
        reopen (the count term), even when the display rows were
        complete;
      - an open, active card fetches as soon as its `count` changes (the
        `count` watcher), so progressive output (a Codex `exec_command`
        chain, a Claude `Monitor`) refreshes on each new row, not only
        every 3 s (`:236-240`);
    - "open" = the Result section (or the inline result) is visible;
      "active" = `sessionActive` (Vue does not pause effects in a
      KeepAlive-deactivated card).

    Derived (computed, never stored — nothing to keep in sync):
    - `showsPending` = the polling predicate. It replaces `isPolling` in the
      template (`:1083-1088`, `:1108-1113`): "Result not yet available.
      Checking again shortly…" shows exactly while the predicate is true,
      and disappears the moment it turns false (owner stopped, run closed,
      cutoff, freeze) — together with the control-card header spinner,
      which reads `pendingCall` (below). (For a non-agent card still running
      when its Result section reopens, this text replaces today's "Loading
      result…"; both say the result is not there yet.)
    - `wantsFetch` = open **and** active **and** (the polling predicate
      **or** `resultState` is `'idle'` / `'loading'` **or**
      `refetchPending` **or** the slot is busy — so a one-off request, e.g.
      an Entry or `connectionEpoch` retry of a finished card, still gets
      its stale check). The only thing that runs the ticker.

    State: `resultState` (`'idle'` / `'loading'` / `'loaded'` / `'error'`),
    `resultData`, `resultError`, `fetchedAtCount`, one request **slot**
    (token + start time), `refetchPending`, the ticker, `staleKills` (the
    number of stale kills since the last Leave or success settle, 0 at
    mount), `errorStreak` (consecutive error settles since the last Leave,
    success settle or `connectionEpoch` retry, rule 6; 0 at mount),
    `lastSettleFailed` (true after an error settle, false after a success
    settle; **kept** across Leave, false at mount — the pipeline's "last
    fetch failed" test; `resultState === 'error'` is only its display
    form when the card shows no rows, rule 3). The old `isPolling`
    and `resultPollingPaused` flags are removed.

    Rules:
    1. **Single flight with tokens.** Every trigger (Entry, the `count`
       watcher, the `connectionEpoch` watcher — rule 6 — and the ticker,
       rule 4, which calls it only with a free slot, so a tick never sets
       `refetchPending`) calls
       `requestFetch()`: slot busy → `refetchPending = true`; slot free →
       start a request with a new token. A request whose token is no longer
       the slot's when it settles does **nothing** (no rows, no state, no
       slot, no `refetchPending`), so no older response overtakes a newer
       one.
    2. **Start.** `resultState = 'loading'` only if it is `'idle'` (never
       loaded). A card that loaded, or errored, keeps its display while it
       refreshes; `resultError` changes only at a settle.
    3. **Settle** (current token): success → rows, `'loaded'`,
       `resultError = null`, `fetchedAtCount` = the count read at Start,
       `errorStreak = 0`, `staleKills = 0`, `lastSettleFailed = false`;
       error → `resultError`, `lastSettleFailed = true`, `errorStreak += 1`,
       `resultState = 'error'` **unless the card shows rows** — i.e. it
       holds at least its display count, so `displayResult` is non-null —
       in which case it keeps `'loaded'` and its rows, so one failed
       refresh of a polling card does not flash rows → error → rows; a card
       holding fewer rows than its display count (e.g. a spawn card with its
       ack only) goes to `'error'`, never "No result available" while a
       fetch is failing), and `fetchedAtCount` = the count
       read at Start **only when `errorStreak` ≥ 3** (every error settle
       from the third in a row on records it). So a transient
       error on a count change (e.g. an HTTP 502 while the backend
       restarts, as the `FINAL_ANSWER` lands) keeps `countChanged` true and
       the ticker retries (every 3 s, up to 3 tries), while a persistent
       error stops the count term after the third try instead of retrying
       forever — also when the count changes again during a long error
       streak (the next error settle records the new count). A
       first fetch that errors leaves `fetchedAtCount` null
       (`countChanged` false), as today. Free the slot.
       Then, if `refetchPending` and `wantsFetch`: clear it and
       `requestFetch()`.
    4. **Ticker.** A 3 s interval runs **exactly while `wantsFetch`**: a
       `watch(wantsFetch, …, { immediate: true })` starts / clears it, so a
       card that mounts with its Result section already open (true from
       setup, never changing) still gets its ticker. Each tick: slot free →
       `requestFetch()` if the predicate is true or the state is still
       `'idle'` / `'loading'`; slot busy for longer than the **stale
       threshold** → a stale kill: invalidate its token (abort its signal on
       the SPA path; the share path, which has no signal, `:182-184`, just
       ignores the late settle), `staleKills += 1`, free the slot and
       `requestFetch()`. The threshold grows with `staleKills`: 30 s, then
       60 s, then 120 s; after 3 stale kills there is **no** threshold (the
       slot is not killed again until a success settle or a Leave resets
       `staleKills`; `fetchResult` has no timeout today, `:167-226`). So a
       hung request — even the very first one — never blocks the card for
       long, and a response that is always slow (large payload, slow
       tunnel, loaded backend) still lands: at most 3 requests are dropped
       between two success settles, and the share path leaves at most 3
       extra requests running on the server in that span. The one case
       left: 3 stale kills in a row with no success in between, then a 4th
       request that hangs for good — the card waits (its section shows
       its last display) until the next Leave (close / reopen, session
       switch). After an error, the ticker retries
       only while the predicate holds (agent card: a pending call, an open
       run short of its rows, a spawn awaiting its run, a count change;
       other card: a running tool, a count change); otherwise the error
       stays shown, as today.
    5. **Leave** — run by a watcher whenever `open && active` turns false
       (Result or card close, the section hidden by `showResultDetails`
       turning false, `:621-647`, deactivation), and at unmount: clear the
       ticker; invalidate the slot's token (and abort its signal on the SPA
       path, as `stopPolling` does today, `:247-252`), free it, clear
       `refetchPending`, reset `staleKills` and `errorStreak` to 0; if
       `resultState` is still
       `'loading'` (no request ever settled, whichever request set it —
       e.g. the first one, later stale-killed), restore `'idle'` (today
       `:223-225` leaves `'loading'` behind).
    6. **Entry** — the mirror of Leave: run by a watcher whenever
       `open && active` turns **true** (`immediate`, so a card that mounts
       open is covered), which replaces today's separate entry points
       (mount, card reopen, Result reopen — `ensureResultFetched`,
       `:151-152`, `:315-316`, `:278-280` — and reactivation, `:342-348`)
       and also covers a section shown again by `showResultDetails`:
       `requestFetch()` when `wantsFetch` **or** `lastSettleFailed`
       — a reopen after an
       error retries once; a card reopened or reactivated with its Result
       section closed fetches nothing (`onToolUseClose` closes the section,
       `:294-300`); the ticker only keeps retrying while the predicate
       holds. The `count` watcher: when `count` changes and the card is
       open and active, `requestFetch()`. The **connection epoch**
       watcher: the store keeps a `connectionEpoch` counter, bumped at the
       end of the reconnect refresh (`refreshAllLoadedToolStates`,
       `data.js:4675-4686`; the share viewer has no reconnect refresh,
       §7.3, so its epoch never moves); when it changes and the card is
       open and active
       with `lastSettleFailed`, reset `errorStreak` to 0 and
       `requestFetch()`. So an error streak that outlasts its 3 tries
       because the backend was down (a restart takes longer than ~6 s)
       retries once the connection is back, even when `count` did not
       change.
    7. **Termination.** The ticker stops as soon as `wantsFetch` is false:
       the predicate terms end (the `count` term after one success settle
       or an error settle with `errorStreak` ≥ 3,
       the own-run term when the backend closes the run or the cutoff /
       freeze, the pending term at the first result or when the call can no
       longer resolve, the spawn term when the run's entry arrives or the
       spawn ends / goes stale, the non-agent running term when the tool
       stops, the non-agent rows term at the first error, when the rows
       reach the display count or when the tool goes stale), the
       `'idle'` / `'loading'` term at the
       first settle,
       `refetchPending` when consumed, the slot-busy term at the settle
       (a tick with a busy slot only runs the stale check). A frozen transcript fetches once and
       never polls. A success with 0 rows and a false predicate ends in
       `'loaded'` with no rows ("No result available"), never `'idle'`.
  - **Display** (template states `:1080-1118`, unchanged except
    `isPolling` → `showsPending`, and one small addition: a card that keeps
    its rows through a failed fetch (rule 3) shows an inline note
    "Could not refresh: <resultError>" under its rows while
    `lastSettleFailed` **and** (`errorStreak` ≥ 3 **or** the polling
    predicate is false) — i.e. as soon as nothing will retry by itself.
    `lastSettleFailed` survives Leave and is cleared only by a success
    settle, so the note also shows after a close / reopen or a
    `connectionEpoch` retry that fails again (both reset `errorStreak`), and
    a row that could not be fetched is never missing silently):
    - a pending or polling card with no displayable rows shows "Result not
      yet available. Checking again shortly…" (`loaded` + `showsPending`,
      the computed polling predicate);
    - **control cards** have display count 1 (above, "Expected result
      count"), so their rows show as
      soon as one exists — e.g. a subagent `SendMessage` whose notification
      landed only in the root file, an interrupted `followup_task`, a run
      closed by the cutoff;
    - **spawn cards** keep today's display count; when their own run closed
      short of it, polling has stopped, so the existing "No result
      available" text shows — a Codex v2 spawn closed by `completed` or a
      child turn end before its `FINAL_ANSWER` never shows the raw ack, and
      the `count` watcher fetches the `FINAL_ANSWER` row when it lands.
    This holds even while the agent runs a later run.
  - The Claude `SendMessage` expected count changes from 1 to 2 when the
    first result sets `opensRun`; this flip turns `needsMoreRows` true while
    the run is open (the ticker, rule 4), and the `count` watcher (rule 6)
    fetches the second row
    when it lands.
- The card's running indicator = `isAgentRunning(agentId)` for both card
  types, gated only by `transcriptFrozen`. The `isStaleAgentUse` gate
  (`ToolUseContent.vue:790-793`) no longer applies to it (the backend state
  already applies the cutoff per run). On the spawn-pending spinner it is
  replaced by the `treeCutoff` check (see `needsMoreRows`).
  So a resumed agent's spawn card shows the working robot again, even when the
  spawn predates a root restart.
- Control cards:
  - Header: action label (`Send message`, `Follow-up task`,
    `Interrupt agent`, `Stop agent`, `Agent output`), returned by each
    provider's `getHeaderLabel` **only when `helperOptions` carries an agent
    interaction** (a control card: `!isTask && agentInteraction`). Otherwise
    the label stays as today: 71 of
    75 `TaskStop` calls and all 28 `TaskOutput` calls target shells, and must
    not read "Stop agent" / "Agent output".
    Summary = the agent's name, computed in `ToolUseContent.vue` with
    `getAgentDisplay(agentId, dataStore)` (`utils/agentLabel.js:50-61`) and
    rendered by the shell for **control cards** (`!isTask &&
    agentInteraction`), where it replaces `summaryRendering`; a fallback
    name (`isFallback`) renders as `Agent "<short id>"`, like the tabs and
    the drawer (`utils/agentLabel.js:17-19`). Spawn cards keep their
    current provider summaries.
  - `helperOptions.agentSlug` (`:438`, `:461`) reads the agent's link by
    agent id (`getAgentLinkInfo(agentId)`), not by `(sessionId, toolId)`,
    which is undefined on control cards.
  - Right side: the existing "View Agent" button and running robot.
  - Stop button (Claude only, `canStopSubagent()`): visible when
    `isAgentRunning(agentId)` and `runBackground`. Calls
    `stopSubagent(rootSessionId, agentId)`; the WS message and the
    `manager.stop_subagent` call are unchanged, and the handler gains the
    §5.2 `ui_stopped` row and stop step.
    Never shown in the share viewer: the share bundle aliases
    `stopSubagent` to a no-op (`noWebSocket.js:8`, `vite.config.share.js:22`).
    A no-op Stop button already shows on live Claude share spawn cards today
    (`canStopSubagent()` defaults to true, `baseHelpers.js:71-73`); control
    cards would inherit it. The gate is a share-viewer signal that is
    **always** present there: the injected `fetchToolResult`
    (`ToolUseContent.vue:182-184`, provided unconditionally by
    `ShareItemsList.vue:99`) — not `openSubagent`, which is provided only
    when `include_subagents` is on at setup (`ShareSessionApp.vue:90-91`)
    while the consumer re-reads it live (`share/consumer.py:248`). Spawn
    and control cards alike.
  - The same Stop rule replaces `agentLink.isBackground` on spawn cards.
  - No spawn-pending spinner. **While the call has no result**, the card
    shows the generic tool spinner ("Running for …") and, in its open
    Result section, the "Result not yet available" placeholder — both driven
    by the same `pendingCall` term (§8.3 fetch pipeline), not by today's
    `isToolRunning` / `isStaleToolUse` (`:813-822`, `:784-788`, whose
    cutoff is the subagent's own `max(last_started_at, last_stopped_at)` in
    the app, not the tree root's, and 0 in the share drawer). So the spinner and
    the Result stop together when the call can no longer resolve (owner
    stopped, root restarted, frozen). Once it has a result, the generic
    spinner is suppressed as for spawn cards (`ToolUseContent.vue:815`) and
    the agent widget carries the state.
- Codex input rendering: the ciphertext `message` argument shows as
  "encrypted message", through the Codex `getDisplayInputObject`
  (`codex/toolHelpers.js:1759`), which replaces that value in the displayed
  input object for `collaboration__followup_task` /
  `collaboration__send_message`. Never through `getInputRendering`: a
  non-null input rendering hides the Result section
  (`ToolUseContent.vue:639-644`), and control cards keep it.
- Result **rendering** of control cards is unchanged: they keep the generic
  result rows (the `followup_task` ack and its rebound `FINAL_ANSWER`, like
  Codex v2 spawn cards show today — `transformDisplayResult`
  (`codex/toolHelpers.js:1674-1695`) only builds the `SpawnAgentResult`
  view from a v1 `<subagent_notification>` row
  (`extractSubagentNotificationBody`, `:903-926`), and a v2 renderer is not
  part of this design). Only the expected count and the display count
  change (display count forced to 1 for control cards, see "Expected result
  count" above): `getExpectedResultCount` (`codex/toolHelpers.js:956-1019`) returns
  2 for `collaboration__followup_task` when the card's interaction has
  `opensRun`, else 1 (a merged follow-up never gets a second row). The
  interaction reaches the helper through `helperOptions`
  (`ToolUseContent.vue:421-469`).
- Before the interaction reaches the store (Codex: between the call and its
  `SubAgentActivity` event; any provider: while the tree rule does not pass
  yet), the card is generic.

### 8.4 Subagent header

`SessionHeader.vue` `canStopAgent` = `isAgentRunning(agentId)` and
`runBackground`, replacing the `!link.stoppedAt` and `link.isBackground`
checks. Its other gates stay: `props.mode === 'subagent'`,
`canStopSubagent()`, not `session.ephemeral`, a subagent
(`parent_session_id`), and a synthetic, non-dead process state
(`SessionHeader.vue:255-266`).

## 9. Tests

Backend (pytest), on the live **and** batch paths, fixtures built on §4:

- Claude: background spawn → end → `SendMessage` resumed (root shape) →
  running → end notification on the `SendMessage` id → stopped.
- Claude: `SendMessage` from a subagent, resumed text shape without
  `resumedAgentId` → run; its notification lands in the **root** file only →
  `AgentRunEnd` → run closed, while the owner has one `ToolResultLink` only.
  Variant: the notification also lands in the caller's file (`attachment`) →
  second `ToolResultLink`, same outcome.
- Claude: a `TaskStop`'d agent's notification with status `killed` →
  `AgentRunEnd` written.
- Claude: a run opened and closed in the same live batch → `AgentStoppedUpdate`
  and `_after_agents_stopped` still fire.
- Two closing pieces of evidence for one run in one batch — a Claude
  user-form notification line (second result **and** `AgentRunEnd`), a
  Codex `completed` + `FINAL_ANSWER` — close it once: one stamp at the
  run's earliest evidence (the `completed` time for Codex), one
  `agent_stopped`, the same `stopped_at` as a reload.
- Codex: `completed` of the previous run between a `followup_task` call and
  its `interacted` line (and the child's `task_complete` at the same time)
  → the follow-up run (`started_at` = its `interacted` time) stays open
  until its own end. Live, the candidate test reads the interaction's
  stored `event_line_num`, not its call line, so live and batch attribute
  that `completed` to the previous run.
- Codex `interrupt_agent` race: the interrupt call, then the target's
  `completed`, then the `interrupted` event, then the interrupt's result,
  then the `FINAL_ANSWER` → the `completed` and the `FINAL_ANSWER` land on
  the interrupted run (it had its `completed` before the stop's event line,
  so it is not "stopped in line order"); live and batch write the same rows.
- Two-call stop step: a batch whose only closing evidence reaches a run
  already closed before it (the t20 late `FINAL_ANSWER`) closes no run: no
  stamp, no `agent_stopped`, no hook; `agent_run_state` is still sent.
- Late tree rule in a batch: a Claude `SendMessage` run whose end is already
  in the DB, then its target's spawn link arrives in a later batch with the
  spawn already closed → that batch closes the run once (stamp at its
  `closed_at`, hook fired); the next batch does not close it again.
- Claude: `SendMessage` queued on a running agent → no run; the agent stops
  with its own run.
- Claude: `TaskStop` success on an agent → stopped; on a shell → row rejected
  by the tree rule; error → no stop.
- Claude: `SendMessage` to `"main"` → no widget, no state.
- Tree rule owner scope: a `SendMessage` / `TaskStop` interaction whose
  owner session is outside the root tree (e.g. copied by a CLI fork under
  another root) targeting this tree's agent → no run, no stop, not in the
  snapshot's `interactions`.
- Claude: interaction written before its target's spawn link exists (session
  order) → counted once the link exists, without recompute; live, the
  `agent_link_created` broadcast re-sends it as `agent_interaction`.
- Codex: spawn → `completed` → `FINAL_ANSWER` → `followup_task` on the idle
  agent → run → `completed` and `FINAL_ANSWER` land on the `followup_task`.
- Codex: `followup_task` on a running agent that merges (08-31 example) →
  `opens_run = false`; the single end pair lands on the spawn.
- Codex: the 09-06 `reaudit_backend` case → documented limitation: agent not
  running between the two runs; the second pair lands on the spawn as extra
  signals.
- Codex: owner turn abort — a `turn_aborted` (the `01a08171…` line 17910
  case) and a `task_complete` with `usage_limit_exceeded` (the `01a0796d…`
  line 704 case) each write an `owner_turn_aborted` `AgentRunEnd` (its
  `agent_id` = the cut run's agent) for every file-open run of
  that file opened in the aborted turn (a run opened in an earlier turn of
  the same file is not cut); the agents stop; the cut runs are not
  candidates, so a later
  `followup_task` opens its own run and gets its own signals; the root's
  live agent drops those children, and a hold armed by the root's turn end
  just before is released by the stop hook. A `turn_aborted` with another
  `reason`, and a `task_complete` with another error kind, write nothing.
- Live tuple: `agent_interaction_updates`, `agent_run_state_updates` and
  `agents_resumed` are appended after the existing elements; existing
  positional indexes are unchanged.
- Claude root interrupt: the child file's last line is exactly
  `[Request interrupted by user]` (the `aa6d89b7469be1a2c` case) → an
  agent-level `interrupted` `AgentRunEnd`; the runs started before it
  close; the same text quoted inside a prompt or a coordinator message
  writes nothing; a run started after it (a later `SendMessage` resume) is
  not closed by it. Live and batch write the same row.
- Stop-button broadcast order: a watcher batch that resumes the agent right
  after the handler's step cannot slip its `agent_run_state` before the
  handler's (the handler sends under the lock); the handler fires no
  `_after_agents_stopped`.
- Codex resume relay that adds a child refreshes background work and the
  status label, in a turn (`wait_agent` label) and in the hold (hold
  label).
- Stop button: `ended_at` is read when the handler sends the stop request
  (before `await manager.stop_subagent`); a `SendMessage` resume whose ack
  lands during the stop round trip or the lock wait stays open, and if
  the kill stops it, its `killed` notification closes it.
- Rule 2 tie-break: `TaskStop(A)` then `SendMessage(to=A)` whose results
  carry the same `tool_result_at` (stop result line first) → the resumed
  run stays open; a stop at the same time on a **later** line closes it.
- Claude resume run start = its ack time: `TaskStop(A)` then
  `SendMessage(to=A)` in one assistant message (stop result before the
  resume ack) → the resumed run stays open until its own end; a Stop-button
  `ended_at` between a `SendMessage` call time and its ack time → the
  resumed run stays open; the interaction's `started_at` moves from the
  call time to the ack time when `opens_run` turns true (live, batch and
  the `agent_interaction` payload).
- Frozen share: a post-freeze agent's `SendMessage` resume of a visible
  agent is not a run in the frozen snapshot (owner not visible), and a
  stop record owned by a non-visible agent does not count.
- Claude Stop button: a successful `stop_subagent` writes a `ui_stopped`
  `AgentRunEnd` on the root and runs the stop step; the agent's runs
  started before `ended_at` close even if the `killed` notification never
  reaches a transcript (a run resumed after `ended_at` is §10's case); a batch recompute
  of the root keeps its `ui` rows.
- Duplicate interaction lines: batch and live keep the same (first) line.
- Codex: `notify_subagents_resumed` for a nested agent (spawned by a
  subagent) adds nothing to the root's `_live_subagents`.
- Stop step: every run the batch closes has a null `closed_at` (its
  closing evidence has null timestamps) → no stamp; an
  `AgentStoppedUpdate` with `stopped_at = None`, `stamped = False`;
  `agent_run_state` sent, `_after_agents_stopped` fired, no
  `session_updated` / `agent_stopped`.
- Codex: a merged follow-up followed later by real follow-ups (09-07
  `spec_provider_review`, 09-06 `reaudit_accessibility` sequences) → the
  merged one gets no signal; each later run gets its own pair and closes.
- Codex: `followup_task` a few seconds after `interrupt_agent` (e.g. 08-12
  `019ff497` lines 4448/4452) → opens a run; the next pair lands on it, not on
  the interrupted run.
- Codex: long `completed` → `FINAL_ANSWER` gap with a follow-up inside it →
  the `FINAL_ANSWER` goes to the first run.
- Codex: `opens_run` computed live after a root restart equals the batch
  value (no cutoff input).
- Codex: `completed` creates no `ToolResultLink` (a spawn card ended by
  `completed` + `FINAL_ANSWER` holds ack + `FINAL_ANSWER` only).
- Codex: run ending with `FINAL_ANSWER` only; pre-08-27 rollout.
- Codex: the `01a08171…` t20 sequence (spawn ends with `FINAL_ANSWER` only
  at 76417; follow-ups at 76422 and 76532) → each `completed` (76508,
  76583) and each `FINAL_ANSWER` (76517, …) lands on its own follow-up; no
  cascade onto the previous run.
- Codex: `send_message` → no run, no stop; `interrupt_agent` → stopped;
  `interacted` without a call → no row.
- Codex: a child `task_complete` **after** the run's start closes it (the
  01a08171 `t20_implementation` case: stopped at 03:50:03Z, not at 06:32:24Z;
  the late `FINAL_ANSWER` neither re-stamps `last_stopped_at` nor sends a
  second `agent_stopped`, and `stopped_at` stays 03:50:03Z);
  a child `task_complete` **before** a `followup_task` does not close the
  follow-up run; the rule never changes where a `FINAL_ANSWER` lands.
- Codex: the batch writes the same agent-level `AgentRunEnd` rows as live
  (real boundary time, not the file mtime; the flat-timestamp files of §10
  excepted).
- Codex: a forked child (`forked_from_id`, `subagent_history_start_ordinal`)
  writes no agent-level row for its copied `task_complete` lines; its first
  run stays open until its own turn ends (the 09-08 `01a08112-b173…` case).
  A **non-fork** child that carries `subagent_history_start_ordinal` still
  writes its rows (the `01a05541-635c…` case).
- Codex: a rule-5 close found in the child's file reaches the root's live
  agent (hooks routed to the tree root): the child leaves `_live_subagents`,
  the hold releases.
- Claude: a notification with `<status>running</status>` and a payload
  writes no `AgentRunEnd`; a `queue-operation` `remove` line writes none.
- Codex: the v1 `<subagent_notification>` still rebinds to its spawn after
  the `FINAL_ANSWER` branch change.
- Codex history replacement (`_begin_replace_codex_history`) deletes the
  session's `AgentInteraction` and `AgentRunEnd` rows.
- Claude: parse input — a rewritten user-form notification
  (`twiccOriginalContent`), a rewritten attachment-form notification
  (`twiccOriginalEntry`) and a raw `queue-operation` line all write the same
  `AgentRunEnd`, live and batch.
- Two open runs: closing one keeps the agent running.
- Compaction-copied result rows (same `tool_result_at`): a copied ack does
  not reach the count; the `aa6d89b7469be1a2c` spawn stays open until its
  stop, not until its copied ack.
- Stop broadcast: `agent_run_state` and `_after_agents_stopped` fire even when
  the monotonic guard refuses the stamp; the child `session_updated` and
  `agent_stopped` are sent only when `stamped`.
- Snapshot: per-agent state, `interactions`, frozen-share filter,
  interaction owned by a subagent; a root `ui` row before / after the
  freeze time, and with no timestamp at or before the frozen line (dropped);
  a root-owned `SendMessage` resume after the frozen line is not a run in
  the frozen snapshot (the agent reads stopped, as at the freeze).
- Attribution with a stop in the `completed` → `FINAL_ANSWER` gap: S0 gets
  `completed`, `interrupt_agent` on the agent, a `followup_task` opens F1,
  S0's late `FINAL_ANSWER` → lands on S0 (it keeps its candidacy for its
  own `FINAL_ANSWER`), F1 stays open.
- Duplicate `tool_use` line → one row.
- Codex live agent: `notify_subagents_resumed` re-adds a running child only;
  the prune keeps it while its run is open; the prune keeps a child whose
  spawn link is not written yet (`known = false`); an ephemeral agent re-adds
  on `interacted`, a watcher-backed one does not.
- Share relay: `share_agent_run_state` and `share_agent_interaction` filters
  (root, descendants, owner, display ceiling); a late interaction re-sent
  after `agent_link_created` passes the descendant filter, both when the new
  agent is its target and when it is its owner.
- `agent_run_state` is never broadcast for a shell / Monitor `AgentRunEnd`.
- Stop step `exclude` sets: a Codex stop row created at its `interrupted`
  line in a batch whose `interrupt_agent` result was written in an earlier
  batch → that batch closes the run (stamp, `agent_stopped`, hook); a
  Claude subagent `SendMessage` whose root-file `AgentRunEnd` was applied
  before the caller's first result → the batch of the `opens_run` flip
  closes the run once (stamp at its `closed_at`, hook).
- Rule-3-only close: a Claude child whose spawn link arrives after a root
  restart, with no end evidence → the batch stamps it at the cutoff time;
  a later root restart alone stamps nothing live.
- Frozen share: a root-owned `SendMessage` called before the frozen line
  whose first result is after it → not a run in the frozen snapshot
  (`opens_run` false in `interactions`); same for a Codex `followup_task`
  whose `interacted` line is after it.
- Codex attribution with an owner abort after L: the abort row is not yet
  in `batch_state` (batch) nor below L in the DB (live); both keep the run
  a candidate for that signal.
- Codex batch "has `FINAL_ANSWER`" reads the `all_tool_result_links` built
  so far: a run whose ack and `FINAL_ANSWER` share a timestamp counts one
  result in both paths, and `opens_run` is the same live and batch.
- Codex relay order: a resume relay whose read would precede the next
  batch's close, and that close's stop relay, run in either order → the
  child is not left in `_live_subagents`; an older stop relay (run 1
  closed) running after a newer resume relay (run 2 open) → the child
  stays; the prune in the same situation keeps it too.
- Batch / live parity through `collect_agent_run_signals`: for the same
  rollout, the batch writes the same `opens_run`, the same `completed`
  attribution and the same owner-abort cut rows as live (outside the
  never-observed spawn-call → `started` gap, §10).
- `closed_at` with mixed evidence: a run closed by an `AgentRunEnd` with a
  null `ended_at` and by a timed result → `closed_at` = the timed result;
  null only when every closing piece is null.
- Codex ephemeral parent in the subagent hold: its last child goes idle
  (`thread_read`) → `notify_subagents_stopped` pops it with no DB read and
  the hold releases; the prune does the same.
- Codex batch file facts: an owner abort in batch finds its turn's
  `task_started` line through the per-session `turn_id` map (same cut rows
  as live); a forked child's copied `task_complete` lines write no row in
  batch (fork fields kept from line 1).
- Hook position: a Claude `SendMessage` first result decides `opens_run`
  in the hook call of that same line (live and batch); a compaction copy
  of that result decides the same value.
- Frozen share: a root-owned Codex stop whose result is before the frozen
  line and whose `interrupted` line is after it → not a stop record in the
  frozen snapshot (the run stays as at the freeze).
- Codex stop whose result comes before its `interrupted` line, with a
  signal of the agent between them → the signal's candidates still
  include the run (the stop's `event_line_num` is after L), live and
  batch.

Frontend (node:test):

- `applyAgentSnapshot` fills `agentRunStates` and `agentInteractions`.
- `applyAgentRunState`: running / not running / run before the cutoff; the
  synthetic `started_at` is the newest run.
- An `agent_run_state` message re-creates the synthetic state after a stop.
- Ordering: an `agent_run_state` received after a snapshot fetch started wins
  over that snapshot; one received before loses; a child `session_updated`
  during the fetch does not block the snapshot's run state; a run state
  arriving before its link is kept.
- Reconnect: `setActiveProcesses` re-applies run states (synthetic states
  survive), including when `active_processes` lands after the snapshot.
- Own run and cutoff: a card whose run is `open` but started before the root
  cutoff reads `ownRunOpen = false` and stops polling without a new
  snapshot (a control card shows its rows; a spawn card below its display
  count shows "No result available").
- Display count: a resumed `SendMessage` / opened `followup_task` card shows
  its ack at once (display count 1) while it still fetches the second row.
- `needsMoreRows` on every entry point: open the Result section, see the
  ack, close it; the second row arrives; reopen → it is fetched (also on
  remount and on reactivation).
- Frozen share snapshot with a run open at the freeze: `ownRunOpen` is
  false, the card does not poll (a control card shows its rows; a spawn card
  below its display count shows "No result available").
- Share viewer: the own-run cutoff comes from the agent's root, not the
  drawer's `rootSessionId`; no Stop button on any agent card.
- Share snapshot: an interaction above the display ceiling is not listed,
  as in the live relay.
- WS dispatcher: `agent_interaction` and `agent_run_state` reach the store.
- `tool_state` no longer changes an agent's state.
- Control card: `agentId` from the interaction; name via `getAgentDisplay`.
- Header label: a shell `TaskStop` and a shell `TaskOutput` card keep
  today's label; only agent cards read "Stop agent" / "Agent output".
- Result fetch: a `FINAL_ANSWER` row arriving after the run closed is still
  fetched (watcher on `resultCount`); the Claude `SendMessage` expected count
  flip 1 → 2 fetches the second row; a **control** card whose own run is
  closed with fewer rows than expected shows the rows it has, even while the
  agent runs a later run (an interrupted `followup_task` followed by another
  one); a **spawn** card in the same case (an interrupted spawn followed by a
  `followup_task`) stops polling and shows "No result available"; polling
  stops once a fetch has seen the current `resultCount` and the own run is
  closed.
- Compaction-duplicated `tool_use` line (`resultCount` 4, 2 rows per card):
  the card fetches once per `resultCount` change and does not poll forever.
- Pending control card (e.g. a blocking `TaskOutput` on an agent, before
  its first result): the generic spinner shows, the open Result section
  polls (`pendingCall`) and shows "Result not yet available. Checking again
  shortly…"; once a result arrives, the agent widget takes over.
- Link / run state arriving after mount: a spawn card mounted before its
  snapshot, Result open, no rows → `spawnAwaitingRun` keeps it pending,
  and when `ownRunOpen` turns true it keeps polling (ticker, rule 4),
  never "No result available" in between.
- Single flight: a `count` change during a request only sets
  `refetchPending`, and one more request follows the settle; a response
  slower than 3 s (but under the 30 s threshold) is never killed by a tick
  and is applied.
- Responses always slower than 30 s (e.g. 45 s each, SPA and share paths):
  the first stale kill raises the threshold to 60 s, the next request
  lands and renders; later refreshes land too. Responses slower than every
  threshold: after 3 stale kills the fourth request is not killed and
  lands. A Leave or a success settle resets the count.
- Ticker at mount: a card that mounts with its Result section open and a
  true polling predicate polls every 3 s (the `immediate` watcher), and
  its "Checking again shortly…" text is followed by real requests.
- Entry after an error with the Result section closed (card reopen,
  reactivation): no request; opening the section retries once.
- No flicker: a polling card never switches to "Loading result…", with
  rows (keeps them) or without (shows "Checking again shortly…"), e.g. a
  running foreground Claude `Agent` card with its Result section open; a
  polling card deactivated then reactivated neither.
- No stuck state: a first fetch (no rows) in flight, the section closes
  (Leave restores `idle`), reopen → fetches; same through deactivation.
- Slot ownership (share viewer, no abort signal): a slow first response,
  the section closes then reopens → the reopen starts its own request at
  once; the old response, when it settles, writes nothing and does not
  touch the new request's slot.
- Success with 0 rows and a false predicate (frozen share with no result
  at the freeze, stale pending call) shows "No result available", never a
  blank section; an error shows the error text.
- Own run closing while the Result section polls (count unchanged): the
  "Checking again shortly…" text stops at once (computed `showsPending`);
  the ticker stops at once when no request is in flight, else at the
  settle of the in-flight request (the busy-slot term; ticks before it
  only run the stale check); the in-flight request still applies its
  rows; no extra request starts.
- Card type switch: a Codex `followup_task` card polling as a generic card
  when its `interacted` event arrives switches to the agent predicate and
  stops once its run closes, even short of its expected rows.
- Reactivation: a Codex v2 spawn card polling (ack only), tab deactivated,
  its run closes by `completed` with no count change, reactivate → no
  fetch, no ticker, "No result available" (no dead spinner); a card left
  `idle` by the deactivation abort fetches on reactivation.
- Share drawer: a pending agent call, and a pending nested spawn with no
  link yet, from before a root restart stop polling and spinning
  (`treeCutoff` from the tree root, not the drawer's `rootSessionId`).
- Persistent fetch error with `count` ≥ 1 (e.g. a finished Bash card or a
  closed-run agent card): a first fetch that errors runs no ticker
  (`fetchedAtCount` null, `countChanged` false); an error on a count change
  gets up to 3 tries in total (the first error plus 2 retries), then stops
  (the third error settle records
  `fetchedAtCount`); closing and reopening the section retries once.
- Transient error on a count change: a closed-run card's `FINAL_ANSWER`
  fetch fails once, then succeeds on the next tick → the row shows without
  a reopen.
- `staleKills` reset: 3 stale kills, then a success settle, then a request
  that hangs → it is killed after 30 s again.
- Non-agent visible changes: a running tool reopened with complete display
  rows polls again; a tool "running" from before the session's cutoff stops
  polling, with rows or with none (a Bash cut by a restart, no result:
  "No result available", no ticker); a reopen or a reactivation after an
  error retries once; a card whose last fetch failed retries once on a
  `connectionEpoch` change and on a `count` change; a hung request is
  killed and retried.
- Entry mirror: a section hidden by `showResultDetails` during its first
  request, then shown again, fetches at once (no blank section for 3 s).
- Store rules: a root restart (cutoff change) removes the synthetic state
  of a run started before the cutoff and keeps the robot of a run started
  after it; a snapshot that no longer lists an agent removes its "working"
  indicator.
- Long error streak: more than 3 errors while the predicate holds through
  another term (open run), then a count change, then that term ends → the
  next error settle records the new count and the ticker stops.
- Leave on the SPA path aborts the in-flight request (no request outlives
  a closed or unmounted card).
- Root first load with a failed snapshot: the stored run states of that
  root are applied (robot, Stop button and card polling agree).
- Post-snapshot apply of every entry (store-level unit test, explicit
  sequence): with `itemsFetched` false for the root, a snapshot fetch
  starts; an `agent_run_state` for an agent absent from that snapshot is
  stored, with a live stamp newer than the fetch token, and no synthetic
  state is set (gate closed); `itemsFetched` then becomes true; the
  snapshot resolves and applies → that kept entry gets its synthetic
  state (robot, Stop button), and an entry the snapshot deletes loses its
  synthetic state.
- Share viewer: a drawer opened from a `#agent=` hash before the snapshot
  uses the shared session's cutoff (`sharedSessionId`); a socket reopen
  re-fetches nothing (as today, §7.3).
- A tick during a slow request (under the stale threshold) does not set
  `refetchPending`:
  when the own run closes meanwhile, no extra request follows the settle.
- Pending call made during the older of two open runs of its owner: still
  pending (header spinner and "Checking again shortly…").
- `unloadSession` of an agent with a running `agentRunStates` entry: its
  cards keep the running robot and the Stop button; the control cards its
  transcript owns keep their agent widget when it loads again (its
  `agentInteractions` survive; only the root's unload drops them).
- Control card whose agent has no display name: summary `Agent "<short
  id>"`.
- Reopen / reactivate with 0 displayable rows (agent card polling, and a
  running non-agent card): "Checking again shortly…" shows at once, never
  "No result available" before the response.
- Non-agent card: the final row lands during a request (`refetchPending`)
  or while the Result section is closed → it is fetched (count term).
- Pending call owned by a subagent that is then stopped while the root runs
  on: the open Result section stops polling ("No result available") and
  the header spinner stops with it; a later resume of that subagent does
  not revive the call (it predates the new run).
- A request that never settles: a tick after 30 s invalidates it, frees the
  slot and retries — also for the very first request (state `'loading'`
  keeps the ticker running); no error text shows; the late settle, if any,
  is ignored. The retries follow the thresholds (60 s, 120 s, then none).
- A request that fails while a count change is pending: the settle
  consumes `refetchPending` and fetches again, so the new row is still
  fetched; with nothing pending and a false predicate the error text stays.
- A card whose section closes or that deactivates runs no ticker and sends
  no request afterwards (no leftover timer).
- A deactivated (KeepAlive) card does not fetch on a `resultCount` change.
- A Codex v2 spawn card whose run closed before its `FINAL_ANSWER` shows
  "No result available", not the raw ack, then the answer when it lands.
- A spawn card keeps its provider summary; a control card shows the agent
  name.
- `agent_run_state` and the snapshot carry per-run `open` and `closed_at`;
  the store's `runs` map follows `open` and `startedAt`.
- Store lifetime: `unloadSession` drops the root's run states and
  interactions;
  `applyAgentRunState` for a root whose items are not loaded removes any
  synthetic state and sets none.
- Share shim: `isAgentRunning`, `getAgentInteraction`,
  `runStatesAvailable`.
- Non-agent card fetch errors: a finished Bash card whose fetch fails
  shows the error and runs no ticker; a running non-agent card whose fetch
  fails retries every 3 s until the tool stops, then stops.
- Spawn card between `agent_link_created` and `agent_run_state` (Result
  open, ack only): `spawnAwaitingRun` keeps it polling ("Checking again
  shortly…"), never "No result available" in between.
- `unloadSession` of a root: the synthetic states of all its
  `agentRunStates` entries are removed, including agents with no loaded
  session row (no phantom "working" in the tree or tabs).
- Reconnect: a snapshot response discarded by a generation bump is
  re-issued once and applies; a failed reconnect snapshot (HTTP or
  network error) and a second discard each apply the root's stored run
  states (robot, Stop button and `ownRunOpen` agree); a snapshot in flight
  when `unloadSession(root)` runs is not re-issued and refills nothing.
- Control card with `count` 0 and an open own run (Codex `interacted`
  before the ack) whose subagent owner stops: the header spinner and the
  "Checking again shortly…" text stop together (own-run term needs
  `count > 0`).
- Share `include_subagents` turned off live: `runStatesAvailable` turns
  false and stays false until reload → no robot, no own-run or
  awaiting-run polling; a root-owned spawn's spinner behaves as today,
  a subagent-owned spawn with `count` 0 in an open drawer stops spinning
  (an interrupted spawn with a
  closed run and an open section does not start polling), a stopped
  subagent's pending call in an open drawer stops — also when the drawer
  was opened from `#agent=` before the setup snapshot landed and that
  snapshot is then discarded (the owner has no `agentRunStates` entry and
  still counts as not running); a setup snapshot in flight at that moment
  is discarded. Turned back on live: still false until reload.
- Share relay seeding: a viewer that connects after an agent's link
  synced but before its `Session` row exists still receives that agent's
  `share_agent_interaction` events.
- Error with rows held: a polling card that shows rows and gets one failed
  refresh keeps its rows (`'loaded'`, no error text) and retries; a card
  with no rows, or with fewer rows than its display count (a spawn card
  holding its ack only whose `FINAL_ANSWER` fetch keeps failing), shows
  the error text, never "No result available".
- Error streak with rows kept: a control card showing its ack whose
  `FINAL_ANSWER` fetch fails 3 times while the socket stays up shows the
  "Could not refresh" note under the ack; a success clears it; after a
  close / reopen whose one Entry retry fails again (`errorStreak` back to
  1, predicate false), the note still shows; same after a failed
  `connectionEpoch` retry.
- Failed spawn: a Claude background `Agent` spawn whose only result is an
  `Unknown error` → no `spawnAwaitingRun`, no polling ("No result
  available" or the error row, as today), and the spawn-pending spinner
  stops (today it spins until the root restarts).
- Spawn owner gate: a spawn called by a subagent that is stopped before
  the ack and the link (root still running) → the Result section stops
  polling and the spawn-pending spinner stops (count 0 → `pendingCall`).
- `unloadSession` of an agent whose root stays loaded re-fetches the root
  snapshot: its nested spawn cards keep their link, robot and View Agent
  when its transcript loads again.
- §8.1 reversals: an `agent_stopped` message no longer removes the
  synthetic state; a child `session_updated` with `last_stopped_at` no
  longer removes it; `agent_link_created` no longer creates it;
  `setAgentLink` carries no `running` field; `SessionHeader.vue`
  `canStopAgent` reads `isAgentRunning` + `runBackground`. A finished card's one-off Entry or
  `connectionEpoch` retry that hangs is stale-killed (the busy slot keeps
  `wantsFetch` true).
- Root cutoff change: `applyAgentRunState` runs after the `$patch`, so a
  run started between the old and the new cutoff loses its robot.
- Interactions ordering: an `agent_interaction` received after a snapshot
  fetch started wins over that snapshot; an interaction that a snapshot of
  its root no longer lists is deleted (when its stamp is not newer than
  the token).
- Connection epoch: a card whose last fetch failed after a 3-try streak during a
  backend restart retries once the reconnect refresh ends (SPA), and shows
  the row, with no `count` change.
- Owner gate: a pending agent call in a Claude workflow agent tab (no
  `agentRunStates` entry for the owner) still polls ("Checking again
  shortly…") until its result lands.
- Leave on a hidden section: `showResultDetails` turning false during a
  request with `refetchPending` set runs Leave (no ticker left running
  with no request; the section fetches again when it is shown and open).
- `treeCutoff` fallback in the app: a subagent card with no link info uses
  the root (`props.parentSessionId`), so a pending nested spawn from before
  a root restart stops.
- Leave after a stale kill of the first request: `resultState` goes back
  to `'idle'`, and the next Entry fetches.

## 10. Risks and limitations

- **Codex follow-up on a running agent that runs afterwards** (1 of 356):
  shown stopped between the two runs, and its end signals land on the spawn
  card (§5.6).
- **Older Codex forked children** (18 files, 22 real turn ends: 10 cli 0.146.0 v2
  forks, e.g. `2026/08/12/…019ff5af-781d…`, ordinal 98 below the field 99;
  8 cli 0.130/0.131 v1 forks from 2026-05-15, e.g. `019e2afe-5acc…`): their
  real turn ends sit below `subagent_history_start_ordinal`, so they write
  no agent-level row and rule 5 does not fire. The parent's `FINAL_ANSWER`
  (v2, worst case 6 s late) or v1 `<subagent_notification>` (1–11 ms late in
  8 of 9 cases, 4.2 s for `019e2cab-be94…` line 38)
  closes the run instead. Historical only. Matching by
  `turn_id` instead is not used: one of these files
  (`01a0201a-ba3a…`) is also a flat-timestamp file, whose re-admitted rows
  would close runs at spawn time.
- **Codex run cut by a root process death that writes no `turn_aborted`**
  (e.g. a killed process; not observed — interrupts and usage-limit turn
  ends are covered by the owner-turn-abort rule, §5.2): it has no end signal in the
  parent file, so it stays a candidate "with neither signal";
  a later `followup_task` on that agent is recorded as merged, the agent
  shows stopped during it, and its signals land on the cut run's card. The
  parent rollout carries no resume marker to detect this file-locally.
- **`turn_aborted` reasons other than `interrupted`** (none in the data):
  they do not cut runs.
- **Owner turn errors other than `usage_limit_exceeded`**
  (`server_overloaded`, `cyber_policy`, `other`; 0 of them had running
  children): they do not cut runs. If one ever stops its children, those
  runs stay open until their own end signal, rule 5, or the root cutoff.
- **Codex `interrupt_agent` issued by a subagent on an agent spawned by
  another session** (0 observed: all 9 rollouts with `interrupt_agent` are
  root rollouts): attribution ignores stops from other files, so the
  spawner's run stays file-open in its own file; a later `followup_task`
  from the spawner is recorded as merged (shown stopped while it runs, its
  signals on the old run's card). The running state itself is right
  (§5.5 closes the run by time).
- **Owner turn abort in a subagent owner's rollout** (unverified: the
  data behind §5.2 covers root rollouts only): the design assumes that a
  subagent's own `turn_aborted` / usage-limit `task_complete` stops the
  nested children it opened in that turn, as for a root, and cuts their
  runs. If such nested children survive, they show stopped while they
  still run (their cut runs are no longer candidates for later signals).
  Revisit if observed.
- **Runs opened in an earlier parent turn at a Codex owner abort** (0
  observed): they are not cut. If Codex does stop them too, they stay open
  until their own end signal, rule 5 (the child's turn end — a cut child
  writes none), or the root cutoff.
- **Nested runs under an abort-cut child** (0 observed: none of the 5 cut
  children owns runs): the abort row cuts only the runs owned by the
  aborting file. A cut child writes no `turn_aborted` of its own, so runs it
  owned (its own nested children) get no abort row and stay open until their
  own end signal, rule 5, or the root cutoff.
- **A resumed agent's own call timestamped before its run start** (the run
  starts at the caller-side ack for Claude — the first result in the
  owner's file, root or subagent — at the `interacted` event for
  Codex — up to 1.6 s after the call, §5.1): such a call made by the
  resumed agent in that short gap fails the `pendingCall` owner-run
  comparison (§8.3), so while it has no result its open Result section
  shows "No result available" instead of "Checking again shortly…"; the
  `count` watcher fetches its result when it lands. No tolerance is added:
  it would revive calls from a just-stopped earlier run.
- **Orchestration tab while the root's items are not loaded** (e.g. its
  compute is pending after a compute-version bump, so
  `SessionItemsList.vue:692-695` skips the load): `applyAgentRunState`
  sets no synthetic state for that root (§8.2), so the tree shows its
  agents stopped until the items load and the root's snapshot re-applies
  them. Today those agents show working. The cards of an open subagent
  tab of that root follow: no robot and no Stop button (`isAgentRunning`
  reads the synthetic state), and the `pendingCall` owner gate counts the
  running owner as stopped (its pending calls show "No result available"),
  while `ownRunOpen`, which reads `agentRunStates` directly, can keep a
  spawn card on "Checking again shortly…" without a robot.
- **Codex `interrupt_agent` and `followup_task` in parallel** (not
  observed: the 4 follow-ups after an interrupt in §4.2 are sequential):
  with the order `interrupted` < `interacted` (follow-up) < interrupt
  output, the line-order exclusion (the stop's result line before L)
  keeps the interrupted run file-open at the `interacted` line, so the
  follow-up is recorded as merged: it opens no run, and the agent shows
  stopped while it runs.
- **Codex ephemeral parent and `send_message`** (§6.4): the ephemeral
  re-add on `interacted` cannot tell `followup_task` from `send_message`
  (§4.2), so a `send_message` to an idle child puts it back in
  `_live_subagents` until the next prune (`wait_agent` or a hold arm,
  which prunes first, `codex/agent/agent.py:1320`): the background-work
  count and the in-turn wait label read one child too many meanwhile; no
  false hold.
- **Share viewer outage** (as today, out of scope, §7.3): a live share
  viewer whose socket drops and reopens re-fetches nothing, so run states,
  interactions, result counts and a root cutoff change missed during the
  outage are not recovered until the page reloads — e.g. an agent that
  stopped during the outage keeps its robot, and a card waiting on its run
  keeps polling. Today the same outage leaves links, tool states and items
  stale the same way.
- **Codex live resume latency:** the child re-enters `_live_subagents` only
  once the watcher has processed the parent's `interacted` line (§6.4).
- **Reference line between a Codex spawn call and its `started` event**
  (never observed: `started` follows the call by ~40–80 ms): live can hold
  the spawn's `AgentLink` before the `started` line
  (`subagent_needs_link`, `compute_base.py:3494-3498`), batch adds it only
  at that line (`tool_result_agent_info`, `:2735-2744`). A signal or abort line L in that gap would
  count the spawn as a candidate live but not in batch.
- **Codex run ending with `completed` but never a `FINAL_ANSWER`** (0
  observed, e.g. 0 of 413 agents in `01a08171…`): it stays "has
  `completed`, no `FINAL_ANSWER`", so the next opened follow-up's
  `FINAL_ANSWER` lands on it, and each later one a run too early. The
  running state stays right (rule 4 closes each run).
- **Codex `completed` arriving after its run's `FINAL_ANSWER`** while a newer
  run of the agent has neither signal (0 observed: `completed` always comes
  first in the data): it lands on the newer run and closes it early.
- **Codex follow-up from a non-spawning session** (0 observed): its end
  signals may land in another file; the run then closes through the child's
  turn end (rule 5) or the root cutoff (§6.2).
- **Codex runs with no end signal in the parent file** (pre-2026-08-27 runs
  without `FINAL_ANSWER`, a parent idle when the child ends): closed by the
  child's turn end (rule 5), else by the root cutoff. A child that never
  ends its turn (crash, deferred) stays open until the root cutoff. Since
  rule 5 is not a file-local signal, such a run also stays **file-open** in
  the parent file: a later `followup_task` on that agent is recorded as
  merged, shows stopped while it runs, and its end signals land on the old
  run's card (same effect as the root-death case above). Historical
  pre-2026-08-27 rollouts only in the data; not observed with a later
  follow-up.
- **Two distinct results with the exact same `tool_result_at`** (rule 1
  counts distinct timestamps to ignore compaction copies): they would count
  as one, and the run would close one result later. Not observed on agent
  calls (an ack and its end signal are always far apart).
- **Claude queued `SendMessage` during a tool-less wake-up** (2 of 13 queued
  calls; these 2 messages were never delivered to the target file): the
  target runs a wake-up that opens no run (§3.3), so the card shows it
  stopped. Resolved by the deferred tool-less wake-up follow-up. Same
  cause, card side: a call made by an agent during such a wake-up (e.g.
  `TaskOutput`, a `SendMessage` to a sibling, a foreground `Agent` spawn)
  fails the `pendingCall` owner gate (the owner reads not running), so
  while it has no result it shows no spinner and its open Result section
  shows "No result available" (for a foreground spawn, for the child's
  whole run, next to the child's own robot); the `count` watcher fetches
  the result when it lands. Today the generic spinner spins there for a
  `TaskOutput` or `SendMessage`; a foreground spawn card shows its robot
  and its Result section polls (the generic spinner is suppressed for
  spawn cards).
- **Stop-button kill of a run resumed after `ended_at`** (§5.2): a
  `SendMessage` resume whose ack lands after the handler's `ended_at` and
  that the kill then stops is closed only by its `killed` notification
  (rule 4); if that notification never reaches a transcript, the run stays
  open until the root cutoff.
- **Codex child files that stamp every line with the thread creation time**
  (4 non-fork files, cli 0.146–0.150, `history_mode: paginated`:
  `019ff1cf…`, `01a038ac…`, `01a04f14-5d48…`, `01a05530-f83e…`): on
  recompute, rule 5 dates their spawn-run close at spawn + ~0.1 s. Live is
  not affected (real times). Historical only. (The flat-timestamp fork
  `01a0201a-ba3a…` is not in this case: its turn ends sit below the field,
  write no row, and the parent's `FINAL_ANSWER` closes its run — see the
  older forks above.)
- **Reconnect, or a root's first load, with a failed `/subagents/`
  snapshot** (HTTP or network error, or a response discarded twice by a
  fetch-generation change, §8.2): the run states stored before (during a
  previous connection, or received while the root was not loaded) stay
  applied. An agent
  whose run closed during the outage shows running (robot, Stop button,
  open-run polling) until its next `agent_run_state` or the root's next
  load. Today the same failure shows every agent stopped instead.
