"""CLI commands to manage TwiCC accounts (multi-user mode).

Local-only (``LOCAL_ONLY_COMMANDS``): never exposed over ``/rpc/`` nor MCP —
an agent must never be able to mint an admin account for itself. Passwords
are only ever read from an interactive prompt (no flag, no stdin) to keep
them out of shell history.

Typical first setup::

    twicc user create admin@example.com --admin --password
    twicc user create teammate@example.com            # Google-only account
    twicc user grant teammate@example.com --project <project-id> --level operate
    twicc user grant teammate@example.com --workspace <workspace-id>

Grants can then be managed from the web UI (Admin panel, or the Access
section of a project/workspace).
"""

from __future__ import annotations

import sys

import orjson
import typer

app = typer.Typer(
    name="user",
    help="Manage accounts and their project/workspace access (multi-user mode).",
    no_args_is_help=True,
)


def _setup() -> None:
    import django

    django.setup()


def _fail(result) -> None:
    for error in result.errors or []:
        typer.echo(f"Error ({error.field}): {error.message}", err=True)
    raise typer.Exit(code=1)


def _prompt_password() -> str:
    if not sys.stdin.isatty():
        typer.echo("Error: setting a password requires an interactive terminal.", err=True)
        raise typer.Exit(code=1)
    return typer.prompt("Password", hide_input=True, confirmation_prompt="Confirm password")


def _get_user(email: str):
    from twicc.accounts.service import normalize_email
    from twicc.core.models import User

    user = User.objects.filter(email=normalize_email(email)).first()
    if user is None:
        typer.echo(f"Error: no account for {email!r}.", err=True)
        raise typer.Exit(code=1)
    return user


def _print(data) -> None:
    typer.echo(orjson.dumps(data, option=orjson.OPT_INDENT_2).decode())


@app.command("list")
def list_users() -> None:
    """List every account with its grants (JSON)."""
    _setup()
    from twicc.accounts.service import serialize_user
    from twicc.core.models import User

    _print([serialize_user(u, with_grants=True) for u in User.objects.all()])


@app.command("create")
def create_user(
    email: str = typer.Argument(..., help="Account email (also the Google address it may sign in with)."),
    name: str = typer.Option("", "--name", help="Display name."),
    admin: bool = typer.Option(False, "--admin", help="Create an admin (default: member)."),
    password: bool = typer.Option(
        False, "--password", help="Prompt for a local password (otherwise the account signs in with Google only)."
    ),
) -> None:
    """Create an account."""
    _setup()
    from twicc.accounts.service import create_user as create, serialize_user

    secret = _prompt_password() if password else None
    result = create(email=email, display_name=name, role="admin" if admin else "member", password=secret)
    if not result.success:
        _fail(result)
    _print(serialize_user(result.user))
    typer.echo("Account created. Restart is not needed; sign-in is available at once.", err=True)


@app.command("set-password")
def set_password(email: str = typer.Argument(...)) -> None:
    """Set or replace an account's local password (signs it out everywhere)."""
    _setup()
    from twicc.accounts.service import update_user

    user = _get_user(email)
    result = update_user(user.id, password=_prompt_password())
    if not result.success:
        _fail(result)
    typer.echo("Password updated.")


@app.command("set-role")
def set_role(
    email: str = typer.Argument(...),
    role: str = typer.Argument(..., help="admin or member"),
) -> None:
    """Change an account's role (signs it out everywhere)."""
    _setup()
    from twicc.accounts.service import update_user

    result = update_user(_get_user(email).id, role=role)
    if not result.success:
        _fail(result)
    typer.echo(f"Role set to {role}.")


@app.command("disable")
def disable(email: str = typer.Argument(...)) -> None:
    """Disable an account (it can no longer sign in; sessions are dropped)."""
    _setup()
    from twicc.accounts.service import update_user

    result = update_user(_get_user(email).id, is_active=False)
    if not result.success:
        _fail(result)
    typer.echo("Account disabled.")


@app.command("enable")
def enable(email: str = typer.Argument(...)) -> None:
    """Re-enable a disabled account."""
    _setup()
    from twicc.accounts.service import update_user

    result = update_user(_get_user(email).id, is_active=True)
    if not result.success:
        _fail(result)
    typer.echo("Account enabled.")


@app.command("delete")
def delete(
    email: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Delete an account and its grants."""
    _setup()
    from twicc.accounts.service import delete_user

    user = _get_user(email)
    if not yes and not typer.confirm(f"Delete account {user.email}?", default=False):
        raise typer.Exit(code=0)
    result = delete_user(user.id)
    if not result.success:
        _fail(result)
    typer.echo("Account deleted.")


def _grant_target(project: str | None, workspace: str | None) -> tuple[str, str]:
    if bool(project) == bool(workspace):
        typer.echo("Error: pass exactly one of --project or --workspace.", err=True)
        raise typer.Exit(code=1)
    return ("project", project) if project else ("workspace", workspace)


@app.command("grant")
def grant(
    email: str = typer.Argument(...),
    project: str = typer.Option(None, "--project", help="Project id or directory."),
    workspace: str = typer.Option(None, "--workspace", help="Workspace id."),
    level: str = typer.Option("view", "--level", help="view or operate"),
) -> None:
    """Grant an account access to a project (and its worktrees) or a whole workspace."""
    _setup()
    from twicc.accounts.service import set_project_grant, set_workspace_grant

    kind, target = _grant_target(project, workspace)
    user = _get_user(email)
    if kind == "project":
        result = set_project_grant(user.id, _resolve_project_id(target), level)
    else:
        result = set_workspace_grant(user.id, target, level)
    if not result.success:
        _fail(result)
    typer.echo(f"Granted {level} on {kind} {target}.")


@app.command("revoke")
def revoke(
    email: str = typer.Argument(...),
    project: str = typer.Option(None, "--project", help="Project id or directory."),
    workspace: str = typer.Option(None, "--workspace", help="Workspace id."),
) -> None:
    """Remove a direct project grant or a workspace grant."""
    _setup()
    from twicc.accounts.service import set_project_grant, set_workspace_grant

    kind, target = _grant_target(project, workspace)
    user = _get_user(email)
    if kind == "project":
        result = set_project_grant(user.id, _resolve_project_id(target), None)
    else:
        result = set_workspace_grant(user.id, target, None)
    if not result.success:
        _fail(result)
    typer.echo(f"Revoked {kind} {target}.")


def _resolve_project_id(value: str) -> str:
    """Accept a project id or a directory path."""
    import os

    from twicc.core.models import Project
    from twicc.paths import path_to_project_id

    if Project.objects.filter(pk=value).exists():
        return value
    if os.path.isabs(os.path.expanduser(value)):
        return path_to_project_id(os.path.realpath(os.path.expanduser(value)))
    return value
