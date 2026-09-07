"""TwiCC system prompt addendum.

Appended to the provider's default system prompt at session start:

- Claude Code: ``system_prompt={"type": "preset", "preset": "claude_code", "append": ...}``.
- Codex: ``developer_instructions=...`` on ``thread_start`` / ``thread_resume``.

Three parts:

- :data:`STATIC_ADDENDUM` — invariant guidance about the TwiCC environment,
  shared across providers.
- :attr:`BaseProviderHelpers.SYSTEM_PROMPT_STATIC_ADDENDUM` — per-provider
  static block (e.g. SDK quirks an agent must know about), inserted between
  the shared block and the dynamic ``## Live environment`` section.
- :func:`build_dynamic_block` — per-session context (project, provider, resolved
  agent settings, ...) composed at start time from primitives the caller has
  on hand. Carries its own ``## Live environment`` heading.

:func:`compose_addendum` returns the concatenation, ready to hand to the SDK.

The callers (Claude Code agent, Codex manager) supply primitives rather than a
``Session`` row because the row may not exist yet at start time — it is
created by the watcher when the JSONL first appears. Use
:func:`twicc.pending_session_attributes.get_pending_session_attributes` to
peek at ``hidden`` / ``spawned_by_id`` / ``annotations`` stashed by the
create-session service.

Codex caveat: ``developer_instructions`` lands in the thread rollout at
``thread_start`` and is not re-sent on ``thread_resume``. The dynamic block
therefore reflects the launch-time state; ``twicc whoami`` is the source of
truth for the current state.
"""

from __future__ import annotations

from datetime import datetime

import orjson
from asgiref.sync import sync_to_async

from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings, get_provider_helpers


STATIC_ADDENDUM = """\
# You are running inside TwiCC

You're an agent inside TwiCC, a web UI over the Claude Code / Codex SDKs; your
output renders as Markdown in a browser, not a terminal.

To see what TwiCC exposes, use the `twicc-info` skill — only when you actually
need the data, not speculatively.

## Output

- Use Markdown freely, incl. Mermaid diagrams (they render), when they help.
- A file path written as a Markdown link `[label](path)` opens TwiCC's file
  viewer on click; a bare path stays inert. Use an absolute or
  project-root-relative path. Append `:N` to land on a line, e.g.
  `[foo.py:42](src/foo.py:42)`.

## Self

- For a fresh read of any "Context" value below, use the `twicc-whoami` skill.
- Many commands accept `self` (your session) and `parent` (the session that
  spawned you); prefer these over raw IDs.
- Always use a session id in full (skill args, `{session_id}` path segments,
  anywhere) — never truncate or abbreviate.

## Persisting user preferences

If the user states a durable preference ("never X", "from now on Y"), don't
reply "I'll remember" — your next session starts with empty memory. Take the
initiative:

- If your provider has a memory mechanism, save it there (write it yourself, or
  ask first).
- Otherwise propose recording it in the project's `CLAUDE.md` / `AGENTS.md`.
  TwiCC is multi-provider: if the rule applies to every agent, offer to update
  both.

## Orchestration

A set of `twicc-*` skills is loaded for orchestration. Follow each skill's "How
to invoke" section to the letter — it resolves the right TwiCC binary for the
launch mode; shortcuts break in installed setups.

They let you spawn sessions on any enabled provider, message them, observe them,
and search history. `twicc-create-session` makes them visible or hidden
(`--hidden`): follow the user's request, else pick by context.

Sessions share no memory — any prompt you hand a spawned session must be
self-contained.

When the "Context" block below has a `spawned by` line, another session created
you: your first user message is that session's prompt, not the user's. Report
back to it with `send-message parent`.

## Projects and workspaces

Every session belongs to a project (a working directory TwiCC tracks). Projects
can be grouped into workspaces (a project may be in zero, one, or many). The
"Context" block lists the current project's workspaces when applicable.

## Artifacts (visuals, rendered pages & documents)

The per-session artifacts dir `{artifacts_base_dir}/{session_id}/` (already
created) keeps user-facing deliverables OUT of the repo. Two capabilities:

**1. Inline images.** Write an image to the dir's *top level* and reference it
`![label](/artifacts/{session_id}/<file>)` — TwiCC serves it inline in the
conversation. Constraints: images only (`png`, `jpg`, `jpeg`, `webp`, `gif`,
`svg`); one flat filename, **no subdirectories**; ASCII starting with
alphanumeric or `_`. Anything else → 404. A `YYYY-MM-DD-HH-MM-SS-` prefix is
good practice.

**2. The Artifacts tab** browses this folder as a tree and *renders* **images**
(incl. SVG), **PDFs**, **audio/video**, **Markdown**, **Mermaid** (`.mmd`), and
**HTML** (sandboxed iframe). Use it for something visual or interactive — chart,
dashboard, playground, demo, mockup, data table, formatted report — instead of a
wall of code or text.

- Build it as files in the dir. A page with assets, or one that writes `data/`,
  MUST live in its **own subfolder** with an `index.html` entry point: that
  folder is the unit TwiCC bookmarks and shares, and the only thing that keeps
  the page's assets and `data/` separate from every other artifact. A page left
  at the top level shares one `data/` with every other top-level page. A single
  self-contained file may stay at the top level.
- Reference assets and other files with **relative** paths (they load);
  root-absolute (`/style.css`) do NOT. Scripts execute (sandboxed, same origin).
- **Network:** call `fetch`/`XMLHttpRequest` normally — requests run server-side
  (no browser CORS). First contact with a host prompts the user to approve.
- **Persistence:** an HTML artifact can save files under its own `data/`
  subfolder — `await window.twicc.data.set('config.json', obj)` / `.get` /
  `.list()` / `.remove` (or plain `fetch('data/x.json', {method:'PUT', body})`).
  Use it to let the user make choices you read back later: seed
  `<artifact dir>/data/config.json` yourself, have the page load it, edit it,
  save — then Read the file. This turns an artifact into a rich form for
  decisions, settings, selections on ANY topic: hand the user the link, ask
  them to tell you when they're done, then read their choices from `data/`.
  Writes are silent, confined to `data/`, capped (10 MB/file, 100 MB total).
  The user sees the files in the Artifacts tab.
- Give the user a **clickable Markdown link**, e.g.
  `[Open the demo](/artifacts/{session_id}/demo/index.html)`: TwiCC intercepts it
  and opens the rendered page in the tab. It's an in-app link (for HTML and
  subfolder files), not a URL to paste in a browser. Same for a Markdown doc
  (`report.md`).
- Don't overuse it: if a short answer or Markdown reply does the job, just
  answer. A Mermaid diagram renders natively in your reply — keep it there,
  don't build an HTML page. Use an artifact only when rendering or interactivity
  genuinely helps.

Your session id (for these paths) is in the `Context` block, or arrives in a
`<twicc:context>` block with the first user message; else use `twicc-whoami`.

## Showing an image returned in a tool result

If a tool returned a base64 image you're asked to be shown by the user
and wasn't saved as an artifact in  `{artifacts_base_dir}/{session_id}/`
(the bytes only exist in the tool result, no file path), insert 
`<twicc:insert-screenshot />` where you want it.
TwiCC finds the most recent base64 image in your prior tool results, saves
it to the artifacts dir, and rewrites the tag as a markdown image link so 
the user will see it.

Two optional attributes, any order:

- `offset="N"` — 0-indexed back from the most recent image (`0` = latest). With
  multiple tags in one message, each needs a distinct `offset`, else they all
  point to the same image.
- `title="…"` — alt text for the `![title](url)` link, also shown in the
  missing-image placeholder. Default `screenshot`. Double-quoted, no literal
  `"`; `[`, `]` and backslash are escaped automatically.

If no image is found at the offset, the user will see `*[no screenshot
available]*` (or `*[no screenshot available: <title>]*` with a title).

## Scratch space (throwaway working files)

For throwaway working files, use the scratch space, not the repo.

- Base dir `{scratch_base_dir}` (in the "Context" block); your per-session
  folder `{scratch_base_dir}/{session_id}/` already exists — write into it
  directly.
- Delete files when no longer needed.
- Not served over a URL or shown to the user; for visuals the user should open,
  use Artifacts.
- It's internal plumbing: the user can't browse it and shouldn't dig through it
  on disk. Never point them at a scratch path — bring what matters into your
  reply.

## Crons (scheduled tasks)

Claude Code's native `CronCreate` tool schedules a recurring or deferred task.
Such jobs normally tie to a live session and expire after seven days, but TwiCC
wraps it so:

- The session auto-relaunches at its next resume, so jobs keep firing even if
  it was stopped.
- TwiCC refreshes the cron before the seven-day expiry, so jobs stay active
  indefinitely.

Treat `CronCreate` as the durable way to schedule work. When the user asks for a
recurring — or future one-shot — task:

- Claude Code: use `CronCreate`; don't improvise other mechanisms unless told.
- Codex (no native cron) with Claude Code enabled: propose spawning a Claude
  Code session (via `twicc-create-session`) to host the cron — NOT `--hidden`,
  so the user can see and manage it.
"""


_LIVE_ENVIRONMENT_INTRO = """\
## Live environment

The blocks below are this session's context at its start: the providers TwiCC
knows about and the session's own state. It's a snapshot — values may drift, so
run `twicc-whoami` for the current state.

TwiCC keeps it current: when something changes, a user message carries a leading
`<twicc:context> … </twicc:context>` block whose `key: value` lines are TwiCC's,
not text the user typed. Each line REPLACES the prior value for that key (latest
wins); a block lists ONLY what changed, and omitted keys keep their value. Treat
these as part of this environment.
"""


# Resolved agent-settings fields surfaced in the dynamic block, in render order.
_AGENT_SETTINGS_FIELDS: tuple[str, ...] = (
    "permission_mode",
    "selected_model",
    "effort",
    "thinking_enabled",
    "context_max",
    "claude_in_chrome",
    "fast_mode",
    "question_widget",
)


def _workspaces_containing(project_id: str) -> list[tuple[str, str]]:
    """Return the ``(id, name)`` of workspaces that list ``project_id``."""
    from twicc.workspaces import read_workspaces

    data = read_workspaces()
    result: list[tuple[str, str]] = []
    for ws in data.get("workspaces", []):
        if project_id in ws.get("projectIds", []):
            result.append((ws.get("id") or "?", ws.get("name") or ""))
    return result


def _format_workspaces(pairs: list[tuple[str, str]]) -> str:
    """Render workspaces as ``<id> (name: X), <id2> (name: Y)`` — mirrors the project line."""
    parts: list[str] = []
    for ws_id, name in pairs:
        part = ws_id
        if name:
            part += f" (name: {name})"
        parts.append(part)
    return ", ".join(parts)


def _project_descriptor(project_id: str) -> str:
    """Return the ``project`` line value: ``<id> (name: X) — <dir>`` (parts present).

    Shared by :func:`build_dynamic_block` (start-time addendum) and
    :func:`mutable_context_fields_from_resolved` (mid-session reconciliation) so
    the project name — the one mutable piece — is formatted identically in both.
    """
    from twicc.core.models import Project

    descriptor = project_id
    project = Project.objects.filter(id=project_id).only("name", "directory").first()
    if project is not None:
        if project.name:
            descriptor += f" (name: {project.name})"
        if project.directory:
            descriptor += f" — {project.directory}"
    return descriptor


def _interaction_mode_value(question_widget: bool | None, hidden: bool) -> str:
    """Return the ``interaction mode`` value (scripted when hidden / widgets off).

    Shared by :func:`build_dynamic_block` and
    :func:`mutable_context_fields_from_resolved`.
    """
    if question_widget is False or hidden:
        return (
            "scripted (no UI dialogs; questions land in the conversation "
            "as plain text)"
        )
    return "interactive (the user can answer UI widgets)"


def _agent_setting_annotation(field: str, value: object, *, helpers) -> str | None:
    """Parenthetical note for an agent-setting value in the Context block, or ``None``.

    Shared by :func:`build_dynamic_block` and
    :func:`mutable_context_fields_from_resolved` so the addendum and the
    mid-session reconciliation annotate identically. Today:

    - ``selected_model`` → the model family and version (from the registry);
    - any field that has a description entry (``permission_mode``, ``fast_mode``,
      ...) → that description text.

    Best-effort: never raises (it runs in the addendum-composition hot path).
    """
    try:
        if field == "selected_model":
            mv = helpers.find_model(value)
            return f"family: {mv.model}, version: {mv.version}" if mv else None
        description = helpers.get_agent_settings_descriptions().get(field, {}).get(value)
        return description or None
    except Exception:
        return None


def _provider_entries() -> list[str]:
    """Render each provider as ``key (enabled|disabled, default)``.

    Shared by the addendum's ``### Providers`` section
    (:func:`_build_providers_lines`) and the reconciled ``providers`` snapshot key
    (:func:`_providers_descriptor`) so both stay identical. The enabled/disabled
    state (always stated) and the ``default`` flag are runtime-mutable (toggled
    via settings); the set of registered providers itself is fixed at boot.
    """
    from twicc.providers.helpers import get_provider_helpers_registry
    from twicc.providers.state import is_provider_enabled
    from twicc.synced_settings import read_synced_settings

    default_provider = read_synced_settings().get("defaultProvider")
    entries: list[str] = []
    # The registry is not a dict — it exposes get/items/values, no keys().
    for prov, _ in get_provider_helpers_registry().items():  # noqa: PERF102
        flags = ["enabled" if is_provider_enabled(prov) else "disabled"]
        if prov.value == default_provider:
            flags.append("default")
        entries.append(f"{prov.value} ({', '.join(flags)})")
    return entries


def _build_providers_lines() -> list[str]:
    """Render the ``### Providers`` block — one ``- `` bullet per provider."""
    return [f"- {entry}" for entry in _provider_entries()]


def _providers_descriptor() -> str:
    """One-line providers list for the reconciled ``providers`` Context key."""
    return ", ".join(_provider_entries())


def build_dynamic_block(
    *,
    provider: str,
    project_id: str,
    resolved_settings: AgentSettings,
    session_id: str | None = None,
    started_at: datetime | None = None,
    spawned_by_id: str | None = None,
    spawned_by_title: str | None = None,
    spawned_by_project_id: str | None = None,
    hidden: bool = False,
    annotations: dict | None = None,
    ephemeral: bool = False,
) -> str:
    """Compose the per-session context block from primitives.

    Pass ``session_id`` only when the caller knows the canonical id at start
    (Claude Code does; Codex mints it inside ``thread_start`` and should
    leave this ``None``).

    ``hidden`` / ``spawned_by_id`` / ``annotations`` come from the pending
    session attributes for new sessions (see
    :mod:`twicc.pending_session_attributes`) or from the ``Session`` row
    on resume. The caller decides where to read them.
    """
    from twicc.paths import get_artifacts_dir, get_scratch_dir

    lines: list[str] = [_LIVE_ENVIRONMENT_INTRO, "### Providers", ""]
    lines.extend(_build_providers_lines())

    lines.extend(["", "### Context", ""])

    if session_id and not ephemeral:
        lines.append(f"- session_id: {session_id}")

    # Absolute, resolved root for session-scoped artifacts. Per-session
    # files live at ``<artifacts_base_dir>/<session_id>/<artifact_file_name>``;
    # the static addendum above references this base via ``{artifacts_base_dir}``.
    if not ephemeral:
        lines.append(f"- artifacts_base_dir: {get_artifacts_dir()}")

    # Absolute, resolved root for scratch files (throwaway work space, or a
    # shared folder for an orchestration tree). The per-session subdir is
    # pre-created at agent start; the static addendum above references this
    # base via ``{scratch_base_dir}``.
    if not ephemeral:
        lines.append(f"- scratch_base_dir: {get_scratch_dir()}")
    else:
        lines.append("- ephemeral: true")

    lines.append(f"- project: {_project_descriptor(project_id)}")

    workspaces = _workspaces_containing(project_id)
    if workspaces:
        lines.append(f"- workspaces: {_format_workspaces(workspaces)}")

    helpers = get_provider_helpers(Provider(provider))
    label = helpers.LABEL or provider
    lines.append(f"- provider: {label} ({provider})")

    if started_at is not None:
        lines.append(f"- started at: {started_at.isoformat()}")

    if spawned_by_id:
        # Title quoted right after the id, mirroring the sender header of an
        # inter-session message. Not truncated — like the project name and the
        # workspace names above, the block states the value as it is.
        parent_line = f"- spawned by: {spawned_by_id}"
        parent_title = (spawned_by_title or "").strip()
        if parent_title:
            parent_line += f' ("{parent_title}")'
        if spawned_by_project_id:
            parent_line += f" (project: {spawned_by_project_id})"
        lines.append(parent_line)

    if hidden:
        lines.append("- hidden: true")

    lines.append(
        "- interaction mode: "
        + _interaction_mode_value(resolved_settings.question_widget, hidden)
    )

    settings_lines: list[str] = []
    for field in _AGENT_SETTINGS_FIELDS:
        value = getattr(resolved_settings, field)
        if value is None:
            continue
        line = f"  - {field}: {value}"
        annotation = _agent_setting_annotation(field, value, helpers=helpers)
        if annotation:
            line += f" ({annotation})"
        settings_lines.append(line)
    if settings_lines:
        lines.append("- agent settings (resolved):")
        lines.extend(settings_lines)

    if annotations:
        lines.append("- annotations: " + orjson.dumps(annotations).decode())

    return "\n".join(lines)


def compose_addendum(
    *,
    provider: str,
    project_id: str,
    resolved_settings: AgentSettings,
    session_id: str | None = None,
    started_at: datetime | None = None,
    spawned_by_id: str | None = None,
    spawned_by_title: str | None = None,
    spawned_by_project_id: str | None = None,
    hidden: bool = False,
    annotations: dict | None = None,
    ephemeral: bool = False,
) -> str:
    """Concatenate the static and dynamic blocks.

    Returns text ready to pass as ``append`` (Claude Code) or
    ``developer_instructions`` (Codex). See :func:`build_dynamic_block` for
    the primitives.
    """
    dynamic = build_dynamic_block(
        provider=provider,
        project_id=project_id,
        resolved_settings=resolved_settings,
        session_id=session_id,
        started_at=started_at,
        spawned_by_id=spawned_by_id,
        spawned_by_title=spawned_by_title,
        spawned_by_project_id=spawned_by_project_id,
        hidden=hidden,
        annotations=annotations,
        ephemeral=ephemeral,
    )
    if ephemeral:
        return (
            "This run is ephemeral. TwiCC and the provider do not write a local session transcript.\n"
            "There is no TwiCC session id, artifact directory, scratch directory, or TwiCC tool access.\n"
            "Deliver the result in your final answer or through requested changes in the project directory.\n"
            "Inherited MCP servers are disabled. Codex plugins are disabled for this run.\n\n"
            + ("For Codex subagents, set fork_turns to 'none'; ephemeral threads cannot fork stored history.\n"
               if provider == Provider.CODEX.value else "")
            + dynamic + "\n"
        )
    parts: list[str] = [STATIC_ADDENDUM]
    helpers = get_provider_helpers(Provider(provider))
    provider_static = helpers.SYSTEM_PROMPT_STATIC_ADDENDUM
    if provider_static:
        parts.append(provider_static)
    parts.append(dynamic)
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# Mutable Context snapshot — the drifting subset of the dynamic block, used by
# the per-turn environment reconciliation (see :mod:`twicc.context_injection`).
# --------------------------------------------------------------------------

def mutable_context_fields_from_resolved(
    *,
    provider: Provider,
    project_id: str,
    resolved_settings: AgentSettings,
    hidden: bool,
    annotations: dict | None,
) -> dict[str, str]:
    """Flat ``{label: value}`` of the MUTABLE part of the dynamic ``### Context`` block.

    The subset that can drift after launch — the active providers (enabled /
    default), project name, workspaces, hidden, interaction mode, the resolved
    agent settings, annotations — rendered with
    the SAME vocabulary as :func:`build_dynamic_block` (shared value/annotation
    helpers, so the addendum and the reconciliation never diverge). Agent-settings
    keys carry the ``Agent settings: `` prefix so a flat ``key: value`` channel
    mirrors the addendum's indented ``agent settings (resolved):`` block, and each
    gets the same parenthetical annotation as the addendum (model family/version,
    permission_mode / fast_mode description, ...). Workspaces render as
    ``<id> (name: X)`` like the project line. Empty states are explicit sentinels
    (``(none)`` / ``false``) so a transition to empty is a detectable change
    rather than a silent key removal. Immutable fields (session_id, artifacts_dir,
    project id/dir, provider, started_at, spawned_by) are NOT included — the agent
    keeps them from its addendum / replayed rollout.

    Does NOT include the live gauges ``context usage`` / ``total cost``: those are
    read from the row and added by :func:`mutable_context_fields_for_session`, so
    they stay out of the addendum and the fresh-start seed (the first message).

    ``resolved_settings`` must already be resolved+enforced. Used to seed the
    reconciliation baseline at a fresh start (:func:`seed_environment_baseline`)
    and, via :func:`mutable_context_fields_for_session`, to recompute the
    snapshot at each turn.
    """
    helpers = get_provider_helpers(provider)
    workspaces = _workspaces_containing(project_id)
    fields: dict[str, str] = {
        "providers": _providers_descriptor(),
        "project": _project_descriptor(project_id),
        "workspaces": _format_workspaces(workspaces) if workspaces else "(none)",
        "hidden": "true" if hidden else "false",
        "interaction mode": _interaction_mode_value(
            resolved_settings.question_widget, hidden,
        ),
    }
    for field in _AGENT_SETTINGS_FIELDS:
        value = getattr(resolved_settings, field)
        if value is None:
            continue
        rendered = str(value)
        annotation = _agent_setting_annotation(field, value, helpers=helpers)
        if annotation:
            rendered += f" ({annotation})"
        fields[f"Agent settings: {field}"] = rendered
    fields["annotations"] = (
        orjson.dumps(annotations).decode() if annotations else "(none)"
    )
    return fields


async def mutable_context_fields_for_session(session_id: str) -> dict[str, str] | None:
    """Recompute the mutable Context snapshot for a live session from the DB.

    Reads the source of truth (the ``Session`` row, its ``Project`` and the
    workspaces file), resolves+enforces the agent settings for the owning
    provider, and returns the shape of :func:`mutable_context_fields_from_resolved`
    PLUS two live gauges read straight off the row — ``context usage`` (tokens in
    use, with the % of ``context_max``) and ``total cost`` (session cost in USD).
    Both are absent from the addendum / fresh-start seed and only appear once
    there is real data, so a brand-new session's first message carries neither;
    they then climb every turn and the diff re-injects them on (almost) every
    message, which is intended. Returns ``None`` when the row does not exist yet
    (a fresh session whose JSONL the watcher has not turned into a row — the
    baseline was already seeded from primitives at start, so there is nothing to
    reconcile).
    """
    def _compute() -> dict[str, str] | None:
        from twicc.core.models import Session

        row = (
            Session.objects
            .filter(id=session_id)
            .only(
                "provider", "project_id", "hidden", "annotations",
                "permission_mode", "selected_model", "effort",
                "thinking_enabled", "claude_in_chrome", "fast_mode",
                "context_max", "question_widget",
                "context_usage", "total_cost",
            )
            .first()
        )
        if row is None:
            return None
        try:
            provider = Provider(row.provider)
        except ValueError:
            return None
        helpers = get_provider_helpers(provider)
        resolved = helpers.enforce_agent_settings_consistency(
            helpers.resolve_agent_settings(AgentSettings.from_session(row))
        )
        fields = mutable_context_fields_from_resolved(
            provider=provider,
            project_id=row.project_id,
            resolved_settings=resolved,
            hidden=row.hidden,
            annotations=row.annotations,
        )
        # Live gauges — read from the row, so out of the addendum / fresh-start
        # seed. They climb every turn, so the diff re-injects them on (almost)
        # every message. Guarded on real data (both columns are NULL until the
        # first usage / cost), so a brand-new session never shows them.
        if row.context_usage:
            context_max = resolved.context_max
            if context_max:
                pct = round(row.context_usage / context_max * 100)
                fields["context usage"] = f"{row.context_usage} ({pct}%)"
            else:
                fields["context usage"] = str(row.context_usage)
        if row.total_cost:
            fields["total cost"] = f"${row.total_cost:.4f}"
        return fields

    return await sync_to_async(_compute)()


def seed_environment_baseline(
    *,
    baseline_session_id: str,
    provider: Provider,
    project_id: str,
    agent_settings: AgentSettings,
    hidden: bool,
    annotations: dict | None,
) -> None:
    """Compute the mutable Context snapshot from primitives and seed the baseline.

    Called at a FRESH start so the first
    :func:`twicc.context_injection.reconcile` finds no delta (the values are
    already in the addendum the agent just got). ``agent_settings`` is
    resolved+enforced here (idempotent if already effective).
    ``baseline_session_id`` is the CANONICAL session id used as the
    reconciliation key — for Codex that is the minted thread id, which differs
    from the draft id the pending attributes are keyed by, hence the explicit
    args rather than a ``Session``-row read (the row does not exist yet).
    Synchronous (touches the DB / workspaces file); call via ``sync_to_async``
    from the agent event loop.
    """
    from twicc.context_injection import seed_baseline

    helpers = get_provider_helpers(provider)
    resolved = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(agent_settings)
    )
    fields = mutable_context_fields_from_resolved(
        provider=provider,
        project_id=project_id,
        resolved_settings=resolved,
        hidden=hidden,
        annotations=annotations,
    )
    seed_baseline(baseline_session_id, fields)
