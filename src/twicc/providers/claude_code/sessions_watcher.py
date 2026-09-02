"""
Claude Code file watcher.

Thin :class:`~twicc.providers.sessions_watcher.BaseSessionsWatcher` subclass
that plugs in Claude Code's directory layout, compute object, agent manager
hook, and project-directory tracking. Everything else (watchfiles loop, ORM
updates, broadcasts, search indexing, polling) lives in the base.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

import orjson
from asgiref.sync import sync_to_async
from watchfiles import Change

from twicc.core.models import SessionType, Workflow
from twicc.core.serializers import serialize_project, serialize_session
from twicc.projects import compute_project_stale

from .compute import (
    WORKFLOW_FILE_PREFIX,
    WORKFLOW_FILE_SUFFIX,
    get_compute as _get_compute,
)
from twicc.provider_homes import claude_projects_dir
from .workflow_synthesis import (
    agent_first_message,
    enrich_previews,
    rebuild_state1,
    stamp_phase_states,
)
from twicc.providers.compute_base import BaseSessionCompute, ToolResultUpdate
from twicc.providers.sessions_watcher import (
    BaseSessionsWatcher,
    IndexingRequest,
    ParsedSessionFile,
    broadcast_message,
    get_project_by_id,
    get_session_by_id,
)

logger = logging.getLogger(__name__)


# Real subagent files are named agent-a<hex>.jsonl (e.g. agent-a6c7d21.jsonl).
# Sidechain files like agent-acompact-<hex>.jsonl or agent-aprompt_suggestion-<hex>.jsonl are excluded.
_REAL_SUBAGENT_RE = re.compile(r"^agent-a[0-9a-f]+\.jsonl$")


def _state0_raw_json(run_id: str, script_text: str) -> str:
    """The minimalist STATE 0 ``raw_json``: a launched-but-unsynthesized run.

    Just enough for a viewing front to recognise the run (``synthetic`` + no
    ``phases``) and regenerate templates from ``script``. See
    :class:`~twicc.core.models.Workflow` for the three states.
    """
    return orjson.dumps({
        "runId": run_id,
        "script": script_text,
        "status": "pending",
        "synthetic": True,
    }).decode()


class ClaudeCodeSessionsWatcher(BaseSessionsWatcher):
    """File watcher for Claude Code's ``<claude home>/projects/`` layout.

    Layouts handled:

    - ``project_id/session_id.jsonl`` → :class:`SessionType.SESSION`
    - ``project_id/session_id/subagents/agent-a<hex>.jsonl`` →
      :class:`SessionType.SUBAGENT` (sidechain files like
      ``agent-acompact-*`` or ``agent-aprompt_suggestion-*`` are skipped).
    - ``project_id/session_id/subagents/workflows/<run_id>/agent-a<hex>.jsonl``
      → :class:`SessionType.SUBAGENT` with the composite id
      ``<run_id>:<agent_id>`` (a workflow subagent).

    Direct children of :attr:`projects_dir` are project directories: their
    creation/deletion is handled by :meth:`maybe_handle_special_change`,
    which broadcasts a ``project_updated`` event when the working
    directory referenced by the project disappears.
    """

    def __init__(self) -> None:
        super().__init__()
        # Resolved at instantiation (``get_watcher()`` creates the singleton
        # lazily), never at import: the home is configurable per instance.
        self.projects_dir = claude_projects_dir()
        # Agents currently surfaced in each live run's STATE 1 envelope, keyed
        # run_id → set of agent_id. Populated by every rebuild
        # (:meth:`_rebuild_workflow_state1`); read by the agent-sync trigger
        # (:meth:`_maybe_rebuild_on_agent_sync`) so an agent-file write only forces
        # a rebuild when it introduces a **new** agent (state changes ride the
        # journal) — discovery in real time, without a rebuild storm. In-memory
        # only: a restart drops it harmlessly (the next journal tick / agent sync
        # repopulates), and a run's entry is purged when it stops being a live
        # STATE 1 (rebuild returns None, or its real envelope lands).
        self._workflow_surfaced: dict[str, set[str]] = {}

    async def parse_session_file(self, path: Path) -> ParsedSessionFile | None:
        try:
            relative = path.relative_to(self.projects_dir)
        except ValueError:
            return None

        parts = relative.parts

        if len(parts) == 2:
            # Format: project_id/xxx.jsonl
            project_id, filename = parts
            if filename.startswith("agent-"):
                # Old format agents at project level - ignore
                return None
            if filename.endswith(".jsonl"):
                session_id = filename.removesuffix(".jsonl")
                return ParsedSessionFile(
                    project_id,
                    session_id,
                    SessionType.SESSION,
                    file_path=str(relative),
                )

        elif len(parts) == 4:
            # Format: project_id/session_id/subagents/agent-a<hex>.jsonl
            # Sidechain files (acompact-*, aprompt_suggestion-*) are excluded.
            project_id, parent_session_id, subdir, filename = parts
            if (
                subdir == "subagents"
                and _REAL_SUBAGENT_RE.match(filename) is not None
            ):
                agent_id = filename.removeprefix("agent-").removesuffix(".jsonl")
                return ParsedSessionFile(
                    project_id,
                    agent_id,
                    SessionType.SUBAGENT,
                    file_path=str(relative),
                    parent_session_id=parent_session_id,
                )

        elif len(parts) == 6:
            # Format: project_id/session_id/subagents/workflows/<run_id>/agent-a<hex>.jsonl
            # A workflow subagent (spawned by the workflow engine, not by a Task
            # tool, so it has no AgentLink and never appears in the parent's
            # conversation flow). Its Session.id is the composite
            # ``<run_id>:<agent_id>`` — unique across runs and distinguishable
            # from a normal subagent (whose id is the bare agent hex). Sidechain
            # files are excluded by the same name regex.
            project_id, parent_session_id, subdir, wf_subdir, run_id, filename = parts
            if (
                subdir == "subagents"
                and wf_subdir == "workflows"
                and _REAL_SUBAGENT_RE.match(filename) is not None
            ):
                agent_id = filename.removeprefix("agent-").removesuffix(".jsonl")
                return ParsedSessionFile(
                    project_id,
                    f"{run_id}:{agent_id}",
                    SessionType.SUBAGENT,
                    file_path=str(relative),
                    parent_session_id=parent_session_id,
                )

        return None

    def get_compute(self) -> BaseSessionCompute:
        return _get_compute()

    async def maybe_handle_special_change(
        self,
        path: Path,
        change_type: Change,
        channel_layer,
    ) -> bool:
        """Handle workflow run files and project directories.

        Two non-``.jsonl`` patterns the base loop would otherwise skip:

        - ``<project>/<session>/workflows/wf_*.json`` — a workflow run
          artifact. Its appearance latches the owning session's
          ``has_workflows`` flag (one-way; deletions are ignored) and upserts
          the :class:`Workflow` row mirroring the file (the runtime rewrites it
          on every progress tick, so writes flow through here live).
        - direct children of :attr:`projects_dir` — project directories,
          whose creation/deletion refreshes the project ``stale`` flag.
        """
        # Workflow run file: a cheap path-shape check (no I/O) runs on every
        # event, so keep it first and bail fast for the overwhelmingly common
        # ``.jsonl`` append. Deletions never reset the flag.
        if change_type != Change.deleted:
            workflow_session_id = self._workflow_json_session_id(path)
            if workflow_session_id is not None:
                await self._handle_workflow_run(workflow_session_id, path, channel_layer)
                return True

            # A workflow ``scripts/*.js`` file is written at LAUNCH, before any
            # ``wf_*.json`` — its appearance is where a run first becomes visible
            # (the minimalist STATE 0 row). A later write means the script
            # changed mid-run (rare) → reset the synthesized state.
            script_target = self._workflow_script_target(path)
            if script_target is not None:
                session_id, run_id = script_target
                await self._handle_workflow_script(session_id, run_id, path, channel_layer)
                return True

            # A run's journal.jsonl grows live as agents start/return. Each write
            # rebuilds the synthesized running view (STATE 1) — but only once a
            # viewing front has supplied templates (the row's ``synthesis``);
            # until then it's a cheap no-op (nothing to detect against yet).
            journal_run_id = self._workflow_journal_run_id(path)
            if journal_run_id is not None:
                await self._handle_workflow_journal(journal_run_id, channel_layer)
                return True

        # Direct children of projects_dir are project dirs.
        if path.parent != self.projects_dir:
            return False
        if not (path.is_dir() or change_type == Change.deleted):
            return False
        await self._sync_project_and_broadcast(path, channel_layer)
        return True

    def _workflow_json_session_id(self, path: Path) -> str | None:
        """Owning session id if ``path`` is a workflow run file.

        Matches ``<project_id>/<session_id>/workflows/wf_*.json`` relative to
        :attr:`projects_dir` — a ``wf_*.json`` directly at the root of a
        top-level session's ``workflows/`` folder. Returns ``None`` for
        anything else (including nested paths like ``workflows/scripts/*`` or
        ``subagents/workflows/*``, which have a different depth). Pure path
        inspection, no filesystem access.
        """
        try:
            parts = path.relative_to(self.projects_dir).parts
        except ValueError:
            return None
        if (
            len(parts) == 4
            and parts[2] == "workflows"
            and parts[3].startswith(WORKFLOW_FILE_PREFIX)
            and parts[3].endswith(WORKFLOW_FILE_SUFFIX)
        ):
            return parts[1]
        return None

    def _workflow_script_target(self, path: Path) -> tuple[str, str] | None:
        """``(session_id, run_id)`` if ``path`` is a workflow launch script, else ``None``.

        Matches ``<project>/<session>/workflows/scripts/<name>-<run_id>.js``
        relative to :attr:`projects_dir`. The script filename is
        ``<workflowName>-<run_id>.js`` and the ``run_id`` always starts with
        ``wf_``; we split on the **last** ``-wf_`` so a workflow name containing
        that substring still resolves correctly. Pure path inspection, no I/O.
        """
        try:
            parts = path.relative_to(self.projects_dir).parts
        except ValueError:
            return None
        if not (
            len(parts) == 5
            and parts[2] == "workflows"
            and parts[3] == "scripts"
            and parts[4].endswith(".js")
        ):
            return None
        stem = parts[4][: -len(".js")]
        _, sep, tail = stem.rpartition(f"-{WORKFLOW_FILE_PREFIX}")
        if not sep:
            return None
        return parts[1], f"{WORKFLOW_FILE_PREFIX}{tail}"

    def _workflow_journal_run_id(self, path: Path) -> str | None:
        """Run id if ``path`` is a run's ``journal.jsonl``, else ``None``.

        Matches ``<project>/<session>/subagents/workflows/<run_id>/journal.jsonl``
        relative to :attr:`projects_dir`. Pure path inspection, no I/O.
        """
        try:
            parts = path.relative_to(self.projects_dir).parts
        except ValueError:
            return None
        if (
            len(parts) == 6
            and parts[2] == "subagents"
            and parts[3] == "workflows"
            and parts[5] == "journal.jsonl"
        ):
            return parts[4]
        return None

    async def _handle_workflow_journal(self, run_id: str, channel_layer) -> None:
        """Rebuild a run's STATE 1 running view when its journal grows."""
        await self._rebuild_workflow_state1(run_id, channel_layer)

    async def _rebuild_workflow_state1(self, run_id: str, channel_layer) -> None:
        """Rebuild a run's STATE 1 envelope, refresh its surfaced-agent set, and
        broadcast. Shared by the journal-grow trigger and the agent-sync trigger
        (both already run under the DB write lock).

        Lazy: a no-op until a viewing front has POSTed templates (the row's
        ``synthesis``) — :func:`rebuild_state1` returns ``None`` then, and for an
        already-completed run (STATE 2) **unless it has resumed** (the journal grew
        past the envelope). When it does rebuild, broadcast so open Workflows tabs
        refetch the fresh progress.
        """
        envelope = await sync_to_async(rebuild_state1)(run_id)
        if envelope is None:  # not a live STATE 1 (no synthesis, or completed)
            self._workflow_surfaced.pop(run_id, None)
            return
        target = await sync_to_async(self._workflow_broadcast_target)(run_id)
        if target is None:  # row gone
            self._workflow_surfaced.pop(run_id, None)
            return
        session_id, project_id, hidden = target
        # Record which agents are now surfaced so a later agent-file sync only
        # rebuilds when it brings a NEW one (state changes ride the journal).
        self._workflow_surfaced[run_id] = {
            entry["agentId"]
            for entry in envelope.get("workflowProgress", [])
            if isinstance(entry, dict) and entry.get("type") == "workflow_agent" and entry.get("agentId")
        }
        if hidden:  # tracked above, but don't broadcast for a hidden session
            return
        await broadcast_message(channel_layer, {
            "type": "workflow_changed",
            "session_id": session_id,
            "project_id": project_id,
            "run_id": run_id,
        })

    @staticmethod
    def _workflow_broadcast_target(run_id: str) -> tuple[str, str, bool] | None:
        """``(session_id, project_id, hidden)`` for a run's owning session, or None."""
        workflow = (
            Workflow.objects.select_related("session").filter(run_id=run_id).first()
        )
        if workflow is None:
            return None
        return (workflow.session_id, workflow.session.project_id, workflow.session.hidden)

    async def sync_and_broadcast(
        self,
        path: Path,
        parsed: ParsedSessionFile,
        change_type: Change,
        channel_layer,
    ) -> IndexingRequest | None:
        """Base sync, then surface a newly-spawned workflow agent in its run's
        live STATE 1 view the instant the agent's first message syncs.

        Runs under the same DB write lock as the base call (the live loop holds
        it across this method), so the follow-up rebuild writes safely — mirroring
        the journal handler, which also rebuilds + broadcasts under the lock.
        """
        indexing = await super().sync_and_broadcast(path, parsed, change_type, channel_layer)
        await self._maybe_rebuild_on_agent_sync(parsed, channel_layer)
        return indexing

    async def _maybe_rebuild_on_agent_sync(self, parsed: ParsedSessionFile, channel_layer) -> None:
        """If ``parsed`` is a workflow agent **not yet surfaced** in its run's live
        STATE 1 view, rebuild now that its file (hence its prompt → phase) may have
        synced — so it appears in real time, not at the next bursty journal tick.

        A workflow agent's Session id is the composite ``<run_id>:<agent_id>``; a
        normal subagent (no ``:``) is ignored. An already-surfaced agent is skipped
        cheaply via :attr:`_workflow_surfaced` (its later state changes ride the
        journal) — so a busy agent's many file writes don't each force a rebuild.
        The prompt pre-check (one query) avoids rebuilding before the prompt is in
        the DB; :func:`rebuild_state1` self-gates a run with no live STATE 1.
        """
        if parsed.type != SessionType.SUBAGENT:
            return
        run_id, sep, agent_id = parsed.session_id.partition(":")
        if not sep:
            return  # a normal subagent, not a workflow agent
        if agent_id in self._workflow_surfaced.get(run_id, ()):
            return  # already shown; the journal drives its state from here
        # Cheap pre-check (one query): only rebuild once the prompt is actually
        # there, else wait for the agent's next file write.
        prompt = await sync_to_async(agent_first_message)(run_id, agent_id)
        if prompt is None:
            return
        await self._rebuild_workflow_state1(run_id, channel_layer)

    async def _handle_workflow_run(self, session_id: str, path: Path, channel_layer) -> None:
        """React to a ``wf_*.json`` write: latch ``has_workflows`` + persist the run.

        No-op when the owning session row isn't synced yet (the boot backfill in
        ``initial_sync`` picks it up). One session fetch shared by both steps.
        """
        session = await get_session_by_id(session_id)
        if session is None:
            return
        await self._latch_session_workflows(session, channel_layer)
        saved = await self._upsert_workflow_run(session.id, session.project_id, path)
        # The real envelope landed (STATE 2) — the run is no longer a live STATE 1,
        # so drop its surfaced-agent tracking (a resume repopulates it on rebuild).
        self._workflow_surfaced.pop(path.stem, None)
        # Tell open Workflows tabs to refetch — the run was created or its
        # envelope changed (the engine rewrites the file on each progress tick).
        if saved and not session.hidden:
            await broadcast_message(channel_layer, {
                "type": "workflow_changed",
                "session_id": session.id,
                "project_id": session.project_id,
                "run_id": path.stem,
            })

    async def _handle_workflow_script(
        self, session_id: str, run_id: str, path: Path, channel_layer,
    ) -> None:
        """React to a ``workflows/scripts/*.js`` write: seed/refresh the STATE 0 row.

        The script is written at LAUNCH, so this is where a run first becomes
        visible — we create a minimalist ``pending`` :class:`Workflow` row
        (``raw_json`` STATE 0). A later write to the same script (rare) resets an
        already-synthesized row back to STATE 0 and drops its stale
        ``synthesis``. A completed row (the real envelope already landed) is
        never downgraded. No-op when the owning session isn't synced yet.
        """
        session = await get_session_by_id(session_id)
        if session is None:
            return
        try:
            script_text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError:
            return
        changed = await sync_to_async(self._save_workflow_script)(session.id, run_id, script_text)
        if not changed:
            return
        await self._latch_session_workflows(session, channel_layer)
        if not session.hidden:
            await broadcast_message(channel_layer, {
                "type": "workflow_changed",
                "session_id": session.id,
                "project_id": session.project_id,
                "run_id": run_id,
            })

    @staticmethod
    def _save_workflow_script(session_id: str, run_id: str, script_text: str) -> bool:
        """Upsert the STATE 0 row for a launched run. Returns ``True`` when
        ``raw_json`` was (re)written — created, or reset after a script change —
        so the caller knows to latch + broadcast; ``False`` on a no-op.

        No-op cases: the row already holds the real envelope (``synthetic``
        falsy → STATE 2, never downgrade), or the script is unchanged (same
        ``script_hash``).
        """
        script_hash = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
        raw0 = _state0_raw_json(run_id, script_text)
        try:
            wf = Workflow.objects.get(run_id=run_id)
        except Workflow.DoesNotExist:
            Workflow.objects.create(
                session_id=session_id,
                run_id=run_id,
                raw_json=raw0,
                script_hash=script_hash,
            )
            return True
        try:
            parsed = orjson.loads(wf.raw_json)
        except orjson.JSONDecodeError:
            parsed = {}
        if not parsed.get("synthetic"):
            return False  # real envelope already landed (STATE 2)
        if wf.script_hash == script_hash:
            return False  # same script — nothing to refresh
        # Script changed mid-run → back to STATE 0, drop the now-stale synthesis.
        wf.raw_json = raw0
        wf.script_hash = script_hash
        wf.synthesis = None
        wf.save(update_fields=["raw_json", "script_hash", "synthesis", "updated_at"])
        return True

    async def _latch_session_workflows(self, session, channel_layer) -> None:
        """Set ``has_workflows=True`` on a session and broadcast the change.

        No-op when already latched. One-way: never resets the flag.
        """
        if session.has_workflows:
            return
        session.has_workflows = True
        await sync_to_async(session.save)(update_fields=["has_workflows"])
        if not session.hidden:
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })

    async def _upsert_workflow_run(self, session_id: str, project_id: str, path: Path) -> bool:
        """Mirror a ``wf_*.json`` file into its :class:`Workflow` row (upsert by run_id).

        ``run_id`` is the filename stem (``wf_<hex>``). The file is read off the
        event loop and validated with ``orjson`` before storing — a partial
        write caught mid-flush fails to parse and is skipped (the next tick
        rewrites it and re-triggers this path). Returns ``True`` when the row was
        written, so the caller knows to broadcast.
        """
        run_id = path.stem
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError:
            return False
        try:
            orjson.loads(raw)
        except orjson.JSONDecodeError:
            return False
        await sync_to_async(self._save_workflow_run)(session_id, project_id, run_id, raw)
        return True

    @staticmethod
    def _save_workflow_run(session_id: str, project_id: str, run_id: str, raw: str) -> None:
        # The real envelope (STATE 2) supersedes any synthesized STATE 0/1, but we
        # KEEP the synthesis bundle (templates): the same runId can resume, and the
        # retained templates let us re-synthesize a live STATE 1 then without a
        # front round-trip (see rebuild_state1 / _run_resumed). ``raw`` is already
        # validated JSON (_upsert_workflow_run). We don't store it verbatim:
        # enrich_previews swaps the engine's truncated prompt/result previews for
        # the full values (durable past Claude deleting the run's files), then we
        # store the enriched envelope.
        try:
            envelope = orjson.loads(raw)
        except orjson.JSONDecodeError:
            envelope = {}
        enrich_previews(envelope, project_id, session_id, run_id)
        stamp_phase_states(envelope)
        cost, phases_cost = Workflow.compute_costs(run_id, envelope)
        Workflow.objects.update_or_create(
            run_id=run_id,
            defaults={
                "session_id": session_id,
                "raw_json": orjson.dumps(envelope).decode(),
                "cost": cost,
                "phases_cost": phases_cost,
            },
        )

    async def _sync_project_and_broadcast(
        self,
        path: Path,
        channel_layer,
    ) -> None:
        """
        React to a project directory being created or deleted.

        Projects are NOT created eagerly here. They are created lazily
        when the first session with content appears (in
        ``sync_and_broadcast``). This avoids polluting the project list
        with empty folders (e.g. folders left behind after Claude
        sublimates old sessions).

        This handler only updates the stale flag on existing projects.
        Stale is based on working directory existence, not the provider folder.
        """
        project = await get_project_by_id(path.name)
        if project is None:
            return

        should_be_stale = compute_project_stale(project.directory)
        if project.stale != should_be_stale:
            project.stale = should_be_stale
            await sync_to_async(project.save)(update_fields=["stale"])
            await broadcast_message(channel_layer, {
                "type": "project_updated",
                "project": serialize_project(project),
            })

    async def _after_tool_result_broadcast(self, update: ToolResultUpdate) -> None:
        """Drop the active-tool entry left over by hooks that never fired.

        PostToolUse / PostToolUseFailure hooks may not run for tools that
        the CLI rejects via its own validation (e.g. permission denials
        that synthesise an ``is_error`` tool_result without ever invoking
        the tool). When the resulting tool_result lands in the JSONL,
        we explicitly evict the entry from the agent's ``_active_tools``
        registry so the UI doesn't keep showing a phantom in-flight tool.
        Hooks fire faster when they do fire; this catches the gaps.
        """
        from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

        manager = get_claude_code_agent_manager()
        await manager.discard_active_tool(update.session_id, update.tool_use_id)

    async def _after_compaction_synced(self, session_id: str) -> None:
        """End a hybrid agent's turn when a compaction lands.

        A manually-triggered ``/compact`` writes no ``turn_duration`` line, so
        without this the JSONL bridge would leave the agent in ASSISTANT_TURN
        until the next real turn (same issue Codex solves with this hook).
        ``handle_hybrid_jsonl_signals`` ignores non-hybrid sessions, so no
        hybrid check is needed here.
        """
        from twicc.providers.claude_code.agent.hybrid.signals import HybridJsonlSignals
        from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

        asyncio.create_task(
            get_claude_code_agent_manager().handle_hybrid_jsonl_signals(
                session_id,
                HybridJsonlSignals(turn_end=True),
            ),
            name=f"hybrid-compact-turn-end-{session_id}",
        )

    async def _after_new_lines_synced(
        self,
        session,
        new_line_nums: list[int],
        tool_result_updates: list[ToolResultUpdate],
    ) -> None:
        """JSONL → state bridge for hybrid sessions.

        Derives the batch's :class:`HybridJsonlSignals` from the freshly
        computed items and hands them to the agent manager. Fire-and-forget:
        the derivation (a DB read) and the agent transitions run in a task
        so the ingest path never blocks on agent locks. Latency stays at
        inotify level (ms), same as the live UI updates.
        """
        if not session.hybrid:
            return
        asyncio.create_task(
            self._bridge_hybrid_signals(
                session.id, new_line_nums, bool(tool_result_updates),
            ),
            name=f"hybrid-jsonl-bridge-{session.id}",
        )

    async def _bridge_hybrid_signals(
        self,
        session_id: str,
        new_line_nums: list[int],
        has_tool_results: bool,
    ) -> None:
        try:
            from twicc.core.enums import ItemKind
            from twicc.core.models import SessionItem

            from twicc.providers.claude_code.compute import (
                INTERRUPTION_MARKER_PREFIX,
                extract_command,
                extract_text_from_content,
                get_message_content,
                is_interruption_marker,
            )

            def _derive() -> tuple[bool, bool, bool, bool]:
                user_message = False
                turn_end = False
                command_message = False
                local_command_ack = False
                rows = (
                    SessionItem.objects
                    .filter(session_id=session_id, line_num__in=new_line_nums)
                    .values_list("kind", "content")
                )
                for kind, content in rows:
                    if kind == ItemKind.USER_MESSAGE:
                        # Slash-command echoes (<command-name> lines) start a
                        # local command, not an assistant turn — keep them
                        # apart so their ack can close exactly what they
                        # opened. Sniff then parse: a real prompt could quote
                        # the tag.
                        if "<command-name>" in content:
                            try:
                                parsed = orjson.loads(content)
                                text = extract_text_from_content(
                                    get_message_content(parsed),
                                )
                            except Exception:
                                text = None
                            if text and extract_command(text):
                                command_message = True
                                continue
                        user_message = True
                    elif kind == ItemKind.API_ERROR and content:
                        # Two shapes both classify as API_ERROR: intermediate
                        # retry progress (type=system, subtype=api_error,
                        # retryAttempt=N/M) which must NOT end the turn — the CLI
                        # is still retrying — and the terminal surfaced error
                        # (isApiErrorMessage=true) shown once retries are
                        # exhausted or the error is non-retryable. Only the
                        # terminal one ends the turn: the interactive CLI returns
                        # to an empty composer but never writes a turn_duration,
                        # so without this a hybrid agent would stay stuck in
                        # ASSISTANT_TURN. Sniff then parse.
                        if '"isApiErrorMessage"' in content:
                            try:
                                parsed = orjson.loads(content)
                            except Exception:
                                continue
                            if parsed.get("isApiErrorMessage"):
                                turn_end = True
                    elif kind == ItemKind.SYSTEM and content:
                        if '"turn_duration"' in content:
                            try:
                                parsed = orjson.loads(content)
                            except Exception:
                                continue
                            if (
                                parsed.get("type") == "system"
                                and parsed.get("subtype") == "turn_duration"
                            ):
                                turn_end = True
                        elif "<local-command-stdout>" in content:
                            # A local slash command's stdout marks its end.
                            # NOT folded into turn_end: mid-turn it must not
                            # end anything (the agent handler decides).
                            local_command_ack = True
                        elif INTERRUPTION_MARKER_PREFIX in content:
                            # Interruption breadcrumb (classified SYSTEM by
                            # compute): the turn was aborted — it will never
                            # write a turn_duration. Sniff then parse: the
                            # prefix could appear inside arbitrary content.
                            try:
                                parsed = orjson.loads(content)
                                text = extract_text_from_content(
                                    get_message_content(parsed),
                                )
                            except Exception:
                                continue
                            if (
                                parsed.get("type") == "user"
                                and text
                                and is_interruption_marker(text)
                            ):
                                turn_end = True
                return user_message, turn_end, command_message, local_command_ack

            user_message, turn_end, command_message, local_command_ack = (
                await asyncio.to_thread(_derive)
            )
            if not (
                user_message or turn_end or command_message
                or local_command_ack or has_tool_results
            ):
                return

            from twicc.providers.claude_code.agent.hybrid.signals import HybridJsonlSignals
            from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

            await get_claude_code_agent_manager().handle_hybrid_jsonl_signals(
                session_id,
                HybridJsonlSignals(
                    user_message=user_message,
                    turn_end=turn_end,
                    tool_results=has_tool_results,
                    command_message=command_message,
                    local_command_ack=local_command_ack,
                ),
            )
        except Exception:
            logger.exception(
                "Hybrid JSONL bridge failed for session %s", session_id,
            )


# ---- Singleton accessor ----

_watcher: ClaudeCodeSessionsWatcher | None = None


def get_watcher() -> ClaudeCodeSessionsWatcher:
    """Return the process-local Claude Code watcher singleton.

    Callers (orchestrator, SDK agent for fast-poll requests) should go
    through this rather than instantiating their own watcher, so the
    stop/boost events and fast-poll deadline are shared.
    """
    global _watcher
    if _watcher is None:
        _watcher = ClaudeCodeSessionsWatcher()
    return _watcher
