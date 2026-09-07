# Nested Subagents (Multi-Level Agents) — Design

**Date:** 2026-08-08 (revised 2026-08-15 for Codex multi-agent v2; revised
2026-09-05 against code state `7b53b36e` — resumable-subagent support,
provider homes, Codex canonical rollouts)
**Status:** approved after independent adversarial reviews; ready for implementation
**Provider scope:** both providers. Claude Code: link creation + display.
Codex: parenthood flattening + display (its v2 protocol already creates the
links, see §2.6/§10).

## 1. Problem

An agent can spawn agents at any depth: the root session spawns a subagent
(depth 1), which spawns its own subagents (depth 2), and so on — Claude
Code's `Agent`/`Task` tool and Codex's multi-agent v2 `spawn_agent` both do
this. TwiCC only handles depth 1. Deeper agents get a `Session` row but no
usable filiation surface: no "View Agent" button in the launcher's panel, no
tab, no running indicator, no stop control.

Goal: a subagent's panel shows the same "View Agent" button as a root session,
at any depth, live or historical, on both providers. The button opens a
regular agent tab — no visual distinction between levels anywhere in the UI
(user decision).

Reference cases, everything below verified against their files/rows:

- **Claude Code:** session `9e1cfb65-c745-43fc-bc5a-8a999804bf92` in project
  `-home-twidi-dev-pickatube` — 12 subagents in the DB today: 8 depth-1
  agents (the session kept running after the first analysis) and 4 depth-2
  agents, spawned two each by `a12d0d…` and `acc4e760…`.
- **Codex:** session `01a004e1-7a55-76e2-8130-f7398710088e` — one depth-1
  agent (`01a004e1-c168…`, slug `Zeno`) which spawned one depth-2 agent
  (`01a004e1-e350…`).

## 2. Verified facts

### 2.1 Storage is flat (Claude)

All subagent files, whatever their depth, live in the root session's folder:
`<project>/<root_session_id>/subagents/agent-a<hex>.jsonl`. There is no
per-depth nesting on disk. TwiCC therefore already creates a `Session` row for
every depth (`type=SUBAGENT`, `parent_session` = root session).

### 2.2 The `meta.json` sidecar carries the filiation (Claude)

Each subagent file has a sibling `agent-<id>.meta.json`, written at spawn
time:

```json
{"agentType": "Explore", "description": "Statistics blast radius",
 "toolUseId": "toolu_01SuUnibKKoEk79X4uRQkrpm",
 "parentAgentId": "acc4e760a52c96577", "spawnDepth": 2}
```

- `toolUseId` — the exact spawning `tool_use`.
- `parentAgentId` — the launching agent; **absent at depth 1**.
- Field survey on this machine (2026-09-05): 619 sidecars, 27 with
  `parentAgentId` (i.e. depth ≥ 2). `agentType`/`description` are always
  present, `toolUseId`/`spawnDepth` in all but one, `model` in 537; two
  recent sidecars carry a new `stoppedByUser` key (ignored). Sidecars exist
  since ~2026-05-17; older sessions have none.
- Workflow agents (`subagents/workflows/<run_id>/`) have their own sidecar
  shape (`{"agentType": "workflow-subagent", "spawnDepth": 1}`, no
  `toolUseId`, no `parentAgentId`) — out of scope, unchanged.
- TwiCC reads none of these today.

### 2.3 What the launcher's transcript contains (and does not) (Claude)

Unlike root session files, subagent JSONL lines carry no `toolUseResult`
envelope for agent-spawn results (0 of 87 `tool_result` lines in
`agent-acc4e760….jsonl`, CLI 2.1.222; 0 of 25 in `agent-a4a57eb7….jsonl`,
CLI 2.1.259; the lone envelope seen in a subagent file — a Bash
`Error: Exit code 2` in `agent-ac4ff7c3….jsonl` — carries no `agentId`).
`extract_agent_info_from_tool_result` therefore returns `None` for every
spawn in them. The child's identity appears only as:

- **Async spawn** — the single ack `tool_result`, plain text:
  `"Async agent launched successfully. (…)\nagentId: ad387380c886bae1d (…)"`.
  This ack is also the only backgroundness signal.
- **Sync spawn** — nothing. The single `tool_result` is the child's final
  report (e.g. 21 286 chars for `toolu_014M2Jt…`), with no id anywhere. What
  is byte-identical to the child's first user message is the spawning
  `tool_use`'s `input.prompt` (verified on both sync spawns of the reference
  session: 1324 and 2353 chars, exact matches), so prompt matching works —
  match the tool_use prompt, never the tool_result text.

### 2.4 Where an async nested completion is persisted (Claude) — CLI-dependent

Two behaviours, verified on two real sessions:

- **CLI 2.1.222 (reference session `9e1cfb65…`):** the completion is
  **never written to the launcher's transcript** — no second `tool_result`
  on the spawning `tool_use`, no `<task-notification>` (the notification is
  injected into the launcher's live LLM context without being persisted).
  The only persisted trace is the root session's `queue-operation` lines
  below. This session still has zero nested links today.
- **CLI 2.1.259 (session `e7932c3f…`, 2026-09-05):** the completion **does
  land in the launcher's transcript**, as an `attachment` line
  (`attachment.type == "queued_command"`, `attachment.prompt` = the full
  `<task-notification>` XML **with** `<tool-use-id>`). TwiCC's existing
  attachment-variant rewrite (claude_code/compute.py:1559) turns it into a
  `tool_result` on the launching tool_use carrying
  `toolUseResult = {agentId, isAsync: true}`, so
  `create_agent_link_from_tool_result` creates the **launcher-owned link at
  completion time** with `is_background=True` — that is how the 3 nested
  links of that session exist in the DB with no dedicated code. The root
  `queue-operation` lines are written as well (48 in that root).

Consequences: on recent CLIs the parent-side counting rule concludes
natively for nested async agents (the rewritten attachment is the second
result), and the link exists — but only **once the child finishes**. Until
then there is no button and no pulse, which is what sources ① (ack) and ②
(sidecar) of §3 fix. On 2.1.222-era transcripts nothing ever links or stops
without the root `queue-operation` mining (§4.6), which is therefore kept
as the historical path — idempotent with the attachment path (both resolve
to the same `(owner, tool_use_id, agent_id)`).

The root session's `queue-operation` shape:

```json
{"type": "queue-operation", "operation": "enqueue", "timestamp": "…",
 "sessionId": "<root>", "content": "<task-notification>\n<task-id>ae1df3ede72f6b6da</task-id>\n<tool-use-id>toolu_01FGG…</tool-use-id>\n<output-file>…</output-file>\n<status>completed</status>\n<summary>Agent \"…\" finished</summary>\n<note>…</note>\n<result>…full report…</result></task-notification>"}
```

- `<task-id>` = the nested child; `<tool-use-id>` = the spawning `tool_use`,
  which lives in the *launcher's* transcript.
- A matching `"operation": "remove"` line follows on delivery.
- These lines already become root `SessionItem`s (`ItemKind.SYSTEM`), so they
  survive CLI transcript sublimation and are available to both compute paths.
- The `<note>` warns the same task-id may notify more than once (SendMessage
  resume) — handling must be idempotent.

On 2.1.222-era transcripts the "background agent is done after 2
tool_results" rule (`check_agent_naturally_stopped`, the full-recompute
result counter, the frontend `isAgentRunning` / `tool_state` handler) can
never conclude for an async nested agent: without mining the
`queue-operation` lines, such an agent shows "running" forever once linked.

### 2.5 Current DB state (Claude, 2026-09-05)

All 12 agents of the reference session are `Session` rows parented to the
root. `AgentLink` has 8 rows for it (the depth-1 agents, owned by the root)
and still **zero** nested links. Across the DB, Claude has exactly 3
subagent-owned links — the 2026-09-05 session of §2.4, created at completion
by the attachment path; no sync nested spawn and no launch-time link exists
anywhere. Costs are already correct: `recalculate_costs` sums items over
`parent_session=root`, which covers every depth exactly once.

### 2.6 Codex multi-agent v2 (commit `4ed8215b`, 2026-08-15)

Codex 0.14x's v2 subagent protocol is already ingested by TwiCC, and its
handling differs from Claude's on two axes (verified on the Codex reference
session's DB rows):

- **Links already exist at every depth, in the target format.** The v2
  pairing (`sub_agent_activity` events, parsed per transcript) creates the
  `AgentLink` with **owner = the launching session** — the depth-2 link is
  owned by the depth-1 agent (`session_id = 01a004e1-c168…`). Nothing to
  build: Codex link creation is done, and it validates the ownership model
  of this design.
- **Parenthood is chained, not flat.** Codex writes `parent_thread_id` (the
  *direct* parent) in each rollout's first line, and ingestion stores it
  verbatim: the depth-2 agent's `parent_session` is the depth-1 agent, not
  the root. This was never a deliberate decision — before 2026-08-15 no
  chained row existed in the DB — and it breaks the single-level
  assumptions: the subagent route resolver 404s on a depth-2 agent, and
  `recalculate_costs` misses depth-2 items in the root's totals. §3
  decision 1 unifies this.

Completion detection also differs: a v2 subagent can answer through
`send_message` and stay alive, never producing the second tool_result the
parent-side counting rule waits for. The v2 commit therefore added
`subagent_turn_boundary` (compute hook: the subagent's own
`task_complete`/`task_started` drive its `last_stopped_at` on the live
path), surfaced as `agent_stopped_at` in the links payload
(`serialize_agent_links`, session_queries.py:117) and consumed by the
frontend behind the per-provider gate `agentRunEndsOnSubagentIdle()`
(baseHelpers.js:1507 — Codex `true` at codex/toolHelpers.js:1877, default
`false`). §3's trust rule builds on this existing mechanism.

The 0.151 canonical rollout format (paginated history, automatic legacy
migration — `CODEX_COMPUTE_VERSION` 48/49) changed nothing here:
`extract_session_meta` still reads `parent_thread_id` from the first line
(codex/initial_sync.py:131), rows are still chained (3 in the DB), and the
v2 linkage runs in both the batch and live canonical readers.

### 2.7 Code landed since the first revision (2026-08-15 → 2026-09-05)

Three commits shipped pieces this design previously planned from scratch —
`6295191a` "track resumable subagents and their orphan task-notifications"
(the first four bullets), `16cf5956` "read the provider home dirs from the
.env" (provider homes), `ef8505c2`/`69177fd4` (canonical rollout support,
sliced compute apply). They are now **prerequisites to build on, not to
duplicate**:

- **Sidecar reading exists:** `_agent_launch_tool_use_id_from_sidecar(session_id, task_id)`
  (claude_code/compute.py:379) resolves a subagent's `toolUseId` from
  `agent-<task_id>.meta.json`, anchoring the `subagents/` dir on the
  notifying session's `Session.file_path` under `claude_projects_dir()` —
  including the **nested case** (notification in a subagent's own file → the
  same flat dir). It reads only `toolUseId`; `parentAgentId`/`spawnDepth`
  are still unread.
- **Orphan notifications** (a `<task-notification>` with `<task-id>` but no
  `<tool-use-id>` — an agent that re-woke on its own) are rewritten into a
  tool_result on the sidecar-resolved tool_use with
  `toolUseResult = {agentId}` (deliberately no `isAsync`), in both the
  user-message and the attachment arrival shapes (compute.py:1509/1641).
  Unresolved ones classify as SYSTEM via `origin.kind == "task-notification"`.
- **Claude `subagent_turn_boundary`** (compute.py:~2300): `stop_reason ==
  "end_turn"` → idle, a CLI-injected wake-up (string content +
  `origin.kind`) → working again. Live path only (compute_base.py:3450,
  written at ~3737): the subagent's own file now drives its
  `last_stopped_at` between recomputes. The frontend gate
  `agentRunEndsOnSubagentIdle()` is still **not** overridden for Claude.
- **Monotonic stop guards:** `check_agent_naturally_stopped`
  (compute_base.py:1877) and apply step 11 both `.exclude(last_updated_at__gt=stopped_at)`
  — a stale parent-side stop never re-freezes a subagent with newer
  activity.
- **Provider homes:** every Claude path goes through
  `twicc.provider_homes.claude_projects_dir()` at call time;
  `ClaudeCodeHelpers.PROJECTS_DIR` no longer exists. Tests point the homes
  at `tmp_path` with the `provider_home` fixture (tests/conftest.py:32).
- **Apply pipeline:** `apply_session_complete` returns a
  `ComputeApplyResult(outcome, folded_ancestor_id)` behind a revision guard;
  large item batches are pre-applied in slices by the DB writer. Message
  keys (`agent_links_to_create/update/delete`, `agent_stopped`, …) are
  unchanged.

## 3. Design decisions

1. **Flat parenthood, unified across providers.** `Session.parent_session`
   is the root session for every depth, on both providers — the tree anchor,
   never the direct parent (same rule as workflow agents: "parent = owner of
   the `subagents/` folder"). Routes, tabs, cost aggregation, and the
   subagent API resolver all keep working unchanged, with **one level of
   `parent_session_id` = the whole tree** as a load-bearing invariant.
   - Claude already stores it this way (the storage is flat).
   - Codex must be aligned: at ingestion, walk the `parent_thread_id` chain
     up to the root and anchor `parent_session` there (§4.9). The direct
     parent is not lost — it lives in `AgentLink` (owner = launcher), which
     is also what any future hierarchy view reconstructs the tree from.
   - A data migration re-points the existing chained Codex rows (3 rows
     under two roots today: `019ff10a-4900…` and `01a004e1-7a55…`) to their
     root and recalculates the affected roots' costs — which also fixes the
     depth-2 cost gap of §2.6. Recalculate former parents after all moves,
     since their stored subagents costs previously included those children.
     Refresh affected stored project totals too.
2. **Real filiation lives in `AgentLink`.** One row per spawn:
   `session` (owner) = the launching session (root *or* subagent),
   `agent_id` = the launched agent. The existing model fits; **no migration,
   no new column**. Unlimited depth by construction.
3. **No UI distinction between levels.** A nested agent opens as a plain
   agent tab; no badge, no "launched by" header line.
4. **Layered link sources**, in priority order (per owner-session compute):
   1. async ack text, strictly after structured `toolUseResult` identity (in-transcript, gives child id + `is_background=true`
      at **launch** time — the only source that makes the button/pulse
      appear before the child finishes);
   2. `meta.json` sidecar (`parentAgentId` + `toolUseId`; deterministic;
      only source for sync nested spawns; absent before ~2026-05) — built
      by **extending** the existing `_agent_launch_tool_use_id_from_sidecar`
      (§2.7), not beside it;
   3. prompt matching. The matching machinery exists, but only on the live
      path — today's full recompute derives links solely from
      `toolUseResult` (compute_base.py:2625). Wiring prompt matching
      (and the other two sources) into the full recompute is a **new
      integration**, not a re-plug. Accepted limit: a pre-sidecar sync
      nested agent is usually not linked by the child-side live path (its
      fallback targets the root and may miss); the launcher-side recovery
      (§4.4) may still catch it live, and the launcher's recompute — which
      the 109 bump (§4.8) performs for all history — links the rest.
5. **Completion of async nested agents** has two persisted sources (§2.4):
   the launcher-side attachment notification (recent CLIs — already handled
   by the existing rewrite, nothing to build) and the root session's
   `queue-operation` lines (historical transcripts — §4.6). Both resolve to
   the same link key; the queue path is kept for history and redundancy.
6. **`is_background`**: preserve explicit launch input and structured-result
   behavior. An async ack or matching launch notification upgrades the flag
   to `true`, including an existing sidecar/prompt link. A sidecar alone
   adds no background flag. A SendMessage continuation never changes the
   original launch flag.

### 3.1 Write authority (non-negotiable invariant)

A full recompute of a session rebuilds its `AgentLink` set and applies a
**diff**: create missing, update changed, delete rows absent from the rebuilt
set (`agent_links_to_delete`). An unchanged link costs zero writes. Therefore:

- Every link owned by session X must be re-derivable from X's own compute
  inputs (its transcript + sidecars + sibling rows + persisted root completion evidence). Otherwise X's next
  recompute deletes it. This is why the link is always created under the
  *launcher's* ownership and why the launcher's recompute learns sources
  ①②③ above.
- The root's compute never creates links it cannot own; from
  `queue-operation` lines it only (a) backfills a link **owned by the
  launcher** when missing — acceptable because the launcher's recompute
  re-derives the same link, so the diff keeps it — and (b) stamps the child
  `Session.last_stopped_at` via the existing `agent_stopped` channel.
- **Backfill transport.** `AgentLink` has no unique constraint (plain
  indexes only), so `bulk_create(ignore_conflicts=True)` deduplicates
  nothing — routing a non-owned backfill through the recompute's own diff
  sets (`all_agent_links` → `agent_links_to_create`) would create one
  duplicate row per root recompute, invisible to the launcher's diff (keyed
  by `(agent_id, tool_use_id)`, one id per key). Therefore: the root's
  recompute carries backfills in a **dedicated payload channel** (e.g.
  `agent_links_backfill`), applied in `apply_session_complete` with a
  `get_or_create` on `(session, tool_use_id, agent_id)` — the same
  existence-checked pattern the live path already uses. Injecting non-owned
  links into `all_agent_links` is forbidden. No unique constraint is added
  (keeps the no-migration decision; the existence-checked writes are the
  protection). The owned-create apply path must also check existence inside
  the atomic writer. Otherwise a root backfill inserted after the launcher's
  compute snapshot races with its owned `bulk_create` and creates duplicates.
  Test both arrival orders. The revision guard does not detect another
  owner's link writes.
- **`last_stopped_at` trust is per-provider.** Any full recompute — for
  ANY provider — stamps the field with the file mtime (compute_base.py:2818),
  including on a still-running agent whose file is momentarily quiet, and
  can even write `None` when mtime is falsy (DB check: the nested agents of
  both Claude reference sessions carry identical batch-stamp values, not turn
  boundaries). Its live value is accurate only where a transcript-derived
  boundary (`subagent_turn_boundary`) maintains it — Codex, and Claude since
  §2.7. The frontend gate `agentRunEndsOnSubagentIdle()` is therefore a
  **necessity trade-off, not an accuracy certificate**: Codex opts in because
  its parent-side counting rule can never conclude (a v2 subagent may never
  produce the second result), accepting the recompute pollution; Claude,
  whose counting rule does conclude (§2.4), stays out. Design consequence:
  `agent_stopped_at` (the child's `last_stopped_at`) is **never** an
  unconditional signal — it is consumed only behind the provider gate — and
  the only unconditional idle source is the per-link `stopped_at` derived
  from a persisted completion notification (§4.6/§4.7). (Making the
  recompute honour the boundary would make the field trustworthy everywhere
  — out of scope.) For Claude, completion is
  decided by result counting plus the persisted `queue-operation` items
  (§4.6, §5.3), delivered as a **per-link** `stopped_at` — the only
  unconditional idle source; `agent_stopped_at` stays in its own gated field
  (§5.2). The `agent_stopped` stamp remains useful metadata but is advisory
  for Claude.

## 4. Backend changes

All in the generic compute (`src/twicc/providers/compute_base.py`) with
Claude-specific hooks in `src/twicc/providers/claude_code/compute.py`, unless
noted.

1. **Sidecar reader — extend the existing one.**
   `_agent_launch_tool_use_id_from_sidecar` (§2.7) already locates the
   `subagents/` dir from a session's `file_path` under
   `claude_projects_dir()` (nested case included) and reads `toolUseId`.
   Move that logic into a `subagent_meta.py` module exposing the full
   sidecar (`toolUseId`, `parentAgentId`, `spawnDepth`) for one agent or for
   the whole dir (orjson, tolerant), and make the existing helper a thin
   caller of it (its tests in `test_claude_subagent_lifecycle.py` must keep
   passing). Never resolve paths through a constant — always
   `claude_projects_dir()` at call time (provider-homes rule). Generic
   compute sees it through provider hooks returning `None`/`{}` by default
   (Codex unaffected).
2. **Effective launcher in the live subagent path.** `subagent_needs_link`
   (compute_base.py:3342) currently targets `session.parent_session_id`; the
   target becomes `meta.parentAgentId or session.parent_session_id`.
   Keep depth-1 sidecar tool ids too. If an authoritative tool id is not yet
   synced, defer linking. Never fall back to another same-prompt tool use. The
   `is_agent_link_done` cache keys on `(launcher_id, child_id)`. When the
   meta provides `toolUseId`, create the link directly: find the tool_use
   line in the launcher's items (`content__contains=tool_use_id`, existing
   pattern) — no prompt matching needed. **Owner-agnostic done check:** the
   in-memory `is_agent_link_done` cache keeps its `(launcher, child)` pair
   key, but the DB existence check that guards the path is owner-agnostic —
   skipped as soon as ANY `AgentLink` with `agent_id == child` exists,
   whoever owns it — otherwise a Codex v2 depth-2 agent (whose link
   is created by the v2 pairing under its launcher, while the stored parent
   is the root after flattening) would re-run the root-targeted prompt match
   on every live batch of its file (a cheap no-match today, but a permanent
   one).
3. **Async ack parser (new Claude hook)**, alongside
   `extract_agent_info_from_tool_result`: on a `tool_result` whose text
   matches the ack shape (`Async agent launched successfully` +
   `agentId: <hex>`), return `(tool_use_id, agent_id, is_background=True)`.
   Wired in the live path and the full recompute of any session, strictly
   after `toolUseResult` in priority: the parser only fires when
   `extract_agent_info_from_tool_result` returns nothing. In practice that
   means subagent transcripts; if a historical root line happens to carry
   the ack without the `toolUseResult` envelope, the parser produces the
   same link the notification path would — benign.
4. **Sibling-aware race recovery.** `create_agent_link_from_tool_use`
   (compute_base.py:2134) currently matches candidates among
   `parent_session_id == session_id`; candidates become the sessions whose
   files live in the same `subagents/` dir (for a root: its subagents,
   unchanged; for a launcher subagent: its siblings via
   `parent_session_id == session.parent_session_id`). Its live call site is
   gated `session.type == SessionType.SESSION` (compute_base.py:3601) — that
   gate must be relaxed to run for subagent sessions too, otherwise the
   recovery never executes for a launcher subagent. Together these cover the
   child-file-synced-before-tool_use race at any depth. **Sidecar first:**
   widening the candidates to the whole flat dir makes identical prompts
   (a root spawn and a subagent spawn with the same text) ambiguous, so the
   recovery matches a candidate on its sidecar `toolUseId` when the sidecar
   exists, and falls back to unique prompt equality only for candidates without authoritative tool identity
   (§3 decision 4 ranks ② above ③ — this pass must honour that order).
5. **Launcher full recompute rebuilds its links** from sources ①②③ and
   matching persisted root completion evidence, so the diff keeps them (§3.1).
   Live and full matching share metadata priority and ambiguity rules.
   Existing links owned by this session never exclude its own candidates.
   A sidecar naming another owner/tool cannot enter prompt fallback.
   Same pass feeds `agent_tool_result_counts`, so the 1-result rule stamps
   sync nested agents as stopped.
6. **Root compute mines `queue-operation` lines** (live + full recompute) —
   the historical/redundant completion path (§2.4: on recent CLIs the
   launcher-side attachment rewrite already produces the link and the second
   result; both paths are idempotent on the same key). On
   `operation == "enqueue"` with a `<task-notification>` content, parse via
   the existing `_parse_task_notification`, then:
   - resolve the owner of `<tool-use-id>`: **sidecar first** — the child's
     `agent-<task-id>.meta.json` gives `parentAgentId` (the owner; absent at
     depth 1 → the owner is the root) and `toolUseId` (to confirm); only
     when the sidecar is absent, scan the
     items of the root and its subagents for the tool_use, restricted to
     `ASSISTANT_MESSAGE`/`CONTENT_ITEMS` kinds (a 2.1.222 root carries 113
     `enqueue` lines — an unbounded `content__contains` scan per line is not
     acceptable);
   - backfill the `AgentLink` if missing (owner = that session, through the
     dedicated channel of §3.1) — **only when the resolved tool_use is an
     agent-spawn tool** (`AGENT_TOOL_NAMES`, mirroring the existing
     extraction guard): a SendMessage-continuation notification carries the
     SendMessage `tool_use_id` (see the compute-107 comment,
     compute_base.py:1961) and must never receive a link, which the
     launcher's recompute could not re-derive; a Monitor notification (the
     reference root has one) is refused by the same guard;
   - stamp the child stopped **through the existing channels** — the
     `agent_stopped_updates` list on the live path, the `agent_stopped`
     payload list applied by step 11 on the full recompute (idempotent
     re-stamp on repeated notifications) — so the
     established consumers keep firing — the `session_updated` broadcast
     (sessions_watcher.py:831) that feeds the frontend `updateSession` safety
     net, and the `_after_agents_stopped` hook (sessions_watcher.py:337, the
     Codex hold release) — **and** broadcast the new `agent_stopped` WS
     message (`agent_session_id`, `stopped_at`, `root_session_id`) that §5.3
     consumes to stamp the link. Two messages, one stamp, no second channel
     of truth;
   - keep the compute-107 guard: such a notification must not flip the
     original link's `is_background` either.
   `remove` lines are ignored.
7. **`subagents_state` becomes a tree payload.** Same URL
   (`/api/projects/<id>/sessions/<root_id>/subagents/`, still root-only), the
   response now returns the links of the root *and* of all its subagents —
   one level of `parent_session_id` is the whole tree on both providers
   (§3 decision 1), so the owner collection stays single-level.
   `serialize_agent_links` (session_queries.py:117) already returns
   `agent_stopped_at` (the child's `last_stopped_at`, the gated signal,
   §3.1) — keep it **in its own field**, and add on top: `owner_session_id`
   (= `link.session_id`), `stopped_at` (the matching completion
   notification's timestamp, else `null` — **never** filled from
   `agent_stopped_at`), and a server-computed `running` boolean:
   - `false` when the link's `started_at` predates the root's lifecycle
     cutoff (`max(root.last_started_at, root.last_stopped_at)`) — every
     depth dies with the root process, so the cutoff the frontend applies
     today to root-owned links (data.js `getSessionCutoffMs`) applies to the
     whole tree; this is what stops a 1-result background agent from pulsing
     forever after a root restart;
   - else result_count (owner's `ToolResultLink` rows) vs required (2 if
     background), short-circuited by a persisted completion notification (a
     root `queue-operation` enqueue item whose `<tool-use-id>` matches the
     link — one scan of the root's queue-operation items per request) and,
     **only for providers whose gate says so** (§3.1), by `agent_stopped_at`.
     The backend gate does not exist yet — add
     `BaseProviderHelpers.subagent_idle_trusted: bool` (`False`; `True` on
     `CodexHelpers`), the mirror of the frontend
     `agentRunEndsOnSubagentIdle()`; the two must stay equal (§8 lists a
     test pinning the Codex/Claude values).
   This keeps one fetch at root load, populates every owner's cache
   reactively when the response arrives, and spares the frontend from needing every
   launcher's tool-states just to decide the pulse.
   The WS `agent_link_created` broadcast keeps its shape;
   `parent_session_id` may now name a subagent (the owner), and the message
   gains a `root_session_id` field so the frontend can resolve the provider
   and the navigation target without requiring the owner's session row to be
   loaded.
8. **Compute version bump:** `CLAUDE_CODE_COMPUTE_VERSION` 108 → 109
   (settings.py:392) so the normal background recompute backfills every
   existing session. `CODEX_COMPUTE_VERSION` untouched — the Codex change
   (§4.9) is an ingestion-time anchor plus a data migration, not a compute
   rule.
9. **Codex parenthood flattening (§3 decision 1).** Three pieces:
   - *Initial sync* — `_sync_subagents` (providers/codex/initial_sync.py:251;
     the child row is built at ~220 with `parent_session_id=entry.parent_session_id`)
     resolves subagents in topological order, but "resolvable" means *pushed
     to the async DB writer*, not *written* (`resolvable_parent_ids.add`
     right after `sync_queue.put`, :317-318): a DB walk from the producer
     thread may not see the parent yet. Resolve the root **in memory** from
     the run's own entries — a `{session_id: parent_session_id}` map over
     every new entry of the run, including top-level roots mapped to `None`.
     A root queued by this producer is proven even before its DB write.
     Walk this map first and query the DB only for ancestry outside the run (walking its stored
     `parent_session_id`, which is already flat after this change). Then
     create the child with `parent_session = root`.
   - *Live watcher path* — same anchoring where the generic watcher verifies
     the subagent's parent (providers/sessions_watcher.py:582, fed by the
     Codex `parse_session_file`'s `meta.parent_session_id`,
     codex/sessions_watcher.py:178): if the referenced parent is itself a
     subagent, walk up to its root before writing.
   - *Data migration* — re-point the existing chained rows
     (`Session.objects.filter(provider='codex', type='subagent',
     parent_session__type='subagent')`) to their root ancestor, then
     recalculate the affected roots and former parents after all moves
     (fixes the §2.6 cost gap), then refresh affected project totals.
     Per repo rule, the migration duplicates the walk logic inline — no
     app-code import in migrations.
   - All ancestry walks use visited sets. Valid trees have no depth cap.
     Missing ancestry defers ingestion; cycles never produce an intermediate
     root. Migration tests execute the real migration with historical models.
     Initial-sync and watcher tests invoke their integration entry points.
   - *Verification pass* (implementation-time, no code expected): audit
     every Codex-side reader of `parent_session_id` for direct-parent
     assumptions — the v2 pairing does not use it (per-transcript events),
     the route resolver and cost aggregation become correct again, the
     share descendant check keeps working (one level = whole tree).

## 5. Frontend changes

1. **`ToolUseContent.vue`** — drop the `!parentSessionId` gate on the View
   Agent block (line 954). Everywhere the root session id is needed
   (navigation target, `stopSubagent` first argument, synthetic-state
   provider resolution), use `props.parentSessionId || props.sessionId`. The
   View Workflow gate (line 997) stays root-only (out of scope).
2. **`fetchSubagentsState`** (stores/data.js:4490 in the working tree —
   HEAD numbers differ by ~+55 lines because of uncommitted user changes in
   this file; implementers anchor on the working tree) consumes the tree
   payload: `setAgentLink(entry.owner_session_id, …)` — the cache is
   already keyed by owner session, no store refactor. The link cache entry
   already holds `stoppedAt`, fed today from `agent_stopped_at`
   (data.js:4262/4502). That changes: `stoppedAt` is fed **only** from the
   per-link `stopped_at` (a persisted completion), and `agent_stopped_at`
   moves to its own entry field `agentStoppedAt`, consumed only behind the
   provider gate (§5.3) — never merged (`??`) into `stoppedAt`, or the
   mtime-polluted value would become an unconditional idle signal (§3.1).
   Add `startedAt`. Synthetic process states are created from the
   server-computed `running` (which already includes the root cutoff and
   the gated `agent_stopped_at`, §4.7); the client keeps its own cutoff
   check as a belt. Provider reference for `setSyntheticProcessState`
   (data.js:4396) stays the root.
3. **Running-state short-circuit.** `isAgentRunning` (ToolUseContent:840)
   and the `tool_state` WS handler (useWebSocket.js:1535) keep result
   counting as the primary signal, short-circuited by the existing
   `agentReportedIdle` computed (ToolUseContent:834) — extended: honor
   `agentLink.stoppedAt` **unconditionally** (it now only ever holds a
   persisted completion, §5.2), and keep the `agentRunEndsOnSubagentIdle()`
   provider gate for the two `last_stopped_at`-derived sources — the cache
   entry's `agentStoppedAt` and the live `getSession(agentId).last_stopped_at`
   (§3.1 trust rule). On the live path, the new `agent_stopped` WS message
   (§4.6) sets `stoppedAt` on the link (found through the reverse index
   below) and removes the agent's synthetic process state — the Claude
   counterpart of the `session_updated` flow Codex already rides. The
   pre-existing `updateSession` safety net (stores/data.js:1501-1509),
   which clears a subagent's own synthetic state from
   `session_updated.last_stopped_at`, **stays untouched**. Without the
   short-circuit, async nested Claude agents would pulse forever (§2.4).
4. **Reverse link index.** `SessionHeader.canStopAgent` currently finds the
   link via `session.parent_session_id`; for nested agents the owner is the
   launcher. Add a store getter/index `agentId → {ownerSessionId, toolUseId,
   toolUseLineNum, isBackground, startedAt, stoppedAt, agentStoppedAt}` —
   the same fields as the cache entry plus the owner — fed by
   `setAgentLink`, and use it
   here, in the `agent_stopped` WS handler, and anywhere else the owner must
   be found from the agent. One more consumer: `_cleanStaleChildSynthetics`
   (data.js:4432) walks `agentLinks[root.id]` only — root-owned links — so
   after a root restart the synthetic states of nested agents would survive.
   It must walk every link of the tree (all owners whose root is the
   restarted session, via the index or the owner-keyed cache).
5. **Panel lifecycle and comments.** Tabs, routes, labels, and virtual
   scrolling stay flat. Agent pulse uses the root cutoff; ordinary tool pulse
   retains its owner cutoff. Comment `subagentToolLineNum` remains a ROOT
   transcript coordinate, found by climbing launcher links to the root spawn.
   Keep comment context reactive when links load after editor mounting.
6. **Fetch and event ordering.** Store root identity with links so cleanup
   works without launcher Session rows. Keep pending completion evidence when
   a stop arrives before its link. A delayed REST response cannot erase newer
   WS state, and `running=false` removes an existing synthetic state.
   Tree fetch completion is asynchronous; direct nested URLs must work even
   when the nested panel mounts before root links arrive.

## 6. Sharing

- `include_subagents` **on** → every agent, every depth, is exposed (link
  lists, items, tool endpoints). **Off** → none, as today. No per-depth
  option.
- The share subagent-links endpoint returns the same tree payload as §4.7.
- **Snapshot freeze rule:** a nested agent's spawning `tool_use` line lives
  in the launcher's transcript and cannot be compared to the root's frozen
  line. Rule: a nested agent is visible iff its **depth-1 ancestor** is
  visible (that ancestor's root `tool_use` line ≤ frozen line). A frozen-out
  depth-1 agent hides its whole subtree. Subagent items themselves stay
  un-clamped (existing design).
- **Implementation warning:** the existing ancestor walk,
  `_crosses_allowed_root_child` (share/session_views.py:61), climbs
  `parent_session_id` — which under the flat model is always the root, so a
  nested agent would silently reduce to `sub.id in allowed_ids` (root-owned
  links only) and 404 in snapshot mode. The depth-1 ancestor must be
  resolved through the **`AgentLink` ownership chain** (owner of the link
  that spawned the agent, climbed until the owner is the root), not through
  `parent_session_id`.
- Ownership walks have no depth cap and reject cycles or missing links.
- Live shares receive nested links AND persisted completion messages.
  The share shim mirrors reverse lookup, stop handling, and freshness rules.
- Root item/result snapshot ceilings remain intact. Nested item contents
  retain the existing un-clamped behavior.

## 7. Stopping a nested agent

This section concerns Claude Code (Codex's stop capability for spawned
agents is whatever the provider supports today — unchanged by this design).
All agents run inside the root session's single CLI process; the stop chain
(WS `stop_subagent` → root manager → SDK `stop_task(agent_id)`) is wired for
every depth, with the frontend passing the root id. Whether the CLI honours
`stop_task` for a depth ≥ 2 agent is unknown (undocumented CLI internals) —
**must be tested live during implementation**. If it does not, hide the Stop
button for nested agents (reverse index gives the depth for free via the
owner); View Agent and the running state are unaffected.

## 8. Testing

- **Backend units** (pytest, fixtures shaped after the reference session):
  sidecar parsing (incl. malformed/absent), ack parsing, queue-operation
  parsing and idempotence, link creation from each source, the
  effective-launcher live path, sibling race recovery (incl. the relaxed
  3601 gate and sidecar-first matching), launcher full recompute re-deriving links (diff = zero writes
  on unchanged data), backfill channel dedup (repeated root recomputes
  create no duplicate rows), the `AGENT_TOOL_NAMES` guard on backfill
  (SendMessage-continuation notifications create no link), root recompute
  stamping nested stops, tree payload serialization (owner + running from
  counting/notifications, with `agent_stopped_at` honoured only for
  gate-enabled providers), share freeze rule for nested agents via the
  `AgentLink` ownership chain.
- **Codex flattening (§4.9):** initial-sync anchoring (a child of a child
  gets `parent_session = root`), live-path anchoring, the data migration
  (chained rows re-pointed, root costs recalculated), and the tree payload
  over the Codex reference shape (the depth-2 link owned by the depth-1
  agent appears with the right `owner_session_id`).
- **Non-regression:** depth-1 flows unchanged (toolUseResult path first, ack
  parser strictly a fallback), SendMessage-continuation guard (compute 107),
  workflow agents untouched.
- **Frontend:** reverse index, `stoppedAt` short-circuit + `agent_stopped`
  WS handling, owner-aware navigation.
- **Manual:** live nested run (async + sync), View Agent at depth 2, stop
  test (§7), full recompute over the reference session after the version
  bump.

## 9. Non-goals

- No visual nesting indicators.
- No schema migration (no new column, no new constraint). The only
  migration is the §4.9 **data** migration re-pointing the chained Codex
  rows.
- No change to workflow agents or their `View Agent` path. Note: relaxing
  the §4.4 gate makes the recovery pass run over workflow-agent sessions too
  (type SUBAGENT); if a workflow agent's transcript ever carries an
  agent-spawn tool_use, the resulting link (owned by the workflow session)
  is correct filiation, not a regression — covered by a test (§8).
- No change to Codex link creation (§2.6: already correct) nor to its
  completion mechanism (`subagent_turn_boundary` /
  `agentRunEndsOnSubagentIdle` stay as the v2 commit built them).

## 10. Codex scope summary

Originally deferred; brought into scope on 2026-08-15 when the multi-agent
v2 commit (`4ed8215b`) made nested Codex agents real. What this design does
and does not change for Codex:

**In scope:**
- Parenthood flattening at ingestion + data migration (§3 decision 1,
  §4.9) — the only Codex-specific backend work.
- The shared display/API work (tree payload §4.7, frontend §5, share §6)
  applies to both providers by construction.

**Already done by the v2 commit (untouched):**
- Link creation at every depth (owner = launcher).
- Completion detection (`subagent_turn_boundary` → `last_stopped_at` →
  `agent_stopped_at` → `agentRunEndsOnSubagentIdle` gate).

**Historical note:** Codex v1 nominally supported nested subagents
(`parent_thread_id` = direct parent) but no chained row ever existed in the
DB before 2026-08-15, so the flattening migration only concerns v2 test
data (3 rows under two roots as of 2026-09-05).


## 11. Review corrections and acceptance evidence (2026-09-07)

Independent backend, frontend, and Codex reviews identify errors in the earlier
implementation sketches. The revised plan contains semantic tasks rather than
those sketches. Its tests must exercise the actual compute, watcher, migration,
API, WS, and rendered UI boundaries.

Additional binding checks:

- Ack extraction falls back only when structured identity is absent. A valid
  envelope with an existing link still wins over conflicting ack text.
- Meta-first/ack-second upgrades background state before result counting.
- Same-prompt races never override authoritative metadata. Ambiguous prompt-only
  history waits for stronger evidence instead of inventing ownership.
- Root and launcher repeated recomputes retain owned links with zero churn.
  Queue-backfilled links remain derivable even when sidecars are unavailable.
- Interleaved owner compute/root backfill/owner apply produces one link.
- Queue completion parsing ignores nonterminal statuses and unrelated trees.
  Completion lookup keys include child and tool identity, with latest timestamp.
- Root restart cleans cached nested agents without loaded launcher rows.
  Launcher idle leaves active children running in cards and headers.
- Stop-before-link and delayed REST-after-stop preserve stopped state.
  Live shares receive queue-only completion without requiring a second result.
- Nested code comments anchor to the root spawn after delayed tree loading.
- Depth greater than 32 works through ingestion, migration, API, and sharing.
  Migration tests execute actual migration code and verify former-parent and
  project aggregates, not only root totals.

Acceptance uses copied reference transcripts and isolated DB/provider homes.
The actual bundled Claude CLI verifies nested async/sync launch and nested
`stop_task`; a controlled local model endpoint can make the scenario deterministic.
Rendered UI tests verify plain tabs, reload, pulse, and stop controls.
These checks do not require restarting or migrating the user's running instance.
Record evidence and limitations; do not claim live verification from source inspection.
