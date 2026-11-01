# Claude Task Tools Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render, in the detail panel of every `TaskCreate` / `TaskUpdate` / `TaskGet` tool_use, a `<wa-divider>` followed by the full list of session tasks as it stood at the moment of the call — mirroring how `TaskList` renders today. Freeze that snapshot against later recomputes so the historical status of each task is preserved.

**Architecture:** Reconstruct the task state in-memory from the tool_use inputs themselves (no disk reads). `ClaudeCodeSessionCompute` keeps a per-session `dict[task_id_str, task_dict]` and advances it on each `TaskCreate` / `TaskUpdate` it sees in `transform_inline`. Each task tool_use gets enriched with a snapshot (`twiccTaskData`, `twiccTasksData`, `twiccTasksTotal`). Idempotence preserved via the existing `'twiccTaskData' in block` / `'twiccTasksData' in block` checks. When the state is empty for a session already persisted in DB, lazy reconstruction: find the last item carrying `twiccTasksData`, restore state from it, then replay subsequent `TaskCreate` / `TaskUpdate` items.

**Tech Stack:** Python 3.13, Django 6, orjson (backend); Vue 3 (Composition API), Web Awesome 3 (frontend).

**Notes:**
- This project uses **no tests, no linting** (per `CLAUDE.md`). Steps that would normally be TDD are replaced with concrete code edits, then manual verification at the end.
- Reference spec: `docs/superpowers/specs/2026-05-18-claude-task-tools-snapshot-design.md`.
- **Do not bump `CLAUDE_CODE_COMPUTE_VERSION`.** Intentional — preserves the (imperfect, disk-based) snapshots already captured on older sessions. The user will retest active sessions himself.
- **Never restart the dev servers.** Reserved to the user (per `CLAUDE.md`).
- **Locate edits by symbol/function name, not by line number.** Line ranges in this plan are indicative and may drift if the file is touched in parallel work.
- **History note:** the branch already has 7 commits — 3 backend commits (`b12987a9`, `a01500d5`, `63d8f98a`) that implemented a disk-read approach (now superseded by this pivot), 3 frontend commits (`c86f99c7`, `78f784a3`, `2b652f3f`) that are **still valid** since the frontend consumes `twiccTaskData` / `twiccTasksData` / `twiccTasksTotal` regardless of how the backend produces them, and 1 spec doc commit (`ba06d0f0`) that documents the pivot. The user will squash the whole branch at the end, so we don't rewrite history here — we add new commits that supersede the disk-read backend logic.

---

## Status Summary

| Component | Status | Notes |
|---|---|---|
| Worktree `.worktrees/feature-claude-task-tools-snapshot` | ✅ Done | branch `feature/claude-task-tools-snapshot` |
| Spec doc updated for the pivot | ✅ Done | commit `ba06d0f0` |
| Frontend — `TaskByIdContent.vue` | ✅ Done | commit `c86f99c7`, unchanged by pivot |
| Frontend — `getInputRendering` branch in `toolHelpers.js` | ✅ Done | commit `78f784a3`, unchanged by pivot |
| Frontend — `ContentList.vue` docstring | ✅ Done | commit `2b652f3f`, unchanged by pivot |
| Backend — old disk-read enrichment in `compute.py` | ❌ To be replaced | added in `b12987a9`, body fully rewritten in Task 1 |
| Backend — old `TasksReader` class in `tasks.py` | ❌ To be removed | partially gutted in `63d8f98a`, fully deleted in Task 2 |
| Backend — new in-memory state machinery | 🔲 To do | Task 1 |
| Backend — drop `tasks.py` entirely | 🔲 To do | Task 2 |
| Manual verification | 🔲 To do (user) | Task 3 |

---

## File Structure (for the pivot)

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/twicc/providers/compute_base.py` | Thread `line_num` through `transform_inline`'s signature and its two call sites (`compute_session_metadata`, `sync_session_items_from_file`). The base implementation does nothing with it — only the Claude Code override needs it. |
| Modify | `src/twicc/providers/claude_code/compute.py` | Replace the disk-based body of `_enrich_task_tool_uses` with the in-memory state machinery. Add `__init__` on `ClaudeCodeSessionCompute` to initialise `_session_task_states`. Add helpers `_next_task_id`, `_apply_task_create`, `_apply_task_update`, `_rebuild_state_if_missing`. Add module-level helpers `_extract_tasks_snapshot`, `_iter_task_tool_use_blocks`. Update the `transform_inline` override to accept `line_num` and forward it. Remove the `from .tasks import TasksReader` import. Remove `_TASK_LOOKUP_BY_ID_TOOLS` if unused after the rewrite. |
| Modify | `src/twicc/providers/codex/compute.py` | Update its `transform_inline` override signature to accept (and ignore) `line_num`. |
| Delete | `src/twicc/providers/claude_code/tasks.py` | Entire file disappears — no more disk reads. |

---

## Task 1: Backend — replace disk-read enrichment with in-memory state

**Files:**
- Modify: `src/twicc/providers/compute_base.py`
- Modify: `src/twicc/providers/claude_code/compute.py`
- Modify: `src/twicc/providers/codex/compute.py`

This task replaces the body of `_enrich_task_tool_uses` (and the surrounding helpers) with the new in-memory state machinery. The function signature changes from a module-level free function to an **instance method** on `ClaudeCodeSessionCompute` because it now needs access to `self._session_task_states`. It also threads `line_num` through `transform_inline` so the reconstruction can scope its DB query correctly.

### Subtasks

- [ ] **Step 1.1: Locate the existing pieces to inspect**

  Before writing code, read:
  - `src/twicc/providers/claude_code/compute.py` — locate the free function `_enrich_task_tool_uses(content, session_id)`, the `_TASK_LOOKUP_BY_ID_TOOLS` constant, the `from .tasks import TasksReader` import, and the call site inside the `transform_inline` method (`if content is not None and _enrich_task_tool_uses(content, session_id): …`).
  - `src/twicc/providers/compute_base.py` — locate `def transform_inline(self, parsed_json: dict) -> str | None:` and its two call sites (one in `compute_session_metadata` around line 1701, one in `sync_session_items_from_file` around line 2292). Each call site has a `line_num` in scope (`item.line_num` or `current_line_num`).
  - `src/twicc/providers/codex/compute.py` — locate the `def transform_inline(self, parsed_json: dict) -> str | None:` override.

- [ ] **Step 1.2: Thread `line_num` through `transform_inline`**

  In `src/twicc/providers/compute_base.py`:
  - Change the base signature to `def transform_inline(self, parsed_json: dict, *, line_num: int) -> str | None:`. Keep the existing default implementation body (which just returns `None`).
  - Update both call sites to pass `line_num=item.line_num` (batch) or `line_num=current_line_num` (live).

  In `src/twicc/providers/codex/compute.py`:
  - Update the override signature to `def transform_inline(self, parsed_json: dict, *, line_num: int) -> str | None:`. The body doesn't use `line_num` — that's fine.

  In `src/twicc/providers/claude_code/compute.py`:
  - Update the override signature to `def transform_inline(self, parsed_json: dict, *, line_num: int) -> str | None:`. The call to `_enrich_task_tool_uses` will be updated in Step 1.6 to pass `line_num` along.

- [ ] **Step 1.3: Add `__init__` to `ClaudeCodeSessionCompute`**

  First check if `BaseSessionCompute` has an `__init__`:

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && grep -n "def __init__" src/twicc/providers/compute_base.py
  ```

  Then add the per-session state attribute. Place it just below the `provider: ClassVar[Provider] = Provider.CLAUDE_CODE` declaration:

  ```python
      def __init__(self) -> None:
          super().__init__()  # drop this line if BaseSessionCompute has no __init__
          # Per-process in-memory task state, indexed by session_id.
          # Inner dict: insertion-ordered task_id_str -> task_dict.
          # Reconstructed lazily on the first transform_inline that needs
          # it (see _rebuild_state_if_missing).
          self._session_task_states: dict[str, dict[str, dict]] = {}
  ```

- [ ] **Step 1.4: Add module-level helpers `_extract_tasks_snapshot` and `_iter_task_tool_use_blocks`**

  Place both helpers near the top of `compute.py`, close to `get_message_content_list` (already defined). These are pure parsers reused by reconstruction.

  ```python
  _TASK_TOOL_NAMES = frozenset({'TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList'})


  def _extract_tasks_snapshot(parsed_json: dict) -> list[dict] | None:
      """Return the first ``twiccTasksData`` list embedded in an assistant
      message's tool_use blocks. None when not found or malformed."""
      content = get_message_content_list(parsed_json, 'assistant')
      if content is None:
          return None
      for block in content:
          if not isinstance(block, dict) or block.get('type') != 'tool_use':
              continue
          snapshot = block.get('twiccTasksData')
          if isinstance(snapshot, list):
              return snapshot
      return None


  def _iter_task_tool_use_blocks(parsed_json: dict):
      """Yield tool_use blocks whose name is one of the four task tools."""
      content = get_message_content_list(parsed_json, 'assistant')
      if content is None:
          return
      for block in content:
          if (
              isinstance(block, dict)
              and block.get('type') == 'tool_use'
              and block.get('name') in _TASK_TOOL_NAMES
          ):
              yield block
  ```

  Note: `_TASK_TOOL_NAMES` becomes the single source of truth for "is this a task tool" inside the file. The existing `_TASK_LOOKUP_BY_ID_TOOLS = frozenset({'TaskUpdate', 'TaskGet'})` is retained because it's used to gate `twiccTasksTotal` write (see Step 1.6).

- [ ] **Step 1.5: Add state-manipulation helpers on `ClaudeCodeSessionCompute`**

  ```python
      def _next_task_id(self, state: dict[str, dict]) -> str:
          """Sequential id allocator. First id is '1', then max(ids)+1."""
          if not state:
              return "1"
          return str(max(int(k) for k in state) + 1)

      def _apply_task_create(self, state: dict[str, dict], tool_input: dict) -> dict | None:
          """Add a new task to state. Returns the new task dict, or None
          when the input is malformed (missing subject)."""
          subject = tool_input.get('subject')
          if not isinstance(subject, str) or not subject:
              return None
          new_id = self._next_task_id(state)
          # Merge all input fields as-is, then default status to 'pending'
          # and set our authoritative id. Any incoming 'id'/'taskId' is
          # dropped (TaskCreate input shouldn't carry them; defensive).
          task = {
              **{k: v for k, v in tool_input.items() if k not in ('id', 'taskId')},
              'status': 'pending',
              'id': new_id,
          }
          state[new_id] = task
          return task

      def _apply_task_update(self, state: dict[str, dict], tool_input: dict) -> dict | None:
          """Merge update fields into the existing task. Returns the updated
          task dict, or None when taskId is missing or unknown."""
          task_id = tool_input.get('taskId')
          if not isinstance(task_id, str) or not task_id:
              return None
          existing = state.get(task_id)
          if existing is None:
              return None
          for k, v in tool_input.items():
              if k in ('taskId', 'id'):
                  continue
              existing[k] = v
          return existing

      def _rebuild_state_if_missing(self, session_id: str, current_line_num: int) -> dict[str, dict]:
          """Ensure self._session_task_states[session_id] is populated
          consistently with the session's items already persisted in DB
          up to (but not including) current_line_num.

          Algorithm:
            1. If state already exists, return it.
            2. Initialise empty state.
            3. Find the latest SessionItem (line_num < current_line_num)
               whose content contains 'twiccTasksData'. Use that snapshot
               to seed the state.
            4. Replay TaskCreate / TaskUpdate items between that snapshot
               (exclusive) and current_line_num (exclusive).
          """
          state = self._session_task_states.get(session_id)
          if state is not None:
              return state

          state = {}
          self._session_task_states[session_id] = state

          snapshot_item = (
              SessionItem.objects
              .filter(
                  session_id=session_id,
                  line_num__lt=current_line_num,
                  content__contains='twiccTasksData',
              )
              .order_by('-line_num')
              .first()
          )

          replay_after_line = 0
          if snapshot_item is not None:
              try:
                  parsed = orjson.loads(snapshot_item.content)
              except orjson.JSONDecodeError:
                  parsed = None
              snapshot = _extract_tasks_snapshot(parsed) if parsed else None
              if snapshot is not None:
                  for task in snapshot:
                      if not isinstance(task, dict):
                          continue
                      task_id = task.get('id')
                      if isinstance(task_id, str):
                          state[task_id] = dict(task)
                  replay_after_line = snapshot_item.line_num

          replay_items = (
              SessionItem.objects
              .filter(
                  session_id=session_id,
                  line_num__gt=replay_after_line,
                  line_num__lt=current_line_num,
              )
              .filter(
                  Q(content__contains='"name":"TaskCreate"')
                  | Q(content__contains='"name":"TaskUpdate"')
              )
              .order_by('line_num')
          )
          for item in replay_items:
              try:
                  parsed = orjson.loads(item.content)
              except orjson.JSONDecodeError:
                  continue
              for block in _iter_task_tool_use_blocks(parsed):
                  name = block.get('name')
                  tool_input = block.get('input') or {}
                  if name == 'TaskCreate':
                      self._apply_task_create(state, tool_input)
                  elif name == 'TaskUpdate':
                      self._apply_task_update(state, tool_input)

          return state
  ```

- [ ] **Step 1.6: Rewrite `_enrich_task_tool_uses` as an instance method**

  Delete the free function `_enrich_task_tool_uses(content, session_id)`. Add the new instance method on `ClaudeCodeSessionCompute`:

  ```python
      def _enrich_task_tool_uses(self, content: list, session_id: str, line_num: int) -> bool:
          """In-memory enrichment of the four task-tracking tool_use blocks.

          For each tool_use of name TaskCreate / TaskUpdate / TaskGet /
          TaskList in ``content``:
            * If the block already carries ``twiccTasksData`` (TaskList path)
              or ``twiccTaskData`` only (legacy disk-based by-id), the block
              is left untouched (immutability). On the ``twiccTasksData``
              path, the in-memory state is reset from the snapshot so
              subsequent blocks remain consistent.
            * Otherwise, the in-memory state is advanced and the block is
              enriched with ``twiccTaskData`` (when applicable),
              ``twiccTasksData`` (always), and ``twiccTasksTotal`` (only
              for by-id tools matching ``_TASK_LOOKUP_BY_ID_TOOLS``).

          Returns True if any block was mutated.
          """
          mutated = False
          state: dict[str, dict] | None = None

          for block in content:
              if not isinstance(block, dict) or block.get('type') != 'tool_use':
                  continue
              name = block.get('name')
              if name not in _TASK_TOOL_NAMES:
                  continue

              # --- Immutability paths ---
              if 'twiccTasksData' in block:
                  if state is None:
                      state = self._rebuild_state_if_missing(session_id, line_num)
                  state.clear()
                  snapshot = block.get('twiccTasksData')
                  if isinstance(snapshot, list):
                      for task in snapshot:
                          if not isinstance(task, dict):
                              continue
                          task_id = task.get('id')
                          if isinstance(task_id, str):
                              state[task_id] = dict(task)
                  continue

              if 'twiccTaskData' in block:
                  # Legacy by-id block enriched with twiccTaskData only (no
                  # twiccTasksData). Immutable, but we have no full snapshot
                  # to restore state from. Skip; rely on the next snapshot
                  # or reconstruction to recover state.
                  continue

              # --- Advance path ---
              if state is None:
                  state = self._rebuild_state_if_missing(session_id, line_num)

              tool_input = block.get('input') or {}

              if name == 'TaskCreate':
                  task = self._apply_task_create(state, tool_input)
                  if task is None:
                      continue
                  block['twiccTaskData'] = dict(task)
              elif name == 'TaskUpdate':
                  task = self._apply_task_update(state, tool_input)
                  if task is None:
                      continue
                  block['twiccTaskData'] = dict(task)
              elif name == 'TaskGet':
                  task_id = tool_input.get('taskId')
                  if isinstance(task_id, str) and task_id in state:
                      block['twiccTaskData'] = dict(state[task_id])
                  # If taskId unknown, no twiccTaskData written. We still
                  # attach the list snapshot + total below.

              block['twiccTasksData'] = [dict(t) for t in state.values()]

              if name in _TASK_LOOKUP_BY_ID_TOOLS:
                  block['twiccTasksTotal'] = len(state)

              mutated = True

          return mutated
  ```

  **Why every embedded dict is `dict(task)` (a defensive copy):** the embedded snapshot must not share memory with `state[task_id]`, otherwise a subsequent state advance would mutate the historical snapshot too.

- [ ] **Step 1.7: Update the `transform_inline` call site to use the instance method and pass `line_num`**

  Inside `transform_inline` in `claude_code/compute.py`, replace:

  ```python
  if content is not None and _enrich_task_tool_uses(content, session_id):
      return orjson.dumps(parsed_json).decode('utf-8')
  ```

  with:

  ```python
  if content is not None and self._enrich_task_tool_uses(content, session_id, line_num):
      return orjson.dumps(parsed_json).decode('utf-8')
  ```

- [ ] **Step 1.8: Clean up obsolete imports and dead constants**

  - Remove `from .tasks import TasksReader` at the top of `compute.py`.
  - Verify `_TASK_LOOKUP_BY_ID_TOOLS` is still used (it should be — gating `twiccTasksTotal` write). Keep it. If after Step 1.6 it's not referenced anywhere else, that's fine, it stays as a clear semantic constant.
  - The free function `_enrich_task_tool_uses` no longer exists. Make sure no other code in the file or module references it by that name.

- [ ] **Step 1.9: Smoke-check by importing the modules**

  Verify there are no syntax / import errors. Run from the worktree root:

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && TWICC_DATA_DIR=$PWD uv run python -c "
  from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
  from twicc.providers.codex.compute import CodexSessionCompute
  c = ClaudeCodeSessionCompute()
  print('state:', c._session_task_states)
  # Quick state manipulation sanity:
  state = {}
  c._apply_task_create(state, {'subject': 's1', 'activeForm': 'a1'})
  c._apply_task_create(state, {'subject': 's2', 'activeForm': 'a2'})
  c._apply_task_update(state, {'taskId': '1', 'status': 'in_progress'})
  print('state after 2 creates + 1 update:', state)
  print('next id:', c._next_task_id(state))
  "
  ```

  Expected output:
  ```
  state: {}
  state after 2 creates + 1 update: {'1': {'subject': 's1', 'activeForm': 'a1', 'status': 'in_progress', 'id': '1'}, '2': {'subject': 's2', 'activeForm': 'a2', 'status': 'pending', 'id': '2'}}
  next id: 3
  ```

  If any import or attribute error: investigate and fix before committing.

- [ ] **Step 1.10: Verify the full diff**

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && git diff --stat src/twicc/providers/
  ```

  Expected files touched: `compute_base.py`, `claude_code/compute.py`, `codex/compute.py`. No other files.

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && git diff src/twicc/providers/
  ```

  Review hunks to confirm:
  - `compute_base.py` has signature change + 2 keyword-arg-passing call sites only.
  - `codex/compute.py` has signature change only.
  - `claude_code/compute.py` has the full rewrite as described.

- [ ] **Step 1.11: Commit**

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && git add src/twicc/providers/claude_code/compute.py src/twicc/providers/compute_base.py src/twicc/providers/codex/compute.py && git commit -m "$(cat <<'EOF'
  feat(claude_code): in-memory task state for tool_use enrichment

  Replace the disk-read snapshot in _enrich_task_tool_uses with an
  in-memory state machine. ClaudeCodeSessionCompute now keeps a
  per-session dict[task_id_str, task_dict] advanced from the inputs of
  the TaskCreate / TaskUpdate tool_use blocks themselves. Each task
  tool_use gets enriched with twiccTaskData (when applicable),
  twiccTasksData (always), and twiccTasksTotal (for by-id tools), all
  copied defensively so the embedded snapshot can't be mutated by
  later state advances.

  Immutability is preserved by the existing 'twiccTaskData' /
  'twiccTasksData' membership checks — when present, the block is left
  untouched and the in-memory state is restored from the snapshot so
  downstream blocks stay consistent.

  When the state is empty for a session already persisted in DB
  (process restart, fresh batch worker), reconstruction kicks in:
  find the latest item carrying twiccTasksData via content__contains,
  restore the state from it, then replay TaskCreate / TaskUpdate items
  up to (but not including) the current line_num.

  Thread line_num through transform_inline (base + claude_code + codex)
  so reconstruction can scope its DB query correctly and avoid
  double-applying the current block's input.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2: Backend — delete `tasks.py` (no more disk reads)

**Files:**
- Delete: `src/twicc/providers/claude_code/tasks.py`

After Task 1, no code path in the repo calls `TasksReader` anymore. Remove the module.

- [ ] **Step 2.1: Confirm zero remaining Python callers**

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && grep -rn --include='*.py' "TasksReader\|from .tasks\|from twicc.providers.claude_code.tasks" src/
  ```

  Expected: zero matches. Documentation files may still reference the symbol (historical record) — that's fine, only Python code must be clean.

  If anything matches, stop and investigate — Task 1 may have missed an import removal.

- [ ] **Step 2.2: Delete the file**

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && git rm src/twicc/providers/claude_code/tasks.py
  ```

- [ ] **Step 2.3: Verify the diff**

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && git diff --staged --stat
  ```

  Expected: a single deletion of `tasks.py`.

- [ ] **Step 2.4: Commit**

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-claude-task-tools-snapshot && git commit -m "$(cat <<'EOF'
  refactor(claude_code): drop the tasks.py module (no more disk reads)

  The disk-read approach is gone since the previous commit reconstructs
  the per-session task state from the JSONL inputs themselves. The
  TasksReader class is dead code now — delete the whole module.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3: Manual verification (user)

No automated tests in this project. Verification is end-to-end in a browser, against a freshly-started TwiCC instance launched from the worktree.

- [ ] **Step 3.1: Ask the user to start the dev servers from the worktree**

  Per `CLAUDE.md`, restarting servers is reserved to the user. Ask the user to run, from the worktree root:

  ```bash
  cd .worktrees/feature-claude-task-tools-snapshot
  uv run ./devctl.py start
  ```

  Note the ports printed by `devctl` (typically Backend 3501, Frontend 5174, may differ).

- [ ] **Step 3.2: 3 consecutive TaskCreate**

  In a fresh Claude Code session inside the worktree's TwiCC instance, ask Claude to create 3 tasks (distinct subjects). Open each tool_use card:
  - 1st card: snapshot list shows 1 task (the one just created) in `pending`.
  - 2nd card: snapshot list shows 2 tasks in `pending`.
  - 3rd card: snapshot list shows 3 tasks in `pending`.

  This is the regression that this pivot fixes — previously, all 3 cards showed 3 tasks (because the disk-based approach read a state already 3 operations ahead).

- [ ] **Step 3.3: Verify the snapshot is frozen across later updates**

  Trigger a `TaskUpdate` to flip task #2 to `in_progress`. Card shows: #1 pending, #2 in_progress, #3 pending.

  Trigger a second `TaskUpdate` to flip task #2 to `completed`. Card shows: #1 pending, #2 completed, #3 pending.

  Go back to the first TaskUpdate card: snapshot list must still show #2 in `in_progress`, not `completed`. Frozen-snapshot guarantee.

- [ ] **Step 3.4: TaskGet**

  Trigger `TaskGet` for task #1. Card's snapshot reflects the current state at the moment of the Get.

- [ ] **Step 3.5: TaskList unchanged**

  Trigger `TaskList`. Card renders the `TodoContent`-only view (no JSON Human View above, no divider). Count summary in the header reflects current count.

- [ ] **Step 3.6: Legacy sessions still render correctly**

  Open a Claude Code session that pre-dates this pivot (a session captured with the disk-based code or even earlier). The by-id tool_use cards must either:
  - Show the JSON Human View plus divider + list (older snapshots captured with the disk-based code, possibly stale because of the race), or
  - Show the JSON Human View alone (sessions captured before any snapshot mechanism).

  In either case: no crash, no orphan rendering.

- [ ] **Step 3.7: Process restart (reconstruction path)**

  After all the above tests, ask the user to restart the backend (e.g. via `devctl.py restart back`). This wipes `_session_task_states` for all sessions. Then, in the existing session, trigger another `TaskUpdate`. The new tool_use card's snapshot must:
  - Include all the previous tasks with correct statuses.
  - Show the update we just made on top.

  Validates the reconstruction algorithm. If the new snapshot is wrong (missing tasks, wrong statuses), reconstruction has a bug.

- [ ] **Step 3.8: Multi-task-tool turn + restart (regression for the snapshot extraction bug)**

  Trigger a turn where Claude emits multiple task tool_uses in a single assistant message (e.g. ask Claude to create 3 tasks "in parallel — same turn"). Then ask the user to restart the backend (`devctl.py restart back`). Then in the same session trigger a `TaskUpdate` on the **last** of those tasks.

  Expected: the new tool_use's snapshot includes all 3 tasks created in the multi-tool turn, plus the update we just made. If the snapshot is missing tasks created late in that turn, the reconstruction's snapshot extractor regressed (it should return the **last** ``twiccTasksData`` of the source assistant message, not the first).

If any of the steps 3.2–3.8 fail, do **not** ship — diagnose and fix.

---

## Done

Once Task 3 verifies cleanly, the feature is complete. The user will squash the branch into a single commit before merging to main. Use the `superpowers:finishing-a-development-branch` skill to decide between merging to main, opening a PR, or further work.

No migrations, no package install, no version bumps.
