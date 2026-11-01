# Hidden Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the hidden-sessions feature defined in [`docs/superpowers/specs/2026-05-30-hidden-sessions-design.md`](../specs/2026-05-30-hidden-sessions-design.md) — two new `Session` columns (`hidden`, `spawned_by`), a `twicc whoami` command, auto-detection of filiation via PID ancestry, validation guards, listings opt-in, FTS schema bump, counter exclusions, broadcasts guards, and the matching skill updates.

**Architecture:** Hidden sessions are top-level (`type=SESSION`) sessions invisible everywhere by default. The flag is mutable via dedicated `hide`/`unhide` sub-commands; mutation triggers synchronous counter recompute and FTS re-index. Costs continue to be aggregated normally; session and user-message counts exclude hidden everywhere. The `spawned_by` FK is independent and auto-filled by the CLI via a PID-ancestry lookup against `ProcessRun.agent_pid`.

**Tech Stack:** Django 6 ORM (model change + migration), Tantivy 0.22 schema bump, Typer CLI, asgiref `sync_to_async`, Django Channels broadcasts, Vue 3 / Pinia store.

---

## Context

- **Spec:** `docs/superpowers/specs/2026-05-30-hidden-sessions-design.md` — read it before starting if you haven't.
- **TwiCC quality rule:** *no tests, no linting* (cf. `CLAUDE.md` → *Quality approach*). Each task ends with a manual sanity check (`python -m py_compile`, `python -m django check`, `--help` invocation, restart by user). No `pytest` runs.
- **Worktree:** `/home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions` on branch `feature/hidden-sessions`. **Every Bash command in this plan must be prefixed with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && `** — the editable install resolves to the worktree's source, and `paths.py` only points at the worktree DB if `TWICC_DATA_DIR=$PWD` is also set for Python invocations that touch the DB.
- **Operations reserved to the user** (cf. `CLAUDE.md` → *Operations Reserved to User*): running `python -m django migrate`, restarting `devctl.py`, installing dependencies. Tasks that change models or backend code end with a **reminder** to ask the user — never run these yourself.
- **Commits:** one per task, conventional-commits style (`feat(scope): subject`, `refactor(scope): subject`, `docs(scope): subject`). Use HEREDOC for the commit message and include the `Co-Authored-By:` trailer the project uses.
- **Frequent commits:** each task is a meaningful, self-contained checkpoint. Do not batch multiple tasks into one commit.

## File Structure (after this plan)

| File | Status | Responsibility |
|---|---|---|
| `src/twicc/core/models.py` | modified | `+2 Session fields (hidden, spawned_by)`; `idx_session_visible` condition extended with `hidden=False`; `PeriodicActivity.recalculate` filters `session__hidden=False` (messages) and `hidden=False` (sessions count) |
| `src/twicc/core/migrations/00XX_session_hidden_and_spawned_by.py` | NEW | `AddField` × 2 + index swap |
| `src/twicc/core/serializers.py` | modified | `serialize_session` exposes `hidden` and `spawned_by` |
| `src/twicc/pending_session_attributes.py` | NEW | In-memory stash/pop for `hidden` and `spawned_by_id` (same pattern as `pending_agent_settings.py`); consumed by the file watcher when it creates the `Session` row |
| `src/twicc/cli/_session_request/whoami.py` | NEW | `resolve_current_session()` — PID-ancestry lookup against `ProcessRun.agent_pid` |
| `src/twicc/cli/whoami.py` | NEW | `twicc whoami [--json]` command — calls the helper, prints session details, exit code 1 if no session in ancestry |
| `src/twicc/cli/__init__.py` | modified | Register the new `whoami` command |
| `src/twicc/cli/_session_request/validation.py` | modified | `+validate_hidden_constraints` and `+_PERMISSION_MODE_HIDDEN_WHITELIST` (per-provider) |
| `src/twicc/cli/create_session/command.py` | modified | `+--hidden` flag; call whoami; build payload with `hidden` and `spawned_by_session_id` |
| `src/twicc/cli/update_session/__init__.py` | modified | Register `hide` and `unhide` sub-commands |
| `src/twicc/cli/update_session/hidden_command.py` | NEW | `update-session <ID> hide` / `unhide` — drop-file with `kind="update_hidden"` |
| `src/twicc/cli/update_session/settings_command.py` | modified | After settings merge, if the target session is currently hidden, run `validate_hidden_constraints` |
| `src/twicc/core/services/session_creation.py` | modified | Read `hidden` and `spawned_by_session_id` from payload, re-run `validate_hidden_constraints` server-side, stash via `pending_session_attributes` |
| `src/twicc/core/services/session_update.py` | modified | `+update_session_hidden_from_payload` — orchestrates the flip via `session_visibility.hide_session`/`unhide_session` |
| `src/twicc/core/services/session_visibility.py` | NEW | `hide_session(session)` / `unhide_session(session)` — atomic flip + counter recompute + FTS reindex + broadcasts |
| `src/twicc/pending_sessions_watcher.py` | modified | Route `kind="update_hidden"` to the new service |
| `src/twicc/providers/sessions_watcher.py` | modified | Pop `hidden`/`spawned_by_id` from `pending_session_attributes` when creating the row; pass new fields to `search.index_document`; add `if session.hidden: return` guards before all `session_updated` / `session_items_added` broadcasts |
| `src/twicc/providers/claude_code/sessions_watcher.py` | modified | Same broadcast guards if provider-specific emitters exist |
| `src/twicc/providers/db_writer.py` | modified | `recalc_sessions_count`: `.filter(hidden=False)`; `_broadcast_project_updated` stays unfiltered (intentional) |
| `src/twicc/projects.py` | modified | `update_project_metadata`: `sessions_count` query gets `.filter(hidden=False)` |
| `src/twicc/views.py` | modified | All session-listing endpoints get `.filter(hidden=False)`; `_resolve_session_or_404` returns 404 for hidden; `session_updated` HTTP broadcasts get `if session.hidden: return` guard |
| `src/twicc/asgi.py` | modified | `if session.hidden: return` guards on every `session_updated` broadcast emitted from the WS consumer; `active_processes` payload at connect time excludes processes whose session is hidden |
| `src/twicc/search.py` | modified | Schema +2 fields (`hidden` bool, `spawned_by` text); `index_document` and `reindex_session` honor the new fields; `search()` filters `hidden=False` by default with an `include_hidden=False, only_hidden=False, spawned_by=None` parameter trio |
| `src/twicc/search_indexing_task.py` | modified | Bump `CURRENT_SEARCH_VERSION`; nuke `search-index/` before re-init if the version changed (Tantivy can't accept a new schema in place); progress counter filters `hidden=False` |
| `src/twicc/providers/claude_code/orchestrator.py` | modified | Post-sync log counter filters `hidden=False` |
| `src/twicc/providers/codex/orchestrator.py` | modified | Same |
| `src/twicc/cli/sessions.py` | modified | `+--include-hidden`, `+--only-hidden`, `+--spawned-by <ID|self>` |
| `src/twicc/cli/processes.py` | modified | Same trio |
| `src/twicc/cli/search.py` | modified | Same trio (forwarded to `search.search()`) |
| `frontend/src/stores/data.js` | modified | Defensive `if (session.hidden) continue` guards in 5 getters; new `session_removed` payload handler that drops the session from `state.sessions` |
| `frontend/src/composables/useWebSocket.js` | modified | Route `session_removed` events to the store |
| `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md` | modified | Document `--hidden` + constraints + related-commands hint |
| `src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md` | modified | Document `hide`/`unhide` + preconditions |
| `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md` | modified | Document `--include-hidden`, `--only-hidden`, `--spawned-by` |
| `src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md` | modified | Same |
| `src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md` | modified | Same |
| `src/twicc/agent/plugin/twicc/skills/twicc-whoami/SKILL.md` | NEW | New short skill |

**Cross-cutting invariants** (assume true unless a task changes them):
- Hidden sessions are top-level (`type=SESSION`). Subagents (`type=SUBAGENT`) keep being filtered by the universal `type=SESSION` filter — no propagation needed.
- The `spawned_by` FK is **never mutated after creation**. There is no CLI surface to change it.
- The `permission_mode` whitelist for hidden sessions is:
  - Claude Code: `bypassPermissions`, `dontAsk`
  - Codex: `yolo`, `strict`
- `question_widget=True` is incompatible with `hidden=True` (Claude Code only; Codex ignores).

---

## Task 1: Add `hidden` and `spawned_by` fields to `Session` + migration

**Files:**
- Modify: `src/twicc/core/models.py` (around lines 337-412 — the `Session` class user-controlled fields block + `Meta.indexes`)
- Create: `src/twicc/core/migrations/00XX_session_hidden_and_spawned_by.py` (the next available auto-numbered slot)

- [ ] **Step 1: Add the two fields in the right spot**

In `Session`, just after `pinned = models.CharField(...)` (around line 341) and before `permission_mode = ...` (line 345), insert:

```python
# Hidden sessions are top-level (type=SESSION) sessions invisible to
# the user — they exist, consume API costs (counted in aggregates),
# but are absent from every list, search result, count, and broadcast.
# Set by the CLI `twicc create-session --hidden`; mutable through
# `twicc update-session <ID> hide / unhide`. Implies a non-interactive
# permission_mode (bypassPermissions/dontAsk for Claude Code; yolo/strict
# for Codex) and question_widget=False — both enforced at create / flip
# time by `validate_hidden_constraints`. Subagent sessions
# (`type=SUBAGENT`) ignore this flag entirely: they are already invisible
# everywhere via the `type=SESSION` filter.
hidden = models.BooleanField(default=False, db_index=True)
# Trace of the session that invoked the CLI to create this one
# (filiation). Set automatically by `twicc create-session` via PID
# ancestry (cf. `twicc whoami`); not exposed as a CLI flag. Independent
# of `hidden`: a visible session can also have spawned_by set. Immutable
# after creation (no UI / CLI mutates it). `on_delete=SET_NULL` so child
# sessions survive deletion of the parent (no cascade).
spawned_by = models.ForeignKey(
    "self",
    null=True,
    blank=True,
    default=None,
    on_delete=models.SET_NULL,
    related_name="spawned_sessions",
    db_index=True,
)
```

- [ ] **Step 2: Extend `idx_session_visible` condition**

In the same file, locate the `Meta.indexes` block (around lines 399-411) and update the `idx_session_visible` index to also require `hidden=False`:

```python
models.Index(
    fields=["project", "-mtime"],
    name="idx_session_visible",
    condition=models.Q(
        user_message_count__gt=0,
        type="session",
        created_at__isnull=False,
        hidden=False,
    ),
),
```

The rationale: every API listing endpoint adds `.filter(hidden=False)` (cf. Task 13), and the index serves those listings. The partial index also gets smaller (excludes hidden rows).

- [ ] **Step 3: Sanity check the model parses**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && python -m py_compile src/twicc/core/models.py && echo OK`
Expected: `OK`

- [ ] **Step 4: Generate the migration**

Run:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --name session_hidden_and_spawned_by --settings=twicc.settings
```

Expected output: the new file path is printed (e.g. `core/migrations/0085_session_hidden_and_spawned_by.py`). Inspect the file briefly — it should contain:
- `AddField` for `hidden` (BooleanField, default False)
- `AddField` for `spawned_by` (ForeignKey to self, nullable, SET_NULL)
- `RemoveIndex` + `AddIndex` for `idx_session_visible` (the new partial index condition supersedes the old one)

If the migration looks wrong, delete it and re-run after fixing the model. **Do NOT run `migrate` yourself** — that's reserved for the user.

- [ ] **Step 5: Django check (no DB write)**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/core/models.py src/twicc/core/migrations/
git commit -m "$(cat <<'EOF'
feat(models): add Session.hidden and Session.spawned_by

Two new structural fields on Session:

- hidden (bool, default False, indexed): toggles user visibility — the
  session and its broadcasts/listings/counters are filtered out
  everywhere when set, while costs continue to flow into aggregates.

- spawned_by (FK self, nullable, SET_NULL, indexed): records the
  session that invoked the CLI to create this one (filiation). Filled
  automatically by twicc create-session via PID ancestry, never
  exposed as a CLI flag, never mutated after creation.

The partial idx_session_visible index is extended with hidden=False so
the dominant listing query path stays index-only.

Migration adds the two columns and swaps the index. Defaults are
backwards-compatible (hidden=False, spawned_by=NULL on every existing
row).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Remind the user to run the migration**

After the commit, tell the user:

> "Migration `00XX_session_hidden_and_spawned_by` is committed but not applied. Please run `cd <worktree> && TWICC_DATA_DIR=$PWD uv run python -m django migrate --settings=twicc.settings` whenever you're ready (it's reserved to you per CLAUDE.md)."

---

## Task 2: Bump Tantivy schema with `hidden` + `spawned_by` fields

**Files:**
- Modify: `src/twicc/search.py` (around line 14 — schema docstring; lines 96-106 — `_build_schema`; the `index_document` signature; the `search` signature)
- Modify: `src/twicc/search_indexing_task.py` (`CURRENT_SEARCH_VERSION` constant; the boot path that nukes the index on version change; line 327 — bulk-progress counter)

This task ONLY bumps the schema and adapts the indexing/search signatures. Wiring the new fields from callers (sessions_watcher, reindex on flip, CLI flags) comes in later tasks (16, 19, 20). The intent: get the schema in place first so subsequent tasks have something to write to.

- [ ] **Step 1: Update the schema builder**

In `search.py:_build_schema()` (line 96-106), add the two fields after `archived`:

```python
def _build_schema() -> tantivy.Schema:
    """Build and return the Tantivy schema for the search index."""
    builder = tantivy.SchemaBuilder()
    builder.add_text_field("body", stored=True, tokenizer_name="twicc")
    builder.add_unsigned_field("line_num", stored=True, indexed=True)
    builder.add_text_field("session_id", stored=True, tokenizer_name="raw")
    builder.add_text_field("project_id", stored=True, tokenizer_name="raw")
    builder.add_text_field("from_role", stored=True, tokenizer_name="raw")
    builder.add_date_field("timestamp", stored=True, indexed=True)
    builder.add_boolean_field("archived", stored=True, indexed=True)
    builder.add_boolean_field("hidden", stored=True, indexed=True)
    builder.add_text_field("spawned_by", stored=True, tokenizer_name="raw")
    return builder.build()
```

Also update the module docstring at the top of `search.py` (lines 7-15) to list the two new schema fields:

```
hidden     — whether the session is hidden from the user (boolean filter)
spawned_by — session_id of the session that spawned this one (exact match via raw tokenizer)
```

- [ ] **Step 2: Update `index_document` signature**

Locate the `index_document(...)` function in `search.py` (search for `def index_document`). Add the two new kwargs after `archived`:

```python
def index_document(
    session_id: str,
    project_id: str,
    line_num: int,
    from_role: str,
    body: str,
    timestamp: datetime | None,
    archived: bool,
    hidden: bool = False,
    spawned_by_id: str | None = None,
) -> None:
    ...
```

In the body of the function, write the two new fields onto the `tantivy.Document` (look for where `archived` is written) — add a `hidden` boolean write and a `spawned_by` string write (use `""` if `spawned_by_id is None` since Tantivy can't store NULL text):

```python
doc.add_boolean("hidden", hidden)
doc.add_text("spawned_by", spawned_by_id or "")
```

- [ ] **Step 3: Update `reindex_session` to pass the new fields**

Find `reindex_session(session_id)` in `search.py` (around line 267). It looks up the `Session` row and calls `index_document(..., archived=session.archived)`. Extend the call:

```python
search.index_document(
    ...,
    archived=session.archived,
    hidden=session.hidden,
    spawned_by_id=session.spawned_by_id,
)
```

(Adapt to the actual call site signature — the existing call may be split across lines.)

- [ ] **Step 4: Update `search()` signature with the new filter knobs**

Find the public `search(query: str, ...)` function (it returns a `SearchResults`). Add three optional parameters:

```python
def search(
    query: str,
    ...,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
) -> SearchResults:
    ...
```

Inside the function, where the Tantivy boolean query is assembled, add term filters:

- If `only_hidden`: add a `must` clause `hidden=True`.
- Elif **not** `include_hidden`: add a `must` clause `hidden=False`. *(Default behaviour. UI and CLI without `--include-hidden` get only visible sessions.)*
- If `spawned_by` is not None: add a `must` clause `spawned_by=<spawned_by>`.

Use `tantivy.Query.term_query(_schema, "hidden", True)` (and `False`) and `tantivy.Query.term_query(_schema, "spawned_by", spawned_by)`. Be careful with the boolean term API — Tantivy py expects native bool values for boolean fields.

- [ ] **Step 5: Bump `CURRENT_SEARCH_VERSION`**

In `search_indexing_task.py`, find `CURRENT_SEARCH_VERSION = N` (a module-level int) and bump it by one. Add a brief comment next to the bump:

```python
# Bumped when the schema or document layout changes — forces a full
# rebuild of the on-disk index at next startup.
# v<N> -> v<N+1>: added `hidden` and `spawned_by` fields (hidden-sessions feature).
CURRENT_SEARCH_VERSION = <N+1>
```

- [ ] **Step 6: Ensure on-disk index is nuked when the schema changes**

Tantivy refuses to open an index whose on-disk schema disagrees with the in-memory schema. The bump above forces every session to be re-indexed, but the search directory itself must be wiped first.

Look in `search_indexing_task.py` (or `search.py:init_search_index`) for an existing mechanism that handles schema migration. Two cases:

- **If a mechanism exists** (e.g. a `_schema_version_file` written to the search dir, or a `try / except / rmtree` around `init_search_index`): verify it still triggers on the version bump. If so, no change needed.
- **If no mechanism exists yet**: add one. Recommended approach:
  - At startup, before `init_search_index()`, check a `search-index/.schema-version` text file. If absent or != `CURRENT_SEARCH_VERSION`, `shutil.rmtree(get_search_dir())` and re-create with the new version written.
  - Centralise the logic in a new helper `ensure_search_index_dir()` called from `init_search_index()`.

Decide based on what's already there. Document the choice in the commit message.

- [ ] **Step 7: Apply the `hidden=False` filter to the bulk-progress counter**

In `search_indexing_task.py` around line 327, the bulk indexer reports progress with a `Session.objects.filter(type=SessionType.SESSION).count()` (or similar). Add `hidden=False`:

```python
Session.objects.filter(type=SessionType.SESSION, hidden=False).count()
```

Rationale: the bulk indexer DOES index hidden sessions (so they can be searched by their owning agents), but the progress counter is the *user-visible* tally — keep it aligned with the user's mental model.

Wait — actually we DO want all sessions in the index (hidden included) so agents can search their filiation. The bulk indexer query should NOT filter `hidden=False`. Re-read the spec §8.3 to confirm: **the index includes hidden sessions; only the count for log/UI purposes is filtered.** Line 327 is a `count()` for progress reporting — yes, filter it `hidden=False` to keep the progress display aligned with what the UI considers as "sessions". The actual indexing loop (line 312-316 region) must NOT filter `hidden`.

- [ ] **Step 8: Sanity-check the file parses**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && python -m py_compile src/twicc/search.py src/twicc/search_indexing_task.py && echo OK`
Expected: `OK`

- [ ] **Step 9: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/search.py src/twicc/search_indexing_task.py
git commit -m "$(cat <<'EOF'
feat(search): add hidden and spawned_by fields to Tantivy schema

Schema bump for the hidden-sessions feature:

- New boolean field `hidden` (stored, indexed) on every search document.
- New raw-tokenized text field `spawned_by` (stored, indexed) holding
  the session_id of the spawning session, or "" when none.
- index_document() and reindex_session() take the two new fields.
- search() accepts include_hidden=False, only_hidden=False, spawned_by=None.
  Default filter is hidden=False — UI and CLI without --include-hidden
  see only visible sessions. Hidden documents are still indexed so
  agents can search within sessions they spawned (cf. spec §8.3).
- CURRENT_SEARCH_VERSION bumped, search-index/ wiped on mismatch
  (Tantivy can't accept a new schema in place).
- Bulk-progress counter filters hidden=False (display only — the actual
  indexing loop still picks up every session).

Wiring of the new fields from sessions_watcher and the CLI listings
arrives in subsequent tasks; this task only locks the schema in place.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `pending_session_attributes` module

**Files:**
- Create: `src/twicc/pending_session_attributes.py`

This module mirrors `pending_agent_settings.py` (cf. `src/twicc/pending_agent_settings.py`). It stashes the `hidden` and `spawned_by_id` values between the moment the CLI / WS handler decides them and the moment the provider's file watcher creates the matching `Session` row from the first JSONL line.

- [ ] **Step 1: Create the module**

```python
"""
Pending per-session structural attributes buffer (hidden + spawned_by).

Same pattern as :mod:`twicc.pending_agent_settings`, but for the two
structural attributes that fall outside the closed ``AgentSettings``
bundle: ``hidden`` and ``spawned_by_id``.

When the CLI / WS handler decides those values, the ``Session`` row
does not exist yet — it will be created by the provider's file watcher
on the first JSONL line. This module bridges the gap with a simple
in-memory keyed store, identical in spirit to the agent-settings
buffer.

- :func:`set_pending_session_attributes` is called by the create-session
  service before the manager spawns the agent process;
- :func:`pop_pending_session_attributes` is called by the watcher when
  it creates the row, and the values are forwarded to
  ``Session.objects.create(...)``.

The absence of a pending entry is signalled by ``None``.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


class PendingSessionAttributes(NamedTuple):
    hidden: bool
    spawned_by_id: str | None


# session_id -> PendingSessionAttributes
_pending: dict[str, PendingSessionAttributes] = {}


def set_pending_session_attributes(
    session_id: str,
    *,
    hidden: bool = False,
    spawned_by_id: str | None = None,
) -> None:
    """Store pending structural attributes to be applied at row creation."""
    _pending[session_id] = PendingSessionAttributes(
        hidden=hidden,
        spawned_by_id=spawned_by_id,
    )
    logger.debug(
        "Set pending session attributes for %s: hidden=%s spawned_by_id=%s",
        session_id, hidden, spawned_by_id,
    )


def pop_pending_session_attributes(
    session_id: str,
) -> PendingSessionAttributes | None:
    """Get and remove the pending attributes for a session, or ``None``."""
    return _pending.pop(session_id, None)
```

- [ ] **Step 2: Sanity check**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && python -m py_compile src/twicc/pending_session_attributes.py && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/pending_session_attributes.py
git commit -m "$(cat <<'EOF'
feat(pending): add pending_session_attributes for hidden + spawned_by

Same pattern as pending_agent_settings.py: an in-memory stash/pop
store keyed by session_id, used to bridge the gap between the moment
the CLI / WS handler decides on `hidden` and `spawned_by_id` and the
moment the provider's file watcher creates the actual Session row
from the first JSONL line.

Consumed by sessions_watcher in a later task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `whoami` helper (`resolve_current_session`)

**Files:**
- Create: `src/twicc/cli/_session_request/whoami.py`

The helper walks the PID ancestry from the calling process upward and matches against `ProcessRun.agent_pid`. A single DB fetch builds the lookup table; the walk runs in pure Python against `psutil` (preferred) or a `/proc` + `ps` fallback.

- [ ] **Step 1: Confirm psutil availability**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && grep -E '^(psutil|.*"psutil)' pyproject.toml uv.lock | head -20`

Inspect output:
- If `psutil` appears in `pyproject.toml` `[project.dependencies]` or in `uv.lock`: it's available, use it.
- If not: use `os.getppid()` fallback (Linux/macOS only; sufficient for TwiCC's supported platforms).

Adapt the implementation below accordingly.

- [ ] **Step 2: Create the helper**

```python
"""PID-ancestry lookup against ``ProcessRun.agent_pid``.

Powers ``twicc whoami`` and the silent auto-fill of ``spawned_by`` in
``twicc create-session``. The strategy is intentionally cheap:

1. One DB read returns every non-DEAD ``ProcessRun`` with its
   ``agent_pid`` and ``session_id``.
2. We then walk the local PID chain (``os.getpid() → ppid → … → 1``)
   and stop on the first match — that's the closest live agent.

The closest-match semantics matter for nested cases: if a session A
spawns session B, and a Bash tool inside B calls ``twicc``, the chain
is ``twicc → bash → claude(B) → backend Python → …``. ``agent_pid``
of B is closer than A in the chain, so we resolve to B. Each level
of nesting works the same way.

Returns the resolved ``Session`` (full row, so callers can serialise
the same shape as ``twicc session <ID>``) or ``None`` when no match
is found in the ancestry (e.g. a human running ``twicc`` from a
plain terminal).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _walk_ppids():
    """Yield successive parent PIDs starting at ``os.getpid()`` up to PID 1."""
    pid = os.getpid()
    while pid is not None and pid > 1:
        ppid = _get_ppid(pid)
        if ppid is None or ppid <= 0:
            return
        yield ppid
        pid = ppid


def _get_ppid(pid: int) -> Optional[int]:
    """Return the parent PID of ``pid``, or ``None`` if unobtainable.

    Prefer ``psutil`` when available; fall back to ``/proc/<pid>/status``
    on Linux. ``ps -o ppid=`` is the last resort for macOS / BSD without
    psutil.
    """
    try:
        import psutil  # type: ignore[import-untyped]
        return psutil.Process(pid).ppid()
    except ImportError:
        pass
    except Exception:
        # Process gone / permission error: walk stops.
        return None

    # Linux fallback
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # macOS fallback via `ps`
    try:
        import subprocess
        out = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return int(out.stdout.strip())
    except Exception:
        pass

    return None


def resolve_current_session():
    """Return the ``Session`` of the closest live agent in the PID ancestry.

    Returns ``None`` when no ``ProcessRun.agent_pid`` matches any
    ancestor — typically the case for a human invoking ``twicc`` from
    a plain shell.

    The caller must have run ``django.setup()`` before this — the
    function does not bootstrap Django itself, to keep cold-start
    paths optional.
    """
    from twicc.core.enums import ProcessRunState
    from twicc.core.models import ProcessRun, Session

    # One DB read returns every live agent_pid → session_id pair.
    pid_to_session_id = dict(
        ProcessRun.objects.exclude(state=ProcessRunState.DEAD)
        .exclude(agent_pid__isnull=True)
        .values_list("agent_pid", "session_id")
    )
    if not pid_to_session_id:
        return None

    for ppid in _walk_ppids():
        sid = pid_to_session_id.get(ppid)
        if sid is not None:
            try:
                return Session.objects.get(pk=sid)
            except Session.DoesNotExist:
                # ProcessRun outlived the Session row (e.g. session deleted
                # while process still alive). Fall through; nothing else to
                # match in the ancestry.
                return None
    return None
```

If `psutil` is NOT in deps, leave the `import psutil` block alone — `ImportError` is caught and the function falls back automatically. No new dependency to add.

- [ ] **Step 3: Sanity check**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && python -m py_compile src/twicc/cli/_session_request/whoami.py && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/cli/_session_request/whoami.py
git commit -m "$(cat <<'EOF'
feat(cli): add resolve_current_session() PID-ancestry helper

Walks the local PID chain upward and matches against
ProcessRun.agent_pid, returning the closest live agent's Session row
(or None when invoked outside any active session — e.g. a human in
a plain shell).

One DB read builds a {agent_pid: session_id} map; the chain walk is
in-process via psutil (when present) with /proc and `ps` fallbacks.

Powers the new `twicc whoami` command and the silent auto-detection
of spawned_by in `twicc create-session` — both arrive in subsequent
tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `twicc whoami` CLI command + skill

**Files:**
- Create: `src/twicc/cli/whoami.py`
- Modify: `src/twicc/cli/__init__.py` (register the command)
- Create: `src/twicc/agent/plugin/twicc/skills/twicc-whoami/SKILL.md`

- [ ] **Step 1: Create the CLI command**

```python
"""``twicc whoami`` — identify the session that owns the calling process."""

from __future__ import annotations

import os
import sys

import typer


def whoami_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit a single JSON object on stdout instead of pretty text. "
            "Exit code is still 0 on success, 1 when no session is found."
        ),
    ),
) -> None:
    """Print details of the session that owns the calling process.

    Walks the PID ancestry from the current process upward and matches
    against the live agents tracked by TwiCC. When a match is found,
    prints the same details ``twicc session <ID>`` does — title,
    provider, project_id, cost, settings, lifecycle, spawned_by, etc.

    Useful from inside a session's Bash tool to discover the session's
    own identity (the agent doesn't otherwise know its TwiCC session_id).
    From a plain terminal, this command exits 1 with a clear message —
    by design, ``whoami`` is only meaningful inside an active session.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._session_request.whoami import resolve_current_session
    from twicc.core.serializers import serialize_session

    session = resolve_current_session()
    if session is None:
        msg = (
            "No TwiCC session found in PID ancestry. whoami is only "
            "meaningful from inside an active agent session."
        )
        if json_output:
            import orjson
            sys.stdout.buffer.write(orjson.dumps({"error": msg}))
            sys.stdout.write("\n")
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(1)

    data = serialize_session(session)
    if json_output:
        import orjson
        sys.stdout.buffer.write(orjson.dumps(data))
        sys.stdout.write("\n")
    else:
        # Reuse the same pretty-printer as `twicc session <ID>` if
        # convenient (see cli/session.py for the helper name) — fall
        # back to a structured dump otherwise.
        from twicc.cli.session import emit_session_details
        emit_session_details(data)
```

> **Note on `emit_session_details`:** check `src/twicc/cli/session.py` for the actual function used to pretty-print a session. If the name differs, use the matching one. If no such helper exists, dump as JSON-ish key=value lines.

- [ ] **Step 2: Register the command in `cli/__init__.py`**

Open `src/twicc/cli/__init__.py` and locate where other top-level commands are registered (look for `app.command(...)` patterns or sub-app registrations). Add:

```python
from twicc.cli.whoami import whoami_cmd
app.command("whoami")(whoami_cmd)
```

Place it alphabetically near the other read-only commands (`session`, `sessions`, `processes`, …) so `--help` output stays organised.

- [ ] **Step 3: Create the skill markdown**

```markdown
---
name: twicc-whoami
description: Return the details of the session that owns the calling process. Use to discover your own TwiCC session_id from inside a Bash tool, when you need to reference your own session (e.g. for related-command filtering).
---

# twicc-whoami

Lookup the TwiCC session owning the **current** invocation. Useful when an agent
needs its own session_id but doesn't otherwise have it in context.

## Mechanism

`twicc whoami` walks the PID ancestry from the current process up to PID 1, and
matches against the live agent processes TwiCC is tracking. If a match is found,
it prints the same details `twicc session <ID>` would print — id, provider,
title, project_id, costs, settings, lifecycle, etc.

If no match is found (you ran it from a plain terminal, not from inside an agent's
Bash tool), the command exits 1 with a clear message.

## Invocation

```bash
twicc whoami           # human-readable output
twicc whoami --json    # machine-readable JSON dump
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Session resolved; details printed |
| 1 | No session in PID ancestry (also: ran from a plain shell) |

## Typical use

```bash
# I'm an agent; what's my TwiCC session_id?
twicc whoami --json | jq -r .id
```

For listing or searching the sessions YOU created, prefer the dedicated
`--spawned-by self` flag on `twicc sessions`, `twicc processes`, and
`twicc search` (no need to call whoami first).
```

- [ ] **Step 4: Sanity checks**

Run:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile src/twicc/cli/whoami.py src/twicc/cli/__init__.py && echo OK
```
Expected: `OK`

Then verify the command is registered (this command does Django setup so it must run with `TWICC_DATA_DIR`):
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
TWICC_DATA_DIR=$PWD uv run twicc whoami --help
```
Expected: a `--help` block describing the command.

Then invoke it once from your terminal (no session in ancestry → exit 1):
```bash
TWICC_DATA_DIR=$PWD uv run twicc whoami; echo "exit=$?"
```
Expected: a message about no session in ancestry; exit code 1.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/cli/whoami.py src/twicc/cli/__init__.py src/twicc/agent/plugin/twicc/skills/twicc-whoami/
git commit -m "$(cat <<'EOF'
feat(cli): add twicc whoami command + skill

Discovers the TwiCC session owning the calling process by walking the
PID ancestry and matching against ProcessRun.agent_pid. Prints the
same details as `twicc session <ID>` when a match is found; exits 1
with a clear message when invoked outside any active session.

Useful from inside an agent's Bash tool when the agent needs to
reference its own session_id (e.g. for follow-up listings via
`--spawned-by self`, which itself uses the same resolver under the
hood — covered in subsequent tasks).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `validate_hidden_constraints` helper

**Files:**
- Modify: `src/twicc/cli/_session_request/validation.py`

A new pure-function validator that enforces the two invariants for hidden
sessions:

1. `permission_mode ∈ whitelist[provider]`
2. `question_widget != True` (Claude Code only — Codex ignores)

Used by `create-session`, by `update-session settings` (when target is hidden), and by `session_visibility.hide_session` (cf. Task 9).

- [ ] **Step 1: Add the whitelist constant**

At the top of `validation.py` (around the existing `_FIELD_TO_FLAG` dict), add:

```python
# Permission modes that do NOT require interactive approvals. Hidden
# sessions must run with one of these because they have no UI surface
# to render approval prompts. Whitelist is per provider — the resolved
# permission_mode value is provider-specific.
_PERMISSION_MODE_HIDDEN_WHITELIST = {
    "claude_code": frozenset({"bypassPermissions", "dontAsk"}),
    "codex": frozenset({"yolo", "strict"}),
}
```

- [ ] **Step 2: Add the validator function**

After `validate_no_set_unset_conflict` (the end of the file), append:

```python
def validate_hidden_constraints(
    provider: str,
    settings,
    *,
    hidden: bool,
) -> list[ValidationError]:
    """Enforce hidden-session invariants on the resolved AgentSettings bundle.

    Called with the **post-resolution** settings (preset applied + CLI
    overrides + ``enforce_agent_settings_consistency`` already run), so the
    ``permission_mode`` and ``question_widget`` values reflect what the
    session would actually be started with.

    When ``hidden`` is ``False``, returns an empty list — the constraints
    only apply to hidden sessions. When ``hidden`` is ``True``:

    - ``permission_mode`` must be in ``_PERMISSION_MODE_HIDDEN_WHITELIST[provider]``;
    - ``question_widget`` must NOT be ``True`` for providers that use it
      (Claude Code). For other providers (Codex) the field is ignored.

    Always returns a flat list of :class:`ValidationError` so the caller
    can aggregate with the other validators.
    """
    if not hidden:
        return []
    errors: list[ValidationError] = []

    # --- permission_mode whitelist -------------------------------
    whitelist = _PERMISSION_MODE_HIDDEN_WHITELIST.get(provider, frozenset())
    if not whitelist:
        errors.append(ValidationError(
            "--hidden", "hidden_unsupported_provider",
            f"--hidden is not supported for provider {provider!r}. "
            f"No non-interactive permission mode is configured for it.",
        ))
        return errors

    mode = getattr(settings, "permission_mode", None)
    if mode not in whitelist:
        whitelist_str = ", ".join(sorted(whitelist))
        errors.append(ValidationError(
            "--permission-mode", "hidden_requires_non_interactive",
            f"--hidden requires a non-interactive permission_mode. "
            f"Provider {provider} accepts: {whitelist_str}. Got: {mode!r}.",
        ))

    # --- question_widget incompatibility (Claude Code only) -----
    # Codex never uses question_widget; ignore the field entirely there.
    if provider == "claude_code":
        qw = getattr(settings, "question_widget", None)
        if qw is True:
            errors.append(ValidationError(
                "--question-widget", "hidden_incompatible_with_question_widget",
                "--hidden is incompatible with question_widget=True. "
                "Pass --no-question-widget to override.",
            ))

    return errors
```

- [ ] **Step 3: Sanity check**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && python -m py_compile src/twicc/cli/_session_request/validation.py && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/cli/_session_request/validation.py
git commit -m "$(cat <<'EOF'
feat(cli): add validate_hidden_constraints + permission-mode whitelist

Validator the hidden-sessions feature relies on:

- permission_mode whitelist (Claude Code: bypassPermissions, dontAsk;
  Codex: yolo, strict). Hidden sessions cannot use interactive modes
  because they have no UI surface to render approval prompts.
- question_widget=True is incompatible with hidden=True for Claude Code
  (Codex ignores the field entirely).

Pure function operating on the post-resolution AgentSettings bundle;
returns the same ValidationError tuples as the rest of the
validation module so callers can aggregate freely.

Wired into create-session, update-session settings, and the
session_visibility flip service in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `twicc create-session` — `--hidden` flag + silent `spawned_by` auto-fill

**Files:**
- Modify: `src/twicc/cli/create_session/command.py`
- Modify: `src/twicc/cli/_session_request/drop_file.py` (no change beyond the payload — already passes `payload` through)

- [ ] **Step 1: Add the `--hidden` flag**

In `create_session/command.py`, locate the Typer command function (probably named `create_session_cmd` or similar). Add a new option **alongside** the existing `--archived`, `--pinned` etc.:

```python
hidden: bool = typer.Option(
    False,
    "--hidden",
    help=(
        "Create the session as hidden — invisible from every list, "
        "search, broadcast, and counter shown to the user, while still "
        "counted in cost aggregates. Requires a non-interactive "
        "permission_mode (bypassPermissions/dontAsk for Claude Code; "
        "yolo/strict for Codex) and question_widget=False."
    ),
),
```

There is NO `--spawned-by` flag. Auto-fill is silent (cf. Step 3).

- [ ] **Step 2: Validate hidden constraints**

Find where `validate_settings`, `validate_provider`, `validate_unset_fields` and `validate_no_set_unset_conflict` are called in `create_session_cmd`. After all of them (including the post-resolution / preset-merge / enforce-consistency step), call:

```python
from twicc.cli._session_request.validation import validate_hidden_constraints

errors.extend(validate_hidden_constraints(
    provider, resolved_settings, hidden=hidden,
))
```

Use the same `errors` list the other validators feed; aggregation flows naturally into `emit_validation_errors(errors, ...)` already wired in.

- [ ] **Step 3: Auto-detect `spawned_by` via `whoami`**

Just before the payload is built (look for the dict literal passed to `write_drop_file`), add the silent resolution:

```python
from twicc.cli._session_request.whoami import resolve_current_session

current = resolve_current_session()
spawned_by_session_id = current.id if current is not None else None
```

No log, no output, no error. If we're outside any session (human in a plain shell), `spawned_by_session_id` stays `None`.

- [ ] **Step 4: Inject into the payload**

In the same function, where the payload is built, add the two keys:

```python
payload = {
    ...
    "hidden": hidden,
    "spawned_by_session_id": spawned_by_session_id,
    ...
}
```

The drop-file writer (`write_drop_file`) already passes the payload through verbatim — no change there.

- [ ] **Step 5: Sanity check**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && python -m py_compile src/twicc/cli/create_session/command.py && echo OK`
Expected: `OK`

Verify the new flag shows up:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
TWICC_DATA_DIR=$PWD uv run twicc create-session --help 2>&1 | grep -A2 -- "--hidden"
```
Expected: `--hidden` block printed.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/cli/create_session/command.py
git commit -m "$(cat <<'EOF'
feat(cli): twicc create-session --hidden + silent spawned_by auto-fill

- New --hidden flag. When set, the post-resolution settings must
  satisfy validate_hidden_constraints (permission_mode whitelist +
  question_widget=False). Aggregated with the other validators.
- No --spawned-by flag is exposed. Instead, the CLI silently calls
  resolve_current_session() and stamps the result into the payload
  as `spawned_by_session_id`. Outside a session (human shell), the
  resolution returns None and the field stays NULL.

Both values land in the drop-file; the server-side service consumes
them in a subsequent task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `session_creation.py` — honor `hidden` / `spawned_by` + re-validate server-side

**Files:**
- Modify: `src/twicc/core/services/session_creation.py`

- [ ] **Step 1: Extract the two fields from the payload**

In `create_session_from_payload`, right after the existing `payload.get` block (around lines 70-77 of the current file), add:

```python
hidden = bool(payload.get("hidden", False))
spawned_by_session_id = payload.get("spawned_by_session_id")  # str | None
```

- [ ] **Step 2: Validate `spawned_by_session_id` references an existing session**

After the project resolution block (around line 145), insert:

```python
# --- spawned_by validation -----------------------------------
if spawned_by_session_id is not None:
    from twicc.core.models import Session
    exists = await sync_to_async(
        lambda: Session.objects.filter(pk=spawned_by_session_id).exists()
    )()
    if not exists:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError(
                "spawned_by_session_id", "invalid_spawned_by",
                f"spawned_by session {spawned_by_session_id!r} does not exist",
            )
        ])
```

If the ID is forged or stale, fail loudly rather than letting the FK constraint blow up later in an opaque `IntegrityError`.

- [ ] **Step 3: Re-run `validate_hidden_constraints` server-side**

Just after the `effective = helpers.enforce_agent_settings_consistency(effective)` line (around line 187), add:

```python
# --- hidden constraints (defence in depth) -------------------
# The CLI validates these before writing the drop-file; we re-validate
# from the payload because the drop-file is a trust boundary (forged or
# version-skewed callers can submit invalid combinations).
from twicc.cli._session_request.validation import validate_hidden_constraints
hidden_errors = validate_hidden_constraints(
    provider.value, effective, hidden=hidden,
)
if hidden_errors:
    return SessionCreationResult(False, None, None, None, [
        SessionCreationError(e.field, e.code, e.message) for e in hidden_errors
    ])
```

- [ ] **Step 4: Stash pending session attributes**

After the existing `set_pending_agent_settings(session_id, agent_settings)` call (around line 180), add a sibling call:

```python
from twicc.pending_session_attributes import set_pending_session_attributes
set_pending_session_attributes(
    session_id,
    hidden=hidden,
    spawned_by_id=spawned_by_session_id,
)
```

- [ ] **Step 5: Sanity check**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions && python -m py_compile src/twicc/core/services/session_creation.py && echo OK`
Expected: `OK`

Then a Django system check:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings
```
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/core/services/session_creation.py
git commit -m "$(cat <<'EOF'
feat(services): session_creation honors hidden + spawned_by

Three additions to create_session_from_payload:

- Read `hidden` (bool) and `spawned_by_session_id` (str|None) from
  the payload.
- Validate the spawned_by ID references an existing session (clear
  error instead of a downstream IntegrityError).
- Re-run validate_hidden_constraints server-side against the resolved
  AgentSettings (defence in depth — the CLI validates up-front but
  the drop-file is a trust boundary).
- Stash both values via pending_session_attributes so the file
  watcher applies them when it creates the row.

The Session row itself is still created by the file watcher when the
provider writes the first JSONL line; wiring of the pop into the
watcher arrives in Task 14.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `session_visibility` service (hide_session / unhide_session)

**Files:**
- Create: `src/twicc/core/services/session_visibility.py`

The two functions own the entire flip lifecycle: pre-validations, atomic save, counter recompute, FTS re-index, broadcasts. Symmetric on both sides.

- [ ] **Step 1: Create the module**

```python
"""hide_session / unhide_session — orchestrate the hidden-flag flip.

Both entry points are async (they touch DB + broadcast + FTS), receive
the already-fetched ``Session`` row, and return a structured result the
caller turns into a status file or WS payload. They share private
helpers for the recompute side-effects.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


class SessionVisibilityError(NamedTuple):
    field: str
    code: str
    message: str


class SessionVisibilityResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SessionVisibilityError] | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def hide_session(session) -> SessionVisibilityResult:
    """Flip hidden False -> True.

    Pre-conditions: session is type=SESSION, permission_mode is in the
    hidden whitelist for its provider, question_widget is not True
    (Claude Code).
    """
    errors = _check_type_session(session)
    if errors:
        return _fail(session, errors)
    if session.hidden:
        return _ok(session)  # no-op: already hidden
    errors = _check_hidden_invariants(session)
    if errors:
        return _fail(session, errors)

    await _apply_flip(session, new_hidden=True)
    await _broadcast_session_removed(session.id)
    await _broadcast_project_updated(session.project_id)
    return _ok(session)


async def unhide_session(session) -> SessionVisibilityResult:
    """Flip hidden True -> False.

    No invariant checks beyond type=SESSION: the session re-enters the
    user surface, the user can reconfigure permission_mode / question_widget
    freely afterwards.
    """
    errors = _check_type_session(session)
    if errors:
        return _fail(session, errors)
    if not session.hidden:
        return _ok(session)  # no-op: already visible

    await _apply_flip(session, new_hidden=False)
    await _broadcast_session_updated(session)
    await _broadcast_project_updated(session.project_id)
    return _ok(session)


# ---------------------------------------------------------------------------
# Pre-conditions
# ---------------------------------------------------------------------------


def _check_type_session(session) -> list[SessionVisibilityError]:
    from twicc.core.enums import SessionType
    if session.type != SessionType.SESSION:
        return [SessionVisibilityError(
            "type", "not_top_level",
            "Only top-level sessions (type=SESSION) can be hidden; "
            f"got type={session.type!r}.",
        )]
    return []


def _check_hidden_invariants(session) -> list[SessionVisibilityError]:
    """Run validate_hidden_constraints against the current Session row.

    We construct the AgentSettings-like view by reading the columns
    directly (the session is already saved, no preset to merge).
    """
    from twicc.cli._session_request.validation import validate_hidden_constraints
    from twicc.providers.helpers import AgentSettings

    fake_settings = AgentSettings(
        **{field: getattr(session, field, None) for field in AgentSettings._fields}
    )
    vlist = validate_hidden_constraints(
        session.provider, fake_settings, hidden=True,
    )
    return [SessionVisibilityError(v.field, v.code, v.message) for v in vlist]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


async def _apply_flip(session, *, new_hidden: bool) -> None:
    """Save the flag, recompute counters, reindex FTS — all in async hops."""
    from twicc.core.models import (
        DailyActivity, WeeklyActivity, Session, SessionItem,
    )
    from twicc.core.enums import Provider
    from twicc.projects import update_project_metadata
    from twicc.providers import db_writer
    from twicc.search import reindex_session

    @sync_to_async
    def _save_and_recompute():
        # 1. Toggle the flag and persist with a tight update_fields list.
        session.hidden = new_hidden
        session.save(update_fields=["hidden"])

        # 2. Recompute sessions_count on the Project. update_project_metadata
        #    is the synchronous path that already covers sessions_count
        #    via its single Session.objects.filter(...).count() call (cf.
        #    Task 15). The db_writer path is event-driven (an
        #    UpdateProjectMetadataPayload with recalc_sessions_count=True
        #    is enqueued by other producers); explicitly calling
        #    update_project_metadata here is sufficient and synchronous.
        update_project_metadata(session.project)

        # 3. Collect dates impacted by the session's items, then recompute
        #    PeriodicActivity for each (DailyActivity + WeeklyActivity,
        #    per-project + global).
        days = {
            d for d, in SessionItem.objects
            .filter(session=session, timestamp__isnull=False)
            .values_list("timestamp__date")
            .distinct()
        }
        if days:
            from twicc.core.models import PeriodicActivity
            provider_enum = Provider(session.provider)
            PeriodicActivity.recalculate_for_days(
                session.project_id, days, provider_enum,
            )

        # 4. Reindex the session document — the `hidden` Tantivy field
        #    is now stale.
        reindex_session(session.id)

    await _save_and_recompute()


# ---------------------------------------------------------------------------
# Broadcasts
# ---------------------------------------------------------------------------


async def _broadcast_session_removed(session_id: str) -> None:
    """Emit a session_removed WS event so connected clients drop the row."""
    layer = get_channel_layer()
    if layer is None:
        return
    await layer.group_send("updates", {
        "type": "session_removed",
        "session_id": session_id,
    })


async def _broadcast_session_updated(session) -> None:
    """Emit a session_updated WS event with the freshly visible session."""
    from twicc.core.serializers import serialize_session
    layer = get_channel_layer()
    if layer is None:
        return
    payload = await sync_to_async(serialize_session)(session)
    await layer.group_send("updates", {
        "type": "session_updated",
        "session": payload,
    })


async def _broadcast_project_updated(project_id: str) -> None:
    """Emit a project_updated WS event (sessions_count + cost may have changed)."""
    from twicc.providers.db_writer import _broadcast_project_updated as broadcast
    # Reuse the existing helper if the signature matches; otherwise
    # inline the equivalent send_group call.
    try:
        await broadcast(project_id)
    except Exception:
        logger.exception("Failed to broadcast project_updated for %s", project_id)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _ok(session) -> SessionVisibilityResult:
    return SessionVisibilityResult(
        success=True,
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        errors=None,
    )


def _fail(session, errors) -> SessionVisibilityResult:
    return SessionVisibilityResult(
        success=False,
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        errors=errors,
    )
```

> **Note on the project count recompute:** `db_writer.recalc_sessions_count` is **not** a callable — it's a boolean field on `UpdateProjectMetadataPayload` (cf. `db_writer.py:163`) consumed by the unified DB writer when other producers enqueue it. Calling `update_project_metadata(project)` directly here is the right move (synchronous, no payload routing). The matching counter filter `hidden=False` is added to `update_project_metadata` in Task 15.

> **Note on `_broadcast_project_updated`:** verify the actual name and async-ness in `db_writer.py` — the import path / wrapper may differ. The intent is to fire the same `project_updated` WS payload the rest of TwiCC already uses.

- [ ] **Step 2: Sanity checks**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile src/twicc/core/services/session_visibility.py && echo OK
```
Expected: `OK`

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings
```
Expected: no issues.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/core/services/session_visibility.py
git commit -m "$(cat <<'EOF'
feat(services): add session_visibility (hide_session / unhide_session)

Orchestrates the hidden-flag flip end-to-end:

- Pre-conditions: type=SESSION; on hide, permission_mode is in the
  per-provider whitelist and question_widget is not True (Claude Code).
- Atomic save with update_fields=['hidden'].
- Recompute Project.sessions_count via both write paths.
- Recompute DailyActivity / WeeklyActivity for every day touched by
  the session's items (collected via SessionItem.timestamp__date).
- Reindex the session document in Tantivy so the new `hidden` field
  is current (no delete+recreate needed — reindex rewrites in place).
- Broadcast: project_updated unconditionally, plus session_removed
  on hide / session_updated on unhide.

Idempotent: hiding an already-hidden session (or unhiding a visible
one) is a no-op success.

CLI wiring + drop-file routing arrives in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `twicc update-session hide/unhide` CLI + drop-file routing

**Files:**
- Create: `src/twicc/cli/update_session/hidden_command.py`
- Modify: `src/twicc/cli/update_session/__init__.py` (or wherever the sub-app dispatcher lives — check the existing `archived_command.py` registration)
- Modify: `src/twicc/pending_sessions_watcher.py` (route the new `kind="update_hidden"`)
- Modify: `src/twicc/core/services/session_update.py` (add `update_session_hidden_from_payload` glue)

- [ ] **Step 1: Create `hidden_command.py` mirroring `archived_command.py`**

```python
"""``twicc update-session <ID> hide | unhide`` sub-commands.

Two commands sharing the same plumbing — they only differ by the
boolean they put in the ``kind="update_hidden"`` payload.

- ``hide``   → ``hidden=True``: the server runs the hidden-invariants
  pre-checks (permission_mode whitelist, question_widget != True) and
  rejects the request if the session can't satisfy them — the user
  must first switch the offending setting via ``update-session settings``.
  On success, the session is removed from every list / search / counter
  immediately (broadcast ``session_removed``); costs continue to flow
  into aggregates.
- ``unhide`` → ``hidden=False``: the server flips the flag back; the
  session re-enters the user surface (broadcast ``session_updated``).
  No invariant checks — the user is free to reconfigure
  ``permission_mode`` afterwards.

Pattern mirrors ``archived_command.py``; only the kind / labels change.
"""

from __future__ import annotations

import typer


def _run_hidden_update(
    session_id: str,
    *,
    hidden: bool,
    timeout: int,
    no_color: bool,
    json_output: bool,
) -> None:
    """Drop a ``kind="update_hidden"`` payload and wait for the status."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._session_request.discovery import (
        ServerDownError, check_heartbeat, get_data_dir,
    )
    from twicc.cli._session_request.drop_file import write_drop_file
    from twicc.cli._session_request.output import (
        emit_final, emit_progress, emit_validation_errors,
    )
    from twicc.cli._session_request.polling import poll_status
    from twicc.cli._session_request.session_lookup import (
        SessionLookupError, lookup_session,
    )
    from twicc.cli._session_request.validation import ValidationError

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors(
            [ValidationError("SESSION_ID", e.code, e.message)],
            json_output=json_output,
        )
        raise typer.Exit(1)

    emit_progress(
        f"✓ Session {resolved.session_id!r} resolved "
        f"(provider: {resolved.provider}, project: {resolved.project_id})",
        json_output=json_output,
    )

    emit_progress(
        f"✓ {'Hide' if hidden else 'Unhide'} request prepared",
        json_output=json_output,
    )

    payload = {
        "session_id": resolved.session_id,
        "hidden": hidden,
    }

    drop = write_drop_file(get_data_dir(), payload, kind="update_hidden")
    emit_progress(
        f"→ Request submitted (request_uuid: {drop.request_uuid[:8]}...)",
        json_output=json_output,
    )

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(
        outcome,
        request_uuid=drop.request_uuid,
        json_output=json_output,
        timeout=timeout,
    )

    if outcome.status == "updated":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout


def update_hide_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(30, "--timeout", help="Seconds to wait for the server's final status."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout. Implies --no-color."),
) -> None:
    """Hide the session.

    Removes the session from every user-visible list / search / counter
    while keeping costs in aggregates. Requires the session's
    permission_mode to be in the non-interactive whitelist and (Claude
    Code) question_widget=False — change those first via
    `twicc update-session <ID> settings` if needed.

    Connected clients receive a `session_removed` broadcast and drop the
    session from their store.
    """
    _run_hidden_update(
        ctx.obj,
        hidden=True,
        timeout=timeout,
        no_color=no_color,
        json_output=json_output,
    )


def update_unhide_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(30, "--timeout", help="Seconds to wait for the server's final status."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout. Implies --no-color."),
) -> None:
    """Unhide the session.

    Flips hidden back to False; the session reappears in every list /
    search / counter. Connected clients receive a `session_updated`
    broadcast and re-add it to their store. Counters and FTS are
    re-synced server-side.
    """
    _run_hidden_update(
        ctx.obj,
        hidden=False,
        timeout=timeout,
        no_color=no_color,
        json_output=json_output,
    )
```

- [ ] **Step 2: Register the sub-commands**

Open `src/twicc/cli/update_session/command.py` (lines 14-23 — existing imports; lines 48-53 — existing sub-command registrations). Add the new import:

```python
from twicc.cli.update_session.hidden_command import (
    update_hide_cmd, update_unhide_cmd,
)
```

Then add two registration lines next to the existing `archive` / `unarchive` (around line 51):

```python
update_session_app.command(name="hide")(update_hide_cmd)
update_session_app.command(name="unhide")(update_unhide_cmd)
```

The `update_session_app` Typer sub-app is already defined in the same file (around line 26).

- [ ] **Step 3: Route the new `kind="update_hidden"` in the watcher**

In `src/twicc/pending_sessions_watcher.py`, find the chain of `elif kind == "...":` (around lines 138-167). Insert a new branch after `update_archived`:

```python
elif kind == "update_hidden":
    from twicc.core.services.session_update import (
        update_session_hidden_from_payload,
    )
    service = update_session_hidden_from_payload
    success_status = "updated"
```

- [ ] **Step 4: Add the service glue in `session_update.py`**

In `src/twicc/core/services/session_update.py` (mirror the existing `update_session_archived_from_payload`), add:

```python
async def update_session_hidden_from_payload(payload: dict):
    """Drop-file glue for `kind="update_hidden"`.

    Looks up the session, then delegates to session_visibility.hide_session
    or unhide_session based on the boolean in the payload.
    """
    from twicc.core.models import Session
    from twicc.core.services.session_visibility import (
        hide_session, unhide_session, SessionVisibilityResult,
        SessionVisibilityError,
    )

    session_id = payload.get("session_id")
    hidden = bool(payload.get("hidden", False))

    if not session_id:
        return SessionVisibilityResult(False, None, None, None, [
            SessionVisibilityError(
                "session_id", "missing", "session_id is required",
            )
        ])

    try:
        session = await sync_to_async(
            lambda: Session.objects.select_related("project").get(pk=session_id)
        )()
    except Session.DoesNotExist:
        return SessionVisibilityResult(False, None, None, None, [
            SessionVisibilityError(
                "session_id", "session_not_found",
                f"Session {session_id!r} not found",
            )
        ])

    if hidden:
        return await hide_session(session)
    return await unhide_session(session)
```

> **Note:** the existing services in this file may already have an `asgiref.sync` import; reuse it. Same for the structured-result tuples — match the in-file convention.

- [ ] **Step 5: Sanity checks**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile \
    src/twicc/cli/update_session/hidden_command.py \
    src/twicc/cli/update_session/__init__.py \
    src/twicc/pending_sessions_watcher.py \
    src/twicc/core/services/session_update.py \
  && echo OK
```
Expected: `OK`.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
TWICC_DATA_DIR=$PWD uv run twicc update-session --help
```
Expected: a list of sub-commands including `hide` and `unhide` next to `archive` / `unarchive`.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add \
    src/twicc/cli/update_session/hidden_command.py \
    src/twicc/cli/update_session/__init__.py \
    src/twicc/pending_sessions_watcher.py \
    src/twicc/core/services/session_update.py
git commit -m "$(cat <<'EOF'
feat(cli): twicc update-session hide / unhide

Two sub-commands mirroring archive / unarchive: each drops a payload
with kind="update_hidden" carrying a hidden bool. The pending-sessions
watcher routes them to a new glue service
update_session_hidden_from_payload, which delegates to
session_visibility.hide_session / unhide_session.

hide pre-checks the hidden invariants (permission_mode whitelist +
question_widget) against the current row and rejects if violated; the
user is told to switch settings first. unhide has no preconditions
beyond type=SESSION.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: `update-session settings` — enforce hidden invariants when target is hidden

**Files:**
- Modify: `src/twicc/cli/update_session/settings_command.py` (the Typer command)
- Modify: `src/twicc/core/services/session_update.py` (the server-side handler)

When the user runs `twicc update-session <ID> settings --permission-mode default` on a session that's currently `hidden=True`, we must reject — otherwise the session ends up in an inconsistent state (hidden + interactive mode = silent broken state because no UI can render the approval prompt).

- [ ] **Step 1: CLI side — pass the target's hidden state to the validator**

In `settings_command.py`, after the session lookup (`lookup_session`) and after the settings merge (preset + overrides + enforce-consistency), call:

```python
if resolved.hidden:
    from twicc.cli._session_request.validation import (
        validate_hidden_constraints,
    )
    hidden_errors = validate_hidden_constraints(
        resolved.provider, effective_settings, hidden=True,
    )
    if hidden_errors:
        emit_validation_errors(hidden_errors, json_output=json_output)
        raise typer.Exit(1)
```

> **Note:** check whether `lookup_session()` returns a tuple that exposes `hidden`. If not, either extend `SessionLookupResult` (preferable — `hidden` is generally useful to surface to the user) or add a short DB read here.

- [ ] **Step 2: Server side — same defence in `update_session_settings_from_payload`**

In `session_update.py`, find `update_session_settings_from_payload`. After the existing settings resolution (preset merge + enforce-consistency), but before writing to DB, add:

```python
if session.hidden:
    from twicc.cli._session_request.validation import validate_hidden_constraints
    hidden_errors = validate_hidden_constraints(
        session.provider, resolved_settings, hidden=True,
    )
    if hidden_errors:
        return UpdateSettingsResult(False, None, None, None, [
            UpdateSettingsError(e.field, e.code, e.message)
            for e in hidden_errors
        ])
```

Replace `UpdateSettingsResult` / `UpdateSettingsError` with the in-file equivalents. The intent: invariants enforced on both sides.

- [ ] **Step 3: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile \
    src/twicc/cli/update_session/settings_command.py \
    src/twicc/core/services/session_update.py \
  && echo OK
```
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add \
    src/twicc/cli/update_session/settings_command.py \
    src/twicc/core/services/session_update.py
git commit -m "$(cat <<'EOF'
feat(cli): enforce hidden invariants on update-session settings

When the target session is currently hidden, updating settings cannot
break the hidden-invariants: permission_mode must stay in the
non-interactive whitelist, and (Claude Code) question_widget must not
become True. Both rules enforced on the CLI side (pre-write) and again
on the server side from the payload (defence in depth — the drop-file
is a trust boundary).

If the user wants to relax those, they must unhide the session first.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `serialize_session` — expose `hidden` and `spawned_by`

**Files:**
- Modify: `src/twicc/core/serializers.py` (around line 99 — the return dict end)

- [ ] **Step 1: Append the two fields to the returned dict**

Just before the closing `}` of the dict literal in `serialize_session` (line 99), add:

```python
        # Hidden + spawned_by (cf. hidden-sessions design spec). The
        # frontend never sees a hidden session via REST (filtered server
        # side); the CLI uses these fields when it explicitly opts into
        # hidden listings via --include-hidden / --only-hidden.
        "hidden": session.hidden,
        "spawned_by": session.spawned_by_id,
```

- [ ] **Step 2: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile src/twicc/core/serializers.py && echo OK
```
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/core/serializers.py
git commit -m "$(cat <<'EOF'
feat(serializer): expose hidden and spawned_by in serialize_session

Both fields are surfaced verbatim. The frontend never sees a hidden
session via REST (filtered server-side in a later task), so for it
the boolean is always False; the CLI uses both fields when invoking
listings with --include-hidden / --only-hidden / --spawned-by.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: REST endpoint filters — `hidden=False` everywhere

**Files:**
- Modify: `src/twicc/views.py` (multiple call sites; addresses listed below)

The REST surface is the user's window. Hidden sessions never appear there. Pattern: add `.filter(hidden=False)` to every session-listing query and 404 on direct lookups when the row is hidden. No opt-in HTTP parameter.

Cf. spec §6.1 for the exhaustive table. The line numbers below match the spec — verify they still line up with the current file before editing (the file evolves; the spec was written one day before this plan).

- [ ] **Step 1: `_get_sessions_page` — line ~81**

Add `.filter(hidden=False)` to the base QuerySet used by `_get_sessions_page` (used by `GET /api/sessions/` and `GET /api/projects/<id>/sessions/`). Look for the existing `.filter(type=SessionType.SESSION, created_at__isnull=False, user_message_count__gt=0)` chain and append `hidden=False`.

- [ ] **Step 2: `_resolve_session_or_404` — line ~461**

After the existing lookup, add a check:
```python
if session.hidden:
    raise Http404(...)  # match the existing 404 raising style
```

This protects `GET /api/sessions/<id>/`, `GET /api/projects/<id>/sessions/<id>/`, and any internal helper that calls it.

- [ ] **Step 3: `bulk_archive_sessions` — line ~721**

The bulk-archive QuerySet already excludes `archived=True` and `pinned__isnull=False`. Append `hidden=False`:

```python
Session.objects.filter(
    type=SessionType.SESSION,
    user_message_count__gt=0,
    created_at__isnull=False,
    archived=False,
    pinned__isnull=True,
    hidden=False,  # NEW
    ...
)
```

- [ ] **Step 4: Search post-enrichment — line ~2122**

The `GET /api/search/` view enriches the Tantivy hits with DB rows. Add `.filter(hidden=False)` to that enrichment query — defence in depth, the Tantivy filter (cf. Task 2 step 4) is the primary guard.

- [ ] **Step 5: HTTP-triggered `session_updated` broadcasts — lines ~601-612**

The HTTP PATCH handlers that broadcast `session_updated` (archive/rename/pin) must skip the broadcast when the session is hidden. Wrap the existing call:

```python
if not session.hidden:
    # existing broadcast
```

Rationale: a hidden session must not surface even mid-flight via a PATCH.

- [ ] **Step 6: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile src/twicc/views.py && echo OK
TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings
```
Expected: `OK` then `System check identified no issues`.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/views.py
git commit -m "$(cat <<'EOF'
feat(views): filter hidden=False on every session-listing endpoint

- _get_sessions_page: adds hidden=False to the base QuerySet powering
  GET /api/sessions/ and GET /api/projects/<id>/sessions/.
- _resolve_session_or_404: 404s when the row is hidden, protecting
  GET /api/sessions/<id>/ and GET /api/projects/<id>/sessions/<id>/.
- bulk_archive_sessions: hidden=False added next to the existing
  archived/pinned exclusions.
- /api/search/ post-enrichment: extra hidden=False filter as defence
  in depth on top of the Tantivy filter.
- HTTP PATCH handlers (archive/rename/pin) skip the
  session_updated broadcast when the row is hidden — a hidden session
  must not surface mid-flight either.

No opt-in HTTP parameter: hidden sessions are CLI-only by design.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: WS broadcast guards + new `session_removed` type + `active_processes` filter

**Files:**
- Modify: `src/twicc/providers/sessions_watcher.py`
- Modify: `src/twicc/providers/claude_code/sessions_watcher.py` (if provider-specific emitters exist)
- Modify: `src/twicc/asgi.py`

This task installs `if session.hidden: return` guards at every `session_updated` / `session_items_added` emission point, registers the new `session_removed` event type in the WS consumer, and filters the `active_processes` payload at connect time.

- [ ] **Step 1: Generic watcher guards**

Open `src/twicc/providers/sessions_watcher.py`. Locate every call to `group_send` (or the equivalent helper) that emits `session_updated` or `session_items_added`. Reference lines from the spec §6.2:
- `:568` — file-watcher session_updated on new session
- `:585` — session_items_added
- `:610` — subagent cost change session_updated
- `:692` — stale-recovery session_updated

For each, prepend:
```python
if session.hidden:
    return  # hidden sessions never broadcast (cf. hidden-sessions design)
```

If a single function emits both kinds, hoist the check to the top.

When indexing items (look for the call site around `:402` — `_index_new_items_for_search` or equivalent), pass the new args explicitly:
```python
search.index_document(
    ...,
    archived=session.archived,
    hidden=session.hidden,
    spawned_by_id=session.spawned_by_id,
)
```

Then locate where the watcher creates a brand-new `Session` row (the `_create_session_in_db` helper around `:360-379`). Pop the pending attributes and apply them:

```python
from twicc.pending_session_attributes import pop_pending_session_attributes

pending = pop_pending_session_attributes(session_id)
session_kwargs.update(
    hidden=pending.hidden if pending else False,
    spawned_by_id=pending.spawned_by_id if pending else None,
)
session = Session.objects.create(**session_kwargs)
```

> **Note:** the exact name `session_kwargs` is illustrative — adapt to the local variable names. The intent is: after the existing kwarg dict is built but before `Session.objects.create(...)` runs, layer in `hidden` and `spawned_by_id` from the pending stash.

- [ ] **Step 2: Claude Code-specific watcher (if any)**

If `src/twicc/providers/claude_code/sessions_watcher.py` exists and emits its own `session_updated` (cf. spec §6.2 line `:146` reference), apply the same guard there.

- [ ] **Step 3: `asgi.py` WS consumer guards**

In `src/twicc/asgi.py`, the WS consumer emits `session_updated` from several call sites:
- `:779` — send_message settings update
- `:1543` — session_viewed
- `:1601` — mark_session_read_state

Prepend the `if session.hidden: return` guard at each.

- [ ] **Step 4: New `session_removed` event handler**

In the same WS consumer (`asgi.py`), add a method that handles the new group event:

```python
async def session_removed(self, event):
    """Forward a session_removed event to the client.

    Emitted by the hide flip in session_visibility.hide_session. The
    payload is a small {type, session_id} dict — the client drops the
    matching session from its store on receipt.
    """
    await self.send_json({
        "type": "session_removed",
        "session_id": event["session_id"],
    })
```

Channels routes events by `type` field → method name; the method name `session_removed` matches the `type: "session_removed"` the service sends.

- [ ] **Step 5: Filter `active_processes` at WS connect**

In `asgi.py`, around lines 395-415, the connect handler builds the `active_processes` payload via:
```python
processes = ...  # iterable of AgentInfo
serialized = [serialize_agent_info(p) for p in processes]
# ... then sent as {"type": "active_processes", "messages": serialized}
```

Replace the list comprehension with a filtered version that drops AgentInfo entries whose `session_id` corresponds to a hidden session. Fetch the hidden IDs once (cheap query):

```python
from twicc.core.models import Session
hidden_ids = await sync_to_async(lambda: set(
    Session.objects.filter(hidden=True).values_list("id", flat=True)
))()
serialized = [
    serialize_agent_info(p) for p in processes
    if p.session_id not in hidden_ids
]
```

Adapt `p.session_id` to the actual `AgentInfo` attribute holding the session id (look at `serialize_agent_info` in `src/twicc/agent/__init__.py` to confirm).

- [ ] **Step 6: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile \
    src/twicc/providers/sessions_watcher.py \
    src/twicc/asgi.py \
  && echo OK
# Only py_compile the Claude Code watcher if it actually exists:
test -f src/twicc/providers/claude_code/sessions_watcher.py && \
    python -m py_compile src/twicc/providers/claude_code/sessions_watcher.py
echo "all OK"
```
Expected: `OK` lines.

```bash
TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings
```
Expected: no issues.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add \
    src/twicc/providers/sessions_watcher.py \
    src/twicc/providers/claude_code/sessions_watcher.py \
    src/twicc/asgi.py
git commit -m "$(cat <<'EOF'
feat(ws): guard session_* broadcasts on hidden + new session_removed

- sessions_watcher: every session_updated and session_items_added
  emission gets an `if session.hidden: return` guard. The watcher pops
  pending_session_attributes when creating a new row, applying hidden
  and spawned_by_id at row-creation time. Live indexing forwards
  session.hidden and session.spawned_by_id to search.index_document.
- claude_code/sessions_watcher (if present): same guards.
- asgi.py: same guards on session_updated emissions from the WS
  consumer (send_message settings update, session_viewed,
  mark_session_read_state).
- New `session_removed` consumer method that forwards the
  {type, session_id} payload emitted by session_visibility.hide_session
  to the connected client; the frontend store drops the row.
- active_processes payload at connect time excludes processes whose
  session is hidden.

project_updated stays unfiltered intentionally — cost aggregates
continue to fluctuate for hidden sessions and the frontend must
reflect that.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Counter filters — `PeriodicActivity`, `Project`, `search_indexing_task`, orchestrator logs

**Files:**
- Modify: `src/twicc/core/models.py` (`PeriodicActivity.recalculate`, around lines 168-194)
- Modify: `src/twicc/projects.py` (`update_project_metadata`, around line 323)
- Modify: `src/twicc/providers/db_writer.py` (`recalc_sessions_count`, around lines 2387-2392)
- Modify: `src/twicc/providers/claude_code/orchestrator.py` (around line 463)
- Modify: `src/twicc/providers/codex/orchestrator.py` (around line 425)

All of these counters EXCLUDE hidden. Costs are NEVER filtered. Verify each touchpoint individually.

- [ ] **Step 1: `PeriodicActivity.recalculate`**

In `core/models.py:140`, the `recalculate` method computes three values. Edit two of them, leave the third (cost) untouched:

```python
# user_message_count: only from type=SESSION, non-hidden sessions
user_message_count = SessionItem.objects.filter(
    item_project_filter,
    session__provider=provider.value,
    kind=ItemKind.USER_MESSAGE,
    timestamp__gte=date_start,
    timestamp__lt=date_end,
    session__type=SessionType.SESSION,
    session__hidden=False,  # NEW
).count()

# cost: from ALL session types — INCLUDES hidden (kept that way on
# purpose: costs always count, even for hidden sessions).
# <unchanged>

# session_count: only type=SESSION, non-hidden sessions with at least
# one user message
session_count = Session.objects.filter(
    session_project_filter,
    provider=provider.value,
    type=SessionType.SESSION,
    created_at__gte=date_start,
    created_at__lt=date_end,
    user_message_count__gt=0,
    hidden=False,  # NEW
).count()
```

- [ ] **Step 2: `Project.sessions_count` — write path 1**

In `projects.py:323`, `update_project_metadata`'s `Session.objects.filter(...)` for `sessions_count` gets `hidden=False`:

```python
sessions_count = Session.objects.filter(
    project=project,
    type=SessionType.SESSION,
    created_at__isnull=False,
    user_message_count__gt=0,
    hidden=False,  # NEW
).count()
```

- [ ] **Step 3: `Project.sessions_count` — write path 2**

In `db_writer.py:2387-2392`, `recalc_sessions_count` has a matching QuerySet. Add `hidden=False` to it.

- [ ] **Step 4: Orchestrator log counters (Claude + Codex)**

In `providers/claude_code/orchestrator.py:463` and `providers/codex/orchestrator.py:425`, the post-sync log counters look like:

```python
count = Session.objects.filter(
    provider=Provider.CLAUDE_CODE.value,
    stale=False,
    type=SessionType.SESSION,
).count()
```

Add `hidden=False`:

```python
count = Session.objects.filter(
    provider=...,
    stale=False,
    type=SessionType.SESSION,
    hidden=False,  # NEW — accurate user-facing log count
).count()
```

Same for the Codex orchestrator.

- [ ] **Step 5: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile \
    src/twicc/core/models.py \
    src/twicc/projects.py \
    src/twicc/providers/db_writer.py \
    src/twicc/providers/claude_code/orchestrator.py \
    src/twicc/providers/codex/orchestrator.py \
  && echo OK
TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings
```

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add \
    src/twicc/core/models.py \
    src/twicc/projects.py \
    src/twicc/providers/db_writer.py \
    src/twicc/providers/claude_code/orchestrator.py \
    src/twicc/providers/codex/orchestrator.py
git commit -m "$(cat <<'EOF'
feat(counters): exclude hidden sessions from every session counter

Counters that EXCLUDE hidden:
- PeriodicActivity.recalculate: session_count and user_message_count
  (cost is unchanged — kept inclusive).
- Project.sessions_count: both write paths
  (projects.update_project_metadata + db_writer.recalc_sessions_count).
- Orchestrator post-sync log counters for both providers.

Costs continue to flow through unchanged:
- PeriodicActivity.cost (sums every SessionItem.cost)
- Project.total_cost

Pass-through points downstream (home_data view, ActivityDashboard,
ProjectCard, etc.) become correct automatically once the source-of-
truth columns are filtered.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: FTS live indexing + reindex on flip (final wiring)

**Files:**
- Modify: `src/twicc/providers/sessions_watcher.py` — verify the `index_document` call from Task 14 passes `hidden=session.hidden, spawned_by_id=session.spawned_by_id`
- Modify: `src/twicc/search.py` — `reindex_session` no-op safety (verify it doesn't early-return on hidden anymore)

This task is small — most of the FTS work landed in Task 2 (schema) and Task 14 (live indexing). What's left is to verify the wiring and document the deliberate "always-index" stance.

- [ ] **Step 1: Verify `reindex_session` indexes hidden sessions**

Open `search.py:reindex_session` (around line 267). The current code has no hidden-based early-return (this is a new feature). Confirm by grepping:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
grep -n "hidden" src/twicc/search.py
```

Expected: matches in the schema builder (`hidden` field added in Task 2), in `index_document` signature (also from Task 2), and in `reindex_session`'s call site (forwarding `session.hidden` — also from Task 2). **NO** `if session.hidden: return` early-return should appear anywhere.

If a future draft accidentally introduces one (e.g. someone misreads the spec §8.3), remove it. The intent: hidden sessions ARE indexed, so their owning agents can search within them via `twicc search --spawned-by self --only-hidden`.

- [ ] **Step 2: Confirm Task 14 wiring**

`grep -n "index_document" src/twicc/providers/sessions_watcher.py` and verify the call sites pass `hidden=session.hidden, spawned_by_id=session.spawned_by_id`. If Task 14 was sloppy on that point, fix it now.

- [ ] **Step 3: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile src/twicc/search.py src/twicc/providers/sessions_watcher.py && echo OK
```

- [ ] **Step 4: Commit (if any changes)**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
# Only if there are actual changes from this task:
git add src/twicc/search.py src/twicc/providers/sessions_watcher.py
git commit -m "$(cat <<'EOF'
chore(search): finalize hidden / spawned_by wiring in indexer + reindex

reindex_session no longer early-returns on hidden=True; both visible
and hidden sessions are indexed alike. Live indexing in
sessions_watcher passes hidden and spawned_by_id explicitly. The
default search filter (hidden=False) suppresses hidden docs from REST
results; CLI opt-in flags expose them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If both files are already correct from Tasks 2 / 14, skip the commit and move on to Task 17.

---

## Task 17: CLI listings filters — `twicc sessions`, `twicc processes`, `twicc search`

**Files:**
- Modify: `src/twicc/cli/sessions.py`
- Modify: `src/twicc/cli/processes.py`
- Modify: `src/twicc/cli/search.py`

The three listings share the same UX: `--include-hidden`, `--only-hidden`, and `--spawned-by <ID | self>`. Code is mostly mechanical — wire the flags into the existing QuerySet / Tantivy call.

- [ ] **Step 1: Shared helper for `--spawned-by self` resolution**

Add to `src/twicc/cli/_session_request/whoami.py` (the file from Task 4):

```python
def resolve_spawned_by_filter(value: str | None) -> str | None:
    """Translate a ``--spawned-by`` CLI value into a session_id filter.

    - ``None``  → ``None`` (no filter)
    - ``"self"`` → resolve via whoami; raise if no session in ancestry
    - any other string → use it verbatim as a session_id
    """
    if value is None:
        return None
    if value == "self":
        session = resolve_current_session()
        if session is None:
            raise RuntimeError(
                "--spawned-by self: no TwiCC session found in PID ancestry. "
                "This flag is only meaningful from inside an active session.",
            )
        return session.id
    return value
```

(Sanity-check that file: `python -m py_compile src/twicc/cli/_session_request/whoami.py`.)

- [ ] **Step 2: `twicc sessions`**

In `cli/sessions.py`, add three Typer options to the command:

```python
include_hidden: bool = typer.Option(False, "--include-hidden", help="Include hidden sessions in the listing."),
only_hidden: bool = typer.Option(False, "--only-hidden", help="Show ONLY hidden sessions (mutually exclusive with --include-hidden)."),
spawned_by: str | None = typer.Option(None, "--spawned-by", help="Filter to sessions spawned by the given session_id, or 'self' for the current session."),
```

Right after argument parsing, enforce mutual exclusion:

```python
if include_hidden and only_hidden:
    typer.echo("Error: --include-hidden and --only-hidden are mutually exclusive.", err=True)
    raise typer.Exit(2)

from twicc.cli._session_request.whoami import resolve_spawned_by_filter
try:
    spawned_by_id = resolve_spawned_by_filter(spawned_by)
except RuntimeError as e:
    typer.echo(str(e), err=True)
    raise typer.Exit(1)
```

Wherever the base `Session.objects.filter(...)` QuerySet is built (it likely lives in this file or imports a helper), apply:

```python
if only_hidden:
    qs = qs.filter(hidden=True)
elif not include_hidden:
    qs = qs.filter(hidden=False)
if spawned_by_id is not None:
    qs = qs.filter(spawned_by_id=spawned_by_id)
```

- [ ] **Step 3: `twicc processes`**

`cli/processes.py:50-94` already enriches `ProcessRun` rows with Session data. Add the same three Typer options. After the enrichment query, drop processes whose session is hidden (or non-hidden, per the flag):

```python
# After enriching with Session metadata:
filtered = []
for row in rows:
    if row["hidden"] is True and not (include_hidden or only_hidden):
        continue
    if only_hidden and row["hidden"] is not True:
        continue
    if spawned_by_id is not None and row["spawned_by"] != spawned_by_id:
        continue
    filtered.append(row)
rows = filtered
```

The exact field names depend on the enrichment shape — adjust.

- [ ] **Step 4: `twicc search`**

`cli/search.py` calls `search.search(...)` and prints results. Add the same three Typer options and forward them to the search function (the kwargs were defined in Task 2 step 4):

```python
results = search.search(
    query,
    ...,
    include_hidden=include_hidden,
    only_hidden=only_hidden,
    spawned_by=spawned_by_id,
)
```

Also enforce mutual exclusion of `--include-hidden` / `--only-hidden` (same snippet as Task 17 step 2).

- [ ] **Step 5: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
python -m py_compile \
    src/twicc/cli/sessions.py \
    src/twicc/cli/processes.py \
    src/twicc/cli/search.py \
    src/twicc/cli/_session_request/whoami.py \
  && echo OK
```

Verify the flags appear in each `--help`:
```bash
TWICC_DATA_DIR=$PWD uv run twicc sessions --help 2>&1 | grep -E "include-hidden|only-hidden|spawned-by"
TWICC_DATA_DIR=$PWD uv run twicc processes --help 2>&1 | grep -E "include-hidden|only-hidden|spawned-by"
TWICC_DATA_DIR=$PWD uv run twicc search --help 2>&1 | grep -E "include-hidden|only-hidden|spawned-by"
```
Expected: three matching options per command.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add \
    src/twicc/cli/sessions.py \
    src/twicc/cli/processes.py \
    src/twicc/cli/search.py \
    src/twicc/cli/_session_request/whoami.py
git commit -m "$(cat <<'EOF'
feat(cli): listings opt-in for hidden + filter by spawned_by

Three new flags on twicc sessions, twicc processes, and twicc search:

- --include-hidden: include hidden sessions alongside visible ones.
- --only-hidden:    show ONLY hidden sessions (mutually exclusive
                    with --include-hidden).
- --spawned-by ID:  filter to sessions spawned by the given session_id.
                    The literal `self` resolves via whoami (PID
                    ancestry) — useful for agents listing their own
                    children without knowing their own session_id.

resolve_spawned_by_filter is added to the whoami helper module so it
stays shared between the three commands.

The default behaviour is unchanged: no flag → no hidden sessions, no
spawned_by filter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Frontend — defensive guards + `session_removed` handler

**Files:**
- Modify: `frontend/src/stores/data.js` (5 getters + the new event payload)
- Modify: `frontend/src/composables/useWebSocket.js` (route the new `session_removed` event)

- [ ] **Step 1: Defensive guards in 5 getters**

In `frontend/src/stores/data.js`, find each of the following getters and prepend `if (session.hidden) continue;` (or the JS-iteration equivalent) inside the loop:

| Getter | Approx. line | Loop pattern |
|---|---|---|
| `getProjectUnreadCount` | ~553 | `for (const session of sessions)` (or similar) |
| `getGlobalUnreadCount` | ~624 | same |
| `getProjectSessions` | ~446-476 | filter / for-loop |
| `getAllSessions` | ~477-500 | same |
| `getNextMruPath` | ~3166 | filter / loop |

For getter functions that use `.filter()`, change:
```js
sessions.filter(s => !s.archived && ...)
```
to
```js
sessions.filter(s => !s.hidden && !s.archived && ...)
```

For functions that use `for...of`, add `if (session.hidden) continue;` near the top of the loop body.

- [ ] **Step 2: Handle `session_removed` payload in the store**

Add a new action / mutation:

```js
// In the actions / mutations section:
removeSession(sessionId) {
    delete this.sessions[sessionId];
    // No further bookkeeping: every dependent getter is reactive on
    // this.sessions; the sidebar / counters reflect the change
    // automatically.
},
```

- [ ] **Step 3: Route the WS event in `useWebSocket.js`**

In `frontend/src/composables/useWebSocket.js`, locate the `switch (data.type)` (or equivalent dispatcher). Add:

```js
case 'session_removed':
    store.removeSession(data.session_id);
    break;
```

Match the existing dispatch style of the file (some projects use object lookup tables instead of switch).

- [ ] **Step 4: Sanity check**

The frontend isn't lint-checked, but a quick syntax sanity check via `node --check`:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
node --check frontend/src/stores/data.js && \
node --check frontend/src/composables/useWebSocket.js && echo OK
```
Expected: `OK`.

> If `node --check` doesn't accept ESM imports, you can skip it — Vite will surface real issues at dev-server startup. The user will tell you when restart hits a problem.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add \
    frontend/src/stores/data.js \
    frontend/src/composables/useWebSocket.js
git commit -m "$(cat <<'EOF'
feat(frontend): defensive hidden guards + session_removed handler

- Five getters in stores/data.js get `if (session.hidden) continue` /
  equivalent filter: getProjectUnreadCount, getGlobalUnreadCount,
  getProjectSessions, getAllSessions, getNextMruPath. The REST API
  filters hidden sessions out server-side so they should never enter
  the store; the guards are belt-and-suspenders against a future leak.
- New store action removeSession(sessionId) that drops a session from
  the dict — reactive dependents (sidebar, counters) update naturally.
- useWebSocket.js routes the new `session_removed` event to the action.

No new UI: hidden sessions are CLI-only by design. The frontend simply
respects the server's invariant.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: Skill markdowns — update docs across CLI surface

**Files:**
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md`

Open each file, add the relevant snippets, save. Commit at the end.

- [ ] **Step 1: `twicc-create-session/SKILL.md`**

In the description section (front-matter) and the "Options" or "Flags" section, add:

```markdown
- `--hidden`: Create the session as hidden — invisible from every list,
  search, broadcast, and counter shown to the user, while still counted
  in cost aggregates. Requires `permission_mode` to be non-interactive
  (`bypassPermissions` or `dontAsk` for Claude Code; `yolo` or `strict`
  for Codex) and `question_widget=False`. The CLI rejects the request
  if those constraints aren't met.
```

Add a new "Related commands" section near the end (if not already there):

```markdown
## Related commands

To list the sessions you've created from this session:
- `twicc sessions --spawned-by self` — list filiation
- `twicc search "<query>" --spawned-by self` — search inside your filiation

Both are silent: they use the same PID-ancestry resolver as
`twicc whoami` to determine your session_id.
```

**Do NOT mention `spawned_by` as an option** — the CLI does not expose it on `create-session`. The skill should stay silent on that mechanism.

- [ ] **Step 2: `twicc-update-session/SKILL.md`**

Add to the sub-commands list:

```markdown
- `hide`: Toggle the session to `hidden=True`. Pre-conditions: the
  session's `permission_mode` must already be in the non-interactive
  whitelist (`bypassPermissions`/`dontAsk` for Claude Code;
  `yolo`/`strict` for Codex), and (Claude Code) `question_widget` must
  be `False`. If those aren't satisfied, change them first via
  `settings --permission-mode ... [--no-question-widget]`. The session
  is removed from every user list / counter / broadcast (clients
  receive a `session_removed` event); costs continue to be aggregated.

- `unhide`: Toggle the session back to `hidden=False`. No
  preconditions. The session reappears everywhere; counters are
  re-synced.
```

- [ ] **Step 3: `twicc-sessions/SKILL.md`**

Add to the options list:

```markdown
- `--include-hidden`: Include hidden sessions in the listing.
- `--only-hidden`: Show ONLY hidden sessions (mutually exclusive with
  `--include-hidden`).
- `--spawned-by <ID|self>`: Filter to sessions spawned by the given
  session. The literal `self` resolves via `twicc whoami` (PID
  ancestry) — useful for an agent listing its own children without
  knowing its own session_id.
```

- [ ] **Step 4: `twicc-processes/SKILL.md`**

Same three options. Adapt the wording to the processes context (it's the same flag semantics).

- [ ] **Step 5: `twicc-search/SKILL.md`**

Same three options, plus note that hidden sessions are still indexed (so `--include-hidden` and `--only-hidden` find content inside them).

- [ ] **Step 6: Sanity check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
ls src/twicc/agent/plugin/twicc/skills/twicc-{create-session,update-session,sessions,processes,search}/SKILL.md
```
Expected: 5 files listed. Verify each was edited (a quick `git diff --stat` is fine).

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
git add src/twicc/agent/plugin/twicc/skills/
git commit -m "$(cat <<'EOF'
docs(skills): document --hidden, hide/unhide, and listing opt-ins

Updates to five existing skills:
- twicc-create-session: --hidden flag + non-interactive constraints,
  plus a "Related commands" section steering agents toward
  `--spawned-by self` for filiation listings. Stays silent on the
  spawned_by mechanism itself — it is auto-detected, not user-facing.
- twicc-update-session: hide / unhide sub-commands + preconditions.
- twicc-sessions, twicc-processes, twicc-search: --include-hidden,
  --only-hidden, --spawned-by <ID|self>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 20: Final end-to-end manual verification

**Files:** none (verification only)

This is the integration test. Run it once everything else is in place. The user must restart the dev servers between tasks 1-19 and this task (the migration must be applied, and the backend reloaded for the new code).

- [ ] **Step 1: Remind the user to apply migration and restart**

> "All 19 implementation tasks are committed. Please:
> 1. Apply the migration: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run python -m django migrate --settings=twicc.settings`
> 2. Restart dev servers: `cd <worktree> && uv run ./devctl.py restart`
> 3. Let me know when both are done so I can run the end-to-end checks."

Wait for confirmation before proceeding.

- [ ] **Step 2: `twicc whoami` from a plain shell — should fail cleanly**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
TWICC_DATA_DIR=$PWD uv run twicc whoami; echo "exit=$?"
```
Expected: a clear "No TwiCC session found" message, `exit=1`.

- [ ] **Step 3: `twicc create-session --hidden` happy path (Claude Code, bypassPermissions)**

Pick a project_id you don't care about polluting (or use a fresh test directory). Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-hidden-sessions
TWICC_DATA_DIR=$PWD uv run twicc create-session \
    "test hidden session" \
    --project <PROJECT_ID> \
    --provider claude_code \
    --permission-mode bypassPermissions \
    --no-question-widget \
    --hidden \
    --json
```
Expected: JSON with `status: created`, a `session_id`. Capture the ID.

- [ ] **Step 4: Verify it's hidden in the UI**

Tell the user to refresh the frontend at the local URL. The new session should NOT appear in the sidebar, in `All projects`, in the project detail. The project's `sessions_count` should NOT have incremented. The project's `total_cost` may have moved (if the agent has emitted anything).

- [ ] **Step 5: Verify it shows up only with `--include-hidden` / `--only-hidden`**

```bash
TWICC_DATA_DIR=$PWD uv run twicc sessions --include-hidden | grep <SESSION_ID>; echo "include exit=$?"
TWICC_DATA_DIR=$PWD uv run twicc sessions | grep <SESSION_ID>; echo "default exit=$?"
TWICC_DATA_DIR=$PWD uv run twicc sessions --only-hidden | grep <SESSION_ID>; echo "only exit=$?"
```
Expected: matched in `include` and `only`, NOT matched in `default`.

- [ ] **Step 6: Verify `--hidden` rejection on interactive mode**

```bash
TWICC_DATA_DIR=$PWD uv run twicc create-session "rejected" --project <ID> --provider claude_code --permission-mode default --hidden 2>&1 | head -5
echo "exit=$?"
```
Expected: a validation error mentioning `permission_mode` whitelist, exit non-zero.

- [ ] **Step 7: Verify the `hide` sub-command requires invariants**

Create a normal session (not hidden, `permission_mode=default`), then attempt to hide it:

```bash
TWICC_DATA_DIR=$PWD uv run twicc update-session <NORMAL_SESSION_ID> hide 2>&1 | head -10
echo "exit=$?"
```
Expected: rejection mentioning the permission_mode invariant; exit non-zero. After switching the session's permission_mode to `bypassPermissions` via `settings --permission-mode bypassPermissions`, the same `hide` should now succeed and the session should disappear from the UI.

- [ ] **Step 8: Verify `unhide` brings it back**

```bash
TWICC_DATA_DIR=$PWD uv run twicc update-session <SESSION_ID> unhide
```
Expected: `status: updated`. Tell the user to refresh; the session should be back in the sidebar (and in `getProjectSessions`).

- [ ] **Step 9: Verify `whoami` from inside a hidden session (manual + advanced)**

Send a message to a hidden session asking it to run `twicc whoami` in a Bash tool, and verify the result matches the session's own ID. This is the most integration-sensitive verification — only run it once the rest passes.

```bash
TWICC_DATA_DIR=$PWD uv run twicc send-message <HIDDEN_SESSION_ID> "Run twicc whoami in a Bash tool and tell me what ID it returns."
```
After the agent responds, verify the returned ID matches `<HIDDEN_SESSION_ID>`.

- [ ] **Step 10: Verify `--spawned-by self`**

From the same hidden session as above, send:

```bash
TWICC_DATA_DIR=$PWD uv run twicc send-message <HIDDEN_SESSION_ID> "Run twicc sessions --spawned-by self --include-hidden --json in a Bash tool and tell me how many sessions are listed."
```

If the hidden session spawned a child session via `twicc create-session` (perhaps in a previous test, perhaps via instruction to do so), `--spawned-by self` should list that child. Otherwise, expect zero results (which is correct — the session hasn't spawned anything yet).

- [ ] **Step 11: Verify FTS includes hidden when opt-in**

```bash
TWICC_DATA_DIR=$PWD uv run twicc search "test hidden session" --include-hidden 2>&1 | head -10
TWICC_DATA_DIR=$PWD uv run twicc search "test hidden session" 2>&1 | head -10
```
Expected: the first listing finds the hidden session (we created it with that title); the second does not.

- [ ] **Step 12: Sanity-check project counters**

Inspect the project in the UI: `sessions_count` should reflect only visible sessions; `total_cost` should reflect all sessions including the hidden ones. Compare with the DB if needed:

```bash
TWICC_DATA_DIR=$PWD uv run python -m django shell --settings=twicc.settings <<'PY'
from twicc.core.models import Project, Session
from django.db.models import Sum
p = Project.objects.get(pk="<PROJECT_ID>")
print("project.sessions_count:", p.sessions_count)
print("visible sessions count:", Session.objects.filter(project=p, type="session", hidden=False, user_message_count__gt=0).count())
print("hidden sessions count:", Session.objects.filter(project=p, type="session", hidden=True).count())
print("project.total_cost:", p.total_cost)
print("sum total_cost incl hidden:", Session.objects.filter(project=p, type="session").aggregate(total=Sum("total_cost"))["total"])
PY
```
Expected: `sessions_count` matches `visible sessions count`; `total_cost` matches the sum across both.

- [ ] **Step 13: Final report to user**

Summarise what worked and what didn't. If everything passes, the feature is ready to merge. If something fails, drill into the broken task and fix.

---

## End of plan

If everything in Task 20 passes, the feature is implemented and verified. Remaining work outside this plan: merge / PR creation (when the user decides), `CHANGELOG.md` entry (when the user decides). Both are reserved-user operations and **not** in this plan.

**Total commit count expected:** ~19 (one per task, except Task 16 which may be a no-op if Tasks 2 / 14 were tight). **Branch:** `feature/hidden-sessions`.
