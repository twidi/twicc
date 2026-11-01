# Session `plan_paths` — Plan-Document Tracking & Revamped Plan Tab

**Goal:** Track every plan-like document a session touches (native Claude plan + pattern-detected docs written via file-edit tools or shell commands, both providers) in a new `Session.plan_paths` JSON field, and rework the Plan tab into a document selector + read-only rendered preview (FilePane), provider-agnostic.

**Architecture:** A new pure module (`plan_docs.py`) owns the pattern list, the shell-write heuristic and the merge algorithm. Each provider's compute class gains an `extract_doc_edit_events()` hook (mirroring `build_tasks_snapshot`), fed by both compute paths — authoritative rebuild in background recompute, additive merge in the live watcher — exactly like `Session.tasks`. The Claude plans watcher additionally latches the native plan into `plan_paths` live. The serializer emits the stored entries verbatim (its contract is query-free and async-safe); the frontend resolves relative paths against the project directory with a `worktree_of`-parent fallback, and replaces `PlanPane`'s markdown fetch with a `wa-select` of entries above a `renderOnly` `FilePane`.

**Tech stack:** Django JSONField + migration, compute pipeline hooks (`compute_base.py`), watchfiles plans watcher, Vue 3 (`PlanPane.vue`, `FilePane.vue` reuse), pytest.

---

## 1. Current state (grounding)

- The Plan tab exists only when `has_plan` is true — computed at serialization (`src/twicc/core/serializers.py:176`) from `ClaudeCodeHelpers.session_has_plan()` (`src/twicc/providers/claude_code/helpers.py:295`), backed by the `ClaudeCodePlansWatcher` in-memory slug set (`src/twicc/providers/claude_code/plans_watcher.py`). Codex never sets it (base helper returns `False`).
- The native plan path is `~/.claude/plans/<slug>.md` (`PLANS_DIR`, `src/twicc/providers/claude_code/constants.py:29`); content served by `GET /api/sessions/<id>/plan/` (`src/twicc/views.py:191`), rendered by `frontend/src/components/plan/PlanPane.vue` via `MarkdownContent`.
- The model to copy for a per-line-fed Session JSON field is `tasks`:
  - model comment + field: `src/twicc/core/models.py:467-479`;
  - batch accumulator `last_tasks_snapshot` (`compute_base.py:2289-2291`, per-line at `2364-2369`, authoritative emission at `2636`);
  - live accumulator (`compute_base.py:2999-3001`, per-line at `3105-3109`, conditional save at `3412-3416`).
- The model for a filesystem-probed field is `has_workflows` (`extra_session_fields` hook, `claude_code/compute.py:660-680`; live latch `sessions_watcher.py:481-494`).
- File-edit tool parsing today:
  - Claude: `_TOOL_PATH_FIELDS` (`claude_code/compute.py:134-140`) reads `input.file_path` from `Edit`/`Write` (also `Read`); `MultiEdit` exists (`agent/permissions.py:34-40`) but is absent from compute-side path extraction.
  - Codex: `apply_patch` results arrive as `event_msg.patch_apply_end` with `changes: {abs_path: {type, ...}}` (`codex/compute.py:2272-2290`); the change `type` distinguishes add/update/delete.
  - **There is no shell-write heuristic anywhere today.** Codex used to emit `parsed_cmd` (with a write variant) but stopped (last rollouts carrying it: 2026-06-04); the frontend `parseCommand.js` deliberately classifies anything containing `>` as `unknown`. The heuristic must be built in this feature, backend-side.
- Frontend rendering: `FilePane.vue` already supports `previewByDefault` + `renderOnly` (locks preview, hides the whole toolbar — `frontend/src/components/files/FilePane.vue:717-721`, `1077`) and exposes `reload()` (`:1014`). File content endpoints: session-scoped `/api/projects/<pid>/sessions/<sid>/file-content/` (validated by `allowed_base_dirs` — includes the project dir, session cwd/git root, **and the worktree parent's root**, `src/twicc/roots.py:20-45`) and standalone `/api/file-content/?path=&root=` (prefix check only).
- Latest migration: `0121` is free (`0120_session_goals.py` is last). Compute versions: `CLAUDE_CODE_COMPUTE_VERSION = 104`, `CODEX_COMPUTE_VERSION = 33` (`src/twicc/settings.py:293-294`).
- Frontend project payload already carries `directory` and `worktree_of` (`core/serializers.py:17,31`), so the client can resolve relative paths itself — required, because `serialize_session` is contractually query-free and async-safe (`serializers.py:1-8`; called directly from coroutines in the watchers, asgi and async views), so it must NOT walk `session.project(.worktree_of)`. Resolution is client-side (§4.2).

## 2. Data model

### 2.1 Field

```python
# src/twicc/core/models.py — right after `tasks` (line ~479)

# Plan-like documents this session touched, newest-first NOT guaranteed
# (append order); the frontend sorts by ``updated_at``. Each entry:
# ``{path, exists, created_at, updated_at, source}`` where ``path`` is
# POSIX-relative to the session's project directory when the file lives
# under it (portable across worktree removal — resolution falls back to
# the ``worktree_of`` parent project), absolute otherwise (e.g. the
# native Claude plan under ~/.claude/plans/). ``exists`` is the last
# probed existence (refreshed by both compute paths and the plans
# watcher); ``created_at``/``updated_at`` are ISO timestamps of the
# first/last JSONL line that touched the file. ``source`` is
# ``"claude_plan"`` for the native plan-mode file, ``"detected"`` for
# pattern-matched writes. Fed by both compute paths like ``tasks``
# (authoritative on full recompute, merged on live sync) and latched
# live by the Claude plans watcher for the native plan. ``[]`` = none.
plan_paths = models.JSONField(default=list, blank=True)
```

Migration: `src/twicc/core/migrations/0121_session_plan_paths.py` (generated with `makemigrations`).

Entry shape (canonical, stored):

```json
{
  "path": "docs/plans/2026-07-04-foo-plan.md",   // or "/home/user/.claude/plans/slug.md"
  "exists": true,
  "created_at": "2026-07-04T12:00:00+00:00",
  "updated_at": "2026-07-04T13:45:10+00:00",
  "source": "detected"                            // or "claude_plan"
}
```

> `source` is an addition over the requested shape — needed to label the native plan in the selector and to let the plans watcher find its own entry. Flag for review.

### 2.2 Scope

- `SessionType.SESSION` only in v1. Subagent-written docs (workflow agents, dev-loop workers) are a possible follow-up (they'd need folding into the parent's list).
- Deletions (`rm`, apply_patch `delete`) flip `exists` to `false` and bump `updated_at`; entries are never removed (history stays browsable; a later write event, a plans-watcher tick or a recompute's `refresh_entries_existence` can flip `exists` back if the file returns).

## 3. Backend

### 3.1 New module `src/twicc/providers/plan_docs.py`

Pure, ORM-free (importable from the compute subprocess and the watcher). Contents:

**a) Pattern configuration** (the user-reviewable list):

```python
DOC_EXTENSIONS = {'.md', '.markdown', '.mdx', '.txt', '.html', '.htm', '.rst', '.adoc', '.mmd'}

# Matched against filename-stem tokens (split on -_. and spaces, lowercased).
NAME_KEYWORDS = {
    'plan', 'plans', 'planning',
    'spec', 'specs', 'specification',
    'design', 'architecture', 'adr', 'rfc', 'prd',
    'proposal', 'proposals',
    'handoff', 'handover',
    'note', 'notes',
    'research', 'analysis', 'findings', 'investigation', 'audit',
    'review', 'postmortem', 'retro', 'retrospective',
    'roadmap', 'strategy', 'brainstorm', 'brainstorming',
    'decision', 'decisions', 'requirements', 'brief',
    'report', 'summary', 'outline', 'draft',
    'todo', 'todos', 'checklist',
    'runbook', 'playbook', 'migration',
    'worklog', 'devlog', 'status',
    'ideas', 'questions',
}

# A file with a DOC_EXTENSION under any ancestor directory with one of
# these names matches even if its own name doesn't.
DIR_KEYWORDS = {
    'plans', 'specs', 'design', 'designs', 'adr', 'adrs', 'rfcs',
    'handoffs', 'proposals', 'research', 'notes', 'decisions',
    'postmortems', 'planning', 'brainstorms',
}

# Filenames never tracked (case-insensitive, compared on the full name).
EXCLUDED_NAMES = {
    'readme', 'changelog', 'license', 'licence', 'contributing',
    'code_of_conduct', 'security', 'claude', 'claude.local', 'agents',
    'gemini', 'memory', 'skill',
}

# Path segments that disqualify (vendored/generated trees).
EXCLUDED_SEGMENTS = {
    'node_modules', '.git', 'vendor', 'vendored', 'dist', 'build',
    '__pycache__', '.venv', 'venv', 'site-packages',
}
```

**b) `is_plan_doc_path(path: str) -> bool`** — extension in `DOC_EXTENSIONS`, no excluded segment, stem not in `EXCLUDED_NAMES`, and (stem tokens ∩ `NAME_KEYWORDS` ≠ ∅ **or** an ancestor dir name ∈ `DIR_KEYWORDS`). Token matching, not substring (`airplane.md` must not match `plan`).

**c) `DocEditEvent(NamedTuple)`** — `path: str` (absolute after cwd-join), `action: str` (`'write' | 'delete'`), `source: str = 'detected'` (`'claude_plan'` for the native plan, used by §3.3/§3.5 and the concurrency folds).

**d) `extract_shell_write_targets(command: str | list) -> list[tuple[str, str]]`** — the shell heuristic (shared by both providers for `Bash` / Codex shell tools). Conservative, operating on `shlex`-tokenized segments (split on `&&`, `||`, `;`, `|`):

- `>` / `>>` / `&>` redirection targets (skip `/dev/null`, fd targets like `&1`); covers heredoc-into-file (`cat > f <<EOF`);
- `tee [-a] file...`;
- `sed -i`/`--in-place` file arguments;
- `cp`/`mv` destination (last non-flag arg, ≥ 2 file args) → `write` (skip when it has no `DOC_EXTENSION`, i.e. likely a directory);
- `touch file...` → `write`;
- `rm [-flags] file...` → `delete`.

Unparseable commands (shlex errors) return `[]`. Note: plain `shlex.split` does not split on shell operators — use `shlex.shlex(punctuation_chars='&|;<>')` (or split segments manually) so `&&`, `;`, `|` and redirections come out as their own tokens. Only targets that later pass `is_plan_doc_path` survive, so false positives are naturally bounded to doc-looking files.

**e) `apply_doc_edit_events(entries, timed_events, *, project_root) -> tuple[list, bool]`** — the merge:

- normalize each event path (`os.path.normpath`), relativize to POSIX when under `project_root`;
- keyed by stored `path`: `write` → create entry (`created_at = updated_at = line timestamp`, `source='detected'`) or bump `updated_at` + `exists=True`; `delete` → `exists=False`, bump `updated_at`;
- returns `(new_entries, changed)`.

**f) `refresh_entries_existence(entries, roots: list[str]) -> bool`** — re-probes `exists` for every entry (absolute as-is; relative against each root in order), returns whether anything changed. `roots` = `[project.directory, worktree_parent.directory?]`.

### 3.2 Compute hooks (`compute_base.py`)

New overridable methods on `BaseSessionCompute`:

```python
def extract_doc_edit_events(self, parsed_json: dict, *, cwd: str | None) -> list[DocEditEvent]:
    """Per-line detection of plan-doc writes/deletes. Default: none."""
    return []

def extra_doc_edit_events(self, session, *, last_slug: str | None) -> list[tuple[DocEditEvent, str | None]]:
    """End-of-compute filesystem-derived events (Claude: the native plan).
    Returns (event, iso_timestamp) pairs. Default: none."""
    return []
```

**Batch path** (`compute_session_metadata`):

- accumulator `plan_doc_events: list[tuple[DocEditEvent, datetime | None]] = []` next to `last_tasks_snapshot` (~line 2291); per line (guarded by `session.type == SessionType.SESSION`), **after the runtime-fields block (~2389, NOT next to the tasks block)** so `last_cwd` already reflects the current line: extend with `self.extract_doc_edit_events(parsed, cwd=last_cwd)` paired with `item.timestamp`;
- before the `session_complete` emission (~2592, next to `extra_session_fields`): append `self.extra_doc_edit_events(session, last_slug=last_slug)` events; rebuild authoritatively: `plan_paths, _ = apply_doc_edit_events([], plan_doc_events, project_root=...)`, then `refresh_entries_existence(plan_paths, roots)`;
- emit `'plan_paths': plan_paths` in `session_fields` (~2636, next to `tasks`). Full recompute is authoritative — an empty result resets stale entries, matching `tasks`;
- **concurrent-writer guard** in `apply_session_complete`: the plans watcher may have latched a fresher `claude_plan` entry after the worker's end-of-compute probe (`observed_last_offset` doesn't advance on watcher writes, so that guard won't skip). Before writing, re-read the row's `plan_paths` and fold in any `source == 'claude_plan'` entry that is newer than (or absent from) the computed list — same just-before-write pattern as `goals`' `preserve_dismissed_flags` (`compute_base.py:2818-2827`).

`project_root`/`roots`: from `session.project` (`directory`, `worktree_of.directory` when set) — resolved once per session at compute start. The compute worker runs sync in a subprocess, so lazy FK loads are legal there; still prefer `select_related('project__worktree_of')` to avoid the two extra queries.

**Live path** (`sync_session_items_from_file`):

- same accumulator next to the live `last_tasks_snapshot` (~3001), **guarded by `session.type == SessionType.SESSION`** (the live path processes subagent files through the same function, `compute_base.py:3018-3022`; without the guard a subagent would gain entries the next authoritative recompute resets — flip-flop) and fed **after the runtime-fields block (~3139)**, with `cwd=(last_cwd or session.cwd)` — the live `last_cwd` is batch-local and starts at `None` (`compute_base.py:2994`), so a mid-turn batch without a cwd-bearing line must fall back to the stored value (analogous fallback to `session.cwd or last_cwd` at 3223, precedence deliberately reversed: per-line extraction prefers the batch-fresh cwd);
- at save time (~3416, mirroring `tasks`): if events collected, `session.plan_paths, changed = apply_doc_edit_events(session.plan_paths, events, project_root=...)`; when `changed`, also `refresh_entries_existence`, apply the same just-before-write fold of a fresher DB `claude_plan` entry (the plans watcher writes concurrently with long live batches), then append `"plan_paths"` to `session_update_fields`. A batch with no doc-edit line leaves the field untouched (additive semantics, like `tasks`).

The updated session then rides the existing `session_updated` broadcasts from both paths — no new WS message type.

### 3.3 Claude Code extraction (`claude_code/compute.py`)

`extract_doc_edit_events` override:

- walk assistant `tool_use` blocks (reuse `get_message_content_list(parsed_json, "assistant")`);
- `Write` / `Edit` / `MultiEdit` → `input.file_path` (absolute by contract) → `write` event when `is_plan_doc_path`;
- `NotebookEdit` ignored (`.ipynb` is not a doc);
- `Bash` → `extract_shell_write_targets(input.command)`, cwd-join relative targets, filter by `is_plan_doc_path`.

`extra_doc_edit_events` override — the native plan:

- if `last_slug` (fallback `session.slug`): `plan_path = PLANS_DIR / f"{slug}.md"`; if `plan_path.is_file()` → one `write` event with `source='claude_plan'` and timestamp from `st_mtime` (the merge helper needs a way to carry `source`; simplest: extend `DocEditEvent` with `source: str = 'detected'`).

### 3.4 Codex extraction (`codex/compute.py`)

`extract_doc_edit_events` override:

- `event_msg.patch_apply_end` lines: **only when the patch actually applied** — `success is True` and `status == "completed"` (the `changes` map is present on failed/declined patches too, cf. `_patch_apply_error`, `codex/compute.py:907-929`). Then iterate `payload.changes` (`{abs_path: {type: 'add'|'update'|'delete', ...}}`) → `write`/`delete` events, filtered by `is_plan_doc_path`. Canonical source for `apply_patch` regardless of how it was invoked (custom_tool_call or shell-wrapped) — no need to parse the v4a patch text backend-side;
- shell tool calls — the input shape diverges per tool (mirror `extractCommandPayload`, `frontend/src/providers/codex/toolHelpers.js:398-408`):
  - `shell` / `container.exec`: JSON-decode `arguments`, command is the **`command` argv list**;
  - `shell_command`: `arguments.command` (string);
  - `exec_command`: `arguments.cmd` (string — NOT `command`);
  - `local_shell_call`: no `arguments` at all — argv lives in **`payload.action.command`** (`codex/compute.py:240-243`);
  - feed the result to `extract_shell_write_targets`; for cwd-joining relative targets, prefer the call's own working directory when present over the loop's `cwd` accumulator — key is `workdir` for `exec_command`/`shell`/`container.exec`/`shell_command`, but **`action.working_directory`** for `local_shell_call` (`toolHelpers.js:316-318`) — then filter by `is_plan_doc_path`.

### 3.5 Plans watcher live latch (`claude_code/plans_watcher.py`)

In `_reconcile`, alongside the existing `plan_available` / `plan_changed` / `plan_gone` broadcasts (kept as-is — the frontend still uses `plan_changed` to trigger a pane reload):

- resolve matching sessions (existing ORM lookup by slug);
- for each: re-read the row fresh (via `sync_to_async`, as the watcher is async), apply a `write` (available/changed, timestamp = file mtime) or `delete` (gone) event with `source='claude_plan'` to `session.plan_paths` via `apply_doc_edit_events` — touching **only** its own `claude_plan` entry and preserving the row's `detected` entries as read — then save `update_fields=["plan_paths"]` and broadcast `session_updated` with `serialize_session(session)` unless `session.hidden` — same shape as `_latch_session_workflows` (`sessions_watcher.py:481-494`). The read-fold-write happens as late as possible to shrink the race window with a concurrent live-sync batch (the symmetric fold in §3.2 covers the other direction);
- `delete` here means `exists=False` on the entry, not removal — note this reverses today's deliberately non-monotonic tab behavior (`plan_gone` used to hide the tab, `plans_watcher.py:5-7`): with entries kept, the tab stays. Owned as a design choice in §8.

This makes the tab appear live when a plan is first written mid-session, without waiting for a JSONL line.

### 3.6 Serialization (`core/serializers.py`)

In `serialize_session`, next to `tasks` (line ~142), emit the stored entries **verbatim**:

```python
"plan_paths": session.plan_paths,
```

No enrichment here: `serialize_session` is contractually query-free and async-safe (`serializers.py:1-8`) — it is called directly from coroutines (both sessions watchers, `asgi.py`, async views, `base_manager.py`, ~15+ sites), so walking `session.project(.worktree_of)` would raise `SynchronousOnlyOperation` for any session with a non-empty list. Absolute-path resolution and the worktree fallback are done client-side (§4.2), where the projects store already has `directory` + `worktree_of`. Existence freshness comes from the stored `exists` (maintained by both compute paths and the plans watcher) plus `FilePane`'s own missing-file error on fetch.

Existing `has_plan` field, `/api/sessions/<id>/plan/` endpoint, and `twicc session plan` CLI stay untouched (the CLI/skill contract is unchanged; no `SKILLS-AND-CLI.md` impact).

### 3.7 Compute version bumps (`settings.py`)

- `CLAUDE_CODE_COMPUTE_VERSION = 105` (comment: `plan_paths backfill`)
- `CODEX_COMPUTE_VERSION = 34` (same)

This backfills `plan_paths` for every historical session on next server restart.

## 4. Frontend

### 4.1 Store (`stores/data.js`)

Getter next to `getSessionTasks` (~line 841):

```js
getSessionPlanDocs: (state) => (sessionId) => {
    const docs = state.sessions[sessionId]?.plan_paths
    if (!Array.isArray(docs) || docs.length === 0) return []
    return [...docs].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
},
```

### 4.2 `PlanPane.vue` rework (`components/plan/PlanPane.vue`)

Full rewrite, keeping props `sessionId` + `active` and the exposed `reload()` contract with `SessionView`:

- entries from `store.getSessionPlanDocs(sessionId)` (reactive — new WS payloads re-sort automatically);
- **header row**: a `wa-select` (pattern: `:value` + `@change`, cf. `MessageSnippetsDialog.vue:496` — no `.prop` modifier there) listing entries newest-first; option label = `path` (absolute paths shortened with `~`); the `claude_plan` entry gets a distinctive icon/prefix ("Claude plan"); `exists === false` entries shown with a warning icon (still selectable — FilePane will show its own error);
- **path resolution (client-side)**: absolute entries used as-is. Relative entries resolve against candidate roots `[project.directory, parentProject?.directory]` (parent = `projects[project.worktree_of]`, the worktree-removal fallback). When there is a single candidate (no `worktree_of`), no probe. When two, `PlanPane` probes candidate 0 once (a `file-content` fetch on selection) and falls back to candidate 1 on failure — bounded to worktree sessions, per selection, and plan docs are small;
- **body**: `FilePane` with `:file-path="resolvedAbsPath"`, `:render-only="true"`, `:preview-by-default="true"`, `:active="active"`, and per-entry API routing:
  - path under `project.directory` (or the `worktree_of` parent's directory) → session-scoped default prefix (pass `projectId` + `sessionId` props; `allowed_base_dirs` already whitelists both roots, `roots.py:20-45`);
  - otherwise (native plan, /tmp docs…) → `api-prefix="/api"` + `:root-restriction="dirname(path)"` (standalone endpoints);
  - `PlanPane` gains a `projectId` prop (SessionView passes `session.project_id`) and reads the project from the store for the containment test;
- **rendering caveat**: `renderOnly` FilePane renders md/svg/html/mermaid; `.txt`/`.rst`/`.adoc` entries display as toolbar-less read-only source — accepted for v1;
- **selection**: default = newest; user selection preserved while the entry still exists in the list; when the selected entry's `updated_at` changes in a store update → `filePaneRef.reload()`;
- `reload()` (exposed) → `filePaneRef.reload()` — keeps `SessionView`'s existing `twicc:plan-changed` / `twicc:ws-reconnected` wiring working unchanged (`SessionView.vue:133-149`);
- when only one entry exists, still show the select (consistency; cheap) — or hide it; reviewer's pick, default to always visible;
- `key` the FilePane on the selected resolved path so switching docs fully resets the pane (`FilePane` handles `filePath` changes, but a key avoids preview-state bleed).

`MarkdownContent` direct usage and the `/api/sessions/<id>/plan/` fetch disappear from this component.

### 4.3 Tab gating (`views/SessionView.vue` + palette)

- `hasPlan` computed (line ~384) becomes `computed(() => (session.value?.plan_paths?.length ?? 0) > 0)` — the tab is now provider-agnostic (Codex sessions with detected docs get it too). `TOOL_TABS` entry, shortcut `6`, teleport block: unchanged;
- pass `:project-id="session.project_id"` to `PlanPane`;
- `staticCommands.js:705` guard: `(data.getSession(sessionId)?.plan_paths?.length ?? 0) > 0`;
- `useWebSocket.js` `plan_available`/`plan_gone` handlers (patching `has_plan`) stay but become secondary — the watcher's new `session_updated` broadcast carries the authoritative `plan_paths`; `plan_changed` keeps driving `PlanPane.reload()`.

## 5. Docs

- `CLAUDE.md` Session bullet: add `plan_paths` (one line, same brevity as `tasks`); propagate to `AGENTS.md` (condensed).

## 6. Tests (`tests/test_plan_docs.py` + provider tests)

Pure-function coverage (pytest, no DB):

1. `is_plan_doc_path`: keyword stems (`implementation-plan.md` ✓, `airplane.md` ✗, `NOTES.txt` ✓), dir keywords (`docs/plans/x.md` ✓, `docs/guide.md` ✗), exclusions (`README.md` ✗, `node_modules/.../plan.md` ✗), extensions (`plan.py` ✗).
2. `extract_shell_write_targets`: `echo x > notes.md`, `cat > docs/plans/a.md <<'EOF'`, `tee -a spec.md`, `sed -i s/x/y/ design.md`, `mv tmp.md docs/handoff.md`, `rm old-plan.md` (delete), argv-list commands, pipes/chains, `> /dev/null` ignored, shlex-hostile input → `[]`.
3. `apply_doc_edit_events`: create/update/delete lifecycle, relativization under project root, absolute passthrough, `created_at` stability across updates, changed-flag semantics.
4. Provider extraction (DB-less, feeding `parsed` dicts): Claude `Write`/`Edit`/`MultiEdit`/`Bash` blocks; Codex `patch_apply_end` (add/update/delete) and `function_call` shell with relative path + cwd join.
5. Merge concurrency folds: batch fold preserves a fresher DB `claude_plan` entry; watcher fold preserves the row's `detected` entries.
6. Codex per-tool command extraction: `exec_command` (`cmd` key), `local_shell_call` (`action.command`), `workdir` preference for relative targets, failed/declined `patch_apply_end` ignored.

## 7. Task breakdown (implementation order)

1. **`plan_docs.py` module + tests** — patterns, `is_plan_doc_path`, `DocEditEvent`, `extract_shell_write_targets`, `apply_doc_edit_events`, `refresh_entries_existence`. Pure TDD-able unit.
2. **Model field + migration 0121** (remind user to restart via devctl — auto-migrates).
3. **`compute_base.py` hooks + both accumulators** (batch authoritative / live additive), `session_fields` emission, `session_update_fields` wiring.
4. **Claude override** (`extract_doc_edit_events` + `extra_doc_edit_events` native plan) + tests.
5. **Codex override** (`patch_apply_end` + shell heuristic) + tests.
6. **Plans watcher latch** (live native-plan entry + `session_updated` broadcast).
7. **Serializer passthrough** (`plan_paths` verbatim, query-free) + compute version bumps (105/34).
8. **Frontend**: store getter → `PlanPane` rework → `SessionView` gating + palette guard.
9. **Docs**: CLAUDE.md + AGENTS.md.

## 8. Open questions (for review)

1. **Pattern list** (§3.1a) — the whole point of the review; extensions beyond md/txt/html (`.rst`, `.adoc`, `.mdx`, `.mmd`) included as proposals.
2. **`source` key** added to the requested entry shape — OK?
3. **Edits count as adds**: an `Edit` on a matching file not yet in the list adds it (a resumed session editing an existing plan doc should surface it). Confirm.
4. **Deletions**: keep the entry with `exists=false` (chosen) vs drop it from the list. Note this makes the Plan tab effectively monotonic — today `plan_gone` hides it (deliberately non-monotonic, `plans_watcher.py:5-7`); with kept entries it stays visible. Confirm.
5. **Subagent sessions** excluded in v1 (their doc writes don't reach the parent's list). Follow-up candidate.
6. **Claude `Bash` heuristic** included (symmetric with Codex shells) — cheap since shared. Confirm.
7. **Select visibility** with a single entry: always shown (chosen) vs auto-hidden.
