"""CLI implementation for the ``twicc workspaces`` subcommand."""

from twicc.cli._output import PAGINATED_DEFAULT_LIMIT, emit_list, pagination_notice, resolve_limit


def main(*, limit: int | None = None, offset: int = 0, archived: bool = False,
         paginated: bool = False) -> None:
    """List workspaces as JSON to stdout.

    Workspaces are stored in ``<data_dir>/workspaces.json`` and managed via the
    TwiCC UI; this command is read-only.
    """
    # No django.setup() in this command — it reads workspaces.json from disk — so
    # the notice reaches stderr but writes no log line on the terminal path.
    paginated = pagination_notice("workspaces", paginated, default_limit=PAGINATED_DEFAULT_LIMIT)

    from twicc.workspaces import read_workspaces

    workspaces = read_workspaces().get("workspaces", [])

    if not archived:
        workspaces = [w for w in workspaces if not w.get("archived", False)]

    # The catalogue is a plain in-memory list read from workspaces.json, so the
    # total costs nothing — no query to weigh, unlike the DB-backed listings.
    limit = resolve_limit(limit, paginated=paginated, default=PAGINATED_DEFAULT_LIMIT)
    total = len(workspaces)
    workspaces = workspaces[offset : offset + limit]

    emit_list(workspaces, paginated=paginated, limit=limit, offset=offset, total=total)
