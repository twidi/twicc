"""Pure helpers for notification-target list edits (add / update / remove / find).

No Django, no I/O — all functions return new lists and never mutate their inputs.
"""
from __future__ import annotations

import uuid


class TargetNotFound(Exception):
    pass


def find_target(targets: list, target_id: str) -> dict:
    """Return the target dict with the given id, or raise TargetNotFound."""
    for t in targets:
        if t.get("id") == target_id:
            return t
    raise TargetNotFound(f"No notification target with id {target_id!r}.")


def add_target(targets: list, *, url: str, name: str = "", flags: dict) -> list:
    """Return a new list with a new target appended.

    Defaults: enabled=True, tested=None, notifyUserTurn=True,
    notifyPendingRequest=True, notifyExtraUsageStart=True, notifyPeer=True,
    awayOnly=True.
    Any key present in *flags* overrides the corresponding default.
    """
    new_target = {
        "id": uuid.uuid4().hex,
        "name": name,
        "url": url,
        "enabled": True,
        "tested": None,
        "notifyUserTurn": True,
        "notifyPendingRequest": True,
        "notifyExtraUsageStart": True,
        "notifyPeer": True,
        "awayOnly": True,
        **flags,
    }
    return [*targets, new_target]


def update_target(targets: list, target_id: str, patch: dict) -> list:
    """Return a new list with the target shallow-merged with *patch*.

    If *patch* changes the url, tested is reset to None.
    Raises TargetNotFound if no target with target_id exists.
    """
    find_target(targets, target_id)  # raise early if absent

    def _merge(t: dict) -> dict:
        if t.get("id") != target_id:
            return t
        merged = {**t, **patch}
        # Reset tested when url changes.
        if "url" in patch and patch["url"] != t.get("url"):
            merged["tested"] = None
        return merged

    return [_merge(t) for t in targets]


def remove_target(targets: list, target_id: str) -> list:
    """Return a new list without the target with target_id.

    Raises TargetNotFound if no target with target_id exists.
    """
    find_target(targets, target_id)  # raise early if absent
    return [t for t in targets if t.get("id") != target_id]
