"""Record durable aggregate obligations atomically with source mutations."""

from collections.abc import Iterable, Sequence
from datetime import datetime


from twicc.core.models import Session
from twicc.providers.compute_repair_journal import (
    ActivityKey,
    RepairSnapshot,
    _require_transaction,
    merge_compute_repair,
)
from twicc.providers.live_aggregates import (
    ItemContribution, SessionContribution, contribution_activity_keys, session_contribution,
)


def record_compute_repair(
    session: Session,
    before_items: Sequence[ItemContribution],
    after_items: Sequence[ItemContribution],
    *,
    before_session: SessionContribution,
    prospective_session: SessionContribution | None,
    extra_activity_keys: Iterable[ActivityKey],
    source_changed: bool,
) -> RepairSnapshot:
    """Record bounded source obligations in the source writer transaction."""
    _require_transaction()
    if not session.storage_incarnation or session.storage_incarnation == "None":
        raise ValueError("Compute repair requires a valid persisted incarnation")
    states = (before_session, session_contribution(session))
    if prospective_session is not None:
        states += (prospective_session,)
    keys = contribution_activity_keys((*before_items, *after_items), states)
    # Prospective project/provider scopes must cover worker-provided dates too.
    if prospective_session is not None:
        prospective_items = tuple(item._replace(project_id=prospective_session.project_id,
                                                provider=prospective_session.provider)
                                  for item in (*before_items, *after_items))
        keys.update(contribution_activity_keys(prospective_items))
    extra_activity_keys = tuple(extra_activity_keys)
    keys.update(extra_activity_keys)
    from twicc.providers.contribution_revisions import record_source_changes

    record_source_changes(before_items, after_items, before_sessions=[before_session],
                          after_sessions=[session_contribution(session)], extra_activity_keys=extra_activity_keys)
    return merge_compute_repair(
        session.id, session.storage_incarnation, activity_keys=keys,
        parent_ids=(state.parent_session_id for state in states if state.parent_session_id),
        project_ids=(state.project_id for state in states),
        activity_scopes=((scope, state.provider) for state in states for scope in (state.project_id, None)),
        source_changed=source_changed, baseline_pending=True,
    )


def preserve_pending_repair(
    session_id: str,
    incarnation: str,
    *,
    before_sessions: Sequence[SessionContribution],
    after_sessions: Sequence[SessionContribution],
    before_items: Sequence[ItemContribution] = (),
    after_items: Sequence[ItemContribution] = (),
    historical_timestamps: Iterable[datetime | None] = (),
    source_changed: bool = True,
) -> RepairSnapshot | None:
    """Retain bounded evidence; the historical_timestamps compatibility input is ignored."""
    _require_transaction()
    from twicc.providers.contribution_revisions import SourceKey, record_source_changes

    record_source_changes(before_items, after_items, before_sessions=before_sessions,
                          after_sessions=after_sessions,
                          force_sources=[SourceKey(session_id, incarnation)] if source_changed else ())
    states = (*before_sessions, *after_sessions)
    keys = contribution_activity_keys((*before_items, *after_items), states)
    # Surviving histories are enumerated by guarded tree preparation. Mutation
    # transactions retain scopes and bounded removed-row evidence only.
    return merge_compute_repair(
        session_id, incarnation, activity_keys=keys,
        parent_ids=(state.parent_session_id for state in states if state.parent_session_id),
        project_ids=(state.project_id for state in states),
        activity_scopes=((scope, state.provider) for state in states for scope in (state.project_id, None)),
        source_changed=source_changed,
    )
