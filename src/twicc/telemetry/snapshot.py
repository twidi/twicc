"""Daily telemetry snapshot, derived from the DB at send time (design §3/§5.2).

Counters, booleans, enums and buckets only — never content, titles,
paths, or identifiers (§3.3). Sync code: run it in a thread from the task.
"""

from __future__ import annotations

import platform
import sys
from datetime import date, datetime, time, timedelta, UTC
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from twicc.core.models import (
    ArtifactBookmark,
    DailyActivity,
    Peer,
    PeerMessage,
    PeerState,
    Project,
    Session,
    SessionCron,
    SessionType,
    Share,
    Workflow,
)
from twicc.core.services.peer_messages import PEER_MESSAGE_AUTHORS
from twicc.core.services.peer_tokens import peer_base_url
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.state import get_enabled_providers
from twicc.telemetry.install_method import detect_install_method
from twicc.telemetry.state import MAX_DAY_ENTRIES
from twicc.workspaces import read_workspaces

SCHEMA_VERSION = 1

# Buckets are part of the public schema (the collector's transparency page
# lists the exact edges) — do not change edges without a schema bump.
#
# (upper_bound_exclusive, label); None = catch-all, always last.
COST_BUCKETS: tuple[tuple[int | None, str], ...] = (
    (0, "0"),
    (1, "<1"),
    (10, "1-10"),
    (50, "10-50"),
    (100, "50-100"),
    (250, "100-250"),
    (500, "250-500"),
    (1000, "500-1000"),
    (None, "1000+"),
)
# Integer counts: upper bound EXCLUSIVE, so 1 -> "1", 5 -> "2-5", 20 -> "6-20".
WORKSPACE_BUCKETS: tuple[tuple[int | None, str], ...] = (
    (0, "0"),
    (2, "1"),
    (6, "2-5"),
    (21, "6-20"),
    (None, "21+"),
)
PROJECT_BUCKETS: tuple[tuple[int | None, str], ...] = (
    (0, "0"),
    (2, "1"),
    (6, "2-5"),
    (21, "6-20"),
    (51, "21-50"),
    (101, "51-100"),
    (None, "101+"),
)
PRESENCE_BUCKETS: tuple[tuple[int | None, str], ...] = (
    (0, "0"),
    (30, "<30"),
    (120, "30-120"),
    (360, "120-360"),
    (720, "360-720"),
    (None, "720+"),
)


def bucket(value: int | Decimal, edges: tuple[tuple[int | None, str], ...]) -> str:
    """Map ``value`` to the label of its bucket.

    Edges are upper bounds, exclusive, in ascending order, ``None`` marking
    the catch-all last bucket. The very first (zero) edge is the sole
    exception and uses ``<=`` so a value of exactly 0 lands in the "0"
    bucket instead of falling through to the next one.
    """
    for upper, label in edges:
        if upper is None:
            return label
        if upper == 0:
            if value <= upper:
                return label
            continue
        if value < upper:
            return label
    return edges[-1][1]


def model_family_version(
    provider: str, selected_model: str | None, raw_model: str | None = None
) -> tuple[str, str]:
    """Resolve a session's model to its ``(family, version)`` couple
    (e.g. ``("opus", "4.8")``) — versions of one family are distinct signals
    and must never be mixed.

    ``selected_model`` is the agent-settings alias, only present on sessions
    created through TwiCC. Sessions merely synced from the provider's files
    (external CLI runs, benchmarks, ...) have it NULL but usually carry
    ``raw_model`` — ``Session.model``, the last SDK model id seen in the JSONL
    (e.g. ``"claude-opus-4-8"``). Anything unresolvable collapses to
    ``("unknown", "unknown")``.
    """
    try:
        helpers = get_provider_helpers(provider)
    except Exception:
        return ("unknown", "unknown")
    for identifier in (selected_model, raw_model):
        if not identifier:
            continue
        try:
            mv = helpers.find_model(identifier)
        except Exception:
            continue
        if not mv:
            # find_model resolves alias forms; raw SDK ids need a full-name
            # match (some provider overrides don't fall back to it themselves).
            mv = next((c for c in helpers.MODEL_VERSIONS if c.full_name == identifier), None)
        if mv:
            return (mv.model, mv.version)
    return ("unknown", "unknown")


def build_instance_block() -> dict:
    workspaces = read_workspaces().get("workspaces", [])
    return {
        "twicc_version": settings.APP_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "os": {"win32": "windows"}.get(sys.platform, sys.platform),
        "arch": platform.machine(),
        "providers": sorted(p.value for p in get_enabled_providers()),
        "install": detect_install_method(),
        "projects_bucket": bucket(Project.objects.count(), PROJECT_BUCKETS),
        "workspaces_bucket": bucket(len(workspaces), WORKSPACE_BUCKETS),
        "remote_access": bool(settings.TWICC_PASSWORD_HASH),
        # Peer messaging adoption, in two steps: an empty `peerBaseUrl` keeps
        # the whole feature off, so the boolean is the configuration gate and
        # the bucket the scale actually reached. Only ACTIVE relationships
        # count — pending, broken and revoked ones are not usage.
        "peer_messaging": bool(peer_base_url()),
        "peers_active_bucket": bucket(
            Peer.objects.filter(state=PeerState.ACTIVE).count(), WORKSPACE_BUCKETS
        ),
    }


def build_day_block(day: date, day_state: dict) -> dict:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)

    # Nested counts: provider -> model family -> version -> effort -> count.
    # Each level is a real dimension (no composite string keys to re-parse
    # downstream — family names contain dashes and versions dots, so a flat
    # key would be ambiguous), versions of one family are never mixed, and
    # aggregating at any level is a subtree walk. A missing effort (external
    # sessions, providers without the field) reads "unknown", mirroring the
    # ("unknown", "unknown") model fallback.
    # Permission modes are provider-specific vocabularies (bypassPermissions
    # vs yolo, ...), so that breakdown is per provider too.
    sessions_by_model_effort: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    sessions_by_permission_mode: dict[str, dict[str, int]] = {}

    session_rows = Session.objects.filter(
        type=SessionType.SESSION, created_at__gte=start, created_at__lt=end,
    ).values_list("provider", "selected_model", "model", "effort", "permission_mode")

    for provider, selected_model, raw_model, effort, permission_mode in session_rows:
        family, version = model_family_version(provider, selected_model, raw_model)
        efforts = (
            sessions_by_model_effort.setdefault(provider, {}).setdefault(family, {}).setdefault(version, {})
        )
        effort_key = effort or "unknown"
        efforts[effort_key] = efforts.get(effort_key, 0) + 1
        if permission_mode:
            modes = sessions_by_permission_mode.setdefault(provider, {})
            modes[permission_mode] = modes.get(permission_mode, 0) + 1

    totals = DailyActivity.objects.filter(project__isnull=True, date=day).aggregate(
        messages=Sum("user_message_count"), cost=Sum("cost"),
    )
    messages_sent = totals["messages"] or 0
    total_cost = totals["cost"] or Decimal(0)

    subagents = Session.objects.filter(
        type=SessionType.SUBAGENT, created_at__gte=start, created_at__lt=end,
    ).count()
    sessions_spawned = Session.objects.filter(
        type=SessionType.SESSION, spawned_by__isnull=False, created_at__gte=start, created_at__lt=end,
    ).count()
    workflow_runs = Workflow.objects.filter(updated_at__gte=start, updated_at__lt=end).count()
    crons_created = SessionCron.objects.filter(created_at__gte=start, created_at__lt=end).count()
    shares_created = Share.objects.filter(created_at__gte=start, created_at__lt=end).count()
    bookmarks_created = ArtifactBookmark.objects.filter(created_at__gte=start, created_at__lt=end).count()

    # Peer messaging traffic: direction -> new/reply -> author. Three closed
    # vocabularies, so the counts stay sparse like sessions_by_model_effort —
    # an instance with no peer traffic sends {}. Direction is what makes the
    # fleet totals readable: one message is an "out" here and an "in" on the
    # other instance, which reports its own payload.
    # The author is a sender-declared hint, already whitelisted on receive;
    # re-clamping it here is the payload's own guarantee that no free-form
    # remote string can become a key (§3.3).
    peer_messages: dict[str, dict[str, dict[str, int]]] = {}
    peer_rows = PeerMessage.objects.filter(
        created_at__gte=start, created_at__lt=end,
    ).values_list("direction", "reply_to", "origin")

    for direction, reply_to, origin in peer_rows:
        author = origin.get("author") if isinstance(origin, dict) else None
        if author not in PEER_MESSAGE_AUTHORS:
            author = "unknown"
        authors = peer_messages.setdefault(direction, {}).setdefault("reply" if reply_to else "new", {})
        authors[author] = authors.get(author, 0) + 1

    return {
        "date": day.isoformat(),
        "sessions_by_model_effort": sessions_by_model_effort,
        "sessions_by_permission_mode": sessions_by_permission_mode,
        "messages_sent": messages_sent,
        "subagents": subagents,
        "sessions_spawned": sessions_spawned,
        "workflow_runs": workflow_runs,
        "crons_created": crons_created,
        "shares_created": shares_created,
        "bookmarks_created": bookmarks_created,
        "peer_messages_by_direction_kind_author": peer_messages,
        "cost_bucket": bucket(total_cost, COST_BUCKETS),
        "presence_bucket": bucket(day_state.get("presence_minutes", 0), PRESENCE_BUCKETS),
        "peak_agents": day_state.get("peak_agents", 0),
    }


def build_payload(state: dict) -> dict | None:
    """Payload for all complete UTC days after ``state["last_sent_date"]``.

    Returns ``None`` when there is no complete unsent day. Capped at the 30
    most recent days. The "days" list is sorted ascending by date —
    ``send_cycle()`` relies on ``days[-1]["date"]`` being the most recent day
    covered.
    """
    last_sent = date.fromisoformat(state["last_sent_date"])
    today = datetime.now(UTC).date()

    complete_days = []
    day = last_sent + timedelta(days=1)
    while day < today:
        complete_days.append(day)
        day += timedelta(days=1)
    if not complete_days:
        return None
    complete_days = complete_days[-MAX_DAY_ENTRIES:]

    day_states = state.get("days", {})
    day_blocks = [build_day_block(d, day_states.get(d.isoformat(), {})) for d in complete_days]

    return {
        "schema": SCHEMA_VERSION,
        "instance_id": state["instance_id"],
        "instance": build_instance_block(),
        "days": day_blocks,
    }
