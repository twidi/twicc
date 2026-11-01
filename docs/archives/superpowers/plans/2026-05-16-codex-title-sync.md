# Codex Title Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronise the Codex thread name (state DB, via SDK) into `Session.title` so externally-named Codex sessions show their title in TwiCC, and fix the missing `pending_title` flush on the Codex agent manager so titles set on draft sessions actually persist.

**Architecture:** Codex state DB stays the source of truth for the title; `Session.title` becomes a local mirror. Three add-only changes : (1) read `Thread.name` in bulk at boot from the orchestrator, (2) read it again per-session in the watcher when a brand-new session is first materialised, (3) push the in-memory `pending_title` to Codex via `set_name()` on the first `ASSISTANT_TURN` transition (mirrors the Claude Code agent manager). No DB migration, no new column, no `protect_title` machinery on the Codex side.

**Tech Stack:** Python 3.13, Django 6 ASGI + Channels, `codex_app_server` (vendored — `AsyncCodex`, `thread_list(use_state_db_only=True)`, `thread_read`, `thread.set_name`), `asgiref.sync_to_async` / `async_to_sync`.

**Spec:** `docs/superpowers/specs/2026-05-16-codex-title-sync-design.md`. Read §0 and §2 of the spec before starting — the invariant "Codex state DB wins" and the non-goals (no flag column, no periodic poll, no serializer change) drive every task below.

**Project policy reminder (from `CLAUDE.md`):** *"The only shortcuts we allow: no tests and no linting."* — therefore steps below skip automated tests and rely on manual verification. The verification scenarios listed at the end of each task are mandatory : run them before committing.

**Worktree:** `feature/multi-provider` at `/home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider`. Every shell command in this plan assumes that working directory ; the `TWICC_DATA_DIR` is auto-injected by `devctl.py` for backend processes, but Python one-liners must export it manually (see `CLAUDE.md` "Running Python / Django code in a worktree without devctl").

---

## File Structure

| Action | Path | Purpose |
|---|---|---|
| **Modify** | `src/twicc/providers/codex/titles.py` | Add two async helpers: `read_title_from_codex(session_id)` and `bulk_sync_titles_from_codex()`. Both spawn an ephemeral `AsyncCodex`, mirroring the existing `rename_thread_via_sdk()` pattern. |
| **Modify** | `src/twicc/providers/sessions_watcher.py` | Add an optional `title: str \| None` slot to `ParsedSessionFile`. Add a hook `async def _fetch_initial_title(parsed) -> str \| None` on `BaseSessionsWatcher` returning `None`, called once before `create_session_sync` so subclasses can fill it. Use `parsed.title` (or the hook return) in `create_session_sync` when present. |
| **Modify** | `src/twicc/providers/codex/sessions_watcher.py` | Override `_fetch_initial_title` to call `read_title_from_codex(parsed.session_id)`. |
| **Modify** | `src/twicc/providers/codex/orchestrator.py` | Call `bulk_sync_titles_from_codex()` once in `_dependency_orchestrator` between `initial_sync_done.wait()` and `start_background_compute_task`. Wrap in try/except so a Codex SDK failure doesn't block the boot. |
| **Modify** | `src/twicc/providers/codex/agent/manager.py` | Override `_on_state_change` (currently inherited from `BaseAgentManager`) to flush `pending_title` on `ASSISTANT_TURN`, mirroring `providers/claude_code/agent/manager.py:481-495`. No `protect_title` (Codex doesn't need it). |
| **Reference only** | `src/twicc/pending_titles.py` | No change — used as-is from the new Codex flush. |
| **Reference only** | `src/twicc/core/serializers.py` | No change — the `get_pending_title or title` overlay is intentionally kept (anti-flash). |

**Naming convention:** all helpers in `providers/codex/titles.py` already follow `<verb>_<noun>` ; keep that style.

---

## Tasks

### Task 1: Add Codex title-read helpers

**Files:**
- Modify: `src/twicc/providers/codex/titles.py`

Add two async helpers next to `rename_thread_via_sdk`. Both spawn a short-lived `AsyncCodex` (same pattern as the existing function — single initialize, single RPC, then close). Errors are caught at the helper level so callers never crash : `read_title_from_codex` returns `None` on error, `bulk_sync_titles_from_codex` returns an empty dict on error.

- [ ] **Step 1: Read the existing `rename_thread_via_sdk` for the spawn pattern**

Re-read `src/twicc/providers/codex/titles.py:22-45` so the new helpers match the surrounding style (module docstring tone, the `bundled_bin` / `AppServerConfig` boilerplate, the comment style).

- [ ] **Step 2: Add `read_title_from_codex`**

Insert this after `rename_thread_via_sdk` in `src/twicc/providers/codex/titles.py` :

```python
async def read_title_from_codex(thread_id: str) -> str | None:
    """Read the Codex thread's current display name from the state DB.

    Returns ``None`` if the thread has no name set, or on any error
    (logged at WARNING). Used by the watcher on first session
    materialisation to import a title that was set via the Codex CLI.
    """
    bundled_bin = resolve_bundled_binary()
    config = AppServerConfig(codex_bin=str(bundled_bin))
    try:
        async with AsyncCodex(config=config) as codex:
            # ``AsyncCodex`` exposes no top-level ``thread_read``; like
            # ``rename_thread_via_sdk`` we go through ``thread_resume``
            # to get an ``AsyncThread`` handle and call ``.read()`` on
            # it (defined at ``codex_app_server/api.py:649-653``).
            thread = await codex.thread_resume(thread_id)
            response = await thread.read(include_turns=False)
            name = response.thread.name
            return name if name else None
    except Exception as e:
        logger.warning("Codex thread/read failed for %s: %s", thread_id, e)
        return None
```

- [ ] **Step 3: Add `bulk_sync_titles_from_codex`**

Same file, after `read_title_from_codex` :

```python
async def bulk_sync_titles_from_codex() -> dict[str, str]:
    """Read every Codex thread's display name via state-DB-only list.

    Returns a ``{thread_id: name}`` mapping (only entries with a non-empty
    name are included). Returns an empty dict on error (logged at WARNING).
    Pagination is exhaustive — there is no upper bound on the number of
    threads, but the call is cheap because ``use_state_db_only=True``
    skips JSONL rollout scanning.
    """
    bundled_bin = resolve_bundled_binary()
    config = AppServerConfig(codex_bin=str(bundled_bin))
    titles: dict[str, str] = {}
    try:
        async with AsyncCodex(config=config) as codex:
            cursor: str | None = None
            while True:
                page = await codex.thread_list(
                    use_state_db_only=True,
                    cursor=cursor,
                    limit=100,
                )
                for thread in page.data:
                    if thread.name:
                        titles[thread.id] = thread.name
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
    except Exception as e:
        logger.warning("Codex bulk thread/list failed: %s", e)
    return titles
```

- [ ] **Step 4: Verify the imports compile AND the SDK surface matches**

Run from the worktree root :

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.titles import read_title_from_codex, bulk_sync_titles_from_codex
from codex_app_server import AsyncCodex
from codex_app_server.api import AsyncThread
# Catch API drift early — these are what the helpers actually call.
assert hasattr(AsyncCodex, 'thread_resume'), 'AsyncCodex missing thread_resume'
assert hasattr(AsyncCodex, 'thread_list'), 'AsyncCodex missing thread_list'
assert hasattr(AsyncThread, 'read'), 'AsyncThread missing read'
print('OK')
"
```

Expected output : `OK`. Any `ImportError`, `SyntaxError`, `NameError`, or `AssertionError` means the snippet or the SDK contract is broken — fix before continuing.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/titles.py
git commit -m "$(cat <<'EOF'
feat(codex): add read_title_from_codex + bulk_sync_titles_from_codex helpers

Two ephemeral-AsyncCodex helpers mirroring the existing
rename_thread_via_sdk pattern. Read-only against the Codex state DB
(use_state_db_only=True) — no JSONL rollout scanning. Used by the
watcher (single thread) and the orchestrator (bulk paginated) to
sync Codex thread names into Session.title.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Plumb the title through the watcher

**Files:**
- Modify: `src/twicc/providers/sessions_watcher.py:60-83` (add slot), and `:299-343` + around `:476` (use the slot)
- Modify: `src/twicc/providers/codex/sessions_watcher.py` (override the hook)

The base watcher currently has no path to receive a per-session title from a subclass. We add :
1. An optional `title: str | None` slot on `ParsedSessionFile`.
2. An async hook `_fetch_initial_title(parsed) -> str | None` on the base watcher that returns `None` by default ; subclasses override.
3. A call to that hook in `sync_and_broadcast` *only when we know we're about to create a new session* (the `if session is None:` branch around `:464`), with the result passed through to `create_session_sync`.

This keeps the SDK fetch out of the modify-event path entirely (no overhead on every JSONL append).

- [ ] **Step 1: Add the `title` slot to `ParsedSessionFile`**

In `src/twicc/providers/sessions_watcher.py`, modify the class to include a `title` slot (default `None`). After editing, the class should look like :

```python
class ParsedSessionFile:
    """Identity of a session file as recognized by a provider.

    Built by :meth:`BaseSessionsWatcher.parse_session_file`, which may
    inspect both the path and (optionally) the file content. Codex for
    instance reads the first JSONL line to recover ``project_id`` from
    the session's ``cwd``, since the file path itself does not encode it.

    ``title`` is an optional initial title supplied by the provider's
    parse step (e.g. read from Codex's state DB) — used only when the
    session is created for the first time, ignored on later events.
    """
    __slots__ = ('project_id', 'session_id', 'type', 'parent_session_id', 'file_path', 'title')

    def __init__(
        self,
        project_id: str,
        session_id: str,
        type: SessionType,
        file_path: str,
        parent_session_id: str | None = None,
        title: str | None = None,
    ):
        self.project_id = project_id
        self.session_id = session_id
        self.type = type
        self.parent_session_id = parent_session_id
        self.file_path = file_path
        self.title = title
```

- [ ] **Step 2: Use `parsed.title` in `create_session_sync`**

In the same file, modify the regular-session branch of `create_session_sync` (around `:343`) to add `title=parsed.title` into `kwargs` when non-empty :

```python
        kwargs: dict = dict(
            id=parsed.session_id,
            project=project,
            provider=compute.provider,
            file_path=parsed.file_path,
            compute_version=compute.compute_version,
        )
        if parsed.title:
            kwargs["title"] = parsed.title
        if agent_settings is not None:
            for field, value in agent_settings._asdict().items():
                if value is not None:
                    kwargs[field] = value
        return Session.objects.create(**kwargs)
```

(Subagents — the `:319-331` branch — do not receive a title : keep that branch unchanged.)

- [ ] **Step 3: Add `_fetch_initial_title` hook on the base watcher**

Same file, somewhere near the other override-points (e.g. next to `parse_session_file` and `get_compute` — search for `def get_compute(` to anchor). Add :

```python
    async def _fetch_initial_title(self, parsed: ParsedSessionFile) -> str | None:
        """Optional hook : fetch an initial title for a newly-discovered session.

        Called by :meth:`sync_and_broadcast` exactly once per new session
        (never on subsequent JSONL modify events). Subclasses that have
        an out-of-band title source (e.g. the Codex state DB) override
        this. Default returns ``None`` — no out-of-band title.
        """
        return None
```

- [ ] **Step 4: Call the hook in `sync_and_broadcast` before creating the session**

In `src/twicc/providers/sessions_watcher.py` around `:464-478` (the `if session is None:` branch), call `_fetch_initial_title` *before* `create_session_sync` and stash the result on `parsed.title`. The result is used by `create_session_sync` (step 2). Code becomes :

```python
        if session is None:
            # New file - check if it has content before creating
            has_content = await check_file_has_content_async(path)
            if not has_content:
                # Empty file (0 lines) - ignore completely
                return

            # Provider-specific initial title (e.g. read from Codex state DB).
            # Mutate parsed in place — it's local to this call.
            if parsed.title is None:
                parsed.title = await self._fetch_initial_title(parsed)

            # Create session (regular or subagent)
            # Pop any pending settings set by the WS handler for new sessions
            from twicc.pending_agent_settings import pop_pending_agent_settings

            pending_agent_settings = pop_pending_agent_settings(parsed.session_id)
            session = await sync_to_async(self.create_session_sync)(
                parsed, project, parent_session, pending_agent_settings,
            )
```

The `if parsed.title is None` guard means a subclass that already filled `title` in `parse_session_file` would short-circuit the hook (no provider does this today, but the guard is cheap and self-documenting).

- [ ] **Step 5: Override the hook in `CodexSessionsWatcher`**

In `src/twicc/providers/codex/sessions_watcher.py`, add the override (it can sit just before or after `get_compute` at `:92-93`). Subagent sessions should NOT trigger the fetch — Codex subagents inherit the parent's title concept differently and the spec scope is top-level sessions only.

```python
    async def _fetch_initial_title(self, parsed: ParsedSessionFile) -> str | None:
        # Top-level sessions only — subagents don't carry a user-facing name.
        if parsed.type != SessionType.SESSION:
            return None
        from .titles import read_title_from_codex
        return await read_title_from_codex(parsed.session_id)
```

Make sure `SessionType` is already imported at the top of the file (it is — line 28 imports `SessionType` from `twicc.core.models`).

- [ ] **Step 6: Verify the imports + class compile**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher; from twicc.providers.sessions_watcher import ParsedSessionFile; p = ParsedSessionFile('proj', 'sess', None, 'file.jsonl'); print('title slot default:', p.title); print('OK')"
```

Expected : `title slot default: None` then `OK`. (The `None` for `type` is fine for this smoke test — we're not exercising real logic.)

- [ ] **Step 7: Manual end-to-end verification of the watcher path**

Pre-conditions :
1. The user has a Codex session in `~/.codex/sessions/` that was renamed via the Codex CLI (its `Thread.name` in the state DB is non-null) AND that is NOT yet known to TwiCC's DB.
2. TwiCC backend is **stopped** (the user will start it explicitly — never start the backend yourself per `CLAUDE.md` "Operations Reserved to User").

The verification has to be performed by the user. Provide them with this checklist :

```
1. Stop TwiCC backend if running (uv run ./devctl.py stop back)
2. Rename a session via the Codex CLI you have not yet opened in TwiCC,
   so its state DB has Thread.name set and TwiCC's DB doesn't have a
   Session row for it yet.
3. Start TwiCC backend (uv run ./devctl.py start back)
4. Open the TwiCC frontend → that session should appear with the title
   from the Codex CLI rename.
5. Check the backend log (uv run ./devctl.py logs back) for any
   "Codex thread/read failed" warnings — there should be none.
```

If the title is missing or a warning appears, do NOT commit — fix and re-verify.

- [ ] **Step 8: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/sessions_watcher.py src/twicc/providers/codex/sessions_watcher.py
git commit -m "$(cat <<'EOF'
feat(codex): import Thread.name into Session.title on first watch

Adds an optional title slot to ParsedSessionFile, plus an async
_fetch_initial_title hook on BaseSessionsWatcher invoked once per
new session before create_session_sync. CodexSessionsWatcher
overrides it to read Thread.name via the state DB, so sessions
renamed via the Codex CLI appear with their title the first time
TwiCC discovers them. Hook is not called on modify events — zero
overhead on the steady-state watcher path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Bulk sync at boot in the Codex orchestrator

**Files:**
- Modify: `src/twicc/providers/codex/orchestrator.py:272-283`

We add a one-shot bulk title sync between `initial_sync_done.wait()` and `start_background_compute_task`. Sequential, not parallel — the bulk call is cheap (one paginated SDK list against the state DB) and we want any updates broadcast before the watcher starts emitting modify events that might overlap.

- [ ] **Step 1: Add a private async method `_sync_titles_at_boot`**

In `src/twicc/providers/codex/orchestrator.py`, add a new method on `CodexOrchestrator` (class defined at `src/twicc/providers/codex/orchestrator.py:71`). Place it just before `_dependency_orchestrator` so it's visually adjacent to its only call site :

```python
    async def _sync_titles_at_boot(self) -> None:
        """Import Codex Thread.name into Session.title for every known thread.

        Runs once between the initial JSONL sync and the background compute.
        For each Codex Session whose title differs from Thread.name (and
        Thread.name is non-empty), we update the row and broadcast a
        session_updated event so clients connected during the boot window
        see the new title without a full reload.
        """
        from twicc.providers.codex.titles import bulk_sync_titles_from_codex
        from twicc.providers.sessions_watcher import broadcast_message
        from twicc.core.serializers import serialize_session

        titles = await bulk_sync_titles_from_codex()
        if not titles:
            logger.info("Codex title sync at boot: no titles to import")
            return

        # Pull only Codex sessions whose id is in the fetched map and whose
        # current title differs. bulk_update keeps it one SQL round-trip.
        def _apply() -> list[Session]:
            sessions = list(
                Session.objects.filter(
                    provider=Provider.CODEX,
                    id__in=list(titles.keys()),
                )
            )
            changed: list[Session] = []
            for s in sessions:
                new_title = titles.get(s.id)
                if new_title and s.title != new_title:
                    s.title = new_title
                    changed.append(s)
            if changed:
                Session.objects.bulk_update(changed, ["title"])
            return changed

        changed = await sync_to_async(_apply)()
        logger.info(
            "Codex title sync at boot: %d/%d titles imported",
            len(changed), len(titles),
        )

        if changed:
            channel_layer = get_channel_layer()
            for s in changed:
                # refresh_from_db is cheap (single row, already in mem buffer),
                # but we already have the up-to-date title on the instance.
                await broadcast_message(channel_layer, {
                    "type": "session_updated",
                    "session": serialize_session(s),
                })
```

Required imports at the top of `orchestrator.py` :
- `from asgiref.sync import sync_to_async` — check if already present, add if not.
- `from channels.layers import get_channel_layer` — check, add if not.
- `from twicc.core.models import Session, Provider` — check, add if not.

(Don't blindly add them — grep first, then add only what's missing.)

- [ ] **Step 2: Wire `_sync_titles_at_boot` into `_dependency_orchestrator`**

Modify `_dependency_orchestrator` around `src/twicc/providers/codex/orchestrator.py:272-283` so it now reads :

```python
    async def _dependency_orchestrator(self) -> None:
        """[Existing docstring stays — extend it with one sentence about
        the title sync.]"""
        await self.initial_sync_done.wait()

        # Pull thread names from the Codex state DB *before* the compute
        # task runs so newly-imported titles appear at the same time as
        # the rest of the initial UI state.
        try:
            await self._sync_titles_at_boot()
        except Exception as e:
            logger.warning("Codex title sync at boot failed: %s", e)

        self._compute_ctx = ComputeContext(
            provider=self.provider,
            compute_version=settings.CODEX_COMPUTE_VERSION,
            compute_factory="twicc.providers.codex.compute:get_compute",
        )
        # ... rest unchanged
```

The try/except is belt-and-suspenders : the helper itself catches its SDK errors, but if we blow up on the ORM side or on the broadcast we still don't want to abort the boot.

- [ ] **Step 3: Verify the orchestrator compiles**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "from twicc.providers.codex.orchestrator import CodexOrchestrator; assert hasattr(CodexOrchestrator, '_sync_titles_at_boot'); print('OK')"
```

Expected : `OK`.

- [ ] **Step 4: Manual end-to-end verification of the bulk sync**

Pre-conditions :
1. TwiCC has at least one Codex session in its DB whose `Session.title` is empty AND whose `Thread.name` in Codex's state DB is set. The simplest setup is :
   - rename a known Codex session via the Codex CLI ;
   - then in TwiCC, manually clear that session's title in the DB (one-off SQL) so it's empty ;
   - or, easier : delete that Session row from TwiCC's DB and let initial sync recreate it without a title, then trigger the boot sync.

Ask the user to provide such a setup or to run the verification on real data.

User-side checklist :
```
1. Stop TwiCC backend.
2. (Setup as above — at least one Codex Session with empty title in DB
    and non-empty Thread.name in Codex state DB.)
3. Start TwiCC backend.
4. Watch the log : you should see
   "Codex title sync at boot: N/M titles imported"
   with N >= 1.
5. Open the TwiCC frontend without refreshing : if a client was already
   connected during boot, the affected session should show its title
   appear within a couple of seconds (session_updated broadcast).
6. Refresh the frontend : the title is also there from the initial fetch.
```

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/orchestrator.py
git commit -m "$(cat <<'EOF'
feat(codex): bulk-import Thread.name into Session.title at boot

After the initial JSONL sync and before the compute task, paginate
thread_list(use_state_db_only=True) against the Codex state DB and
update every Session.title that differs from the imported name.
Broadcast a session_updated event per change so clients connected
during the boot window see the title without a full reload. Failure
is logged at WARNING and never blocks the boot.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Flush `pending_title` on the Codex agent manager

**Files:**
- Modify: `src/twicc/providers/codex/agent/manager.py`

The base `_on_state_change` lives at `src/twicc/agent/base_manager.py:319`. Claude Code overrides it at `src/twicc/providers/claude_code/agent/manager.py:412` to add several transition-specific actions, including the `ASSISTANT_TURN` flush (`:481-495`). Codex does not currently override `_on_state_change` at all — its manager class inherits the base unchanged, which is why the pending title never reaches `set_name()`.

We override `_on_state_change` on the Codex manager to add **only** the pending-title flush (no settings flush, no protected-title rewrite — Codex doesn't have those concerns). The base implementation handles `DEAD` already ; we call `super()._on_state_change(agent)` to keep that working.

- [ ] **Step 1: Re-read the Claude Code version to lock in the pattern**

Open `src/twicc/providers/claude_code/agent/manager.py:412-510` and read it through once. Pay attention to :
- Where `super()._on_state_change()` is called (if at all) ;
- The exact pop / `protect_title` sequence on `ASSISTANT_TURN` ;
- The error handling shape.

We won't copy verbatim — Codex needs less — but the structure should feel parallel.

- [ ] **Step 2: Re-read the base `_on_state_change`**

Open `src/twicc/agent/base_manager.py:319` and read through. Note what it does on each state (especially `DEAD` — we must preserve it). Note whether it broadcasts.

- [ ] **Step 3: Add the override on `CodexAgentManager`**

In `src/twicc/providers/codex/agent/manager.py`, add the method on `CodexAgentManager` (class defined at `src/twicc/providers/codex/agent/manager.py:35`). Placement : alongside other instance methods on the class ; if the class is short, append near the end.

```python
    async def _on_state_change(self, agent: BaseAgent) -> None:
        # Keep all base behaviour (DEAD cleanup, broadcast, etc.).
        await super()._on_state_change(agent)

        state = agent.state
        if state == AgentState.ASSISTANT_TURN:
            from twicc.pending_titles import get_pending_title, pop_pending_title
            from twicc.providers.codex.titles import rename_thread_via_sdk

            pending = get_pending_title(agent.session_id)
            if pending:
                try:
                    # Push to Codex state DB. Use the standalone helper rather
                    # than agent._thread so we don't reach into private state.
                    await rename_thread_via_sdk(agent.session_id, pending)
                    pop_pending_title(agent.session_id)
                    # Mirror into Session.title immediately.
                    await sync_to_async(
                        Session.objects.filter(id=agent.session_id).update
                    )(title=pending)
                except Exception as e:
                    logger.error(
                        "Codex pending title flush failed for %s: %s",
                        agent.session_id, e,
                    )
```

Required imports at the top of the file :
- `from asgiref.sync import sync_to_async` — **missing**, add.
- `from twicc.core.models import Session` — check (likely missing in this manager file), add if needed.
- `BaseAgent` and `AgentState` are already imported via `from twicc.agent import AgentState, BaseAgent, BaseAgentManager` at `src/twicc/providers/codex/agent/manager.py:24` — **do not duplicate**.

(Grep first ; only add what's missing.)

- [ ] **Step 4: Justify the standalone helper choice in a short comment in the method**

The override above uses `rename_thread_via_sdk` (spawn ephemeral `AsyncCodex`) rather than the agent's already-open `_thread`. Two reasons, worth one inline comment :
1. `agent._thread` is the SDK transport for the *active turn* ; we're in a state-change callback where ownership rules around the streamed turn consumer (`_active_turn_consumer` guard, see `codex_app_server.client`) get awkward — see the existing comment in `titles.py:25-32` for the matching rationale on rename.
2. Symmetry : the UI rename path and the pending flush path now both go through the same helper, which is easier to reason about.

The comment in the code can be a single line ; the long explanation lives in this plan and in the spec.

- [ ] **Step 5: Verify the manager class compiles**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "from twicc.providers.codex.agent.manager import CodexAgentManager; assert '_on_state_change' in CodexAgentManager.__dict__, 'override not registered'; print('OK')"
```

Expected : `OK`. The `assert` confirms the new method actually lives on the subclass (not inherited from the base), which is what we want.

- [ ] **Step 6: Manual end-to-end verification of the pending flush**

User-side checklist :
```
1. Stop TwiCC backend (uv run ./devctl.py stop back).
2. Start TwiCC backend (uv run ./devctl.py start back).
3. In the TwiCC frontend, start composing a new Codex session.
4. Type a message and use the "Suggest title" action — accept the
   suggested title.
5. Send the message — the session is created in Codex, first turn
   starts.
6. Once the first ASSISTANT_TURN appears, the title should remain
   visible (no flash to empty or to a different value).
7. Stop TwiCC backend.
8. Inspect the DB to confirm Session.title is set :
   sqlite3 db/data.sqlite "select id, title from twicc_core_session
       where provider='codex' order by created_at desc limit 5;"
9. Restart TwiCC backend.
10. The session is still showing its title after restart — this is
    the regression we're fixing (today, the title disappears).
11. Inspect the backend log for "Codex pending title flush failed" —
    there should be none.
```

If any step fails, do NOT commit — debug, fix, re-verify.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/manager.py
git commit -m "$(cat <<'EOF'
fix(codex): flush pending_title to state DB on first ASSISTANT_TURN

Override _on_state_change on CodexAgentManager (was inheriting base
unchanged) to consume pending_titles on the STARTING -> ASSISTANT_TURN
transition, mirroring providers/claude_code/agent/manager.py:481-495.
Uses the standalone rename_thread_via_sdk helper rather than the
agent's active SDK transport, to avoid colliding with the active
turn consumer guard. Mirrors the title into Session.title in the
same step.

Without this fix, a title suggested+accepted on a draft Codex session
was only kept in the in-memory pending_titles dict — visible in the
UI thanks to the serializer overlay, but lost on backend restart.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: End-to-end integration verification

This task has no code changes — only manual verification scenarios that the user runs to confirm all three flows work together. It must pass before considering the feature done.

**Files:**
- (none)

- [ ] **Step 1: Verify scenarios A, B, C, D together**

Provide the user with this combined checklist. They run each scenario sequentially against a fresh backend restart.

```
Scenario A — externally-renamed session is imported on boot
  - Setup : a Codex session whose Session.title is empty in TwiCC's DB
    and whose Thread.name in the Codex state DB is non-empty (rename
    via the Codex CLI on a session not yet opened in TwiCC, or delete
    the TwiCC Session row to force re-import).
  - Action : restart TwiCC backend.
  - Expected : log line "Codex title sync at boot: N/M titles imported"
    with N >= 1 ; the session shows its Codex CLI title in the UI.

Scenario B — externally-renamed *new* session arrives while TwiCC is running
  - Setup : TwiCC running, no special prep.
  - Action : in the Codex CLI, create a brand-new session, send one
    message, then rename it (so Thread.name is non-empty), then leave
    it on disk.
  - Expected : the watcher picks the new JSONL file ; the session
    appears in the TwiCC UI with its Codex CLI title (the
    _fetch_initial_title hook ran on creation).
  - No "Codex thread/read failed" warning in the backend log.

Scenario C — pending title on a draft TwiCC session survives restart
  - Setup : TwiCC running.
  - Action : start composing a new Codex session in TwiCC. Use
    "Suggest title", accept the suggestion, send the first message.
    Wait for the first ASSISTANT_TURN. Then restart TwiCC backend.
  - Expected : after restart, the session's title is still there
    (Session.title persisted via the flush + Codex state DB persisted
    via set_name).
  - SQL sanity : Session.title in the DB matches what's on screen.

Scenario D — TwiCC rename + CLI rename → CLI wins on next boot
  - Setup : a Codex session both ends know about, with TwiCC.title="A"
    and Codex Thread.name="A" (set via TwiCC rename).
  - Action : rename in the Codex CLI to "B". Restart TwiCC backend.
  - Expected : TwiCC now shows "B" (state DB wins on sync down). This
    is the intentional behaviour per the spec §0.3 corollary.
```

- [ ] **Step 2: Read the log for any unexpected error**

```
uv run ./devctl.py logs back --lines=400 | grep -iE "(codex|title|pending)" | head -50
```

Look for stack traces or repeated WARN/ERROR. The expected shape is a single INFO line for the boot sync and zero warnings. If errors are present, fix the root cause before declaring done — do NOT commit a workaround.

- [ ] **Step 3: Stage a single "release-notes" entry (not a commit yet)**

The feature is shipping behind no flag (per spec). A line in `CHANGELOG.md` (under `[Unreleased]`) describing the change is appropriate. Skip if the project's release process is unclear or if the user prefers to handle that themselves.

Suggested wording (in `CHANGELOG.md`, under `[Unreleased]` → "Added" or "Fixed" depending on the existing section structure) :

> - **Codex titles**: TwiCC now imports the thread name from the Codex state DB at boot and when discovering new sessions, and persists user-accepted suggested titles on draft Codex sessions through the first turn (previously lost on restart).

Ask the user before editing `CHANGELOG.md` — they may prefer to do the wording themselves.

- [ ] **Step 4: Final commit (only the CHANGELOG, if edited)**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): codex title sync at boot + pending flush

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Skip this step if no `CHANGELOG.md` edit was made.)

---

## Out-of-scope reminders (do not implement here)

These come straight from spec §0.2 / §5. If a step in this plan tempts you to do any of these, stop and re-read the spec before going further :

- **No periodic poll** for Codex CLI renames against already-known sessions.
- **No new `Session.title_user_set` / `title_source` column.** No migration.
- **No `protect_title` machinery** on the Codex side (titles aren't in the JSONL).
- **No change to `core/serializers.py`** — the `get_pending_title or title` overlay is intentional (anti-flash).
- **No "Sync titles from Codex" UI button.**

---

## Rollback

Every task ends on a commit. If a scenario in Task 5 fails after Task 4 and the cause isn't immediately clear, `git revert` the offending task's commit (one commit per task) and re-investigate. No DB migration was added, so rollback is just `git revert` + restart backend — no data cleanup needed.
