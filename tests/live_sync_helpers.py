"""Explicit full-file replay for fixtures that predate bounded live slices."""

from twicc.providers.live_sync import LiveSyncLimits, LiveSyncUpdates, merge_live_updates


REPLAY_LIMITS = LiveSyncLimits()

def drain_live_sync(compute, session, path, *, limits=None):
    limits = limits or REPLAY_LIMITS
    updates = LiveSyncUpdates.empty()
    while True:
        result = compute.sync_session_slice(session.id, path, limits=limits)
        updates = merge_live_updates(updates, result.updates)
        session.refresh_from_db()
        if not result.has_more:
            return updates
