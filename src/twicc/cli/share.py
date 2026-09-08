"""``twicc share`` (list) / ``show`` — read-only, direct DB (works with the server
down). ``url`` uses the backend Share URL builder. With ``shareBaseUrl`` unset
or unusable, unredacted rows use the relative ``/share/<token>/`` path. Links
only resolve on the dedicated Share origin."""

from twicc.cli._output import emit_error, emit_json, emit_list, pagination_notice, resolve_limit


def _base_url(current: dict) -> str:
    from twicc.core.services.share_url import normalize_share_base
    return normalize_share_base(current.get("shareBaseUrl"))


def _redacted_kinds(current: dict) -> set[str]:
    """Kinds whose token/url are redacted for THIS process's caller (§7.3):
    empty for a human; for an agent, every kind whose gate setting is off.
    The row itself is never dropped — a redacted row lets the agent say
    'a share exists here, ask the user to enable the setting'."""
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.core.services.share_agent_gate import SETTING_KEYS

    from twicc.mcp.identity import external_caller
    if external_caller.get() is not None:
        return set()  # The external connection has an explicit twicc:full grant.
    if resolve_current_session() is None:
        return set()
    return {kind for kind, key in SETTING_KEYS.items() if not current.get(key, False)}


def list_main(*, kind: str | None = None, session: str | None = None,
              project: str | None = None, include_revoked: bool = False,
              limit: int | None = None, offset: int = 0, paginated: bool = False) -> None:
    import django
    django.setup()
    # default_limit=50 → the page size is not changing, so the notice announces
    # the shape alone.
    paginated = pagination_notice("share", paginated, default_limit=50)

    from django.db.models import Q

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share
    from twicc.core.services.share_url import build_share_url
    from twicc.synced_settings import read_synced_settings

    qs = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).all()
    if kind is not None:
        qs = qs.filter(kind=kind)
    if session is not None:
        qs = qs.filter(Q(session_id=session) | Q(artifact_bookmark__session_id=session))
    if project is not None:
        from twicc.projects import project_scope_ids
        ids = project_scope_ids(project)
        # Both kinds: an artifact share has session NULL (CheckConstraint), its
        # project comes from the bookmark's denormalised raw project FK.
        qs = qs.filter(Q(session__project_id__in=ids) | Q(artifact_bookmark__project_id__in=ids))
    if not include_revoked:
        # ``status() == "revoked"`` is exactly ``revoked_at IS NOT NULL``, so the
        # filter belongs in the query rather than in a post-slice loop: a page is
        # then full, and the row count the window sees is the one the caller gets.
        qs = qs.filter(revoked_at__isnull=True)
    limit = resolve_limit(limit, paginated=paginated, default=50)
    total = qs.count() if paginated else None
    rows = list(qs[offset:offset + limit])
    current = read_synced_settings()
    base = _base_url(current)
    redacted_kinds = _redacted_kinds(current)
    out = []
    for s in rows:
        data = serialize_share(s)
        if s.kind in redacted_kinds:
            data["token"] = None
            data["url_path"] = None
            data["url"] = None
            data["redacted"] = True
        else:
            data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
        out.append(data)
    emit_list(out, paginated=paginated, limit=limit, offset=offset, total=total)


def show_main(share_id: str) -> None:
    import django
    django.setup()

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share
    from twicc.core.services.share_url import build_share_url
    from twicc.synced_settings import read_synced_settings

    s = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).filter(id=share_id).first()
    if s is None:
        emit_error(f"Error: share {share_id!r} not found.", code=1)
    data = serialize_share(s)
    current = read_synced_settings()
    base = _base_url(current)
    redacted_kinds = _redacted_kinds(current)
    if s.kind in redacted_kinds:
        data["token"] = None
        data["url_path"] = None
        data["url"] = None
        data["redacted"] = True
    else:
        data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
    emit_json(data)
