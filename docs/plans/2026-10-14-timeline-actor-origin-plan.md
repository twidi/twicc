# Timeline Actor and Origin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every timeline event caused by a request records the path (`via`), the caller identity (`actor_ref`) and a display-name snapshot (`actor_name`) next to `actor`, and the `actor` value follows the real caller (`user`, `agent`, `external`) instead of a fixed `user`.

**Architecture:** One `ContextVar` (`request_origin`) holds a `RequestOrigin` NamedTuple. Every entry point sets it (WebSocket frame, REST middleware, RPC view, MCP call, MCP batch child, drop-file watcher). The recording service resolves an event's four origin columns from, in order: a captured full `origin=`, an explicit `actor=`, the carrier, the default `user` / `ui`. Deferred writers (pending title, pending creation attributes, process DEAD hook) never read the carrier: they use an origin captured earlier. Automatic starts and stops run inside an explicit `system` marker scope.

**Tech Stack:** Python 3.13, Django 6 (async middleware, ORM migration), asyncio `contextvars`, pytest + pytest-django, SQLite. No new dependency, no frontend change.

**Spec:** `docs/plans/2026-10-14-timeline-actor-origin-design.md` (reviewed, committed as `e8a263c1`). The implemented system it extends: `docs/plans/2026-10-13-session-timeline-design.md`, `docs/plans/2026-10-14-timeline-incremental-backfill-design.md`. Do not edit those documents.

---

## Working conventions (every task)

- **Worktree.** All work happens in `/home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline` (branch `feature/session-timeline`). Prefix EVERY Bash command with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && `. In this plan the prefix is written `cd $WT &&` with `WT=/home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline` (expand it literally, the shell does not persist variables).
- **Tests.** `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest <paths> -q`. Never plain `pytest`, never `uv run --active`, never `uv pip`.
- **Lint.** `cd $WT && uvx ruff check <changed files>`. The branch stays at zero findings on the lines it writes, including `C408` (use `{}` and `[]` literals, not `dict(...)` or `list()` calls). A finding on a line this task did not write is not this task's job.
- **Commits.** One commit per task, conventional subject, a descriptive body, an explicit `git add <files>` list (never a directory, never `-A`), and this trailer as the last line of the message: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never commit `docs/plans/2026-10-14-session-timeline-handoff.md` (untracked on purpose). No CHANGELOG entry.
- **Forbidden.** `migrate`, restarting servers, `npm`, editing historical design/plan docs, `Project.objects.create` outside tests.
- **Language.** All code, comments, docs and test names in English.

## Global Constraints

These are the spec's project-wide requirements. Every task obeys them.

- **Columns** on `TimelineEvent`, all nullable, no default, no index, next to `actor`: `via` = `CharField(max_length=20)`, `actor_ref` = `CharField(max_length=255)`, `actor_name` = `CharField(max_length=80)`. `actor` keeps its four values `user`, `agent`, `system`, `external`.
- **`via` values**, exactly: `ui`, `cli`, `rpc`, `mcp_internal`, `mcp_external`. NULL for an automatic event and for a not-request-caused event. An invalid `via` is stored as NULL (O35); the event is still written with its resolved `actor`, `actor_ref`, `actor_name`.
- **Truncation, never rejection:** `actor_ref` > 255 and `actor_name` > 80 are cut to width and the event is written (O24).
- **Mapping (spec section 4), final (O3):**

  | Entry path | `actor` | `via` | `actor_ref` | `actor_name` |
  |---|---|---|---|---|
  | Web UI (WS, REST) and the default | `user` | `ui` | NULL | NULL |
  | CLI by a human | `user` | `cli` | NULL | NULL |
  | CLI by an agent | `agent` | `cli` | session id | NULL |
  | Internal MCP, session token | `agent` | `mcp_internal` | session id | NULL |
  | Internal MCP endpoint, API token | `user` | `mcp_internal` | token id | NULL |
  | External MCP | `external` | `mcp_external` | connection id | connection name at event time |
  | RPC with token (incl. CLI `--remote`) | `user` | `rpc` | token id | NULL |
  | RPC without token record | `user` | `rpc` | NULL | NULL |
  | Automatic (system marker) | `system` | NULL | NULL | NULL |

- **Precedence** (spec section 5): a captured full `origin` keeps all four fields; an explicit `actor` gives NULL origin fields; otherwise the carrier; otherwise the default `user` / `ui`. The default applies only to a request-caused write that reaches the writer with no origin at all, never to an automatic event.
- **Recording never raises.** A failure to read or resolve the origin falls back to the default (`user` / `ui`) for a request-caused write only. An event marked `system` or written by a system writer keeps `system`. A failure is logged.
- **Migration:** exactly one new migration, number `0158` (after `0157_timeline_event_session_kind_index`), three `AddField` operations. The plan creates the file with `makemigrations`; it NEVER applies it. The user runs `migrate` on their own instance.
- **Order** is by `id`; `session_id`, `project`, `dedupe_key` and every payload rule are unchanged. An `ended` row never inherits the origin fields of its `started` row (O7).
- **Backfill** and boot reconciliation rows keep NULL origin fields with actor `system`.
- **No consumer** reads the new columns in this scope (no API, UI, CLI or share change).
- **No per-event `data.via`.** The reserved `data["via"]` key is removed from the registry (decision D1 below).

## Decisions left to the plan

| # | Decision | Why (one line) |
|---|---|---|
| D1 | `validate_data` **rejects** a `data["via"]` key (it falls under "unknown payload key"). | The key leaves the contract; a leftover writer must show up as a logged, dropped event in tests, not pass silently. |
| D2 | The drop-payload key is the reserved top-level key `"_origin"` holding `{"actor", "via", "actor_ref", "actor_name"}`; `execute_drop_payload` pops it before calling the handler. | One name, one place, and no service ever sees it, so no handler needs to tolerate an unknown key. |
| D3 | `kill_reason` becomes a property of a `StopOriginMixin` (`src/twicc/agent/stop_origin.py`): the setter captures the origin when the reason is deliberate and clears it otherwise. | "Paired at every assignment" is enforced by construction, including future assignment sites. The one stale re-assignment (`self.kill_reason or "cli-exit"` in the hybrid agent) is fixed at its source. |
| D4 | The UI origin for REST is held by `RequestOriginMiddleware` (in `auth/middleware.py`), skipping `/rpc/`; for WS by a wrapper of `receive_json`. | A middleware covers every current and future `/api/` view with one tested seam. |
| D5 | The API token id reaches the RPC view as `request.rpc_token_id`, and the MCP handlers as `scope["twicc_mcp_token_id"]`. | The auth layers already hold the record; they only publish its id. |
| D6 | MCP origin is derived in `_call_tool` (`_origin_of_call`) from `external_caller`, the session token and the scope token id, and carried to batch children through `_Run.origin`. | Reuses the `external_caller` propagation already relied on, and children (created outside the request task) get it explicitly. |
| D7 | Pending stores move whole entries on a Codex re-key (`rekey_pending_title`, `rekey_pending_session_attributes`) instead of forwarding fields one by one. | A field added later can no longer be forgotten by the re-key. |
| D8 | `record_session_created(session, *, origin, creator=None)`: `creator` is the captured request origin; `origin == "twicc"` uses `creator or DEFAULT_ORIGIN`, any other `origin` uses the explicit `system` actor. | Implements spec 6.6 and section 8 in one branch. |
| D9 | `record_event_sync(..., actor=None, origin=None)`: omitting both means "request-caused". Every system writer passes `actor=` explicitly. A guard test greps `src/twicc` for `TimelineActor.USER`. | Keeps the diff small; the guard catches a forgotten system writer or a leftover hard-coded `user`. |
| D10 | The CLI process resolves its caller once (`cli_origin()`, cached per process, `django.setup()` inside a try/except). | "Resolution is cheap": one ancestry walk and one DB read per invocation. |

## Review Focus

Five failure modes the spec implies but no task's ordinary tests would exercise. Each has a named test in the owning task.

1. **Marker leak on failure (T9).** The `system` marker of an inline `apply-settings` restart must restore the request origin even when the restart raises, so the send's other writes stay attributed to the user. Test: `test_marker_is_restored_when_the_inline_restart_raises`.
2. **Stale re-assignment of the stop reason (T8).** A deliberate reason re-assigned from the agent's own task (the old `self.kill_reason or "cli-exit"` in the hybrid liveness loop) would re-capture the starter's stale carrier. Tests: `test_hybrid_liveness_never_reassigns_the_stop_reason` (source guard), `test_a_deliberate_reason_assigned_from_a_stale_context_captures_that_stale_context` (documents why), and fakes without the new attributes must keep working (`test_agents_built_without_init_still_read_a_reason`, `test_hook_fakes_without_a_stop_origin_still_work`).
3. **Drop payload hygiene (T5).** The `_origin` key must never reach a service handler, a forged `system` actor or garbage must degrade to the default, and a stale carrier must not leak into a payload that has no origin. Tests parametrized over every kind of `_KIND_HANDLERS`: `test_every_kind_gets_its_origin_re_established_and_never_sees_the_key`, `test_a_payload_without_origin_runs_unset_even_inside_a_stale_carrier`, `test_a_forged_or_malformed_origin_degrades_to_unset`.
4. **Pending-store whole-entry moves (T7).** A Codex draft re-key must move every field of the pending creation attributes and the title origin; a title re-set must not keep an older origin; the origin must disappear with the title. Tests: `test_rekey_moves_every_field_of_the_pending_attributes`, `test_setting_a_title_without_an_origin_forgets_an_older_origin`, `test_the_pending_title_keeps_its_origin_until_popped`.
5. **Every thread / executor hop between the entry point and the write (T1, T5, T6).** The carrier must survive `asyncio.to_thread`, asgiref `sync_to_async` on the compute executor (hide), `run_under_db_write_lock`'s inner task and `run_coroutine_threadsafe` in backend-mode submit; a plain `threading.Thread` must read nothing. Tests: `test_the_visibility_facts` (T1), `test_backend_mode_submit_carries_the_origin_through_the_loop` (T5), `test_hide_through_the_compute_executor_keeps_the_origin` (T6).

## File Structure

New files:

| Path | Role |
|---|---|
| `src/twicc/request_origin.py` | Pure module: `RequestOrigin`, constants, the `request_origin` ContextVar, scopes, builders per entry path, drop-payload codec, `resolve_event_origin`. No Django import. |
| `src/twicc/agent/stop_origin.py` | `StopOriginMixin`: the `kill_reason` / `stop_origin` pairing (D3). |
| `src/twicc/cli/_drop_request/origin.py` | `cli_origin()`: the CLI process's own origin, resolved once. |
| `src/twicc/core/migrations/0158_timeline_event_origin.py` | Generated by `makemigrations`. Adds the three columns. |
| `tests/test_request_origin.py` | Carrier, scopes, visibility facts, builders, codec, precedence. |
| `tests/test_timeline_origin_columns.py` | Columns, service plumbing (single, batch, fallback, backfill), widths. |
| `tests/test_timeline_origin_ui.py` | WS frame origin, REST middleware. |
| `tests/test_timeline_origin_entry.py` | RPC, internal MCP, external MCP, batch children. |
| `tests/test_timeline_origin_drop.py` | Transport, CLI resolution, watcher re-establishment, end to end drop. |
| `tests/test_timeline_origin_writers.py` | Flags, archive, bulk, settings, hide, permission mode. |
| `tests/test_timeline_origin_titles_creation.py` | Titles, pending stores, re-key, `session.created`. |
| `tests/test_timeline_origin_process.py` | Stop pairing, process hooks, DEAD hook, hybrid switch. |
| `tests/test_timeline_origin_markers.py` | System marker: funnel, apply-settings, cron, adoption. |
| `tests/test_timeline_origin_guard.py` | Source guards (`TimelineActor.USER`, hybrid reassignment). |

Modified files (by task):

| Path | Task |
|---|---|
| `src/twicc/core/models.py` | T2 |
| `src/twicc/core/timeline.py` | T2 |
| `src/twicc/core/services/session_timeline.py` | T2, T7 |
| `src/twicc/auth/middleware.py` | T3, T4 |
| `src/twicc/settings.py` | T3 |
| `src/twicc/asgi.py` | T3 |
| `src/twicc/rpc/views.py` | T4 |
| `src/twicc/mcp/identity.py`, `endpoint.py`, `server.py`, `batch.py` | T4 |
| `src/twicc/cli/_drop_request/transport.py` | T5 |
| `src/twicc/drop_requests_watcher.py` | T5 |
| `src/twicc/core/services/session_update.py`, `session_visibility.py` | T6 |
| `src/twicc/providers/claude_code/ws.py` | T6 |
| `src/twicc/pending_titles.py`, `pending_session_attributes.py` | T7 |
| `src/twicc/core/services/session_creation.py` | T7 |
| `src/twicc/agent/base_manager.py` | T7 (re-key, title flush) |
| `src/twicc/providers/sessions_watcher.py` | T7 |
| `src/twicc/agent/base_agent.py` | T8, T9 |
| `src/twicc/agent/timeline_hooks.py` | T8 |
| `src/twicc/providers/claude_code/agent/hybrid/agent.py` | T8 |
| `src/twicc/providers/claude_code/agent/manager.py` | T9 |
| `src/twicc/providers/claude_code/cron_restart.py` | T9 |
| `tests/timeline_helpers.py` | T1 (mapping, `columns`), T5 (recording service) |
| `tests/test_timeline_model.py` | T2 |
| `tests/test_timeline_bulk_archive.py` | T6 |
| `tests/test_timeline_service.py` | T2 |
| `CLAUDE.md`, `AGENTS.md` | T10 |

Verified facts the tasks rely on (checked against the repo on 2026-10-14):

- `record_event_sync` / `record_events_bulk_sync` / `insert_event_rows` / `record_session_created` / `record_title_change` live in `src/twicc/core/services/session_timeline.py`; the batch insert is built from `TimelineEvent._meta.concrete_fields`, the single path and the fallback build explicit fields.
- `TimelineActor.USER` is hard-coded in: `core/services/session_timeline.py` (flag rows, title), `core/services/session_update.py` (bulk archive, `persist_session_settings` default, archive, pin, mute), `core/services/session_visibility.py`, `providers/claude_code/ws.py`, `agent/timeline_hooks.py`. Task T10 re-greps it.
- `kill_reason` is assigned at: `agent/base_manager.py` (`hard_kill_agent`), `providers/claude_code/agent/agent.py` (`interrupt_or_kill`, `kill`, `auth_required`, `error`), `providers/codex/agent/agent.py` (`interrupt_or_kill`, `error`, `auth_required`), `providers/claude_code/agent/hybrid/agent.py` (`startup-failed`, `cli-exit`, `kill`).
- `asyncio.to_thread`, a task created after the `set()`, and `run_coroutine_threadsafe` called from a `to_thread` worker see the variable; a task created before the `set()` and a plain `threading.Thread` do not (probed with a standalone script; T1 pins it as a test).

---

## Task 1: The request-origin module

**Files:**
- Create: `src/twicc/request_origin.py`
- Create: `tests/test_request_origin.py`
- Modify: `tests/timeline_helpers.py` (shared mapping table)

**Interfaces:**
- Consumes: nothing.
- Produces (all in `twicc.request_origin`, used by every later task):
  - constants `ACTOR_USER`, `ACTOR_AGENT`, `ACTOR_SYSTEM`, `ACTOR_EXTERNAL`; `VIA_UI`, `VIA_CLI`, `VIA_RPC`, `VIA_MCP_INTERNAL`, `VIA_MCP_EXTERNAL`; `VIA_VALUES: frozenset[str]`; `VIA_MAX_LENGTH = 20`, `ACTOR_REF_MAX_LENGTH = 255`, `ACTOR_NAME_MAX_LENGTH = 80`; `ORIGIN_PAYLOAD_KEY = "_origin"`.
  - `class RequestOrigin(NamedTuple)`: `actor: str`, `via: str | None = None`, `actor_ref: str | None = None`, `actor_name: str | None = None`.
  - `UI_ORIGIN`, `DEFAULT_ORIGIN` (same value), `SYSTEM_ORIGIN`.
  - `request_origin: ContextVar[RequestOrigin | None]` (default `None` = unset).
  - `current_origin() -> RequestOrigin | None`; `capture_origin() -> RequestOrigin` (carrier, else `DEFAULT_ORIGIN`, never `None`); `origin_scope(origin: RequestOrigin | None)` (context manager; `None` is an explicit unset); `system_marker()` (context manager, `origin_scope(SYSTEM_ORIGIN)`).
  - builders: `human_cli_origin()`, `agent_cli_origin(session_id)`, `rpc_origin(token_id)`, `mcp_session_origin(session_id)`, `mcp_token_origin(token_id)`, `mcp_external_origin(connection_id, name)`.
  - codec: `origin_to_payload(origin) -> dict`, `origin_from_payload(raw) -> RequestOrigin | None`.
  - `resolve_event_origin(actor: str | None, origin: RequestOrigin | None) -> RequestOrigin`.

- [ ] **Step 1: Add the shared mapping table to the test helpers**

Edit `tests/timeline_helpers.py`: put the `import pytest` line and the `from twicc.request_origin import (...)` block of the snippet below at the TOP of the file with the other imports (a mid-file import is ruff E402), and append the rest (`ORIGIN_MAPPING`, `columns`) at the end; keep the existing functions:

```python
from twicc.request_origin import (
    SYSTEM_ORIGIN, UI_ORIGIN, agent_cli_origin, human_cli_origin, mcp_external_origin, mcp_session_origin,
    mcp_token_origin, rpc_origin,
)

#: The mapping table of the spec (section 4): (origin built by the entry path, expected columns).
ORIGIN_MAPPING = [
    pytest.param(UI_ORIGIN, ("user", "ui", None, None), id="web-ui"),
    pytest.param(human_cli_origin(), ("user", "cli", None, None), id="cli-human"),
    pytest.param(agent_cli_origin("sess-A"), ("agent", "cli", "sess-A", None), id="cli-agent"),
    pytest.param(mcp_session_origin("sess-A"), ("agent", "mcp_internal", "sess-A", None), id="mcp-internal-session"),
    pytest.param(mcp_token_origin("tok_1"), ("user", "mcp_internal", "tok_1", None), id="mcp-internal-token"),
    pytest.param(
        mcp_external_origin("conn-1", "ChatGPT"), ("external", "mcp_external", "conn-1", "ChatGPT"),
        id="mcp-external",
    ),
    pytest.param(rpc_origin("tok_1"), ("user", "rpc", "tok_1", None), id="rpc-token"),
    pytest.param(rpc_origin(None), ("user", "rpc", None, None), id="rpc-no-token"),
    pytest.param(SYSTEM_ORIGIN, ("system", None, None, None), id="automatic"),
]


def columns(row):
    """The four origin columns of an event row, in the order of ``RequestOrigin``."""
    return (row.actor, row.via, row.actor_ref, row.actor_name)
```

Add `import pytest` to the top import block of `tests/timeline_helpers.py`. (`columns` reads `row.via`, which exists only after T2; it is not called before then.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_request_origin.py`:

```python
"""The request-origin carrier (design 2026-10-14-timeline-actor-origin sections 2, 5, 6)."""

import asyncio
import threading

import pytest

from tests.timeline_helpers import ORIGIN_MAPPING
from twicc.request_origin import (
    ACTOR_NAME_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, DEFAULT_ORIGIN, ORIGIN_PAYLOAD_KEY, SYSTEM_ORIGIN, UI_ORIGIN,
    VIA_MAX_LENGTH, VIA_VALUES, RequestOrigin, agent_cli_origin, capture_origin, current_origin,
    mcp_session_origin, origin_from_payload, origin_scope, origin_to_payload, resolve_event_origin, rpc_origin,
    system_marker,
)

AGENT = mcp_session_origin("sess-A")


@pytest.mark.parametrize(("origin", "expected"), ORIGIN_MAPPING)
def test_each_entry_path_builds_its_row_of_the_mapping_table(origin, expected):
    assert tuple(origin) == expected


def test_constants_are_pinned_to_the_spec():
    assert VIA_VALUES == {"ui", "cli", "rpc", "mcp_internal", "mcp_external"}
    assert (VIA_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, ACTOR_NAME_MAX_LENGTH) == (20, 255, 80)
    assert DEFAULT_ORIGIN == UI_ORIGIN == RequestOrigin("user", "ui")
    assert SYSTEM_ORIGIN == RequestOrigin("system")
    assert ORIGIN_PAYLOAD_KEY == "_origin"


def test_an_unset_carrier_reads_none_and_captures_the_default():
    assert current_origin() is None
    assert capture_origin() == DEFAULT_ORIGIN


def test_a_scope_sets_then_restores_and_nests():
    with origin_scope(AGENT):
        assert current_origin() == AGENT
        with origin_scope(UI_ORIGIN):
            assert current_origin() == UI_ORIGIN
        assert current_origin() == AGENT
    assert current_origin() is None


def test_a_none_scope_is_an_explicit_unset_inside_an_outer_origin():
    with origin_scope(AGENT):
        with origin_scope(None):
            assert current_origin() is None
            assert capture_origin() == DEFAULT_ORIGIN
        assert current_origin() == AGENT


def test_a_scope_restores_on_exception():
    with pytest.raises(RuntimeError), origin_scope(AGENT):
        raise RuntimeError("boom")
    assert current_origin() is None


def test_the_visibility_facts():
    """Design section 6.3: what sees the carrier and what does not."""

    async def scenario():
        loop = asyncio.get_running_loop()
        gate = asyncio.Event()

        async def long_lived():
            await gate.wait()
            return current_origin()

        async def read():
            return current_origin()

        def worker():
            return asyncio.run_coroutine_threadsafe(read(), loop).result()

        early = asyncio.create_task(long_lived())  # created before the set(): keeps the old context
        with origin_scope(AGENT):
            late = asyncio.create_task(long_lived())
            gate.set()
            in_thread = await asyncio.to_thread(current_origin)
            in_threadsafe = await asyncio.to_thread(worker)
            plain = []
            thread = threading.Thread(target=lambda: plain.append(current_origin()))
            thread.start()
            thread.join()
            return await early, await late, in_thread, in_threadsafe, plain[0]

    early, late, in_thread, in_threadsafe, plain = asyncio.run(scenario())
    assert (early, plain) == (None, None)
    assert (late, in_thread, in_threadsafe) == (AGENT, AGENT, AGENT)


def test_the_system_marker_is_scoped_and_restores_the_request_origin():
    async def scenario():
        with origin_scope(UI_ORIGIN):
            with system_marker():
                inside = current_origin()
                await asyncio.sleep(0)
            return inside, current_origin()

    assert asyncio.run(scenario()) == (SYSTEM_ORIGIN, UI_ORIGIN)


def test_concurrent_requests_never_mix():
    async def request(origin, gate, seen):
        with origin_scope(origin):
            await gate.wait()
            seen.append((origin, current_origin()))

    async def scenario():
        gate, seen = asyncio.Event(), []
        origins = (AGENT, UI_ORIGIN, rpc_origin("tok"), agent_cli_origin("sess-B"))
        tasks = [asyncio.create_task(request(origin, gate, seen)) for origin in origins]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*tasks)
        return seen

    seen = asyncio.run(scenario())
    assert len(seen) == 4 and all(expected == got for expected, got in seen)


@pytest.mark.parametrize("origin", [pytest.param(o, id=i) for o, i in (
    (UI_ORIGIN, "ui"), (agent_cli_origin("S"), "agent"), (RequestOrigin("external", "mcp_external", "c", "N"), "ext"),
)])
def test_the_payload_codec_round_trips(origin):
    assert origin_from_payload(origin_to_payload(origin)) == origin


@pytest.mark.parametrize("raw", [
    None, "ui", [], {}, {"actor": "system", "via": "ui"}, {"actor": ["user"]}, {"actor": "root"},
    {"actor": "user", "via": 3}, {"actor": "user", "actor_ref": ["x"]}, {"actor": "user", "actor_name": 1.5},
])
def test_the_payload_codec_rejects_garbage_and_forged_system_origins(raw):
    assert origin_from_payload(raw) is None


def test_resolve_prefers_a_captured_origin_then_an_explicit_actor_then_the_carrier():
    captured = RequestOrigin("external", "mcp_external", "c1", "Name")
    with origin_scope(AGENT):
        assert resolve_event_origin("system", captured) == captured  # captured full origin wins
        assert resolve_event_origin("agent", None) == RequestOrigin("agent")  # explicit actor: NULL fields
        assert resolve_event_origin(None, None) == AGENT  # carrier
    assert resolve_event_origin(None, None) == DEFAULT_ORIGIN  # nothing known: the default
    with system_marker():
        assert resolve_event_origin(None, None) == SYSTEM_ORIGIN  # the marker is not "unset"


def test_resolve_falls_back_to_the_default_when_the_carrier_cannot_be_read(monkeypatch):
    def boom():
        raise RuntimeError("carrier unreadable")

    monkeypatch.setattr("twicc.request_origin.capture_origin", boom)
    assert resolve_event_origin(None, None) == DEFAULT_ORIGIN
    assert resolve_event_origin("system", None) == RequestOrigin("system")  # a system writer never falls to the default
```

- [ ] **Step 3: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_request_origin.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'twicc.request_origin'`.

- [ ] **Step 4: Write the module**

Create `src/twicc/request_origin.py`:

```python
"""Who made a request and through which path (design 2026-10-14-timeline-actor-origin).

The origin of a request is held in a ``ContextVar`` set by every entry point (WebSocket frame,
REST middleware, RPC view, MCP call, drop-file watcher) and read by the services that write
timeline events. Pure module: no Django import, so every layer can use it.

Three kinds of value live in the variable:

* ``None``: unset. A request-caused write falls back to :data:`DEFAULT_ORIGIN`.
* a :class:`RequestOrigin` of a caller (``user``, ``agent``, ``external``).
* :data:`SYSTEM_ORIGIN`, the explicit system marker: an automatic step. It is *not* "unset".
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from contextvars import ContextVar
from typing import NamedTuple

logger = logging.getLogger(__name__)

ACTOR_USER = "user"
ACTOR_AGENT = "agent"
ACTOR_SYSTEM = "system"
ACTOR_EXTERNAL = "external"

VIA_UI = "ui"
VIA_CLI = "cli"
VIA_RPC = "rpc"
VIA_MCP_INTERNAL = "mcp_internal"
VIA_MCP_EXTERNAL = "mcp_external"
VIA_VALUES = frozenset({VIA_UI, VIA_CLI, VIA_RPC, VIA_MCP_INTERNAL, VIA_MCP_EXTERNAL})

#: Column widths of ``TimelineEvent.via`` / ``actor_ref`` / ``actor_name`` (a test pins the model to them).
VIA_MAX_LENGTH = 20
ACTOR_REF_MAX_LENGTH = 255
ACTOR_NAME_MAX_LENGTH = 80

#: Reserved top-level key of a drop-file payload carrying the CLI-side origin (decision D2).
ORIGIN_PAYLOAD_KEY = "_origin"

# Actors a drop file may claim. ``system`` is reserved for code paths: a forged one is ignored.
_CALLER_ACTORS = frozenset({ACTOR_USER, ACTOR_AGENT, ACTOR_EXTERNAL})


class RequestOrigin(NamedTuple):
    """``(actor, via, actor_ref, actor_name)`` of the request that caused an event."""

    actor: str
    via: str | None = None
    actor_ref: str | None = None
    actor_name: str | None = None


UI_ORIGIN = RequestOrigin(ACTOR_USER, VIA_UI)
#: Applied to a request-caused write that reaches the writer with no origin at all.
DEFAULT_ORIGIN = UI_ORIGIN
SYSTEM_ORIGIN = RequestOrigin(ACTOR_SYSTEM)

request_origin: ContextVar[RequestOrigin | None] = ContextVar("twicc_request_origin", default=None)


def current_origin() -> RequestOrigin | None:
    """The origin held by the carrier, ``None`` when unset."""
    return request_origin.get()


def capture_origin() -> RequestOrigin:
    """The origin to store for a request-caused fact: the carrier, else the default. Never ``None``."""
    return request_origin.get() or DEFAULT_ORIGIN


@contextlib.contextmanager
def origin_scope(origin: RequestOrigin | None) -> Iterator[None]:
    """Hold ``origin`` for the enclosed block, then restore the previous value.

    ``None`` is an explicit unset (the default then applies). Use it from a single task or thread:
    the token is reset in the context that set it.
    """
    token = request_origin.set(origin)
    try:
        yield
    finally:
        request_origin.reset(token)


def system_marker() -> contextlib.AbstractContextManager[None]:
    """Mark the enclosed block as an automatic step (design section 2, *System marker*)."""
    return origin_scope(SYSTEM_ORIGIN)


# --- Builders: one per row of the mapping table (design section 4) ---------------------------------------


def human_cli_origin() -> RequestOrigin:
    return RequestOrigin(ACTOR_USER, VIA_CLI)


def agent_cli_origin(session_id: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_AGENT, VIA_CLI, session_id)


def rpc_origin(token_id: str | None) -> RequestOrigin:
    return RequestOrigin(ACTOR_USER, VIA_RPC, token_id)


def mcp_session_origin(session_id: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_AGENT, VIA_MCP_INTERNAL, session_id)


def mcp_token_origin(token_id: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_USER, VIA_MCP_INTERNAL, token_id)


def mcp_external_origin(connection_id: str, name: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_EXTERNAL, VIA_MCP_EXTERNAL, connection_id, name)


# --- Drop-file codec (design section 6, requirement 2) ----------------------------------------------------


def origin_to_payload(origin: RequestOrigin) -> dict:
    """The JSON-able form written under :data:`ORIGIN_PAYLOAD_KEY` of a drop payload."""
    return origin._asdict()


def origin_from_payload(raw: object) -> RequestOrigin | None:
    """Parse a drop-file origin; ``None`` (= unset, the default applies) when absent or not credible.

    The file is forgeable by any local process (design section 11): this is an audit trail, not a
    security control. Still, ``system`` is never accepted from a file and every field must be a string.
    """
    if not isinstance(raw, dict):
        return None
    actor = raw.get("actor")
    if not isinstance(actor, str) or actor not in _CALLER_ACTORS:
        return None
    fields = [raw.get(key) for key in ("via", "actor_ref", "actor_name")]
    if any(field is not None and not isinstance(field, str) for field in fields):
        return None
    return RequestOrigin(actor, *fields)


# --- Resolution used by the recording service -------------------------------------------------------------


def resolve_event_origin(actor: str | None, origin: RequestOrigin | None) -> RequestOrigin:
    """The origin an event is written with (design section 5, *Precedence*).

    1. A captured full ``origin`` wins; all four fields are kept as captured.
    2. An explicit ``actor`` (system and non-request writers) gives NULL origin fields.
    3. Neither: a request-caused write. The carrier, else the default ``user`` / ``ui``; a carrier
       holding the system marker gives ``system`` with NULL fields.

    Never raises: reading the carrier failing falls back to the default (request-caused writes only).
    """
    if origin is not None:
        return origin
    if actor is not None:
        return RequestOrigin(actor)
    try:
        return capture_origin()
    except Exception:
        logger.exception("Could not read the request origin; using the default")
        return DEFAULT_ORIGIN
```

- [ ] **Step 5: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_request_origin.py -q`
Expected: all tests pass (about 30 collected, `0 failed`).

- [ ] **Step 6: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/request_origin.py tests/test_request_origin.py tests/timeline_helpers.py`
Expected: `All checks passed!`

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/request_origin.py tests/test_request_origin.py tests/timeline_helpers.py && git commit -m "$(cat <<'EOF'
feat(timeline): add the request origin carrier

Add twicc.request_origin: a RequestOrigin NamedTuple, the request_origin
ContextVar, scoped set/restore helpers, the explicit system marker, one
builder per row of the actor/via mapping table, the drop-payload codec and
resolve_event_origin (captured origin > explicit actor > carrier > default).

The module is pure (no Django import) so the CLI, ASGI, services and agents
can all use it. Tests pin the visibility facts the design relies on
(to_thread, run_coroutine_threadsafe, tasks created before the set, plain
threads) and that concurrent requests never mix.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Columns, migration, registry and recording-service plumbing

**Files:**
- Modify: `src/twicc/core/models.py` (class `TimelineEvent`)
- Create: `src/twicc/core/migrations/0158_timeline_event_origin.py` (generated)
- Modify: `src/twicc/core/timeline.py`
- Modify: `src/twicc/core/services/session_timeline.py`
- Modify: `tests/test_timeline_model.py`
- Create: `tests/test_timeline_origin_columns.py`

**Interfaces:**
- Consumes (T1): `RequestOrigin`, `resolve_event_origin`, `VIA_VALUES`, `VIA_MAX_LENGTH`, `ACTOR_REF_MAX_LENGTH`, `ACTOR_NAME_MAX_LENGTH` from `twicc.request_origin`; `ORIGIN_MAPPING`, `columns` from `tests/timeline_helpers.py`.
- Produces:
  - `TimelineEvent.via`, `.actor_ref`, `.actor_name` (nullable columns).
  - `session_timeline.record_event_sync(kind, *, session_id=None, project_id=None, occurred_at=None, precision=..., actor: str | None = None, origin: RequestOrigin | None = None, reason="", caused_by=None, data=None, dedupe_key=None)`. `actor=None` and `origin=None` together mean "request-caused" (carrier, else default). `origin` wins over `actor`.
  - `session_timeline.EventSpec(..., dedupe_key=None, origin: RequestOrigin | None = None)`; `actor` still defaults to `TimelineActor.SYSTEM` (NULL origin fields); a request-caused batch passes `origin=`.
  - `session_timeline.origin_columns(resolved: RequestOrigin) -> dict` (`actor`, `via`, `actor_ref`, `actor_name`; invalid `via` -> `None`; ref/name truncated).
  - `core.timeline.validate_data` no longer accepts a `via` key (D1).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_origin_columns.py`:

```python
"""The origin columns and their plumbing through every write path (design sections 3, 4, 8)."""

import pytest
from django.utils import timezone

from tests.timeline_helpers import ORIGIN_MAPPING, columns, run_with_writer
from twicc.core.models import Project, Session, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services import session_timeline
from twicc.core.services.session_timeline import EventSpec, record_event_sync, record_events_bulk_sync
from twicc.core.services.timeline_backfill import run_timeline_backfill
from twicc.request_origin import (
    ACTOR_NAME_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, DEFAULT_ORIGIN, SYSTEM_ORIGIN, VIA_MAX_LENGTH, VIA_VALUES,
    RequestOrigin, agent_cli_origin, mcp_session_origin, origin_scope, system_marker,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-cols", directory="/tmp/tl-origin-cols")


# --- The model --------------------------------------------------------------------------------------------


def test_widths_nullability_and_constants_are_pinned():
    meta = TimelineEvent._meta
    for name, width in (("via", VIA_MAX_LENGTH), ("actor_ref", ACTOR_REF_MAX_LENGTH),
                        ("actor_name", ACTOR_NAME_MAX_LENGTH)):
        field = meta.get_field(name)
        assert field.max_length == width and field.null is True and field.db_index is False, name
        assert not any(name in index.fields for index in meta.indexes), name
    assert (VIA_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, ACTOR_NAME_MAX_LENGTH) == (20, 255, 80)
    assert VIA_VALUES == {"ui", "cli", "rpc", "mcp_internal", "mcp_external"}
    assert all(len(value) <= VIA_MAX_LENGTH for value in VIA_VALUES)


def test_actor_values_still_are_the_four_of_the_spec():
    assert set(TimelineActor.values) == {"user", "agent", "system", "external"}


# --- The single-event path --------------------------------------------------------------------------------


@pytest.mark.parametrize(("origin", "expected"), ORIGIN_MAPPING)
def test_every_mapping_row_is_stored_as_given(origin, expected):
    row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", origin=origin)
    row.refresh_from_db()
    assert columns(row) == expected


def test_no_actor_no_origin_and_no_carrier_is_the_default():
    row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == ("user", "ui", None, None)


def test_the_carrier_is_read_when_neither_actor_nor_origin_is_given():
    with origin_scope(agent_cli_origin("sess-A")):
        row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == ("agent", "cli", "sess-A", None)


def test_an_explicit_actor_wins_over_the_carrier_and_leaves_the_origin_fields_null():
    with origin_scope(agent_cli_origin("sess-A")):
        row = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
        system = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.SYSTEM)
    assert columns(row) == ("agent", None, None, None)
    assert columns(system) == ("system", None, None, None)


def test_the_system_marker_gives_system_with_null_fields_never_the_default():
    with system_marker():
        row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == ("system", None, None, None)


def test_a_captured_origin_wins_over_the_carrier_and_over_an_actor_and_keeps_all_four_fields():
    captured = RequestOrigin("external", "mcp_external", "conn-1", "ChatGPT")
    with origin_scope(agent_cli_origin("sess-A")):
        row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor="system", origin=captured)
    assert columns(row) == tuple(captured)


def test_an_invalid_via_is_stored_as_null_and_the_event_is_still_written():
    row = record_event_sync(
        TimelineKind.ARCHIVED_STARTED, session_id="s1", origin=RequestOrigin("user", "carrier-pigeon", "ref", "name"),
    )
    assert row is not None
    assert columns(TimelineEvent.objects.get(pk=row.pk)) == ("user", None, "ref", "name")


def test_a_long_ref_and_name_are_truncated_not_rejected():
    row = record_event_sync(
        TimelineKind.ARCHIVED_STARTED, session_id="s1",
        origin=RequestOrigin("external", "mcp_external", "r" * 300, "n" * 100),
    )
    stored = TimelineEvent.objects.get(pk=row.pk)
    assert stored.actor_ref == "r" * 255 and stored.actor_name == "n" * 80


def test_an_unreadable_carrier_falls_back_to_the_default_and_the_event_is_written(monkeypatch):
    def boom():
        raise RuntimeError("unreadable")

    monkeypatch.setattr("twicc.request_origin.capture_origin", boom)
    row = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1")
    assert columns(row) == tuple(DEFAULT_ORIGIN)


def test_an_ended_row_never_copies_the_origin_fields_of_its_started_row():
    record_event_sync(
        TimelineKind.PROCESS_STARTED, session_id="s1", origin=agent_cli_origin("sess-A"), data={"provider": "codex"},
    )
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", origin=SYSTEM_ORIGIN, reason="error")
    assert columns(ended) == ("system", None, None, None)
    assert ended.data["provider"] == "codex"  # the payload is still inherited
    other = record_event_sync(TimelineKind.PROCESS_STARTED, session_id="s2", origin=mcp_session_origin("sess-B"),
                              data={"provider": "codex"})
    user_end = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s2", origin=RequestOrigin("user", "ui"),
                                 reason="manual")
    assert columns(other) != columns(user_end) and columns(user_end) == ("user", "ui", None, None)


# --- The batch path and its row-by-row fallback ------------------------------------------------------------


def test_a_batch_writes_one_origin_on_every_row():
    origin = mcp_session_origin("sess-A")
    count = record_events_bulk_sync(
        [EventSpec(TimelineKind.ARCHIVED_STARTED, session_id=f"s{i}", origin=origin) for i in range(3)]
    )
    assert count == 3
    assert {columns(row) for row in TimelineEvent.objects.all()} == {tuple(origin)}


def test_a_batch_spec_with_only_an_actor_has_null_origin_fields():
    record_events_bulk_sync([EventSpec(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)])
    assert columns(TimelineEvent.objects.get(session_id="s1")) == ("agent", None, None, None)


def test_the_batch_path_truncates_and_nulls_like_the_single_path():
    record_events_bulk_sync([
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="a",
                  origin=RequestOrigin("external", "mcp_external", "r" * 300, "n" * 100)),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="b", origin=RequestOrigin("user", "bad-via", "ref")),
    ])
    first, second = (TimelineEvent.objects.get(session_id=s) for s in ("a", "b"))
    assert columns(first) == ("external", "mcp_external", "r" * 255, "n" * 80)
    assert columns(second) == ("user", None, "ref", None)


def test_the_batch_fallback_carries_the_same_columns_as_the_batch_path(monkeypatch):
    specs = [
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="a",
                  origin=RequestOrigin("external", "mcp_external", "r" * 300, "n" * 100)),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="b", origin=RequestOrigin("user", "bad-via", "ref")),
        EventSpec(TimelineKind.TURN_STARTED, session_id="c", actor=TimelineActor.AGENT),
    ]
    record_events_bulk_sync(specs)
    expected = {s: columns(TimelineEvent.objects.get(session_id=s)) for s in ("a", "b", "c")}
    TimelineEvent.objects.all().delete()

    def boom(rows):
        raise RuntimeError("batch insert failed")

    monkeypatch.setattr(session_timeline, "insert_event_rows", boom)
    assert record_events_bulk_sync(specs) == 3
    assert {s: columns(TimelineEvent.objects.get(session_id=s)) for s in ("a", "b", "c")} == expected


# --- Backfill and old rows ---------------------------------------------------------------------------------


def test_the_backfill_writes_null_origin_fields(project):
    Session.objects.create(
        id="s1", file_path="s1.jsonl", project=project, provider="codex", created_at=timezone.now(), hidden=True,
    )
    run_with_writer(run_timeline_backfill)
    rows = list(TimelineEvent.objects.filter(session_id="s1"))
    assert {row.kind for row in rows} == {"session.created", "hidden.started"}
    assert {columns(row) for row in rows} == {("system", None, None, None)}


def test_rows_created_without_the_columns_read_as_null():
    row = TimelineEvent.objects.create(
        session_id="old", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    row.refresh_from_db()
    assert (row.via, row.actor_ref, row.actor_name) == (None, None, None)
```

Edit `tests/test_timeline_model.py`: replace the `via` handling.

Replace
```python
    started = {"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True, "via": "cli"}
```
with
```python
    started = {"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True}
```
and replace the whole `test_validate_data_allows_the_reserved_via_key_on_every_kind` function with:
```python
def test_validate_data_no_longer_accepts_a_data_via_key():
    # The origin lives in the TimelineEvent columns now (design 2026-10-14, section 8, decision D1).
    with pytest.raises(ValueError):
        validate_data("turn.started", {"via": "cli"})
    with pytest.raises(ValueError):
        validate_data("title.changed", {"source": "user", "new": "x", "via": "ui"})
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_columns.py tests/test_timeline_model.py -q`
Expected: failures, e.g. `django.core.exceptions.FieldDoesNotExist: TimelineEvent has no field named 'via'` / `TypeError: record_event_sync() got an unexpected keyword argument 'origin'`, and `test_validate_data_no_longer_accepts_a_data_via_key` fails (`DID NOT RAISE`).

- [ ] **Step 3: Add the columns to the model**

In `src/twicc/core/models.py`, class `TimelineEvent`, directly after the `actor = ...` line add:

```python
    # Origin of the request that caused the event (design 2026-10-14-timeline-actor-origin). All NULL for
    # an automatic or not-request-caused event and for every row written before this change. Plain
    # columns: ``actor_ref`` is not a foreign key (the referenced token or connection may be deleted),
    # ``actor_name`` is a snapshot. The widths mirror ``twicc.request_origin``; a test pins them.
    via = models.CharField(max_length=20, null=True, blank=True)
    actor_ref = models.CharField(max_length=255, null=True, blank=True)
    actor_name = models.CharField(max_length=80, null=True, blank=True)
```

- [ ] **Step 4: Update the registry (decision D1)**

In `src/twicc/core/timeline.py`:

1. Delete the `VIA_VALUES` definition and its comment (the two lines `#: Entry surfaces accepted in the reserved ...` and `VIA_VALUES = frozenset({"ui", "cli", "mcp"})`) AND one of the blank lines around them, so exactly two blank lines remain before `class SessionCreatedData` (E303).
2. In the `PAYLOAD_TYPES` comment replace `#: \`\`None\`\` means "no payload allowed" (only the reserved \`\`via\`\` key).` with `#: \`\`None\`\` means "no payload allowed".`
3. In `inherit_started_fields`, remove the reserved-`via` sentence from the docstring: the two source lines `nothing is copied. The reserved ``via`` key` and `is never copied. Pure: no validation, see :func:`validate_data`.` become the single line `nothing is copied. Pure: no validation, see :func:`validate_data`.`
4. Replace `validate_data` with:

```python
def validate_data(kind: str, data: dict | None) -> dict:
    """Return the validated payload of ``kind``, defaults filled in.

    Raises ``ValueError`` on an unknown kind or an unknown or missing key. The origin of an event is
    not part of the payload (it lives in the ``via`` / ``actor_ref`` / ``actor_name`` columns), so a
    ``via`` key is an unknown key like any other.
    """
    try:
        payload_type = PAYLOAD_TYPES[kind]
    except KeyError:
        raise ValueError(f"unknown timeline kind {kind!r}") from None
    data = dict(data or {})
    if payload_type is None:
        if data:
            raise ValueError(f"{kind} takes no payload, got {sorted(data)}")
        return {}
    unknown = set(data) - set(payload_type._fields)
    if unknown:
        raise ValueError(f"{kind}: unknown payload keys {sorted(unknown)}")
    try:
        return payload_type(**data)._asdict()
    except TypeError as exc:
        raise ValueError(f"{kind}: {exc}") from exc
```

- [ ] **Step 5: Plumb the origin through the recording service**

In `src/twicc/core/services/session_timeline.py`:

1. Update the imports: add
```python
from twicc.request_origin import (
    ACTOR_NAME_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, VIA_VALUES, RequestOrigin, resolve_event_origin,
)
```
2. Add, above `record_event_sync`:

```python
def _truncate(value, limit: int) -> str | None:
    return None if value is None else str(value)[:limit]


def origin_columns(resolved: RequestOrigin) -> dict:
    """The four origin columns of a row (design section 3).

    An invalid ``via`` becomes NULL and the other three values are kept (O35); ``actor_ref`` and
    ``actor_name`` are cut to their column width, never rejected (O24).
    """
    via = resolved.via
    if via is not None and via not in VIA_VALUES:
        logger.warning("Timeline event: invalid via %r stored as NULL", via)
        via = None
    return {
        "actor": resolved.actor,
        "via": via,
        "actor_ref": _truncate(resolved.actor_ref, ACTOR_REF_MAX_LENGTH),
        "actor_name": _truncate(resolved.actor_name, ACTOR_NAME_MAX_LENGTH),
    }
```
3. Replace `record_event_sync` (signature, docstring head and the `create` call):

```python
def record_event_sync(
    kind: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    occurred_at=None,
    precision: str = TimelinePrecision.EXACT,
    actor: str | None = None,
    origin: RequestOrigin | None = None,
    reason: str = "",
    caused_by: TimelineEvent | int | None = None,
    data: dict | None = None,
    dedupe_key: str | None = None,
) -> TimelineEvent | None:
    """Write one event. Returns the row, or ``None`` on failure or dedupe hit.

    Who caused the event (design section 5, *Precedence*): a captured full ``origin`` keeps its four
    fields; else an explicit ``actor`` (system and non-request writers) gives NULL origin fields; else
    the write is request-caused and takes the carrier, falling back to the default ``user`` / ``ui``.
    A system writer MUST pass ``actor=`` explicitly.

    The insert runs in its own savepoint: when the caller already holds a
    transaction, a failed insert rolls back only this row. For an ``ended`` kind
    listed in ``STARTED_OF``, the payload fields the caller did not provide are
    copied from the open ``started`` row of the same session (design §3); the
    origin fields are never copied.
    """
    try:
        resolved = resolve_event_origin(actor, origin)
        if kind in STARTED_OF:
            data = inherit_started_fields(kind, data, _open_started_data(session_id, kind))
        payload = validate_data(kind, data)
        caused_by_id = caused_by.pk if isinstance(caused_by, TimelineEvent) else caused_by
        with transaction.atomic():
            if (
                dedupe_key is not None
                and TimelineEvent.objects.filter(session_id=session_id, dedupe_key=dedupe_key).exists()
            ):
                return None
            return TimelineEvent.objects.create(
                session_id=session_id,
                project_id=project_id,
                kind=kind,
                occurred_at=occurred_at or timezone.now(),
                precision=precision,
                reason=reason,
                caused_by_id=caused_by_id,
                data=payload,
                dedupe_key=dedupe_key,
                **origin_columns(resolved),
            )
```
(the two `except` clauses below stay unchanged).
4. In `EventSpec`, extend the docstring and the fields:

```python
class EventSpec(NamedTuple):
    """One event of a batch given to :func:`record_events_bulk_sync` (same fields as ``record_event_sync``).

    ``actor`` defaults to ``system`` (NULL origin fields). A request-caused batch passes ``origin=``
    (resolved once by the caller, so every row carries the same origin).
    """

    kind: str
    session_id: str | None = None
    project_id: str | None = None
    occurred_at: object = None
    precision: str = TimelinePrecision.EXACT
    actor: str = TimelineActor.SYSTEM
    reason: str = ""
    caused_by_id: int | None = None
    data: dict | None = None
    dedupe_key: str | None = None
    origin: RequestOrigin | None = None
```
5. In `record_events_bulk_sync`, replace the building loop and the fallback so the origin is resolved once per spec and reused by the fallback:

```python
        built: list[tuple[EventSpec, TimelineEvent, RequestOrigin]] = []
        for spec in specs:
            try:
                resolved = resolve_event_origin(spec.actor, spec.origin)
                data = spec.data
                if spec.kind in STARTED_OF:
                    data = inherit_started_fields(spec.kind, data, _open_started_data(spec.session_id, spec.kind))
                built.append((spec, TimelineEvent(
                    session_id=spec.session_id, project_id=spec.project_id, kind=spec.kind,
                    occurred_at=spec.occurred_at or now, precision=spec.precision,
                    reason=spec.reason, caused_by_id=spec.caused_by_id, data=validate_data(spec.kind, data),
                    dedupe_key=spec.dedupe_key, **origin_columns(resolved),
                ), resolved))
            except Exception:
                logger.exception("Timeline event %s could not be built for session %s", spec.kind, spec.session_id)
        if not built:
            return 0
        rows = [row for _, row, _ in built]
        try:
            with transaction.atomic():
                return insert_event_rows(rows)
        except Exception:
            logger.exception("Timeline batch insert failed (%s rows); retrying row by row", len(rows))
            return sum(
                1 for spec, _, resolved in built
                if record_event_sync(
                    spec.kind, session_id=spec.session_id, project_id=spec.project_id,
                    occurred_at=spec.occurred_at or now, precision=spec.precision, origin=resolved,
                    reason=spec.reason, caused_by=spec.caused_by_id, data=spec.data, dedupe_key=spec.dedupe_key,
                ) is not None
            )
```
(The outer `except Exception` of the function stays.)

- [ ] **Step 6: Create the migration (do NOT apply it)**

Sanity-check the resolved data directory first:

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run python -c "from twicc.paths import get_db_path; print(get_db_path())"`
Expected: `/home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/db/data.sqlite` (inside the worktree; anything else: stop).

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --settings=twicc.settings -n timeline_event_origin`
Expected: `Migrations for 'core': src/twicc/core/migrations/0158_timeline_event_origin.py` with `+ Add field actor_name to timelineevent`, `+ Add field actor_ref to timelineevent`, `+ Add field via to timelineevent`.

Open the generated file and check: `dependencies = [('core', '0157_timeline_event_session_kind_index')]` and exactly three `migrations.AddField(model_name='timelineevent', ...)` operations with `max_length` 80, 255, 20, `null=True`, `blank=True`. Do not run `migrate`. The user applies it on their own instance (devctl start applies it automatically).

- [ ] **Step 6b: Fix the existing test that used `actor=None` as a NOT NULL trigger**

After this task `actor=None` means "request-caused", so that event is written and the old assertion fails. In `tests/test_timeline_service.py`, `test_a_failing_insert_does_not_poison_the_callers_transaction`, replace

```python
        # actor=None violates NOT NULL: the INSERT fails at SQL level.
        assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=None) is None
```
with
```python
        # precision=None violates NOT NULL: the INSERT fails at SQL level.
        assert record_event_sync(
            TimelineKind.TURN_STARTED, session_id="s1", actor="system", precision=None,
        ) is None
```
Audited: `grep -rn "actor=None\|NOT NULL" tests/test_timeline_*.py` finds only this test and `tests/test_timeline_backfill.py`, whose NOT NULL case sets `bad.kind = None` and is unaffected. Add `tests/test_timeline_service.py` to this task's `git add` list.

- [ ] **Step 7: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_columns.py tests/test_timeline_model.py tests/test_timeline_service.py tests/test_timeline_bulk_record.py tests/test_timeline_backfill.py tests/test_timeline_enums_sync.py -q`
Expected: all pass (the pytest test database is built from the migrations, so it includes `0158`).

- [ ] **Step 8: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/core/models.py src/twicc/core/timeline.py src/twicc/core/services/session_timeline.py tests/test_timeline_origin_columns.py tests/test_timeline_model.py`
Expected: no finding on lines written by this task.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/models.py src/twicc/core/migrations/0158_timeline_event_origin.py src/twicc/core/timeline.py src/twicc/core/services/session_timeline.py tests/test_timeline_origin_columns.py tests/test_timeline_model.py tests/test_timeline_service.py && git commit -m "$(cat <<'EOF'
feat(timeline): add the via, actor_ref and actor_name columns

TimelineEvent gets three nullable columns next to actor (via 20, actor_ref
255, actor_name 80) and migration 0158. record_event_sync takes actor=None
plus an optional captured origin and resolves the four columns in one place:
captured origin, then explicit actor (NULL fields), then the carrier, then
the default user/ui. The batch path and its row-by-row fallback carry the
same columns; an invalid via is stored as NULL, long refs and names are
truncated, never rejected. The registry no longer accepts a data.via key.

The migration is created but not applied: the user runs migrate.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: UI entry points (WebSocket frames and REST)

**Files:**
- Modify: `src/twicc/asgi.py` (`WSConsumer.receive_json`)
- Modify: `src/twicc/auth/middleware.py` (new `RequestOriginMiddleware`)
- Modify: `src/twicc/settings.py` (`MIDDLEWARE`)
- Create: `tests/test_timeline_origin_ui.py`

**Interfaces:**
- Consumes (T1): `UI_ORIGIN`, `origin_scope`, `current_origin`.
- Produces: every WS frame and every non-`/rpc/` Django request runs with `UI_ORIGIN` in the carrier. Tasks spawned by the frame handlers (`_spawn_detached`) inherit it. After the frame / request the carrier is restored to its previous value.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_origin_ui.py`:

```python
"""The web UI sets the origin: WebSocket frames and REST requests (design sections 5, 6.1)."""

import asyncio

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory

from twicc import asgi
from twicc.asgi import WSConsumer
from twicc.auth.middleware import RequestOriginMiddleware
from twicc.request_origin import UI_ORIGIN, current_origin


def test_each_frame_runs_with_the_ui_origin_and_restores_the_carrier(monkeypatch):
    seen = []

    async def probe(self, content):
        seen.append(current_origin())

    monkeypatch.setattr(WSConsumer, "_handle_changelog_seen", probe)

    async def scenario():
        await WSConsumer().receive_json({"type": "changelog_seen"})
        return current_origin()

    assert asyncio.run(scenario()) is None
    assert seen == [UI_ORIGIN]


def test_a_detached_task_spawned_by_a_frame_inherits_the_ui_origin(monkeypatch):
    seen = []

    async def probe(self, session_id, content):
        seen.append(current_origin())

    monkeypatch.setattr(WSConsumer, "_run_send_message", probe)

    async def scenario():
        await WSConsumer().receive_json(
            {"type": "send_message", "session_id": "s1", "project_id": "-p", "text": "hello"}
        )
        await asyncio.gather(*list(asgi._DETACHED_TASKS))

    asyncio.run(scenario())
    assert seen == [UI_ORIGIN]


def test_the_frame_origin_is_restored_even_when_the_handler_raises(monkeypatch):
    async def boom(self, content):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(WSConsumer, "_handle_changelog_seen", boom)

    async def scenario():
        with pytest.raises(RuntimeError):
            await WSConsumer().receive_json({"type": "changelog_seen"})
        return current_origin()

    assert asyncio.run(scenario()) is None


async def _through_middleware(path):
    seen = []

    async def get_response(request):
        seen.append(current_origin())
        return HttpResponse()

    await RequestOriginMiddleware(get_response)(RequestFactory().get(path))
    return seen, current_origin()


def test_the_middleware_holds_the_ui_origin_around_a_view():
    assert asyncio.run(_through_middleware("/api/projects/")) == ([UI_ORIGIN], None)


def test_the_middleware_leaves_rpc_requests_to_the_rpc_view():
    assert asyncio.run(_through_middleware("/rpc/sessions")) == ([None], None)


def test_the_middleware_is_installed_after_the_auth_middlewares():
    chain = list(settings.MIDDLEWARE)
    assert "twicc.auth.middleware.RequestOriginMiddleware" in chain
    assert chain.index("twicc.auth.middleware.RequestOriginMiddleware") > chain.index(
        "twicc.auth.middleware.RpcTokenAuthMiddleware"
    )
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_ui.py -q`
Expected: `ImportError: cannot import name 'RequestOriginMiddleware'` (collection error).

- [ ] **Step 3: Add the middleware**

In `src/twicc/auth/middleware.py` add to the imports `from twicc.request_origin import UI_ORIGIN, origin_scope`, and append at the end of the file:

```python
class RequestOriginMiddleware:
    """Hold the web UI origin around every request the browser makes (design 2026-10-14, sections 5-6).

    Django runs each request in its own task, so an unset carrier would already read as the default
    ``user`` / ``ui``; setting it explicitly makes the entry point visible and testable, and a new
    ``/api/`` view gets it with no extra work. ``/rpc/`` is skipped: the RPC view sets its own origin
    (a token id, or nothing for the read-only cookie scope, which writes no event).
    """

    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)

    async def __call__(self, request):
        if request.path.startswith("/rpc/"):
            return await self.get_response(request)
        with origin_scope(UI_ORIGIN):
            return await self.get_response(request)
```

In `src/twicc/settings.py` change `MIDDLEWARE` to:

```python
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "twicc.auth.middleware.PasswordAuthMiddleware",
    "twicc.auth.middleware.RpcTokenAuthMiddleware",
    "twicc.auth.middleware.RequestOriginMiddleware",
]
```

- [ ] **Step 4: Wrap the WebSocket frame handling**

In `src/twicc/asgi.py` add `from twicc.request_origin import UI_ORIGIN, origin_scope` to the imports (next to the other `twicc.` imports), then replace the head of `WSConsumer.receive_json`:

Old:
```python
    async def receive_json(self, content, **kwargs):
        """Handle incoming messages from clients.
```
New:
```python
    async def receive_json(self, content, **kwargs):
        """Handle one client frame with the web UI origin held (design 2026-10-14, sections 5-6).

        The scope covers the whole dispatch. Detached tasks the handlers spawn (sends, stops, title
        suggestions) are created inside it, so they inherit the origin; the carrier is restored when
        the frame is done.
        """
        with origin_scope(UI_ORIGIN):
            await self._receive_frame(content, **kwargs)

    async def _receive_frame(self, content, **kwargs):
        """Handle incoming messages from clients.
```
The rest of the old method body (docstring continuation and the `if msg_type == ...` chain) stays as the body of `_receive_frame`, unchanged.

- [ ] **Step 5: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_ui.py tests/test_timeline_bulk_archive.py tests/test_ephemeral_ws.py tests/test_composer_attachment_send.py -q`
Expected: all pass. (`test_timeline_bulk_archive.py` is run unchanged as a regression check: its endpoint test asserts `actor == "user"`, which still holds. The `via` assertions on the bulk endpoint belong to T6, where the writer changes.)

**Mutation check (do not commit).** (1) Delete `with origin_scope(UI_ORIGIN):` in `WSConsumer.receive_json`: `test_each_frame_runs_with_the_ui_origin_and_restores_the_carrier` and the detached-task test FAIL (no ambient origin in them). (2) Delete the same line in `RequestOriginMiddleware.__call__`: `test_the_middleware_holds_the_ui_origin_around_a_view` FAILS.

- [ ] **Step 6: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/auth/middleware.py tests/test_timeline_origin_ui.py`
Expected: no finding on lines written by this task.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/asgi.py src/twicc/auth/middleware.py src/twicc/settings.py tests/test_timeline_origin_ui.py && git commit -m "$(cat <<'EOF'
feat(timeline): set the web UI origin on WebSocket frames and REST requests

WSConsumer.receive_json now runs each frame inside a UI origin scope (the
detached tasks it spawns inherit it) and a new RequestOriginMiddleware holds
the same origin around every non-RPC Django request. The RPC view sets its
own origin in a later change. The carrier is restored after each frame or
request, including when the handler raises.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: RPC and MCP entry points

**Files:**
- Modify: `src/twicc/auth/middleware.py` (`RpcTokenAuthMiddleware` publishes the token id)
- Modify: `src/twicc/rpc/views.py` (`dispatch`)
- Modify: `src/twicc/mcp/identity.py` (scope key constant)
- Modify: `src/twicc/mcp/endpoint.py` (`_authenticate`)
- Modify: `src/twicc/mcp/server.py` (`_call_tool`, `_origin_of_call`)
- Modify: `src/twicc/mcp/batch.py` (`_Run.origin`, `_child`)
- Create: `tests/test_timeline_origin_entry.py`

**Interfaces:**
- Consumes (T1): `rpc_origin`, `mcp_session_origin`, `mcp_token_origin`, `mcp_external_origin`, `origin_scope`, `request_origin` (the ContextVar), `current_origin`.
- Produces:
  - `request.rpc_token_id: str | None` on every `/rpc/` request (set by `RpcTokenAuthMiddleware`).
  - `twicc.mcp.identity.TOKEN_ID_SCOPE_KEY = "twicc_mcp_token_id"`; `twicc.mcp.endpoint._authenticate(scope) -> McpAuth | None` with `McpAuth(session_id, token_id)`; `handle_mcp` stores the token id in the ASGI scope.
  - `twicc.mcp.server._origin_of_call(ctx, session_id) -> RequestOrigin | None`; `_call_tool` runs the whole call (single or batch) inside `origin_scope(...)`.
  - `BatchRuntime` children run with the origin of the batch call (`_Run.origin`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_origin_entry.py`:

```python
"""RPC and MCP entry points set the origin (design sections 4, 5, 6; verification 3a, 3b, 3c, 4, 8b)."""

import asyncio
import contextvars
from types import SimpleNamespace

import httpx
import pytest
from django.test import AsyncClient
from mcp import types as mcp_types

from twicc.auth import tokens as api_tokens
from twicc.auth.tokens import TokenRecord
from twicc.mcp import batch as batch_module
from twicc.mcp import identity, server
from twicc.mcp.batch import BatchRuntime
from twicc.mcp.batch_contract import PreparedBatch, PreparedCall
from twicc.mcp.dispatch import PreparedTool
from twicc.mcp.endpoint import _authenticate, handle_mcp, mcp_lifespan
from twicc.mcp.identity import ExternalCaller, TOKEN_ID_SCOPE_KEY, external_caller, mint_session_token
from twicc.mcp.tools import tools_by_name
from twicc.request_origin import (
    UI_ORIGIN, current_origin, mcp_external_origin, mcp_session_origin, mcp_token_origin, origin_scope, rpc_origin,
)
from twicc.rpc.invoker import InvocationResult

pytestmark = pytest.mark.django_db(transaction=True)


# --- RPC ---------------------------------------------------------------------------------------------------


@pytest.fixture
def tokens_store(monkeypatch):
    records: dict[str, TokenRecord] = {}
    monkeypatch.setattr(api_tokens, "has_tokens", lambda: bool(records))
    monkeypatch.setattr(api_tokens, "verify_token", lambda token: records.get(token))
    return records


@pytest.fixture
def invoked(monkeypatch):
    """The origins seen by the command invoker, one per executed RPC command."""
    seen = []

    def fake_invoke(argv):
        seen.append(current_origin())
        return InvocationResult(exit_code=0, result={"ok": True}, error=None)

    monkeypatch.setattr("twicc.rpc.views.invoke", fake_invoke)
    return seen


def _post(path, **extra):
    return asyncio.run(AsyncClient().post(path, data=b"", content_type="application/json", **extra))


def test_an_rpc_call_with_a_token_carries_the_token_id(tokens_store, invoked):
    tokens_store["secret"] = TokenRecord(id="tok_abc", name="t", digest="x", created_at="", last_used_at=None)
    assert _post("/rpc/sessions", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert invoked == [rpc_origin("tok_abc")]


def test_an_rpc_call_with_no_token_record_has_no_ref(tokens_store, invoked, settings):
    settings.TWICC_PASSWORD_HASH = ""  # neither a password nor a token: the instance is open to loopback
    assert _post("/rpc/sessions").status_code == 200
    assert invoked == [rpc_origin(None)]


def test_a_cookie_scope_rpc_call_sets_no_origin(tokens_store, invoked, settings):
    # The cookie scope is read-only and writes no timeline event (design section 4): nothing is set.
    from importlib import import_module

    from django.conf import settings as dj_settings

    from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY, compute_fingerprint

    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$test$deadbeef"
    client = AsyncClient()
    store = import_module(dj_settings.SESSION_ENGINE).SessionStore()
    store[SESSION_AUTH_KEY] = True
    store[SESSION_FINGERPRINT_KEY] = compute_fingerprint(settings.TWICC_PASSWORD_HASH)
    store.create()
    client.cookies[dj_settings.SESSION_COOKIE_NAME] = store.session_key
    response = asyncio.run(client.post("/rpc/sessions", data=b"", content_type="application/json"))
    assert response.status_code == 200
    assert invoked == [None]


# --- Internal MCP --------------------------------------------------------------------------------------------


def _ctx(token="", *, token_id=None):
    scope = {TOKEN_ID_SCOPE_KEY: token_id} if token_id else {}
    return SimpleNamespace(request=SimpleNamespace(headers={"authorization": f"Bearer {token}"}, scope=scope))


@pytest.fixture
def executed(monkeypatch):
    """Replace the command execution: record the origin each tool call runs under."""
    seen = []

    async def fake_execute(prepared, *, session_id, on_start=None):
        seen.append(current_origin())
        await asyncio.sleep(0)
        return {"exit_code": 0, "result": None, "error": None}

    monkeypatch.setattr(server, "execute_prepared", fake_execute)
    return seen


def _call(ctx, name="workspaces", arguments=None):
    return server._call_tool(ctx, mcp_types.CallToolRequestParams(name=name, arguments=arguments or {}))


def test_an_internal_session_token_gives_the_calling_session(executed):
    asyncio.run(_call(_ctx(mint_session_token("sess-A"))))
    assert executed == [mcp_session_origin("sess-A")]


def test_a_local_api_token_gives_the_token_id(executed):
    asyncio.run(_call(_ctx("twicc_pat_xxx", token_id="tok_9")))
    assert executed == [mcp_token_origin("tok_9")]


def test_an_external_call_is_never_a_cli_call(executed):
    async def scenario():
        token = external_caller.set(ExternalCaller("conn-1", "ChatGPT"))
        try:
            await _call(_ctx("oauth-access-token"))
        finally:
            external_caller.reset(token)

    asyncio.run(scenario())
    assert executed == [mcp_external_origin("conn-1", "ChatGPT")]
    assert executed[0].via == "mcp_external" and executed[0].actor == "external"


def test_an_internal_call_is_never_an_agent_cli_call(executed):
    # That the backend never walks its own process ancestry is pinned where the walk could happen:
    # ``test_timeline_origin_drop::test_backend_mode_submit_carries_the_origin_through_the_loop`` (T5).
    asyncio.run(_call(_ctx(mint_session_token("sess-A"))))
    assert executed[0].via == "mcp_internal" and executed[0].actor_ref == "sess-A"


def test_two_overlapping_calls_never_mix_their_origins(monkeypatch):
    seen = {}

    async def scenario():
        gate = asyncio.Event()

        async def fake_execute(prepared, *, session_id, on_start=None):
            await gate.wait()
            seen[session_id] = current_origin()
            return {"exit_code": 0, "result": None, "error": None}

        monkeypatch.setattr(server, "execute_prepared", fake_execute)
        calls = [asyncio.create_task(_call(_ctx(mint_session_token(sid)))) for sid in ("sess-A", "sess-B")]
        await asyncio.sleep(0.01)
        gate.set()
        await asyncio.gather(*calls)

    asyncio.run(scenario())
    assert seen == {"sess-A": mcp_session_origin("sess-A"), "sess-B": mcp_session_origin("sess-B")}


def test_the_call_runs_under_its_origin_and_restores_the_previous_one(executed):
    # Inside an outer origin different from the call's: the tool must see the CALL's origin (it fails when
    # ``_call_tool`` does not set one) and the outer one must be back afterwards.
    async def scenario():
        with origin_scope(UI_ORIGIN):
            await _call(_ctx(mint_session_token("sess-A")))
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert executed == [mcp_session_origin("sess-A")]


def test_the_external_name_is_read_at_call_time(executed):
    async def scenario(name):
        token = external_caller.set(ExternalCaller("conn-1", name))
        try:
            await _call(_ctx("oauth-access-token"))
        finally:
            external_caller.reset(token)

    asyncio.run(scenario("Old name"))
    asyncio.run(scenario("Renamed"))
    assert [origin.actor_name for origin in executed] == ["Old name", "Renamed"]


# --- MCP batch ------------------------------------------------------------------------------------------------


def _batch(count=3, *, mode="parallel"):
    spec = tools_by_name()["session"]
    return PreparedBatch("batch", mode, "continue", tuple(
        PreparedCall(i, str(i), PreparedTool("session", spec, {"session_id": str(i)})) for i in range(count)
    ))


async def _valid(grant):
    return True


@pytest.mark.parametrize("mode", ["parallel", "sequential"])
def test_batch_children_carry_the_origin_of_the_batch_call(mode, monkeypatch):
    # ``BatchRuntime.run`` creates its children inside the request's scope, so they would inherit the
    # origin by themselves and the test would pass without the explicit ``request_origin.set`` in
    # ``_child``. Launch them in an EMPTY context instead (what an executor or a detached task gives):
    # only the explicit set can then supply the origin.
    def launch_in_an_empty_context(self, state, call):
        child = asyncio.create_task(self._child(state, call), context=contextvars.Context())
        state.children.add(child)
        self.children.add(child)
        child.add_done_callback(state.children.discard)
        child.add_done_callback(self.children.discard)
        child.add_done_callback(batch_module._consume)

    monkeypatch.setattr(BatchRuntime, "_launch", launch_in_an_empty_context)

    async def scenario():
        seen = []

        async def execute(tool, session_id, *, on_start):
            on_start()
            await asyncio.sleep(0)
            seen.append(current_origin())
            return {"exit_code": 0, "result": None, "error": None}

        runtime = BatchRuntime(execute=execute, check_grant=_valid)
        try:
            with origin_scope(mcp_session_origin("sess-A")):
                await runtime.run(_batch(mode=mode), session_id="sess-A")
        finally:
            await runtime.close()
        return seen

    assert asyncio.run(scenario()) == [mcp_session_origin("sess-A")] * 3


# --- The real endpoint ------------------------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def fresh_managers(monkeypatch):
    for name in ("_session_manager", "_external_manager"):
        monkeypatch.setattr(f"twicc.mcp.server.{name}", None)
    yield
    for name in ("_session_manager", "_external_manager"):
        monkeypatch.setattr(f"twicc.mcp.server.{name}", None)


def test_authenticate_returns_the_session_or_the_token_id(monkeypatch):
    monkeypatch.setattr(
        "twicc.mcp.endpoint.verify_token",
        lambda token: TokenRecord(id="tok_7", name="t", digest="x", created_at="", last_used_at=None)
        if token == "good" else None,
    )

    def scope(token):
        return {"headers": [(b"authorization", f"Bearer {token}".encode())]}

    assert _authenticate(scope(mint_session_token("sess-A"))) == ("sess-A", None)
    assert _authenticate(scope("good")) == (None, "tok_7")
    assert _authenticate(scope("bad")) is None
    assert _authenticate(scope(identity.TOKEN_PREFIX + "forged.signature")) is None
    assert _authenticate({"headers": []}) is None


def test_the_endpoint_publishes_the_token_id_and_the_tool_runs_under_its_origin(fresh_managers, monkeypatch):
    monkeypatch.setattr("twicc.mcp.endpoint.verify_token", lambda token: TokenRecord(
        id="tok_7", name="t", digest="x", created_at="", last_used_at=None) if token == "good" else None)
    seen = []

    def fake_run_invoke(argv):
        seen.append(current_origin())
        return InvocationResult(exit_code=0, result=[], error=None)

    monkeypatch.setattr(server, "_run_invoke", fake_run_invoke)
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream",
               "authorization": "Bearer good"}

    async def scenario():
        async with mcp_lifespan():
            transport = httpx.ASGITransport(app=handle_mcp, client=("127.0.0.1", 9999))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "workspaces", "arguments": {}},
                })
                return response.status_code

    assert asyncio.run(scenario()) == 200
    assert seen == [mcp_token_origin("tok_7")]
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_entry.py -q`
Expected: `ImportError: cannot import name '_authenticate' from 'twicc.mcp.endpoint'` (collection error; the endpoint import comes first in the file).

- [ ] **Step 3: RPC — publish the token id and set the origin**

In `src/twicc/auth/middleware.py`, `RpcTokenAuthMiddleware.__call__`:

Old:
```python
        if not request.path.startswith("/rpc/"):
            return await self.get_response(request)

        password_set = bool(settings.TWICC_PASSWORD_HASH)
```
New:
```python
        if not request.path.startswith("/rpc/"):
            return await self.get_response(request)

        # The id of the API token record behind this call, for the timeline origin (design 2026-10-14,
        # section 5). None for the unprotected pass-through and for the read-only cookie scope.
        request.rpc_token_id = None
        password_set = bool(settings.TWICC_PASSWORD_HASH)
```
and in the token branch:

Old:
```python
            api_tokens.note_used(record.id)
            request.rpc_scope = RPC_SCOPE_FULL
```
New:
```python
            api_tokens.note_used(record.id)
            request.rpc_token_id = record.id
            request.rpc_scope = RPC_SCOPE_FULL
```

In `src/twicc/rpc/views.py` add `from twicc.request_origin import origin_scope, rpc_origin` to the imports, and in `dispatch` replace

Old:
```python
    loop = asyncio.get_running_loop()
    token = transport.backend_loop.set(loop)
    try:
        result = await asyncio.to_thread(_run_invoke, argv)
```
New:
```python
    loop = asyncio.get_running_loop()
    token = transport.backend_loop.set(loop)
    # The caller is the API token behind the call (None without a token record). A read-only cookie
    # call writes no event, so it sets no origin. ``to_thread`` copies the context, and backend-mode
    # ``submit`` forwards the carrier into the drop payload.
    origin = None if scope == RPC_SCOPE_READ else rpc_origin(getattr(request, "rpc_token_id", None))
    try:
        with origin_scope(origin):
            result = await asyncio.to_thread(_run_invoke, argv)
```
(the `except` and `finally` clauses below are unchanged).

- [ ] **Step 4: MCP — identity, endpoint, server, batch**

In `src/twicc/mcp/identity.py`, below `TOKEN_PREFIX`, add:

```python
#: ASGI-scope key under which the endpoint publishes the id of the API token that authenticated a
#: call (``None`` for a session token). Read by ``server._origin_of_call`` for the timeline origin.
TOKEN_ID_SCOPE_KEY = "twicc_mcp_token_id"
```

In `src/twicc/mcp/endpoint.py`: add `from typing import NamedTuple` and `TOKEN_ID_SCOPE_KEY` to the `twicc.mcp.identity` import, then replace `_authorized` with:

```python
class McpAuth(NamedTuple):
    """Who a local MCP request is: a session (session token) or an API token (its record id)."""

    session_id: str | None
    token_id: str | None


def _authenticate(scope) -> McpAuth | None:
    token = _bearer(scope)
    if token.startswith(TOKEN_PREFIX):
        session_id = resolve_session_token(token)
        return McpAuth(session_id, None) if session_id is not None else None
    if token:
        record = verify_token(token)
        return McpAuth(None, record.id) if record is not None else None
    return None
```
and in `handle_mcp` replace the block

Old:
```python
    # ``_authorized`` reads the token/secret files; keep that off the event loop,
    # matching the /rpc/ auth middleware's sync_to_async convention.
    auth_started = monotonic()
    if not await asyncio.to_thread(_authorized, scope):
```
New:
```python
    # ``_authenticate`` reads the token/secret files; keep that off the event loop,
    # matching the /rpc/ auth middleware's sync_to_async convention.
    auth_started = monotonic()
    auth = await asyncio.to_thread(_authenticate, scope)
    if auth is None:
```
and after the line `scope["twicc_mcp_auth_ms"] = (monotonic() - auth_started) * 1000` of the local branch (the second occurrence, just before the comment `# The session manager expects to own the path`), add:

```python
    scope[TOKEN_ID_SCOPE_KEY] = auth.token_id
```

In `src/twicc/mcp/server.py`:
1. Import: `from twicc.mcp.identity import resolve_session_token, external_caller, batch_correlation, TOKEN_ID_SCOPE_KEY` and `from twicc.request_origin import RequestOrigin, mcp_external_origin, mcp_session_origin, mcp_token_origin, origin_scope`.
2. Add before `_call_tool`:

```python
def _origin_of_call(ctx: ServerRequestContext, session_id: str | None) -> RequestOrigin | None:
    """Origin of one MCP call (design 2026-10-14, sections 4-5).

    An external connection first (``external_caller`` is set by the endpoint, it is never a CLI call),
    then the calling session (per-session token), then the API token behind the call. ``None`` when
    the call is not identified: the default applies.
    """
    caller = external_caller.get()
    if caller is not None:
        return mcp_external_origin(caller.connection_id, caller.name)
    if session_id is not None:
        return mcp_session_origin(session_id)
    request = getattr(ctx, "request", None)
    token_id = request.scope.get(TOKEN_ID_SCOPE_KEY) if request is not None else None
    return mcp_token_origin(token_id) if token_id else None
```
3. Replace the head of `_call_tool`:

Old:
```python
async def _call_tool(
    ctx: ServerRequestContext,
    params: mcp_types.CallToolRequestParams,
) -> mcp_types.CallToolResult:
    name, arguments = params.name, params.arguments or {}
    session_id = _session_id_from_request(ctx)
    if name in BATCH_NAMES:
```
New:
```python
async def _call_tool(
    ctx: ServerRequestContext,
    params: mcp_types.CallToolRequestParams,
) -> mcp_types.CallToolResult:
    session_id = _session_id_from_request(ctx)
    # The whole call, a batch included, runs under the caller's origin; batch children copy it
    # explicitly (``BatchRuntime``), they are created outside this task's context.
    with origin_scope(_origin_of_call(ctx, session_id)):
        return await _call_tool_in_origin(ctx, params, session_id)


async def _call_tool_in_origin(
    ctx: ServerRequestContext,
    params: mcp_types.CallToolRequestParams,
    session_id: str | None,
) -> mcp_types.CallToolResult:
    name, arguments = params.name, params.arguments or {}
    if name in BATCH_NAMES:
```
(the rest of the old `_call_tool` body stays as the body of `_call_tool_in_origin`).

In `src/twicc/mcp/batch.py`: import `from twicc.request_origin import request_origin` ; in `_Run.__init__` add `self.origin = request_origin.get()` after `self.grant = external_grant.get()`; in `_child`, add to the `tokens` list:

```python
            (request_origin, request_origin.set(state.origin)),
```
(placed right after the `forced_session_id` entry).

- [ ] **Step 5: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_entry.py tests/test_mcp_endpoint.py tests/test_mcp_dispatch.py tests/test_mcp_batch_runtime.py tests/test_mcp_batch_external.py tests/test_mcp_external.py tests/test_rpc_auth.py -q`
Expected: all pass. (If an existing MCP/RPC test fails because it imported `_authorized`, it is a rename to `_authenticate`; `grep -rn "_authorized" tests` found none when this plan was written.)

**Mutation check (do not commit).** (1) Delete `(request_origin, request_origin.set(state.origin))` from `BatchRuntime._child`: both cases of `test_batch_children_carry_the_origin_of_the_batch_call` FAIL (the test launches children in an empty context, see its comment). (2) Delete `with origin_scope(...)` in `_call_tool`: all `test_..._gives_...`, the external tests and `test_the_call_runs_under_its_origin_and_restores_the_previous_one` FAIL (no ambient origin matches the call's). (3) Delete `with origin_scope(origin):` in `rpc.views.dispatch`: the two RPC token tests FAIL.

- [ ] **Step 6: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/auth/middleware.py src/twicc/rpc/views.py src/twicc/mcp/identity.py src/twicc/mcp/endpoint.py src/twicc/mcp/server.py src/twicc/mcp/batch.py tests/test_timeline_origin_entry.py`
Expected: no finding on lines written by this task.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/auth/middleware.py src/twicc/rpc/views.py src/twicc/mcp/identity.py src/twicc/mcp/endpoint.py src/twicc/mcp/server.py src/twicc/mcp/batch.py tests/test_timeline_origin_entry.py && git commit -m "$(cat <<'EOF'
feat(timeline): set the origin on the RPC and MCP entry points

The RPC middleware publishes the API token id (request.rpc_token_id) and
dispatch runs the command under rpc_origin(token id), or none for the
read-only cookie scope. The local MCP endpoint now returns who the caller is
(a session or an API token id) and publishes the token id in the ASGI scope.
_call_tool derives the origin (external connection, calling session, API
token) and holds it for the whole call; batch children copy the origin of the
batch call because they are created outside the request task.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Drop requests carry the origin (CLI side, transport, watcher)

**Files:**
- Create: `src/twicc/cli/_drop_request/origin.py`
- Modify: `src/twicc/cli/_drop_request/transport.py` (`submit`)
- Modify: `src/twicc/drop_requests_watcher.py` (`execute_drop_payload`)
- Modify: `tests/timeline_helpers.py` (recording drop service)
- Create: `tests/test_timeline_origin_drop.py`

**Interfaces:**
- Consumes (T1): `ORIGIN_PAYLOAD_KEY`, `RequestOrigin`, `current_origin`, `origin_scope`, `origin_to_payload`, `origin_from_payload`, `agent_cli_origin`, `human_cli_origin`.
- Produces:
  - `twicc.cli._drop_request.origin.cli_origin() -> RequestOrigin` (cached per process) and `reset_cli_origin_cache()` (tests).
  - `transport.submit(payload, *, kind)` adds `payload["_origin"]` for EVERY kind: in a real CLI process the cached `cli_origin()`; in backend mode (`backend_loop` set: MCP, RPC) the carrier unchanged, and nothing when the carrier is unset. The process-ancestry walk never runs in the backend.
  - `execute_drop_payload(payload, kind)` pops `_origin` (the caller's dict is not mutated, the handler never sees the key), re-establishes it for the duration of the handler, and runs the handler with an explicit unset when the key is absent or not credible (so the default applies and a stale carrier cannot leak).
  - `tests/timeline_helpers.RECORDED` and `recording_service(payload)`: a fake drop-kind handler recording `(payload, current_origin())`.

- [ ] **Step 1: Add the recording service to the test helpers**

In `tests/timeline_helpers.py` add the imports `from types import SimpleNamespace` and `from twicc.request_origin import current_origin` (merge with the existing `twicc.request_origin` import), then append:

```python
#: ``(payload, origin)`` pairs recorded by :func:`recording_service`.
RECORDED: list = []


async def recording_service(payload):
    """A drop-kind handler that records the payload it receives and the origin it runs under."""
    RECORDED.append((dict(payload), current_origin()))
    return SimpleNamespace(success=True, errors=None)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_timeline_origin_drop.py`:

```python
"""Drop requests carry the origin from one common place (design section 6.2; verification 3, 3a)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.timeline_helpers import RECORDED
from twicc.cli._drop_request import transport
from twicc.cli._drop_request.origin import cli_origin, reset_cli_origin_cache
from twicc.drop_requests_watcher import _KIND_HANDLERS, execute_drop_payload
from twicc.request_origin import (
    ORIGIN_PAYLOAD_KEY, agent_cli_origin, current_origin, human_cli_origin, mcp_session_origin, origin_scope,
    origin_to_payload,
)

KINDS = sorted(_KIND_HANDLERS)


@pytest.fixture(autouse=True)
def clean_state():
    RECORDED.clear()
    reset_cli_origin_cache()
    yield
    RECORDED.clear()
    reset_cli_origin_cache()


def _use_recording_service(monkeypatch, kind):
    monkeypatch.setitem(_KIND_HANDLERS, kind, ("tests.timeline_helpers", "recording_service", "updated"))


# --- CLI side -----------------------------------------------------------------------------------------------


def test_local_mode_writes_the_cli_origin_into_the_payload_of_every_kind(monkeypatch):
    written = []

    def fake_write(payload, kind):
        written.append((payload, kind))
        return SimpleNamespace(request_uuid="u1", path=Path("/tmp/u1.json"))

    monkeypatch.setattr(transport, "write_drop_file", fake_write)
    monkeypatch.setattr(transport, "cli_origin", lambda: agent_cli_origin("sess-A"))
    for kind in KINDS:
        transport.submit({"session_id": "s1"}, kind=kind)
    assert [kind for _, kind in written] == KINDS
    expected = {"actor": "agent", "via": "cli", "actor_ref": "sess-A", "actor_name": None}
    assert all(payload[ORIGIN_PAYLOAD_KEY] == expected and payload["session_id"] == "s1" for payload, _ in written)


def test_backend_mode_submit_carries_the_origin_through_the_loop(monkeypatch):
    seen = []

    async def fake_execute(payload, kind):
        seen.append((payload, current_origin()))
        return {"status": "updated"}

    def forbidden():
        raise AssertionError("the process ancestry must not be walked inside the backend")

    monkeypatch.setattr("twicc.drop_requests_watcher.execute_drop_payload", fake_execute)
    monkeypatch.setattr(transport, "cli_origin", forbidden)

    async def scenario():
        loop = asyncio.get_running_loop()

        def worker():
            token = transport.backend_loop.set(loop)
            try:
                with origin_scope(mcp_session_origin("sess-A")):
                    return transport.submit({"session_id": "s1"}, kind="session:update_title")
            finally:
                transport.backend_loop.reset(token)

        submission = await asyncio.to_thread(worker)
        return await asyncio.wrap_future(submission._future)

    assert asyncio.run(scenario()) == {"status": "updated"}
    [(payload, visible)] = seen
    assert payload[ORIGIN_PAYLOAD_KEY]["actor_ref"] == "sess-A" and payload[ORIGIN_PAYLOAD_KEY]["via"] == "mcp_internal"
    assert visible == mcp_session_origin("sess-A")  # the carrier also crossed run_coroutine_threadsafe


def test_backend_mode_with_no_origin_adds_no_key(monkeypatch):
    seen = []

    async def fake_execute(payload, kind):
        seen.append(payload)
        return {"status": "updated"}

    monkeypatch.setattr("twicc.drop_requests_watcher.execute_drop_payload", fake_execute)

    async def scenario():
        loop = asyncio.get_running_loop()

        def worker():
            token = transport.backend_loop.set(loop)
            try:
                return transport.submit({"session_id": "s1"}, kind="session:update_title")
            finally:
                transport.backend_loop.reset(token)

        await asyncio.wrap_future((await asyncio.to_thread(worker))._future)

    asyncio.run(scenario())
    assert ORIGIN_PAYLOAD_KEY not in seen[0]


def test_cli_origin_is_an_agent_when_the_ancestry_resolves(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: SimpleNamespace(id="sess-A"),
    )
    assert cli_origin() == agent_cli_origin("sess-A")


def test_cli_origin_is_a_human_without_a_session_in_the_ancestry(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    assert cli_origin() == human_cli_origin()


def test_cli_origin_falls_back_to_a_human_when_the_lookup_fails(monkeypatch):
    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", boom)
    assert cli_origin() == human_cli_origin()


def test_cli_origin_is_resolved_once_per_process(monkeypatch):
    calls = []

    def lookup():
        calls.append(1)
        return SimpleNamespace(id="sess-A")

    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lookup)
    assert cli_origin() == cli_origin() == agent_cli_origin("sess-A")
    assert len(calls) == 1


# --- Watcher side ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_gets_its_origin_re_established_and_never_sees_the_key(kind, monkeypatch):
    _use_recording_service(monkeypatch, kind)
    origin = agent_cli_origin("sess-A")
    payload = {"kind": kind, "session_id": "s1", ORIGIN_PAYLOAD_KEY: origin_to_payload(origin)}
    assert asyncio.run(execute_drop_payload(payload, kind))["status"] == "updated"
    [(seen_payload, seen_origin)] = RECORDED
    assert seen_origin == origin
    assert ORIGIN_PAYLOAD_KEY not in seen_payload and seen_payload["session_id"] == "s1"
    assert ORIGIN_PAYLOAD_KEY in payload  # the caller's dict is left alone


@pytest.mark.parametrize("kind", KINDS)
def test_a_payload_without_origin_runs_unset_even_inside_a_stale_carrier(kind, monkeypatch):
    _use_recording_service(monkeypatch, kind)

    async def scenario():
        with origin_scope(mcp_session_origin("stale")):
            return await execute_drop_payload({"kind": kind, "session_id": "s1"}, kind)

    asyncio.run(scenario())
    assert RECORDED[0][1] is None  # unset: the default user/ui applies downstream


@pytest.mark.parametrize("raw", [
    {"actor": "system", "via": "ui"}, {"actor": "root", "via": "cli"}, "ui", ["user"], {"actor": "user", "via": 3},
])
def test_a_forged_or_malformed_origin_degrades_to_unset(raw, monkeypatch):
    _use_recording_service(monkeypatch, "session:update_title")
    payload = {"kind": "session:update_title", "session_id": "s1", ORIGIN_PAYLOAD_KEY: raw}
    asyncio.run(execute_drop_payload(payload, "session:update_title"))
    [(seen_payload, seen_origin)] = RECORDED
    assert seen_origin is None and ORIGIN_PAYLOAD_KEY not in seen_payload
```

- [ ] **Step 3: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_drop.py -q`
Expected: `ModuleNotFoundError: No module named 'twicc.cli._drop_request.origin'`.

- [ ] **Step 4: Write the CLI origin resolution**

Create `src/twicc/cli/_drop_request/origin.py`:

```python
"""The origin of THIS CLI process (design 2026-10-14, sections 5-6).

Resolved once per process through the existing PID-ancestry lookup (``resolve_current_session``): a hit
means an agent session runs the CLI (``agent`` / ``cli`` / its session id), no hit means a human
(``user`` / ``cli``). Used only in a real CLI process; the backend never walks its own ancestry.
"""

from __future__ import annotations

import logging

from twicc.request_origin import RequestOrigin, agent_cli_origin, human_cli_origin

logger = logging.getLogger(__name__)

_cached: RequestOrigin | None = None


def reset_cli_origin_cache() -> None:
    """Forget the resolved origin (tests only: a real process resolves once)."""
    global _cached
    _cached = None


def cli_origin() -> RequestOrigin:
    """The origin of this CLI process, resolved on first use. Never raises."""
    global _cached
    if _cached is None:
        _cached = _resolve()
    return _cached


def _resolve() -> RequestOrigin:
    try:
        import django
        from django.apps import apps

        if not apps.ready:
            django.setup()
        from twicc.cli._drop_request.whoami import resolve_current_session

        session = resolve_current_session()
    except Exception:
        logger.debug("CLI origin: the caller lookup failed; recorded as a human call", exc_info=True)
        return human_cli_origin()
    return human_cli_origin() if session is None else agent_cli_origin(session.id)
```

- [ ] **Step 5: Add the origin in the one common place — `transport.submit`**

In `src/twicc/cli/_drop_request/transport.py` add the imports

```python
from twicc.cli._drop_request.origin import cli_origin
from twicc.request_origin import ORIGIN_PAYLOAD_KEY, RequestOrigin, current_origin, origin_to_payload
```
and replace the head of `submit`:

Old:
```python
def submit(payload: dict, *, kind: str) -> Submission:
    """Submit one request in the active mode."""
    loop = backend_loop.get()
```
New:
```python
def _origin_to_forward() -> RequestOrigin | None:
    """The origin written into every payload (design 2026-10-14, section 6.2).

    In a real CLI process: its own origin (one ancestry lookup per process). In backend mode (MCP, RPC,
    the entry point already set the carrier): the carrier, unchanged and never re-resolved, so an
    external-MCP call never becomes ``user`` / ``cli`` and the backend never walks its own ancestry.
    """
    if _in_backend():
        return current_origin()
    return cli_origin()


def submit(payload: dict, *, kind: str) -> Submission:
    """Submit one request in the active mode. The origin is added here, once, for every kind."""
    origin = _origin_to_forward()
    if origin is not None:
        payload = {**payload, ORIGIN_PAYLOAD_KEY: origin_to_payload(origin)}
    loop = backend_loop.get()
```
(the rest of `submit` is unchanged).

- [ ] **Step 6: Re-establish the origin in the watcher**

In `src/twicc/drop_requests_watcher.py` add `from twicc.request_origin import ORIGIN_PAYLOAD_KEY, origin_from_payload, origin_scope` to the imports, and in `execute_drop_payload` replace

Old:
```python
    module_path, attr_name, success_status = handler
    service = getattr(importlib.import_module(module_path), attr_name)
    try:
        result = await service(payload)
```
New:
```python
    module_path, attr_name, success_status = handler
    service = getattr(importlib.import_module(module_path), attr_name)
    # The origin written by the submitting side (design 2026-10-14, section 6.2) is re-established for
    # the handler and never reaches it as a payload key. A file with no (or no credible) origin runs
    # with an explicit unset, so the default applies and a stale carrier cannot leak in.
    origin = origin_from_payload(payload.get(ORIGIN_PAYLOAD_KEY))
    if ORIGIN_PAYLOAD_KEY in payload:
        payload = {key: value for key, value in payload.items() if key != ORIGIN_PAYLOAD_KEY}
    try:
        with origin_scope(origin):
            result = await service(payload)
```
(the `except` clause and the rest stay as they are).

- [ ] **Step 7: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_drop.py tests/test_drop_transport.py tests/test_drop_request_statuses.py tests/test_attachment_drop_requests.py tests/test_cli_sessions_stop.py tests/test_cli_stop_batch.py -q`
Expected: all pass. (The end-to-end "drop payload becomes an event" test needs the T6 writers and lives in T6.) (An existing CLI test that calls the real `transport.submit` in local mode now resolves `cli_origin()`; it degrades to a human origin on any failure, so none should break. If one asserts the exact payload given to `write_drop_file`, add the `_origin` key to its expectation.)

**Mutation check (do not commit).** (1) Delete the `_origin` assignment in `transport.submit`: `test_local_mode_writes_the_cli_origin_into_the_payload_of_every_kind` and `test_backend_mode_submit_carries_the_origin_through_the_loop` FAIL. (2) Delete `with origin_scope(origin):` in `execute_drop_payload`: the two parametrized watcher tests FAIL (31 cases each: the fake service sees `None`, or the stale carrier). `test_a_forged_or_malformed_origin_degrades_to_unset` does not fail here: it runs with an unset ambient carrier. (3) Stop popping the key: `..._never_sees_the_key` FAILS. In `test_backend_mode_submit...` the `visible ==` assertion only restates the propagation fact (the payload key is the real check).

- [ ] **Step 8: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/cli/_drop_request/origin.py src/twicc/cli/_drop_request/transport.py src/twicc/drop_requests_watcher.py tests/test_timeline_origin_drop.py tests/timeline_helpers.py`
Expected: no finding on lines written by this task.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/cli/_drop_request/origin.py src/twicc/cli/_drop_request/transport.py src/twicc/drop_requests_watcher.py tests/timeline_helpers.py tests/test_timeline_origin_drop.py && git commit -m "$(cat <<'EOF'
feat(timeline): carry the request origin in drop requests for every kind

transport.submit is the one place that adds the origin to a drop payload,
under the reserved "_origin" key, for every kind: a real CLI process writes
its own origin (resolved once through the PID ancestry: agent or human),
backend mode (MCP, RPC) forwards the carrier unchanged and never walks the
backend's ancestry. execute_drop_payload pops the key, re-establishes the
origin around the handler and runs with an explicit unset when it is absent,
forged (system actor) or malformed, so the default applies and no stale
carrier leaks into a file written by an old CLI.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Request-caused writers read the carrier (flags, archive, bulk, settings, hide, permission mode)

**Files:**
- Modify: `src/twicc/core/services/session_update.py`
- Modify: `src/twicc/core/services/session_visibility.py`
- Modify: `src/twicc/providers/claude_code/ws.py`
- Create: `tests/test_timeline_origin_writers.py`
- Modify: `tests/test_timeline_bulk_archive.py`

**Interfaces:**
- Consumes (T1, T2): `capture_origin`, `origin_scope`, `mcp_session_origin`, `agent_cli_origin`; `record_event_sync(actor=None)`, `EventSpec(origin=)`.
- Produces:
  - `archive_sessions_bulk(ids)` resolves ONE origin (`capture_origin()`) and gives it to every `EventSpec`.
  - `record_settings_change(session_id, before, updates, *, actor: str | None = None, reason="")` and `persist_session_settings(session_id, updates, *, actor: str | None = None, reason="")`: `actor=None` means the request origin; an explicit `actor` (system writers) still gives NULL fields.
  - `apply_session_archived_change`, `apply_session_pinned_change`, `apply_session_mute_on_user_turn_change`, `session_visibility._apply_visibility_flag` and `claude_code.ws.update_session_permission_mode` no longer hard-code `TimelineActor.USER`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_origin_writers.py`:

```python
"""Request-caused writers take the origin of the request (design section 6, writers list; verification 6, 8b)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.timeline_helpers import columns, run_with_writer
from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.session_update import (
    apply_session_archived_change, apply_session_mute_on_user_turn_change, apply_session_pinned_change,
    archive_sessions_bulk, persist_session_settings,
)
from twicc.request_origin import agent_cli_origin, mcp_session_origin, origin_scope

pytestmark = pytest.mark.django_db(transaction=True)

AGENT = mcp_session_origin("sess-A")
AGENT_COLUMNS = ("agent", "mcp_internal", "sess-A", None)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-writers", directory="/tmp/tl-origin-writers")


@pytest.fixture(autouse=True)
def no_agent_side_effects(monkeypatch):
    from twicc import terminal
    from twicc.agent import registry

    monkeypatch.setattr(registry, "get_agent_manager_registry",
                        lambda: SimpleNamespace(kill_agent=AsyncMock(), get_agent_info=lambda session_id: None))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def under(origin, factory):
    """Run ``factory()`` with the DB writer started, inside an origin scope."""

    async def runner():
        with origin_scope(origin):
            return await factory()

    return run_with_writer(runner)


def rows(session_id="s1"):
    return list(TimelineEvent.objects.filter(session_id=session_id).order_by("id"))


def test_archive_and_unarchive_take_the_request_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_archived_change(session, True))
    under(agent_cli_origin("sess-B"), lambda: apply_session_archived_change(session, False))
    started, ended = rows()
    assert (started.kind, columns(started)) == ("archived.started", AGENT_COLUMNS)
    assert (ended.kind, columns(ended)) == ("archived.ended", ("agent", "cli", "sess-B", None))


def test_archive_with_also_unpin_gives_both_rows_the_same_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_pinned_change(session, "project"))
    under(agent_cli_origin("sess-B"), lambda: apply_session_archived_change(session, True, also_unpin=True))
    pinned, archived, unpinned = rows()
    assert columns(pinned) == AGENT_COLUMNS
    assert columns(archived) == columns(unpinned) == ("agent", "cli", "sess-B", None)


def test_a_pin_scope_change_writes_both_rows_with_one_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_pinned_change(session, "project"))
    under(agent_cli_origin("sess-B"), lambda: apply_session_pinned_change(session, "all"))
    assert [columns(r) for r in rows()] == [AGENT_COLUMNS] + [("agent", "cli", "sess-B", None)] * 2


def test_mute_takes_the_request_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_mute_on_user_turn_change(session, True))
    assert columns(rows()[0]) == AGENT_COLUMNS


def test_with_no_origin_set_every_writer_gives_the_default(project):
    session = make(project)
    run_with_writer(lambda: apply_session_mute_on_user_turn_change(session, True))
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    assert {columns(r) for r in rows()} == {("user", "ui", None, None)}


def _hide_unhide(session, hide, origin):
    from twicc.core.services import session_visibility

    async def go():
        with origin_scope(origin):
            return await (session_visibility.hide_session if hide else session_visibility.unhide_session)(session)

    with patch.object(session_visibility, "_push_hidden_to_live_agent", lambda *a: None), \
            patch.object(session_visibility, "_broadcast_session_removed", AsyncMock()), \
            patch.object(session_visibility, "_broadcast_session_updated", AsyncMock()), \
            patch.object(session_visibility, "_broadcast_project_updated", AsyncMock()):
        return run_with_writer(go)


def test_hide_through_the_compute_executor_keeps_the_origin(project, monkeypatch):
    # The flip's best-effort search reindex would log a swallowed "index writer is not initialized" error;
    # ``_recompute`` does ``from twicc.search import reindex_session``, so patching the package attribute works.
    monkeypatch.setattr("twicc.search.reindex_session", lambda session_id: None)
    # The flag is written inside ``run_compute_sync`` (asgiref ``sync_to_async`` on a dedicated worker
    # thread): the carrier must survive that hop, or an agent's hide would read as the default.
    session = make(project, permission_mode="yolo", question_widget=False)
    assert _hide_unhide(session, True, AGENT).success
    assert _hide_unhide(session, False, agent_cli_origin("sess-B")).success
    hidden, unhidden = rows()
    assert (hidden.kind, columns(hidden)) == ("hidden.started", AGENT_COLUMNS)
    assert (unhidden.kind, columns(unhidden)) == ("hidden.ended", ("agent", "cli", "sess-B", None))


def test_settings_default_to_the_request_origin(project):
    make(project, selected_model="sonnet")
    with origin_scope(AGENT):
        persist_session_settings("s1", {"selected_model": "opus"})
    assert columns(rows()[0]) == AGENT_COLUMNS


def test_settings_with_an_explicit_system_actor_stay_system_with_null_fields(project):
    make(project, selected_model="sonnet")
    with origin_scope(AGENT):
        persist_session_settings("s1", {"selected_model": "opus"}, actor="system", reason="model_retired")
    assert columns(rows()[0]) == ("system", None, None, None)


def test_the_retire_job_stays_system_with_null_fields(project):
    from twicc.core.enums import Provider
    from twicc.providers import db_writer

    make(project, selected_model="old-model")
    job = db_writer._RetireSessionsJob(
        provider=Provider.CODEX, updates={"s1": {"selected_model": "new-model"}}, future=None,
    )
    with origin_scope(AGENT):
        db_writer._apply_retire_sessions_job(job)
    assert columns(rows()[0]) == ("system", None, None, None)


def test_the_permission_mode_suggestion_takes_the_request_origin(project):
    from twicc.providers.claude_code.ws import update_session_permission_mode

    make(project, permission_mode="default", hidden=True)  # hidden: no broadcast needed
    under(AGENT, lambda: update_session_permission_mode("s1", "acceptEdits"))
    row = rows()[0]
    assert row.reason == "set_mode" and columns(row) == AGENT_COLUMNS


def test_the_bulk_archive_writes_one_origin_on_every_row(project):
    for sid in ("a", "b", "c"):
        make(project, sid)
    with origin_scope(AGENT):
        flipped = archive_sessions_bulk({"a", "b", "c"})
    assert sorted(flipped) == ["a", "b", "c"]
    got = TimelineEvent.objects.filter(kind="archived.started")
    assert got.count() == 3 and {columns(r) for r in got} == {AGENT_COLUMNS}


def test_the_bulk_archive_without_a_carrier_writes_the_default_on_every_row(project):
    for sid in ("a", "b"):
        make(project, sid)
    archive_sessions_bulk({"a", "b"})
    assert {columns(r) for r in TimelineEvent.objects.filter(kind="archived.started")} == {("user", "ui", None, None)}
```

Append to the same file a helper and a test that need the T5 drop path and the T6 writers (add the imports `import asyncio`, `from twicc.core.enums import Provider`, `from twicc.core.models import SessionType`, `from twicc.drop_requests_watcher import execute_drop_payload`, and `ORIGIN_PAYLOAD_KEY, human_cli_origin, origin_to_payload` to the existing `twicc.request_origin` import):

```python
async def _passthrough_db_write(coro_factory):
    return await coro_factory()


@pytest.mark.parametrize(("origin_key", "expected"), [
    (agent_cli_origin("boss"), ("agent", "cli", "boss", None)),
    (human_cli_origin(), ("user", "cli", None, None)),
    (None, ("user", "ui", None, None)),  # a drop file from an old CLI: the default
])
def test_a_drop_payload_writes_its_origin_into_the_event(project, origin_key, expected):
    session = Session.objects.create(
        id="drop-session", project=project, provider=Provider.CODEX.value, type=SessionType.SESSION,
    )
    payload = {"session_id": session.id, "mute_on_user_turn": True}
    if origin_key is not None:
        payload[ORIGIN_PAYLOAD_KEY] = origin_to_payload(origin_key)
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.core.services.session_update.get_channel_layer", return_value=layer), \
            patch("twicc.core.services.session_update.ensure_provider_running"), \
            patch("twicc.core.services.session_update.run_under_db_write_lock", side_effect=_passthrough_db_write):
        status = asyncio.run(execute_drop_payload(payload, "session:update_mute_on_user_turn"))
    assert status["status"] == "updated"
    assert columns(TimelineEvent.objects.get(session_id=session.id, kind="muted.started")) == expected
```

In `tests/test_timeline_bulk_archive.py`, in `test_the_bulk_archive_endpoint_records_one_user_event_per_archived_session`, replace the line
```python
    assert all(r.actor == "user" and r.reason == "" for r in got.values())
```
with
```python
    assert all(
        (r.actor, r.via, r.actor_ref, r.actor_name) == ("user", "ui", None, None) and r.reason == ""
        for r in got.values()
    )
```
(Before this task the endpoint rows have `via` NULL, because `archive_sessions_bulk` builds `EventSpec(actor=TimelineActor.USER)`; after it they carry the middleware's UI origin. Add `tests/test_timeline_bulk_archive.py` to this task's `git add` list.)

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_writers.py tests/test_timeline_bulk_archive.py -q`
Expected: failures such as `assert ('user', None, None, None) == ('agent', 'mcp_internal', 'sess-A', None)` (the writers still hard-code `user`, so `via` is NULL).

- [ ] **Step 3: Edit `session_update.py`**

1. Imports: change `from twicc.core.models import TimelineActor, TimelineKind` to `from twicc.core.models import TimelineKind` and add `from twicc.request_origin import capture_origin`.
2. `archive_sessions_bulk`: after `if not ids: return []` add

```python
    # One origin for the whole batch (design section 6, requirement 4): resolved once, here, in the
    # awaited chain of the request, then given to every row.
    origin = capture_origin()
```
and replace the `EventSpec(...)` of the list with
```python
            EventSpec(TimelineKind.ARCHIVED_STARTED, session_id=session_id, project_id=project_id, origin=origin)
```
3. `record_settings_change(session_id, before, updates, *, actor: str, reason: str = "")` -> `*, actor: str | None = None, reason: str = ""` and extend its docstring with: ``actor=None`` means a request-caused change (the carrier, else the default); an explicit actor (system writers) leaves the origin fields NULL.``
4. `persist_session_settings(session_id, updates, *, actor: str = TimelineActor.USER, reason: str = "")` -> `*, actor: str | None = None, reason: str = ""`.
5. `apply_session_archived_change`: delete `, actor=TimelineActor.USER` from both `record_event_sync` calls.
6. `apply_session_pinned_change`: delete `"actor": TimelineActor.USER,` from the `common` dict.
7. `apply_session_mute_on_user_turn_change`: delete `, actor=TimelineActor.USER` from the call.

- [ ] **Step 4: Edit `session_visibility.py` and `ws.py`**

`src/twicc/core/services/session_visibility.py`, `_apply_visibility_flag`: change the local import to `from twicc.core.models import Session, TimelineKind` and delete `, actor=TimelineActor.USER` from the `record_event_sync` call.

`src/twicc/providers/claude_code/ws.py`, `update_session_permission_mode`: change the local import to `from twicc.core.models import Session` and replace
```python
                    actor=TimelineActor.USER, reason="set_mode",
```
with
```python
                    reason="set_mode",
```

- [ ] **Step 5: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_writers.py tests/test_timeline_flag_hooks.py tests/test_timeline_bulk_archive.py tests/test_timeline_title_settings.py -q`
Expected: all pass.

**Mutation check (do not commit).** (1) In `archive_sessions_bulk` remove `origin=origin` from the `EventSpec`: `test_the_bulk_archive_writes_one_origin_on_every_row` FAILS (`system` columns). (2) In `_apply_visibility_flag` restore `actor=TimelineActor.USER`: `test_hide_through_the_compute_executor_keeps_the_origin` FAILS (NULL `via`). (3) Restore the hard-coded actor in `apply_session_mute_on_user_turn_change`: `test_mute_takes_the_request_origin` and the drop end-to-end test FAIL. The ambient carrier is unset in these tests, so nothing is inherited by accident.

- [ ] **Step 6: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/core/services/session_update.py src/twicc/core/services/session_visibility.py src/twicc/providers/claude_code/ws.py tests/test_timeline_origin_writers.py`
Expected: no finding on lines written by this task (an unused `TimelineActor` import is a finding: remove it).

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/services/session_update.py src/twicc/core/services/session_visibility.py src/twicc/providers/claude_code/ws.py tests/test_timeline_origin_writers.py tests/test_timeline_bulk_archive.py && git commit -m "$(cat <<'EOF'
feat(timeline): attribute flag, archive and settings events to the request origin

Archive, unarchive, pin (scope changes included), mute, hide, unhide, the
settings default and the Claude permission-mode suggestion no longer
hard-code actor=user: they read the request origin, so a call made by an
agent through the CLI or MCP, an API token or an external client is
recorded as such. The bulk archive resolves one origin and writes it on
every row. System writers (model retirement, hybrid switch) keep their
explicit system actor and NULL origin fields.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Titles and created sessions (captured origins, Codex re-key)

**Files:**
- Modify: `src/twicc/pending_titles.py`
- Modify: `src/twicc/pending_session_attributes.py`
- Modify: `src/twicc/core/services/session_creation.py`
- Modify: `src/twicc/core/services/session_timeline.py` (`record_session_created`, `record_title_change`)
- Modify: `src/twicc/agent/base_manager.py` (Codex re-key block, `_try_flush_pending_title`)
- Modify: `src/twicc/providers/sessions_watcher.py` (`create_session_sync`)
- Create: `tests/test_timeline_origin_titles_creation.py`

**Interfaces:**
- Consumes (T1, T2): `RequestOrigin`, `DEFAULT_ORIGIN`, `capture_origin`, `origin_scope`; `record_event_sync(actor=None, origin=...)`.
- Produces:
  - `pending_titles.set_pending_title(session_id, title, origin: RequestOrigin | None = None)`; `get_pending_title_origin(session_id) -> RequestOrigin | None`; `pop_pending_title(session_id)` (also drops the origin); `rekey_pending_title(old_id, new_id) -> bool` (moves title and origin together).
  - `PendingSessionAttributes.origin: RequestOrigin | None = None` (last field); `set_pending_session_attributes(..., origin=None)`; `rekey_pending_session_attributes(old_id, new_id) -> bool` (moves the whole entry).
  - `record_title_change(session_id, title, *, source, old=None, origin: RequestOrigin | None = None)`: `source == "user"` -> request-caused (`origin`, else the carrier, else the default); any other source -> explicit `agent` actor, NULL origin fields.
  - `record_session_created(session, *, origin: str, creator: RequestOrigin | None = None)`: `origin == "twicc"` -> `session.created` and its flag rows take `creator`, else the default `user` / `ui`; any other `origin` -> explicit `system` actor, NULL fields.
  - `BaseAgentManager._try_flush_pending_title` records the title with the origin captured when it was stored (or the default), never the carrier.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_origin_titles_creation.py`:

```python
"""Titles and created sessions keep the origin captured with them (design 6.5, 6.6; verification 7, 7a, 7b, 7c, 8f, 8i)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from django.utils import timezone

from tests.timeline_helpers import columns, kinds, run_with_writer
from twicc.agent.base_manager import BaseAgentManager
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, Session, SessionType, TimelineEvent
from twicc.core.services.session_creation import create_session_from_payload
from twicc.core.services.session_timeline import record_session_created, record_title_change
from twicc.core.services.session_update import update_session_title_from_payload
from twicc.pending_agent_settings import pop_pending_agent_settings
from twicc.pending_session_attributes import (
    PendingSessionAttributes, get_pending_session_attributes, pop_pending_session_attributes,
    rekey_pending_session_attributes, set_pending_session_attributes,
)
from twicc.pending_titles import (
    get_pending_title, get_pending_title_origin, pop_pending_title, rekey_pending_title, set_pending_title,
)
from twicc.providers.helpers import AgentSettings
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile
from twicc.request_origin import (
    SYSTEM_ORIGIN, UI_ORIGIN, agent_cli_origin, mcp_session_origin, origin_scope,
)

pytestmark = pytest.mark.django_db(transaction=True)

AGENT = mcp_session_origin("boss")
AGENT_COLUMNS = ("agent", "mcp_internal", "boss", None)


class _Compute:
    provider = Provider.CODEX
    compute_version = 1


class _Watcher(BaseSessionsWatcher):
    def get_compute(self):
        return _Compute()


class _Manager(BaseAgentManager):
    provider = Provider.CODEX

    async def _create_agent(self, session_id, project_id, cwd, **kwargs):
        return SimpleNamespace(session_id="canonical-id", interrupt_or_kill=AsyncMock())


@pytest.fixture
def project(tmp_path):
    return Project.objects.create(id="-tl-origin-tc", directory=str(tmp_path))


@pytest.fixture(autouse=True)
def clean_pending_stores():
    yield
    for sid in ("s1", "draft-id", "canonical-id", "kid", "flagged", "created-id"):
        pop_pending_title(sid)
        pop_pending_session_attributes(sid)


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def parsed(project, sid):
    return ParsedSessionFile(project.id, sid, SessionType.SESSION, f"{sid}.jsonl")


# --- record_title_change ------------------------------------------------------------------------------------


def test_a_user_title_takes_the_carrier(project):
    make(project)
    with origin_scope(AGENT):
        record_title_change("s1", "New", source="user", old="Old")
    assert columns(TimelineEvent.objects.get(session_id="s1")) == AGENT_COLUMNS


def test_a_user_title_with_a_captured_origin_ignores_the_carrier(project):
    make(project)
    with origin_scope(SYSTEM_ORIGIN):
        record_title_change("s1", "New", source="user", old="Old", origin=AGENT)
    assert columns(TimelineEvent.objects.get(session_id="s1")) == AGENT_COLUMNS


@pytest.mark.parametrize("source", ["auto", "agent"])
def test_a_non_user_title_keeps_actor_agent_and_null_origin_fields(project, source):
    make(project)
    with origin_scope(AGENT):
        record_title_change("s1", "New", source=source, old=None)
    assert columns(TimelineEvent.objects.get(session_id="s1")) == ("agent", None, None, None)


def test_the_title_update_service_records_the_drop_origin(project):
    make(project, title="Old")
    with patch("twicc.core.services.session_update.ensure_provider_running"), origin_scope(agent_cli_origin("sess-B")):
        run_with_writer(lambda: update_session_title_from_payload({"session_id": "s1", "title": "New"}))
    assert columns(TimelineEvent.objects.get(session_id="s1")) == ("agent", "cli", "sess-B", None)


# --- The pending title and its origin -----------------------------------------------------------------------


def test_the_pending_title_keeps_its_origin_until_popped():
    set_pending_title("s1", "T", origin=AGENT)
    assert get_pending_title("s1") == "T" and get_pending_title_origin("s1") == AGENT
    assert pop_pending_title("s1") == "T"
    assert get_pending_title_origin("s1") is None


def test_setting_a_title_without_an_origin_forgets_an_older_origin():
    set_pending_title("s1", "T", origin=AGENT)
    set_pending_title("s1", "T2")
    assert get_pending_title_origin("s1") is None


def test_rekey_moves_the_title_and_its_origin_together():
    set_pending_title("draft-id", "T", origin=AGENT)
    assert rekey_pending_title("draft-id", "canonical-id") is True
    assert (get_pending_title("draft-id"), get_pending_title_origin("draft-id")) == (None, None)
    assert (get_pending_title("canonical-id"), get_pending_title_origin("canonical-id")) == ("T", AGENT)
    assert rekey_pending_title("nothing", "x") is False


class _FlushAgent:
    session_id = "s1"
    provider = Provider.CODEX


def _flush(carrier):
    manager = _Manager()
    manager._broadcast_session_updated = AsyncMock()

    async def go():
        with origin_scope(carrier):
            return await manager._try_flush_pending_title(_FlushAgent(), "Title")

    helpers = SimpleNamespace(rename_session=AsyncMock())
    with patch("twicc.providers.helpers.get_provider_helpers", return_value=helpers):
        return run_with_writer(go)


def test_a_flushed_title_records_the_captured_origin_even_inside_the_system_marker(project):
    make(project)
    set_pending_title("s1", "Title", origin=AGENT)
    assert _flush(SYSTEM_ORIGIN) is True  # the state-change funnel runs the flush under the system marker
    assert columns(TimelineEvent.objects.get(session_id="s1", kind="title.changed")) == AGENT_COLUMNS


def test_a_flushed_title_without_a_captured_origin_is_the_default_never_system(project):
    make(project)
    set_pending_title("s1", "Title")
    assert _flush(SYSTEM_ORIGIN) is True
    assert columns(TimelineEvent.objects.get(session_id="s1", kind="title.changed")) == ("user", "ui", None, None)


def test_a_flushed_title_ignores_a_different_stale_request_origin(project):
    make(project)
    set_pending_title("s1", "Title", origin=AGENT)
    assert _flush(agent_cli_origin("someone-else")) is True
    assert columns(TimelineEvent.objects.get(session_id="s1", kind="title.changed")) == AGENT_COLUMNS


# --- The Codex draft -> canonical re-key ----------------------------------------------------------------------


def test_rekey_moves_every_field_of_the_pending_attributes():
    set_pending_session_attributes(
        "draft-id", hidden=True, mute_on_user_turn=True, spawned_by_id="boss", spawn_root_id="root",
        annotations={"a": 1}, system_prompt_addendum="addendum", hybrid=True, layout={"k": 1}, ephemeral=True,
        origin=AGENT,
    )
    before = get_pending_session_attributes("draft-id")
    # Every field holds a non-default value, so a field the re-key forgot would show up below.
    assert all(getattr(before, name) not in (None, False, {}, "") for name in PendingSessionAttributes._fields)
    assert rekey_pending_session_attributes("draft-id", "canonical-id") is True
    assert get_pending_session_attributes("draft-id") is None
    assert get_pending_session_attributes("canonical-id") == before
    assert rekey_pending_session_attributes("nothing", "x") is False


def test_a_codex_start_keeps_the_captured_origins_of_the_title_and_the_creation_attributes():
    set_pending_title("draft-id", "Title", origin=AGENT)
    set_pending_session_attributes("draft-id", hidden=True, origin=AGENT)
    manager = _Manager()
    manager.notify_session_bound = AsyncMock()
    manager._register_and_start = AsyncMock()
    canonical = asyncio.run(manager._start_agent(
        "draft-id", "-tl-origin-tc", "/tmp/tl-origin-tc", "Work", False, settings=AgentSettings(),
    ))
    assert canonical == "canonical-id"
    assert get_pending_title_origin("canonical-id") == AGENT
    assert get_pending_session_attributes("canonical-id").origin == AGENT
    assert get_pending_title_origin("draft-id") is None and get_pending_session_attributes("draft-id") is None


def test_an_agent_created_codex_session_keeps_its_origin_after_the_rekey(project):
    set_pending_session_attributes("draft-id", hidden=True, origin=AGENT)
    manager = _Manager()
    manager.notify_session_bound = AsyncMock()
    manager._register_and_start = AsyncMock()
    asyncio.run(manager._start_agent(
        "draft-id", project.id, "/tmp/x", "Work", False, settings=AgentSettings(),
    ))
    now = timezone.now()
    ProcessRun.objects.create(provider="codex", session_id="canonical-id", started_at=now, last_state_change_at=now)
    _Watcher().create_session_sync(parsed(project, "canonical-id"), project)
    assert {columns(r) for r in TimelineEvent.objects.filter(session_id="canonical-id")} == {AGENT_COLUMNS}
    assert kinds("canonical-id") == ["session.created", "hidden.started"]


# --- session.created and its flag rows ------------------------------------------------------------------------


def _twicc_process(sid):
    now = timezone.now()
    ProcessRun.objects.create(provider="codex", session_id=sid, started_at=now, last_state_change_at=now)


def test_a_ui_creation_gives_user_ui_and_no_flag_rows(project):
    _twicc_process("created-id")
    set_pending_session_attributes("created-id", origin=UI_ORIGIN)
    _Watcher().create_session_sync(parsed(project, "created-id"), project)
    [row] = TimelineEvent.objects.filter(session_id="created-id")
    assert row.kind == "session.created" and columns(row) == ("user", "ui", None, None)


@pytest.mark.parametrize(("origin", "expected"), [
    (AGENT, AGENT_COLUMNS),
    (agent_cli_origin("boss"), ("agent", "cli", "boss", None)),
])
def test_a_hidden_and_muted_session_created_by_an_agent_carries_that_agent_on_every_row(project, origin, expected):
    _twicc_process("kid")
    set_pending_session_attributes("kid", hidden=True, mute_on_user_turn=True, origin=origin)
    _Watcher().create_session_sync(parsed(project, "kid"), project)
    rows = list(TimelineEvent.objects.filter(session_id="kid").order_by("id"))
    assert [r.kind for r in rows] == ["session.created", "hidden.started", "muted.started"]
    assert {columns(r) for r in rows} == {expected}
    assert rows[1].caused_by_id == rows[0].id and rows[2].caused_by_id == rows[0].id


def test_a_twicc_session_without_a_captured_origin_gets_the_default_on_every_row(project):
    _twicc_process("flagged")
    set_pending_session_attributes("flagged", hidden=True, mute_on_user_turn=True)  # no origin captured
    # The watcher runs long after the request: a stale carrier must not be read (deferred writer).
    with origin_scope(agent_cli_origin("stale")):
        _Watcher().create_session_sync(parsed(project, "flagged"), project)
    rows = TimelineEvent.objects.filter(session_id="flagged")
    assert rows.count() == 3 and {columns(r) for r in rows} == {("user", "ui", None, None)}


@pytest.mark.parametrize("data_origin", ["external", "sync"])
def test_a_session_found_without_a_twicc_request_is_system_even_with_a_creator_and_a_carrier(project, data_origin):
    session = make(project, hidden=True, mute_on_user_turn=True)
    with origin_scope(AGENT):
        record_session_created(session, origin=data_origin, creator=agent_cli_origin("boss"))
    rows = TimelineEvent.objects.filter(session_id="s1")
    assert rows.count() == 3 and {columns(r) for r in rows} == {("system", None, None, None)}


def test_an_externally_discovered_session_is_system_through_the_watcher(project):
    set_pending_session_attributes("flagged", hidden=True, origin=AGENT)  # attributes without a TwiCC process
    _Watcher().create_session_sync(parsed(project, "flagged"), project)  # no ProcessRun: data.origin "external"
    assert {columns(r) for r in TimelineEvent.objects.filter(session_id="flagged")} == {("system", None, None, None)}


def test_the_creation_service_captures_the_creating_origin(tmp_path):
    # The capture sites in ``create_session_from_payload`` (title and creation attributes) are the only
    # place the creating request is still known; the other tests stash the pending entries by hand.
    project = Project.objects.create(id="-tl-create-cap", directory=str(tmp_path))
    manager = SimpleNamespace(create_session=AsyncMock(return_value="draft-id"))
    registry = SimpleNamespace(get=lambda provider: manager)
    payload = {"session_id": "draft-id", "project_id": project.id, "provider": Provider.CODEX.value,
               "text": "Work", "title": "T", "layout": {}}

    async def go():
        with origin_scope(AGENT):
            return await create_session_from_payload(payload)

    try:
        with patch("twicc.core.services.session_creation.ensure_provider_running"), \
                patch("twicc.agent.registry.get_agent_manager_registry", return_value=registry):
            assert asyncio.run(go()).success is True
        assert get_pending_session_attributes("draft-id").origin == AGENT
        assert get_pending_title_origin("draft-id") == AGENT
    finally:
        pop_pending_agent_settings("draft-id")
        pop_pending_session_attributes("draft-id")
        pop_pending_title("draft-id")
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_titles_creation.py -q`
Expected: collection error `ImportError: cannot import name 'rekey_pending_session_attributes'`.

- [ ] **Step 3: Pending title store**

Replace the body of `src/twicc/pending_titles.py` below the module docstring with:

```python
import logging

from twicc.request_origin import RequestOrigin

logger = logging.getLogger(__name__)

# session_id -> title
_pending: dict[str, str] = {}
# session_id -> origin of the request that stored the title (design 2026-10-14, section 6.5). The flush
# runs long after that request, so it cannot read the carrier: it uses this captured origin.
_origins: dict[str, RequestOrigin] = {}


def set_pending_title(session_id: str, title: str, origin: RequestOrigin | None = None) -> None:
    """Store a pending title for a draft session (provider file not yet created).

    ``origin`` is the origin of the request that chose the title. Setting a title again replaces the
    origin too (``None`` forgets an older one).
    """
    _pending[session_id] = title
    if origin is None:
        _origins.pop(session_id, None)
    else:
        _origins[session_id] = origin
    logger.debug("Set pending title for session %s: %s", session_id, title[:50])


def get_pending_title(session_id: str) -> str | None:
    """Return the pending title for ``session_id``, or ``None`` if none pending. Does not remove it."""
    return _pending.get(session_id)


def get_pending_title_origin(session_id: str) -> RequestOrigin | None:
    """The origin captured with the pending title of ``session_id``, or ``None``. Does not remove it."""
    return _origins.get(session_id)


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove the pending title (and its origin) for ``session_id``."""
    _origins.pop(session_id, None)
    return _pending.pop(session_id, None)


def rekey_pending_title(old_id: str, new_id: str) -> bool:
    """Move the whole pending entry (title and origin) from ``old_id`` to ``new_id``.

    Used by the Codex draft -> canonical id re-key. Returns whether there was an entry to move.
    """
    origin = _origins.pop(old_id, None)
    title = _pending.pop(old_id, None)
    if title is None:
        return False
    set_pending_title(new_id, title, origin)
    return True
```
(keep the existing module docstring above it, and mention in it that a pending title carries the origin of the request that set it).

- [ ] **Step 4: Pending creation attributes**

In `src/twicc/pending_session_attributes.py`: add `from twicc.request_origin import RequestOrigin` to the imports; add the last field of `PendingSessionAttributes`:

```python
    ephemeral: bool = False
    # Origin of the creating request (design 2026-10-14, section 6.6): ``session.created`` and its
    # flag rows are written by the watcher long after the request, so they take it from here.
    origin: RequestOrigin | None = None
```
add the parameter `origin: RequestOrigin | None = None,` at the end of `set_pending_session_attributes`, pass `origin=origin,` to the `PendingSessionAttributes(...)` constructor, and add below `pop_pending_session_attributes`:

```python
def rekey_pending_session_attributes(old_id: str, new_id: str) -> bool:
    """Move the whole pending entry from ``old_id`` to ``new_id`` (Codex draft -> canonical id).

    The entry moves as one object, so a field added to :class:`PendingSessionAttributes` later cannot
    be forgotten by the re-key. Returns whether there was an entry to move.
    """
    entry = _pending.pop(old_id, None)
    if entry is None:
        return False
    _pending[new_id] = entry
    return True
```

- [ ] **Step 5: Capture the creating origin and use whole-entry re-keys**

`src/twicc/core/services/session_creation.py`: add `from twicc.request_origin import capture_origin`; change `set_pending_title(session_id, title)` to `set_pending_title(session_id, title, origin=capture_origin())` and add `origin=capture_origin(),` as the last argument of the `set_pending_session_attributes(...)` call (after `ephemeral=ephemeral,`).

`src/twicc/agent/base_manager.py`, in `_start_agent`, replace the two re-key sub-blocks (from the comment `# Same rationale for pending_titles:` to the end of the `set_pending_session_attributes(...)` call) with:

```python
                        # Same rationale for pending_titles: the creation service stored the title
                        # (with the origin of the creating request) under the draft id. Move the
                        # whole entry under the canonical id so the Codex manager's ASSISTANT_TURN
                        # flush finds it. No-op for Claude Code where draft id == canonical id.
                        from twicc.pending_titles import rekey_pending_title

                        rekey_pending_title(session_id, agent.session_id)

                        # Same rationale for pending_session_attributes (hidden, mute-on-user-turn,
                        # spawned links, system-prompt addendum, hybrid, dockable layout, creating
                        # origin): stashed under the draft id, popped by the watcher under the
                        # canonical id Codex writes into the JSONL. The whole entry moves, so a
                        # field added later cannot silently revert to its default here.
                        from twicc.pending_session_attributes import rekey_pending_session_attributes

                        rekey_pending_session_attributes(session_id, agent.session_id)
```

In `_try_flush_pending_title`, add after `session_id = agent.session_id`:

```python
        # A deferred writer never reads the carrier: it runs inside the state-change funnel (system
        # marker) or in a retry task with a stale context. Use the origin captured when the title was
        # stored, else the explicit default (design 2026-10-14, section 6.5).
        from twicc.pending_titles import get_pending_title_origin
        from twicc.request_origin import DEFAULT_ORIGIN

        origin = get_pending_title_origin(session_id) or DEFAULT_ORIGIN
```
and change the recording line to
```python
                    await asyncio.to_thread(
                        record_title_change, session_id, pending, source="user", old=old_title, origin=origin,
                    )
```

- [ ] **Step 6: The recording service and the watcher**

In `src/twicc/core/services/session_timeline.py` add `DEFAULT_ORIGIN` to the `twicc.request_origin` import, replace `record_session_created` and `record_title_change`:

```python
def record_session_created(
    session: Session, *, origin: str, creator: RequestOrigin | None = None,
) -> TimelineEvent | None:
    """Write ``session.created`` and the initial flag intervals of a new row.

    A session created already hidden or muted gets the matching ``*.started``
    rows, same ``occurred_at``, ``caused_by`` the creation event (design §5).
    ``pinned`` is never set at creation. Sync: call from worker-thread code.

    ``settings`` and ``spawned_by`` are read from the row just created (the
    stored, clamped values, not the request). ``settings`` is kept only for
    ``origin == "twicc"``; ``spawned_by`` is ``Session.spawned_by_id``.

    Who caused the rows (design 2026-10-14, section 6.6): for ``origin == "twicc"`` the creating
    request, captured with the pending creation attributes (``creator``), else the default ``user`` /
    ``ui``; for any other ``origin`` the explicit ``system`` actor with NULL origin fields. These rows are
    written long after the request, so the carrier is never read.
    """
    from twicc.providers.helpers import AgentSettings

    try:
        now = timezone.now()
        common = {"session_id": session.id, "project_id": session.project_id, "occurred_at": now}
        who = {"origin": creator or DEFAULT_ORIGIN} if origin == "twicc" else {"actor": TimelineActor.SYSTEM}
        settings = None
        if origin == "twicc":
            settings = {
                name: value for name, value in AgentSettings.from_session(session)._asdict().items()
                if value is not None
            }
        created = record_event_sync(
            TimelineKind.SESSION_CREATED,
            data={"origin": origin, "provider": session.provider, "settings": settings,
                  "spawned_by": session.spawned_by_id}, **who, **common,
        )
        if created is None:
            return None
        if session.hidden:
            record_event_sync(TimelineKind.HIDDEN_STARTED, caused_by=created, **who, **common)
        if session.mute_on_user_turn:
            record_event_sync(TimelineKind.MUTED_STARTED, caused_by=created, **who, **common)
        return created
    except Exception:
        logger.exception("Timeline creation events could not be written for session %s", session.id)
        return None


def record_title_change(
    session_id: str, title: str, *, source: str, old: str | None = None, origin: RequestOrigin | None = None,
) -> None:
    """Write ``title.changed``. ``source`` is ``"auto"``, ``"user"`` or ``"agent"``.

    ``old`` is the previous title when the caller knows it (``None`` otherwise).

    A ``user`` title is request-caused: it takes ``origin`` when the caller captured one (a deferred
    flush), else the carrier, else the default. A title from another source comes from compute, the
    watcher or a Codex job, not from a request: actor ``agent``, NULL origin fields.

    Subagent sessions get no event (decision D2): the compute fills titles for
    every session it processes, subagents included.
    """
    try:
        row = Session.objects.filter(id=session_id).values_list("project_id", "type").first()
        if row is None or row[1] == SessionType.SUBAGENT:
            return
        who = {"origin": origin} if source == "user" else {"actor": TimelineActor.AGENT}
        record_event_sync(
            TimelineKind.TITLE_CHANGED, session_id=session_id, project_id=row[0],
            data={"source": source, "new": title, "old": old}, **who,
        )
    except Exception:
        logger.exception("Timeline title change could not be written for session %s", session_id)
```
(For a user title with `origin=None`, `who = {"origin": None}` -> `record_event_sync(origin=None, actor=None)` -> carrier -> default.)

In `src/twicc/providers/sessions_watcher.py`, `create_session_sync`, replace

Old:
```python
        origin = "twicc" if ProcessRun.objects.filter(session_id=session.id).exists() else "external"
        record_session_created(session, origin=origin)
        return session
```
New:
```python
        origin = "twicc" if ProcessRun.objects.filter(session_id=session.id).exists() else "external"
        # The creating request travels with the pending attributes: this runs long after it.
        record_session_created(session, origin=origin, creator=pending.origin if pending is not None else None)
        return session
```

- [ ] **Step 7: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_titles_creation.py tests/test_timeline_session_created.py tests/test_timeline_title_settings.py tests/test_mute_on_user_turn_creation.py tests/test_title_state.py tests/test_title_echo.py tests/test_ephemeral_creation.py tests/test_ephemeral_lifecycle.py -q`
Expected: all pass.

**Mutation check (do not commit).** (1) In `_try_flush_pending_title` drop `origin=origin` from the `record_title_change` call: `test_a_flushed_title_records_the_captured_origin_even_inside_the_system_marker` and `..._ignores_a_different_stale_request_origin` FAIL (the carrier is the system marker / another agent, never the captured one). (2) Replace `rekey_pending_session_attributes(session_id, agent.session_id)` by nothing: the two Codex tests FAIL. (3) Drop `creator=...` from the watcher call: the agent-origin creation tests FAIL. (4) In `create_session_from_payload` delete `origin=capture_origin()` from `set_pending_title(...)`, or from `set_pending_session_attributes(...)` (two separate mutations): `test_the_creation_service_captures_the_creating_origin` FAILS on the matching assertion each time. (5) Change `{"origin": creator or DEFAULT_ORIGIN}` to `{"origin": creator}` in `record_session_created`: `test_a_twicc_session_without_a_captured_origin_gets_the_default_on_every_row` FAILS (it runs inside a stale agent carrier). (Known weak spot, not a defect: `test_a_ui_creation_gives_user_ui_and_no_flag_rows` equals the default, so it cannot see a dropped `creator`; the agent-origin cases cover that line.)

- [ ] **Step 8: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/pending_titles.py src/twicc/pending_session_attributes.py src/twicc/core/services/session_creation.py src/twicc/core/services/session_timeline.py src/twicc/agent/base_manager.py src/twicc/providers/sessions_watcher.py tests/test_timeline_origin_titles_creation.py`
Expected: no finding on lines written by this task.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/pending_titles.py src/twicc/pending_session_attributes.py src/twicc/core/services/session_creation.py src/twicc/core/services/session_timeline.py src/twicc/agent/base_manager.py src/twicc/providers/sessions_watcher.py tests/test_timeline_origin_titles_creation.py && git commit -m "$(cat <<'EOF'
feat(timeline): keep the creating request's origin with titles and new sessions

The pending title now carries the origin of the request that set it, and the
deferred flush (inline, retry and verify tasks, state-change funnel) records
that origin, or the explicit default, instead of reading the carrier. The
pending creation attributes carry the creating origin too: session.created and
its flag rows take it for TwiCC-created sessions (UI, or an agent through the
CLI or MCP) and are system with NULL fields for synced or external sessions.
The Codex draft-to-canonical re-key now moves whole pending entries, so no
field (the origin included) can be forgotten. Non-user titles keep actor
agent with NULL origin fields.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Process events — paired stop origin, hooks, DEAD hook

**Files:**
- Create: `src/twicc/agent/stop_origin.py`
- Modify: `src/twicc/agent/base_agent.py` (base class, `__init__`)
- Modify: `src/twicc/agent/timeline_hooks.py`
- Modify: `src/twicc/providers/claude_code/agent/hybrid/agent.py` (liveness fallback)
- Create: `tests/test_timeline_origin_process.py`

**Interfaces:**
- Consumes (T1, T2): `capture_origin`, `DEFAULT_ORIGIN`, `SYSTEM_ORIGIN`, `RequestOrigin`, `origin_scope`; `record_event_sync(actor=None, origin=...)`.
- Produces:
  - `twicc.agent.stop_origin.StopOriginMixin` with the properties `kill_reason` (getter tolerant of objects built without `__init__`; setter) and `stop_origin -> RequestOrigin | None`. The setter pairs them: a reason in `DELIBERATE_STOP_REASONS` captures `capture_origin()` (carrier, else default `user` / `ui`) at that moment; any other reason (or `None`) clears the origin. `BaseAgent` inherits it, so EVERY assignment site pairs them.
  - `timeline_hooks.record_process_started(agent, *, resume)` is request-caused (carrier, else default; the system marker gives `system`).
  - `timeline_hooks.record_process_ended(agent, prev)`: deliberate reason -> `agent.stop_origin` (else default `user` / `ui`, never the carrier); anything else -> `SYSTEM_ORIGIN`. The inner `turn.ended` / `awaiting_input.ended` rows closed with it take the same four fields.
  - `timeline_hooks._write(agent, specs, *, occurred_at, actor=TimelineActor.AGENT, origin=None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_origin_process.py`:

```python
"""Process events: the stop origin is paired with the stop reason (design section 7; verification 8, 8a, 8d, 8e, 8f, 10)."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.utils import timezone

from tests.timeline_helpers import columns, run_with_writer
from twicc.agent.base_agent import BaseAgent
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.stop_origin import StopOriginMixin
from twicc.agent.states import DELIBERATE_STOP_REASONS, AgentState
from twicc.agent.timeline_hooks import (
    ProcessSnapshot, record_process_changes, record_process_ended, record_process_started,
)
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, TimelineEvent
from twicc.providers.helpers import AgentSettings
from twicc.request_origin import (
    DEFAULT_ORIGIN, UI_ORIGIN, agent_cli_origin, mcp_session_origin, origin_scope, system_marker,
)

A = mcp_session_origin("sess-A")
B = agent_cli_origin("sess-B")
SYSTEM_COLUMNS = ("system", None, None, None)


class _Agent(BaseAgent):
    """A minimal concrete agent: the pairing lives on the base class."""

    provider = Provider.CODEX

    async def start(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def send_message(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def new_agent():
    return _Agent(session_id="s1", project_id="-p", cwd="/tmp", agent_settings=AgentSettings())


# --- The pairing of reason and origin (no database) ---------------------------------------------------------


def test_a_new_agent_has_no_reason_and_no_origin():
    agent = new_agent()
    assert (agent.kill_reason, agent.stop_origin) == (None, None)


def test_a_new_agent_never_inherits_the_origin_of_a_previous_one():
    old = new_agent()
    with origin_scope(A):
        old.kill_reason = "manual"
    assert new_agent().stop_origin is None


@pytest.mark.parametrize("reason", sorted(DELIBERATE_STOP_REASONS))
def test_a_deliberate_reason_captures_the_carrier_at_assignment(reason):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = reason
    assert (agent.kill_reason, agent.stop_origin) == (reason, A)


def test_a_deliberate_reason_with_an_empty_carrier_captures_the_default():
    agent = new_agent()
    agent.kill_reason = "manual"
    assert agent.stop_origin == DEFAULT_ORIGIN == UI_ORIGIN


@pytest.mark.parametrize("reason", [
    "error", "shutdown", "apply-settings", "switch-hybrid", "cron_restart_timeout", "startup-failed",
    "auth_required", "cli-exit", "ephemeral-done", None,
])
def test_a_non_deliberate_reason_has_no_origin_even_under_a_carrier(reason):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = reason
    assert (agent.kill_reason, agent.stop_origin) == (reason, None)


@pytest.mark.parametrize(("first", "second"), [("manual", "force"), ("manual", "archived"), ("force", "archived")])
def test_overlapping_stops_keep_reason_and_origin_of_the_same_request(first, second):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = first
    with origin_scope(B):
        agent.kill_reason = second  # last writer wins, both fields together
    assert (agent.kill_reason, agent.stop_origin) == (second, B)


@pytest.mark.parametrize("later", ["error", "shutdown", "cron_restart_timeout", "apply-settings"])
def test_a_later_non_request_reason_clears_the_origin(later):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = "manual"
    agent.kill_reason = later
    assert agent.stop_origin is None


def test_agents_built_without_init_still_read_a_reason():
    agent = object.__new__(_Agent)
    assert (agent.kill_reason, agent.stop_origin) == (None, None)


def test_a_deliberate_reason_assigned_from_a_stale_context_captures_that_stale_context():
    # Why no agent task may re-assign a deliberate reason it merely read back: the setter captures the
    # context it runs in, here the one of the request that STARTED the agent. The hybrid liveness
    # fallback used to do exactly that.
    agent = new_agent()

    async def scenario():
        with origin_scope(A):  # the starting request: the agent's task copies this context
            async def agents_own_task():
                await asyncio.sleep(0)
                agent.kill_reason = agent.kill_reason

            task = asyncio.create_task(agents_own_task())
        with origin_scope(B):
            agent.kill_reason = "manual"
        await task

    asyncio.run(scenario())
    assert agent.stop_origin == A


def test_hybrid_liveness_never_reassigns_the_stop_reason():
    from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent

    source = inspect.getsource(HybridClaudeAgent)
    assert "self.kill_reason = self.kill_reason" not in source
    assert 'if self.kill_reason is None:' in source


# --- The hooks (database) -----------------------------------------------------------------------------------

pytestmark_db = pytest.mark.django_db(transaction=True)


class _Manager(BaseAgentManager):
    provider = Provider.CODEX


class FakeAgent(StopOriginMixin):
    """The attributes the timeline hooks read, with the real reason/origin pairing."""

    provider = Provider.CODEX

    def __init__(self, project, *, with_run=True):
        now = timezone.now()
        self.session_id = "s1"
        self.project_id = project.id
        self.ephemeral = False
        self.pending_requests = ()
        self.error = None
        self.is_hybrid = False
        self.process_run = (
            ProcessRun.objects.create(provider="codex", session_id="s1", started_at=now, last_state_change_at=now)
            if with_run else None
        )
        self.kill_reason = None

    def get_pid(self):
        return 123

    def background_work_snapshot(self):
        return None


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-proc", directory="/tmp/tl-origin-proc")


def rows(*kinds_):
    qs = TimelineEvent.objects.filter(session_id="s1").order_by("id")
    return list(qs.filter(kind__in=kinds_) if kinds_ else qs)


def die(agent):
    run_with_writer(lambda: _Manager()._persist_process_run_transition(agent, AgentState.DEAD))


@pytestmark_db
def test_process_started_takes_the_origin_of_the_starting_request(project):
    with origin_scope(A):
        record_process_started(FakeAgent(project), resume=False)
    assert columns(rows("process.started")[0]) == ("agent", "mcp_internal", "sess-A", None)


@pytestmark_db
def test_process_started_without_any_origin_is_the_default(project):
    record_process_started(FakeAgent(project), resume=False)
    assert columns(rows("process.started")[0]) == ("user", "ui", None, None)


@pytestmark_db
def test_an_automatic_process_started_is_system_never_the_default(project):
    with system_marker():
        record_process_started(FakeAgent(project), resume=True)
    assert columns(rows("process.started")[0]) == SYSTEM_COLUMNS


@pytestmark_db
def test_a_stop_asked_by_b_on_a_session_started_by_a_records_b_from_the_agents_own_task(project):
    agent = FakeAgent(project)
    manager = _Manager()

    async def scenario():
        release = asyncio.Event()
        with origin_scope(A):  # the request that started the session: the agent's task keeps this context
            async def agents_own_task():
                await release.wait()
                await manager._persist_process_run_transition(agent, AgentState.DEAD)

            task = asyncio.create_task(agents_own_task())
        with origin_scope(B):  # the request that asks for the stop
            agent.kill_reason = "manual"
        release.set()
        await task

    run_with_writer(scenario)
    assert columns(rows("process.ended")[0]) == ("agent", "cli", "sess-B", None)


@pytestmark_db
def test_a_stop_from_an_old_cli_with_no_origin_records_the_default_not_the_starter(project):
    agent = FakeAgent(project)
    manager = _Manager()

    async def scenario():
        release = asyncio.Event()
        with origin_scope(A):
            async def agents_own_task():
                await release.wait()
                await manager._persist_process_run_transition(agent, AgentState.DEAD)

            task = asyncio.create_task(agents_own_task())
        with origin_scope(None):  # a drop file with no origin
            agent.kill_reason = "manual"
        release.set()
        await task

    run_with_writer(scenario)
    assert columns(rows("process.ended")[0]) == ("user", "ui", None, None)


@pytestmark_db
def test_an_error_that_overrides_a_pending_stop_reason_makes_the_end_system(project):
    agent = FakeAgent(project)
    with origin_scope(A):
        agent.kill_reason = "manual"
    agent.kill_reason = "error"
    die(agent)
    assert columns(rows("process.ended")[0]) == SYSTEM_COLUMNS


@pytestmark_db
@pytest.mark.parametrize("reason", ["shutdown", "apply-settings", "switch-hybrid", "error", "cron_restart_timeout"])
def test_every_automatic_end_is_system_with_null_fields_whatever_the_carrier(project, reason):
    agent = FakeAgent(project)
    with origin_scope(A):
        agent.kill_reason = reason
        die(agent)
    assert columns(rows("process.ended")[0]) == SYSTEM_COLUMNS


@pytestmark_db
def test_inner_rows_closed_with_the_end_share_its_actor_via_ref_and_name(project):
    agent = FakeAgent(project)
    with origin_scope(B):
        agent.kill_reason = "archived"
    record_process_ended(agent, ProcessSnapshot("assistant_turn", True, None))
    got = rows()
    assert [r.kind for r in got] == ["turn.ended", "awaiting_input.ended", "process.ended"]
    assert {columns(r) for r in got} == {("agent", "cli", "sess-B", None)}


@pytestmark_db
def test_inner_rows_of_an_automatic_end_are_system_too(project):
    agent = FakeAgent(project)
    agent.kill_reason = "shutdown"
    record_process_ended(agent, ProcessSnapshot("assistant_turn", True, None))
    assert {columns(r) for r in rows()} == {SYSTEM_COLUMNS}


@pytestmark_db
def test_an_ended_row_does_not_inherit_the_origin_of_its_started_row(project):
    agent = FakeAgent(project)
    with origin_scope(A):
        record_process_started(agent, resume=False)
        agent.kill_reason = "manual"
    with origin_scope(B):
        agent.kill_reason = "manual"
    record_process_ended(agent, ProcessSnapshot("user_turn", False, None))
    started, ended = rows("process.started", "process.ended")
    assert columns(started) != columns(ended) and columns(ended) == ("agent", "cli", "sess-B", None)


@pytestmark_db
def test_standalone_process_rows_keep_actor_agent_with_null_fields(project):
    agent = FakeAgent(project)
    with origin_scope(A):
        record_process_changes(
            agent, ProcessSnapshot("user_turn", False, None), new_state="assistant_turn", new_awaiting=False,
            new_work=None,
        )
        agent.pending_requests = (SimpleNamespace(request_type="question", tool_name="Ask"),)
        record_process_changes(
            agent, ProcessSnapshot("assistant_turn", False, None), new_state="assistant_turn", new_awaiting=True,
            new_work=None,
        )
        agent.pending_requests = ()
        record_process_changes(  # a natural end: the user answered the request, it is NOT attributed to them
            agent, ProcessSnapshot("assistant_turn", True, None), new_state="assistant_turn", new_awaiting=False,
            new_work=None,
        )
    got = rows()
    assert [r.kind for r in got] == ["turn.started", "awaiting_input.started", "awaiting_input.ended"]
    assert {columns(r) for r in got} == {("agent", None, None, None)}


@pytestmark_db
def test_a_hybrid_switch_kill_is_system_and_the_next_start_takes_the_send_origin(project):
    first = FakeAgent(project)
    with origin_scope(UI_ORIGIN):  # the composer's switch request
        first.kill_reason = "switch-hybrid"
    die(first)
    assert columns(rows("process.ended")[0]) == SYSTEM_COLUMNS
    second = FakeAgent(project, with_run=False)  # a new agent for the same session
    with origin_scope(B):  # the send of an agent through the CLI
        record_process_started(second, resume=True)
    assert columns(rows("process.started")[0]) == ("agent", "cli", "sess-B", None)


@pytestmark_db
def test_an_idle_hybrid_switch_has_no_kill_and_the_start_takes_its_send_origin(project):
    with origin_scope(A):
        record_process_started(FakeAgent(project, with_run=False), resume=True)
    assert [r.kind for r in rows()] == ["process.started"]
    assert columns(rows()[0]) == ("agent", "mcp_internal", "sess-A", None)


@pytestmark_db
def test_hook_fakes_without_a_stop_origin_still_work(project):
    # SimpleNamespace agents (older tests) have a kill_reason but no stop_origin attribute.
    now = timezone.now()
    run = ProcessRun.objects.create(provider="codex", session_id="s1", started_at=now, last_state_change_at=now)
    agent = SimpleNamespace(
        session_id="s1", project_id=project.id, provider=Provider.CODEX, ephemeral=False, process_run=run,
        pending_requests=(), kill_reason="manual", error=None, is_hybrid=False, get_pid=lambda: 1,
        background_work_snapshot=lambda: None, start=AsyncMock(),
    )
    die(agent)
    assert columns(rows("process.ended")[0]) == ("user", "ui", None, None)
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_process.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'twicc.agent.stop_origin'`.

- [ ] **Step 3: Write the mixin**

Create `src/twicc/agent/stop_origin.py`:

```python
"""The stop reason of an agent and the origin of the request that asked for it, paired.

Design 2026-10-14-timeline-actor-origin, section 7 (*Stale context*). The DEAD transition is often
written from the agent's own long-lived task, whose inherited context is the one of the request that
STARTED the session, not the one that stopped it. So the origin is captured when the reason is
assigned (in the stopping request's own context) and stored next to it; the DEAD hook reads only
``stop_origin``, never the carrier.

The pairing is a property setter, so it holds at EVERY assignment site (provider agents, hybrid
agent, the manager's hard kill) and for any site added later. Never re-assign a deliberate reason you
merely read back from an agent task: the setter would capture that task's stale context.
"""

from __future__ import annotations

from twicc.agent.states import DELIBERATE_STOP_REASONS
from twicc.request_origin import RequestOrigin, capture_origin


class StopOriginMixin:
    @property
    def kill_reason(self) -> str | None:
        # ``getattr``: some tests build agents with ``object.__new__`` and never run ``__init__``.
        return getattr(self, "_kill_reason", None)

    @kill_reason.setter
    def kill_reason(self, reason: str | None) -> None:
        """Assign the reason and, with it, the origin: captured for a deliberate stop, cleared otherwise.

        A deliberate stop (``manual``, ``force``, ``archived``) resolves its origin NOW, default
        included, and the last assignment wins (reason and origin always come from the same request).
        A later non-request reason (``error``, ``shutdown``, ``timeout``, ``apply-settings``...) clears
        the origin so an automatic cause never keeps a stale human origin.
        """
        self._kill_reason = reason
        self._stop_origin = capture_origin() if reason in DELIBERATE_STOP_REASONS else None

    @property
    def stop_origin(self) -> RequestOrigin | None:
        """The origin captured with a deliberate ``kill_reason``; ``None`` for any other reason."""
        return getattr(self, "_stop_origin", None)
```

- [ ] **Step 4: Use it in `BaseAgent`**

In `src/twicc/agent/base_agent.py` add `from twicc.agent.stop_origin import StopOriginMixin` to the `twicc.agent` imports, change `class BaseAgent:` to `class BaseAgent(StopOriginMixin):`, and replace `self.kill_reason: str | None = None` with

```python
        # Property from StopOriginMixin: the reason and the origin of the request that asked for the stop
        # are assigned together (design 2026-10-14, section 7).
        self.kill_reason = None
```

- [ ] **Step 5: Fix the one stale re-assignment in the hybrid agent**

In `src/twicc/providers/claude_code/agent/hybrid/agent.py`, replace

Old:
```python
                self.kill_reason = self.kill_reason or "cli-exit"
```
New:
```python
                if self.kill_reason is None:
                    # Never re-assign a reason already set: this runs in the agent's own task, and the
                    # setter would capture the stale context of the request that started it as the stopper.
                    self.kill_reason = "cli-exit"
```

- [ ] **Step 6: Update the process hooks**

In `src/twicc/agent/timeline_hooks.py`: add `from twicc.request_origin import DEFAULT_ORIGIN, SYSTEM_ORIGIN, RequestOrigin` to the imports; replace `_write`, `record_process_started` and `record_process_ended`:

```python
def _write(agent, specs, *, occurred_at, actor=TimelineActor.AGENT, origin=None) -> None:
    for spec in specs:
        record_event_sync(
            spec.kind, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
            occurred_at=occurred_at, actor=actor, origin=origin, data=spec.data,
        )


@_never_raises
def record_process_started(agent, *, resume: bool) -> None:
    """Write ``process.started``. Request-caused: the carrier of the awaited request chain, else the default.

    An automatic start (cron restart, boot adoption, pending-settings restart) runs under the system
    marker, so it is recorded as ``system`` with NULL origin fields (design 2026-10-14, section 7).
    """
    if not _enabled(agent):
        return
    run = agent.process_run
    record_event_sync(
        TimelineKind.PROCESS_STARTED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        data={"provider": agent.provider.value, "cause": "resume" if resume else "new",
              "twicc_pid": run.twicc_pid if run is not None else None,
              # ``HybridClaudeAgent.is_hybrid`` is a class attribute; other agents lack it.
              "hybrid": bool(getattr(agent, "is_hybrid", False))},
    )


def _end_origin(agent, reason: str) -> RequestOrigin:
    """Origin of ``process.ended`` and of the rows closed with it (design 2026-10-14, section 7).

    A deliberate stop: the origin captured on the agent when the stop was requested, else the default;
    NEVER the carrier (this runs in the agent's own task with the starter's stale context). Anything
    else is automatic: ``system`` with NULL fields.
    """
    if reason in DELIBERATE_STOP_REASONS:
        return getattr(agent, "stop_origin", None) or DEFAULT_ORIGIN
    return SYSTEM_ORIGIN
```
and in `record_process_ended` replace everything after `now = timezone.now()` with:

```python
    reason = getattr(agent, "kill_reason", None) or "error"
    ended_origin = _end_origin(agent, reason)
    specs = process_transition_events(
        prev_state=prev.state, prev_awaiting=prev.awaiting, prev_work=prev.work,
        new_state=AgentState.DEAD.value, new_awaiting=False, new_work=None,
    )
    # The rows closed with the end take its origin: the stop caused them.
    _write(agent, specs, occurred_at=now, origin=ended_origin)
    error = getattr(agent, "error", None)
    caused_by = latest_event_id(agent.session_id, TimelineKind.ARCHIVED_STARTED) if reason == "archived" else None
    record_event_sync(
        TimelineKind.PROCESS_ENDED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        occurred_at=now, reason=reason, caused_by=caused_by, origin=ended_origin,
        data={"error": str(error)[:_ERROR_TEXT_LIMIT]} if error else None,
    )
```
(`DELIBERATE_STOP_REASONS` is already imported in this module.)

- [ ] **Step 7: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_process.py tests/test_timeline_process_hooks.py tests/test_auto_deny.py tests/test_agent_hidden_broadcast_gate.py tests/test_ephemeral_lifecycle.py tests/test_hybrid_pending_tool_input.py tests/test_cron_stop_reasons.py -q`
Expected: all pass. The pre-existing `test_death_closes_open_intervals_then_ends_the_process_with_one_timestamp` still expects `actor == "user"` for the manual stop: the fake has no `stop_origin`, so the default `user` / `ui` applies.

**Mutation check (do not commit).** (1) In `_end_origin` return `capture_origin()` instead of `getattr(agent, "stop_origin", None) or DEFAULT_ORIGIN`: `test_a_stop_asked_by_b_on_a_session_started_by_a_records_b_from_the_agents_own_task` FAILS (the agent's own task holds A's context, the row would say A). (2) In `StopOriginMixin.kill_reason` setter always set `self._stop_origin = None`: the pairing tests and the stop-by-B test FAIL. (3) Revert the hybrid `if self.kill_reason is None:` guard: `test_hybrid_liveness_never_reassigns_the_stop_reason` FAILS.

- [ ] **Step 8: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/agent/stop_origin.py src/twicc/agent/base_agent.py src/twicc/agent/timeline_hooks.py src/twicc/providers/claude_code/agent/hybrid/agent.py tests/test_timeline_origin_process.py`
Expected: no finding on lines written by this task.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/agent/stop_origin.py src/twicc/agent/base_agent.py src/twicc/agent/timeline_hooks.py src/twicc/providers/claude_code/agent/hybrid/agent.py tests/test_timeline_origin_process.py && git commit -m "$(cat <<'EOF'
feat(timeline): attribute process events to the request that caused them

The agent's kill_reason becomes a property that captures the origin of the
request when a deliberate stop reason (manual, force, archived) is assigned
and clears it for any other reason, so reason and origin are paired at every
assignment site, including overlapping stops and a later error. The DEAD
hook reads only that captured origin (never the carrier, whose context in the
agent's own task is the starter's), falling back to the default user/ui, and
writes process.ended with it; the turn and awaiting-input rows closed with it
take the same actor, via, ref and name. process.started is request-caused
(carrier, else default, system under the marker). The hybrid liveness
fallback no longer re-assigns an existing reason from the agent's task.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Automatic starts and stops carry the system marker

**Files:**
- Modify: `src/twicc/agent/base_agent.py` (`_notify_state_change`)
- Modify: `src/twicc/providers/claude_code/agent/manager.py` (inline and pending `apply-settings` restarts, hybrid boot adoption)
- Modify: `src/twicc/providers/claude_code/cron_restart.py` (`restart_session_crons` wrapper)
- Create: `tests/test_timeline_origin_markers.py`

**Interfaces:**
- Consumes (T1): `system_marker`, `SYSTEM_ORIGIN`, `current_origin`, `origin_scope`; (T8) `record_process_started` reads the carrier.
- Produces:
  - `BaseAgent._notify_state_change` runs the state-change callback chain under the system marker (the clean context): cron restart tasks, pending-settings restarts and any follow-up the chain starts are automatic. The enclosing origin is restored when the callback returns.
  - `ClaudeCodeAgentManager.send_to_session` and `_apply_pending_settings` start the restart that belongs to an `apply-settings` kill under `system_marker()` only around that start (it is restored on return AND on an exception).
  - `ClaudeCodeAgentManager.adopt_running_hybrid_sessions` registers each adopted agent under the marker.
  - `cron_restart.restart_session_crons(session_id, *, stop_event, initial_delay=0)` keeps its public name and signature and runs under the marker (boot-time and runtime cron restarts); the old body is `_restart_session_crons`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_origin_markers.py`:

```python
"""Automatic starts and stops carry the system marker (design sections 5, 7; verification 8e, 8g, 9)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.timeline_helpers import columns
from twicc.agent.base_agent import BaseAgent
from twicc.agent.states import AgentState
from twicc.agent.timeline_hooks import record_process_started
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, TimelineEvent
from twicc.providers.claude_code import cron_restart
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.request_origin import SYSTEM_ORIGIN, UI_ORIGIN, current_origin, mcp_session_origin, origin_scope

A = mcp_session_origin("sess-A")
LOW, HIGH = AgentSettings(effort="low"), AgentSettings(effort="high")


class _Agent(BaseAgent):
    provider = Provider.CODEX

    async def start(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def send_message(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def new_agent():
    return _Agent(session_id="s1", project_id="-p", cwd="/tmp", agent_settings=AgentSettings())


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-mark", directory="/tmp/tl-origin-mark")


# --- The state-change funnel ---------------------------------------------------------------------------------


def test_the_funnel_runs_the_callback_under_the_system_marker_and_restores_the_origin():
    agent = new_agent()
    seen = []

    async def callback(_agent):
        seen.append(current_origin())

    agent._state_change_callback = callback

    async def scenario():
        with origin_scope(UI_ORIGIN):
            await agent._notify_state_change()
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("carrier", [None, UI_ORIGIN, A], ids=["unset", "ui", "agent"])
def test_a_follow_up_start_launched_by_the_funnel_is_system_whatever_the_triggering_request(project, carrier):
    restart = SimpleNamespace(
        session_id="s1", project_id=project.id, provider=Provider.CODEX, ephemeral=False, process_run=None,
        is_hybrid=False,
    )
    dying = new_agent()

    async def callback(_agent):  # stands for the cron / pending-settings restart the DEAD callback launches
        await asyncio.to_thread(record_process_started, restart, resume=True)

    dying._state_change_callback = callback

    async def scenario():
        with origin_scope(carrier):
            await dying._notify_state_change()

    asyncio.run(scenario())
    row = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    assert columns(row) == ("system", None, None, None)


# --- apply-settings restarts ---------------------------------------------------------------------------------


def _idle_manager(seen, *, start_raises=False, between=None, after=None):
    """A Claude manager with one idle agent whose startup settings (effort) differ from ``HIGH``."""
    manager = ClaudeCodeAgentManager.__new__(ClaudeCodeAgentManager)
    agent = SimpleNamespace(
        session_id="s1", state=AgentState.USER_TURN, agent_settings=LOW, background_shell_count=lambda: 0,
        process_run=None, interrupt_or_kill=AsyncMock(), is_hybrid=False,
    )
    manager._agents = {"s1": agent}
    manager._lock = asyncio.Lock()
    manager._pending_after_restart = {}
    manager._cron_restart_tasks = {}
    manager._check_ephemeral_readonly = lambda *a, **k: None
    manager._cancel_cron_restart_task = lambda session_id: None

    async def broadcast(*args, **kwargs):
        if between is not None:
            between.append(current_origin())

    async def start(*args, **kwargs):
        seen.append(current_origin())
        if start_raises:
            raise RuntimeError("start failed")

    def parked_release(session_id, pending):
        if after is not None:
            after.append(current_origin())

    manager._broadcast_agent_state = broadcast
    manager._start_agent = start
    manager._parked_release_after_start = parked_release
    return manager, agent


def test_the_inline_apply_settings_restart_is_system_and_the_rest_of_the_send_keeps_the_user_origin():
    seen, between, after = [], [], []

    async def scenario():
        manager, _agent = _idle_manager(seen, between=between, after=after)
        with origin_scope(UI_ORIGIN):
            await manager.send_to_session("s1", "-p", "/tmp", "hello", HIGH)
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]  # the restart is automatic
    assert between == [UI_ORIGIN] and after == [UI_ORIGIN]  # the send's other writes are still the user's (8g)


def test_marker_is_restored_when_the_inline_restart_raises():
    seen = []

    async def scenario():
        manager, _agent = _idle_manager(seen, start_raises=True)
        with origin_scope(UI_ORIGIN):
            with pytest.raises(RuntimeError, match="start failed"):
                await manager.send_to_session("s1", "-p", "/tmp", "hello", HIGH)
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]


def test_an_apply_settings_kill_without_restart_leaves_a_later_send_its_own_origin():
    seen = []

    async def scenario():
        manager, agent = _idle_manager(seen)
        with origin_scope(UI_ORIGIN):
            # Settings-only change in USER_TURN: no text, no crons -> the kill restarts nothing.
            assert await manager.send_to_session("s1", "-p", "/tmp", "", HIGH) is False
        agent.interrupt_or_kill.assert_awaited_once_with(reason="apply-settings")
        assert seen == []
        agent.state = AgentState.DEAD
        with origin_scope(A):  # a later, independent send by an agent
            await manager.send_to_session("s1", "-p", "/tmp", "hello again", HIGH)

    asyncio.run(scenario())
    assert seen == [A]


@pytest.mark.django_db(transaction=True)
def test_the_pending_settings_restart_that_re_sends_a_parked_text_is_system(project):
    session = Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider=Provider.CLAUDE_CODE.value)
    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    current = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(AgentSettings.from_session(session))
    )
    Session.objects.filter(id="s1").update(effort="max" if current.effort != "max" else "low")  # a startup change
    agent = SimpleNamespace(
        session_id="s1", project_id=project.id, cwd="/tmp", state=AgentState.USER_TURN, agent_settings=current,
        background_shell_count=lambda: 0, process_run=None, interrupt_or_kill=AsyncMock(), is_hybrid=False,
    )
    manager = ClaudeCodeAgentManager.__new__(ClaudeCodeAgentManager)
    manager._pending_after_restart = {"s1": {"text": "parked", "images": None, "documents": None}}
    manager._broadcast_agent_state = AsyncMock()
    manager._parked_release_after_start = lambda session_id, pending: None
    seen = []

    async def start(*args, **kwargs):
        seen.append(current_origin())

    manager._start_agent = start

    async def scenario():
        with origin_scope(UI_ORIGIN):
            await manager._apply_pending_settings(agent)
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]


# --- Cron restarts and hybrid boot adoption ---------------------------------------------------------------------


@pytest.mark.parametrize("carrier", [None, UI_ORIGIN], ids=["boot-unset", "runtime-ui"])
def test_a_cron_restart_sends_under_the_system_marker(monkeypatch, carrier):
    seen = []
    stop = asyncio.Event()

    class _FakeManager:
        _agents = {}

        async def _session_is_hybrid(self, session_id):
            return False

        async def send_to_session(self, **kwargs):
            seen.append(current_origin())
            stop.set()  # end the retry loop after this attempt

    monkeypatch.setattr(
        "twicc.providers.claude_code.agent.manager.get_claude_code_agent_manager", lambda: _FakeManager(),
    )
    monkeypatch.setattr(cron_restart, "_collect_restart_data", lambda session_id: {
        "session_id": session_id, "project_id": "-p", "cwd": "/tmp", "settings": LOW,
        "restored_crons": [], "crons_to_recreate": [],
    })
    monkeypatch.setattr(cron_restart, "_build_restart_message", lambda restored, to_recreate: "restart")

    async def scenario():
        with origin_scope(carrier):
            await cron_restart.restart_session_crons("s1", stop_event=stop, initial_delay=0)
            return current_origin()

    assert asyncio.run(scenario()) == carrier
    assert seen == [SYSTEM_ORIGIN]


@pytest.mark.django_db(transaction=True)
def test_hybrid_boot_adoption_registers_under_the_system_marker(project, monkeypatch):
    from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux

    Session.objects.create(id="h1", file_path="h1.jsonl", project=project, provider=Provider.CLAUDE_CODE.value, hybrid=True)
    monkeypatch.setattr(hybrid_tmux, "list_hybrid_sessions", lambda: [hybrid_tmux.HYBRID_SESSION_PREFIX + "h1"])
    monkeypatch.setattr(hybrid_tmux, "pane_status", lambda session_id: (123, False))

    class _FakeHybrid:
        def __init__(self, **kwargs):
            self.session_id = kwargs["session_id"]

    monkeypatch.setattr("twicc.providers.claude_code.agent.hybrid.agent.HybridClaudeAgent", _FakeHybrid)
    seen = []

    async def register(agent, text, *, resume, **kwargs):
        seen.append(current_origin())

    async def scenario():
        manager = ClaudeCodeAgentManager.__new__(ClaudeCodeAgentManager)
        manager._lock = asyncio.Lock()
        manager._agents = {}
        manager._register_and_start = register
        await manager.adopt_running_hybrid_sessions()

    asyncio.run(scenario())
    assert seen == [SYSTEM_ORIGIN]
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_markers.py -q`
Expected: failures such as `assert [UI_ORIGIN] == [SYSTEM_ORIGIN]` (the starts still read the enclosing origin). Adjust a fake only if a real attribute the production code reads is missing from it; never weaken an assertion.

- [ ] **Step 3: The funnel**

In `src/twicc/agent/base_agent.py` import `from twicc.request_origin import system_marker` and in `_notify_state_change` replace

Old:
```python
            try:
                await self._state_change_callback(self)
            except Exception as e:
```
New:
```python
            try:
                # Everything this callback chain starts or stops (cron restart, pending-settings
                # restart) is automatic: it runs under the system marker, so it neither reads the
                # origin of whoever triggered this transition (often a stale context) nor falls to the
                # default. Deferred writers inside the chain use an origin captured earlier.
                with system_marker():
                    await self._state_change_callback(self)
            except Exception as e:
```

- [ ] **Step 4: The `apply-settings` restarts and the hybrid adoption**

In `src/twicc/providers/claude_code/agent/manager.py` add `from twicc.request_origin import system_marker` to the module imports. Then:

1. In `send_to_session`, replace

Old:
```python
                            if not has_crons and has_content:
                                pending = self._pending_after_restart.pop(session_id, None)
                                await self._start_agent(
                                    session_id, project_id, cwd, pending["text"],
                                    resume=True, settings=settings,
                                    images=pending.get("images"),
                                    documents=pending.get("documents"),
                                    **_parked_start_kwargs(pending),
                                )
```
New:
```python
                            if not has_crons and has_content:
                                pending = self._pending_after_restart.pop(session_id, None)
                                # The restart performed by the same operation as the apply-settings kill
                                # is automatic, even inline in a user's send (design 2026-10-14, 5 and 7):
                                # the marker covers this start only and is restored on return or raise.
                                with system_marker():
                                    await self._start_agent(
                                        session_id, project_id, cwd, pending["text"],
                                        resume=True, settings=settings,
                                        images=pending.get("images"),
                                        documents=pending.get("documents"),
                                        **_parked_start_kwargs(pending),
                                    )
```
2. In `_apply_pending_settings`, replace

Old:
```python
                pending = self._pending_after_restart.pop(agent.session_id)
                await self._start_agent(
                    agent.session_id, agent.project_id, agent.cwd,
                    pending["text"], resume=True, settings=requested_settings,
                    images=pending.get("images"), documents=pending.get("documents"),
                    **_parked_start_kwargs(pending),
                )
```
New:
```python
                pending = self._pending_after_restart.pop(agent.session_id)
                # Automatic even when it re-sends a parked user text; this path is also reached from
                # tasks with a stale context (a shell ending), so the marker is explicit here too.
                with system_marker():
                    await self._start_agent(
                        agent.session_id, agent.project_id, agent.cwd,
                        pending["text"], resume=True, settings=requested_settings,
                        images=pending.get("images"), documents=pending.get("documents"),
                        **_parked_start_kwargs(pending),
                    )
```
3. In `adopt_running_hybrid_sessions`, replace

Old:
```python
                    await self._register_and_start(agent, "", resume=True, adopt=True)
```
New:
```python
                    # Boot adoption has no request at all: an automatic start (design 2026-10-14, section 5).
                    with system_marker():
                        await self._register_and_start(agent, "", resume=True, adopt=True)
```

- [ ] **Step 5: The cron restart wrapper**

In `src/twicc/providers/claude_code/cron_restart.py` add `from twicc.request_origin import system_marker` to the module-level import block (insert it right after the module-level, non-indented `from twicc.core.enums import Provider`, line ~35; the two other occurrences of that import are indented, inside functions), rename the function `restart_session_crons` to `_restart_session_crons` (signature, docstring and body unchanged), and add right after its end (before `_format_cron_description`):

```python
async def restart_session_crons(
    session_id: str,
    *,
    stop_event: asyncio.Event,
    initial_delay: int = 0,
) -> None:
    """Restart cron jobs for a single session with infinite retry (see :func:`_restart_session_crons`).

    The restart is automatic, at boot or at runtime: every start it performs runs under the system
    marker, so it is recorded as ``system`` and never falls to the default (design 2026-10-14, section 5).
    """
    with system_marker():
        await _restart_session_crons(session_id, stop_event=stop_event, initial_delay=initial_delay)
```

- [ ] **Step 6: Run the tests and see them pass**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_markers.py tests/test_background_work.py tests/test_cron_stop_reasons.py tests/test_claude_attachment_delivery.py tests/test_composer_attachment_send.py tests/test_hybrid_switch_send_lane.py -q`
Expected: all pass.

**Mutation check (do not commit).** A test that passes without its key line proves nothing; confirm each fails when the line is removed, then restore it. (1) In `_notify_state_change` delete `with system_marker():` (keep the call): `test_the_funnel_runs_the_callback_under_the_system_marker_and_restores_the_origin` and the three `test_a_follow_up_start_launched_by_the_funnel...` cases FAIL (each sets a UI/agent carrier or leaves it unset, so nothing is inherited by accident). (2) Remove the `with system_marker():` around the inline `_start_agent`: `test_the_inline_apply_settings_restart_is_system_...` FAILS (`[UI_ORIGIN] == [SYSTEM_ORIGIN]`). (3) Remove the one around `_register_and_start` in the adoption: `test_hybrid_boot_adoption_registers_under_the_system_marker` FAILS (`[None]`). (4) Make the `restart_session_crons` wrapper call `_restart_session_crons` without the marker: both `test_a_cron_restart_sends_under_the_system_marker` cases FAIL.

- [ ] **Step 7: Lint and commit**

Run: `cd $WT && uvx ruff check src/twicc/agent/base_agent.py src/twicc/providers/claude_code/agent/manager.py src/twicc/providers/claude_code/cron_restart.py tests/test_timeline_origin_markers.py`
Expected: no finding on lines written by this task.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/agent/base_agent.py src/twicc/providers/claude_code/agent/manager.py src/twicc/providers/claude_code/cron_restart.py tests/test_timeline_origin_markers.py && git commit -m "$(cat <<'EOF'
feat(timeline): mark automatic starts and stops with the system origin

The state-change callback funnel now runs under an explicit system marker, so
the cron restart and pending-settings restart it launches are recorded as
system and never read a stale request origin or fall to the default. The same
marker wraps, for that start only, the restart performed by the same
operation as an apply-settings kill (inline in a user's send or through the
pending-settings path, parked text included), the hybrid boot adoption, and
every cron restart (boot and runtime). The marker is restored when the start
returns or raises, so the rest of the user's send keeps the user's origin. A
later independent start still takes its own send's origin.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Source guards, documentation and full verification

**Files:**
- Create: `tests/test_timeline_origin_guard.py`
- Modify: `CLAUDE.md`, `AGENTS.md` (the `TimelineEvent` bullet of each, kept in sync)

**Interfaces:**
- Consumes: everything above.
- Produces: a guard test that fails when a writer hard-codes the `user` actor again or when a system writer forgets its explicit `actor=`; updated agent docs; a green verification run.

- [ ] **Step 1: Re-grep the hard-coded actors (the spec asks for it)**

Run: `cd $WT && grep -rn "TimelineActor.USER" src/twicc`
Expected: zero lines (the enum definition `USER = "user", "User"` in `src/twicc/core/models.py` does not match this pattern). Any other hit is a writer that still hard-codes `user`: fix it in the task that owns the file (flags/settings T6, titles/creation T7, process hooks T8) before continuing.

- [ ] **Step 2: Write the guard tests**

Create `tests/test_timeline_origin_guard.py`:

```python
"""Source guards of the origin work (design 2026-10-14-timeline-actor-origin, sections 5-7)."""

import ast
from pathlib import Path

import twicc

SRC = Path(twicc.__file__).resolve().parent

#: Calls that omit both ``actor`` and ``origin`` are request-caused: they read the carrier. Exactly these
#: functions are allowed to write that way. A new system writer that forgets its explicit ``actor=``
#: shows up here instead of silently becoming ``user`` / ``ui``. (Calls spread with ``**kwargs`` are NOT covered by this guard: the pin-scope ``common`` dict in ``session_update``, ``record_session_created`` / ``record_title_change`` (``who``) and ``timeline_boot`` (``common`` with an explicit system actor). They are covered by the T6/T7 origin tests and by the explicit system actors.)
REQUEST_CAUSED_WRITERS = {
    ("agent/timeline_hooks.py", "record_process_started"),
    ("core/services/session_update.py", "_save_and_record"),  # archive / unarchive and mute flips
    ("core/services/session_visibility.py", "_apply_visibility_flag"),
}


def test_no_writer_hard_codes_the_user_actor():
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if path != SRC / "core" / "models.py" and "TimelineActor.USER" in path.read_text()
    ]
    assert offenders == []


class _Collector(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.found = set()

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        keywords = self._recording_keywords(node)
        if keywords is not None and None not in keywords and not ({"actor", "origin"} & keywords):
            self.found.add(self.stack[-1] if self.stack else "<module>")
        self.generic_visit(node)

    @staticmethod
    def _recording_keywords(node):
        """Keyword names of a ``record_event[_sync](...)`` call, also when it is the target of ``to_thread``."""
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in {"record_event_sync", "record_event"}:
            return {keyword.arg for keyword in node.keywords}
        if name == "to_thread" and node.args and isinstance(node.args[0], ast.Name) \
                and node.args[0].id in {"record_event_sync", "record_event"}:
            return {keyword.arg for keyword in node.keywords}
        return None


def test_only_the_known_request_caused_writers_omit_the_actor_and_the_origin():
    found = set()
    for path in SRC.rglob("*.py"):
        collector = _Collector()
        collector.visit(ast.parse(path.read_text()))
        found |= {(path.relative_to(SRC).as_posix(), function) for function in collector.found}
    assert found == REQUEST_CAUSED_WRITERS
```

- [ ] **Step 3: Run the guard**

Run: `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_origin_guard.py -q`
Expected: 2 passed. If the second test fails with an extra element, a writer omits `actor=`/`origin=`: add the explicit system actor if it is a system writer, or add it to `REQUEST_CAUSED_WRITERS` with a comment if it is truly request-caused. If an expected element is missing, a request-caused writer got an explicit actor: that is a bug (it would no longer follow the request).

- [ ] **Step 4: Update `CLAUDE.md` and `AGENTS.md`**

In `CLAUDE.md`, in the `**TimelineEvent**` bullet, replace

Old: `writing only \`session.created\` and flag states, never process rows. Design: \`docs/plans/2026-10-13-session-timeline-design.md\`.`
New: `writing only \`session.created\` and flag states, never process rows. Every request-caused row also records its **origin**: \`actor\` (\`user\`/\`agent\`/\`external\`), \`via\` (\`ui\`/\`cli\`/\`rpc\`/\`mcp_internal\`/\`mcp_external\`), \`actor_ref\` (session id, API token id or MCP connection id) and \`actor_name\` (connection-name snapshot). The origin lives in the \`request_origin\` ContextVar (\`twicc/request_origin.py\`), set by every entry point (WS frame, REST middleware, RPC view, MCP call and batch child, drop-file watcher; the CLI writes it under \`_origin\` in the drop payload from \`transport.submit\`) and read by the services; a writer passes an explicit \`actor=\` only when it is NOT request-caused (then the origin fields are NULL). Deferred writers never read the carrier: pending title and pending creation attributes carry the origin captured with them, the process DEAD hook reads the origin paired with \`kill_reason\` (\`StopOriginMixin\`), and automatic steps (state-change funnel, cron restart, boot adoption, the restart of an \`apply-settings\` kill) run under the \`system\` marker. NULL \`via\` means "unknown" (old rows) or "automatic". Designs: \`docs/plans/2026-10-13-session-timeline-design.md\`, \`docs/plans/2026-10-14-timeline-actor-origin-design.md\`.`

In `AGENTS.md`, in its `**TimelineEvent**` bullet, replace

Old: `writing only \`session.created\` and flag states. Design: \`docs/plans/2026-10-13-session-timeline-design.md\`.`
New: `writing only \`session.created\` and flag states. Every request-caused row also records \`via\`, \`actor_ref\` and \`actor_name\` next to \`actor\`: the origin lives in the \`request_origin\` ContextVar (\`twicc/request_origin.py\`), set by every entry point (WS frame, REST middleware, RPC, MCP, drop-file watcher; the CLI writes it under \`_origin\` in the drop payload) and read by the services; pass an explicit \`actor=\` only for a non-request writer (origin fields NULL). Deferred writers (pending title, pending creation attributes, process DEAD hook) use an origin captured earlier, never the carrier; automatic steps run under the \`system\` marker. NULL \`via\` = unknown (old rows) or automatic. Designs: \`docs/plans/2026-10-13-session-timeline-design.md\`, \`docs/plans/2026-10-14-timeline-actor-origin-design.md\`.`

Use the Edit tool (exact strings above, with plain backticks, no backslashes). Check each anchor is unique first: `cd $WT && grep -c "never process rows. Design:" CLAUDE.md` -> `1` and `grep -c "writing only \`session.created\` and flag states. Design:" AGENTS.md` -> `1`.

- [ ] **Step 5: Check the CLI-surface docs**

The CLI surface does not change: no command, flag, output or skill is added or edited, and the `_origin` key is internal to drop files and never shown. Verify and record the result:

Run: `cd $WT && git diff --stat e8a263c1 -- SKILLS-AND-CLI.md RPC-API.md src/twicc/agent/plugin`
Expected: empty (no file in the CLI surface docs or the plugin changed). `SKILLS-AND-CLI.md`, `RPC-API.md` and the skills therefore need no edit, and `plugin.json` is NOT bumped.

- [ ] **Step 6: Full verification**

Run, in this order:

1. `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_request_origin.py tests/test_timeline_origin_columns.py tests/test_timeline_origin_ui.py tests/test_timeline_origin_entry.py tests/test_timeline_origin_drop.py tests/test_timeline_origin_writers.py tests/test_timeline_origin_titles_creation.py tests/test_timeline_origin_process.py tests/test_timeline_origin_markers.py tests/test_timeline_origin_guard.py -q` -> all pass.
2. `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_*.py -q` -> all pass (the pre-existing timeline suites).
3. `cd $WT && TWICC_DATA_DIR=$PWD uv run pytest -q` -> the whole backend suite, about 5-6 minutes. Expected: no new failure compared with `main` of the same date. A failure in an unrelated area is investigated, not skipped.
4. `cd $WT && git diff --name-only e8a263c1 -- '*.py' | xargs uvx ruff check` lints whole files: the expected result is no finding on a line this branch wrote. For any finding, check with `git blame -s <file> -L <line>,<line>` (or `git diff -U0 e8a263c1 -- <file>`) that the line predates this work before ignoring it.
5. `cd $WT && git status --short` -> only `docs/plans/2026-10-14-session-timeline-handoff.md` (untracked, never committed) and this plan file if still untracked.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add tests/test_timeline_origin_guard.py CLAUDE.md AGENTS.md && git commit -m "$(cat <<'EOF'
docs(timeline): document the request origin and guard its invariants

CLAUDE.md and AGENTS.md describe the origin columns, the request_origin
carrier, the entry points that set it, the captured origins of deferred
writers, the stop origin paired with kill_reason and the system marker. Two
source guards keep the invariants: no writer hard-codes the user actor, and
only the known request-caused writers may omit actor and origin, so a new
system writer cannot silently become user/ui. The CLI surface docs and skills
are unchanged (the drop-payload origin key is internal).

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Tell the user what they must do (never do it for them)**

1. Apply migration `0158_timeline_event_origin` on their own instance (`migrate`; `devctl.py start` applies it automatically at backend startup).
2. Restart the backend through `devctl.py` (backend changes).
3. Rows written before the migration keep NULL origin fields; nothing reads the new columns yet.

---

## Self-Review

### Spec coverage

| Spec item | Task |
|---|---|
| §1 Goal, scope | all |
| §2 Vocabulary (system marker, clean context, explicit actor) | T1 (`SYSTEM_ORIGIN`, `system_marker`, `resolve_event_origin`), T9 |
| §3 Data model: three columns, widths, no index, no payload key, NULL meanings | T2 |
| §3 Every write path carries the columns (single, batch, fallback; backfill NULL) | T2 |
| §4 Mapping table (9 rows) | T1 builders + `ORIGIN_MAPPING`; T2 persistence; T3 (UI), T4 (RPC, MCP), T5 (CLI) |
| §4 Non-request titles and standalone process rows keep `agent` | T7, T8 |
| §5 Origin resolution per entry path | T3, T4, T5 |
| §5 Default only for request-caused writes with no origin | T1, T2, T5 |
| §5 Automatic starts marked, not unset | T9 |
| §5 Deliberate stops resolve at request time | T8 |
| §5 Precedence (explicit actor, captured origin, deferred writers) | T1, T2, T7, T8 |
| §5 In-process entry points win; no ancestry walk in the backend | T4, T5 |
| §5 Resolution is cheap (once per invocation) | T5 |
| §6 ContextVar, set at every entry point | T3, T4, T5 |
| §6.1 MCP batch children carry the origin | T4 |
| §6.2 Drop files, one common place, all kinds | T5 |
| §6.3 Visibility facts | T1 (`test_the_visibility_facts`) |
| §6.4 Batch operations, one origin (bulk archive) | T2, T6 |
| §6.5 Deferred writes (pending title, retry, verify, Codex re-key) | T7 |
| §6.6 Created sessions (`session.created` + flag rows, Codex re-key) | T7 |
| §6.7 Long-lived tasks never read a request origin by accident | T8 (DEAD hook, reason pairing), T9 (funnel), T7 (deferred writers) |
| §6 Writers list; re-grep `TimelineActor.USER` | T6, T7, T8, T10 (grep + guard) |
| §7 Process events table | T8, T9 |
| §7 Stale context, reason/origin pairing, new agent object | T8 |
| §7 Funnel clean context, scope of the marker, hybrid switch | T9, T8 |
| §7 Inner rows | T8 |
| §8 Superseded statements, behaviour changes, registry `data.via` | T2 (D1), T7, T8; no edit of historical docs |
| §8 Unchanged guarantees (never raises, order, ended rows) | T2 |
| §9 One migration after 0157, no backfill of old rows | T2 |
| §10 O1 | T2 |
| O2 | T1, T2 |
| O3 | T1 (`ORIGIN_MAPPING`), T2 |
| O4 | T1, T3, T4, T5 |
| O5 | T1, T2 |
| O6 | T8 |
| O7 | T2 (`test_an_ended_row_never_copies...`), T8 |
| O8 | T2 (plain columns; widths) |
| O9 | T2 (no data migration; backfill NULL test) |
| O10 | T9 |
| O11 | T4, T5 |
| O12 | T4 |
| O13 | T4 |
| O14 | T7, T8 |
| O15 | T8 |
| O16 | T7 |
| O17 | T7 |
| O18 | T2 |
| O19 | T9 |
| O20 | T7 |
| O21 | T1, T2 |
| O22 | T4 |
| O23 | T8 |
| O24 | T2 |
| O25 | T9 |
| O26 | T7 |
| O27 | T8 |
| O28 | T7, T8 |
| O29 | T8 (`test_standalone_process_rows...`, natural `awaiting_input.ended` stays `agent`) |
| O30 | T7 |
| O31 | T9, T8 |
| O32 | T7, T8 |
| O33 | T8 (hybrid kill system), superseded for the start by O34 |
| O34 | T9, T8 |
| O35 | T2 |
| §11 Known limits | documented, not coded; nothing to test |
| §12 Out of scope | not touched (no display, no shares, no backfill of old rows) |

### Spec section 13 verification map

| Item | Test (file::name) |
|---|---|
| 1 Mapping | `test_request_origin::test_each_entry_path_builds_its_row_of_the_mapping_table`; `test_timeline_origin_columns::test_every_mapping_row_is_stored_as_given`; entry points T3/T4/T5 |
| 2 Default and precedence | `test_timeline_origin_columns::test_no_actor_no_origin_and_no_carrier_is_the_default`, `test_an_explicit_actor_wins_over_the_carrier_and_leaves_the_origin_fields_null` |
| 3a In-process precedence | `test_timeline_origin_entry::test_an_external_call_is_never_a_cli_call`, `test_an_internal_call_is_never_an_agent_cli_call`; `test_timeline_origin_drop::test_backend_mode_submit_carries_the_origin_through_the_loop` |
| 3b RPC variants | `test_timeline_origin_entry::test_an_rpc_call_with_a_token_carries_the_token_id`, `test_an_rpc_call_with_no_token_record_has_no_ref`, `test_a_cookie_scope_rpc_call_sets_no_origin` (the `--remote` CLI is an RPC token call) |
| 3c Local MCP with API token | `test_timeline_origin_entry::test_a_local_api_token_gives_the_token_id`, `test_the_endpoint_publishes_the_token_id_and_the_tool_runs_under_its_origin` |
| 3 Drop-file path | `test_timeline_origin_drop::test_every_kind_gets_its_origin_re_established_and_never_sees_the_key`, `test_timeline_origin_writers::test_a_drop_payload_writes_its_origin_into_the_event` (T6: it needs the T6 writers), `test_local_mode_writes_the_cli_origin_into_the_payload_of_every_kind`, `test_cli_origin_*` |
| 4 Entry points; no mixing | T3 `test_each_frame_runs_with_the_ui_origin_and_restores_the_carrier`, middleware tests; T4 `test_two_overlapping_calls_never_mix_their_origins`, `test_batch_children_carry_the_origin_of_the_batch_call`; T1 `test_concurrent_requests_never_mix` |
| 5 Name snapshot, truncation | `test_timeline_origin_columns::test_a_long_ref_and_name_are_truncated_not_rejected`; `test_timeline_origin_entry::test_the_external_name_is_read_at_call_time` (the column is a plain snapshot, not a foreign key: `test_widths_nullability_and_constants_are_pinned`) |
| 6 Batch | `test_timeline_origin_writers::test_the_bulk_archive_writes_one_origin_on_every_row`; `test_timeline_bulk_archive::test_the_bulk_archive_endpoint_records_one_user_event_per_archived_session` (edited in T6); `test_timeline_origin_columns::test_a_batch_writes_one_origin_on_every_row` |
| 7 Deferred write | `test_timeline_origin_titles_creation::test_a_flushed_title_records_the_captured_origin_even_inside_the_system_marker`, `..._without_a_captured_origin_is_the_default_never_system` |
| 7c Codex re-key | `test_a_codex_start_keeps_the_captured_origins_of_the_title_and_the_creation_attributes`, `test_an_agent_created_codex_session_keeps_its_origin_after_the_rekey` |
| 7a Titles | `test_a_user_title_takes_the_carrier`, `test_a_non_user_title_keeps_actor_agent_and_null_origin_fields` |
| 7b Created sessions | `test_the_creation_service_captures_the_creating_origin` (capture sites), `test_a_ui_creation_gives_user_ui_and_no_flag_rows`, `test_a_hidden_and_muted_session_created_by_an_agent_...`, `test_a_twicc_session_without_a_captured_origin_...`, `test_a_session_found_without_a_twicc_request_is_system_...`, `test_the_backfill_writes_null_origin_fields` |
| 8d Stop with no origin | `test_timeline_origin_process::test_a_stop_from_an_old_cli_with_no_origin_records_the_default_not_the_starter` |
| 8e System marker (i, ii, iii) | `test_timeline_origin_markers` (funnel, inline and pending apply-settings, cron, adoption, `test_an_apply_settings_kill_without_restart_leaves_a_later_send_its_own_origin`); `test_timeline_origin_process::test_an_idle_hybrid_switch_has_no_kill_...`, `test_a_hybrid_switch_kill_is_system_and_the_next_start_takes_the_send_origin` |
| 8f Not request-caused, unchanged | `test_standalone_process_rows_keep_actor_agent_with_null_fields`, `test_a_non_user_title_keeps_actor_agent_and_null_origin_fields` |
| 8g Marker scope | `test_the_inline_apply_settings_restart_is_system_and_the_rest_of_the_send_keeps_the_user_origin` |
| 8a Overlapping stops | `test_overlapping_stops_keep_reason_and_origin_of_the_same_request`, `test_a_later_non_request_reason_clears_the_origin` (every assignment site, by the property) |
| 8h Batch fallback | `test_the_batch_fallback_carries_the_same_columns_as_the_batch_path` |
| 8i Deferred writers | the two flush tests above (the flush runs under the system marker) |
| 8b Token ids | `test_an_rpc_call_with_a_token_carries_the_token_id`, `test_a_local_api_token_gives_the_token_id` |
| 8c Widths | `test_a_long_ref_and_name_are_truncated_not_rejected`, `test_the_batch_path_truncates_and_nulls_like_the_single_path` |
| 8 Process events | T8 and T9 tests (started origin, B stops A's session from the agent's task, error clears the origin, new agent has none, inner rows) |
| 9 Clean follow-up | `test_a_follow_up_start_launched_by_the_funnel_is_system_whatever_the_triggering_request` |
| 10 `ended` rows | `test_an_ended_row_never_copies_the_origin_fields_of_its_started_row`, `test_an_ended_row_does_not_inherit_the_origin_of_its_started_row` |
| 11 Registry, invalid values | `test_validate_data_no_longer_accepts_a_data_via_key`, `test_an_invalid_via_is_stored_as_null_and_the_event_is_still_written` |
| 12 Compatibility | `test_rows_created_without_the_columns_read_as_null`, `test_the_backfill_writes_null_origin_fields`, `test_an_unreadable_carrier_falls_back_to_the_default_and_the_event_is_written` |

### Task independence audit (each commit stays green)

Every test a task adds or edits depends only on that task and earlier ones.

| Task | Tests added or edited | Depends on | Verdict |
|---|---|---|---|
| T1 | `test_request_origin` | T1 | ok (`columns` in the helpers is only called from T2 on) |
| T2 | `test_timeline_origin_columns`, `test_timeline_model` (edited), `test_timeline_service` (edited: `actor=None` no longer triggers NOT NULL, `precision=None` does) | T1, T2 | ok |
| T3 | `test_timeline_origin_ui` | T1, T3 | ok. The `test_timeline_bulk_archive` edit moved to T6 (the bulk writer hard-codes the actor until then); the file is only run unchanged. |
| T4 | `test_timeline_origin_entry` (fakes the command execution) | T1, T4 | ok |
| T5 | `test_timeline_origin_drop` (fake services, transport) | T1, T5 | ok. The end-to-end drop test moved to T6 (the mute writer hard-codes the actor until then). |
| T6 | `test_timeline_origin_writers` (incl. the drop end-to-end test), `test_timeline_bulk_archive` (edited) | T1, T2, T3 (middleware), T5 (drop), T6 | ok |
| T7 | `test_timeline_origin_titles_creation` (incl. the creation-service capture test) | T1, T2, T7 | ok |
| T8 | `test_timeline_origin_process` | T1, T2, T8 | ok (marker via `system_marker` from T1, not T9) |
| T9 | `test_timeline_origin_markers` | T1, T8 (process.started reads the carrier), T9 | ok |
| T10 | `test_timeline_origin_guard` | all | ok (its AST guard does not see `**kwargs` calls: listed in its docstring, covered by T6/T7 tests) |

### Vacuity audit

A test is vacuous when it passes although its key line is removed, because the context is inherited anyway. Fixed: the T4 batch test (children are now launched in an empty context). Checked and not vacuous (no ambient origin, or an ambient one different from the expected value): WS, middleware, RPC, MCP single call, drop transport and watcher, writers, titles, hooks, funnel, apply-settings, cron, adoption. Each task has a "Mutation check" paragraph naming the line to delete and the tests that must then fail. T7 additions: the two capture sites of `session_creation.py` (title and creation attributes) are pinned by `test_the_creation_service_captures_the_creating_origin`, and the "deferred writers never read the carrier" rule for `session.created` by running the default case inside a stale carrier. One known weak test, documented in T7: the UI creation case equals the default. Likewise `test_the_bulk_archive_endpoint_records_one_user_event_per_archived_session` asserts `("user", "ui", None, None)`, equal to the default, so it cannot prove the carrier crossed the executor; the T6 bulk test with an agent origin covers that.

### Placeholder scan

No `TBD`, `TODO`, "add appropriate handling", "similar to Task N" or empty step remains. Every code step shows the code or the exact old/new text. Two steps depend on a fake matching the real signature at implementation time (T4 RPC tests copy `tests/test_rpc_auth.py` fixtures; T9 fakes of the Claude manager): the plan states the attributes the production code reads, and says to adjust the fake, never the assertion.

### Type and name consistency

- `RequestOrigin(actor, via, actor_ref, actor_name)`; builders `human_cli_origin()`, `agent_cli_origin(session_id)`, `rpc_origin(token_id)`, `mcp_session_origin(session_id)`, `mcp_token_origin(token_id)`, `mcp_external_origin(connection_id, name)`: same names in T1 (definition), T2 (`ORIGIN_MAPPING`), T4, T5, T6, T7, T8, T9.
- ContextVar `request_origin`; helpers `current_origin()`, `capture_origin()`, `origin_scope(origin)`, `system_marker()`: used with these exact names in T3-T9.
- `record_event_sync(..., actor=None, origin=None)`, `EventSpec(..., origin=None)`, `origin_columns(resolved)`: T2 definitions, used by T6 (`EventSpec(origin=)`), T7 (`origin=` / `who` kwargs), T8 (`origin=ended_origin`).
- `ORIGIN_PAYLOAD_KEY = "_origin"`: T1 definition, T5 transport and watcher, T5 tests.
- `TOKEN_ID_SCOPE_KEY = "twicc_mcp_token_id"`: T4 identity, endpoint, server, tests.
- `kill_reason` / `stop_origin`: T8 mixin; `timeline_hooks._end_origin` reads `stop_origin` through `getattr`.
- `record_session_created(session, *, origin: str, creator=None)`: the `origin` keyword keeps its old meaning (the `data.origin` string); the request origin is `creator` (no name clash).
- `rekey_pending_title`, `rekey_pending_session_attributes`: T7 definitions, used in the `base_manager` re-key and in the T7 tests.

### Review Focus check

| Focus line | Test in its owning task |
|---|---|
| 1 Marker leak on failure | T9 `test_marker_is_restored_when_the_inline_restart_raises` |
| 2 Stale re-assignment, fakes without attributes | T8 `test_hybrid_liveness_never_reassigns_the_stop_reason`, `test_a_deliberate_reason_assigned_from_a_stale_context_captures_that_stale_context`, `test_agents_built_without_init_still_read_a_reason`, `test_hook_fakes_without_a_stop_origin_still_work` |
| 3 Drop payload hygiene | T5 `test_every_kind_gets_its_origin_re_established_and_never_sees_the_key`, `test_a_payload_without_origin_runs_unset_even_inside_a_stale_carrier`, `test_a_forged_or_malformed_origin_degrades_to_unset` |
| 4 Pending-store whole-entry moves | T7 `test_rekey_moves_every_field_of_the_pending_attributes`, `test_setting_a_title_without_an_origin_forgets_an_older_origin`, `test_the_pending_title_keeps_its_origin_until_popped` |
| 5 Every thread / executor hop | T1 `test_the_visibility_facts`; T5 `test_backend_mode_submit_carries_the_origin_through_the_loop`; T6 `test_hide_through_the_compute_executor_keeps_the_origin` |

### Not verified while writing this plan

- No test was run: the plan reads the repo and probes only the `contextvars` propagation facts with a standalone script.
- The exact `classify_agent_settings_changes` result for `AgentSettings(effort="low")` versus `AgentSettings(effort="high")` (T9 uses it to reach the startup-change branch; `effort` is documented as a startup setting in `send_to_session`).
- Whether `sessions` stays in `COOKIE_READONLY_COMMANDS` (T4 cookie test; it is listed today).
- The shape of an `InvocationResult` accepted by the MCP envelope in the T4 endpoint test (`exit_code`, `result`, `error` as used by the existing RPC tests).
