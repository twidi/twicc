"""Daily reaper for never-used, orphaned twicc terminal tmux sessions.

A user-facing terminal (the Files/Git/Terminal layout tabs, or a project /
workspace / global terminal) spins up a persistent ``twicc-*`` tmux session on
this instance's terminal socket (``terminal.TMUX_SOCKET_NAME``: ``twicc`` on the
default data dir, suffixed per data dir otherwise). tmux sessions survive the WebSocket close (a close
only *detaches*), with no idle reaper — so merely *viewing* a session's terminal
without ever running anything in it leaks a tmux session that lives until the
process or machine restarts. This task reaps those.

A session is reaped only when it is genuinely waste, i.e. **all** of:

- **Never used** — no byte of input was ever written to its PTY. The terminal
  layer sets the tmux user option ``@twicc_used`` the moment any input reaches a
  session, from *any* source: the user typing, a snippet, an auto-injected
  provider-login command (see ``terminal.py``'s WebSocket input handler). A
  session carrying that flag is never touched, regardless of age.
- **Detached** — no client is attached right now (nobody is viewing it).
- **Stale** — created more than :data:`UNUSED_TMUX_MAX_AGE_SECONDS` ago.

**Hybrid Claude CLI sessions are out of scope by construction**: they live on a
*separate* tmux socket (``HYBRID_TMUX_SOCKET_NAME``); this task only ever talks
to the terminal socket, so it never even sees them.

**Grandfathering** — ``@twicc_used`` is only set going forward (from the first
input after this feature shipped). Sessions that already existed when it shipped
carry no flag even if heavily used, so on first run they would look "never
used". To honor "never touch a used session", the first run marks *every*
pre-existing ``twicc`` session as used, recorded once-ever by a sentinel file in
the data dir (:data:`_GRANDFATHER_MARKER`). Never-used sessions created later
under the new code are unaffected.

All tmux calls + the sentinel I/O are blocking, so each cycle runs on a worker
thread via :func:`asyncio.to_thread`. No DB access.

Gated by ``settings.TMUX_CLEANUP_ENABLED`` (env ``TWICC_NO_TMUX_CLEANUP``). The
socket is per data dir, so a worktree instance only ever sees — and reaps — its
own sessions; the flag is a manual opt-out, not something devctl sets.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time

logger = logging.getLogger(__name__)


# Run once a day, like session_dirs_cleanup. The loop sleeps before its first
# pass so a just-started instance never reaps before the user has interacted.
TMUX_CLEANUP_INTERVAL = 24 * 60 * 60

# A never-used, detached session must be at least this old before it is reaped.
UNUSED_TMUX_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

# Sentinel filename (under the data dir) recording that the one-time grandfather
# of pre-existing sessions has run. Its mere existence is the signal.
_GRANDFATHER_MARKER = ".tmux-reaper-grandfathered"


def _tmux_base() -> list[str] | None:
    """``[tmux, -L, <terminal socket>]`` argv prefix, or ``None`` when tmux is unavailable.

    Always the *terminal* socket (``TMUX_SOCKET_NAME``) — never the hybrid one
    — so hybrid Claude CLI sessions are out of scope by construction.
    """
    from twicc.terminal import TMUX_SOCKET_NAME, get_tmux_path

    tmux_path = get_tmux_path()
    if tmux_path is None:
        return None
    return [tmux_path, "-L", TMUX_SOCKET_NAME]


def _run_tmux(args: list[str]) -> subprocess.CompletedProcess | None:
    """Run one ``tmux -L <terminal socket> <args>``; ``None`` on missing tmux / timeout / OS error."""
    base = _tmux_base()
    if base is None:
        return None
    try:
        return subprocess.run(base + args, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _list_sessions() -> list[tuple[str, str, str, int]]:
    """Return ``(name, used, attached, created_epoch)`` for every twicc session.

    Empty when the tmux server isn't running (no sessions), tmux is missing, or
    the call fails. ``used`` is the raw ``@twicc_used`` value (``""`` when unset);
    ``attached`` is the attached-client count as a string (``"0"`` = detached).
    """
    from twicc.terminal import TMUX_USED_OPTION

    fmt = "\t".join((
        "#{session_name}", f"#{{{TMUX_USED_OPTION}}}", "#{session_attached}", "#{session_created}",
    ))
    result = _run_tmux(["list-sessions", "-F", fmt])
    if result is None or result.returncode != 0:
        return []

    sessions: list[tuple[str, str, str, int]] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, used, attached, created = parts
        try:
            created_epoch = int(created)
        except ValueError:
            continue
        sessions.append((name, used.strip(), attached.strip(), created_epoch))
    return sessions


def _mark_used(name: str) -> None:
    """Set ``@twicc_used`` on one session (per-session ``-t``, never ``-g``/``-s``)."""
    from twicc.terminal import TMUX_USED_OPTION

    _run_tmux(["set-option", "-t", name, TMUX_USED_OPTION, "1"])


def _grandfather_existing_sessions() -> None:
    """Once ever: mark every pre-existing twicc session "used".

    Spares sessions that were in use before the ``@twicc_used`` flag existed from
    being read as "never used" on the first reap. Recorded by the sentinel file,
    so this is a no-op on every later run (even across restarts).
    """
    from twicc.paths import get_data_dir

    marker = get_data_dir() / _GRANDFATHER_MARKER
    if marker.exists():
        return

    grandfathered = 0
    for name, used, _attached, _created in _list_sessions():
        if not used:
            _mark_used(name)
            grandfathered += 1
    try:
        marker.touch()
    except OSError:
        logger.exception("tmux reaper: could not write grandfather marker; will retry next start")
        return
    if grandfathered:
        logger.info("tmux reaper: grandfathered %d pre-existing session(s) as used", grandfathered)


def _reap_unused_tmux_sessions() -> int:
    """Run one reap cycle; return the number of sessions killed.

    Kills a session iff it is unused (no ``@twicc_used``), detached
    (``session_attached == 0``), and older than
    :data:`UNUSED_TMUX_MAX_AGE_SECONDS`. A used session is never touched, at any age.
    """
    now = time.time()
    killed = 0
    for name, used, attached, created in _list_sessions():
        if used:
            continue                                  # used → never touch, any age
        if attached != "0":
            continue                                  # someone is viewing it right now
        if now - created < UNUSED_TMUX_MAX_AGE_SECONDS:
            continue                                  # opened too recently
        result = _run_tmux(["kill-session", "-t", name])
        if result is not None and result.returncode == 0:
            killed += 1
    if killed:
        logger.info("tmux reaper: killed %d never-used tmux session(s)", killed)
    return killed


async def start_tmux_cleanup_task(stop_event: asyncio.Event) -> None:
    """Periodic reaper loop for never-used, orphaned twicc tmux sessions.

    Grandfathers pre-existing sessions once, then sleeps
    :data:`TMUX_CLEANUP_INTERVAL` seconds and reaps, repeating until
    ``stop_event`` is set. No-op — logs and returns — when
    ``settings.TMUX_CLEANUP_ENABLED`` is false.
    """
    from django.conf import settings

    if not settings.TMUX_CLEANUP_ENABLED:
        logger.info("tmux reaper disabled (TWICC_NO_TMUX_CLEANUP is set)")
        return

    logger.info("tmux reaper task started")
    try:
        # One-time grandfather of sessions that predate the @twicc_used flag.
        try:
            await asyncio.to_thread(_grandfather_existing_sessions)
        except Exception:  # noqa: BLE001 — never let backfill take down the loop
            logger.exception("tmux reaper grandfather pass failed")

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=TMUX_CLEANUP_INTERVAL)
            except TimeoutError:
                # Timeout means it's time to reap again.
                pass
            else:
                # stop_event fired — exit before another pass.
                break

            try:
                await asyncio.to_thread(_reap_unused_tmux_sessions)
            except Exception:  # noqa: BLE001 — keep the loop alive across transient errors
                logger.exception("tmux reaper cycle failed")
    finally:
        logger.info("tmux reaper task stopped")
