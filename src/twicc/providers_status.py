"""Persisted upstream service status per provider — ``<data_dir>/providers-status.json``.

One record per provider, written by two producers and read by every client:

- the shared statuspage poll (``providers/statuspage_task``) records the
  vendor's current status and tracks **incidents** — one incident spans from
  the first non-operational status after an ``operational`` one (or from the
  first status ever observed, when no ``operational`` is known) to the return
  to ``operational``. A resolved incident is kept, not dropped: it is what lets
  a client that was away learn there *was* an outage, and when;
- the WebSocket handler records the user's **acknowledgment** — which episode
  of the current incident they dismissed the toast for. It is one value for
  the whole installation, so dismissing on one tab or device settles it
  everywhere.

On-disk shape::

    {
      "<provider>": {
        "status": "operational" | "degraded_performance" | ... | null,
        "incident": {
          "started_at": iso,            # identity of the incident; never moves
          "status": <last non-operational level>,
          "changed_at": iso,            # identity of its latest transition
          "resolved_at": iso | null
        } | null,
        "acknowledged": {"started_at": iso, "status": <level> | "operational", "changed_at": iso} | null
      }
    }

An *episode* is one transition of an incident: its opening, every change of
level, its resolution. ``changed_at`` is the transition's identity, so each
step is announced and acknowledged on its own — ``major → partial → major``
is three episodes, the second ``major`` included, even though it shares its
level with the first. The incident's ``started_at`` never moves: it is what
the "since when" window is built on. The poll compares plain status values,
so ``major`` followed by ``major`` is one continuous episode, not two.

Both writers go through :func:`twicc.atomic_json.locked_json_file`, so the
read-modify-write of one cannot lose the other's patch. The lock blocks the
thread: event-loop callers wrap these functions in ``sync_to_async``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import orjson
from channels.layers import get_channel_layer

from twicc.atomic_json import CorruptConfigError, locked_json_file
from twicc.core.enums import Provider
from twicc.paths import get_providers_status_path

logger = logging.getLogger(__name__)

OPERATIONAL = "operational"

# WebSocket message type carrying the whole file, sent on connect and after
# every write. The frontend derives everything (which toast to show, what to
# clear) from this one payload.
PROVIDERS_STATUS_MESSAGE_TYPE = "providers_status_updated"

_KNOWN_PROVIDERS = frozenset(p.value for p in Provider)


# ── Shape helpers ────────────────────────────────────────────────────────────

def _empty_entry() -> dict:
    return {"status": None, "incident": None, "acknowledged": None}


def _is_iso(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _normalise_episode(raw: object) -> dict | None:
    """Return a clean ``{started_at, status, changed_at}`` or ``None`` when *raw* is not one."""
    if not isinstance(raw, dict):
        return None
    started_at = raw.get("started_at")
    status = raw.get("status")
    changed_at = raw.get("changed_at")
    if not _is_iso(started_at) or not _is_iso(changed_at) or not isinstance(status, str) or not status:
        return None
    return {"started_at": started_at, "status": status, "changed_at": changed_at}


def _normalise_incident(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    started_at = raw.get("started_at")
    status = raw.get("status")
    if not _is_iso(started_at) or not isinstance(status, str) or not status:
        return None
    resolved_at = raw.get("resolved_at")
    if resolved_at is not None and not _is_iso(resolved_at):
        return None
    changed_at = raw.get("changed_at")
    if changed_at is not None and not _is_iso(changed_at):
        return None
    return {
        "started_at": started_at,
        "status": status,
        # A record written before transitions were dated: its latest
        # transition is the resolution when there is one, else the opening.
        "changed_at": changed_at or resolved_at or started_at,
        "resolved_at": resolved_at,
    }


def _normalise_entry(raw: object) -> dict | None:
    """Return a clean provider entry, or ``None`` when *raw* is unusable."""
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if status is not None and (not isinstance(status, str) or not status):
        return None
    incident = raw.get("incident")
    acknowledged = raw.get("acknowledged")
    entry = {
        "status": status,
        "incident": _normalise_incident(incident) if incident is not None else None,
        "acknowledged": _normalise_episode(acknowledged) if acknowledged is not None else None,
    }
    # A present-but-invalid sub-object means the file was hand-edited or
    # produced by a different version: refuse the whole entry rather than
    # guess, the next poll rebuilds it.
    if incident is not None and entry["incident"] is None:
        return None
    if acknowledged is not None and entry["acknowledged"] is None:
        return None
    return entry


def _normalise_state(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    state: dict[str, dict] = {}
    for provider, entry in raw.items():
        if provider not in _KNOWN_PROVIDERS:
            continue
        clean = _normalise_entry(entry)
        if clean is None:
            logger.warning("providers-status.json: dropping invalid entry for %s", provider)
            continue
        state[provider] = clean
    return state


def _utc_iso(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


# ── Reads ────────────────────────────────────────────────────────────────────

def read_providers_status() -> dict[str, dict]:
    """Read the file defensively: ``{}`` when missing or invalid, bad entries dropped."""
    path = get_providers_status_path()
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except orjson.JSONDecodeError:
        logger.warning("providers-status.json is invalid JSON, returning empty state")
        return {}
    return _normalise_state(data)


def build_providers_status_message(state: dict[str, dict] | None = None) -> dict:
    """The WebSocket frame for *state* (read from disk when not given)."""
    return {
        "type": PROVIDERS_STATUS_MESSAGE_TYPE,
        "providers_status": read_providers_status() if state is None else state,
    }


# ── Writes ───────────────────────────────────────────────────────────────────

def record_status(provider: Provider, status: str, *, now: datetime | None = None) -> dict[str, dict] | None:
    """Record the vendor's current *status* for *provider*; drive its incident.

    Returns the new full state when something changed, ``None`` when *status*
    equals the recorded one (nothing written). Invalid input is ignored the
    same way.
    """
    if not isinstance(status, str) or not status:
        return None
    path = get_providers_status_path()
    try:
        with locked_json_file(path, default={}) as txn:
            state = _normalise_state(txn.data)
            entry = state.get(provider.value) or _empty_entry()
            if entry["status"] == status:
                return None
            incident = entry["incident"]
            stamp = _utc_iso(now)
            if status != OPERATIONAL:
                if incident is None or incident["resolved_at"] is not None:
                    # First non-operational status since the last operational
                    # one (or ever): a new incident starts now.
                    incident = {"started_at": stamp, "status": status, "changed_at": stamp, "resolved_at": None}
                else:
                    # A change of level inside the ongoing incident, up or
                    # down: a new transition (its own identity), same incident.
                    incident = {**incident, "status": status, "changed_at": stamp}
            elif incident is not None and incident["resolved_at"] is None:
                incident = {**incident, "resolved_at": stamp, "changed_at": stamp}
            state[provider.value] = {**entry, "status": status, "incident": incident}
            txn.write(state)
            return state
    except CorruptConfigError as exc:
        logger.warning("providers-status.json refused for write: %s", exc)
        return None


def acknowledge_incident(provider: str, episode: object) -> dict[str, dict] | None:
    """Record that the user dismissed the toast for *episode* of *provider*'s incident.

    Only an episode of the **current** incident is accepted (same
    ``started_at``): a stale tab acknowledging a previous incident must not
    overwrite a fresher acknowledgment and resurrect a settled toast
    everywhere. Returns the new full state, or ``None`` when nothing was
    written (unknown provider, malformed episode, stale incident, unchanged).
    """
    if provider not in _KNOWN_PROVIDERS:
        return None
    clean = _normalise_episode(episode)
    if clean is None:
        return None
    path = get_providers_status_path()
    try:
        with locked_json_file(path, default={}) as txn:
            state = _normalise_state(txn.data)
            entry = state.get(provider)
            if entry is None or entry["incident"] is None:
                return None
            if entry["incident"]["started_at"] != clean["started_at"]:
                return None
            if entry["acknowledged"] == clean:
                return None
            state[provider] = {**entry, "acknowledged": clean}
            txn.write(state)
            return state
    except CorruptConfigError as exc:
        logger.warning("providers-status.json refused for write: %s", exc)
        return None


# ── Broadcast ────────────────────────────────────────────────────────────────

async def broadcast_providers_status(state: dict[str, dict]) -> None:
    """Push the whole *state* to every connected client."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {"type": "broadcast", "data": build_providers_status_message(state)},
    )
