"""
CLI entry point for TwiCC.

Lightweight dispatcher — subcommand modules must be imported lazily inside each
command function so that they never pay for Django startup.
"""

import os

import typer

from twicc.version import get_version

# Ensure Django settings are discoverable for all subcommands that call django.setup().
# Force to twicc.settings unless already set to a twicc-specific variant (e.g. for tests).
# This prevents a stray DJANGO_SETTINGS_MODULE from another project from breaking twicc.
if not os.environ.get("DJANGO_SETTINGS_MODULE", "").startswith("twicc.settings"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "twicc.settings"


def _normalize_project_id(project_id: str) -> str:
    """Ensure the project ID starts with a dash (auto-prepend if missing)."""
    if not project_id.startswith("-"):
        project_id = f"-{project_id}"
    return project_id


app = typer.Typer(
    name="twicc",
    help="TwiCC — The Web Interface for Claude Code.",
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


@app.command()
def projects(
    limit: int = typer.Option(20, help="Max number of projects to return."),
    offset: int = typer.Option(0, help="Skip first N projects."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived projects."),
) -> None:
    """List all projects as JSON (ordered by most recently active)."""
    from twicc.cli.projects import main as projects_main

    projects_main(limit=limit, offset=offset, archived=include_archived)


@app.command()
def project(
    project_id: str = typer.Argument(help="The project ID (leading dash is optional)."),
) -> None:
    """Show a single project as JSON."""
    from twicc.cli.project import main as project_main

    project_main(_normalize_project_id(project_id))


@app.command()
def sessions(
    project: str = typer.Option(None, help="Filter by project ID (leading dash is optional)."),
    limit: int = typer.Option(20, help="Max number of sessions to return."),
    offset: int = typer.Option(0, help="Skip first N sessions."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived sessions."),
) -> None:
    """List sessions as JSON (ordered by most recently active)."""
    from twicc.cli.sessions import main as sessions_main

    sessions_main(
        project=_normalize_project_id(project) if project is not None else None,
        limit=limit,
        offset=offset,
        archived=include_archived,
    )


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
    range: str = typer.Argument(help="Line number or range (e.g. '5' or '10-20')."),
) -> None:
    """Show session item(s) content as JSON."""
    from twicc.cli.session import content as session_content

    session_content(ctx.obj, range_str=range)


@session_app.command()
def agents(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of subagents to return."),
    offset: int = typer.Option(0, help="Skip first N subagents."),
) -> None:
    """List subagents of a session as JSON."""
    from twicc.cli.session import agents as session_agents

    session_agents(ctx.obj, limit=limit, offset=offset)


@app.command()
def usage() -> None:
    """Show the latest usage quota snapshot as JSON."""
    from twicc.cli.usage import main as usage_main

    usage_main()


@app.command()
def search(
    query: str = typer.Argument(help="Tantivy query string (e.g. 'websocket', 'body:websocket AND from_role:user')"),
    limit: int = typer.Option(20, help="Max number of hits."),
    offset: int = typer.Option(0, help="Skip first N hits."),
) -> None:
    """Query the TwiCC search index using raw Tantivy query syntax."""
    from twicc.cli.search import main as search_main

    search_main(query, limit=limit, offset=offset)


def main() -> None:
    """Entry point for ``pyproject.toml`` scripts and ``__main__.py``."""
    app()
