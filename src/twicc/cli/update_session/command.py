"""Top-level ``twicc update-session`` sub-app.

The sub-app shape mirrors ``twicc session``: a parent ``SESSION_ID`` argument
captured in ``ctx.obj`` and one sub-command per supported update. Currently
``settings``, ``title``, ``archive``, ``unarchive``, ``pin``, ``unpin``,
``hide``, ``unhide``, ``mute``, ``notify``, ``detach-tree``, ``attach-tree``
and ``annotations`` are implemented. Future sub-commands (``stop``, ...) will
register against this same app.
"""

from __future__ import annotations

import typer

from twicc.cli._output import emit_error
from twicc.cli.update_session.annotations_command import update_annotations_cmd
from twicc.cli.update_session.archived_command import (
    update_archive_cmd,
    update_unarchive_cmd,
)
from twicc.cli.update_session.hidden_command import (
    update_hide_cmd,
    update_unhide_cmd,
)
from twicc.cli.update_session.mute_command import (
    update_mute_cmd,
    update_notify_cmd,
)
from twicc.cli.update_session.pinned_command import (
    update_pin_cmd,
    update_unpin_cmd,
)
from twicc.cli.update_session.settings_command import update_settings_cmd
from twicc.cli.update_session.title_command import update_title_cmd
from twicc.cli.update_session.tree_detached_command import (
    update_attach_tree_cmd,
    update_detach_tree_cmd,
)

update_session_app = typer.Typer(
    name="update-session",
    help=(
        "Update an existing session (settings, title, archive, unarchive, "
        "pin, unpin, hide, unhide, mute, notify, detach-tree, attach-tree, "
        "annotations; stop later)."
    ),
    invoke_without_command=False,
)


@update_session_app.callback()
def _update_session_default(
    ctx: typer.Context,
    session_id: str = typer.Argument(
        ...,
        help=(
            "Id of the existing session to update. Use 'self' to target the "
            "current TwiCC session (resolved from PID ancestry)."
        ),
    ),
) -> None:
    """Parent callback. Stashes ``session_id`` for the sub-command to read.

    The ``self`` keyword is resolved here so every sub-command sees a real
    session id in ``ctx.obj``. Django is bootstrapped lazily only on the
    ``self`` branch, to keep ``--help`` and the common explicit-id path fast.
    """
    if session_id == "self":
        import os

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
        import django

        django.setup()

        from twicc.cli._drop_request.whoami import resolve_current_session

        current = resolve_current_session()
        if current is None:
            emit_error(
                "Error: self could not be resolved: no TwiCC session found "
                "in PID ancestry.",
                code=1,
            )
        session_id = current.id

    ctx.obj = session_id


update_session_app.command(name="settings")(update_settings_cmd)
update_session_app.command(name="title")(update_title_cmd)
update_session_app.command(name="archive")(update_archive_cmd)
update_session_app.command(name="unarchive")(update_unarchive_cmd)
update_session_app.command(name="pin")(update_pin_cmd)
update_session_app.command(name="unpin")(update_unpin_cmd)
update_session_app.command(name="hide")(update_hide_cmd)
update_session_app.command(name="unhide")(update_unhide_cmd)
update_session_app.command(name="mute")(update_mute_cmd)
update_session_app.command(name="notify")(update_notify_cmd)
update_session_app.command(name="detach-tree")(update_detach_tree_cmd)
update_session_app.command(name="attach-tree")(update_attach_tree_cmd)
update_session_app.command(name="annotations")(update_annotations_cmd)
