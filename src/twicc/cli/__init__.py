"""
CLI entry point for TwiCC.

Lightweight dispatcher — subcommand modules must be imported lazily inside each
command function so that they never pay for Django startup.
"""

import os

import typer

from twicc.paths import ensure_env_loaded, get_env_load_warnings

# Load the instance ``.env`` into the environment BEFORE any command module is
# imported: ``twicc/__init__.py`` imports this package, so this runs on any
# ``import twicc``, and the command modules below pull the provider modules
# (``create_session.command`` → ``_drop_request/help_context`` →
# ``providers.*.constants``). Only the load happens at import; validation,
# warning output and any exit happen in ``main()`` (a module-level exit would
# kill every Django-only entry point during package import).
ensure_env_loaded()

from twicc.cli._drop_request.project import derive_project_id  # noqa: E402
from twicc.cli._output import emit_error  # noqa: E402
from twicc.version import get_version  # noqa: E402

# Ensure Django settings are discoverable for all subcommands that call django.setup().
# Force to twicc.settings unless already set to a twicc-specific variant (e.g. for tests).
# This prevents a stray DJANGO_SETTINGS_MODULE from another project from breaking twicc.
if not os.environ.get("DJANGO_SETTINGS_MODULE", "").startswith("twicc.settings"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "twicc.settings"


app = typer.Typer(
    name="twicc",
    help="TwiCC — The Web Interface for Claude and Codex.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"twicc {get_version()}")
        raise typer.Exit()


@app.callback()
def _default(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit."),
) -> None:
    """Launch the TwiCC server (default when no subcommand is given)."""
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.run import main as run_main

    run_main()


@app.command()
def run() -> None:
    """Start the TwiCC server (you can commit thr `run` command)."""
    from twicc.cli.run import main as run_main

    run_main()


projects_app = typer.Typer(
    name="projects",
    help="List projects, or look up specific project_ids in batch.",
    invoke_without_command=True,
)
app.add_typer(projects_app)


@projects_app.callback(invoke_without_command=True)
def _projects_default(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of projects to return."),
    offset: int = typer.Option(0, help="Skip first N projects."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived projects."),
    workspace: str = typer.Option(None, "--workspace", help="Filter by workspace ID (only projects belonging to that workspace)."),
) -> None:
    """List all projects as JSON (ordered by most recently active, default action)."""
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.projects import main as projects_main

    projects_main(limit=limit, offset=offset, archived=include_archived, workspace=workspace)


@projects_app.command(name="get")
def _projects_get(
    project_ids: list[str] = typer.Argument(
        ...,
        metavar="PROJECT...",
        help=(
            "One or more projects to look up. Each value is a project ID "
            "(with or without leading dash) or a directory path (absolute or "
            "relative); paths are resolved via realpath and converted to "
            "their canonical id. The output mirrors the input order "
            "(duplicates collapsed by canonical id, first occurrence wins). "
            "Each entry is either the full project metadata or a placeholder "
            "with `known: false` when no Project row exists for that id "
            "(applies the same way to a path that doesn't match any known "
            "project). Archived projects are returned just like active "
            "ones — the listing filter doesn't apply when you name explicit "
            "projects."
        ),
    ),
) -> None:
    """Look up projects by id or path (placeholder for missing, includes archived).

    Unlike ``twicc projects``, ``get`` takes no filter flags: when the
    caller names the projects it cares about, the archived-by-default
    filter would only blur the meaning of the placeholder rows.
    """
    from twicc.cli.projects_get import main as projects_get_main

    projects_get_main([derive_project_id(pid)[0] for pid in project_ids])


@app.command()
def project(
    project_id: str = typer.Argument(
        help=(
            "Project ID (with or without leading dash) or directory path "
            "(absolute or relative)."
        ),
    ),
) -> None:
    """Show a single project as JSON."""
    from twicc.cli.project import main as project_main

    project_main(derive_project_id(project_id)[0])


workspaces_app = typer.Typer(
    name="workspaces",
    help="List workspaces, or look up specific workspace_ids in batch.",
    invoke_without_command=True,
)
app.add_typer(workspaces_app)


@workspaces_app.callback(invoke_without_command=True)
def _workspaces_default(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of workspaces to return."),
    offset: int = typer.Option(0, help="Skip first N workspaces."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived workspaces."),
) -> None:
    """List all workspaces as JSON (in their stored order, default action)."""
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.workspaces import main as workspaces_main

    workspaces_main(limit=limit, offset=offset, archived=include_archived)


@workspaces_app.command(name="get")
def _workspaces_get(
    workspace_ids: list[str] = typer.Argument(
        ...,
        metavar="WORKSPACE_ID...",
        help=(
            "One or more workspace IDs to look up. The output mirrors the "
            "input order (duplicates collapsed, first occurrence wins). "
            "Each entry is either the full workspace definition or a "
            "placeholder with `known: false` when no workspace exists for "
            "that id. Archived workspaces are returned just like active "
            "ones — the listing filter doesn't apply when you name "
            "explicit ids."
        ),
    ),
) -> None:
    """Look up workspaces by id (placeholder for missing, includes archived).

    Unlike ``twicc workspaces``, ``get`` takes no filter flags: when the
    caller names the workspaces it cares about, the archived-by-default
    filter would only blur the meaning of the placeholder rows.
    """
    from twicc.cli.workspaces_get import main as workspaces_get_main

    workspaces_get_main(workspace_ids)


@app.command()
def workspace(
    workspace_id: str = typer.Argument(help="The workspace ID."),
) -> None:
    """Show a single workspace as JSON."""
    from twicc.cli.workspace import main as workspace_main

    workspace_main(workspace_id)


sessions_app = typer.Typer(
    name="sessions",
    help="List sessions, or look up specific session_ids in batch.",
    invoke_without_command=True,
)
app.add_typer(sessions_app)


@sessions_app.callback(invoke_without_command=True)
def _sessions_default(
    ctx: typer.Context,
    project: str = typer.Option(
        None,
        help=(
            "Filter by project: either a project ID (with or without "
            "leading dash) or a directory path (absolute or relative)."
        ),
    ),
    workspace: str = typer.Option(None, "--workspace", help="Filter by workspace ID (only sessions of projects in that workspace, worktrees included). Mutually exclusive with --project."),
    limit: int = typer.Option(20, help="Max number of sessions to return."),
    offset: int = typer.Option(0, help="Skip first N sessions."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived sessions."),
    include_hidden: bool = typer.Option(False, "--include-hidden", help="Include hidden sessions in the listing."),
    only_hidden: bool = typer.Option(False, "--only-hidden", help="Show ONLY hidden sessions (mutually exclusive with --include-hidden)."),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Filter to sessions spawned by the given session_id, 'self' for the "
            "current session, or 'parent' for the session that spawned the current "
            "one (= my siblings, myself included). Implies --include-hidden by "
            "default: a filiation query shows every matching child whatever its "
            "visibility. Add --only-hidden to narrow to hidden children, or pass an "
            "explicit ID and rely on the JSON output's `hidden` field to filter "
            "further. Mutually exclusive with --spawn-tree."
        ),
    ),
    spawn_tree: str = typer.Option(
        None,
        "--spawn-tree",
        help=(
            "Filter to every session in the spawn tree that contains the given "
            "session_id — any id in the tree works (root, middle, or leaf): the "
            "CLI looks it up and resolves to the tree it belongs to. Use 'self' "
            "for the tree containing the current session. Mutually exclusive "
            "with --spawned-by and --descendants. Implies --include-hidden by "
            "default."
        ),
    ),
    descendants: str = typer.Option(
        None,
        "--descendants",
        help=(
            "Filter to the proper descendants of the given session_id (every session "
            "transitively spawned by it, at any depth, target excluded), 'self' for "
            "the descendants of the current session, or 'parent' for the descendants "
            "of the current session's spawner (= my siblings, their subtrees, and my "
            "own subtree). Mutually exclusive with --spawned-by, --spawn-tree and "
            "--siblings. Implies --include-hidden by default."
        ),
    ),
    siblings: str = typer.Option(
        None,
        "--siblings",
        help=(
            "Filter to the siblings of the given session_id — the other sessions "
            "spawned by the same parent — or 'self' for the current session's "
            "siblings. The target itself is always excluded (use --spawned-by "
            "parent to include yourself). 'parent' is not supported. Mutually "
            "exclusive with --spawned-by, --spawn-tree and --descendants. Implies "
            "--include-hidden by default."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Filter sessions by annotation. Repeatable, AND-combined. "
            "Operators: KEY=VALUE, KEY!=VALUE, KEY:exists, KEY:not-exists, "
            "KEY:in:V1,V2. KEY is a dotted path. Values are typed "
            "(true/false/null/int/float/string), same rules as "
            "create-session --annotation. See twicc-sessions skill for details."
        ),
    ),
) -> None:
    """List sessions as JSON (ordered by most recently active, default action)."""
    if ctx.invoked_subcommand is not None:
        return

    if include_hidden and only_hidden:
        emit_error("Error: --include-hidden and --only-hidden are mutually exclusive.", code=2)

    if sum(x is not None for x in (spawned_by, spawn_tree, descendants, siblings)) > 1:
        emit_error(
            "Error: --spawned-by, --spawn-tree, --descendants and --siblings are mutually exclusive.",
            code=2,
        )

    if project is not None and workspace is not None:
        # Their scopes (project + its worktrees / workspace members + their
        # worktrees) only ever intersect to "the project's scope" (when the
        # project belongs to the workspace) or to nothing — never a useful
        # different subset. Reject rather than silently return either.
        emit_error("Error: --project and --workspace are mutually exclusive.", code=2)

    from twicc.cli.sessions import main as sessions_main

    sessions_main(
        project=derive_project_id(project)[0] if project is not None else None,
        workspace=workspace,
        limit=limit,
        offset=offset,
        archived=include_archived,
        include_hidden=include_hidden,
        only_hidden=only_hidden,
        spawned_by=spawned_by,
        spawn_tree=spawn_tree,
        descendants=descendants,
        siblings=siblings,
        annotation=annotation,
    )


@sessions_app.command(name="get")
def _sessions_get(
    session_ids: list[str] = typer.Argument(
        ...,
        metavar="SESSION_ID...",
        help=(
            "One or more session IDs to look up. The output mirrors the input "
            "order (duplicates collapsed, first occurrence wins). Each entry "
            "is either the full session metadata or a placeholder with "
            "`known: false` when no Session row exists for that id. "
            "Subagents, archived and hidden sessions are returned just like "
            "regular ones — the listing filters don't apply when you name "
            "explicit ids."
        ),
    ),
) -> None:
    """Look up sessions by id (placeholder for missing, includes subagents).

    Unlike ``twicc sessions``, ``get`` takes no filter flags: when the
    caller names the sessions it cares about, layering archived /
    hidden / subagent filters on top would only blur the meaning of the
    placeholder rows.
    """
    from twicc.cli.sessions_get import main as sessions_get_main

    sessions_get_main(session_ids)


session_app = typer.Typer(
    name="session",
    help="Inspect a session.",
    invoke_without_command=True,
)
app.add_typer(session_app)


@session_app.callback(invoke_without_command=True)
def _session_default(
    ctx: typer.Context,
    session_id: str = typer.Argument(help="The session ID (for normal sessions or agents) to look up."),
) -> None:
    """Show a single session as JSON."""
    ctx.obj = session_id
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.session import main as session_main

    session_main(session_id)


@session_app.command()
def content(
    ctx: typer.Context,
    range: str = typer.Argument(None, help="Line number or range (e.g. '5' or '10-20'). Optional when --contains is given."),
    contains: list[str] = typer.Option(
        [],
        "--contains",
        help=(
            "Case-insensitive substring filter on the raw item content. "
            "Repeatable, AND-combined (an item must contain every term). "
            "Combinable with a line/range to scope the search."
        ),
    ),
) -> None:
    """Show session item(s) content as JSON."""
    from twicc.cli.session import content as session_content

    session_content(ctx.obj, range_str=range, contains=contains)


@session_app.command()
def messages(
    ctx: typer.Context,
    range: str = typer.Option(None, "--range", help="Restrict to a line number or range (e.g. '5' or '10-20')."),
    role: str = typer.Option(None, "--role", help="Filter by author: 'user' or 'assistant'."),
    contains: list[str] = typer.Option(
        [],
        "--contains",
        help=(
            "Case-insensitive substring filter on the message text. "
            "Repeatable, AND-combined (a message must contain every term). "
            "Applied before --tail/--limit/--offset."
        ),
    ),
    limit: int = typer.Option(None, "--limit", help="Max number of messages to return (default: no limit)."),
    offset: int = typer.Option(0, "--offset", help="Skip first N messages."),
    tail: int = typer.Option(None, "--tail", help="Return the last N messages (mutually exclusive with --limit/--offset)."),
) -> None:
    """Show all user/assistant messages of a session as JSON (cross-provider)."""
    from twicc.cli.session import messages as session_messages

    session_messages(ctx.obj, range_str=range, role=role, contains=contains, limit=limit, offset=offset, tail=tail)


@session_app.command()
def agents(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of subagents to return."),
    offset: int = typer.Option(0, help="Skip first N subagents."),
) -> None:
    """List subagents of a session as JSON."""
    from twicc.cli.session import agents as session_agents

    session_agents(ctx.obj, limit=limit, offset=offset)


@session_app.command()
def plan(
    ctx: typer.Context,
    path: str = typer.Argument(
        None,
        help="A tracked document's path (project-relative or absolute, see --list). Default: the most recently updated one.",
    ),
    list_docs: bool = typer.Option(False, "--list", help="List every tracked plan-like document (plan_paths)."),
) -> None:
    """Show a tracked plan document as JSON (most recently updated by default; --list to enumerate them)."""
    from twicc.cli.session import plan as session_plan

    session_plan(ctx.obj, list_docs=list_docs, doc_path=path)


@session_app.command()
def workflows(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of workflows to return."),
    offset: int = typer.Option(0, help="Skip first N workflows."),
) -> None:
    """List the session's workflows as JSON (Claude Code only)."""
    from twicc.cli.session import workflows as session_workflows

    session_workflows(ctx.obj, limit=limit, offset=offset)


@session_app.command()
def workflow(
    ctx: typer.Context,
    id: str = typer.Argument(help="The workflow ID."),
) -> None:
    """Show one of the session's workflows as JSON (Claude Code only)."""
    from twicc.cli.session import workflow as session_workflow

    session_workflow(ctx.obj, id)


artifacts_app = typer.Typer(
    name="artifacts",
    help="List bookmarked artifacts, or bookmark / unbookmark one.",
    invoke_without_command=True,
)
app.add_typer(artifacts_app)


# Mirrors :class:`twicc.core.models.PinMode`. Kept as a flat tuple so the CLI
# validates a scope locally without importing Django (the check happens before
# ``django.setup()`` so --help / validation paths stay fast).
_VALID_ARTIFACT_SCOPES: tuple[str, ...] = ("project", "workspace", "all")


def _validate_artifact_scope(scope: str | None) -> None:
    """Reject an unknown ``--scope`` value with the standard validation envelope."""
    if scope is None or scope in _VALID_ARTIFACT_SCOPES:
        return
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    emit_validation_errors([ValidationError(
        "--scope", "invalid_scope",
        f"Unknown scope {scope!r}. Accepted: {list(_VALID_ARTIFACT_SCOPES)}.",
    )])
    raise typer.Exit(1)


@artifacts_app.callback(invoke_without_command=True)
def _artifacts_default(
    ctx: typer.Context,
    project: str = typer.Option(
        None,
        help=(
            "Filter by project: a project ID (with or without leading dash) or a "
            "directory path (absolute or relative). Includes the project's git "
            "worktrees. Mutually exclusive with --workspace."
        ),
    ),
    workspace: str = typer.Option(
        None,
        "--workspace",
        help=(
            "Filter by workspace ID: bookmarks of the workspace's projects, each "
            "member's git worktrees included. Mutually exclusive with --project."
        ),
    ),
    scope: str = typer.Option(
        None,
        "--scope",
        help=(
            "Filter by each bookmark's own visibility scope: 'project', "
            "'workspace', or 'all'. E.g. --scope all for the ones bookmarked "
            "everywhere. Independent of --project / --workspace."
        ),
    ),
    limit: int = typer.Option(20, help="Max number of bookmarks to return."),
    offset: int = typer.Option(0, help="Skip first N bookmarks."),
) -> None:
    """List bookmarked artifacts as JSON (most recently updated first, default action)."""
    if ctx.invoked_subcommand is not None:
        return

    if project is not None and workspace is not None:
        emit_error("Error: --project and --workspace are mutually exclusive.", code=2)

    from twicc.cli.artifacts import main as artifacts_main

    artifacts_main(
        project=derive_project_id(project)[0] if project is not None else None,
        workspace=workspace,
        scope=scope,
        limit=limit,
        offset=offset,
    )


@artifacts_app.command(name="bookmark")
def _artifacts_bookmark(
    session_id: str = typer.Argument(help="The session that owns the artifact: a session id, 'self', or 'parent'."),
    path: str = typer.Argument(
        metavar="PATH",
        help=(
            "Artifact path, relative to the session's artifacts directory (the "
            "value `twicc artifacts` prints as relative_path), or an absolute path "
            "confined to that directory."
        ),
    ),
    name: str = typer.Option(..., "--name", help="Bookmark name (required)."),
    scope: str = typer.Option(
        None,
        "--scope",
        help=(
            "Visibility scope: 'project' (default on create), 'workspace', or "
            "'all'. Omit when updating to keep the current scope."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. The "
            "request stays on disk; the write may still apply on the server side."
        ),
    ),
) -> None:
    """Bookmark an artifact (or rename / re-scope an existing bookmark).

    Upserts on the (session, path) key — same effect as the UI's bookmark
    button + dialog. Requires the live TwiCC server: the write is broadcast so
    open UIs refresh.
    """
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    session_id = resolve_session_keyword(
        session_id, param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    )
    _validate_artifact_scope(scope)

    from twicc.cli.artifacts_mutation import run_bookmark

    run_bookmark(session_id=session_id, path=path, name=name, scope=scope, timeout=timeout)


@artifacts_app.command(name="unbookmark")
def _artifacts_unbookmark(
    session_id: str = typer.Argument(help="The session that owns the artifact: a session id, 'self', or 'parent'."),
    path: str = typer.Argument(
        metavar="PATH",
        help=(
            "Artifact path, relative to the session's artifacts directory (the "
            "value `twicc artifacts` prints as relative_path), or an absolute path "
            "confined to that directory."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. The "
            "request stays on disk; the removal may still apply on the server side."
        ),
    ),
) -> None:
    """Remove an artifact bookmark by its (session, path) key.

    Symmetric with ``bookmark`` — the artifact file need not still exist on
    disk. Requires the live TwiCC server.
    """
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    session_id = resolve_session_keyword(
        session_id, param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    )
    from twicc.cli.artifacts_mutation import run_unbookmark

    run_unbookmark(session_id=session_id, path=path, timeout=timeout)


share_app = typer.Typer(name="share", help="List / show shares (read). Manage share links (create/revoke/…).", invoke_without_command=True)
app.add_typer(share_app)


@share_app.callback(invoke_without_command=True)
def _share_default(
    ctx: typer.Context,
    kind: str = typer.Option(None, "--kind", help="Filter by kind: session | artifact."),
    session: str = typer.Option(None, "--session", help="Filter by session id; accepts 'self' and 'parent'."),
    project: str = typer.Option(None, "--project", help="Filter by project (worktrees included)."),
    include_revoked: bool = typer.Option(False, "--include-revoked", help="Include revoked shares."),
    limit: int = typer.Option(50), offset: int = typer.Option(0),
) -> None:
    """List shares as JSON (default action; read-only, direct DB)."""
    if ctx.invoked_subcommand is not None:
        return
    if session is not None:
        from twicc.cli._session_keywords import (
            SELF_PARENT_KEYWORDS,
            resolve_session_keyword,
        )

        session = resolve_session_keyword(
            session, param_name="--session", allowed=SELF_PARENT_KEYWORDS,
        )
    from twicc.cli.share import list_main
    list_main(kind=kind, session=session,
              project=derive_project_id(project)[0] if project else None,
              include_revoked=include_revoked, limit=limit, offset=offset)


@share_app.command(name="show")
def _share_show(share_id: str = typer.Argument(help="Share id (shr_…).")) -> None:
    """Show one share as JSON (read-only)."""
    from twicc.cli.share import show_main
    show_main(share_id)


# ── Mutation commands (agent-gated: two Settings → Sharing switches + the
#    spawn-subtree scope — core/services/share_agent_gate.py; the human
#    surfaces bypass the gate. Design: docs/plans/2026-08-10-agent-sharing-design.md) ──
share_create_app = typer.Typer(name="create", help="Create a share link.")
share_app.add_typer(share_create_app)


@share_create_app.command(name="session")
def _share_create_session(
    session_id: str = typer.Argument(help="Session to share: a session id, 'self', or 'parent'."),
    label: str = typer.Option("", "--label"),
    password: str = typer.Option(None, "--password"),
    expires: str = typer.Option(None, "--expires", help="ISO 8601."),
    live: bool | None = typer.Option(
        None, "--live/--frozen",
        help="Live-follow or snapshot. Default: live for a human caller, frozen for an agent caller.",
    ),
    max_display: str = typer.Option("normal", "--max-display"),
    include_subagents: bool = typer.Option(True, "--include-subagents/--no-subagents"),
    title: str = typer.Option(None, "--title", help="Public title shown to viewers (default: the session title). Ignored with --no-title."),
    show_title: bool = typer.Option(True, "--show-title/--no-title", help="Show a title to viewers; --no-title shows a generic label instead."),
    timeout: int = typer.Option(30, "--timeout"),
) -> None:
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    session_id = resolve_session_keyword(
        session_id, param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    )
    from twicc.cli.share_mutation import run_create_session
    run_create_session(
        session_id=session_id, label=label, password=password, expires_at=expires,
        mode=None if live is None else ("live" if live else "snapshot"),
        options={"max_display_mode": max_display, "include_subagents": include_subagents,
                 "show_title": show_title, "display_title": (title or "") if show_title else ""},
        timeout=timeout,
    )


@share_create_app.command(name="artifact")
def _share_create_artifact(
    bookmark_id: int = typer.Argument(...),
    label: str = typer.Option("", "--label"),
    password: str = typer.Option(None, "--password"),
    expires: str = typer.Option(None, "--expires"),
    title: str = typer.Option(None, "--title", help="Public title shown to viewers (default: the bookmark name). Ignored with --no-title."),
    show_title: bool = typer.Option(True, "--show-title/--no-title", help="Show a title to viewers; --no-title shows a generic label instead."),
    timeout: int = typer.Option(30, "--timeout"),
) -> None:
    from twicc.cli.share_mutation import run_create_artifact
    run_create_artifact(
        bookmark_id=bookmark_id, label=label, password=password, expires_at=expires,
        options={"show_title": show_title, "display_title": (title or "") if show_title else ""},
        timeout=timeout,
    )


@share_app.command(name="revoke")
def _share_revoke(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:revoke", success="updated", timeout=timeout)


@share_app.command(name="unrevoke")
def _share_unrevoke(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:unrevoke", success="updated", timeout=timeout)


@share_app.command(name="delete")
def _share_delete(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:delete", success="deleted", timeout=timeout)


@share_app.command(name="propagate")
def _share_propagate(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:propagate", success="updated", timeout=timeout)


@share_app.command(name="update")
def _share_update(
    share_id: str = typer.Argument(...),
    label: str = typer.Option(None, "--label"),
    password: str = typer.Option(None, "--password"),
    expires: str = typer.Option(None, "--expires"),
    timeout: int = typer.Option(30),
) -> None:
    from twicc.cli.share_mutation import run_update
    fields = {}
    if label is not None:
        fields["label"] = label
    if password is not None:
        fields["password"] = password
    if expires is not None:
        fields["expires_at"] = expires or None
    run_update(share_id=share_id, fields=fields, timeout=timeout)


@app.command()
def usage() -> None:
    """Show the latest usage quota snapshot as JSON."""
    from twicc.cli.usage import main as usage_main

    usage_main()


@app.command()
def topology(
    session_id: str = typer.Argument(
        help=(
            "Session ID to anchor the topology, or 'self' from inside an "
            "agent session."
        ),
    ),
    processes: bool = typer.Option(
        True,
        "--processes/--no-processes",
        help=(
            "Include compact live process state when a TwiCC backend is running. "
            "If no backend is running, topology is still returned with process "
            "data marked unavailable."
        ),
    ),
    full_sessions: bool = typer.Option(
        False,
        "--full-sessions/--no-full-sessions",
        help=(
            "Emit the full session serialization for every node (same shape as "
            "``twicc session <id>``). Disabled by default: each node carries a "
            "slim subset (id, project_id, provider, title, annotations, "
            "spawned_by, spawn_root, created_at, last_new_content_at, "
            "context_usage, context_max, total_cost, directory). Use this when "
            "you need fields like timestamps, costs breakdown or agent "
            "settings; otherwise call ``twicc session <id>`` for the few nodes "
            "you actually care about."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Filter sessions by annotation. Repeatable, AND-combined. "
            "Operators: KEY=VALUE, KEY!=VALUE, KEY:exists, KEY:not-exists, "
            "KEY:in:V1,V2. KEY is a dotted path. Values are typed "
            "(true/false/null/int/float/string), same rules as "
            "create-session --annotation. See twicc-sessions skill for details."
        ),
    ),
    siblings: bool = typer.Option(
        False,
        "--siblings",
        help=(
            "Mark the anchor session's siblings (the other sessions spawned by "
            "its parent, anchor excluded) with a `matches_siblings` flag on "
            "every node. The full tree is preserved — this annotates, it does "
            "not prune. Combinable with --annotation."
        ),
    ),
) -> None:
    """Show the spawned-session tree containing a session as JSON."""
    from twicc.cli.topology import main as topology_main

    topology_main(
        session_id,
        include_processes=processes,
        full_sessions=full_sessions,
        annotation=annotation,
        siblings=siblings,
    )


processes_app = typer.Typer(
    name="processes",
    help="List live TwiCC processes, or look up specific session_ids.",
    invoke_without_command=True,
)
app.add_typer(processes_app)


@processes_app.callback(invoke_without_command=True)
def _processes_default(
    ctx: typer.Context,
    provider: str = typer.Option(None, "--provider", help="Filter by backend provider (e.g. 'claude_code', 'codex')."),
    state: str = typer.Option(
        None,
        "--state",
        help=(
            "Filter by state: 'starting', 'assistant_turn' (actively generating), "
            "'awaiting_user_input' (blocked on a user click), or 'user_turn' "
            "(turn finished, awaiting next user message). 'dead' is never returned."
        ),
    ),
    limit: int = typer.Option(20, help="Max number of processes to return."),
    offset: int = typer.Option(0, help="Skip first N processes."),
    include_hidden: bool = typer.Option(False, "--include-hidden", help="Include processes of hidden sessions."),
    only_hidden: bool = typer.Option(False, "--only-hidden", help="Show ONLY processes of hidden sessions (mutually exclusive with --include-hidden)."),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Filter to processes of sessions spawned by the given session_id, 'self' "
            "for the current session, or 'parent' for the session that spawned the "
            "current one (= my siblings, myself included). Implies --include-hidden "
            "by default: a filiation query surfaces every matching child whatever "
            "its visibility. Mutually exclusive with --spawn-tree."
        ),
    ),
    spawn_tree: str = typer.Option(
        None,
        "--spawn-tree",
        help=(
            "Filter to processes of every session in the spawn tree that contains "
            "the given session_id — any id in the tree works (root, middle, or "
            "leaf): the CLI looks it up and resolves to the tree it belongs to. "
            "Use 'self' for the tree containing the current session. Mutually "
            "exclusive with --spawned-by and --descendants. Implies "
            "--include-hidden by default."
        ),
    ),
    descendants: str = typer.Option(
        None,
        "--descendants",
        help=(
            "Filter to processes of the proper descendants of the given session_id "
            "(every session transitively spawned by it, at any depth, target "
            "excluded), 'self' for the descendants of the current session, or "
            "'parent' for the descendants of the current session's spawner (= my "
            "siblings, their subtrees, and my own subtree). Mutually exclusive with "
            "--spawned-by, --spawn-tree and --siblings. Implies --include-hidden by default."
        ),
    ),
    siblings: str = typer.Option(
        None,
        "--siblings",
        help=(
            "Filter to processes of the siblings of the given session_id — the "
            "other sessions spawned by the same parent — or 'self' for the current "
            "session's siblings. The target itself is always excluded (use "
            "--spawned-by parent to include yourself). 'parent' is not supported. "
            "Mutually exclusive with --spawned-by, --spawn-tree and --descendants. "
            "Implies --include-hidden by default."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Filter sessions by annotation. Repeatable, AND-combined. "
            "Operators: KEY=VALUE, KEY!=VALUE, KEY:exists, KEY:not-exists, "
            "KEY:in:V1,V2. KEY is a dotted path. Values are typed "
            "(true/false/null/int/float/string), same rules as "
            "create-session --annotation. Requires --spawned-by, --spawn-tree, "
            "--descendants, or --siblings. See twicc-sessions skill for details."
        ),
    ),
) -> None:
    """List currently running processes of the live TwiCC instance as JSON (default action)."""
    if ctx.invoked_subcommand is not None:
        return

    if include_hidden and only_hidden:
        emit_error("Error: --include-hidden and --only-hidden are mutually exclusive.", code=2)

    if sum(x is not None for x in (spawned_by, spawn_tree, descendants, siblings)) > 1:
        emit_error(
            "Error: --spawned-by, --spawn-tree, --descendants and --siblings are mutually exclusive.",
            code=2,
        )

    if annotation and not any((spawned_by, spawn_tree, descendants, siblings)):
        emit_error(
            "Error: --annotation requires --spawned-by, --spawn-tree, "
            "--descendants, or --siblings on processes listing.",
            code=2,
        )

    from twicc.cli.processes import main as processes_main

    processes_main(
        provider=provider,
        state=state,
        limit=limit,
        offset=offset,
        include_hidden=include_hidden,
        only_hidden=only_hidden,
        spawned_by=spawned_by,
        spawn_tree=spawn_tree,
        descendants=descendants,
        siblings=siblings,
        annotation=annotation,
    )


@processes_app.command(name="get")
def _processes_get(
    session_ids: list[str] = typer.Argument(
        ...,
        metavar="SESSION_ID...",
        help=(
            "One or more session IDs to look up. The output mirrors the input "
            "order (duplicates collapsed, first occurrence wins). Each entry "
            "is either the live process row or a placeholder with state=\"dead\" "
            "when no live process exists for that ID; a session_known flag "
            "distinguishes typos from genuinely-stopped sessions."
        ),
    ),
) -> None:
    """Look up live process state for one or more session_ids (placeholder for missing).

    Unlike ``twicc processes``, ``get`` takes no filter flags: when the
    caller names the sessions it cares about, layering ``--provider`` /
    ``--state`` would only blur the meaning of the placeholder rows.
    """
    from twicc.cli.processes_get import main as processes_get_main

    processes_get_main(session_ids)


@processes_app.command(name="stop")
def _processes_stop(
    session_ids: list[str] | None = typer.Argument(
        None,
        metavar="SESSION_ID...",
        help=(
            "Optional session IDs whose live agent process should be stopped. "
            "If omitted, pass --spawned-by or --descendants to select "
            "sessions; --annotation can narrow that scope."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status across the whole "
            "batch (drops are processed in parallel server-side, so this is "
            "a wall-clock budget, not N×30). Entries with no final status "
            "by the deadline are reported with status=\"timeout\". Must be > 0."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Hard kill every selected process: SIGKILL the process tree now, "
            "bypassing the grace window (no clean turn finalization). For "
            "wedged processes."
        ),
    ),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Stop processes of sessions spawned by the given session_id, 'self' "
            "for the current session."
        ),
    ),
    descendants: str = typer.Option(
        None,
        "--descendants",
        help=(
            "Stop processes of the proper descendants of the given session_id, "
            "or 'self'."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Filter selected sessions by annotation. Repeatable, AND-combined. "
            "Requires --spawned-by or --descendants."
        ),
    ),
) -> None:
    """Batch-stop live agent processes (idempotent, tolerant to skipped IDs).

    Explicit session_ids and filtered session_ids are merged, then pre-checked
    locally before dropping kill requests. Exit 0 always when the command
    completes — callers inspect each entry's ``status`` for the per-id outcome.
    """
    if sum(x is not None for x in (spawned_by, descendants)) > 1:
        emit_error(
            "Error: --spawned-by and --descendants are mutually exclusive.",
            code=2,
        )

    from twicc.cli.processes_stop import stop_cmd

    stop_cmd(
        session_ids or [],
        timeout=timeout,
        force=force,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@processes_app.command(name="wait")
def _processes_wait(
    items: list[str] | None = typer.Argument(
        None,
        metavar="ITEM...",
        help=(
            "A single list mixing optional session_ids and required statuses, "
            "auto-discriminated by value. If session_ids are omitted, pass one "
            "scope filter (--spawned-by or --descendants) to select sessions; "
            "--annotation can narrow that scope. Explicit session_ids and "
            "filtered session_ids are merged."
        ),
    ),
    timeout: float = typer.Option(
        ...,
        "--timeout",
        help=(
            "Required. Seconds to wait before giving up (exit 5). Must be > 0. "
            "Wall-clock budget for the entire batch (all session_ids are "
            "polled in parallel)."
        ),
    ),
    wait_all: bool = typer.Option(
        True,
        "--all/--first",
        help=(
            "--all (default): wait until EVERY active session_id has matched "
            "at least one status. --first: stop as soon as one has matched. "
            "Inactive (skipped_unknown) session_ids participate in neither."
        ),
    ),
    transition: bool = typer.Option(
        False,
        "--transition",
        help=(
            "Only evaluate a match for a session_id after observing at least "
            "one state transition since the initial snapshot. Applied "
            "per-session — each id must transition before it can match."
        ),
    ),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Wait on sessions spawned by the given session_id, 'self' for the "
            "current session."
        ),
    ),
    descendants: str = typer.Option(
        None,
        "--descendants",
        help=(
            "Wait on the proper descendants of the given session_id, or "
            "'self' for the current session."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Filter selected sessions by annotation. Repeatable, AND-combined. "
            "Requires --spawned-by or --descendants."
        ),
    ),
) -> None:
    """Block until multiple session_ids reach matching virtual states.

    Explicit session_ids and filtered session_ids are merged into one wait pool.
    Unknown explicit session_ids (no Session row AND no ProcessRun for this
    TwiCC) are skipped silently and do NOT participate in --all / --first.
    If every session_id is skipped, exits 0 (vacuous truth — nothing to
    wait for).
    """
    if sum(x is not None for x in (spawned_by, descendants)) > 1:
        emit_error(
            "Error: --spawned-by and --descendants are mutually exclusive.",
            code=2,
        )

    from twicc.cli.processes_wait import wait_cmd

    wait_cmd(
        items or [],
        timeout=timeout,
        wait_all=wait_all,
        transition=transition,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


process_app = typer.Typer(
    name="process",
    help="Inspect or control a session's live process.",
    invoke_without_command=True,
)
app.add_typer(process_app)


@process_app.callback(invoke_without_command=True)
def _process_default(
    ctx: typer.Context,
    session_id: str = typer.Argument(help="The session ID of the running process."),
) -> None:
    """Show the currently running process for a session as JSON (default action)."""
    ctx.obj = session_id
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.process import main as process_main

    process_main(session_id)


@process_app.command(name="stop")
def process_stop(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the kill may still apply on the "
            "server side."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Hard kill: SIGKILL the process tree now, bypassing the grace "
            "window (no clean turn finalization). For a wedged process."
        ),
    ),
) -> None:
    """Stop the live agent process attached to the session.

    Equivalent to clicking the UI's *Stop process* button: asks the agent
    manager to kill the agent with ``reason="manual"``. Idempotent — if no
    live agent is currently attached, the command still exits 0.

    ``--force`` hard-kills (SIGKILL the process tree) without the grace window.
    """
    from twicc.cli.process_stop import stop_cmd

    stop_cmd(
        ctx.obj,
        timeout=timeout,
        force=force,
    )


@process_app.command(name="wait")
def process_wait(
    ctx: typer.Context,
    statuses: list[str] = typer.Argument(
        ...,
        metavar="STATUS...",
        help=(
            "One or more virtual states to wait for (any-of match). "
            "Valid values: starting, assistant_turn, awaiting_user_input, "
            "user_turn, dead. 'dead' matches when no live ProcessRun "
            "exists for the session."
        ),
    ),
    timeout: float = typer.Option(
        ...,
        "--timeout",
        help=(
            "Required. Seconds to wait for a matching state before giving "
            "up (exit 5). Must be > 0. No default — pass --timeout=N "
            "explicitly to bound the wait."
        ),
    ),
    transition: bool = typer.Option(
        False,
        "--transition",
        help=(
            "Only evaluate the match after observing at least one state "
            "transition since the initial snapshot. Useful to wait for the "
            "next change rather than the current value. Note: 'wait dead "
            "--transition' on an already-dead session can never match (the "
            "row is frozen) and will always timeout."
        ),
    ),
) -> None:
    """Block until the live process reaches any of the listed states.

    Polls the DB locally every 250 ms; the live TwiCC writes process
    transitions to the same row this command reads. Exits 0 on match,
    5 on timeout, 2 if TwiCC is not running, 1 on validation errors.
    """
    from twicc.cli.process_wait import wait_cmd

    wait_cmd(
        ctx.obj,
        statuses,
        timeout=timeout,
        transition=transition,
    )


@app.command()
def status() -> None:
    """Report the live TwiCC backend's status as JSON (exit 0 only when running)."""
    from twicc.cli.status import main as status_main

    status_main()


@app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True, "help_option_names": []},
)
def claude(ctx: typer.Context) -> None:
    """Run the Claude Code CLI bundled with claude-agent-sdk."""
    from twicc.cli.claude import main as claude_main

    claude_main(ctx.args)


@app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True, "help_option_names": []},
)
def codex(ctx: typer.Context) -> None:
    """Run the Codex CLI bundled with codex-app-server."""
    from twicc.cli.codex import main as codex_main

    codex_main(ctx.args)


@app.command()
def search(
    query: str = typer.Argument(help="Tantivy query string (e.g. 'websocket', 'body:websocket AND from_role:user')"),
    project: str = typer.Option(
        None,
        help=(
            "Scope the search to a project: either a project ID (with or "
            "without leading dash) or a directory path (absolute or relative). "
            "A normal project also includes its git worktrees' sessions; a "
            "worktree project is scoped to its own only. Mutually exclusive "
            "with --workspace."
        ),
    ),
    workspace: str = typer.Option(
        None,
        "--workspace",
        help=(
            "Scope the search to all projects in the given workspace, each "
            "member's git worktrees included. Mutually exclusive with --project."
        ),
    ),
    limit: int = typer.Option(20, help="Max number of session groups to return."),
    offset: int = typer.Option(0, help="Skip first N session groups."),
    include_hidden: bool = typer.Option(False, "--include-hidden", help="Include hidden sessions in search results."),
    only_hidden: bool = typer.Option(False, "--only-hidden", help="Search ONLY hidden sessions (mutually exclusive with --include-hidden)."),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Filter to sessions spawned by the given session_id, 'self' for the "
            "current session, or 'parent' for the session that spawned the current "
            "one (= my siblings, myself included). Implies --include-hidden by "
            "default: a filiation query matches every spawned child whatever its "
            "visibility. Mutually exclusive with --spawn-tree."
        ),
    ),
    spawn_tree: str = typer.Option(
        None,
        "--spawn-tree",
        help=(
            "Filter to every session in the spawn tree that contains the given "
            "session_id — any id in the tree works (root, middle, or leaf): the "
            "CLI looks it up and resolves to the tree it belongs to. Use 'self' "
            "for the tree containing the current session. Mutually exclusive "
            "with --spawned-by and --descendants. Implies --include-hidden by "
            "default."
        ),
    ),
    descendants: str = typer.Option(
        None,
        "--descendants",
        help=(
            "Filter to hits in the proper descendants of the given session_id "
            "(every session transitively spawned by it, at any depth, target "
            "excluded), 'self' for the descendants of the current session, or "
            "'parent' for the descendants of the current session's spawner (= my "
            "siblings, their subtrees, and my own subtree). Mutually exclusive "
            "with --spawned-by, --spawn-tree and --siblings. Implies --include-hidden "
            "by default."
        ),
    ),
    siblings: str = typer.Option(
        None,
        "--siblings",
        help=(
            "Filter to hits in the siblings of the given session_id — the other "
            "sessions spawned by the same parent — or 'self' for the current "
            "session's siblings. The target itself is always excluded (use "
            "--spawned-by parent to include yourself). 'parent' is not supported. "
            "Mutually exclusive with --spawned-by, --spawn-tree and --descendants. "
            "Implies --include-hidden by default."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Filter sessions by annotation. Repeatable, AND-combined. "
            "Operators: KEY=VALUE, KEY!=VALUE, KEY:exists, KEY:not-exists, "
            "KEY:in:V1,V2. KEY is a dotted path. Values are typed "
            "(true/false/null/int/float/string), same rules as "
            "create-session --annotation. See twicc-sessions skill for details."
        ),
    ),
) -> None:
    """Query the TwiCC search index using raw Tantivy query syntax."""
    if include_hidden and only_hidden:
        emit_error("Error: --include-hidden and --only-hidden are mutually exclusive.", code=2)

    if sum(x is not None for x in (spawned_by, spawn_tree, descendants, siblings)) > 1:
        emit_error(
            "Error: --spawned-by, --spawn-tree, --descendants and --siblings are mutually exclusive.",
            code=2,
        )

    if project is not None and workspace is not None:
        # Same redundant-or-empty intersection as ``twicc sessions``: scoping to
        # a project and a workspace at once never yields a useful different set.
        emit_error("Error: --project and --workspace are mutually exclusive.", code=2)

    from twicc.cli.search import main as search_main

    search_main(
        query,
        limit=limit,
        offset=offset,
        include_hidden=include_hidden,
        only_hidden=only_hidden,
        spawned_by=spawned_by,
        spawn_tree=spawn_tree,
        descendants=descendants,
        siblings=siblings,
        project=derive_project_id(project)[0] if project is not None else None,
        workspace=workspace,
        annotation=annotation,
    )


# ``create-session`` is registered directly from its module: the function
# already declares all its Typer options and performs lazy Django setup
# inside its body, so importing it here is cheap (no Django bootstrap).
from twicc.cli.create_session.command import create_session_cmd  # noqa: E402
app.command(name="create-session")(create_session_cmd)


# ``send-message`` follows the same pattern: lazy Django setup inside the
# function body keeps ``--help`` fast.
from twicc.cli.send_message.command import send_message_cmd  # noqa: E402
app.command(name="send-message")(send_message_cmd)


# ``send-messages`` is the batch sibling: same message to several sessions,
# selected with the same model as ``update-sessions``.
from twicc.cli.send_messages import send_messages_cmd  # noqa: E402
app.command(name="send-messages")(send_messages_cmd)


# ``update-session`` is a Typer sub-app: ``twicc update-session <ID>
# <subcommand>`` (only ``settings`` for now; ``title``, ``archive``,
# ``pin``, ``stop`` will plug into the same sub-app later).
from twicc.cli.update_session.command import update_session_app  # noqa: E402
app.add_typer(update_session_app)


# ``update-sessions`` is the batch sibling: ``twicc update-sessions <subcommand>
# [SESSION_ID...] [--spawned-by ...] [--descendants ...]`` applies the same
# update to every resolved session at once.
from twicc.cli.update_sessions.command import update_sessions_app  # noqa: E402
app.add_typer(update_sessions_app)


# ``password`` is a Typer sub-app (set/clear/status). The module is lightweight
# (no Django setup) so importing it at module load is cheap.
from twicc.cli.password import app as password_app  # noqa: E402
app.add_typer(password_app)


# ``token`` manages RPC API tokens. Local/human only — never exposed via
# ``/rpc/`` (chicken-and-egg). Lightweight module, cheap to import at load.
from twicc.cli.token import app as token_app  # noqa: E402
app.add_typer(token_app)


# ``whoami`` resolves the TwiCC session owning the calling process via PID
# ancestry. The function performs lazy Django setup inside its body.
from twicc.cli.whoami import whoami_cmd  # noqa: E402
app.command("whoami")(whoami_cmd)


# ``create-workspace`` / ``update-workspace`` / ``delete-workspace`` write
# to ``workspaces.json`` through the drop-request protocol. The functions
# declare all their Typer options inline and perform lazy Django setup
# inside their body, so importing them here stays cheap.
from twicc.cli.create_workspace import create_workspace_cmd  # noqa: E402
app.command(name="create-workspace")(create_workspace_cmd)

from twicc.cli.update_workspace import update_workspace_cmd  # noqa: E402
app.command(name="update-workspace")(update_workspace_cmd)

from twicc.cli.delete_workspace import delete_workspace_cmd  # noqa: E402
app.command(name="delete-workspace")(delete_workspace_cmd)


# ``create-project`` / ``update-project`` write to the ``Project`` table
# through the drop-request protocol. No ``delete-project`` by design —
# a Project row is bound to its sessions; projects are archived,
# never deleted.
from twicc.cli.create_project import create_project_cmd  # noqa: E402
app.command(name="create-project")(create_project_cmd)

# ``update-project`` is a Typer group: the flat field patch lives on the
# callback (``twicc update-project <PROJECT> --name X`` keeps working) and
# ``settings`` is a sub-command (``twicc update-project <PROJECT> settings
# --provider P ...``) for the per-provider agent-settings defaults.
from twicc.cli.update_project.command import update_project_app  # noqa: E402
app.add_typer(update_project_app)


# ``settings`` is a Typer group: the bare callback prints the full synced
# settings JSON, and ``get`` retrieves a single key. Both are offline reads
# with lazy Django setup inside their bodies, so importing here is cheap.
from twicc.cli.settings.command import settings_app  # noqa: E402
app.add_typer(settings_app)


# Peer messaging agent surface (design 2026-07-24): read-only ``peers`` /
# ``peer-message`` plus the write ``peer-send`` (drop-request kind
# ``peer:send``). Relationship management (add/verify/accept/revoke) is
# deliberately NOT on the CLI — human-only, web UI REST only.
from twicc.cli.peers import peers_cmd  # noqa: E402
app.command(name="peers")(peers_cmd)

from twicc.cli.peer_message import peer_message_cmd  # noqa: E402
app.command(name="peer-message")(peer_message_cmd)

from twicc.cli.peer_send import peer_send_cmd  # noqa: E402
app.command(name="peer-send")(peer_send_cmd)


# ``info`` is a single read-only command taking zero or more positional
# section names (presets, commands, models, agent-settings). The command
# performs lazy Django setup inside its body, so importing it here stays
# cheap.
from twicc.cli.info.command import info_cmd  # noqa: E402
app.command(name="info")(info_cmd)


# --- Advertise remote forwarding in each forwardable command's --help --------
# ``--remote`` / ``--remote-token`` are global *leading* flags resolved in
# ``main()`` before Typer ever runs (see ``_remote.parse_remote_invocation``), so
# they cannot be declared as real per-command options. Instead we attach an
# epilog note to every command the forwarder accepts — that is, everything except
# the local-only commands — so ``twicc <command> --help`` advertises the
# capability without polluting the root ``twicc --help``.
#
# Done as a one-shot post-registration sweep rather than an ``epilog=`` on each of
# the ~40 registrations: it keeps the wording in one place, lets new commands
# inherit the note automatically, and reuses the very gate the forwarder itself
# uses (``LOCAL_ONLY_COMMANDS``). The two stay in lockstep — every non-local-only
# command is exactly the set of routes in the RPC registry.
from typer.main import get_command_name  # noqa: E402

from twicc.cli._local_only import LOCAL_ONLY_COMMANDS  # noqa: E402

_REMOTE_EPILOG = (
    "Remote: this command can run against a remote TwiCC over its /rpc/ API. "
    "Prefix it with --remote <url> (and optionally --remote-token <tok>); the "
    "url and token also fall back to the TWICC_REMOTE_URL / TWICC_REMOTE_TOKEN "
    "environment variables. The flags lead the command, e.g. "
    "`twicc --remote <url> <command>`."
)

# Commands that accept a file path (the prompt / --message body and --attach):
# over --remote those paths are read on the client and inlined, so they get an
# extra note about the `remote:` scheme that defers resolution to the server.
# Kept in lockstep with the inliner in _remote.py (_PROMPT_PARAM_NAMES + the
# --attach option) — the only CLI commands that read a path from disk.
_PATH_COMMANDS = frozenset({"create-session", "send-message", "send-messages"})

_REMOTE_EPILOG_WITH_PATHS = _REMOTE_EPILOG + (
    " File paths you pass (the prompt or --message body, and --attach files) are "
    "read locally over --remote; prefix an absolute path with 'remote:' to read "
    "it on the remote server instead, e.g. --attach remote:/abs/file."
)


def _registered_command_name(info) -> str | None:
    """Resolve a registered command's CLI name (Typer derives it from the callback when unset)."""
    if info.name:
        return info.name
    if info.callback is not None:
        return get_command_name(info.callback.__name__)
    return None


def _advertise_remote_in_help() -> None:
    """Stamp the remote epilog onto every forwardable command and sub-typer."""
    for info in app.registered_commands:
        name = _registered_command_name(info)
        if name in LOCAL_ONLY_COMMANDS:
            continue
        info.epilog = _REMOTE_EPILOG_WITH_PATHS if name in _PATH_COMMANDS else _REMOTE_EPILOG
    for group in app.registered_groups:
        sub = group.typer_instance
        if sub.info.name in LOCAL_ONLY_COMMANDS:
            continue
        group.epilog = _REMOTE_EPILOG
        for info in sub.registered_commands:
            info.epilog = _REMOTE_EPILOG


_advertise_remote_in_help()


def main() -> None:
    """Entry point for ``pyproject.toml`` scripts and ``__main__.py``.

    Keep the common LOCAL path fast: a cheap argv pre-check avoids importing the
    ``_remote`` module (httpx, the registry, …) on every invocation. Only when a
    ``--remote`` token is actually present do we load the forwarder. A non-leading
    literal ``--remote`` in free text trips this cheap check, but ``maybe_forward``
    then returns ``None`` via its front-parse — so the local app still runs (a
    rare, harmless extra import).
    """
    import sys

    argv = sys.argv[1:]
    if any(a == "--remote" or a.startswith("--remote=") for a in argv):
        from twicc.cli._remote import maybe_forward

        code = maybe_forward(argv)
        if code is not None:
            raise SystemExit(code)

    # The ``.env`` was loaded at package import; surface what it dropped, then
    # fail fast on an unusable provider home — for EVERY local invocation
    # (server, ``twicc claude`` / ``twicc codex``, drop-request commands,
    # ``--version`` / ``--help`` included), before any Django setup or
    # traceback. A remote-forwarded command (above) must not be blocked by a
    # broken local ``.env``.
    for warning in get_env_load_warnings():
        print(f"Warning: {warning}", file=sys.stderr)
    from twicc.provider_homes import ProviderHomeConfigError, validate

    try:
        validate()
    except ProviderHomeConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    app()
