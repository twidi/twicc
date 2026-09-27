# Subagent runs and agent-control tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Contract:** [`2026-09-26-subagent-runs-and-control-tools-design.md`](2026-09-26-subagent-runs-and-control-tools-design.md).
That document is the source of truth for *what* and *why*; this one is *in what
order* and *where*. Where they disagree, the design wins and this plan is wrong.
Every task cites the design sections it implements: read them before the task,
not after.

**Goal:** track whether a subagent is running across all its runs (spawn, Claude
`SendMessage` resumes, Codex `followup_task` resumes; stops by `TaskStop`, the
Stop button, `interrupt_agent`, a Codex owner turn abort, a Claude child
interrupt), compute it in the backend, and show it everywhere; control-tool cards
get the spawn card's agent widget.

**Architecture:** two new tables (`AgentInteraction`, `AgentRunEnd`) filled by
one live hook and one batch hook per provider; one read helper
(`agent_run_states`) is the only place that decides whether an agent runs; a
per-batch stop step broadcasts `agent_run_state`; the frontend stores the
backend's per-agent / per-run state and applies only the root cutoff.

**Tech stack:** Django 6 + SQLite, provider compute (`providers/compute_base.py`
and the two provider `compute.py`), Channels WS, Vue 3 + Pinia, node:test.

**Status:** reviewed (three parallel reviewers — backend, frontend, coverage
— round 1, fixes confirmed PASS by the same reviewers). Not started.

## Scope

In scope: design §3.1. Out of scope and never to be "fixed" on the way: design
§3.2 (Codex multi-agent v1), §3.3 (CLI `session agents`, crash detection,
tool-less wake-ups), share viewer reconnect recovery (§7.3), and every §10
limitation.

## Why the order matters

**Backend first, bottom-up** (Tasks 1-11): tables, then the one read helper
that decides the running state, then the compute plumbing both providers plug
into, then each provider's signals, then the stop step that turns evidence into
broadcasts, then live processes, snapshot and WS. The helper (Task 2) comes
before any writer because every later task's tests assert through it; the
plumbing (Task 3) comes before the providers so that Tasks 4 and 5 only fill
hooks and never touch the base loop.

**The live tuple and the positional test indexes are protected in Task 3**: the
three new lists are appended at the end, the four early returns grow with them,
and the existing `tests/test_nested_agent_compute.py` indexes stay valid (design
§7.1, §8.1). A task that changes a tuple position is wrong.

**Frontend second** (Tasks 12-18), consuming the payloads exactly as design
§7.2 / §7.3 state them: store model, WS dispatcher, share shim, then the card
fetch pipeline (the riskiest piece: 20 review rounds were spent on it — its
rules are in design §8.3 and must be implemented rule by rule, not
reinterpreted), then the card widget, provider helpers and header.

**Docs and the manual check close** (Tasks 19-20).

## Choices settled here (the design leaves them open)

- **Share relay payloads** (design §7.3 names the messages, not their
  fields): both are **flat**, like `share_agent_stopped`, carrying the §7.3
  app payload fields minus `project_id`:
  `{"type": "share_agent_run_state", root_session_id, agent_session_id, running, run_started_at, run_background, runs}` and
  `{"type": "share_agent_interaction", root_session_id, owner_session_id, agent_session_id, tool_use_id, tool_use_line_num, kind, opens_run, started_at}`.
  Task 10 emits exactly these; Task 14 consumes exactly these.
- **Frontend testability:** `node --test` cannot import `stores/data.js`, the
  share shim, `useWebSocket.js` or the provider `toolHelpers.js`
  (extensionless imports, `.vue` files). The rules therefore go into modules
  that import nothing but `vue` or explicit `.js` files —
  `utils/agentLinkIndex.js` (extended), `utils/agentCardState.js`,
  `composables/useToolResultFetch.js`,
  `providers/{claude_code,codex}/agentControlTools.js` — and the store / shim /
  component glue is tested by source slicing, as the repo already does
  (Tasks 12-17).
- **Where the backend code lives:** a new `src/twicc/core/agent_runs.py`
  holds the run model (`agent_run_states`), the stop step, the Stop-button
  writer and the tree-rule payload helpers; the pure parsers go to
  `providers/claude_code/agent_runs.py` and `providers/codex/agent_runs.py`.
  The shared broadcast helper stays in `providers/sessions_watcher.py`
  (`tests/test_nested_subagents_watcher.py` monkeypatches
  `sessions_watcher.broadcast_message`).
- **Frozen path reads the whole tree** (design §5.4 "never by the whole tree"
  vs the frozen owner-visibility rule, which needs every link:
  `visible_tree_agent_ids`, `session_queries.py:119-148`): the tree's links are
  read once, in the frozen path only (share snapshots). The non-frozen path,
  used by the stop step and live, stays bounded by `agent_ids` (Task 2's
  query-count test).
- **`AgentRunEnd` rows are not filtered by the owner tree rule** (design §5.1
  lists it for interactions and stop records only): ends are selected by
  `agent_id` in any session — rule 4 needs the root-file end of a subagent's
  `SendMessage`, rule 5 rows live in the child's own file — except `ui` rows,
  which count only on this root. A CLI-fork copy of a notification carries the
  same globally unique `tool_use_id`, so it can only re-close the same run.
- **`BatchAgentState` carries two more fields and one index** than design §6.2
  lists: `root_session_id`, `session_type` (the Claude write-time rule and the
  subagent-only rules need them in batch; live reads them from `Session`) and
  `results_by_tool_use`, an index kept in step with `all_tool_result_links` so
  a Codex signal never scans up to 15 000 rows (Task 3). No rule changes.
- **Two new update fields:** `AgentLinkUpdate.created` (a link creation vs an
  `is_background` upgrade, for the `exclude` set) and
  `ToolResultUpdate.link_id` (to exclude this batch's evidence rows) (Task
  6).

## Global rules (every task)

- **Commits:** one per task (or sub-task), Conventional Commit subject, a
  descriptive body, and the `Co-Authored-By: Claude <the running model>
  <noreply@anthropic.com>` trailer (`CLAUDE.md`, *Commit Conventions*). Stage
  files explicitly, never a directory. No CHANGELOG entry without an explicit
  ask. No lint pass (the ruff baseline is deferred).
- **Tests:** backend `uv run pytest`; frontend `cd frontend && npm test`
  (node:test). In a worktree: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run
  pytest`. Never `uv pip` or `--active`.
- **Migration (Task 1):** create it, never run `migrate` on the user's
  instance; remind the user at the end of the task. Never import app code in a
  migration.
- **Compute versions** are bumped once, in **Task 11**, when every signal is
  in: a bump recomputes each session once at the next backend start, so a dev
  backend restarted between Task 3 and Task 5 would store a half-signal
  recompute and never redo it (design §7.1).
- **Each task updates the existing tests it breaks, in the same commit**, so
  every commit is green; Task 11 only audits the design §8.1 list.
- **Dev servers:** never restarted by the implementer; remind the user after
  backend changes. The share viewer bundle is not HMR'd: `cd frontend && npm
  run build` after editing `share-session/*` (Task 14).
- **Out-of-scope temptations:** a pre-existing issue met on the way is not
  fixed here; note it for the user.

## Review Focus

The five conditions the design implies but no fixture test fully exercises,
most likely to bite first:

1. **The full-history recompute after the compute-version bump** (Task 11): the
   batch must give the same rows and states as live on real data, not only on
   the §9 fixtures. The user's DB has no new rows before the migration, so
   there is nothing live to diff against: Task 5c's parity test replays every
   Task 5b rollout fixture (which include the real-data shapes named there)
   live and batch, and Task 20 recomputes a copy of the user's DB and checks
   the design §4 / §9 known cases and the running counts before the user
   restarts.
2. **Giant trees** (413 links, 15 000+ `ToolResultLink` rows on one root,
   design §5.4): `agent_run_states` and the per-batch stop step filter by the
   affected `agent_ids`, never the whole tree. Task 2 adds a query-count test
   (`django_assert_num_queries`, equal counts on a small and a large tree,
   plus `django_assert_max_num_queries(5)`) on a tree with many agents; Task 6 adds
   one on a batch touching one agent of a large tree.
3. **Initial sync order** (child file before its parent, root before its
   subagents, design §6.3 "created already closed" / late tree rule): Task 6
   adds a test that syncs the files of one tree in the reverse order and
   asserts the same final states as the natural order.
4. **Old transcripts** (Claude `SendMessage` results without
   `resumedAgentId`, pre-2026-08-27 Codex rollouts with `FINAL_ANSWER` only,
   flat-timestamp files, design §4, §10): Tasks 4 and 5 each include one
   fixture per old shape, asserting no crash and the documented outcome.
5. **Many open agent cards** (a long transcript with dozens of agent cards
   whose Result sections are open): one ticker per card that `wantsFetch`, no
   interval left behind by a card that closes, deactivates or unmounts (design
   §8.3 rules 4, 5, 7). Task 15 adds a test mounting several pipelines and
   asserting the live interval count after closes / unmounts.

---

## Backend tasks

The backend goes bottom-up, and every commit leaves `uv run pytest` green.
Task 1 adds the schema. Task 2 writes the one read model, `agent_run_states`,
against hand-built rows: every later task tests its rows through it, so it
must exist before anything writes rows. Task 3 adds the hooks, the tuple and
the batch diffs with **no behaviour**: default hooks return nothing. Tasks 4
and 5 make each provider write rows. Task 6 turns rows into stamps and
broadcasts and deletes the old stop check. Task 7 (Stop button) reuses the
Task 6 step and helper. Task 8 (Codex live process) reads Task 2 and the hook
routing of Task 6. Task 9 moves the snapshot, Task 10 the WebSocket. Task 11
removes the dead gates, bumps the compute versions and audits the design's
list of existing tests.

**Rule for existing tests:** a task that breaks an existing test updates that
test in the same commit (the design's §8.1 list names them; each task below
says which ones it owns). Task 11 only audits the list.

**Compute-version bumps are in Task 11, not Task 3.** A bump makes the
background compute recompute every session of the provider once, at the next
backend start, and never again at that version. If Task 3 bumped, a dev
backend restarted between Task 3 and Task 5 would recompute the history with
half the signals and keep the result. So the bump lands after the last task
that changes batch output.

**Module layout.** The run model lives in a new module,
`src/twicc/core/agent_runs.py`: `agent_run_states` (Task 2), the stop step
(Task 6), the Stop-button writer (Task 7) and the tree-rule payload helpers
(Task 10). `core/session_queries.py` stays the snapshot shaper and imports
from it (Task 9). Pure provider parsers go to
`src/twicc/providers/claude_code/agent_runs.py` (Task 4) and
`src/twicc/providers/codex/agent_runs.py` (Task 5a).

**Commands.** Tests: `uv run pytest` (in a worktree:
`cd <worktree> && TWICC_DATA_DIR=$PWD uv run pytest`). Every commit has a
Conventional Commit subject, a descriptive body and the
`Co-Authored-By: Claude <running model> <noreply@anthropic.com>` trailer
(`CLAUDE.md`, *Commit Conventions*). No CHANGELOG entry. No lint pass.

---

### Task 1: models and migration

**Files:**
- Modify: `src/twicc/core/models.py` — new `AgentInteraction` and
  `AgentRunEnd` after `AgentLink` (`:872-906`); `AgentLink.Meta.indexes`
  (`:897-903`) gains `Index(fields=["agent_id"], name="idx_agent_link_agent")`;
  `ToolResultLink.Meta.indexes` (`:856-866`) gains
  `Index(fields=["session", "tool_use_id"], name="idx_tool_result_link_by_tool")`.
- Create: `src/twicc/core/migrations/0146_agent_runs.py` (last is
  `0145_processrun_background_work_in_progress.py`).
- Test: `tests/test_agent_run_models.py` (new).

Field tables: design §5.1 and §5.2, copied exactly. Choices as plain
`TextChoices` in `models.py`:

```python
class AgentInteractionKind(models.TextChoices):
    MESSAGE = "message"; RESUME = "resume"; STOP = "stop"; OUTPUT = "output"

class AgentRunEndSource(models.TextChoices):
    TRANSCRIPT = "transcript"; UI = "ui"
```

`AgentRunEnd.status` stays a free nullable `CharField` (design §5.2 lists
Claude notification statuses verbatim, plus `interrupted`, `ui_stopped`,
`completed`, `turn_complete`, `owner_turn_aborted`): no choices, so a new
CLI status never needs a migration.

Constraints and indexes:
- `AgentInteraction`: `UniqueConstraint(fields=["session", "tool_use_id"],
  name="uniq_agent_interaction_call")`,
  `Index(fields=["agent_id"], name="idx_agent_interaction_agent")`.
  `related_name="agent_interactions"`.
- `AgentRunEnd`: `UniqueConstraint(fields=["session", "line_num",
  "tool_use_id"], condition=Q(source="transcript"),
  name="uniq_agent_run_end_transcript")`,
  `Index(fields=["agent_id", "tool_use_id"], name="idx_agent_run_end_agent_tool")`.
  `related_name="agent_run_ends"`. `line_num` nullable (a `ui` row has none).

The conditional constraint matters twice: Task 3's `bulk_create(...,
ignore_conflicts=True)` relies on it for first-wins (SQLite `INSERT OR
IGNORE` honours partial unique indexes), and `ui` rows must never collide
with each other (all have `line_num = NULL`, `tool_use_id = ""`).

The migration is generated, then read line by line: it must import only
`django.db` (memory rule: never import app code in a migration; the choices
render as literal tuples). Generate it from the implementation checkout with
the data-dir rule of `CLAUDE.md` (in a worktree, `TWICC_DATA_DIR=$PWD`):
`uv run python -m django makemigrations core -n agent_runs --settings=twicc.settings`.

- [ ] **Step 1: Write the failing tests** — the unique `(session,
      tool_use_id)` on `AgentInteraction` raises `IntegrityError`; two
      `transcript` `AgentRunEnd` rows on the same `(session, line_num,
      tool_use_id)` raise; two `ui` rows for the same root and agent do not;
      the four indexes exist (`connection.introspection.get_constraints`).
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_agent_run_models.py -x`
- [ ] **Step 3: Implement** the models, generate the migration, read it.
- [ ] **Step 4: Run the full suite** — `uv run pytest`
- [ ] **Step 5: Commit** — `feat(core): add agent interaction and run-end tables`
- [ ] **Step 6: Remind the user** (end of task report): run `migrate` on their
      own running instance, or let `devctl.py start` apply it (*Operations
      Reserved to User*). Never run `migrate` yourself.

---

### Task 2: `agent_run_states` — the one run model

**Files:**
- Create: `src/twicc/core/agent_runs.py`
- Test: `tests/test_agent_run_states.py` (new)

This task implements design §5.4 and §5.5 on DB rows only. Nothing calls it
yet. Everything later — the stop step (§6.3), the Codex live process (§6.4),
the snapshot (§7.2) — reads its output, so its tests are the oracle for the
rest of the plan.

**Interfaces** (later tasks import these names):

```python
class RunInfo(NamedTuple):
    owner_session_id: str
    tool_use_id: str
    started_at: datetime | None
    open: bool
    closed_at: datetime | None
    background: bool            # spawn: link.is_background; interaction run: True

class AgentRunState(NamedTuple):
    known: bool                 # at least one run (spawn link or run interaction)
    running: bool
    run_started_at: datetime | None   # newest open run; None when not running
    run_background: bool | None       # newest open run; None when not running
    stopped_at: datetime | None       # max non-null closed_at over closed runs
    runs: tuple[RunInfo, ...]         # sorted by (started_at, nulls first), owner, tool_use_id

class RunStateExclude(NamedTuple):    # §5.4 "exclude"; all default to frozenset()
    agent_links: frozenset[tuple[str, str]]       # (owner session id, tool_use_id) of AgentLink rows CREATED in the batch
    run_interactions: frozenset[tuple[str, str]]  # (session id, tool_use_id): created, or opens_run turned true, in the batch
    stop_records: frozenset[tuple[str, str]]      # stop interactions that became stop records in the batch
    tool_result_link_ids: frozenset[int]
    run_end_ids: frozenset[int]                   # transcript rows written in the batch, and a ui row

def agent_run_states(root: Session, agent_ids: Iterable[str], frozen_at_line: int | None = None,
                     exclude: RunStateExclude | None = None) -> dict[str, AgentRunState]
def serialize_runs(state: AgentRunState) -> list[dict]
    # [{owner_session_id, tool_use_id, started_at (iso|None), open, closed_at (iso|None)}]
def serialize_run_state(root_id: str, agent_id: str, state: AgentRunState) -> dict
    # {root_session_id, agent_session_id, running, run_started_at, run_background, runs}
    # The §7.3 agent_run_state payload minus project_id (added by the broadcaster).
```

It returns an entry for **every** requested id (`known=False` when the id has
no run: a shell `task_id`, `"main"`, a Monitor id).

The design says "a set of explicit row ids" for `exclude`. Runs are
identified here by their natural key `(owner session, tool_use_id)` —
`AgentInteraction` is unique on it (Task 1) and a spawn link is looked up by
it everywhere (`idx_agent_link_lookup`, `models.py:899-902`). Evidence rows
use their primary key. Same meaning, fewer lookups.

**Reads, in order (the query budget is fixed, whatever the tree size):**
1. Spawn links: `AgentLink` with `agent_id__in=agent_ids`, owner = the root or
   a session whose `parent_session_id` is the root (the owner filter of
   `tree_agent_links`, `session_queries.py:151-156`, written as
   `Q(session_id=root.id) | Q(session__parent_session_id=root.id)` — a join,
   not a whole-tree subquery), `.exclude(agent_id=root.id)`. Uses the new
   `idx_agent_link_agent`.
2. Interactions: `AgentInteraction` with `agent_id__in=agent_ids`, same owner
   filter.
3. Results: `ToolResultLink` with `session_id__in=<owners of 1 and 2>` and
   `tool_use_id__in=<their tool ids>`, then filtered to the exact pairs in
   Python. Uses the new `idx_tool_result_link_by_tool`.
4. Ends: `AgentRunEnd` with `agent_id__in=agent_ids`.
5. Frozen path only: the tree's links (`tree_agent_links(root)` +
   `visible_tree_agent_ids`, `session_queries.py:119-148`) and the freeze
   time (newest non-null `SessionItem.timestamp` of the root at or before
   `frozen_at_line`). See "Choices settled here", *Frozen path reads the
   whole tree*.

**Rules an implementer can get wrong** (the design section is the contract):

- **Tree rule** (§5.1): an interaction counts only when its target has a
  **non-excluded** in-tree link, and its owner passes the owner filter.
  Removing `exclude.agent_links` from the link set **before** the tree rule
  is the whole "late tree rule" mechanism of §5.4: a target whose only links
  were created in the batch fails the tree rule in the "before" call, so
  its interactions' runs are absent; a target that already had another link
  still passes. An `is_background` upgrade is not a creation (Task 6 sets
  `AgentLinkUpdate.created=False` for it), so it never enters the set.
- **Rule 1 counts distinct `tool_result_at`**, never rows (§5.4 rule 1: the
  `aa6d89b7469be1a2c` ack copied at two lines). The close time is the
  `required`-th smallest distinct non-null timestamp.
- **Rule 4 matches `(agent_id, tool_use_id)` whatever the row's session**: a
  subagent's `SendMessage` often ends in the **root** file only (§4.1,
  `toolu_01USQDKRXhX9zuv97Uxq46U2`). Filtering by owner here breaks the
  headline Claude case.
- **`ui` rows** count only when their `session_id` is `root.id` (they are
  written on the root, §5.2). They are stop records (rule 2), never rule 4
  or 5, even though `tool_use_id` is `""` like rule-5 rows.
- **Rule 5** reads only `tool_use_id == ""` rows with `status in
  {"turn_complete", "interrupted"}`, `ended_at > started_at`, both non-null.
- **Rule 2 tie-break** (§5.4 rule 2) closes the run on an equal time unless
  **all** of: the stop is an `AgentInteraction` (not `ui`), the run is an
  interaction run (not a spawn), same owner file, and the stop's first
  non-error result line is **before** the run's opening line. The opening
  line is: Claude, the run's first result line in its owner file (min
  `tool_result_line_num` among its results); Codex, `event_line_num`. The
  provider comes from `root.provider` (a tree is single-provider). A stop
  with a null time closes only runs with a null `started_at` (literal
  reading of §5.5 "at or before the stop time").
- **Rule 3**: `root.cutoff` (`models.py:676-683`); a null `started_at` is
  before any non-null cutoff.
- **`closed_at`** is the **earliest** non-null time among every piece that
  closes the run (§5.4 end); null only when every piece is null.
  `stopped_at` is the **max** of the non-null `closed_at` of closed runs.
- **Frozen filter** (§5.4, §7.2), applied only when `frozen_at_line` is set:
  root-owned results and root `transcript` ends with a line above the
  freeze are dropped; a root-owned run interaction is a run only when its
  `tool_use_line_num` and its deciding line (Claude: first result line in
  the root; Codex: `event_line_num`) are both at or before the freeze; a
  root-owned stop record needs its `event_line_num` and its non-error result
  line both at or before the freeze; a root `ui` row counts only when its
  `ended_at` is at or before the freeze time (dropped when no freeze time
  exists); runs and stop records owned by a session that is not visible are
  dropped (§5.4: selecting interactions by target would otherwise let a
  post-freeze agent's resume show a visible agent running).

- [ ] **Step 1: Write the failing tests** — every row hand-built through the
      ORM, no transcript. One test per behaviour:
  - spawn foreground closes at 1 distinct result, background at 2; a
    compaction copy (same `tool_result_at`) does not count (§9 "Compaction-
    copied result rows", the `aa6d89b7469be1a2c` shape);
  - interaction run needs 2; two open runs, closing one keeps `running`
    (§9 "Two open runs");
  - rule 2: stop after start closes; stop before start does not; null
    `started_at` closed by any stop; the tie-break both ways (§9 "Rule 2
    tie-break": stop result line first → stays open; later line → closes);
    `ui` row at equal time closes; spawn run at equal time closes;
  - rule 3 with a cutoff and a null `started_at`;
  - rule 4 with the end row in the root and the run owned by a subagent;
  - rule 5: `turn_complete` after start closes, before start does not
    (§9 "a child task_complete before a followup_task does not close the
    follow-up run"); `interrupted` the same; null times ignored;
  - `closed_at` with mixed evidence: an end row with null `ended_at` plus a
    timed result → the timed result; all null → null (§9 "`closed_at` with
    mixed evidence");
  - earliest evidence wins: the t20 shape (turn end 03:50:03Z, `FINAL_ANSWER`
    06:32:24Z) → `closed_at` and `stopped_at` = 03:50:03Z;
  - tree rule: target without link → no run and `known=False`; interaction
    written before the link, link added later → counted without any other
    change (§9 "interaction written before its target's spawn link");
    owner outside the tree (another root) → no run, no stop (§9 "Tree rule
    owner scope"); a shell `task_id` `AgentRunEnd` → `known=False`;
  - exclude: a created link → its spawn run absent; an interaction whose
    target's only link is created → absent (late tree rule); a second link
    for an agent already linked → still present; `run_interactions`,
    `stop_records`, `tool_result_link_ids`, `run_end_ids` each removed;
  - frozen: root-owned `SendMessage` whose first result is after the freeze
    → not a run; Codex `followup_task` whose `event_line_num` is after the
    freeze → not a run (§9 "Frozen share: a root-owned SendMessage…");
    post-freeze agent's resume of a visible agent → not a run; stop record
    owned by a non-visible agent → ignored (§9 "Frozen share: a post-freeze
    agent's…"); root Codex stop with result before and `interrupted` line
    after the freeze → not a stop record (§9 "Frozen share: a root-owned
    Codex stop…"); root `ui` row before / after the freeze time, and with
    no timestamp at or before the frozen line → dropped;
  - `run_started_at` / `run_background` describe the newest open run;
  - **query budget** (coordinator): a tree with 5 agents and one with 200
    agents and 15 000 `ToolResultLink` rows on the root; asking for one
    agent runs the same number of queries in both
    (`django_assert_num_queries` with the count measured on the small
    tree), and at most 5 on the non-frozen path
    (`django_assert_max_num_queries(5)`).
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_agent_run_states.py -x`
- [ ] **Step 3: Implement** `core/agent_runs.py` as above.
- [ ] **Step 4: Run the full suite** — `uv run pytest`
- [ ] **Step 5: Commit** — `feat(core): compute per-agent run state from run evidence`

---

### Task 3: compute plumbing (no behaviour)

**Files:**
- Modify: `src/twicc/providers/compute_base.py` — new NamedTuples next to
  the others (`:74-224`); hooks next to `begin_session_compute` /
  `remap_tool_result_id` (`:1291-1351`); batch loop (`:2553-2754`), diffs
  (`:2787-2860`), message (`:2915-2935`), apply (`:3171-3205`); live pass
  (`:3349-3996`).
- Modify: `src/twicc/providers/codex/compute.py` — `remap_tool_result_id`
  (`:2384-2400`) accepts and forwards `batch_state`;
  `_resolve_tool_result_id` (`:2402`) accepts it (unused until Task 5b).
- Modify: `src/twicc/providers/sessions_watcher.py` — the unpacking at `:684`.
- Test: `tests/test_agent_run_plumbing.py` (new).

**Interfaces:**

```python
class BatchAgentState(NamedTuple):          # read-only view, built ONCE before the loop (:2570)
    session_id: str
    root_session_id: str                    # session.parent_session_id or session.id
    session_type: str                       # SessionType value
    tool_use_map: dict[str, ToolUseEntry]
    all_tool_result_links: dict[tuple[str, int], dict]
    results_by_tool_use: dict[str, list[dict]]      # same dicts, per tool_use_id, line order
    all_agent_links: dict[tuple[str, str], dict]
    all_agent_interactions: dict[str, dict]         # tool_use_id -> row dict, FIRST line wins
    all_agent_run_ends: dict[tuple[int, str], dict] # (line_num, tool_use_id) -> row dict

class BatchAgentSignals(NamedTuple):
    interactions: tuple[dict, ...] = ()             # new rows (row dict below)
    opens_run: tuple[tuple[str, str | None], ...] = ()   # (tool_use_id, started_at iso) -> opens_run=True
    run_ends: tuple[dict, ...] = ()

class LiveAgentSignals(NamedTuple):
    changed_interactions: tuple[tuple[str, str], ...] = ()  # (session_id, tool_use_id): created or opens_run changed
    run_interactions: tuple[tuple[str, str], ...] = ()      # created with opens_run, or flipped to true
    stop_records: tuple[tuple[str, str], ...] = ()          # stop rows created while a non-error result exists
    run_end_ids: tuple[int, ...] = ()
    affected_agent_ids: tuple[str, ...] = ()
    agents_resumed: tuple[tuple[str, str], ...] = ()        # Codex: (agent_id, agent_path)

def collect_agent_run_signals(self, session_id, item, parsed, batch_state) -> BatchAgentSignals  # default: BatchAgentSignals()
def apply_agent_run_signals(self, session_id, item, parsed) -> LiveAgentSignals              # default: LiveAgentSignals()
def remap_tool_result_id(self, parsed_json, naive_tool_use_id, *, session_id, tool_use_map,
                         batch_state: BatchAgentState | None = None) -> str
```

Row dicts (batch message and `BatchAgentState` values):
- interaction: `{session_id, tool_use_line_num, event_line_num, tool_use_id,
  agent_id, kind, opens_run, started_at}` (`started_at` iso or `None`);
- run end: `{session_id, line_num, tool_use_id, agent_id, ended_at, status}`
  (`source` is always `transcript` on this path).

Defaults are tuples, never lists: a `NamedTuple` default is shared by every
instance.

`root_session_id` and `session_type` are two scalars added to the field list
of design §6.2. The Claude hooks need them for the write-time rule (§5.1:
skip the owner and the owner's root) and for "subagent file only" rules;
the loop already holds `session` (loaded at `:2441`), so this avoids a query per line.

`results_by_tool_use` is an index over `all_tool_result_links`, appended in
the **same statement** as `all_tool_result_links[new_key] = …` (`:2717`).
Each key is written once (one `tool_result_ref` per line, and a compaction
copy has another line), so the index mirrors the dict exactly. Without it,
every Codex signal would scan up to 15 000 links.

**Batch.** Build `batch_state` before `for item in queryset…` (`:2570`); the
dicts mutate in place, so the view always shows "built so far". Pass it to
`remap_tool_result_id` (`:2695`). Call the hook for **every** item after the
agent-link block (ends `:2752`), before the prefix/suffix block (`:2754`) —
design §6.2: it then sees this line's result link and the link created from
this line's result or `SubAgentActivity`. Apply what it returns before the
next line: `all_agent_interactions.setdefault(row["tool_use_id"], row)`
(first wins, §5.1 — unlike `all_agent_links`, `:2744`, which overwrites);
for `opens_run`, update the stored dict **only if present**;
`all_agent_run_ends[(row["line_num"], row["tool_use_id"])] = row`.

Diffs after `end_session_compute` (`:2785`), next to the link diffs:
original interactions keyed by `tool_use_id`; original run ends keyed by
`(line_num, tool_use_id)` and loaded with `source="transcript"` only (§7.1:
`ui` rows never enter the diff). Message keys `agent_interactions_to_create`
/ `_update` / `_delete` and `agent_run_ends_to_create` / `_update` /
`_delete`. Apply them right after the agent-link block (`:3171-3205`):
creates with `bulk_create(ignore_conflicts=True)` (the Task 1 constraints
are the in-writer existence re-check of §5.1), updates with `bulk_update`
(interaction fields: `tool_use_line_num`, `event_line_num`, `kind`,
`agent_id`, `opens_run`, `started_at`; run-end fields: `agent_id`,
`ended_at`, `status`), deletes by id.

**Live.** Call `apply_agent_run_signals` for **every** item, right after the
`if self.is_tool_result_item(parsed):` block (ends `:3753`) and before the
queue block (`:3758`): after `create_tool_result_link_live` (`:3747`), before
the tool_use link creation (`:3769`), as §6.2 requires. Accumulate the
results in a small per-call collector (a local class, mutable; it lives
only inside `sync_session_items_from_file`).

The live tuple grows from 7 to 10 elements: `agent_interaction_updates`,
`agent_run_state_updates`, `agents_resumed`, **appended** after
`found_compact_summary`. In this task the first two are `[]` (Tasks 10 and 6
fill them) and the third is the collector's `agents_resumed`. Update the
annotation (`:3353-3361`), the docstring (`:3368-3370`), the final return
(`:3988-3996`) **and the four early returns** (`:3390`, `:3399`, `:3425`,
`:3434`, each `[], [], [], [], [], [], False` today): a missed early return
makes the watcher's unpacking raise on every "no new content" event. The
watcher (`sessions_watcher.py:684`) unpacks the three new names; nothing
uses them yet.

**Recompute entry point (for the coordinator's Task 20).** There is no
command that recomputes one session. The background compute recomputes
every session of a provider whose `compute_version` differs from the
current constant, at backend start: `start_background_compute_task`
(`src/twicc/providers/background_compute_task.py:546`, selection
`:612-619`, newest `mtime` first). So: (a) all sessions — the Task 11 bump,
then a backend restart; (b) one session — set its `compute_version` to
`NULL` (`cd <worktree> && TWICC_DATA_DIR=$PWD uv run python -m django shell
--settings=twicc.settings`, then
`Session.objects.filter(id=...).update(compute_version=None)`), then a
backend restart. Restarts are user-reserved. `core/management/commands/sync.py`
is a file sync, not a recompute (its `--reset` deletes everything).

- [ ] **Step 1: Write the failing tests**
  - Live tuple (§9 "Live tuple"): a real `sync_session_items_from_file` on a
    Claude file returns 10 elements; `result[2]` and `result[5]` keep their
    meaning; the three new ones are lists; a missing file and a
    "no new content" sync also return 10 elements.
  - Hook position, batch: a test subclass of the Claude compute records, per
    call, `batch_state.results_by_tool_use.get(<this line's result id>)`
    and `batch_state.all_agent_links`; on the result line the current line's
    link is present; on a Codex-shaped `SubAgentActivity started` line (use
    the Codex compute subclass) the link from that line is present.
  - Hook position, live: the same spy sees this line's `ToolResultLink` in
    the DB, and does not yet see a link created from a `tool_use` on the
    same line.
  - Batch diff: a spy hook returning one interaction and one run end →
    created; a second recompute with a changed field → updated; a third
    without them → deleted; two lines with the same `tool_use_id` → the
    first line is kept.
  - A seeded `ui` `AgentRunEnd` on the root survives a root recompute.
  - `remap_tool_result_id` without `batch_state` still works (the
    `tests/test_codex_code_mode.py:1219-1428` calls stay unchanged).
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_agent_run_plumbing.py -x`
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run the full suite** — `uv run pytest`. Nothing else moves:
      the default hooks return nothing.
- [ ] **Step 5: Commit** — `refactor(compute): add agent-run signal hooks and batch diffs`

---

### Task 4: Claude signals

**Files:**
- Create: `src/twicc/providers/claude_code/agent_runs.py` — pure parsers.
- Modify: `src/twicc/providers/claude_code/notifications.py` — expose the
  XML extraction the new parser needs (no behaviour change to
  `parse_queue_completion`, `:160-181`).
- Modify: `src/twicc/providers/claude_code/compute.py` — the two hooks on
  `ClaudeCodeSessionCompute` (class at `:592`).
- Test: `tests/test_claude_agent_runs.py` (new).

**Interfaces** (`claude_code/agent_runs.py`):

```python
TERMINAL_NOTIFICATION_STATUSES = frozenset(
    {"completed", "failed", "stopped", "killed", "cancelled", "canceled"})

class ControlCall(NamedTuple):
    tool_use_id: str
    kind: str        # "message" | "stop" | "output"
    target: str      # SendMessage input.to ; TaskStop/TaskOutput input.task_id

class RunEndNotification(NamedTuple):
    task_id: str
    tool_use_id: str
    status: str | None

def control_calls(parsed: dict) -> list[ControlCall]
def send_message_opens_run(parsed: dict, tool_use_id: str) -> bool
def run_end_notification(parsed: dict) -> RunEndNotification | None
def is_plain_interrupt_marker(parsed: dict) -> bool
```

`parse_queue_completion` keeps its status set (no `killed`): it also drives
the link recovery and the batch stamping, which §6.1 keeps "as today". The
new `run_end_notification` has its own set, `killed` included (§5.2).

**Rules the parsers encode** (design §4.1, §5.2, §6.1):
- `control_calls`: every `tool_use` block of an assistant line named
  `SendMessage` (kind `message`, target `input.to`), `TaskStop` (`stop`,
  `input.task_id`), `TaskOutput` (`output`, `input.task_id`). No shape check
  on the target (§4.1: the tree rule sorts shells, workflows, `"main"`).
  Several blocks on one line give several calls.
- `send_message_opens_run`: read `toolUseResult` (a dict) else the JSON text
  of the `tool_result` block (string content, or the first text block).
  True when `resumedAgentId` is present, or `success is True` and `message`
  does not start with `"Message queued"`. `success` false, unparsable text
  or a `tool_use_error` → False.
- `run_end_notification` reads the **preserved original**, because both
  paths see the rewritten dict (`transform_inline` mutates it, live
  `compute_base.py:3540-3548`, batch `:2583-2589`): user form →
  `twiccOriginalContent` when it is a string starting (after `lstrip`) with
  `<task-notification>`, else `message.content` with the same test;
  attachment form → `twiccOriginalEntry` (a JSON string of the whole entry,
  `claude_code/compute.py:1463`, `:1499`, `:1546`) → its
  `attachment.prompt`, else the raw `attachment.prompt` of a
  `queued_command` / `task-notification` attachment; queue form →
  `content` of a `queue-operation` line with `operation == "enqueue"` only.
  **Trap:** `twiccOriginalContent` is also set by the local-command rewrite
  (`claude_code/compute.py:1596-1598`), so the `<task-notification>` prefix
  test is mandatory. Written when `task_id` and `tool_use_id` exist and
  (status in the terminal set, or no status and `is_task_result`,
  `notifications.py:74-134`). The regex status recovery of
  `parse_queue_completion` (`:175-177`) applies too: the manual XML
  fallback drops `<status>`, and a `running` notification must stay
  non-terminal.
- `is_plain_interrupt_marker`: a `user` line, not `isMeta`, no `origin`, and
  content exactly `[Request interrupted by user]` (a string or a single text
  block). Not `is_interruption_marker` (`claude_code/compute.py:188-191`),
  which also matches `… for tool use]` (§6.1).

**Hooks** (same decisions, two evidence sources):
- `tool_use` line → one interaction per control call whose target is
  neither the owner nor its root (§5.1 write-time rule): `tool_use_line_num
  = event_line_num = item.line_num`, `opens_run=False`, `started_at =
  item.timestamp`. Live: `AgentInteraction.objects.get_or_create(session,
  tool_use_id, defaults=…)` — an existing row wins (first line, §5.1). The
  live root id costs one `Session` read, only on lines that carry a
  control call.
- result line of a `SendMessage` interaction of this session (kind
  `message`) → first-result decision (§6.1): the line is the first result
  when this line has a result row for the call and the call's rows have
  exactly **one** distinct `tool_result_at`. Batch reads
  `batch_state.results_by_tool_use[tool_use_id]`; live reads
  `ToolResultLink` rows (this line's row is already written, Task 3
  position). When `send_message_opens_run` is true and the row is not yet
  `opens_run`: set `opens_run=True` and `started_at` = this line's
  `tool_result_at` (§5.1: the resume ack, not the call time). A compaction
  copy re-decides the same value and changes nothing.
- notification line (any of the three forms, **any session type** — today's
  live queue path is root-only, `compute_base.py:3758`) → one run end
  `{line_num: item.line_num, tool_use_id, agent_id: task_id, ended_at:
  item.timestamp, status}`. Live: `get_or_create` on `(session, line_num,
  tool_use_id, source="transcript")`.
- subagent file (`session_type == SUBAGENT`) with
  `is_plain_interrupt_marker` → run end `{tool_use_id: "", agent_id:
  session_id, status: "interrupted", ended_at: item.timestamp}`.

Live hook output: `changed_interactions` / `affected_agent_ids` for created
rows and `opens_run` flips (`run_interactions` for flips), `run_end_ids` and
the end's `agent_id` in `affected_agent_ids`. `agents_resumed` stays empty
(§7.1: Codex only). A Claude `TaskStop` row is created before its result
exists, so the hook never reports `stop_records`; Task 6 detects the flip
at the result line.

`create_agent_link_from_tool_result`'s guard against flipping a spawn link
on a `SendMessage` notification (`compute_base.py:1983-2019`) and the
existing rewrites stay as they are (§6.1).

- [ ] **Step 1: Write the failing tests** — live (`sync_session_items_from_file`
      on files under `provider_home`, as `tests/test_nested_agent_compute.py:61-66`)
      **and** batch (`compute_session_metadata` + `apply_session_complete`,
      as `:69-75`) for each, then assert rows and `agent_run_states`:
  - background spawn → end → `SendMessage` resumed (root shape) → running →
    end notification on the `SendMessage` id → stopped (§9 first Claude test);
  - subagent-made `SendMessage`, text result without `resumedAgentId` → run;
    notification only in the root file → `AgentRunEnd` → closed, with one
    `ToolResultLink` on the owner; variant with the caller-file
    `attachment` notification → second `ToolResultLink`, same outcome;
  - `killed` notification → `AgentRunEnd` (§9);
  - `running` notification with a payload → no row; `queue-operation`
    `remove` → no row (§9);
  - parse input: rewritten user form (`twiccOriginalContent`), rewritten
    attachment form (`twiccOriginalEntry`) and raw `queue-operation` give
    the same `AgentRunEnd`, live and batch (§9);
  - queued `SendMessage` on a running agent → `opens_run` false; the agent
    stops with its own run (§9);
  - `TaskStop` success on an agent → stop record → stopped; on a shell id →
    row written, rejected by the tree rule; `tool_use_error` → no stop (§9);
  - `SendMessage` to `"main"` → row written, `known=False`, never a run (§9);
  - interaction synced before its target's spawn link (session order) →
    counted once the link exists, no recompute (§9, state part; the WS
    part is Task 10);
  - root interrupt marker (`aa6d89b7469be1a2c` shape) → `interrupted` row;
    runs started before close; the same text quoted in a prompt or a
    coordinator message writes nothing; a later `SendMessage` resume is not
    closed by it; live and batch write the same row (§9);
  - rule-2 tie-break with real lines: `TaskStop(A)` then `SendMessage(to=A)`
    whose results share one `tool_result_at` (stop result line first) →
    resumed run open; a stop on a later line at that time → closed (§9);
  - resume start = ack time: `TaskStop(A)` and `SendMessage(to=A)` in one
    assistant message, stop result before the ack → the resumed run stays
    open until its own end; `started_at` moves from the call time to the
    ack time, live and batch (§9; the payload part is Task 10);
  - hook position: the first result decides `opens_run` in the hook call of
    that same line, live and batch; a compaction copy decides the same (§9);
  - duplicate `tool_use` line → one row; batch and live keep the first
    line (§9 "Duplicate interaction lines", "Duplicate `tool_use` line");
  - old shapes (coordinator), each "no crash, documented outcome": the
    resumed wordings `Agent "…" had no active task; resumed from
    transcript…` and `…was stopped (completed); resumed it…` → run; the
    subagent-side text JSON without `resumedAgentId` → run; `success:
    false` with `to: "parent"` → row, no run, `known=False`; a notification
    without `<tool-use-id>` (Monitor fragment) → no row; malformed XML
    (manual fallback, status recovered by regex) → row only when terminal;
    legacy 7-character agent ids (`a` + 6 hex) in `TaskOutput` → row, run
    state follows the tree rule.
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_claude_agent_runs.py -x`
- [ ] **Step 3: Implement** the parsers and the two hooks.
- [ ] **Step 4: Run the full suite** — `uv run pytest`. The old stop check
      still runs (Task 6 replaces it); nothing reads the new rows yet.
- [ ] **Step 5: Commit** — `feat(claude): record agent control calls and run ends`

---

### Task 5a: Codex parsing and attribution (pure functions)

**Files:**
- Create: `src/twicc/providers/codex/agent_runs.py` — no Django import.
- Test: `tests/test_codex_agent_runs_attribution.py` (new).

Design §5.6 is a line-order algorithm over one owner file and one agent. It
is written **once**, as pure functions over an evidence value; 5b and 5c
only build that value (from `batch_state` or from the DB). That is what
makes live and batch agree (§5.6 "The rules depend only on line order").

**Interfaces:**

```python
INTERACTION_KIND_BY_TOOL = {
    "collaboration__followup_task": "resume",
    "collaboration__send_message": "message",
    "collaboration__interrupt_agent": "stop",
}   # any other qualified name -> no row (§6.2)

class SubAgentActivity(NamedTuple):
    kind: str            # started | interacted | interrupted | completed
    event_id: str        # call_id, or "subagent-completed-<turn>" (never parsed)
    agent_id: str        # agent_thread_id
    agent_path: str

class ForkFields(NamedTuple):
    forked_from_id: str | None
    history_start_ordinal: int | None

class FileRun(NamedTuple):
    tool_use_id: str
    call_line: int                 # AgentLink.tool_use_line_num / interaction.tool_use_line_num
    event_line: int | None         # resume: event_line_num; spawn: None
    kind: str                      # "spawn" | "resume"

class FileEvidence(NamedTuple):    # one owner file, one agent; ALL lines, functions filter by L
    runs: tuple[FileRun, ...]                           # spawns + resume interactions with opens_run
    stops: tuple[tuple[int, int | None], ...]           # (event_line_num, first non-error result line)
    completed_lines: Mapping[str, tuple[int, ...]]      # tool_use_id -> lines of its `completed` rows
    aborted_lines: Mapping[str, tuple[int, ...]]        # tool_use_id -> lines of its owner_turn_aborted rows
    results: Mapping[str, tuple[tuple[int, datetime | None], ...]]  # tool_use_id -> (line, tool_result_at)

def parse_sub_agent_activity(parsed: dict) -> SubAgentActivity | None
def owner_turn_abort_turn_id(parsed: dict) -> str | None
    # turn_aborted with reason == "interrupted", or task_complete whose
    # error.codex_error_info == "usage_limit_exceeded" (a string, checked on a real rollout)
def task_started_turn_id(parsed: dict) -> str | None
def is_task_complete(parsed: dict) -> bool
def fork_fields(parsed_line_1: dict) -> ForkFields     # session_meta payload
def line_ordinal(parsed: dict) -> int | None            # top-level "ordinal"
def candidates(ev: FileEvidence, before_line: int) -> list[FileRun]
def file_open_runs(ev: FileEvidence, before_line: int) -> list[FileRun]
def attribute_completed(ev: FileEvidence, line: int) -> FileRun | None
def attribute_final_answer(ev: FileEvidence, line: int) -> FileRun | None   # None -> caller's path fallback
```

`parse_sub_agent_activity` sits next to `_parse_sub_agent_activity_started`
(`codex/compute.py:1735-1763`), which keeps its contract (it gates
`is_tool_result_item`, `:4026-4028`, and must never accept `completed`,
§6.2).

**Rules to pin** (§5.6):
- `candidates(ev, L)`: spawns with `call_line < L` and resumes with
  `event_line < L`, ordered by `call_line`; minus runs with an abort line
  `< L`; minus runs **stopped in line order**: for each stop with
  `event_line < L` and a result line `< L`, the runs that are
  `file_open_runs(ev, stop.event_line)`. This is recursive on a strictly
  smaller line, so it terminates; memoize per call.
- has `completed` = a `completed` line `< L`; has `FINAL_ANSWER` = at least
  2 distinct timestamps among result lines `< L` (the ack counts 1).
- `file_open_runs(ev, L)` = candidates with neither signal before `L`.
- `attribute_completed`: oldest candidate with neither signal; else oldest
  with `FINAL_ANSWER` and no `completed`; else newest candidate (extra
  signal); no candidate → newest spawn with `call_line < L`; none → `None`
  (no row).
- `attribute_final_answer`: oldest candidate with `completed` and no
  `FINAL_ANSWER`; else oldest with neither; else newest candidate; no
  candidate → `None` (the caller falls back to today's newest spawn for the
  sender path).
- A run that already has its `completed` stays a candidate for its own
  `FINAL_ANSWER` even after a later stop (§5.6: stop exclusion needs the run
  file-open at the stop's line).

- [ ] **Step 1: Write the failing tests** — hand-built `FileEvidence` and
      line sequences only, one per case:
  - the `01a08171…` t20 sequence (spawn ends with `FINAL_ANSWER` only at
    76417; follow-ups 76422 and 76532; `completed` 76508, 76583;
    `FINAL_ANSWER` 76517) → each signal on its own follow-up, no cascade (§9);
  - the `01a0796d-72bd…` line-210 case: the previous run's `completed`
    between a `followup_task` call and its `interacted` line → attributed to
    the previous run, because candidates read `event_line` (§9);
  - `interrupt_agent` race: call, target `completed`, `interrupted`, the
    interrupt's result, `FINAL_ANSWER` → both on the interrupted run (§9);
  - stop in the gap: S0 has `completed`, `interrupt_agent`, `followup_task`
    opens F1, S0's late `FINAL_ANSWER` → S0; F1 still file-open (§9);
  - stop result before its `interrupted` line with a signal between → the
    run is still a candidate for that signal (§9 last test);
  - owner abort after L → not yet excluded (§9 "Codex attribution with an
    owner abort after L");
  - merged follow-up (08-31 shape) → no candidate, the single pair on the
    spawn; 09-06 `reaudit_backend` → second pair on the spawn as extra
    signals (documented limitation, §5.6);
  - merged then real follow-ups (09-07 `spec_provider_review`, 09-06
    `reaudit_accessibility` shapes) → each real run gets its own pair (§9);
  - follow-up a few seconds after `interrupt_agent` (08-12 `019ff497`
    4448/4452) → the next pair on the follow-up (§9);
  - long `completed` → `FINAL_ANSWER` gap with a follow-up inside → the
    `FINAL_ANSWER` goes to the first run (§9);
  - runs ending with `FINAL_ANSWER` only (pre-08-27) and the follow-up after
    one (§9);
  - ack and `FINAL_ANSWER` with the same timestamp → one distinct result,
    no `FINAL_ANSWER` yet (§9 "Codex batch 'has FINAL_ANSWER'");
  - the parsers: all four activity kinds; `turn_aborted` with another
    reason → `None`; `task_complete` with `server_overloaded` /
    `cyber_policy` / `other` → `None`; `fork_fields` on a non-`session_meta`
    line → both `None`.
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_codex_agent_runs_attribution.py -x`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite** — `uv run pytest`
- [ ] **Step 5: Commit** — `feat(codex): add file-local run attribution rules`

---

### Task 5b: Codex batch wiring

**Files:**
- Modify: `src/twicc/providers/codex/compute.py` — per-session state in
  `__init__` (`:2088-2191`), `begin_session_compute` (`:2245-2258`),
  `end_session_compute` (`:2260-2275`); `_resolve_tool_result_id`
  (`:2402-2448`); new `collect_agent_run_signals`.
- Test: `tests/test_codex_agent_runs_batch.py` (new).

Two file facts live in new per-session maps, set up and freed like
`_agent_id_to_spawn_call_id` (§6.2): `_turn_started_lines: dict[session_id,
dict[turn_id, line_num]]` and `_fork_fields: dict[session_id, ForkFields]`.
The hook fills them as the loop passes line 1 and each `task_started`.

`collect_agent_run_signals`, per line (design §5.2, §5.6, §6.2):
- `interacted` / `interrupted`: the call is `batch_state.tool_use_map[event_id]`
  (none → no row; `wait_agent` is already absent, `:461`, `:4061`); kind from
  `INTERACTION_KIND_BY_TOOL[entry.tool_name]` (the qualified name, via
  `_tool_use_name`, `:1106-1119`); target = `agent_id`, skipped when it is
  the owner or `batch_state.root_session_id`. Row: `tool_use_line_num =
  entry.line_num`, `event_line_num = item.line_num`, `started_at =
  item.timestamp` (the event time, §5.1). `resume` → `opens_run` = no
  `file_open_runs` at this line.
- `completed` → `attribute_completed` → run end `status="completed"`,
  `agent_id = agent_thread_id`, `tool_use_id` = the chosen call.
- owner abort (`owner_turn_abort_turn_id`) → the turn's `task_started` line
  from `_turn_started_lines`; none → nothing. For every agent with a run
  owned by this file: each `file_open_runs` run with `call_line >` that line
  → run end `status="owner_turn_aborted"`, `tool_use_id` = the run's call,
  `ended_at = item.timestamp` (§5.2).
- subagent file (`session_type == SUBAGENT`) and `is_task_complete` → run end
  `{tool_use_id: "", agent_id: session_id, status: "turn_complete"}`, except
  when `_fork_fields` has a `forked_from_id` **and** `line_ordinal(parsed) <
  history_start_ordinal` (§5.2: the field alone is not the gate). A
  usage-limit `task_complete` in a subagent that owns runs writes both rows
  (different `tool_use_id`).

The evidence builder `evidence_from_batch_state(batch_state, session_id,
agent_id) -> FileEvidence` reads `all_agent_links` (spawns owned by this
session), `all_agent_interactions` (resumes with `opens_run`, stops),
`all_agent_run_ends` (`completed`, `owner_turn_aborted`) and
`results_by_tool_use`.

`FINAL_ANSWER` rebind: split the shared branch at `:2441-2448`. The v1
`<subagent_notification>` keeps `_agent_id_map`. The v2 `FINAL_ANSWER`, when
`batch_state` is given: resolve the sender path to the agent (newest
`started` for the path, today's `_agent_id_map[path]`, then the agent id
from `batch_state.all_agent_links`), then `attribute_final_answer`; `None`
→ today's `_agent_id_map` fallback. Without `batch_state` (direct calls,
`tests/test_codex_code_mode.py`) → today's behaviour. `remap_tool_result_id`
runs before this line's link is written (`compute_base.py:2695` vs
`:2717`), so the evidence holds earlier lines only, as live does.

`analyze_content`'s `SubAgentActivity` branch (`:4954-4985`) keeps only its
`started` work (§6.2); `is_tool_result_item` (`:4026-4028`) is unchanged.

- [ ] **Step 1: Write the failing tests** — batch compute on seeded rollouts
      (`_seed` / `_run_batch_compute` style of
      `tests/test_codex_subagent_links.py:189-208`, **distinct timestamps**
      per line), asserting rows and `agent_run_states`:
  - spawn → `completed` → `FINAL_ANSWER` → `followup_task` on the idle agent
    → run → both signals on the `followup_task` (§9);
  - `followup_task` that merges (08-31) → `opens_run=False`, pair on the
    spawn (§9); the 09-06 limitation (§9);
  - owner abort: `turn_aborted` interrupted (`01a08171…` 17910 shape) and
    usage-limit `task_complete` (`01a0796d…` 704 shape) → one
    `owner_turn_aborted` row per file-open run opened in that turn; a run
    of an earlier turn not cut; a later `followup_task` opens its own run
    and gets its own signals; another reason / error kind → nothing (§9);
  - `completed` creates no `ToolResultLink`: a spawn ended by `completed` +
    `FINAL_ANSWER` holds ack + `FINAL_ANSWER` only (§9);
  - `send_message` → no run; `interrupt_agent` → stop record → stopped;
    `interacted` with no call → no row; `collaboration__wait_agent` and a
    v1 name → no row (§9);
  - child turn end: `task_complete` rows at the real line time, not the
    mtime; forked child (`01a08112-b173…` shape) → no row for copied lines,
    first run open until its own turn end; non-fork child with the field
    (`01a05541-635c…` shape) → rows written (§9);
  - batch file facts: the abort finds its `task_started` through the turn
    map; fork fields read from line 1 (§9);
  - v1 `<subagent_notification>` still rebinds to its spawn (§9);
  - **the attribution sequences as rollouts, not only as 5a pure-function
    cases** — they exist to prove that the two evidence builders
    (`evidence_from_batch_state` here, `evidence_from_db` in 5c) filter by L
    the same way, so each one is a seeded rollout here and is replayed live
    by 5c's parity test (§9 "on the live **and** batch paths"):
    the t20 sequence (`01a08171…` 76417-76583 shape); the `01a0796d-72bd…`
    line-210 shape (previous run's `completed` between a `followup_task`
    call and its `interacted` line); a stop inside a `completed` →
    `FINAL_ANSWER` gap; a merged follow-up followed by real follow-ups
    (09-07 `spec_provider_review` / 09-06 `reaudit_accessibility` shapes); a
    `followup_task` a few seconds after `interrupt_agent` (08-12 `019ff497`
    4448/4452 shape); a long `completed` → `FINAL_ANSWER` gap with a
    follow-up inside; an owner abort **after** L (the run stays a candidate
    for the earlier signal); a Codex stop whose result comes **before** its
    `interrupted` line with a signal of the agent between them; the
    interrupt race (interrupt call → `completed` → `interrupted` → result →
    `FINAL_ANSWER`); duplicate `interacted` lines (first line wins);
  - ack and `FINAL_ANSWER` with the **same** timestamp (the one fixture that
    breaks the distinct-timestamps rule, on purpose): they count as one
    result, in batch here and live in 5c (§9 "Codex batch 'has
    `FINAL_ANSWER`'…"), and `opens_run` of a following follow-up is the same
    in both;
  - old shapes (coordinator), "no crash, documented outcome": a pre-08-27
    rollout (no `completed`, `FINAL_ANSWER` only) → the pair lands as today,
    runs close by `FINAL_ANSWER` / rule 5; a flat-timestamp child (every
    line at the thread creation time, §10) → `turn_complete` rows at that
    time, so the spawn run closes at spawn + ~0.1 s (the documented §10
    limitation).
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_codex_agent_runs_batch.py -x`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite** — `uv run pytest`
- [ ] **Step 5: Commit** — `feat(codex): record agent interactions and run ends in recompute`

---

### Task 5c: Codex live wiring and parity

**Files:**
- Modify: `src/twicc/providers/codex/compute.py` — new
  `apply_agent_run_signals`; new `_lookup_tool_call` next to
  `_lookup_tool_call_payload` (`:2742-2770`); `_resolve_tool_result_id_live`
  (`:2614-2740`); `_lookup_spawn_call_id_for_agent_path` (`:2772-2801`).
- Test: `tests/test_codex_agent_runs_live.py` (new).

**Interfaces:**

```python
def _lookup_tool_call(self, session_id: str, max_line_num: int, call_id: str) -> tuple[dict, int] | None
    # (payload, line_num); _lookup_tool_call_payload keeps its signature and delegates
    # (its 5 callers read the dict, :2708, :2724, :2975, :2983, :2993)
def _lookup_spawn_for_agent_path(self, session_id: str, max_line_num: int, agent_path: str) -> _SubAgentSpawn | None
    # replaces _lookup_spawn_call_id_for_agent_path: same newest-first scan, returns call id AND agent id
def evidence_from_db(session_id: str, agent_id: str, before_line: int) -> FileEvidence   # module function
```

`evidence_from_db` reads, for one owner session and one agent, rows whose
**stored** lines are below `before_line`: spawns (`AgentLink`,
`tool_use_line_num`), resumes with `opens_run` and stops
(`AgentInteraction`, `event_line_num` — §5.1 says every "before line L"
test reads this stored line, never the call line), `completed` and
`owner_turn_aborted` ends (`line_num`), and results (`tool_result_line_num`).
Same filters as the batch builder, so the pure functions see equal input.

The live hook mirrors 5b line by line, writing rows immediately:
- kind from `_tool_use_name(payload)` of `_lookup_tool_call(session_id,
  item.line_num, event_id)`. Live can find a `wait_agent` call that batch
  dropped; `INTERACTION_KIND_BY_TOOL` maps it to "no row" in both (§6.2).
- `get_or_create` on `(session, tool_use_id)`; created with `opens_run` →
  `run_interactions` and `agents_resumed += (agent_id, agent_path)`.
- a created `stop` row whose call already has a non-error `ToolResultLink`
  → `stop_records` (a Codex stop's result can precede its `interrupted`
  line, §5.6).
- turn map: the aborted turn's `task_started` found by a `SessionItem` scan
  of the same session (`content__contains=turn_id`, parsed, below the
  line) — usually in an earlier batch (§5.2).
- fork fields: the child's line-1 `SessionItem`, already bulk-created
  (`compute_base.py:3694`) before the second pass (§5.2).
- `_resolve_tool_result_id_live`: only the v2 `FINAL_ANSWER` branch
  (`:2670-2677`) changes: resolve the path with
  `_lookup_spawn_for_agent_path`, build `evidence_from_db(…, item.line_num)`,
  `attribute_final_answer`, fallback = the spawn found by the path lookup;
  **no spawn for the path → return `naive_tool_use_id` unchanged**, as
  today's `_lookup_spawn_call_id_for_agent_path` does
  (`codex/compute.py:2772-2801`; `tests/test_codex_subagent_links.py:479-487`,
  `test_v2_live_final_answer_of_unknown_agent_stays_unpaired`, depends on it).
  The v1 branch (`:2662-2669`) is untouched.

- [ ] **Step 1: Write the failing tests**
  - **Parity** (§9 "Batch / live parity"): **each** 5b rollout fixture —
    the attribution sequences and the same-timestamp fixture included —
    synced live (one line per sync, then in one chunk) and recomputed batch in a
    fresh DB → identical `AgentInteraction` rows (`tool_use_id`,
    `tool_use_line_num`, `event_line_num`, `kind`, `opens_run`,
    `started_at`), `transcript` `AgentRunEnd` rows and `ToolResultLink`
    `(tool_use_id, tool_result_line_num)` sets;
  - `01a0796d-72bd…` line-210 case live: the `completed` goes to the
    previous run, the follow-up run (`started_at` = its `interacted` time)
    stays open until its own end (§9);
  - interrupt race live = batch rows (§9);
  - `opens_run` live after a root restart (root `last_started_at` moved)
    equals the batch value (§9: no cutoff input);
  - live `agents_resumed` carries `(agent_id, agent_path)` for a created
    `resume` row with `opens_run`, and nothing for `send_message` or a
    merged follow-up;
  - Codex stop row created at its `interrupted` line while its result was
    written in an earlier batch → reported in `stop_records`.
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_codex_agent_runs_live.py -x`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite** — `uv run pytest`
- [ ] **Step 5: Commit** — `feat(codex): record agent interactions and run ends live`

---

### Task 6: the stop step (both providers)

**Files:**
- Modify: `src/twicc/core/agent_runs.py` — `run_stop_step`.
- Modify: `src/twicc/providers/compute_base.py` — `AgentStoppedUpdate`
  (`:118-121`), `AgentLinkUpdate` (`:80-87`), `ToolResultUpdate`
  (`:102-115`); `create_tool_result_link_live` (`:1778-1891`); delete
  `check_agent_naturally_stopped` (`:1893-1943`); `apply_queue_completion`
  (`:2242-2260`); live pass (`:3747-3765`, end of method after `:3960`).
- Modify: `src/twicc/providers/sessions_watcher.py` — new module-level
  `broadcast_agent_run_outcome`; the block `:872-889`; the
  `_after_agents_stopped` docstring (`:339-354`).
- Modify: `src/twicc/providers/codex/sessions_watcher.py` — `_after_agents_stopped`
  comment (`:213-219`).
- Modify (stale comments that name the removed check — Task 11's grep for
  `check_agent_naturally_stopped` in `src/` must return nothing):
  `providers/claude_code/compute.py:2310`, `:2427`;
  `providers/compute_base.py:1551`, `:1989`, `:3325`; and the comment at
  `compute_base.py:3737-3743` ("BEFORE the naturally-stopped check"), which
  the grep does not catch — reword each when the check goes.
- Tests: `tests/test_agent_run_stop_step.py` (new); update
  `tests/test_nested_agent_compute.py`, `tests/test_claude_subagent_lifecycle.py`,
  `tests/test_codex_subagent_links.py`.

**Interfaces:**

```python
class AgentStoppedUpdate(NamedTuple):
    agent_session_id: str
    stopped_at: datetime | None
    stamped: bool = True

class AgentLinkUpdate(NamedTuple):  # existing six fields, plus:
    created: bool = True            # False on an is_background upgrade (:2012-2019, :2111-2115)

class ToolResultUpdate(NamedTuple): # existing fields, plus:
    link_id: int | None = None      # pk of the row create_tool_result_link_live just created

class StopStepResult(NamedTuple):
    run_state_payloads: list[dict]          # serialize_run_state(...) for the KNOWN affected agents
    stopped: list[AgentStoppedUpdate]

def run_stop_step(root_id: str, affected_agent_ids: Iterable[str], exclude: RunStateExclude) -> StopStepResult

async def broadcast_agent_run_outcome(channel_layer, *, root_session_id: str, project_id: str,
                                      run_state_payloads: list[dict],
                                      stopped_updates: list[AgentStoppedUpdate]) -> None
```

**The step** (design §6.3): `before = agent_run_states(root, affected,
exclude=exclude)`, `after = agent_run_states(root, affected)`. A run key
`(owner, tool_use_id)` is **closed by the batch** when it is closed in
`after` and open or absent in `before`. For each agent with such a run and
`not after.running`: stop time = max non-null `closed_at` of those runs;
`None` → `AgentStoppedUpdate(agent, None, stamped=False)`; else the existing
guard, `Session.objects.filter(id=agent).exclude(last_updated_at__gt=t)
.update(last_stopped_at=t, last_updated_at=t)`, and `stamped = rows > 0`.
The update is **returned even when the guard refuses** — that is the §6.3
reversal: the hook must fire for every agent this batch stopped, or a Codex
root keeps a stopped child in `_live_subagents`.

**Collecting affected agents and `exclude` in the live pass** (end of
batch, one pass, a fixed number of queries):
- links: `AgentLinkUpdate`s with `created` → `exclude.agent_links`,
  `affected`. Links are created in four places, not one: the first pass
  (`create_agent_link_from_meta`, `:3653`; `create_agent_link_from_subagent`,
  `:3677`), `create_agent_link_from_tool_result` (`:3745`), the queue
  recovery (`apply_queue_completion`), and `create_agent_link_from_tool_use`
  (`:3769`). All already feed `agent_link_updates`; reading that list covers
  them. The two upgrade returns pass `created=False`.
- results: `ToolResultUpdate.link_id` → `exclude.tool_result_link_ids`.
  Then two indexed queries over the batch's result `tool_use_id`s in this
  session: `AgentLink` → affected; `AgentInteraction` → affected, and for
  `kind == "stop"` whose non-error results are **all** in this batch's
  `link_id`s (at least one) → `exclude.stop_records` (the Claude `TaskStop`
  flip, and a Codex stop whose row predates its result).
- hook outputs: `run_interactions`, `stop_records`, `run_end_ids`,
  `affected_agent_ids`.
- `root_id = session.parent_session_id or session.id`.

Run the step **after** `session.save` (`:3960`), before the return (§6.3:
the save would otherwise overwrite the stamp when the synced file is the
stopped child itself). Its `run_state_payloads` become
`agent_run_state_updates` (`result[8]`, 0-based); its `stopped` becomes
`agent_stopped_updates` (`result[5]`). Remove the per-line
`check_agent_naturally_stopped` call (`:3750-3751`) and the stop half of
`apply_queue_completion`: it returns only the link update now, and its
call site (`:3761-3765`) keeps only that half. The link recovery
(`_resolve_queue_spawn`, `:2211-2240`) is unchanged (§6.1).

**The shared broadcast helper** is extracted from `:870-889` and lives in
`sessions_watcher.py` itself, as a module function. **Trap:**
`tests/test_nested_subagents_watcher.py:37-38` monkeypatches
`sessions_watcher.broadcast_message`; a helper in another module that
imported `broadcast_message` by name would bypass the patch and that test
(which must pass unchanged, §8.1) would lose its `agent_stopped`. It sends,
in order: `agent_run_state` for each payload (`{**payload, "type":
"agent_run_state", "project_id": project_id}`, always, §6.3 step 1); then
per stopped update **only when `stamped`**: the child `session_updated`
(not hidden) and `agent_stopped` (today's shape). The watcher then calls
`_after_agents_stopped(root_id, [all stopped ids])` when the list is not
empty — **the tree root id**, not `session.id` (§6.3: rule-5 evidence comes
from the child's file, and the Codex manager looks the live agent up by the
root, `codex/agent/manager.py:453-454`). Task 7 calls the helper without
the hook.

- [ ] **Step 1: Write the failing tests** (live syncs through
      `sync_session_items_from_file`; watcher-level ones through
      `sync_and_broadcast` with `broadcast_message` patched, as
      `tests/test_nested_subagents_watcher.py`):
  - a run opened and closed in one live batch → `AgentStoppedUpdate` and
    `_after_agents_stopped` fire (§9);
  - two closing pieces in one batch close once: a Claude user-form
    notification line (second result and `AgentRunEnd`); a Codex
    `completed` + `FINAL_ANSWER` → one stamp at the earliest evidence (the
    `completed` time), one `agent_stopped`, the same `stopped_at` as
    `agent_run_states` (§9);
  - a batch whose only evidence reaches an already-closed run (t20 late
    `FINAL_ANSWER`) → no stamp, no `agent_stopped`, no hook;
    `agent_run_state` sent (§9 "Two-call stop step"; §9 "Codex: a child
    `task_complete` after the run's start…" live part);
  - late tree rule: a Claude `SendMessage` run whose end is already in the
    DB, the target's spawn link arrives later with the spawn closed → that
    batch closes the run once (stamp at its `closed_at`, hook); the next
    batch does not (§9);
  - guard refuses: `agent_run_state` and the hook fire, `session_updated` /
    `agent_stopped` do not (§9 "Stop broadcast");
  - null `closed_at` → no stamp, `AgentStoppedUpdate(None, False)`, hook
    fired, `agent_run_state` sent, no `session_updated` / `agent_stopped` (§9);
  - `exclude` sets: Codex stop row created at its `interrupted` line after
    its result in an earlier batch → closes (stamp, `agent_stopped`, hook);
    a Claude subagent `SendMessage` whose root `AgentRunEnd` landed before
    the caller's first result → the `opens_run` flip batch closes it once (§9);
  - rule-3-only close: a Claude child whose link arrives after a root
    restart, no end evidence → stamped at the cutoff; a later root restart
    alone stamps nothing (§9);
  - no `agent_run_state` for a shell or Monitor `AgentRunEnd` (§9);
  - hook routing: a rule-5 row synced from a Codex child file calls
    `_after_agents_stopped` with the **root** id (§9, watcher side; the
    agent side is Task 8);
  - **query budget** (coordinator): the step for a batch touching one agent
    of a 200-agent tree with 15 000 root `ToolResultLink` rows runs the same
    number of queries as on a 5-agent tree (`django_assert_num_queries`);
  - **initial-sync order** (coordinator; §6.3 "created already closed",
    §5.4 late tree rule): one Claude tree (root spawn, ack, subagent-made
    `SendMessage` resume ending in the root file, `TaskStop`, interrupt
    marker) and one Codex tree (spawn, follow-up, child `task_complete`,
    `completed` + `FINAL_ANSWER`), each synced live in natural order and in
    reverse order (children before the root) in two fresh DBs → identical
    final `agent_run_states` for every agent.
- [ ] **Step 2: Update the existing tests this task breaks** (design §8.1):
  - `tests/test_nested_agent_compute.py:274-284`
    (`test_queue_sendmessage_stops_without_creating_or_upgrading_launch`):
    the `SendMessage` has no first result, so it opens no run; the
    `AgentRunEnd` on `continuation` closes nothing → `result[5] == []`;
    still no new link and no upgrade; assert the `AgentRunEnd` row exists.
  - `:264-271` becomes vacuous (no `AgentLink`); keep it, and add next to it
    the same scenario **with** a background link whose ack exists — create
    the link and its ack through `live(owner, home, spawn(), ack())`, or
    give the hand-built link `started_at=NOW`: the root's first live sync
    sets its cutoff to NOW (`compute_base.py:3880-3887`), and a link without
    `started_at` counts as before it (rule 3), is closed in both stop-step
    calls and gives `[]` — then the queue completion closes the run, the guard refuses (child
    `last_updated_at` newer) → `result[5] == [AgentStoppedUpdate("ad123",
    NOW, stamped=False)]`, `last_stopped_at` still `None`.
  - Positional indexes (`:79-87`, `:142-159`, `:261`, `:301`, `:311`) stay.
    `:140-147` must still pass: the recovered link is created already
    closed (rule 4), so the step returns an update with `stamped=False`
    (the child row is deleted).
  - `tests/test_claude_subagent_lifecycle.py:268-330`
    (`TestNaturallyStoppedMonotonicGuard`): re-target the three tests at
    `run_stop_step` on hand-built rows (the stale-stop test now expects an
    update with `stamped=False`, not `None`); fix the module docstring
    (`:8-15`).
  - `tests/test_codex_subagent_links.py:216-259`: `_run_live_sync` and
    `_run_live_sync_collecting` call the deleted method; drive
    `sync_session_items_from_file` instead. Give each line its own
    timestamp: with the shared `_NOW` the ack and the `FINAL_ANSWER` count
    as **one** distinct result (§5.4 rule 1), and
    `test_v2_live_sequence` (`:412-442`) would keep the agent running.
    Docstring `:22`.
- [ ] **Step 3: Run, confirm the new tests fail** — `uv run pytest tests/test_agent_run_stop_step.py -x`
- [ ] **Step 4: Implement**
- [ ] **Step 5: Run the full suite** — `uv run pytest`;
      `tests/test_nested_subagents_watcher.py:16-57` must pass unchanged.
- [ ] **Step 6: Commit** — `feat(agents): close agent runs from run evidence in one step per batch`

---

### Task 7: the Stop button (`ui_stopped`)

**Files:**
- Modify: `src/twicc/core/agent_runs.py` — `record_ui_stop`.
- Modify: `src/twicc/asgi.py` — `_handle_stop_subagent` (`:1384-1441`).
- Test: `tests/test_agent_run_ui_stop.py` (new).

**Interfaces:**

```python
def record_ui_stop(root_id: str, agent_id: str, ended_at: datetime) -> StopStepResult
    # transaction.atomic: create AgentRunEnd(session=root, line_num=None, source="ui",
    # agent_id, tool_use_id="", status="ui_stopped", ended_at), then
    # run_stop_step(root_id, [agent_id], RunStateExclude(run_end_ids=frozenset({row.id})))
```

Handler flow (design §5.2 last bullet):
1. `ended_at = timezone.now()` **just before** `await manager.stop_subagent(…)`
   (`:1435`), outside any lock: a resume whose ack lands during the IPC
   round trip or the lock wait then starts after `ended_at` (a Claude run
   starts at its ack, §5.1) and rule 2 leaves it open.
2. Only when `stopped` is true: `await run_under_db_write_lock(lambda:
   _apply())`, where `_apply` runs `record_ui_stop` through `sync_to_async`
   (transaction committed on return) **then** awaits
   `broadcast_agent_run_outcome(...)` — still inside the locked coroutine,
   so no watcher batch can broadcast a newer `agent_run_state` in between.
3. No `_after_agents_stopped`: the handler has no watcher, and only Claude
   has a Stop button, whose hook is the base no-op.

`session_id` is the root: `ClaudeCodeAgentManager.stop_subagent` checks
`parent_session_id=session_id` (`claude_code/agent/manager.py:353-375`).
`project_id` for the broadcast comes from the root row.

- [ ] **Step 1: Write the failing tests**
  - success → one `ui` row on the root, the step runs, runs started before
    `ended_at` close even with no `killed` notification (§9 "Claude Stop
    button"); a failed stop (`False`) → no row, no broadcast;
  - `ended_at` is read before the stop request (patch `manager.stop_subagent`
    to advance a fake clock); a `SendMessage` whose ack lands between →
    run open; its later `killed` notification closes it (§9 "Stop button:
    `ended_at`…");
  - broadcast order: a watcher batch that resumes the agent right after,
    started while the handler holds the lock, sends its `agent_run_state`
    **after** the handler's; no `_after_agents_stopped` from the handler
    (§9 "Stop-button broadcast order");
  - a root recompute keeps the `ui` row (§9; already covered by Task 3's
    diff test, asserted here end to end).
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_agent_run_ui_stop.py -x`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite** — `uv run pytest`
- [ ] **Step 5: Commit** — `feat(agents): record Stop-button stops as run evidence`

---

### Task 8: Codex live parent process

**Files:**
- Modify: `src/twicc/providers/sessions_watcher.py` — new
  `_after_agents_resumed` next to `_after_agents_stopped` (`:339-354`);
  called in the broadcast block **before** the stop hook (§6.4); comment
  `:348-351`.
- Modify: `src/twicc/providers/codex/sessions_watcher.py` — override next to
  `_after_agents_stopped` (`:210-228`), same fire-and-forget pattern.
- Modify: `src/twicc/providers/codex/agent/manager.py` —
  `notify_subagents_resumed` next to `notify_subagents_stopped`
  (`:442-457`, docstring `:447-450` corrected).
- Modify: `src/twicc/providers/codex/agent/agent.py` — `_stopped_subagent_ids`
  (`:350-367`), the `_live_subagents` comment (`:486-489`), `__init__`
  (`:499`), `_note_sub_agent_activity` (`:1047-1068`), `_prune_finished_subagents`
  (`:1278-1303`), `notify_subagents_stopped` (`:1358-1410`), new
  `notify_subagents_resumed`.
- Tests: `tests/test_codex_agent_runs_live_process.py` (new); update
  `tests/test_codex_subagent_hold.py`, `tests/test_codex_subagent_wait_label.py`.

**Interfaces:**

```python
# providers/sessions_watcher.py
async def _after_agents_resumed(self, session_id: str, agents: list[tuple[str, str]]) -> None  # base no-op
# codex/agent/manager.py
async def notify_subagents_resumed(self, session_id: str, agents: list[tuple[str, str]]) -> None
# codex/agent/agent.py
async def notify_subagents_resumed(self, agents: list[tuple[str, str]]) -> None
def _stopped_subagent_ids(root_id: str, session_ids: list[str]) -> list[str]      # known and not running
def _running_first_level_subagent_ids(root_id: str, session_ids: list[str]) -> list[str]
    # running per agent_run_states AND an AgentLink owned by the root
self._subagent_set_lock = asyncio.Lock()   # in __init__, next to _live_subagents
```

Both sync helpers load the root `Session`; a missing row returns `[]` (keep
everything). They call `agent_run_states(root, ids)` — never
`last_stopped_at` (§5.4: a subagent's `last_stopped_at` is not a run input).

Watcher: the resume hook gets the **root** id and the batch's
`agents_resumed` (`result[9]`, 0-based), and fires before `_after_agents_stopped`.

Agent (design §6.4):
- One lock, not re-entrant, around the **whole body** of
  `notify_subagents_resumed`, `notify_subagents_stopped` and
  `_prune_finished_subagents`. None calls another (the prune is called by
  `_refresh_subagent_wait_label`, `:1227`, and `_try_arm_subagent_hold`,
  `:1320`, neither of which holds the lock), and the awaited callees
  (`_broadcast_process_label`, `_notify_state_change`) do not re-enter.
- Watcher-backed (not `ephemeral`): each of the three reads the DB
  **inside** the lock. Resume adds only `_running_first_level_subagent_ids`
  (nested agents never appear on the root stream); on an add:
  `_schedule_background_work_refresh()` and, when
  `_subagent_wait_label_active` or `_subagent_hold_active`, re-broadcast
  `current_status_label()` (as `:1374-1381`, `:1397-1400`). Stop and prune
  pop only `_stopped_subagent_ids` (known and not running) — not the relay
  payload alone.
- Ephemeral: resume is a no-op (no watcher relay fires anyway); stop pops
  the given ids and prune uses `thread_read` (`:1256-1276`, `:1287-1291`),
  as today, with no DB read, under the same lock. `_note_sub_agent_activity`
  re-adds the child on `interacted` **only** when `ephemeral` (§6.4).
- The hold is not re-armed by a resume after the turn ended (§6.4).
- Correct the stale "`completed` never reaches the parent stream" claim at
  every site §6.4 lists.

- [ ] **Step 1: Write the failing tests**
  - `notify_subagents_resumed` re-adds a running first-level child only; a
    nested agent (link owned by a subagent) adds nothing (§9);
  - the prune keeps a child while its run is open, and keeps a child with
    no link yet (`known=False`) (§9);
  - an ephemeral agent re-adds on `interacted`, a watcher-backed one does
    not (§9);
  - a resume relay that adds a child refreshes background work and the
    label, in a turn (`wait_agent` label) and in the hold (hold label) (§9);
  - relay order: resume relay then the next batch's stop relay, in both
    orders → child not left; an older stop relay (run 1 closed) after a
    newer resume relay (run 2 open) → child stays; the prune in that
    situation keeps it too (§9);
  - ephemeral parent in the hold: its last child idle by `thread_read` →
    `notify_subagents_stopped` pops it with no DB read, the hold releases;
    the prune does the same (§9);
  - owner turn abort end to end: the root's live agent drops the cut
    children; a hold armed by the turn end just before is released by the
    stop hook (§9 owner-abort test, agent part);
  - a rule-5 close from the child file reaches the root's live agent: the
    child leaves `_live_subagents`, the hold releases (§9).
- [ ] **Step 2: Update the existing tests this task breaks** (design §8.1):
  - `tests/test_codex_subagent_hold.py:129-205`: `_agent()` (`:37-60`) sets
    `_subagent_set_lock = asyncio.Lock()`; patch
    `twicc.providers.codex.agent.agent._stopped_subagent_ids` (or
    `agent_run_states`) so the tests keep asserting the hold logic; keep
    `test_unknown_ids_are_a_no_op` meaningful (the patched view reports the
    unknown id as not known).
  - `tests/test_codex_subagent_wait_label.py:183-218`
    (`TestPruningAgainstTheWatcher`): replace the `last_stopped_at` seeding
    with real rows — a root-owned background link per child, the "done"
    child with ack + `FINAL_ANSWER` at distinct timestamps, the "live" one
    with its ack only — and set the lock on the `__new__`-built agent.
- [ ] **Step 3: Run, confirm the new tests fail** — `uv run pytest tests/test_codex_agent_runs_live_process.py -x`
- [ ] **Step 4: Implement**
- [ ] **Step 5: Run the full suite** — `uv run pytest`
- [ ] **Step 6: Commit** — `feat(codex): track resumed subagents in the live parent`

---

### Task 9: history replacement and the `/subagents/` snapshot

**Files:**
- Modify: `src/twicc/providers/codex/rollout_migration.py` —
  `_begin_replace_codex_history` (`:340-356`, deletes at `:350-352`).
- Modify: `src/twicc/core/session_queries.py` — `build_subagents_state`
  (`:200-238`), `serialize_agent_links` (`:241-299`).
- Modify: `src/twicc/share/display.py` — a ceiling helper for call lines.
- Modify: `src/twicc/share/session_views.py` — `share_session_subagents`
  (`:231-243`) passes the ceiling.
- Tests: `tests/test_agent_run_snapshot.py` (new),
  `tests/test_codex_migration_jobs.py` (one test); update
  `tests/test_subagents_tree_endpoint.py`, `tests/test_share_nested_subagents.py`.

**Interfaces:**

```python
def build_subagents_state(root, *, frozen_at_line=None, include_metrics=False,
                          display_ceiling: int | None = None) -> list[dict]
def serialize_agent_links(links, *, run_states=None, interactions=None, root_session_id=None,
                          include_metrics=False, display_names=None) -> list[dict]
    # completions / result_counts / trust_agent_stopped / root_cutoff are removed
def visible_call_lines(pairs: Iterable[tuple[str, int]], ceiling: int) -> set[tuple[str, int]]   # share/display.py
```

Each entry keeps today's fields and gets, from `agent_run_states(root,
<visible link agents>, frozen_at_line)`: `running`, `run_started_at`,
`run_background`, `runs` (`serialize_runs`), `stopped_at` (the agent's, no
longer the link's queue time), and `interactions` = the tree-rule-passing
interactions targeting the agent, each `{owner_session_id, tool_use_id,
tool_use_line_num, kind, opens_run, started_at}` (§7.2). One query for all
interactions of the listed agents, then grouped. The queue-item scan
(`:225-232`) goes away: rule 4 reads `AgentRunEnd` (§5.2 last bullet).
`serialize_agent_links` with no `run_states` gives `running=False`, `runs=[]`
(kept callable for `tests/test_codex_subagent_links.py:567-602`).

Frozen: interactions follow the link filter (root-owned: call line ≤ frozen
line; owned by a visible agent: kept; others dropped), and a kept
root-owned interaction whose deciding line is after the freeze is listed
with `opens_run: false` — the same predicate `agent_run_states` applies
(share it as one function in `core/agent_runs.py`, so the list and the
state never disagree).

Share: when `display_ceiling < 3`, drop interactions whose call item
`(owner_session_id, tool_use_line_num)` has a display level above the
ceiling (§7.2 last bullet); Task 10's relay uses the same helper so a live
viewer and a reloaded one agree.

History replacement also deletes `AgentInteraction` and
`AgentRunEnd(source="transcript")` of the session (§7.1: stale rows keep
old lines and keep closing runs through rule 4).

- [ ] **Step 1: Write the failing tests**
  - snapshot: per-agent state, `interactions`, an interaction owned by a
    subagent, frozen filter, a root-owned `SendMessage` resume after the
    frozen line → not a run (agent stopped, as at the freeze), a root `ui`
    row before / after the freeze time and with no timestamp at or before
    the frozen line (§9 "Snapshot");
  - share snapshot: an interaction above the display ceiling is not listed
    (§9, backend half of "Share snapshot…");
  - snapshot owner scope (§9 "Tree rule owner scope", snapshot half): an
    interaction owned by a session of **another** root, targeting this
    tree's agent, is not in that agent's `interactions`;
  - `_begin_replace_codex_history` deletes the session's interactions and
    `transcript` run ends (§9) — in `tests/test_codex_migration_jobs.py`.
- [ ] **Step 2: Update the existing tests this task breaks** (design §8.1):
  - `tests/test_subagents_tree_endpoint.py:43-47`: `queue()` also seeds the
    `AgentRunEnd` the Claude batch would write (same line, `ended_at` =
    timestamp), since the snapshot no longer scans items;
  - `:63-73` (`test_completion_identity_latest_and_nonterminal`): `stopped_at`
    is now the **earliest** closing evidence (§5.4), so `NOW`, not `later`;
    the `running` status row stays non-terminal (no `AgentRunEnd`);
  - `:76-87` (`test_child_idle_is_provider_gated`): `last_stopped_at` no
    longer closes a run for either provider; rewrite as "`agent_stopped_at`
    is display only" plus a Codex `turn_complete` `AgentRunEnd` after the
    run start → closed. The `subagent_idle_trusted` asserts (`:86-87`) stay
    until Task 11 removes the flag;
  - `:90-96` and `:99-106`: still pass (cutoff rule 3; missing child row);
  - `tests/test_share_nested_subagents.py:46-51`: seed an `AgentRunEnd` at
    line 150 next to `queue(...)`, so the frozen-line check still runs
    (§8.1).
- [ ] **Step 3: Run, confirm the new tests fail** — `uv run pytest tests/test_agent_run_snapshot.py -x`
- [ ] **Step 4: Implement**
- [ ] **Step 5: Run the full suite** — `uv run pytest`
- [ ] **Step 6: Commit** — `feat(api): serve per-agent run state and interactions in the subagents snapshot`

---

### Task 10: WebSocket messages and the share relay

**Files:**
- Modify: `src/twicc/core/agent_runs.py` — interaction payload helpers.
- Modify: `src/twicc/providers/compute_base.py` — build
  `agent_interaction_updates` (`result[7]`, 0-based) at the end of the live pass.
- Modify: `src/twicc/providers/sessions_watcher.py` — broadcast block
  (`:811-846` links, before `:848`).
- Modify: `src/twicc/share/consumer.py` — `_load_descendants` (`:90-101`),
  `broadcast` (`:131-253`), a call-line ceiling check next to
  `_tool_use_visible` (`:111-129`).
- Tests: `tests/test_agent_run_ws.py` (new); extend `tests/test_share_consumer.py`.

**Interfaces:**

```python
def interaction_payloads(root_id: str, keys: Iterable[tuple[str, str]]) -> list[dict]
    # rows by (session_id, tool_use_id) that pass the tree rule, as
    # {root_session_id, owner_session_id, agent_session_id, tool_use_id,
    #  tool_use_line_num, kind, opens_run, started_at}   (§7.3 minus project_id)
def late_tree_rule_payloads(root_id: str, agent_id: str) -> list[dict]
    # interactions TARGETING agent_id (indexed agent_id query) and OWNED by agent_id
    # (session_id query), each passing the tree rule, same payload shape
```

Share relay messages are **flat** (like `share_agent_stopped`, not nested
like `share_agent_link`): the §7.3 app payload minus `project_id`. The
frontend half consumes exactly these keys:

```python
{"type": "share_agent_run_state", "root_session_id", "agent_session_id",
 "running", "run_started_at", "run_background", "runs"}
{"type": "share_agent_interaction", "root_session_id", "owner_session_id",
 "agent_session_id", "tool_use_id", "tool_use_line_num", "kind", "opens_run",
 "started_at"}
```

`root_session_id` is set to `self.session_id` (as `share_agent_stopped`
does, `share/consumer.py:204-208`). Build each message from an explicit key
list, never `{**data, ...}`: the app payload carries `project_id`, which must
not reach a share viewer.

The live pass builds `agent_interaction_updates` from the collector's
`changed_interactions` **after** the stop step, so the tree rule sees every
link of the batch. The watcher, inside the existing gate (`:759`):
1. for each `agent_link_created` (`:828-846`): send it, then its
   `late_tree_rule_payloads` (§7.3 order: `agent_link_created` adds the
   agent to the share relay's `descendant_ids` first), skipping keys that
   are in this batch's `agent_interaction_updates`;
2. then this batch's `agent_interaction_updates`;
3. then workflow / `tool_state` / the Task 6 helper, as today.
`project_id` is added from `parsed.project_id`.

Share relay (§7.3):
- `agent_run_state` → `share_agent_run_state` when `include_subagents` and
  `root_session_id == self.session_id` (like `agent_stopped`, `:201-209`).
- `agent_interaction` → `share_agent_interaction` when `include_subagents`,
  root match, `agent_session_id in descendant_ids`, owner is the shared
  session or in `descendant_ids`, and the call item
  `(owner_session_id, tool_use_line_num)` is at or under the ceiling —
  through the Task 9 helper, not `_tool_use_visible`, which needs a
  `ToolResultLink` and returns false before the first result.
- `_load_descendants` adds the agent ids of `tree_agent_links(root)` (root
  loaded by id): an agent whose link synced before its `Session` row exists
  must still enter the set for a viewer that connects in that window.
- No share reconnect work (§7.3, out of scope).

- [ ] **Step 1: Write the failing tests**
  - `agent_interaction` on creation and on the `opens_run` change, only
    when the tree rule passes; its `started_at` is the ack time after the
    flip (§9 "Claude resume run start", payload part);
  - late tree rule: a Claude interaction synced before its target's link →
    the `agent_link_created` broadcast is followed by its
    `agent_interaction` (§9 "interaction written before its target's spawn
    link", live part); same for an interaction **owned** by the new agent;
  - share relay shapes: each relayed message has **exactly** the key set
    pinned above (`set(message) == {...}`), `project_id` absent;
  - share relay: `share_agent_run_state` and `share_agent_interaction`
    filters (root, descendants, owner, display ceiling); a late interaction
    re-sent after `agent_link_created` passes the descendant filter, both
    when the new agent is its target and when it is its owner (§9);
  - relay seeding: a viewer that connects after an agent's link synced but
    before its `Session` row exists still receives its
    `share_agent_interaction` events (§9 "Share relay seeding");
  - `tests/test_share_nested_subagents.py:77-108` and `:131-152` pass
    unchanged (§8.1).
- [ ] **Step 2: Run, confirm failure** — `uv run pytest tests/test_agent_run_ws.py tests/test_share_consumer.py -x`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite** — `uv run pytest`
- [ ] **Step 5: Commit** — `feat(ws): broadcast agent interactions and run states`

---

### Task 11: removals, compute versions, audit

**Files:**
- Modify: `src/twicc/providers/helpers.py` — delete `subagent_idle_trusted`
  (`:279-280`) and `get_queue_completions` (`:287-289`, no caller left
  after Task 9).
- Modify: `src/twicc/providers/codex/helpers.py` — delete `:180`.
- Modify: `src/twicc/providers/claude_code/helpers.py` — delete
  `get_queue_completions` (`:223-234`).
- Modify: `src/twicc/settings.py` — `CLAUDE_CODE_COMPUTE_VERSION` (`:412`,
  109 → 110) and `CODEX_COMPUTE_VERSION` (`:413`, 49 → 50), each with a
  comment "agent control calls and run ends".
- Modify: `tests/test_subagents_tree_endpoint.py:86-87` — delete the two
  `subagent_idle_trusted` asserts.

- [ ] **Step 1: Delete, bump, and grep.** No hit may remain in `src/` or
      `tests/` for: `subagent_idle_trusted`, `trust_agent_stopped`,
      `check_agent_naturally_stopped`, `get_queue_completions`,
      `_lookup_spawn_call_id_for_agent_path`, and "never reaches the parent"
      in the Codex agent/watcher/manager comments.
- [ ] **Step 2: Audit design §8.1's backend list** — each file below was
      updated in the task named, or passes unchanged by design:
      `tests/test_subagents_tree_endpoint.py` (Task 9 + here),
      `tests/test_claude_subagent_lifecycle.py` (Task 6),
      `tests/test_codex_subagent_links.py` (Task 6),
      `tests/test_nested_agent_compute.py` (Task 6),
      `tests/test_share_nested_subagents.py:46-51` (Task 9),
      `tests/test_codex_subagent_wait_label.py` (Task 8),
      `tests/test_codex_subagent_hold.py` (Task 8); unchanged:
      `tests/test_share_nested_subagents.py:77-108`, `:131-152`,
      `tests/test_nested_subagents_watcher.py:16-57`.
- [ ] **Step 3: Run the full suite** — `uv run pytest`
- [ ] **Step 4: Commit** — `chore(compute): drop the idle gate and recompute agent runs`
      (body: the bumps make the background compute rebuild every Claude and
      Codex session once, at the next backend start).
- [ ] **Step 5: Remind the user** at the end of the backend: migrate (Task 1)
      and restart the backend through `devctl.py` (user-reserved); the
      restart starts the recompute.

---


---

## Frontend tasks

The frontend half consumes the payloads of design §7.2 (the `/subagents/`
snapshot) and §7.3 (`agent_interaction`, `agent_run_state` and their share
relays). It can be written against fixtures, before the backend half lands.
The end-to-end check needs both halves (see Task 20).

**Order.** The store model comes first (Task 12): every later task reads
`agentRunStates` / `agentInteractions` or the new getters. The WS wiring
(Task 13) and the share shim (Task 14) only feed that model. The result
fetch pipeline (Task 15) is independent of agent state: it lands first with
the generic polling predicate for every card, so its commit is a pure
pipeline change. Task 16 then plugs the agent predicate and the agent
widget into it. Task 17 teaches the provider helpers the control-card
counts and labels that Task 16's `helperOptions.agentInteraction` makes
available. Task 18 closes with the subagent header and the stale-comment
sweep.

**Testability decides the file layout.** `node --test` cannot import
`stores/data.js`, `share-session/shims/dataStoreShim.js`,
`composables/useWebSocket.js` or the provider `toolHelpers.js`: they use
extensionless imports (`'../utils/agentLinkIndex'`, `'../baseHelpers'`) and
`.vue` files, which Node's ESM loader refuses. The repo works around this in
two ways, and this plan uses both:

- pure logic lives in modules with **no imports, or only `vue` and
  explicit `.js` imports** (`utils/agentLinkIndex.js`, `utils/sessions.js`,
  `utils/agentLabel.js`), tested by direct import;
- store / shim / component code is tested by **slicing its source** and
  running it through `new Function` with injected dependencies
  (`stores/nestedAgentState.test.js:1-17`,
  `utils/nestedAgentComponents.test.js:7-8`). The slicing harness of
  `nestedAgentState.test.js` cuts an action from its name to the next
  `        /**`, so **every new store action gets a JSDoc block**.

**Every commit** carries a descriptive body and the `Co-Authored-By` trailer
(plan header). No CHANGELOG entry. No lint pass. No new Web Awesome
component is used (only `wa-spinner`, `wa-button`, `wa-icon`, already
imported in `main.js`). No store↔store, store↔composable or
composable→component static import is added (`CLAUDE.md`, *Circular
imports*): the new modules import nothing from stores or components.

Run the suite with `cd frontend && npm test`.

---

### Task 12: the store model

**Files:**
- Modify: `frontend/src/utils/agentLinkIndex.js` — `agentLinkState` (`:4-6`),
  `setAgentLink` (`:9-35`), `markAgentStopped` (`:46-55`), `markAgentIdle`
  (`:56-65`), `applyAgentSnapshot` (`:69-100`), `staleSyntheticAgentIds`
  (`:112-116`), `handleAgentEvent` (`:152-164`); stale comment `:17-18`.
- Modify: `frontend/src/stores/data.js` — import (`:1`), top-level state
  (next to `processStates`, `:472`), getters (next to `getAgentLinkInfo`,
  `:1261`), `updateSession` (`:1575-1627`), `unloadSession` (`:2966-2993`),
  `markAgentStopped` (`:4387-4390`), `setSyntheticProcessState`
  (`:4508-4545`), `fetchSubagentsState` (`:4603-4631`),
  `refreshSessionToolStates` docblock (`:4650-4652`),
  `refreshAllLoadedToolStates` (`:4675-4686`), `setActiveProcesses`
  (`:4839-4948`); the misplaced docblock `:4576-4584` (it describes
  synthetic states but sits above `fetchWorkflowLinks`).
- Rewrite: `frontend/src/utils/agentLinkIndex.test.js`,
  `frontend/src/stores/nestedAgentState.test.js`.
- Modify: `frontend/src/share-session/shims/shareLiveNested.test.js` — only
  its `running` assertions (`:23`, `:75`), see Step 1.

Design §8.1 (rows `agentLinkIndex.js`, `data.js`), §8.2, §10 ("Orchestration
tab while the root's items are not loaded", "Reconnect … with a failed
snapshot").

**Pure logic goes into `agentLinkIndex.js`**, the store keeps thin actions.
`agentLinkIndex.js` has no import today; keep it that way (the share shim,
the tests and Task 16's `agentCardState.js` import it).

**Interfaces** (later tasks depend on these names):

```js
// utils/agentLinkIndex.js
agentLinkState()  // gains: agentRunStates: {}, agentInteractions: {},
                  //        agentRunRevisions: {}, agentInteractionRevisions: {}
// agentRunStates[agentId] = { running, runStartedAt, runBackground, rootSessionId,
//                             runs: { [runKey(owner, toolUseId)]: { open, startedAt } } }
// agentInteractions[owner][toolUseId] = { agentId, rootSessionId, kind, opensRun,
//                             startedAt, toolUseLineNum, ownerSessionId, toolUseId }
export function runKey(ownerSessionId, toolUseId)            // `${owner}:${toolUseId}`
export function runStateFromPayload(payload, rootSessionId)  // snapshot entry or agent_run_state → entry
export function interactionFromPayload(payload, agentId, rootSessionId)
export function setAgentRunState(state, agentId, entry, live = true)
export function setAgentInteraction(state, entry, live = true)
export function dropRootAgentState(state, root)              // → dropped agent ids
export function effectiveAgentRun(runState, cutoffMs)        // → { running, startedAtUnix }
```

```js
// stores/data.js
state:   connectionEpoch: 0                     // top level, next to processStates
getters: getAgentRunState(agentId)              // localState.agentRunStates[agentId] || null
         getAgentInteraction(sessionId, toolId) // localState.agentInteractions[sessionId]?.[toolId] || null
         isAgentRunning(agentId)                // !!processStates[agentId]?.synthetic
         runStatesAvailable                     // always true (the share shim's is a state field)
actions: setAgentRunState(msg)                  // msg = agent_run_state payload (§7.3)
         setAgentInteraction(msg)               // msg = agent_interaction payload (§7.3)
         applyAgentRunState(agentId)
         fetchSubagentsState(projectId, sessionId, { reissued = false } = {})
```

The data store and the shim both expose `runStatesAvailable` and
`connectionEpoch` as plain properties (getter / state field), so a card
reads `dataStore.runStatesAvailable` and `dataStore.connectionEpoch` the same
way in both bundles.

**The rules an implementer can get wrong:**

- **Payload conversion.** `runs` arrives as an array
  `[{owner_session_id, tool_use_id, started_at, open, closed_at}]` (§7.2,
  §7.3) and is stored as a map keyed `runKey(owner, tool)` =
  `{ open, startedAt }`; `closed_at` is not stored (§8.2).
  `runStateFromPayload` reads `payload.runs ?? []` and the snapshot fill
  reads `agent.interactions ?? []`: existing tests call
  `applyAgentSnapshot` with entries that carry neither
  (`nestedAgentComponents.test.js:67`, `shareLiveNested.test.js:21`, `:63`,
  the `api()` helper of `agentLinkIndex.test.js:8`).
- **`setAgentRunState(msg)`** stores the entry (live stamp) whatever the
  root's load state, **then calls `applyAgentRunState(msg.agent_session_id)`**
  (design §8.2: `applyAgentRunState` "runs on `agent_run_state`"). The snapshot
  entry carries the agent as `agent_id` and its `interactions`
  (`{owner_session_id, tool_use_id, tool_use_line_num, kind, opens_run,
  started_at}`, §7.2) under the agent they target; the WS payloads carry it
  as `agent_session_id` (§7.3). The root comes from the fetched root for the
  snapshot, from `root_session_id` for WS. `project_id` is not stored (the
  project is read from the root session, §8.2).
- **Revisions are global stamps.** `setAgentRunState(…, live=true)` writes
  `agentRunRevisions[agentId] = ++state.agentRevision`;
  `setAgentInteraction(…, live=true)` writes
  `agentInteractionRevisions[runKey(owner, tool)] = ++state.agentRevision`.
  Snapshot writes pass `live=false` and stamp nothing. Never reuse
  `agentRevisions`: `markAgentIdle` stamps it (`:64`), and a child
  `session_updated` during a fetch must not block the snapshot's run state
  (§8.2, §9 "Ordering").
- **`applyAgentSnapshot` fill runs first.** After the generation check
  (`:70`) and **before** the link deletion loop and the per-link `continue`
  (`:80`): delete the run states and interactions of that root absent from
  the snapshot whose stamp is `<= token.revision`; then write every
  snapshot run state and interaction whose stamp is `<= token.revision`.
  No "link exists" condition (§8.2). Its return value (applied links) is
  unchanged.
- **No `running` on links any more.** `setAgentLink` drops the
  `running` carry (`:20`), the `running: undefined` of the idle branch
  (`:29`) and the `running:` of `value` (`:30`); it keeps the `stoppedAt`
  merge as a display time (§8.1). `markAgentStopped` drops
  `entry.running = false` (`:52`); `markAgentIdle` drops
  `entry.running = undefined` (`:62`). `applyAgentSnapshot` stops passing
  `running: agent.running` (`:88`).
- **`staleSyntheticAgentIds`** keeps its signature and walks the root's
  `agentRunStates` entries instead of `agentLinkIndex` (§8.1).
- **`handleAgentEvent`**: `agent_link_created` → `setAgentLink` only; the
  synthetic decision (`:161-163`) is deleted (§8.1). `agent_stopped` →
  `store.markAgentStopped` (unchanged). New branches: `agent_run_state` →
  `store.setAgentRunState(msg)`; `agent_interaction` →
  `store.setAgentInteraction(msg)`. Task 13 wires the dispatcher cases.
- **`effectiveAgentRun(runState, cutoffMs)`** is the one copy of the
  frontend cutoff rule (§8.2): running when `runState.running` and
  `runStartedAt` is not before the cutoff; a null `runStartedAt` counts as
  before when `cutoffMs > 0`; `cutoffMs` 0 applies no cutoff.
  `startedAtUnix` = `Date.parse(runStartedAt) / 1000`, or null. The store
  and the share shim (Task 14) both call it.
- **`applyAgentRunState(agentId)`** (§8.2): entry missing, or
  `!localState.sessions[root]?.itemsFetched` → `removeSyntheticProcessState`
  and stop. Otherwise `effectiveAgentRun(entry,
  getSessionCutoffMs(this.sessions[root]))` → running:
  `setSyntheticProcessState(agentId, root, this.sessions[root]?.project_id,
  startedAtUnix)`; not running: remove. `started_at` = the newest open run,
  so `_cleanStaleChildSynthetics` compares the newest run and a resume after
  a root restart is not pruned.
- **`setSyntheticProcessState` only writes**: delete the refusal block
  (`:4509-4516`: `stoppedAt`, the `agentRunEndsOnSubagentIdle` read,
  cutoff). It keeps "never overwrite a real process state" (`:4518-4520`)
  and the provider lookup.
- **`markAgentStopped` action** keeps `cacheAgentStop` and drops
  `removeSyntheticProcessState` (`:4389`): display only (§8.1).
- **`updateSession`**: keep the `markAgentIdle` call (`:1582-1585`, only
  when the child is new or its `last_stopped_at` changes) and the
  `_cleanStaleChildSynthetics` call (`:1586-1589`, before the patch, as
  today). Delete the child safety net (`:1590-1600`). **After** the
  `$patch` (`:1607`), for a root (`!session.parent_session_id`) whose
  `getSessionCutoffMs(prev) !== getSessionCutoffMs(session)`: call
  `applyAgentRunState` for every entry whose `rootSessionId` is that root.
  Before the patch it would read the old cutoff from `this.sessions[root]`
  and re-set a state `_clean` just removed (§8.1 `updateSession` row, §9
  "Root cutoff change").
- **`setActiveProcesses`** re-applies every `agentRunStates` entry right
  after the rebuild loop (`for (const p of processes)`, ends `:4915`), so
  the recompute pass below already sees the restored synthetic states. This
  is the design's "at its end" (§8.1): after the `processStates = {}`
  rebuild. A real process state in `processes` is never overwritten
  (`setSyntheticProcessState` refuses).
- **`unloadSession(sessionId)`**: read `const ownRun =
  localState.agentRunStates[sessionId]` **before** the cleanup. After the
  existing cleanup: `dropRootAgentState(localState, sessionId)` and
  `removeSyntheticProcessState` for each dropped id (the loop at
  `:2987-2992` only reaches agents whose session row is loaded). Then, if
  `ownRun` and `ownRun.rootSessionId !== sessionId`: `applyAgentRunState(
  sessionId)`, and when `localState.sessions[ownRun.rootSessionId]
  ?.itemsFetched`, call `fetchSubagentsState(sessions[root].project_id,
  root)` without awaiting (`clearAgentLinks(agent)` dropped the links the
  agent owns, §8.1). `clearAgentLinks` itself is unchanged.
- **`fetchSubagentsState`** (§8.1, §8.2 "Reconnect"):
  1. `token = beginAgentFetch(…)`; remember the root's run-state ids.
  2. Outcome: HTTP not ok or thrown → `failed`; generation changed
     (`localState.agentFetches[root] !== token.generation`, checked in the
     store **before** calling `applyAgentSnapshot`, which returns `[]` for
     both a discard and an empty tree) → `discarded`; else apply →
     `applied`. `agentLoaded[root] = true` stays in `finally`.
  3. `discarded`, `!reissued`, and `localState.sessions[root]?.itemsFetched`
     read **now** → `return this.fetchSubagentsState(projectId, root,
     { reissued: true })`. A discard caused by `unloadSession(root)` is not
     re-issued (items no longer loaded), so it refills nothing.
  4. After `applied`: `removeSyntheticProcessState` for each remembered id
     that no longer has an entry (deleted by the snapshot).
  5. In every outcome that did not re-issue: `applyAgentRunState` for
     **every** entry of that root, not only the snapshot's agents (an entry
     kept because its live stamp beat the token is re-applied too).
  The old per-link synthetic loop (`:4609-4622`) is deleted.
- **`refreshAllLoadedToolStates`** bumps `this.connectionEpoch++` after its
  `Promise.allSettled` (§8.2, §8.3 rule 6).
- **Stale comments in the code this task rewrites**: `agentLinkIndex.js:17-18`,
  the docblock at `data.js:4576-4584`, `data.js:4650-4652` ("reads
  `toolStates` to decide whether an agent is still running" — it no longer
  does; the order stays harmless). Task 18 checks the full §8.1 list.

- [ ] **Step 1: Write the failing tests**

`utils/agentLinkIndex.test.js` (rewrite; keep the tree / comment-anchor /
replacement tests at `:32-68`, `:136-153`, which do not read `running`):
- §9 "`applyAgentSnapshot` fills `agentRunStates` and `agentInteractions`",
  with the `runs` map keyed `owner:toolUseId`, `closed_at` not stored
  (§9 "`agent_run_state` and the snapshot carry per-run `open` and
  `closed_at`; the store's `runs` map follows `open` and `startedAt`").
- §9 "Ordering": an `agent_run_state` stamped after `beginAgentFetch` wins
  over that snapshot; one stamped before loses; a `markAgentIdle` during
  the fetch does not block the snapshot's run state; a run state arriving
  before its link is kept.
- §9 "Interactions ordering": an `agent_interaction` stamped after the
  token wins; an interaction the snapshot no longer lists is deleted when
  its stamp is not newer than the token.
- §9 "§8.1 reversals" (the store half): `setAgentLink` carries no
  `running` field; `markAgentStopped` records `stoppedAt` only.
- `effectiveAgentRun`: running / not running / `runStartedAt` before the
  cutoff / null `runStartedAt` with and without a cutoff.
- `staleSyntheticAgentIds` walks run-state entries (an agent with a
  synthetic state and a run state but no link is found).
- `dropRootAgentState` drops only the given root's entries and revisions.
- `handleAgentEvent`: `agent_link_created` sets the link and calls no
  `setSyntheticProcessState`; `agent_run_state` / `agent_interaction` reach
  `store.setAgentRunState` / `store.setAgentInteraction` with the message.
  (Replaces `:69-86` and `:113-134`.)

`stores/nestedAgentState.test.js` (rewrite; extend its harness to slice
`applyAgentRunState`, `setAgentRunState`, `setAgentInteraction`,
`fetchSubagentsState`, `setSyntheticProcessState`,
`removeSyntheticProcessState`, `_cleanStaleChildSynthetics`,
`unloadSession`, `updateSession`, `setActiveProcesses`,
`refreshAllLoadedToolStates`, `markAgentStopped`, injecting the
`agentLinkIndex.js` exports and `getSessionCutoffMs` from
`utils/sessions.js` — importable, no imports — plus stubs for the rest
(`apiFetch`, `isLaunchedEphemeral`, `destroyAllBuffers`,
`backgroundWorkStatusKey`, `sweepPendingRequestDrafts`, `liveDraftKey`,
`PROCESS_STATE`, `getToolHelpers`), and a fake `this` with `localState`,
`sessions`, `processStates`, `sessionItems`, `$patch`, `getSessionProvider`,
`recomputeVisualItems`, the no-op hooks `updateSession` calls, and a real
`clearAgentLinks` that calls the imported `clearAgentLinks` from
`agentLinkIndex.js` (a no-op stub would let the `unloadSession` re-fetch test
pass without proving anything: it must assert the owned link is gone
**before** the re-fetch resolves, and back after):
- §9 "`applyAgentRunState`: running / not running / run before the cutoff;
  the synthetic `started_at` is the newest run".
- §9 "An `agent_run_state` message re-creates the synthetic state after a
  stop" (run state running → `markAgentStopped` keeps the synthetic state
  → not running removes it → running again re-creates it).
- §9 "Reconnect: `setActiveProcesses` re-applies run states (synthetic
  states survive), including when `active_processes` lands after the
  snapshot".
- §9 "Store rules": a root restart removes the synthetic state of a run
  started before the cutoff and keeps the one started after it; a snapshot
  that no longer lists an agent removes its synthetic state.
- §9 "Root cutoff change: `applyAgentRunState` runs after the `$patch`, so a
  run started between the old and the new cutoff loses its robot".
- §9 "Root first load with a failed snapshot: the stored run states of that
  root are applied".
- §9 "Post-snapshot apply of every entry" — the design's explicit sequence,
  verbatim: `itemsFetched` false, fetch starts, live run state for an
  absent agent stored (stamp newer than the token) with no synthetic state,
  `itemsFetched` true, snapshot resolves → the kept entry gets its
  synthetic state, and an entry the snapshot deletes loses its own.
- §9 "Reconnect": a response discarded by a generation bump is re-issued
  once and applies; an HTTP error, a network error, and a second discard
  each apply the root's stored run states; a snapshot in flight when
  `unloadSession(root)` runs is not re-issued and refills nothing.
- §9 "Store lifetime": `unloadSession(root)` drops the root's run states and
  interactions; `applyAgentRunState` for a root whose items are not loaded
  removes any synthetic state and sets none.
- §9 "`unloadSession` of a root: the synthetic states of all its
  `agentRunStates` entries are removed, including agents with no loaded
  session row".
- §9 "`unloadSession` of an agent with a running `agentRunStates` entry":
  its synthetic state is back after the call, and its `agentInteractions`
  (the control cards its transcript owns) survive.
- §9 "`unloadSession` of an agent whose root stays loaded re-fetches the
  root snapshot" (the fake `apiFetch` records the URL; the nested spawn's
  link is back after it resolves).
- §9 "§8.1 reversals" (the action half): an `agent_stopped` no longer
  removes the synthetic state; a child `session_updated` with
  `last_stopped_at` no longer removes it; `agent_link_created` no longer
  creates it (through `handleAgentEvent` with the sliced actions).
- `refreshAllLoadedToolStates` bumps `connectionEpoch` once, after the
  refreshes settle (the store half of §9 "Connection epoch").

`share-session/shims/shareLiveNested.test.js`: its first and third tests
assert `running === false` on a link (`:23`, `:75`), a field this task
removes. Replace those two assertions by the display fields they carry
(`stoppedAt`, `agentStoppedAt`); change nothing else (Task 14 extends the
file).

- [ ] **Step 2: Run, confirm the new tests fail** — `cd frontend && npm test`
- [ ] **Step 3: Implement** (rules above)
- [ ] **Step 4: Run the full suite** — `cd frontend && npm test`.
  `utils/nestedAgentComponents.test.js` must stay green: it slices the card
  (untouched until Task 16) and reads no `running` field of a link.
- [ ] **Step 5: Commit** — `feat(ui): store per-agent run states from the backend`

---

### Task 13: the WS dispatcher

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js` — the agent cases
  (`:1568-1571`) and `tool_state` (`:1585-1597`).
- Create: `frontend/src/composables/wsAgentDispatch.test.js`.

Design §7.3, §8.1 rows `useWebSocket.js`.

The cases become `agent_link_created` / `agent_stopped` /
`agent_interaction` / `agent_run_state` → `handleAgentEvent(store, msg)`
(Task 12 implemented the two new branches). `tool_state` keeps its
`store.setToolState(…)` call and loses the whole agent block
(`:1587-1595`, including the stale comment at `:1589`): no agent decision
from a tool count any more (§8.1).

`useWebSocket.js` cannot be imported under Node (see the header), so the
test slices the dispatcher source, as `nestedAgentComponents.test.js` does
for the card.

- [ ] **Step 1: Write the failing tests** (`composables/wsAgentDispatch.test.js`)
  - §9 "WS dispatcher: `agent_interaction` and `agent_run_state` reach the
    store": slice the `case 'agent_link_created':` … `break` block and
    assert it lists the four types and calls `handleAgentEvent`; then run
    `handleAgentEvent` with a fake store and one message of each type.
  - §9 "`tool_state` no longer changes an agent's state": slice the
    `case 'tool_state': {` block, run it with a fake store holding a spawn
    link with `resultCount` at its old required count, and assert the only
    call is `setToolState` (no `getAgentLink`, no `markAgentStopped`).
- [ ] **Step 2: Run, confirm failure** — `cd frontend && npm test`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite**
- [ ] **Step 5: Commit** — `feat(ui): route agent run-state and interaction events to the store`

---

### Task 14: the share viewer

**Files:**
- Modify: `frontend/src/share-session/shims/shareLive.js` — the parameter
  list (`:3`) and the dispatch (`:16-18`).
- Modify: `frontend/src/share-session/shims/dataStoreShim.js` — import
  (`:1`), state (`:28-41`), getters (`:59-70`), actions (`:283-304`).
- Modify: `frontend/src/share-session/ShareSessionApp.vue` — the setup
  snapshot block (`:90-99`), `provide`s (`:100-104`), `onMeta`
  (`:123-129`), the agent callbacks (`:139-145`).
- Modify: `frontend/src/share-session/shims/shareLiveNested.test.js`.

Design §7.3 ("Share relay", "`include_subagents` in the share viewer",
"Share reconnect: unchanged"), §8.1 share rows, §8.3 first bullet.

**Interfaces:**

```js
// shims/dataStoreShim.js
state:   runStatesAvailable: false, connectionEpoch: 0   // + agentLinkState() already brings the maps
getters: getAgentRunState(agentId), getAgentInteraction(sessionId, toolId),
         isAgentRunning(agentId)
actions: setAgentRunState(msg), setAgentInteraction(msg), disableRunStates(root)
// shims/shareLive.js: connectShareLive({ …, onAgentRunState, onAgentInteraction })
// ShareSessionApp.vue: provide('sharedSessionId', meta.session_id)
```

**Payload shapes.** Design §7.3 names `share_agent_run_state` and
`share_agent_interaction` and their filters, not their fields. This task
consumes them **flat**, like `share_agent_stopped`
(`share/consumer.py:201-209`): `share_agent_run_state` =
`{root_session_id, agent_session_id, running, run_started_at,
run_background, runs}`; `share_agent_interaction` = `{root_session_id,
owner_session_id, agent_session_id, tool_use_id, tool_use_line_num, kind,
opens_run, started_at}`. With that shape the shim's `setAgentRunState(msg)`
/ `setAgentInteraction(msg)` take the same arguments as the store's. The
shape is pinned in "Choices settled here", *Share relay payloads*: Task 10
emits exactly this.

**The rules an implementer can get wrong:**

- **`runStatesAvailable`** is set to true **inside** the existing
  `if (ready.value && meta.include_subagents)` block (`:90`), before the
  snapshot fetch — "on at setup" (§7.3). In `onMeta`, when
  `store.runStatesAvailable && !m.include_subagents`:
  `store.disableRunStates(meta.session_id)`, which sets it false **and**
  bumps `agentFetches[root]`, so a setup snapshot still in flight is
  discarded by `applyAgentSnapshot`'s generation check (`:70`). Nothing ever
  sets it back to true (it stays false until reload, like `openSubagent`,
  provided only at setup, `:91`).
- **Shim `isAgentRunning(agentId)`** = `runStatesAvailable` and
  `effectiveAgentRun(entry, getSessionCutoffMs(sessions[entry.rootSessionId]))
  .running` (Task 12's function; `getSessionCutoffMs` from
  `utils/sessions.js`). The cutoff comes from the entry's root, which the
  shim seeds with `last_started_at` / `last_stopped_at` and `onMeta`
  refreshes (`:125-127`). The shim's `getProcessState` stays `() => null`
  (`:70`): no synthetic state in the share viewer.
- **Stored whatever `runStatesAvailable` says.** `setAgentRunState` /
  `setAgentInteraction` store with `live = true` (stamps), exactly like the
  store; every **read** of run state is gated (the getter above, and the
  cards in Task 16). Interactions are not gated: they only decide the
  widget.
- **`markAgentStopped` / `markAgentIdle` / `addAgentLink`** follow Task 12:
  `addAgentLink` drops `running: link.running` (`:301`); the two others
  already delegate to the shared functions.
- **`provide('sharedSessionId', meta.session_id)`** next to
  `provide('sessionActive', …)` (`:100`), unconditionally. Task 16's
  `treeCutoff` injects it (`null` default in the app).
- **No reconnect re-fetch.** `connectShareLive` gains the two callbacks and
  nothing else; no `onopen` hook (§7.3 "Share reconnect: unchanged").
- **Test markers.** The shim's getters and actions are object members,
  sliced by the test. Group the new getters under a
  `// ── Agent runs (share) ──` comment and end the group with
  `// ── end agent runs ──`, same for the actions, so the slice is exact.
- **The bundle is not HMR'd** (`CLAUDE.md`, *Artifact Network Broker*).
  Build it to prove it compiles (Step 5). The output
  (`src/twicc/static/share-session/`) is gitignored: nothing to commit
  from it. To prove it compiles without touching the output a running
  backend serves, build the share bundle to a temporary directory:
  `cd frontend && npx vite build --config vite.config.share.js --outDir
  /tmp/share-check` (Step 5). A failed build blocks the task. The real
  `npm run build` runs in Task 20, Step 0.

- [ ] **Step 1: Write the failing tests** (`shims/shareLiveNested.test.js`)
  - `connectShareLive` routes `share_agent_run_state` and
    `share_agent_interaction` to `onAgentRunState` / `onAgentInteraction`
    with the message.
  - §9 "Share shim: `isAgentRunning`, `getAgentInteraction`,
    `runStatesAvailable`": through the sliced groups — running entry →
    true; entry before the root cutoff → false; `runStatesAvailable` false
    → false even for a running entry; `getAgentInteraction` returns the
    stored entry and null for an unknown call (the §9 "Share snapshot: an
    interaction above the display ceiling is not listed" case: the backend
    omits it, so the card stays generic).
  - §9 "Share `include_subagents` turned off live" (the shim half):
    `disableRunStates` turns it false; a setup snapshot resolved after it
    is discarded (no run state, no link filled); a later
    `include_subagents: true` meta leaves it false.
  - §9 "Share viewer: … a socket reopen re-fetches nothing": drive a close
    and a reopen through the fake `WebSocket` (and fake timers for the
    backoff); no callback fires without a message.
- [ ] **Step 2: Run, confirm failure** — `cd frontend && npm test`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite**
- [ ] **Step 5: Build** — `cd <checkout>/frontend && npx vite build --config
  vite.config.share.js --outDir /tmp/share-check`; it must succeed (a failure
  blocks the task).
- [ ] **Step 6: Commit** — `feat(share): read agent run states in the share viewer`

---

### Task 15: the result fetch pipeline

**Files:**
- Create: `frontend/src/composables/useToolResultFetch.js`,
  `frontend/src/composables/useToolResultFetch.test.js`.
- Modify: `frontend/src/components/session/detail/items/ToolUseContent.vue`
  — replace `onMounted`'s fetch (`:145-154`), the fetch / poll code
  (`:156-363`: `resultState`…`abortController`, `fetchResult`,
  `startPolling`, `stopPolling`, `ensureResultFetched`, the
  `sessionActive` watcher, `resultPollingPaused`, the `onUnmounted`
  cleanup), the fetch calls in `onResultOpen` / `onResultClose` /
  `onToolUseClose` / `onToolUseOpen` (`:274-319`), and the template's
  `isPolling` (`:1083`, `:1113`).

Design §8.3 "The fetch pipeline of a card" (terms, derived values, state,
rules 1-7) and "Display".

**Decision: extract the pipeline into a composable.** Reasons, all grounded:
- The rules are timing rules (3 s ticker, 30/60/120 s stale thresholds,
  single flight with tokens, settle ordering). They need a fake fetch and a
  fake clock. A `.vue` file cannot be imported under `node --test`, and the
  slicing workaround cannot drive watchers, intervals and promises together.
- The composable imports only `vue`: no store, no component, no cycle
  (`CLAUDE.md`, *Circular imports*). It runs the same in the SPA and the
  share bundle (the share alias list, `vite.config.share.js:18-26`, does not
  touch it).
- Task 16 swaps the polling predicate per card type; the pipeline must not
  know what an agent is. It takes the predicate as an input.

**Interfaces:**

```js
// composables/useToolResultFetch.js — imports only from 'vue'
export const POLL_INTERVAL_MS = 3000
export const STALE_THRESHOLDS_MS = [30000, 60000, 120000]   // then none
export function useToolResultFetch({
    fetchRows,         // (signal: AbortSignal) => Promise<Array>   the `results` list
    open,              // () => boolean   Result section (or inline result) visible
    active,            // () => boolean   sessionActive
    count,             // () => number    toolState?.resultCount ?? 0
    displayCount,      // () => number    requiredDisplayCount
    predicate,         // ({ rowCount, count, countChanged, lastSettleFailed }) => boolean
    transcriptFrozen,  // () => boolean
    connectionEpoch,   // () => number
    timers,            // optional { setInterval, clearInterval, now } (tests)
}) → {
    resultState, resultData, resultError,        // refs, same meaning as today
    errorStreak, lastSettleFailed, countChanged, // refs / computed
    showsPending,      // computed: !transcriptFrozen() && predicate(…)  — the polling predicate
    wantsFetch,        // computed (§8.3)
    refreshNote,       // computed: the "Could not refresh" condition
    start,             // () => void — creates the watchers; call once, at the end of setup
}
export function genericCardPredicate({ isToolRunning, rowCount, displayCount,
                                       lastSettleFailed, isStaleToolUse, countChanged })
```

**Why a separate `start()`.** `ToolUseContent.vue` needs `resultData`
early: `helperOptions` reads it (`resultsArray`, `:466`), and
`showResultDetails` (`:621`) reads `helperOptions` through
`getExpectedResultCount` (`:626`). The Entry watcher is `immediate` and
evaluates `open()` → `showResultDetails` → `helperOptions` → `resultData`
during the composable call. If the refs came out of that same call, a card
that mounts open would hit the temporal dead zone of the destructured
`const`. So: call `useToolResultFetch(…)` where `resultState` is declared
today (`:157`), destructure the refs, and call `start()` as the **last**
statement of `<script setup>` (after every computed the getters read). The
getters are closures; nothing evaluates them before `start()`.

**Default timers must be wrapped**: `{ setInterval: (fn, ms) =>
setInterval(fn, ms), clearInterval: id => clearInterval(id), now: () =>
Date.now() }`. Calling the global `setInterval` as a method of another
object throws "Illegal invocation" in browsers.

**The rules** (restated only where an implementer can slip; the design is
the reference):

- **Terms.** `rowCount = resultData?.length ?? 0`. `fetchedAtCount` starts
  `null`; `countChanged = fetchedAtCount !== null && count() !==
  fetchedAtCount`. "Shows rows" (rule 3) = `rowCount >= Math.max(1,
  displayCount())`, the condition under which `displayResult` is non-null.
- **Reactive state.** The slot, `refetchPending`, `resultState` and
  `lastSettleFailed` are refs: `wantsFetch` = `open() && active() &&
  (showsPending || resultState is 'idle'/'loading' || refetchPending ||
  slot busy)` must recompute when any of them moves.
- **Rule 1.** `requestFetch()`: slot busy → `refetchPending = true`; free →
  start. Each start gets a new token and a new `AbortController` whose
  signal goes to `fetchRows` (the share path ignores it, `:182-184`). A
  settle whose token is not the slot's does **nothing** — no rows, no
  state, no slot, no `refetchPending` — so no AbortError branch is needed:
  only invalidated requests are aborted.
- **Rule 2.** Start sets `'loading'` only from `'idle'`, and reads
  `count()` into the request (`countAtStart`).
- **Rule 3.** Success: rows, `'loaded'`, `resultError = null`,
  `fetchedAtCount = countAtStart`, `errorStreak = 0`, `staleKills = 0`,
  `lastSettleFailed = false`. Error: `resultError = err.message`,
  `lastSettleFailed = true`, `errorStreak += 1`, `'error'` unless the card
  shows rows (then it keeps `'loaded'` and its rows), and
  `fetchedAtCount = countAtStart` **only when `errorStreak >= 3`**. Free the
  slot; then `refetchPending && wantsFetch` → clear it, `requestFetch()`.
- **Rule 4.** One `watch(wantsFetch, …, { immediate: true })` starts /
  clears the interval. Starting is idempotent: never two intervals per
  instance. Tick: slot free → `requestFetch()` when `showsPending` or the
  state is `'idle'`/`'loading'`; slot busy for longer than
  `STALE_THRESHOLDS_MS[staleKills]` (none once `staleKills >= 3`) → abort,
  invalidate, `staleKills += 1`, free, `requestFetch()`. A tick never sets
  `refetchPending`.
- **Rule 5 (Leave)** — a watcher on `open() && active()` turning false, and
  `onScopeDispose` (the unmount; it also runs when a test stops its
  `effectScope`): clear the interval, abort and invalidate the slot, free
  it, `refetchPending = false`, `staleKills = errorStreak = 0`,
  `'loading'` → `'idle'`. `lastSettleFailed` is kept.
- **Rule 6 (Entry)** — the same watcher turning true, `immediate`:
  `requestFetch()` when `wantsFetch` or `lastSettleFailed`. `count`
  watcher: on change, when `open() && active()`, `requestFetch()`.
  `connectionEpoch` watcher: on change, when `open() && active()` and
  `lastSettleFailed`, `errorStreak = 0` then `requestFetch()`.
- **Rule 7** follows from the above; `showsPending` and `wantsFetch` are
  computed, never stored.
- **`refreshNote`** = card shows rows and `lastSettleFailed` and
  (`errorStreak >= 3` or `!showsPending`) (§8.3 "Display").
- **`genericCardPredicate`** = `isToolRunning || (rowCount < displayCount
  && !lastSettleFailed && !isStaleToolUse) || countChanged`. The frozen
  gate is the composable's (`showsPending`), not the predicate's.

**Wiring in `ToolUseContent.vue`** (this task, every card on the generic
predicate):
- `fetchRows(signal)`: share path → `(await fetchToolResult(props.lineNum,
  props.toolId, props.parentSessionId)).results`; SPA path → today's URL
  (`:186-189`), `apiFetch(url, { signal })`, `HTTP <status>` error on
  `!ok`, `.results`.
- `open` = `isOpen && showResultDetails && (rendersResultInline ||
  isResultOpen)`. It covers today's four entry points and the section
  hidden by `showResultDetails` (§8.3 rule 5/6). The four handlers keep
  only their ref and `setDetailOpen` writes; their fetch / `stopPolling`
  calls go.
- `active` = `sessionActive.value`; `connectionEpoch` =
  `dataStore.connectionEpoch` (0 in the share shim, Task 14);
  `transcriptFrozen` = the injected ref.
- `predicate` = `genericCardPredicate({ isToolRunning:
  isToolRunning.value, … })`. Spawn cards get `isToolRunning` false
  (`:815`), so until Task 16 they poll on the rows term, i.e. until their
  display rows are in (today's behaviour).
- Template: `isPolling` → `showsPending` in both result branches; under the
  `displayResult` rendering of both branches, `<div v-if="refreshNote"
  class="tool-result-refresh-note">Could not refresh: {{ resultError }}</div>`
  (plain markup, scoped style next to `.tool-result-error`).

- [ ] **Step 1: Write the failing tests** (`composables/useToolResultFetch.test.js`)

Harness: each instance runs in its own `effectScope()` (stopping the scope =
unmount); a fake clock `{ setInterval, clearInterval, now, advance(ms),
liveIntervals() }` that fires due intervals on `advance`; a fake
`fetchRows` returning deferred promises the test resolves or rejects, and
recording each call's `signal`; `await nextTick()` after reactive changes.
One test per §9 behaviour:

- §9 "Single flight": a `count` change during a request only sets
  `refetchPending`, and one more request follows the settle; a response
  slower than 3 s but under 30 s is never killed by a tick and is applied.
- §9 "A tick during a slow request (under the stale threshold) does not set
  `refetchPending`": when the predicate turns false meanwhile, no extra
  request follows the settle.
- §9 "Responses always slower than 30 s": the first stale kill raises the
  threshold to 60 s and the next request lands; later refreshes land too.
  Responses slower than every threshold: after 3 kills the fourth request
  is not killed and lands. A Leave or a success settle resets the count.
  Run it once with an abort-aware fake (SPA) and once with one that
  ignores the signal (share).
- §9 "`staleKills` reset": 3 kills, a success, a hanging request → killed
  after 30 s again.
- §9 "A request that never settles": killed after 30 s, also for the very
  first request (`'loading'` keeps the ticker); no error text; the late
  settle is ignored; retries follow 60 s, 120 s, then none.
- §9 "Ticker at mount": an instance created open with a true predicate
  requests every 3 s.
- §9 "Entry after an error with the Result section closed": card reopen and
  reactivation with the section closed → no request; opening the section
  retries once.
- §9 "No flicker": a loaded card never returns to `'loading'` on a refresh
  (with rows, and with 0 rows showing `showsPending`), also across a
  deactivate / reactivate.
- §9 "Reopen / reactivate with 0 displayable rows", non-agent half (the
  agent half is in Task 16): a running generic card (`genericCardPredicate`)
  with 0 rows, section closed then reopened, and deactivated then
  reactivated → `showsPending` is true at once and the state never shows
  "No result available" before the response.
- §9 "No stuck state": first request in flight, Leave → `'idle'`; reopen →
  a new request; same through `active` false.
- §9 "Leave after a stale kill of the first request": `'idle'` again, the
  next Entry fetches.
- §9 "Slot ownership (share viewer, no abort signal)": slow first response,
  close, reopen → a new request starts at once; the old settle writes
  nothing and does not free the new slot.
- §9 "Success with 0 rows and a false predicate": ends `'loaded'` with no
  rows (never `'idle'`); an error with 0 rows ends `'error'`.
- §9 "Persistent fetch error with `count` ≥ 1": a first error runs no
  ticker (`fetchedAtCount` null); an error on a count change gets 3 tries
  in total, then stops (third error records `fetchedAtCount`); close and
  reopen retries once.
- §9 "Transient error on a count change": fails once, succeeds on the next
  tick → the row shows without a reopen.
- §9 "Long error streak": more than 3 errors while the predicate holds
  through another term, then a count change, then that term ends → the
  next error settle records the new count and the ticker stops.
- §9 "A request that fails while a count change is pending": the settle
  consumes `refetchPending` and fetches again; with nothing pending and a
  false predicate the error stays.
- §9 "Error with rows held": a card showing rows keeps them (`'loaded'`,
  no error state) on one failed refresh and retries; a card holding fewer
  rows than its display count shows the error.
- §9 "Error streak with rows kept" (pipeline half): 3 failures while
  showing rows → `refreshNote` true; a success clears it; after a close /
  reopen whose one retry fails (`errorStreak` 1, predicate false) it is
  still true; same after a failed `connectionEpoch` retry.
- §9 "Connection epoch": after a 3-try streak, an epoch bump retries once
  and shows the row with no `count` change; an epoch bump with
  `lastSettleFailed` false does nothing.
- §9 "A finished card's one-off Entry or `connectionEpoch` retry that hangs
  is stale-killed (the busy slot keeps `wantsFetch` true)".
- §9 "A card whose section closes or that deactivates runs no ticker and
  sends no request afterwards"; §9 "A deactivated (KeepAlive) card does not
  fetch on a `resultCount` change".
- §9 "Leave on the SPA path aborts the in-flight request" (the recorded
  signal is aborted on close and on scope stop).
- §9 "Leave on a hidden section": `open` turning false (the
  `showResultDetails` case) during a request with `refetchPending` set →
  no interval left, and the section fetches again when shown.
- §9 "Entry mirror": a section hidden during its first request, then
  shown again, fetches at once.
- §9 "Own run closing while the Result section polls (count unchanged)"
  (pipeline half, driven by the predicate input): `showsPending` false at
  once; the interval stops at once with no request in flight, else at the
  in-flight settle, which still applies its rows; no extra request.
- §9 "Non-agent visible changes" and "Non-agent card fetch errors", with
  `genericCardPredicate`: a running tool reopened with complete display
  rows polls again; a tool from before the session's cutoff
  (`isStaleToolUse`) stops polling with rows and with none (0 rows → "No
  result available" state, no interval); a reopen or reactivation after an
  error retries once; a failed card retries once on an epoch change and on
  a `count` change; a hung request is killed and retried; a finished Bash
  card whose fetch fails shows the error and runs no interval; a running
  card whose fetch fails retries every 3 s until the tool stops.
- §9 "Non-agent card: the final row lands during a request
  (`refetchPending`) or while the Result section is closed → it is
  fetched".
- §9 "Compaction-duplicated `tool_use` line (`resultCount` 4, 2 rows per
  card)": one fetch per `count` change, no endless polling.
- **Interval hygiene (coordinator addition, design §8.3 rules 4, 5, 7):**
  mount several dozen instances with open sections and mixed predicates;
  close some, deactivate others, stop the scope of others; after each step
  assert `liveIntervals()` equals the number of instances whose `wantsFetch`
  is true — no interval left behind, none doubled.

- [ ] **Step 2: Run, confirm failure** — `cd frontend && npm test`
- [ ] **Step 3: Implement the composable, then wire `ToolUseContent.vue`**
- [ ] **Step 4: Run the full suite.** `nestedAgentComponents.test.js` slices
  `const rootSessionId` → `// Line number`, `const isStaleAgentUse` →
  `// Unix timestamp`, `const agentReportedIdle` → `// Whether the pre-ack`
  and `function navigateToSubagent()` → `// --- Workflow link`: keep those
  blocks and markers intact in this task.
- [ ] **Step 5: Commit** — `feat(ui): fetch tool results through a single-flight pipeline`

---

### Task 16: agent cards

**Files:**
- Create: `frontend/src/utils/agentCardState.js`,
  `frontend/src/utils/agentCardState.test.js`.
- Modify: `frontend/src/components/session/detail/items/ToolUseContent.vue`
  — `helperOptions` (`:421-469`), `isToolRunning` (`:813-822`), the View
  Agent block (`:825-905`), `handleStopAgent` (`:944-948`), the header
  (`:955-1008`).
- Rewrite: `frontend/src/utils/nestedAgentComponents.test.js` (`:14`,
  `:50`, `:72` slice `const agentReportedIdle`, which this task removes).

Design §8.3 (all bullets but the pipeline), §7.3 (`runStatesAvailable`),
§10 ("A resumed agent's own call timestamped before its run start",
"Orchestration tab while the root's items are not loaded").

**Pure rules in `utils/agentCardState.js`**, imported by the card and the
tests. It imports only `./agentLinkIndex.js` (`runKey`) and
`./agentLabel.js` (`getAgentDisplay`), both import-free. Times are compared
as parsed milliseconds (§8.3).

**Interfaces:**

```js
// utils/agentCardState.js
export function startedBeforeCutoff(startedAt, cutoffMs)  // cutoff > 0 && (null || ms < cutoff)
export function treeRootId({ runStateRoot, linkRoot, cardRootId, sharedSessionId })
export function ownRunOpen({ runState, ownerSessionId, toolUseId, isRunCall, cutoffMs,
                             runStatesAvailable, transcriptFrozen })
export function pendingCall({ count, callError, transcriptFrozen, callAt, treeCutoffMs,
                              ownerIsSubagent, runStatesAvailable, ownerRunState, ownerRunning })
export function spawnAwaitingRun({ isTask, count, hasOwnRunEntry, callError, runStatesAvailable,
                                   transcriptFrozen, callAt, treeCutoffMs, helperRunning })
export function spawnPending({ isTask, agentId, transcriptFrozen, callAt, treeCutoffMs,
                               helperRunning, count, pendingCall, callError })
export function needsMoreRows({ countChanged, count, ownRunOpen, rowCount, expectedCount,
                                pendingCall, spawnAwaitingRun })
export function controlCardAgentName(agentId, dataStore)   // name, or `Agent "<short id>"`
// ToolUseContent.vue → helperOptions.agentInteraction (control cards only; Task 17 reads it)
```

**The rules an implementer can get wrong:**

- **`agentInteraction`** = `dataStore.getAgentInteraction(props.sessionId,
  props.toolId)`; `isAgentCard = isTask || !!agentInteraction`;
  `isControlCard = !isTask && !!agentInteraction`; `agentId` = the spawn
  link's `agentId` ?? the interaction's `agentId` (`:832`).
  `navigateToSubagent` and `handleStopAgent` already read `agentId`.
- **`treeCutoff`** = `getSessionCutoffMs(dataStore.getSession(root))` with
  `root = treeRootId(…)`: `agentRunStates[agentId].rootSessionId`, else
  `getAgentLinkInfo(props.sessionId)?.rootSessionId`, else the injected
  `sharedSessionId` when non-null (share viewer, Task 14), else the card's
  `rootSessionId` computed (`:99`, app only). Never the card's
  `rootSessionId` in the share viewer: in the drawer it is the agent itself
  (`SharedSubagentView.vue:32` passes `:parent-session-id="current"`).
- **`ownRunOpen`**: a run call is a spawn (`isTask`) or an interaction with
  `opensRun`; key = `runKey(props.sessionId, props.toolId)`; open and
  `startedAt` not before the cutoff of the root named by
  `agentRunStates[agentId].rootSessionId` (null counts as before a cutoff
  > 0). False when not a run call, when `!dataStore.runStatesAvailable`,
  and when `transcriptFrozen`.
- **`pendingCall`** (agent cards): `count === 0`, `!toolState?.error`,
  `!transcriptFrozen`, the call (`props.timestamp`) not before
  `treeCutoff` (null timestamp never "before"; 0 cutoff passes), and the
  owner gate. Owner gate, only when `props.parentSessionId` is set: if
  `!runStatesAvailable` → not pending, **with or without** an
  `agentRunStates[props.sessionId]` entry; else, when that entry exists →
  `dataStore.isAgentRunning(props.sessionId)` **and** some open run in its
  `runs` has `startedAt` ≤ the call time (a null `startedAt` passes, and a
  null call time `props.timestamp` passes this owner-run comparison too — it
  never counts as "before", design §8.3; add one unit case for it); when
  no entry exists (a workflow agent tab `<run_id>:<agent_id>`,
  `SessionView.vue:2366-2371`) → skip the gate.
- **`spawnAwaitingRun`**: `isTask`, `count > 0`, no
  `runs[runKey(props.sessionId, props.toolId)]` entry (no link counts as no
  entry), `!toolState?.error`, `runStatesAvailable`, `!transcriptFrozen`,
  the call not before `treeCutoff`, and the helper's
  `isToolRunning(props.name, props.input, helperOptions)` true.
- **`needsMoreRows`** = `countChanged` or (`count > 0` and `ownRunOpen`
  and `rowCount < expectedCount`) or `pendingCall` or `spawnAwaitingRun`.
  `expectedCount` = the helper's `getExpectedResultCount(…,
  helperOptions)`; `countChanged` and `rowCount` come from the pipeline, so
  the card's predicate input is the function Task 15 pinned:
  `predicate = p => isAgentCard.value ? needsMoreRows({ ...p, … }) :
  genericCardPredicate({ ...p, … })`. It reads `isAgentCard` reactively, so
  a card that becomes a control card while it polls switches predicate
  (§9 "Card type switch").
- **`isToolRunning`** (`:813-822`) keeps its order and gains one line:
  `transcriptFrozen` → false; `isTask` → false; `agentInteraction` →
  `count === 0 && pendingCall`; then `isStaleToolUse` / helper.
- **Card running indicator**: `isAgentRunning = !transcriptFrozen &&
  !!agentId && dataStore.isAgentRunning(agentId)` for both card types. The
  `isStaleAgentUse` gate goes from it (§8.3); `agentReportedIdle`
  (`:845-851`) goes entirely, with its `agentRunEndsOnSubagentIdle` read.
- **Spawn-pending spinner** (`isAgentSpawnPending`, `:877-883`) =
  `spawnPending`: `!transcriptFrozen`, `isTask`, no `agentId`, the call not
  before `treeCutoff` (replaces `isStaleAgentUse`), the helper running,
  and `count === 0 ? pendingCall : !toolState?.error`. `isStaleAgentUse`
  (`:790-793`) then has no reader: delete it.
- **Header, right side**: the `v-if="isTask"` block (`:967`) becomes
  `v-if="isAgentCard"`; the spawn-pending spinner stays spawn-only. The
  "Agent running for" tooltip (`:979-981`) reads a new computed
  `agentRunStartedAt` = `Date.parse(getAgentRunState(agentId)?.runStartedAt)
  / 1000`, or null (the store keeps the payload's ISO string;
  `ProcessDuration` takes unix seconds, `ProcessDuration.vue:36`), used in
  both the `v-if` (today guarded on `toolStartedAt`, `:979`) and the prop,
  instead of `toolStartedAt`.
- **Stop button** (`:993-1006`): `isAgentRunning &&
  getAgentRunState(agentId)?.runBackground && canStopAgent`, with
  `canStopAgent = !fetchToolResult && !!providerHelpers.canStopSubagent()`
  (`:599`). The injected `fetchToolResult` is always provided in the share
  viewer (`ShareItemsList.vue:99`); `openSubagent` is not a valid signal
  (§8.3). `handleStopAgent` calls `stopSubagent(rootSessionId, agentId)`
  as today.
- **Header, left side**: for control cards, a new `controlAgentName`
  computed (`controlCardAgentName(agentId, dataStore)`) replaces
  `summaryRendering` after the ` — ` separator; spawn cards and every other
  card keep `summaryRendering`. The label comes from `getHeaderLabel`
  (Task 17).
- **`helperOptions`**: `agentSlug` reads `getAgentLinkInfo(agentId)` (link
  by agent id, `:438`, `:461-463`), not `getAgentLink(sessionId, toolId)`, which is
  undefined on control cards; add `agentInteraction: isControlCard ?
  agentInteraction : null`. `helperOptions` is a lazy computed: reading
  `agentId` / `isControlCard` declared further down is safe.
- **Comment** `:825-830` (the View Agent link comment) names control cards
  and the interaction fallback for `agentId` (§8.1 last row).

- [ ] **Step 1: Write the failing tests**

`utils/agentCardState.test.js` — pure rules, then the rules composed with
`useToolResultFetch` (Task 15's harness, fake clock and fetch), the
predicate built as in the card:
- §9 "Own run and cutoff": an `open` run started before the root cutoff →
  `ownRunOpen` false, polling stops without a new snapshot; a control card
  (display count 1) shows its rows; a spawn card below its display count
  ends on "No result available".
- §9 "Frozen share snapshot with a run open at the freeze": `ownRunOpen`
  false, no polling (same two outcomes).
- §9 "Share viewer: the own-run cutoff comes from the agent's root, not the
  drawer's `rootSessionId`".
- §9 "Share viewer: a drawer opened from a `#agent=` hash before the
  snapshot uses the shared session's cutoff (`sharedSessionId`)".
- §9 "`treeCutoff` fallback in the app": a subagent card with no link info
  uses the root (`props.parentSessionId`), so a pending nested spawn from
  before a root restart stops.
- §9 "Share drawer: a pending agent call, and a pending nested spawn with no
  link yet, from before a root restart stop polling and spinning".
- §9 "Pending control card": the generic spinner term
  (`count === 0 && pendingCall`) and "Checking again shortly…"
  (`showsPending`) are both true; once a result arrives, both stop.
- §9 "Pending call made during the older of two open runs of its owner":
  still pending.
- §9 "Pending call owned by a subagent that is then stopped while the root
  runs on": polling and spinner stop together; a later resume of that
  subagent does not revive it (the call predates the new run).
- §9 "Owner gate: a pending agent call in a Claude workflow agent tab (no
  `agentRunStates` entry for the owner) still polls".
- §9 "Control card with `count` 0 and an open own run whose subagent owner
  stops": spinner and "Checking again shortly…" stop together.
- §9 "Link / run state arriving after mount" and "Spawn card between
  `agent_link_created` and `agent_run_state`": `spawnAwaitingRun` keeps the
  card polling; when `ownRunOpen` turns true it keeps polling; never "No
  result available" in between.
- §9 "Failed spawn": a Claude background `Agent` whose only result is
  `Unknown error` → no `spawnAwaitingRun`, no polling, and `spawnPending`
  false.
- §9 "Spawn owner gate": a spawn called by a subagent stopped before the ack
  and the link → polling and `spawnPending` stop (`count` 0 →
  `pendingCall`).
- §9 "Share `include_subagents` turned off live" (card half, with
  `runStatesAvailable` false): no robot, no own-run or awaiting-run
  polling; a root-owned spawn's spinner behaves as today; a subagent-owned
  spawn with `count` 0 stops spinning; an interrupted spawn with a closed
  run and an open section does not start polling; a stopped subagent's
  pending call stops — also with no `agentRunStates` entry for the owner
  (the drawer opened from `#agent=` before a discarded setup snapshot).
- §9 "Result fetch": a `FINAL_ANSWER` row arriving after the run closed is
  still fetched (the `count` watcher); a **control** card whose own run is
  closed with fewer rows than expected shows the rows it has, even while
  the agent runs a later run; a **spawn** card in the same case stops
  polling and shows "No result available"; polling stops once a fetch has
  seen the current `count` and the own run is closed.
- §9 "A Codex v2 spawn card whose run closed before its `FINAL_ANSWER`
  shows 'No result available', not the raw ack, then the answer when it
  lands".
- §9 "`needsMoreRows` on every entry point": open, see the ack, close; the
  second row arrives; reopen → fetched; same on remount (a new instance)
  and on reactivation.
- §9 "Reopen / reactivate with 0 displayable rows (agent card polling)":
  `showsPending` true at once.
- §9 "Reactivation": a Codex v2 spawn card polling on its ack, deactivated,
  its run closes with no count change, reactivated → no request, no
  interval, "No result available"; a card left `'idle'` by the
  deactivation fetches on reactivation.
- §9 "Card type switch": a generic card polling when its interaction lands
  switches to the agent predicate and stops once its run closes, short of
  its expected rows.
- §9 "Own run closing while the Result section polls" (card half: the own
  run closes through `agentRunStates`).
- §9 "Control card: `agentId` from the interaction; name via
  `getAgentDisplay`"; §9 "Control card whose agent has no display name:
  summary `Agent \"<short id>\"`".

`utils/nestedAgentComponents.test.js` — rewrite `:10-23`, `:46-62`,
`:63-81` (they slice the removed `agentReportedIdle` and test the old
count / idle rule); keep the comment-context and navigation tests (`:24-45`)
with their markers:
- §9 "Share viewer: … no Stop button on any agent card": slice the Stop
  button condition and `canStopAgent`, run with `fetchToolResult` set and a
  running background agent → false; without it → true.
- The card's `isAgentRunning` reads only `dataStore.isAgentRunning(agentId)`
  and `transcriptFrozen` (a spawn predating a root restart shows the robot
  when the store says it runs, §8.3).
- §9 "A spawn card keeps its provider summary; a control card shows the
  agent name" (slice the `controlAgentName` / `summaryRendering` template
  branch condition).

- [ ] **Step 2: Run, confirm failure** — `cd frontend && npm test`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full suite**
- [ ] **Step 5: Commit** — `feat(ui): show the agent widget on agent control tool cards`

---

### Task 17: provider helpers

**Files:**
- Create: `frontend/src/providers/claude_code/agentControlTools.js` (+ `.test.js`).
- Create: `frontend/src/providers/codex/agentControlTools.js` (+ `.test.js`).
- Modify: `frontend/src/providers/claude_code/toolHelpers.js` —
  `getHeaderLabel` (`:326-332`), `getExpectedResultCount` (`:562-568`),
  `getRequiredResultCountForDisplay` (`:570-573`).
- Modify: `frontend/src/providers/codex/toolHelpers.js` —
  `getExpectedResultCount` (`:956-1019`, stale comment `:984-992`),
  `getRequiredResultCountForDisplay` (`:1021-1028`), `getHeaderLabel`
  (`:1071`), `getDisplayInputObject` (`:1759`), `agentRunEndsOnSubagentIdle`
  (`:1860-1870`, removed).
- Modify: `frontend/src/providers/baseHelpers.js` —
  `agentRunEndsOnSubagentIdle` (`:1502-1518`, removed).

Design §8.3 ("Expected result count", "Control cards: Header", "Codex input
rendering", "Result rendering of control cards"), §8.1 row
`agentRunEndsOnSubagentIdle`.

**Pure rules in per-provider modules.** The two `toolHelpers.js` cannot be
imported under Node (extensionless imports, `.vue` components), so the
rules live in import-free modules next to them, like
`codex/execRunning.js` / `execRunning.test.js`; the helpers delegate.

**Interfaces:**

```js
// providers/claude_code/agentControlTools.js
export function agentControlHeaderLabel(name, agentInteraction)  // 'Send message' | 'Stop agent' | 'Agent output' | null
export function agentControlExpectedCount(name, agentInteraction) // any control card (agentInteraction set): SendMessage with opensRun → 2, every other control call → 1; no interaction or a non-control name → null (the helper keeps its own rule)
// providers/codex/agentControlTools.js
export const FOLLOWUP_TASK_TOOL_NAME = 'collaboration__followup_task'
export const SEND_MESSAGE_TOOL_NAME = 'collaboration__send_message'
export const INTERRUPT_AGENT_TOOL_NAME = 'collaboration__interrupt_agent'
export function agentControlHeaderLabel(name, agentInteraction)  // 'Follow-up task' | 'Send message' | 'Interrupt agent' | null
export function agentControlExpectedCount(name, agentInteraction) // any control card (agentInteraction set): followup_task with opensRun → 2, every other control call → 1; no interaction or a non-control name → null
export function maskEncryptedMessage(name, input)                 // input with message → 'encrypted message'
```

**The rules an implementer can get wrong:**

- **Labels need an interaction.** `agentControlHeaderLabel` returns null
  when `agentInteraction` is null, whatever the name: 71 of 75 `TaskStop`
  and all 28 `TaskOutput` calls target shells and keep today's label
  (§8.3). The helpers receive it as `options.agentInteraction`, set only
  for control cards (Task 16). Claude's `getHeaderLabel(name)` gains the
  `input, options` parameters the base signature already has
  (`baseHelpers.js:1242`) and checks the control label first.
- **Expected vs display count.** Expected: Claude `SendMessage` and Codex
  `followup_task` → 2 with `opensRun`, else 1 (Claude `SendMessage` is 1
  today, so a resumed run's second row was never fetched live). Codex puts
  the check before the `wrapperType` branches (`:972`). Display: both
  `getRequiredResultCountForDisplay` return **1 whenever
  `options?.agentInteraction` is set**, before their current logic (which
  returns the expected count, `claude_code/toolHelpers.js:572`,
  `codex/toolHelpers.js:1027`). Otherwise the ack hides behind "Result not
  yet available" for the whole run.
- **"encrypted message" goes through `getDisplayInputObject`**, never
  `getInputRendering`: a non-null input rendering hides the Result section
  (`ToolUseContent.vue:639-644`). `maskEncryptedMessage` returns a copy
  with `message: 'encrypted message'` for the two names when the input has
  a `message` field, the input itself otherwise. It applies whether or not
  the card is a control card yet (name-based).
- **Remove `agentRunEndsOnSubagentIdle`** (base and Codex). After Tasks 12
  and 16 no caller is left: prove it with `grep -rn
  agentRunEndsOnSubagentIdle frontend/src` → no match.
- **Stale comment** `codex/toolHelpers.js:984-992` ("matching the
  agent-running semantics the View-Agent UI relies on"): the count now
  drives fetching only; the running state comes from the backend (§8.1).

- [ ] **Step 1: Write the failing tests**
  - §9 "Header label: a shell `TaskStop` and a shell `TaskOutput` card keep
    today's label; only agent cards read 'Stop agent' / 'Agent output'"
    (Claude); the Codex labels with and without an interaction.
  - §9 "Display count" (helper half): a resumed `SendMessage` and an opened
    `followup_task` expect 2 rows; a queued `SendMessage`, a merged
    `followup_task`, `send_message`, `interrupt_agent`, `TaskStop`,
    `TaskOutput` expect 1; non-control names return null (the helper keeps
    its own rule).
  - §9 "Display count" (pipeline half): with Task 15's harness, expected 2
    and display 1 → the ack shows at once while the open own run keeps
    polling for the second row.
  - §9 "Result fetch … the Claude `SendMessage` expected count flip 1 → 2
    fetches the second row" (§8.3 last bullet of the pipeline): the flip
    turns the own-run term true; the `count` watcher fetches the row when
    it lands.
  - `maskEncryptedMessage`: both names masked, other names and inputs
    without `message` untouched, the original object not mutated.
- [ ] **Step 2: Run, confirm failure** — `cd frontend && npm test`
- [ ] **Step 3: Implement and delegate from the helpers; remove the idle gate**
- [ ] **Step 4: Run the full suite**
- [ ] **Step 5: Commit** — `feat(providers): label and count agent control tool calls`

---

### Task 18: subagent header, and the stale-comment sweep

**Files:**
- Modify: `frontend/src/components/session/detail/SessionHeader.vue` —
  `canStopAgent` (`:255-266`).
- Modify (comments only, where still stale): the §8.1 list —
  `data.js:4579-4581`, `data.js:4650-4652`, `SessionItemsList.vue:707`,
  `codex/toolHelpers.js:984-992`, `agentLinkIndex.js:17-18`,
  `useWebSocket.js:1589`, `ToolUseContent.vue:825-830`.
- Check, no change: `frontend/src/components/orchestration/AgentTreeNode.vue`.
- Modify: `frontend/src/utils/nestedAgentComponents.test.js`.

Design §8.4, §8.1 rows `SessionHeader.vue`, `AgentTreeNode.vue`, last row.

- **`canStopAgent`** keeps every gate but the link checks
  (`props.mode === 'subagent'`, not `ephemeral`, a synthetic non-dead
  process state, `parent_session_id`, `canStopSubagent()`), and replaces
  `if (!link?.isBackground || link.stoppedAt) return false` (`:261-262`) by
  `store.isAgentRunning(props.sessionId) &&
  store.getAgentRunState(props.sessionId)?.runBackground`.
- **The turn duration** (`:843-853`) reads the synthetic
  `state_changed_at`, which is now the current run's start
  (`runStartedAt`): intended (§8.1), no change.
- **`AgentTreeNode.vue`**: `isRunning` (`:55`) reads the process state,
  which Task 12 keeps in step with the run state; `finishedAt` (`:124`)
  reads `stoppedAt ?? agentStoppedAt`, both still maintained as display
  values by Task 12. Confirm, change nothing.
- **Comments**: most of the list was rewritten with the code it describes
  (Tasks 12, 13, 16, 17). This task greps each location and fixes what is
  left: `SessionItemsList.vue:707` ("needed by fetchSubagentsState to
  determine agent running status" — the snapshot now carries the state;
  the order of the two fetches stays).

- [ ] **Step 1: Write the failing test** (`utils/nestedAgentComponents.test.js`)
  — §9 "§8.1 reversals: … `SessionHeader.vue` `canStopAgent` reads
  `isAgentRunning` + `runBackground`": slice the `canStopAgent` computed,
  run it with a fake store: running + `runBackground` → true; a link with
  `stoppedAt` set and a running background run → true (the link no longer
  decides); `runBackground` false → false; not running → false.
- [ ] **Step 2: Run, confirm failure** — `cd frontend && npm test`
- [ ] **Step 3: Implement; sweep the comments**
- [ ] **Step 4: Run the full suite**, then `grep -rn "isBackground\|stoppedAt"
  frontend/src/components/session/detail/SessionHeader.vue` → no match.
- [ ] **Step 5: Commit** — `feat(ui): gate the subagent header Stop on the agent's run state`

---


---

## Closing tasks

### Task 19: project docs

**Files:** `CLAUDE.md` (*Database Models*, the `ToolResultLink` / `AgentLink`
line, `:127`), `AGENTS.md` (the same line, `:128`).

Add one bullet for the two new models next to the existing
`ToolResultLink` / `AgentLink` line, in the file's register (non-obvious points
only, as the section's intro asks):

- **`AgentInteraction`** — one agent-control tool call (Claude `SendMessage` /
  `TaskStop` / `TaskOutput`, Codex `followup_task` / `send_message` /
  `interrupt_agent`) targeting an agent; `opens_run` makes it a run; counted only
  through the tree rule (target link **and** owner in the root tree).
- **`AgentRunEnd`** — end evidence that is not a tool result (Claude
  notifications, the child interrupt marker, the Stop button's `ui_stopped` row;
  Codex `completed`, child turn end, owner turn abort). Whether an agent runs is
  decided only by `agent_run_states` (design
  `docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md`).

`AGENTS.md` must follow `CLAUDE.md` word for word on this section. No plugin /
skill change, so no `plugin.json` bump and no `SKILLS-AND-CLI.md` change (the CLI
`session agents` follow-up is out of scope, design §3.3).

- [ ] **Step 1:** edit both files identically.
- [ ] **Step 2: Commit** — `docs: describe AgentInteraction and AgentRunEnd`

---

### Task 20: end-to-end check with the user (manual)

Not a code task. It needs the user: never restart the servers, never run
`migrate` on the main instance.

- [ ] **Step 0: full test suites and build.** `uv run pytest`, `cd frontend &&
  npm test`, then `cd frontend && npm run build` (the standalone share bundle
  included; the build output is gitignored, nothing to commit).

- [ ] **Step 1: recompute check on a copy.** In a worktree (created only when
  the user asks for one), start the servers
  with `uv run ./devctl.py start` (it copies the user's db on first setup and
  applies the migration there); the compute-version bump makes the background
  compute rebuild every session. When it is done, compare on that copy:
  - the known cases of design §4 / §9 by id: t20 in `01a08171…` (runs and their
    `closed_at`), the `01a0796d…` owner abort at line 704, the `aa6d89b7469be1a2c`
    spawn (compaction copies), the `ab35d70110fd1e813` stop;
  - no agent reported running under a root whose process is dead and whose
    cutoff is after the run start (rule 3);
  - the count of running agents per tree against the Orchestration tab.
  Report the numbers to the user.
  (There is no command that recomputes one session: the bump plus a backend
  start recomputes every session whose `compute_version` differs,
  `background_compute_task.py:546`, selection `:612-619`; for one session, set
  its `compute_version` to `NULL` in a Django shell under the worktree
  data-dir rule, then ask the user to restart.)
- [ ] **Step 2: live scenarios**, run by the user on the worktree instance with
  a real Claude session and a real Codex session. Each time the Orchestration
  tab, the subagent tab header and the cards must agree:
  - Claude: a background `Agent` ends → `SendMessage` resumes it → the robot on
    the spawn card, the `SendMessage` card (label "Send message", agent name,
    View Agent, Stop), the subagent tab and the Orchestration tree → the end
    notification stops all of them; the `SendMessage` card shows its ack at
    once and fetches the second row.
  - Claude: the Stop button on a control card and in the subagent header; a
    root interrupt while children run.
  - Claude: a shell `TaskStop` / `TaskOutput` keeps its generic card.
  - Codex v2: `followup_task` on an idle agent (widget, "encrypted message",
    robot until its end), `send_message` (widget, no state), `interrupt_agent`.
  - Reconnect: the user stops the backend while an agent runs and restarts it →
    states re-applied; a failed result fetch retries once the socket is back.
- [ ] **Step 3: share viewer** — a live share with `include_subagents` on
  (robot and control widgets, no Stop button anywhere), then turned off live
  (every robot gone until reload): the rules of design §7.3.
- [ ] **Step 4:** remind the user to apply the migration and restart their own
  instance when they merge; worktree cleanup (`stop all` then `kill-tmux`) only
  when they ask.
