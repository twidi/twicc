"""
Simple JSON serializers for core models.

Note: These serializers only access model attributes that are already loaded
in memory (no lazy-loaded relationships, no database queries). This makes them
safe to call from async contexts without sync_to_async wrapping, as long as
the model instance was already fetched from the database.
"""

from twicc.project_icons import project_own_icon_url, project_repo_icon_url
from twicc.providers.helpers import AGENT_SETTINGS_HIDDEN_FROM_FRONTEND, AgentSettings, get_provider_helpers


def session_compute_ready(session) -> bool:
    """Return whether a session uses its provider's current compute rules."""

    return session.compute_version == get_provider_helpers(session.provider).current_compute_version


def serialize_project(project):
    """Serialize a Project model to a dictionary."""
    return {
        "id": project.id,
        "directory": project.directory,
        "git_root": project.git_root,
        "sessions_count": project.sessions_count,
        "mtime": project.mtime,
        "stale": project.stale,
        "name": project.name,
        "color": project.color,
        "archived": project.archived,
        "total_cost": float(project.total_cost) if project.total_cost else None,
        # Non-null => this project is a git worktree; the value is the id of its
        # main repository's project (or None when it is not a worktree). We read
        # the raw FK id (``worktree_of_id``) rather than ``worktree_of`` to avoid
        # the lazy relationship load — keeping this serializer query-free and
        # safe to call from async contexts (see module docstring).
        "worktree_of": project.worktree_of_id,
        # Cross-provider trust: True/False = explicit decision, None = no own
        # decision (the front resolves the effective value by walking ancestors
        # / the worktree_of link). ``trust_imported`` is internal bookkeeping
        # and intentionally not exposed.
        "trust": project.trust,
        "trust_propagation": project.trust_propagation,
        # Per-project agent settings defaults (optional). default_provider is
        # the provider a new session defaults to (None = inherit). The front
        # resolves both by walking the project chain (worktree_of / path
        # ancestors); see frontend/src/utils/projectAgentDefaults.js.
        "default_provider": project.default_provider,
        "default_agent_settings": project.default_agent_settings,
        # Per-project default layout id (named-layout id / "single-pane" / None=inherit).
        "default_layout_id": project.default_layout_id,
        # Per-project Browser-pane default URL (None = inherit; see models.py).
        "browser_urls": project.browser_urls or [],
        # Absolute base directory for new git worktrees of this project (None =
        # inherit the global worktreeDirectoryTemplate expanded against the project).
        "worktree_directory": project.worktree_directory,
        # Project icon — two independent bricks; the EFFECTIVE icon is resolved
        # CLIENT-SIDE by walking the project chain so a manual override cascades
        # to descendants (utils/projectIcon.js), keeping this serializer
        # query-free. ``icon`` is the per-project state ("inherit"/"none"/token);
        # ``icon_override_url`` is this project's OWN manual icon (or null);
        # ``repo_icon_url`` is the auto-discovered repo icon for its anchor (the
        # socle, shared by the repo). See docs/plans/2026-07-17-project-icons-design.md.
        "icon": project.icon,
        "icon_override_url": project_own_icon_url(project),
        "repo_icon_url": project_repo_icon_url(project),
        # The git root feeding ``repo_icon_url`` when it differs from ``git_root``
        # (worktree main repo / umbrella).
        "icon_anchor": project.icon_anchor,
    }


def serialize_artifact_bookmark(bookmark):
    """Serialize an ArtifactBookmark to a dict. Pure: no DB queries — FK ids via
    *_id, root computed from the session id (no relationship access)."""
    import os
    from twicc.paths import get_session_artifacts_dir

    rel = bookmark.relative_path
    return {
        "id": bookmark.id,
        "name": bookmark.name,
        "scope": bookmark.scope,
        "session_id": bookmark.session_id,
        "project_id": bookmark.project_id,
        "relative_path": rel,
        "root": str(get_session_artifacts_dir(bookmark.session_id)),
        "file_ext": os.path.splitext(rel)[1].lstrip(".").lower(),
        "allowed_hosts": bookmark.allowed_hosts,
        "denied_hosts": bookmark.denied_hosts,
        "created_at": bookmark.created_at.isoformat() if bookmark.created_at else None,
        "updated_at": bookmark.updated_at.isoformat() if bookmark.updated_at else None,
    }


def serialize_network_denial(denial):
    """Serialize an ArtifactNetworkDenial. Callers must select_related("share")
    (pure-serializer convention: no queries here)."""
    share = denial.share
    return {
        "host_key": denial.host_key,
        "kind": denial.kind,
        "share": {"id": share.id, "label": share.label or "", "status": share.status()} if share else None,
        "ip": denial.ip,
        "user_agent": denial.user_agent,
        "count": denial.count,
        "first_at": denial.first_at.isoformat() if denial.first_at else None,
        "last_at": denial.last_at.isoformat() if denial.last_at else None,
    }


def serialize_session(session):
    """
    Serialize a Session model to a dictionary.

    Includes ``compute_version_up_to_date`` boolean to indicate if the session's
    metadata has been computed with the current version of rules. The reference
    version comes from the owning provider's ``current_compute_version`` so
    bumping one provider's compute does not invalidate sessions of another;
    for providers without a compute pipeline (``current_compute_version=None``)
    sessions match their default ``compute_version=NULL`` and are reported
    up-to-date.

    Works for both regular sessions and subagents. For subagents,
    parent_session_id will be set; for regular sessions it will be None.

    For the title field, pending titles take priority over the database value.
    This ensures that when a session is created with a custom title, the title
    is immediately visible even before it's written to the JSONL file.
    """
    from twicc.pending_titles import get_pending_title
    from twicc.artifacts_watcher import session_has_artifacts
    from twicc.paths import get_session_artifacts_dir

    # Use pending title if available, otherwise use the stored title
    title = get_pending_title(session.id) or session.title

    # O(1) in-memory read of the ArtifactsWatcher set (no I/O — safe here per
    # the module docstring's "no DB / no I/O" contract).
    has_artifacts = session_has_artifacts(session.id)

    provider_helpers = get_provider_helpers(session.provider)

    return {
        "id": session.id,
        "project_id": session.project_id,
        "provider": session.provider,  # Backend provider (see Provider enum)
        "parent_session_id": session.parent_session_id,  # None for regular sessions, set for subagents
        "last_line": session.last_line,
        "mtime": session.mtime,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "last_started_at": session.last_started_at.isoformat() if session.last_started_at else None,
        "last_updated_at": session.last_updated_at.isoformat() if session.last_updated_at else None,
        "last_stopped_at": session.last_stopped_at.isoformat() if session.last_stopped_at else None,
        "last_new_content_at": session.last_new_content_at.isoformat() if session.last_new_content_at else None,
        "last_viewed_at": session.last_viewed_at.isoformat() if session.last_viewed_at else None,
        "stale": session.stale,
        "title": title,  # Session title (from pending, first user message, or custom-title)
        # Provider-supplied short identifier — Codex stores the agent_nickname
        # of a subagent here (e.g. "Bohr"), the frontend uses it for the
        # subagent tab labels and the SessionHeader name.
        "slug": session.slug,
        "user_message_count": session.user_message_count,  # Number of user messages (message turns)
        # Boolean indicating if session metadata is up-to-date for the owning provider
        "compute_version_up_to_date": session_compute_ready(session),
        # Cost and context usage fields
        "context_usage": session.context_usage,  # Current context usage in tokens
        "self_cost": float(session.self_cost) if session.self_cost else None,  # Own items cost in USD
        "subagents_cost": float(session.subagents_cost) if session.subagents_cost else None,  # Sum of subagents cost
        "total_cost": float(session.total_cost) if session.total_cost else None,  # Total cost in USD
        # Runtime environment fields
        "cwd": session.cwd,  # Current working directory
        "git_branch": session.git_branch or (session.cwd_git_branch if session.git_directory else None),  # Resolved branch, fallback to cwd
        "git_directory": session.git_directory,  # Resolved git root directory
        "model": provider_helpers.serialize_model(session.model),  # Model info object
        # User-controlled fields
        "archived": session.archived,  # Whether the session is archived
        "pinned": session.pinned,  # Whether the session is pinned
        "mute_on_user_turn": session.mute_on_user_turn,
        "layout": session.layout,  # Per-session dockable-layout intention (UI state; {} = single pane)
        "browser_url": session.browser_url,  # Browser-pane last URL (UI state; None = use the resolved default)
        "tasks": session.tasks,  # Latest task/todo/plan snapshot (common shape; {} = none). Not consumed by the UI yet
        # Plan-like documents touched by the session (list; [] = none) —
        # emitted verbatim, honoring this serializer's query-free contract:
        # relative entries are resolved client-side against the project
        # directory (with the ``worktree_of`` parent as fallback). Drives the
        # Plan tab; see ``Session.plan_paths``.
        "plan_paths": session.plan_paths,
        "goals": session.goals,  # Goal lifecycle history (list; [] = none). Last entry drives the footer goal bar

        "hybrid": session.hybrid,  # Hybrid CLI mode (one-way, see models.Session.hybrid)
        # Closed AgentSettings bundle (cross-provider). Fields listed in
        # ``AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`` are filtered out so they
        # never leak to the frontend.
        **{
            field: getattr(session, field)
            for field in AgentSettings._fields
            if field not in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND
        },
        # Whether the session has been compacted at least once
        "compacted": session.compacted,
        # Whether the session has at least one Claude Code workflow run
        # (``wf_*.json`` in its ``<session_id>/workflows/`` folder). Monotonic /
        # one-way; latched live by the watcher and backfilled by the background
        # compute. See ``Session.has_workflows``.
        "has_workflows": session.has_workflows,
        # Per-session artifacts (files under <data_dir>/artifacts/<id>/). The
        # frontend shows an Artifacts tab when ``has_artifacts`` is true and
        # mounts it on ``artifacts_dir``. ``has_artifacts`` is monotonic and
        # flips false->true live via the ``artifacts_available`` WS message;
        # ``artifacts_dir`` is only sent when present so list payloads stay lean.
        "has_artifacts": has_artifacts,
        "artifacts_dir": str(get_session_artifacts_dir(session.id)) if has_artifacts else None,
        # Provider plan file (Claude Code: ``<claude home>/plans/<slug>.md``). The
        # frontend shows a read-only Plan tab when ``has_plan`` is true and
        # fetches the markdown from ``/api/sessions/<id>/plan/``. Provider-
        # agnostic: ``session_has_plan`` reads each provider's live plans-watcher
        # set in the server (O(1)); off the server (CLI, background compute) it
        # falls back to an on-disk check so the flag stays accurate there too — a
        # read, within the serializer's no-write contract. NOT monotonic — flips
        # both ways live via the ``plan_available`` / ``plan_gone`` WS messages.
        "has_plan": provider_helpers.session_has_plan(session),
        # Hidden + spawned tree links (cf. hidden-sessions design spec). The
        # frontend never sees a hidden session via REST (filtered server
        # side); the CLI uses these fields when it explicitly opts into
        # hidden listings via --include-hidden / --only-hidden.
        "hidden": session.hidden,
        "spawned_by": session.spawned_by_id,
        "spawn_root": session.spawn_root_id,
        "annotations": session.annotations,
    }


def serialize_usage_snapshot(snapshot, period_costs=None, references=None):
    """
    Serialize a UsageSnapshot model to a dictionary.

    Sends raw stored data — the frontend computes derived values
    (temporal %, burn rate, levels).

    Args:
        snapshot: UsageSnapshot model instance.
        period_costs: Optional dict with "five_hour" and "seven_day" cost data
            from compute_period_costs(). Each contains spent, estimated_period,
            estimated_monthly.
        references: Optional dict with historical reference snapshots for
            recent burn rate computation (keys: "one_hour", "one_day").
    """
    def _fmt_dt(dt):
        return dt.isoformat() if dt else None

    data = {
        "provider": snapshot.provider,
        "fetched_at": _fmt_dt(snapshot.fetched_at),
        # Five-hour quota
        "five_hour_utilization": snapshot.five_hour_utilization,
        "five_hour_resets_at": _fmt_dt(snapshot.five_hour_resets_at),
        # Seven-day global quota
        "seven_day_utilization": snapshot.seven_day_utilization,
        "seven_day_resets_at": _fmt_dt(snapshot.seven_day_resets_at),
        # Extra usage
        "extra_usage_is_enabled": snapshot.extra_usage_is_enabled,
        "extra_usage_monthly_limit": snapshot.extra_usage_monthly_limit,
        "extra_usage_used_credits": snapshot.extra_usage_used_credits,
        "extra_usage_utilization": snapshot.extra_usage_utilization,
        "extra_usage_remaining_credits": snapshot.extra_usage_remaining_credits,
        "extra_usage_currency": snapshot.extra_usage_currency,
        "extra_usage_decimal_places": snapshot.extra_usage_decimal_places,
    }

    # Period cost data (spent, estimated_period, estimated_monthly)
    if period_costs:
        data["period_costs"] = period_costs

    # Reference snapshots for recent burn rate computation
    if references:
        data["references"] = references

    return data


def serialize_benchmark_row(row):
    """Serialize a ModelBenchmark row for the bootstrap payload.

    Sends the raw stored metrics only; the frontend derives the composite
    score over the complete dataset (see ``utils/benchmarkScores.js``) and
    joins to the model picker on ``(provider, model, reasoning_effort)`` —
    ``model`` being the internal SDK ``full_name`` shared with the serialized
    model registry.
    """
    return {
        "provider": row.provider,
        "model": row.model,
        "reasoning_effort": row.reasoning_effort,
        "pass_at_1": row.pass_at_1,
        "pass_at_4": row.pass_at_4,
        "mean_cost_usd": row.mean_cost_usd,
        "median_duration_seconds": row.median_duration_seconds,
    }


def serialize_session_item(item):
    """
    Serialize a SessionItem model to a dictionary with full content.

    Used by:
    - GET /api/.../items/?range=... endpoint
    - WebSocket item_created messages
    """
    return {
        "line_num": item.line_num,
        "content": item.content,
        # Display metadata fields
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind,
        # Item timestamp (ISO 8601, UTC) — the moment the provider wrote this
        # JSONL line. Surfaced so the frontend can place per-block day separators
        # without parsing the raw content (and even before content is loaded).
        "timestamp": item.timestamp.isoformat() if item.timestamp else None,
    }


def serialize_session_item_metadata(item):
    """
    Serialize a SessionItem model to a dictionary WITHOUT content.

    Used by:
    - GET /api/.../items/metadata/ endpoint

    This is a lightweight serialization for loading all item metadata
    without the potentially large content field.
    """
    return {
        "line_num": item.line_num,
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind,
        "git_directory": item.git_directory,
        "git_branch": item.git_branch,
        # Item timestamp (ISO 8601, UTC) — surfaced at the metadata level so the
        # frontend has it for every item up front (content stays lazy-loaded),
        # which is what per-block day separators need.
        "timestamp": item.timestamp.isoformat() if item.timestamp else None,
        # NO content field - that's the whole point
    }


_PRIVATE_SHARE_OPTION_KEYS = frozenset({"_codex_rollout_migration_anchor"})


def _serialize_share_options(options: dict | None) -> dict:
    return {
        key: value
        for key, value in (options or {}).items()
        if key not in _PRIVATE_SHARE_OPTION_KEYS
    }


def serialize_share(share):
    """Owner-facing full serializer. Query-light: reads target refs via *_id and
    only touches the related row's already-loaded fields (callers select_related).
    Computes ``url_path`` and, for artifact shares, ``source_updated_at`` (outdated
    badge) — the latter walks the FS, so callers on a hot path may skip it by
    passing a pre-fetched share whose related bookmark is None."""
    from twicc.core.enums import ShareKind

    data = {
        "id": share.id,
        "token": share.token,  # plaintext (O2) — owner UI needs it to build the URL
        "kind": share.kind,
        "label": share.label,
        "has_password": bool(share.password_hash),
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "revoked_at": share.revoked_at.isoformat() if share.revoked_at else None,
        "status": share.status(),
        "options": _serialize_share_options(share.options),
        "view_count": share.view_count,
        "last_viewed_at": share.last_viewed_at.isoformat() if share.last_viewed_at else None,
        "notify_on_view": share.notify_on_view,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "updated_at": share.updated_at.isoformat() if share.updated_at else None,
        "url_path": f"/share/{share.token}/",
    }
    if share.kind == ShareKind.SESSION.value:
        data["session_id"] = share.session_id
        sess = share.session if share.session_id else None
        data["project_id"] = sess.project_id if sess else None
        data["target_title"] = (sess.title if sess else None)
    else:
        data["bookmark_id"] = share.artifact_bookmark_id
        bm = share.artifact_bookmark if share.artifact_bookmark_id else None
        data["target_name"] = bm.name if bm else None
        data["allowed_hosts"] = bm.allowed_hosts if bm else {}
        if bm is not None:
            from twicc.core.services.share_mutation import source_updated_at
            src = source_updated_at(bm)
            data["source_updated_at"] = src.isoformat() if src else None
    # Agent provenance (agent-sharing design §9). The created_by channel
    # follows the frontend hidden rule: a hidden creator serializes as
    # {"kind": "agent", "session": null} — never its id or title. The
    # share's own target fields are deliberately NOT subject to this rule
    # (a self-target share of a hidden session still shows session_id /
    # target_title: the transcript it publishes reveals far more).
    creator_id = share.created_by_session_id
    if creator_id is None:
        data["created_by"] = {"kind": "human_or_legacy", "session": None}
    else:
        creator = share.created_by_session
        if creator.hidden:
            data["created_by"] = {"kind": "agent", "session": None}
        else:
            data["created_by"] = {"kind": "agent", "session": {
                "id": creator.id,
                "title": creator.title,
                "project_id": creator.project_id,
            }}
    return data


def serialize_share_public_meta(share):
    """Viewer-facing meta (design §6.2). Never label, never counters, never real
    bookmark/project ids. Session id IS included (needed for media URL rewriting;
    grants nothing — every real route is gated). Title = the `display_title` override,
    else the real session title (per `show_title`) / bookmark name. Cost is never
    exposed to viewers."""
    from twicc.core.enums import ShareKind

    opts = _serialize_share_options(share.options)
    if share.kind == ShareKind.SESSION.value:
        sess = share.session
        frozen = opts.get("frozen_at_line")
        last_line = sess.last_line if sess else 0
        if opts.get("mode") == "snapshot" and frozen is not None:
            last_line = min(last_line, frozen)
        data = {
            "kind": "session",
            "ready": session_compute_ready(sess) if sess else False,
            "session_id": share.session_id,
            "provider": sess.provider if sess else None,
            "last_line": last_line,
            "mode": opts.get("mode", "live"),
            "max_display_mode": opts.get("max_display_mode", "normal"),
            "include_subagents": opts.get("include_subagents", True),
            "show_timestamps": opts.get("show_timestamps", True),
            # Drives the viewer's /compact reorder (compact_summary vs the /compact
            # command land in swapped JSONL order).
            "compacted": bool(sess.compacted) if sess else False,
            "created_at": sess.created_at.isoformat() if sess and sess.created_at else None,
            "last_updated_at": sess.last_updated_at.isoformat() if sess and sess.last_updated_at else None,
        }
        # Public title: show_title is the master switch — off ⇒ no title at all (the
        # viewer sees the generic label). On ⇒ the owner's display_title override,
        # else the real session title.
        if opts.get("show_title", True):
            display_title = (opts.get("display_title") or "").strip()
            data["title"] = display_title or (sess.title if sess else None)
        return data
    # Artifact: show_title is the master switch (as for sessions) — off ⇒ no title
    # at all (the viewer sees the generic label). On ⇒ the owner's display_title
    # override, else the real bookmark name.
    bookmark = share.artifact_bookmark
    data = {
        "kind": "artifact",
        "snapshot_at": opts.get("snapshot_at"),
    }
    if opts.get("show_title", True):
        display_title = (opts.get("display_title") or "").strip()
        data["title"] = display_title or (bookmark.name if bookmark else None)
    return data


def serialize_peer(peer):
    """Owner-facing peer serializer. NO tokens, EVER (token_ours/token_theirs are
    bearer secrets). ``verification_code`` is included ONLY on pending_received
    rows — the owner UI must display it (design §4.2 step 2); every other state
    serializes "" to shrink exposure. It never reaches the agent surface (the
    CLI's peers output is a separate hand-built dict).

    SECURITY INVARIANT: peer broadcasts ride the shared "updates" channel group,
    which share viewers also join; ShareConsumer.broadcast (share/consumer.py)
    is a strict whitelist that silently drops all peer_* types — that is what
    keeps codes away from share links. Never add a peer type to that whitelist.
    """
    from twicc.core.models import PeerState

    return {
        "id": peer.id,
        "name": peer.name,
        "remote_display_name": peer.remote_display_name,
        "base_url": peer.base_url,
        "state": peer.state,
        "broken_reason": peer.broken_reason,
        "reconnect_direction": peer.reconnect_direction,
        # Crossed handshake marker (design §4.2): a pending_received row that
        # also carries OUR outbound leg (both users added each other). The UI
        # must offer the code entry on it — verification applies symmetrically.
        # Only token EXISTENCE is exposed, never the token.
        "crossed": peer.state == PeerState.PENDING_RECEIVED and peer.token_ours is not None,
        "verification_code": peer.verification_code if (
            peer.state == PeerState.PENDING_RECEIVED or peer.reconnect_direction == "received"
        ) else "",
        "verified_at": peer.verified_at.isoformat() if peer.verified_at else None,
        "code_confirmed_at": peer.code_confirmed_at.isoformat() if peer.code_confirmed_at else None,
        "remote_accepted_at": peer.remote_accepted_at.isoformat() if peer.remote_accepted_at else None,
        "created_at": peer.created_at.isoformat() if peer.created_at else None,
        "accepted_at": peer.accepted_at.isoformat() if peer.accepted_at else None,
        "last_contact_at": peer.last_contact_at.isoformat() if peer.last_contact_at else None,
    }


def peer_message_session_ref(session):
    """A peer message's local session, as the UI needs it: ``{id, title,
    project_id}`` or ``None``.

    The title is read LIVE from the session row, never stored on the message:
    a session gets renamed (by its user or by the title generator), and the
    inbox must show the name the user knows today. It is also the reason the
    UI never has to fall back on an id — the one thing a human cannot place.

    Callers in an async context MUST have loaded the relation
    (``select_related``); the broadcast helpers do it for every push, the REST
    views for every list. Sync callers (CLI) may let it lazy-load.
    """
    if session is None:
        return None
    return {"id": session.id, "title": session.title, "project_id": session.project_id}


def peer_message_own_project(message):
    """The message's OWN ``EffectiveContext``: its local session's project,
    else the hand-attached one, else none. No query: the session FKs are
    select_related by every caller. The thread fallback needs the other
    rows — see ``peer_messages.peer_message_projects_map``."""
    from twicc.core.services.peer_messages import (
        PROJECT_SOURCE_ATTACHED, PROJECT_SOURCE_SESSION, EffectiveContext,
    )

    session = message.origin_session if message.origin_session_id else (
        message.delivered_to_session if message.delivered_to_session_id else None
    )
    if session is not None and session.project_id:
        return EffectiveContext(session.project_id, PROJECT_SOURCE_SESSION)
    if message.project_id:
        return EffectiveContext(message.project_id, PROJECT_SOURCE_ATTACHED)
    return EffectiveContext(None, None)


def serialize_peer_message(message, *, include_payload=False, include_attachments=True, effective_project=None):
    """Peer-message serializer. Summary form by default — base64 blobs must never
    transit the channel layer; only the REST detail endpoint passes
    ``include_payload=True``.

    ``effective_project`` is the ``EffectiveContext`` resolved through the
    thread (``peer_messages.peer_message_projects_map``); callers that have
    the map pass it so a session-less reply reports the project — and the
    session — it inherits. Without it, only the message's own project is
    reported."""
    from twicc.core.models import PeerMessageDirection

    effective = effective_project or peer_message_own_project(message)

    reply_to_message = message.reply_to_message
    reply_to_ref = None
    reply_target = None
    if reply_to_message is not None:
        reply_to_ref = {
            # The local row id, so the UI can open the answered message: the
            # `message_id` is the peer-facing handle, not what the review
            # dialog and the REST detail endpoint are keyed on.
            "id": reply_to_message.pk,
            "message_id": reply_to_message.message_id,
            "title": reply_to_message.title,
            "direction": reply_to_message.direction,
            "status": reply_to_message.status,
            # The parent's authorship (`origin.author`, absent = agent): an
            # outbound parent written directly by the owner has no origin
            # session by construction, which the review dialog must not
            # report as a session that went missing.
            "author": (reply_to_message.origin or {}).get("author") or "agent",
        }
        reply_target = (
            reply_to_message.origin_session_id
            if reply_to_message.direction == PeerMessageDirection.OUT
            else reply_to_message.delivered_to_session_id
        )
    text = (message.payload or {}).get("text", "") or ""
    # Who answered this message, if anyone: the authorship of its most recent
    # reply. Read from the prefetched reverse relation — `.all()` iterated in
    # Python, never `.filter()`/`.latest()`, which would query per row.
    # An absent author is the historical reading: agent.
    latest_reply_author = None
    latest_reply = max(message.replies.all(), key=lambda reply: reply.created_at, default=None)
    if latest_reply is not None:
        latest_reply_author = (latest_reply.origin or {}).get("author") or "agent"
    data = {
        "id": message.pk,
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "reply_to": message.reply_to,
        "reply_to_ref": reply_to_ref,
        "reply_target": reply_target,
        # "agent" | "human" | null — which side wrote the reply is the row's
        # own direction: replies to an outbound message are the peer's, to an
        # inbound one the owner's.
        "latest_reply_author": latest_reply_author,
        "peer_id": message.peer_id,
        "direction": message.direction,
        # Required on every send since 2026-08-11; "" on older rows — the UI
        # skips the line rather than inventing a subject.
        "title": message.title,
        "status": message.status,
        "error": message.error,
        "text_preview": text[:300],
        "text_bytes": len(text.encode("utf-8")),
        "attachments_meta": message.attachments_meta,
        "origin": message.origin,
        "recipient_note": message.recipient_note,
        # The two LOCAL endpoints, one per direction: where an outbound message
        # left from, where an inbound one landed. The inbox shows either as the
        # same "local session" column. The ids serve scripts; the refs carry
        # the live title + project the UI displays.
        "origin_session_id": message.origin_session_id,
        "delivered_to_session_id": message.delivered_to_session_id,
        "origin_session": peer_message_session_ref(message.origin_session),
        "delivered_to_session": peer_message_session_ref(message.delivered_to_session),
        # The project attached BY HAND (null = none). Distinct from the
        # effective one below, which it feeds only when no session does.
        "project_id": message.project_id,
        # `{id, source}` — source "session" | "attached" | "conversation" — or
        # null: the project the message counts under (the inbox filter, the
        # "In project" line). "conversation" = inherited from its thread (the
        # nearest ancestor of its reply chain that owns one, else the nearest
        # other row of the thread); the row itself names nothing.
        "effective_project": (
            {"id": effective.project_id, "source": effective.source} if effective.project_id else None
        ),
        # `{id, title, project_id}` or null: the session behind a
        # "conversation" project, when that thread row has one — the session
        # a session-less message is about. The row's own session is not
        # repeated here (see the two refs above).
        "effective_session": effective.session,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "resolved_at": message.resolved_at.isoformat() if message.resolved_at else None,
        "purged": message.purged_at is not None,
    }
    if include_payload:
        payload = message.payload or {}
        data["payload"] = payload if include_attachments else {
            "text": text,
            "images": [],
            "documents": [],
        }
    return data
