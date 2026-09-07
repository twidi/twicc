"""Shared query/aggregation helpers over a session's items and tool-result links.

Both the owner REST views (``twicc.views``) and the public share views
(``twicc.share.session_views``) read the same underlying data; keeping the range
parsing, the tool-state aggregation and the subagent-link shaping here means the
two surfaces can never drift apart. Unlike ``core.serializers`` (pure, no DB),
the aggregation and slug-resolution helpers here run queries, so async callers
must wrap them in ``sync_to_async``.
"""

from __future__ import annotations

from django.db.models import Count, Max, Q

# The four aggregates that define a tool call's completion state. Kept as a single
# spec so every site that summarizes ``ToolResultLink`` rows stays in lockstep: the
# owner ``tool_states`` view, the share equivalent (both via ``aggregate_tool_states``)
# and the live compute broadcast in ``providers.compute_base``. ``Max`` on
# ``extra``/``error`` keeps the richest value regardless of arrival order (Codex
# emits several links per ``tool_use_id``).
TOOL_STATE_ANNOTATIONS = {
    "result_count": Count("id"),
    "completed_at": Max("tool_result_at"),
    "extra": Max("extra"),
    "error": Max("error"),
}


def parse_line_ranges(raw_ranges) -> Q | None:
    """Parse repeated ``range`` query values into a combined OR ``Q`` on ``line_num``.

    Each value is one of ``N`` (exact line), ``lo:hi`` (inclusive), ``lo:`` (from
    ``lo`` onward) or ``:hi`` (up to ``hi``). Malformed values are skipped. Returns
    ``None`` when nothing valid was parsed — callers turn that into their own
    400 / error response.
    """
    combined = None
    for r in raw_ranges:
        try:
            if ":" not in r:
                cond = Q(line_num=int(r))
            else:
                lo_str, hi_str = r.split(":", 1)
                lo = int(lo_str) if lo_str else None
                hi = int(hi_str) if hi_str else None
                if lo is not None and hi is not None:
                    cond = Q(line_num__gte=lo, line_num__lte=hi)
                elif lo is not None:
                    cond = Q(line_num__gte=lo)
                elif hi is not None:
                    cond = Q(line_num__lte=hi)
                else:
                    continue  # both bounds empty = invalid
        except ValueError:
            continue
        combined = cond if combined is None else (combined | cond)
    return combined


def aggregate_tool_states(links_qs) -> dict:
    """Summarize a ``ToolResultLink`` queryset into the per-tool state payload the
    frontend consumes: ``{tool_use_id: {result_count, completed_at, error, extra,
    tool_result_line_nums}}``.

    ``links_qs`` may be the full session set (owner view) or a
    visibility/ceiling-restricted subset (share view); the aggregation is identical
    either way. A tool with multiple links (Codex ``apply_patch`` / MCP / exec chains)
    exposes every ``tool_result_line_num``, not just the max. Synchronous — async
    callers wrap it in ``sync_to_async``.
    """
    aggregated = links_qs.values("tool_use_id").annotate(**TOOL_STATE_ANNOTATIONS)
    line_nums_by_tool: dict[str, list[int]] = {}
    for tool_use_id, line_num in (
        links_qs.order_by("tool_result_line_num").values_list("tool_use_id", "tool_result_line_num")
    ):
        line_nums_by_tool.setdefault(tool_use_id, []).append(line_num)
    return {
        entry["tool_use_id"]: {
            "result_count": entry["result_count"],
            "completed_at": entry["completed_at"].isoformat() if entry["completed_at"] else None,
            "error": entry["error"],
            "extra": entry["extra"],
            "tool_result_line_nums": line_nums_by_tool.get(entry["tool_use_id"], []),
        }
        for entry in aggregated
    }


def tool_results_payload(session, line_num, tool_id, max_line=None) -> dict:
    """Resolve the tool_result content(s) for one ``tool_use`` into
    ``{"results": [...]}``.

    Finds the linked tool_result line(s) via ``ToolResultLink``, loads those items —
    optionally clamped to ``max_line`` so a frozen share snapshot never leaks a
    post-freeze result — and runs the provider's ``get_tool_results`` extractor.
    Shared by the owner ``tool_results`` view and the share equivalent. Runs queries;
    async callers wrap it in ``sync_to_async``.
    """
    from twicc.core.models import SessionItem, ToolResultLink
    from twicc.providers.helpers import get_provider_helpers

    link_lines = list(
        ToolResultLink.objects.filter(
            session=session, tool_use_line_num=line_num, tool_use_id=tool_id
        ).values_list("tool_result_line_num", flat=True)
    )
    if not link_lines:
        return {"results": []}
    qs = SessionItem.objects.filter(session=session, line_num__in=link_lines)
    if max_line is not None:
        qs = qs.filter(line_num__lte=max_line)
    items = list(qs.order_by("line_num"))
    results = get_provider_helpers(session.provider).get_tool_results(items, tool_id)
    return {"results": results}


def visible_tree_agent_ids(root_id, links, allowed_root_ids):
    """Expand snapshot-visible root spawns through launcher ownership, without a depth cap."""
    parents = {}
    for link in links:
        parents.setdefault(link.agent_id, set()).add(link.session_id)
    visibility = {}
    for agent_id in parents:
        if agent_id == root_id:
            continue
        path = []
        seen = set()
        node = agent_id
        allowed = False
        while node not in visibility and node not in seen:
            seen.add(node)
            path.append(node)
            owners = parents.get(node, set())
            if len(owners) != 1:
                break
            owner = next(iter(owners))
            if owner == root_id:
                allowed = node in allowed_root_ids
                break
            node = owner
        else:
            allowed = visibility.get(node, False)
        for node_id in path:
            visibility[node_id] = allowed
    return {agent_id for agent_id, allowed in visibility.items() if allowed and agent_id != root_id}



def tree_agent_links(root):
    """Read every launcher's links; flat parenthood includes cross-project agents."""
    from twicc.core.models import AgentLink, Session

    owners = Session.objects.filter(parent_session_id=root.id).values("id")
    return list(AgentLink.objects.filter(Q(session_id=root.id) | Q(session_id__in=owners)).exclude(agent_id=root.id).order_by("id"))


def build_subagents_state(root, *, frozen_at_line=None, include_metrics=False):
    """Build the shared owner/share tree payload with one completion scan.

    ``include_metrics`` adds each agent's cost, turns and context usage — owner
    surfaces only. A share link never carries them.
    """
    from twicc.core.models import SessionItem, ToolResultLink
    from twicc.providers.helpers import get_provider_helpers

    links = tree_agent_links(root)
    if frozen_at_line is not None:
        allowed = {link.agent_id for link in links
                   if link.session_id == root.id and link.tool_use_line_num <= frozen_at_line}
        visible = visible_tree_agent_ids(root.id, links, allowed)
        links = [link for link in links if link.agent_id in visible and (
            (link.session_id in visible) if link.session_id != root.id
            else link.tool_use_line_num <= frozen_at_line
        )]
    owners = {link.session_id for link in links}
    results = ToolResultLink.objects.filter(session_id__in=owners)
    if frozen_at_line is not None:
        results = results.filter(~Q(session_id=root.id) | Q(tool_result_line_num__lte=frozen_at_line))
    counts = {(row["session_id"], row["tool_use_id"]): row["count"]
              for row in results.values("session_id", "tool_use_id").annotate(count=Count("id"))}
    helpers = get_provider_helpers(root.provider)
    queue_items = SessionItem.objects.filter(session_id=root.id, content__contains="queue-operation")
    if frozen_at_line is not None:
        queue_items = queue_items.filter(line_num__lte=frozen_at_line)
    completions = {}
    for child_id, tool_id, timestamp in helpers.get_queue_completions(queue_items.only("content", "timestamp")):
        key = (child_id, tool_id)
        if timestamp is not None and (key not in completions or timestamp > completions[key]):
            completions[key] = timestamp
    return serialize_agent_links(
        links, completions=completions, result_counts=counts,
        trust_agent_stopped=helpers.subagent_idle_trusted, root_cutoff=root.cutoff,
        root_session_id=root.id, include_metrics=include_metrics,
    )


def serialize_agent_links(
    links, *, completions=None, result_counts=None, trust_agent_stopped=False,
    root_cutoff=None, root_session_id=None, include_metrics=False,
) -> list[dict]:
    """Serialize links with distinct persisted-completion and provider-idle evidence.

    Child idle may be mtime-derived during recompute. Only providers whose
    parent result stream cannot conclude opt into that signal. Queue completion
    is keyed by both child and tool id and never derives from child idle.

    ``include_metrics`` adds the agent's own cost, turns and context usage, read
    from the same row this already loads for the slug. Off by default: the share
    view builds the very same payload and must not expose them.
    """
    from twicc.core.models import Session

    links = list(links)
    completions = completions or {}
    result_counts = result_counts or {}
    subagents = {
        row[0]: row[1:]
        for row in Session.objects.filter(id__in=[link.agent_id for link in links])
        .values_list("id", "slug", "last_stopped_at", "total_cost", "user_message_count", "context_usage")
    }
    result = []
    for link in links:
        slug, agent_stopped, total_cost, turns, context_usage = subagents.get(
            link.agent_id, (None, None, None, 0, None)
        )
        stopped = completions.get((link.agent_id, link.tool_use_id))
        before_cutoff = root_cutoff is not None and (
            link.started_at is None or link.started_at < root_cutoff
        )
        metrics = {} if not include_metrics else {
            "total_cost": float(total_cost) if total_cost is not None else None,
            "user_message_count": turns,
            "context_usage": context_usage,
        }
        result.append({
            **metrics,
            "agent_id": link.agent_id,
            "agent_slug": slug,
            "owner_session_id": link.session_id,
            "root_session_id": root_session_id,
            "agent_stopped_at": agent_stopped.isoformat() if agent_stopped else None,
            "stopped_at": stopped.isoformat() if stopped else None,
            "tool_use_id": link.tool_use_id,
            "tool_use_line_num": link.tool_use_line_num,
            "is_background": link.is_background,
            "started_at": link.started_at.isoformat() if link.started_at else None,
            "running": not before_cutoff and stopped is None
            and result_counts.get((link.session_id, link.tool_use_id), 0) < (2 if link.is_background else 1)
            and not (trust_agent_stopped and agent_stopped is not None),
        })
    return result
