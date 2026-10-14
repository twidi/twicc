"""Compatibility helpers for existing source and storage tests."""
from tests.statistics_helpers import drain_statistics as drain_aggregates


__all__ = ['begin_history_replacement', 'drain_aggregates']


def begin_history_replacement(job):
    from twicc.providers.codex.rollout_migration import _begin_replace_codex_history
    return _begin_replace_codex_history(job)
