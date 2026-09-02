"""Tmux primitives for hybrid CLI sessions.

A hybrid session owns exactly one tmux session named ``twicc-hybrid-<id>``
(sanitized), on the dedicated hybrid socket (``HYBRID_TMUX_SOCKET_NAME``:
``twicc-hybrid`` on the default data dir, suffixed per instance otherwise —
separate from the Terminal panel's socket so the user's custom tmux config
never reaches the Claude TUI), whose single pane runs the Claude CLI directly
(via ``exec``) so the pane PID *is* the claude PID and ``pane_dead`` flips when
claude exits (``remain-on-exit on``).

All functions are sync — callers wrap them with ``asyncio.to_thread``.
"""

import logging
import os
import re
import shlex
import subprocess
import time

from twicc.provider_homes import provider_env_overlay
from twicc.terminal import HYBRID_TMUX_SOCKET_NAME, get_tmux_path

logger = logging.getLogger(__name__)

HYBRID_SESSION_PREFIX = "twicc-hybrid-"

# Prefix-based purge, same intent as ClaudeCodeHelpers.purge_env_vars:
# CLAUDE_CODE* and CLAUDECODE*. ``env -u`` needs exact names, so expand the
# prefixes against the CURRENT environment at build time, and always add the
# known marker names below: the tmux server keeps the environment of whoever
# first started it, so a marker can reach the pane through the server even
# when the backend's own environment is clean.
_PURGED_ENV_PREFIXES = ("CLAUDE_CODE", "CLAUDECODE")

# Markers the CLI injects into the environment of its subprocesses (Bash
# tools). Leftovers are NOT harmless: CLAUDE_CODE_CHILD_SESSION alone makes
# a CLI >= 2.1.171 treat itself as a child session and silently skip
# transcript persistence entirely — nothing is written (not even at a
# graceful exit) and --resume answers "No conversation found" (regression of
# the upstream 2.1.170 inherited-env fix; bisected on 2.1.172, 2026-06-11).
_PURGED_ENV_NAMES = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_EXECPATH",
)

# Environment variables FORCED on the hybrid claude process, passed as
# ``NAME=VALUE`` assignments to the same ``env`` wrapper that purges the markers
# above. Names listed here are excluded from the prefix purge so our value stays
# authoritative even when the same name is inherited (these start with the
# purged ``CLAUDE_CODE`` prefix, so the assignment must win over the ``-u``).
_HYBRID_STATIC_LAUNCH_ENV: dict[str, str] = {
    # Suppress the CLI's full-screen repaint flicker in the embedded xterm.
    "CLAUDE_CODE_NO_FLICKER": "1",
}


def _hybrid_launch_env() -> dict[str, str]:
    """The forced launch vars: the static ones plus the configured provider homes.

    The ``NAME=VALUE`` assignments win over the tmux server's frozen
    environment (the server keeps the env of whoever first started it), so a
    hybrid CLI always runs against this instance's ``CLAUDE_CONFIG_DIR`` & co.
    """
    return {**_HYBRID_STATIC_LAUNCH_ENV, **provider_env_overlay()}


def _purged_env_names() -> list[str]:
    names = {name for name in os.environ if name.startswith(_PURGED_ENV_PREFIXES)}
    names.update(_PURGED_ENV_NAMES)
    return sorted(names)


def launch_command(argv: list[str]) -> str:
    """The pane command: ``exec env -u VAR… NAME=VALUE… <argv>`` (pure, testable).

    ``exec env`` ensures the ``sh -c`` wrapper is replaced, the purged
    variables never reach claude (regardless of the tmux server env), and the
    forced launch vars are set authoritatively.
    """
    forced = _hybrid_launch_env()
    unsets: list[str] = []
    for var in _purged_env_names():
        # A forced var is set below; never also unset it (it would be ambiguous
        # whether the ``-u`` or the assignment wins across env implementations).
        if var in forced:
            continue
        unsets += ["-u", var]
    assignments = [f"{key}={value}" for key, value in forced.items()]
    env_args = unsets + assignments
    return "exec env " + shlex.join(env_args + argv) if env_args else "exec " + shlex.join(argv)


def hybrid_tmux_session_name(session_id: str) -> str:
    """Tmux session name for a hybrid session (disjoint from the Terminal
    panel's ``twicc-<id>`` namespace)."""
    return HYBRID_SESSION_PREFIX + session_id.replace(".", "_").replace(":", "_")


def _tmux_base() -> list[str]:
    tmux = get_tmux_path()
    if tmux is None:
        raise RuntimeError("tmux is not installed — hybrid mode requires tmux")
    return [tmux, "-L", HYBRID_TMUX_SOCKET_NAME]


def _run(args: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_tmux_base(), *args],
        input=input_bytes,
        capture_output=True,
        timeout=5,
        check=False,
    )


def session_exists(session_id: str) -> bool:
    # '=' forces exact-name match (no prefix matching).
    return _run(["has-session", "-t", "=" + hybrid_tmux_session_name(session_id)]).returncode == 0


def create_session(session_id: str, cwd: str, argv: list[str]) -> None:
    """Create the tmux session running ``argv`` directly as the pane command.

    See :func:`launch_command` for the ``exec env`` wrapper.
    """
    name = hybrid_tmux_session_name(session_id)
    command = launch_command(argv)
    base = _tmux_base()
    # NEVER load the user's custom tmux config (``terminalTmuxConfigPath``) for
    # the hybrid CLI pane. That setting is meant for the user's own Terminal
    # panel; here it would only risk interfering with the embedded Claude TUI
    # (status scripting, key bindings, escape-time, colours…). Always start from
    # a clean ``/dev/null`` config — the session-level options set just below
    # force the few settings the hybrid pane actually needs.
    base += ["-f", "/dev/null"]
    result = subprocess.run(
        [*base, "new-session", "-d", "-s", name, "-c", cwd, command],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux new-session failed: {result.stderr.decode(errors='replace')}")
    # NOTE: set-option's -t is a target-PANE (options exist at every level
    # since tmux 3.x): a bare '=name' fails with "no such session" — and
    # _run ignores return codes here, so the failure would be silent
    # (it was, before the '=name:' form fixed it). Same form as paste/capture.
    target = "=" + name + ":"
    _run(["set-option", "-t", target, "remain-on-exit", "on"])
    _run(["set-option", "-t", target, "mouse", "off"])
    # The embedded composer terminal shows ONLY the claude TUI — the tmux
    # status bar would waste a line and wrap noisily at narrow widths.
    _run(["set-option", "-t", target, "status", "off"])


def read_process_environ(pid: int) -> dict[str, str] | None:
    """Best-effort environment of a live process, or ``None`` when unreadable.

    Linux: ``/proc/<pid>/environ``. macOS: ``ps -Eww`` (the environment is
    appended to the command line as ``NAME=VALUE`` words; only unambiguous
    words are kept). Used at boot adoption to warn when a surviving hybrid CLI
    runs against provider homes that differ from this instance's.
    """
    try:
        raw = open(f"/proc/{pid}/environ", "rb").read()
    except OSError:
        raw = None
    if raw is not None:
        env: dict[str, str] = {}
        for chunk in raw.split(b"\0"):
            if b"=" in chunk:
                key, _, value = chunk.decode("utf-8", errors="replace").partition("=")
                env[key] = value
        return env
    try:
        result = subprocess.run(
            ["ps", "-Eww", "-o", "command=", "-p", str(pid)],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    env = {}
    for word in result.stdout.decode("utf-8", errors="replace").split():
        if "=" in word and not word.startswith("="):
            key, _, value = word.partition("=")
            env[key] = value
    return env


def pane_status(session_id: str) -> tuple[int | None, bool]:
    """Return ``(pane_pid, pane_dead)``; ``(None, True)`` if the session is gone."""
    result = _run(
        ["list-panes", "-t", "=" + hybrid_tmux_session_name(session_id), "-F", "#{pane_pid} #{pane_dead}"]
    )
    if result.returncode != 0:
        return None, True
    parts = result.stdout.decode().split()
    if len(parts) < 2:
        return None, True
    return int(parts[0]), parts[1] == "1"


def _pane_target(session_id: str) -> str:
    """Exact-match target-PANE for the session's active pane.

    Pane-target commands (``paste-buffer``, ``send-keys``, ``capture-pane``)
    reject a bare ``=name`` ("can't find pane") — the exact-session form for
    a pane is ``=name:`` (trailing colon = active window/pane). Verified
    empirically; session-target commands keep the bare ``=name`` form.
    """
    return "=" + hybrid_tmux_session_name(session_id) + ":"


def capture_pane(session_id: str) -> str | None:
    """Return the visible pane content, or ``None`` if the session is gone.

    Used to detect TUI dialogs that must clear before a paste (the trust
    dialog swallows pasted text entirely — verified empirically).
    """
    result = _run(["capture-pane", "-p", "-t", _pane_target(session_id)])
    if result.returncode != 0:
        return None
    return result.stdout.decode(errors="replace")


# The TUI frames its composer with runs of "─". The frame lines are NOT
# always pure: the top border embeds the session title
# ("──── My session title ──") and the bottom border can embed mode
# indicators ("──── ● high · /effort"). Worse, on NARROW panes a long
# title makes the top border WRAP, and the line adjacent to the chevron
# becomes the title tail with ZERO leading dashes ("backend ──") —
# verified empirically down to 28 columns. So a border line is any line
# that STARTS with a run of "─" (right-aligned indicators keep the
# leading run at every width) OR ENDS with one (the wrapped title tail
# always does). Edit-diff rules use "╌" and box borders start/end with
# "╭"/"╮"/"╰"/"╯" — none match. Checked with search(), not match(): the
# first alternative is ^-anchored, the second matches at end of line.
# The bare chevron may be followed by a non-breaking space (\xa0), which
# \s covers in unicode mode.
_COMPOSER_BORDER_RE = re.compile(r"^\s*─{4,}|─{2,}\s*$")
_EMPTY_COMPOSER_RE = re.compile(r"^❯\s*$")


def composer_ready(session_id: str) -> bool:
    """True when the TUI shows its EMPTY composer, ready for a paste.

    The pattern is a bare ``❯`` line sandwiched between two full-width
    separator lines. It is the pre-paste guard: while any TUI dialog is
    open (permission prompt, AskUserQuestion, plan approval, the bare
    ``/effort``/``/fast`` pickers, the Tab "amend" field…) a paste is
    swallowed and its trailing Enter would VALIDATE the highlighted
    option; and while the user has text typed in the TUI composer, a
    paste would append to (and submit) their draft. In both states the
    sandwich is absent — verified empirically on 2.1.170/2.1.172
    (2026-06-11), including during a turn (spinner), where the empty
    composer stays visible so steering pastes keep working. History
    echoes (``❯ <sent text>``) never match: they carry text and are not
    framed by separators. The status line is never inspected (it is
    user-scripted, arbitrary content). Re-verify this pattern at every
    CLI bump.
    """
    screen = capture_pane(session_id)
    if screen is None:
        return False
    lines = screen.splitlines()
    return any(
        _EMPTY_COMPOSER_RE.match(lines[i])
        and _COMPOSER_BORDER_RE.search(lines[i - 1])
        and _COMPOSER_BORDER_RE.search(lines[i + 1])
        for i in range(1, len(lines) - 1)
    )


# A composer line carrying text: ``❯`` followed by anything (the empty
# variant is _EMPTY_COMPOSER_RE).
_COMPOSER_LINE_RE = re.compile(r"^❯(.*)$")

# Post-submit verification polls (seconds between checks).
_SUBMIT_VERIFY_DELAYS = (0.15, 0.3, 0.5)


def _composer_text(screen: str) -> str | None:
    """Text sitting in the TUI composer: ``''`` if empty, None if no composer.

    The composer's first line is a ``❯`` line directly under a separator
    (the block's top border). Scanned bottom-up — the composer is the lowest
    such line, and history echoes (``❯ <sent text>``) are not framed by
    separators. Only the first composer line is returned: enough to
    recognize a pasted text, wrapped continuation lines don't matter.
    """
    lines = screen.splitlines()
    for i in range(len(lines) - 1, 0, -1):
        match = _COMPOSER_LINE_RE.match(lines[i])
        if match and _COMPOSER_BORDER_RE.search(lines[i - 1]):
            return match.group(1).strip()
    return None


def _ensure_submitted(session_id: str, target: str, text: str) -> None:
    """Re-press Enter when the paste's trailing Enter was swallowed.

    The CLI's composer occasionally drops an Enter arriving right behind the
    bracketed paste (the text hadn't been committed yet), leaving the pasted
    text sitting unsubmitted — observed twice in real use. Poll the composer:
    empty → submitted, done; still showing OUR text → press Enter again;
    anything else (a dialog popped up, no composer on screen) → hands off,
    a blind Enter there would validate the dialog's highlighted option.
    """
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "").strip()
    prefix = first_line[:40]
    if not prefix:
        return
    for delay in _SUBMIT_VERIFY_DELAYS:
        time.sleep(delay)
        screen = capture_pane(session_id)
        if screen is None:
            return
        line = _composer_text(screen)
        if not line:  # '' = submitted, None = no composer (dialog?) — done
            return
        compare_len = min(len(line), len(prefix))
        if line[:compare_len] != prefix[:compare_len]:
            return  # not our text — never touch someone else's draft
        logger.warning(
            "Pasted text still sitting in the composer of hybrid session %s "
            "— pressing Enter again", session_id,
        )
        _run(["send-keys", "-t", target, "Enter"])


def paste_text(session_id: str, text: str, *, submit: bool = True) -> None:
    """Bracketed-paste ``text`` into the TUI composer, then press Enter.

    Verified: multiline pastes don't auto-submit, ``@`` mentions don't open
    the picker, pasted slash commands are interpreted on submit.
    """
    target = _pane_target(session_id)
    buf = "twicc-hybrid"
    r = _run(["load-buffer", "-b", buf, "-"], input_bytes=text.encode())
    if r.returncode != 0:
        raise RuntimeError(f"tmux load-buffer failed: {r.stderr.decode(errors='replace')}")
    r = _run(["paste-buffer", "-p", "-d", "-b", buf, "-t", target])
    if r.returncode != 0:
        raise RuntimeError(f"tmux paste-buffer failed: {r.stderr.decode(errors='replace')}")
    if submit:
        _run(["send-keys", "-t", target, "Enter"])
        _ensure_submitted(session_id, target, text)


def interrupt_turn(session_id: str) -> None:
    """Send Escape to the pane to interrupt an in-flight turn.

    The Claude CLI TUI treats Escape as "stop the current turn", which returns
    it to an empty composer — used before submitting ``/exit`` so the composer
    is free to accept it. Best-effort.
    """
    _run(["send-keys", "-t", _pane_target(session_id), "Escape"])


def kill_session(session_id: str) -> None:
    _run(["kill-session", "-t", "=" + hybrid_tmux_session_name(session_id)])


def list_hybrid_sessions() -> list[str]:
    """Return raw tmux session names with the hybrid prefix (boot adoption)."""
    result = _run(["list-sessions", "-F", "#{session_name}"])
    if result.returncode != 0:
        return []
    return [n for n in result.stdout.decode().splitlines() if n.startswith(HYBRID_SESSION_PREFIX)]
