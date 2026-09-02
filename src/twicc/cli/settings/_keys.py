"""Pure helpers for the generic `twicc settings set/unset/get` backbone."""
from __future__ import annotations

# Keys excluded from CLI mutation (visual-only or internal).
EXCLUDED_KEYS = frozenset({"waTheme", "waBrand", "defaultLayoutId", "_version"})
# Keys owned by dedicated sub-commands.
PROVIDER_KEYS = frozenset({"defaultProvider", "disabledProviders",
                           "orchestrationDisabledProviders"})
NOTIFICATION_KEYS = frozenset({"externalNotificationTargets"})


# One-line descriptions for the generic, directly-settable keys — the surface
# of `twicc settings set/unset` (and the only keys `twicc settings`/`get`
# expose). Surfaced in the `--help` of those commands via
# :func:`format_settable_keys_help`. Django-free so `--help` stays fast.
#
# INVARIANT: the key set here must equal the set of keys that classify as
# "generic" (every cross-provider key in ``SYNCED_SETTINGS_DEFAULTS`` that is
# not excluded / provider / notifications). A new generic synced setting MUST
# get an entry here — ``tests/test_settings_cli.py`` enforces this so the help
# never drifts out of sync.
GENERIC_KEY_DESCRIPTIONS: dict[str, str] = {
    "titleGenerationEnabled": "Generate a session title from the first user message.",
    "titleAutoApply": "Apply generated titles automatically (vs. only suggesting them).",
    "titleSuggestionModel": "Model used for title suggestions: provider, haiku, or luna.",
    "titleSystemPrompt": "System prompt used to generate titles ('{text}' = the message); unset restores the default.",
    "autoUnpinOnArchive": "Unpin a session automatically when it is archived.",
    "worktreeDirectoryTemplate": "Template for the base dir of new git worktrees; placeholders {git_root} {project_name} {project_basedir} (empty = none).",
    "terminalUseTmux": "Wrap terminal sessions in tmux by default.",
    "terminalTmuxConfigPath": "Path to a custom tmux config for terminal sessions (empty = default).",
    "publicBaseUrl": "External address where you reach TwiCC, appended as a deep link to external notifications (empty = no link).",
    "shareBaseUrl": "Dedicated share host — a hostname distinct from this app, pointing at the same port — where read-only /share/ links are served (empty = sharing disabled).",
    "peerBaseUrl": "Public base URL advertised to peer instances for cross-instance messaging; may be the working origin, but must be reachable machine-to-machine — no tunnel-level auth gate (empty = peer messaging disabled).",
    "peerDisplayName": "Display name sent to peer instances in pairing handshakes (empty = the hostname of peerBaseUrl).",
    "notifyOnExtraUsageStart": "Master switch for the 'extra usage started' alert (in-app + external push).",
    "telemetryEnabled": "Send anonymous usage statistics (no content, ever).",
    "telemetryNoticeSeen": "One-time telemetry notice acknowledged.",
    "allowAgentSessionShares": "Let agents create session shares in their own spawn subtree, revoke any session share, and read every session share URL.",
    "allowAgentArtifactShares": "Let agents create artifact shares in their own spawn subtree, revoke any artifact share, and read every artifact share URL.",
}


class ValueParseError(ValueError):
    pass


def _provider_prefixed(key: str) -> bool:
    return key.startswith(("claudeCode", "codex"))


def classify_key(key: str) -> str:
    """One of: generic | provider | notifications | excluded | unknown."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS
    if key in EXCLUDED_KEYS:
        return "excluded"
    if key in PROVIDER_KEYS or _provider_prefixed(key):
        return "provider"
    if key in NOTIFICATION_KEYS:
        return "notifications"
    if key in SYNCED_SETTINGS_DEFAULTS:
        return "generic"
    return "unknown"


_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def parse_value(key: str, raw: str):
    """Parse `raw` into the type of the key's default."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS
    default = SYNCED_SETTINGS_DEFAULTS[key]
    if isinstance(default, bool):
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueParseError(f"{key} expects a boolean (true/false), got {raw!r}.")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            raise ValueParseError(f"{key} expects an integer, got {raw!r}.")
    return raw


def format_settable_keys_help() -> str:
    """Render the settable-keys section appended to `get`/`set`/`unset` --help.

    Lists every generic key with its one-line description, then points to the
    dedicated commands for the keys this backbone does not own. Pure and
    Django-free so it can be built at import time without slowing `--help`.
    """
    lines = ["Settable keys (run `twicc info settings` for types & defaults):", ""]
    lines += [f"- {key} — {desc}" for key, desc in GENERIC_KEY_DESCRIPTIONS.items()]
    lines += [
        "",
        ("Other keys are read/written through their own commands: provider keys "
        "(claudeCode*/codex*, defaultProvider, disabledProviders, "
        "orchestrationDisabledProviders) via `twicc settings provider`; "
        "notification keys (externalNotificationTargets) via "
        "`twicc settings notifications`."),
    ]
    return "\n".join(lines)
