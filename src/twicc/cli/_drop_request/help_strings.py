"""Shared help-string builders for the CLI commands.

Each builder takes a :class:`HelpContext` snapshot (already loaded by the
calling command at module import time) and returns the rendered string for
one ``typer.Option(..., help=...)`` entry. Pure data formatting — no Django,
no I/O. Designed so ``create-session`` and ``update-session settings`` share
the same wording without duplicating any of the rendering logic.

The set of builders covers every flag whose help text depends on the user's
current providers / presets / defaults. Static texts that several commands
share, or that are built from the inline attachment limits (``--no-expand``,
the prompt include hint, the ``--attach`` texts, the ``peer-send``
``--timeout``), are module-level constants, defined before the builder
functions. Any other static flag (the ``--timeout`` of the other commands,
...) keeps its inline help in the calling command — duplicating a one-liner
is cheaper than shipping a tiny helper per flag.
"""

from __future__ import annotations

from twicc.cli._drop_request.help_context import HelpContext
from twicc.core.services.attachments.inline import (
    INLINE_TOO_LARGE_HINT,
    PEER_SEND_TIMEOUT_WITH_FILES,
    PEER_TOO_LARGE_HINT,
)


_PROVIDER_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}


# Alias hints appended to the inline ``--effort`` / ``--permission-mode`` help in
# every settings command. Kept here (single source) so the three commands
# (create-session, update-session, update-sessions) stay in sync. The keys
# themselves live in each provider's ``AGENT_SETTINGS_ALIASES`` — this is only
# their human-facing summary, resolved to concrete values per provider.
EFFORT_ALIAS_HINT = " Aliases (resolved per provider): 'min', 'max'."
PERMISSION_ALIAS_HINT = (
    " Aliases (resolved per provider): 'strict'/'safe' (most-locked, "
    "non-interactive), 'open'/'full'/'yolo'/'bypass' (most permissive, "
    "non-interactive), 'auto' (balanced, interactive)."
)

# ``@@`` include-marker help, shared verbatim by the commands that expand
# markers (create-session, send-message, send-messages) so the wording never
# drifts between them. peer-send deliberately has neither.
PROMPT_INCLUDE_HINT = (
    " '@@<abs path>' include markers in the text are replaced by that file's "
    "content (see --no-expand for the full syntax)."
)
NO_EXPAND_HELP = (
    "Disable '@@' include expansion. By default an '@@/abs/path', '@@~/path' "
    "or '@@{/path with spaces}' marker in the text (or in the file it is read "
    "from) is replaced by that file's UTF-8 content, recursively (5 levels "
    "max). Inside a file, '@@./path' and '@@../path' resolve against that "
    "file's own directory (never the cwd), so only the entry point needs an "
    "absolute path; in inline text they are an error. A missing file expands "
    "to nothing — a marker alone on its line "
    "takes the whole line with it, so includes are optional; a directory, "
    "unreadable or non-UTF-8 file is an error; '@@@@' escapes a literal "
    "'@@'; the final text is capped at 500 KB. Over --remote, markers "
    "resolve on the client; use '@@remote:/abs/path' for a file on the "
    "remote server."
)

_ATTACH_FORMS = (
    "Each value is a local file path, a base64 data URI "
    "(data:<mime>;name=<percent-encoded file name>;base64,<data>; name= is optional), or, over "
    "--remote, remote:<absolute path> to read a file on the server."
)
_INLINE_LIMIT = (
    "Inline data is limited to 50 MB in total per command: data URIs, and local files sent over "
    "--remote (the forwarder turns them into data URIs; on a local command line, Linux caps one "
    "argument at 128 KiB). A file the CLI or the server reads from its own disk has no limit. "
)
ATTACH_HELP = "File to attach (repeatable), of any type. " + _ATTACH_FORMS + " " + _INLINE_LIMIT + INLINE_TOO_LARGE_HINT
ATTACH_EVERY_MESSAGE_HELP = (
    "File to attach to every message (repeatable), of any type; one copy is staged per recipient. "
    + _ATTACH_FORMS + " " + _INLINE_LIMIT + INLINE_TOO_LARGE_HINT
)
PEER_ATTACH_HELP = (
    "File to attach (repeatable), of any type. " + _ATTACH_FORMS + " Every file travels inline to "
    "the peer, paths included, so all files together are limited to 50 MB in total per message. "
    + PEER_TOO_LARGE_HINT
)
PEER_TIMEOUT_HELP = (
    f"Seconds to wait for the server's final status: 30 by default, {PEER_SEND_TIMEOUT_WITH_FILES} when "
    "the message carries files (a 50 MB send at 2 Mbit/s takes about 5 minutes). The request is not "
    "cancelled. Exit 5, exit 4, and exit 3 with code unreachable or send_failed mean the message may "
    "still have reached the peer: run `peer-message <message_id>` with the id of the output before you "
    "send again. If the backend restarts during the send, the message stays pending, and a pending "
    "status does not prove that the send completed. Agents: send files with the MCP peer_send tool, or "
    "give the shell call a timeout above 8 minutes. A CLI killed by its shell, or exit 7 over --remote, "
    "gives no id: check the Peers outbox in the TwiCC UI or report to your user; do not send again blindly."
)


def provider_label(name: str) -> str:
    return _PROVIDER_LABELS.get(name, name)


def tokens_to_alias(value: int) -> str:
    """Compact token count alias: ``1_000_000 → "1m"``, ``200_000 → "200k"``.

    Non-integer or non-round inputs fall through to ``str(value)``.
    """
    if isinstance(value, int):
        if value % 1_000_000 == 0:
            return f"{value // 1_000_000}m"
        if value % 1_000 == 0:
            return f"{value // 1_000}k"
    return str(value)


def format_tokens(value) -> str:
    """Render a token count for ``--help`` strings: quoted compact alias."""
    return repr(tokens_to_alias(value))


def default_suffix(ctx: HelpContext, field: str, formatter=repr) -> str:
    """Render ``Current default: provider=value, ...`` for a field.

    Returns an empty string when no provider declares a default for the
    field (e.g. the field is provider-disabled or the data dir is empty).
    ``formatter`` re-formats each value (e.g. tokens rendered as 200k/1m).
    """
    per_provider = ctx.field_defaults.get(field)
    if not per_provider:
        return ""
    parts = [
        f"{_PROVIDER_LABELS[provider]}: {formatter(value)}"
        for provider, value in per_provider.items()
    ]
    return " Current default: " + " | ".join(parts) + "."


def provider_help(ctx: HelpContext) -> str:
    base = (
        "Provider to use: 'claude_code' or 'codex'. Falls back to the default "
        "provider from settings when omitted."
    )
    if ctx.providers:
        items = []
        for name in ctx.providers:
            if name == ctx.default_provider:
                items.append(f"'{name}' (default)")
            else:
                items.append(f"'{name}'")
        base += f" Currently enabled: {', '.join(items)}."
    return base


def preset_help(ctx: HelpContext) -> str:
    base = (
        "Name of a saved settings preset for the chosen provider. "
        "Per-flag options below override preset values; unset fields fall "
        "back to the defaults from settings."
    )
    if ctx.presets:
        lines = []
        for provider_name, names in ctx.presets.items():
            if names:
                lines.append(
                    f"{_PROVIDER_LABELS[provider_name]}: "
                    + ", ".join(f"'{name}'" for name in names)
                )
            else:
                lines.append(f"{_PROVIDER_LABELS[provider_name]}: (none)")
        base += " Currently saved: " + " | ".join(lines) + "."
    return base


def model_help(ctx: HelpContext) -> str:
    base = "Model alias (provider-specific)."
    chunks = []
    for provider_name, aliases in ctx.model_aliases.items():
        if not aliases:
            continue
        rendered = []
        for ma in aliases:
            if ma.is_latest:
                rendered.append(f"'{ma.alias}' (latest: {ma.version})")
            else:
                rendered.append(f"'{ma.alias}'")
        chunks.append(f"{provider_label(provider_name)}: {', '.join(rendered)}")
    if chunks:
        base += " " + ". ".join(chunks) + "."
    base += (
        " Aliases (resolved per provider): 'max'/'strongest' (top family), "
        "'min'/'fastest'/'cheapest' (lightest family); always the latest version."
    )
    base += default_suffix(ctx, "selected_model")
    return base


def context_max_help(ctx: HelpContext) -> str:
    base = (
        "Max context window (accepted forms: '200k', '1m', '272k'). "
        "Claude Code: '200k' or '1m' (1m requires a 1m-capable model; "
        "otherwise silently capped to 200k) | Codex: '272k'."
    )
    base += " Aliases (resolved per provider): 'min', 'max'."
    base += default_suffix(ctx, "context_max", formatter=format_tokens)
    return base


def parse_context_max(value: str | None) -> int | None:
    """Parse ``--context-max`` accepting ``1m``/``200k``/``272k``/plain int."""
    if value is None:
        return None
    s = value.strip().lower()
    if not s:
        return None
    multiplier = 1
    if s.endswith("m"):
        multiplier = 1_000_000
        s = s[:-1]
    elif s.endswith("k"):
        multiplier = 1_000
        s = s[:-1]
    try:
        n = int(s)
    except ValueError:
        raise ValueError(
            f"invalid --context-max {value!r}; expected forms like 200k, 1m, "
            f"or a plain integer."
        )
    return n * multiplier
