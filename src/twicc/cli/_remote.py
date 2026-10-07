"""Client-side ``--remote`` forwarder — pre-flight half.

This module forwards a local ``twicc`` command to a *remote* TwiCC's ``/rpc/``
HTTP API. It is the inverse of the server's generator: it reuses the very same
``build_registry()`` (the canonical command↔URL mapping) so what maps
command→URL on the server maps argv→URL on the client.

This file implements the **pre-flight** half (path resolution + local
rejections), **attachment / prompt inlining**, and the **network** half — the
HTTP call to ``/rpc/`` and the envelope→exit mapping (:func:`forward`). The
entry-point wiring (intercepting ``--remote`` before Typer) lands in a later
task (R5). The public surface is intentionally small:

- :class:`RemoteUsageError` / :class:`RemoteTransportError` — error types with
  reserved exit codes, so callers can map a failure to a process exit code;
- :class:`Resolved` — the result of resolving an argv to a registry route;
- :func:`resolve_command` — argv → :class:`Resolved` (or a usage error);
- :func:`reject_host_bound` — reject ``self`` / ``parent`` where they reference
  the *local* session and have no meaning on a remote;
- :func:`inline_attachments` — rewrite each ``--attach <local path>`` into a
  ``data:`` URI so the server can decode it without a shared filesystem;
- :func:`inline_prompt` — read a file-path prompt / ``--message`` into its text
  client-side (the symmetric counterpart of :func:`inline_attachments`);
- :func:`forward` — resolve, pre-flight, POST to ``<base>/rpc/<path>`` and map
  the ``{exit_code, result, error}`` envelope to local stdout/stderr/exit;
- :data:`HOST_BOUND_PARAMS` — the audited set of param names that accept the
  ``self`` / ``parent`` keywords.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import sys
from typing import NamedTuple
from urllib.parse import quote

import click
import httpx
import orjson
from click.core import ParameterSource

from twicc.cli._drop_request.prompt import (
    PromptError,
    expand_prompt_includes,
    resolve_prompt,
)
from twicc.cli._drop_request.remote_scheme import has_remote_scheme, remote_scheme_path
from twicc.cli._local_only import LOCAL_ONLY_COMMANDS
from twicc.cli._session_group import SessionGroup
from twicc.core.services.attachments import inline
from twicc.rpc.generator import CommandSpec, build_registry
from twicc.rpc.invoker import get_command
from twicc.rpc.schema import ParamSpec


class RemoteUsageError(Exception):
    """Client-side misuse of ``--remote`` (no HTTP attempted).

    Raised for a local-only command, an unknown command, or a ``self`` /
    ``parent`` reference that only makes sense on the local host. The caller
    prints ``twicc: <message>`` to stderr and exits with :attr:`exit_code`.
    """

    exit_code = 2


class RemoteTransportError(Exception):
    """Transport / remote-layer failure (connect, DNS, timeout, non-2xx, …).

    Defined here for the network half (R4) to raise; the pre-flight code never
    raises it. The caller prints ``twicc: <message>`` to stderr and exits with
    :attr:`exit_code` — a code reserved for transport failures, distinct from
    the command vocabulary (``0/1/2/5/64``).
    """

    exit_code = 7


class Resolved(NamedTuple):
    """A resolved remote command: its registry path, spec, and bound params."""

    path: str                # registry key, e.g. "session/content"
    spec: CommandSpec        # the matching CommandSpec from build_registry()
    params: dict             # merged Click params across every navigated level (defaults included)
    explicit: frozenset[str] = frozenset()  # params of the leaf given on the command line


# Param names that accept the host-bound ``self`` / ``parent`` keywords.
#
# Audited from the CLI command signatures:
# - Filiation filter options ``--spawned-by`` / ``--spawn-tree`` /
#   ``--descendants`` / ``--siblings`` (Click param names ``spawned_by`` /
#   ``spawn_tree`` / ``descendants`` / ``siblings``) — interpreted by
#   ``resolve_spawned_by_filter`` / ``resolve_spawn_tree_filter`` /
#   ``resolve_descendants_filter`` / ``resolve_siblings_filter`` in
#   ``cli/_drop_request/whoami.py``. Present on ``sessions``, ``search``,
#   and ``send-messages`` (``spawned_by`` / ``descendants`` / ``siblings``). ``spawn_tree`` and
#   ``siblings`` reject ``parent`` themselves, but they still accept ``self`` —
#   so both belong here. (On ``topology`` ``siblings`` is a boolean flag, never
#   a ``self`` / ``parent`` string, so listing it here can't misfire there.)
# - Session-id positionals (Click param name ``session_id``). ``send-message``,
#   ``update-session``, ``topology`` and ``session`` (with its subcommands)
#   truly RESOLVE ``self`` / ``parent`` from the local session, so they must be
#   rejected over --remote.
# - Variadic session-id positionals: ``update-sessions`` (``session_ids`` —
#   every batch sub-command) and ``send-messages`` (``session_ids``). Like ``update-session``, both
#   ``update-sessions`` and ``send-messages`` truly RESOLVE ``self`` in their
#   explicit ids, so rejecting it over --remote is meaningful, not just
#   conservative. The batch reader ``sessions get`` uses
#   plain ``session_ids`` too; including the name stays harmless there (they
#   don't special-case ``self`` / ``parent``, so the value would never
#   legitimately be either).
# - ``session`` is the share list's ``--session`` filter (resolves both keywords
#   since the agent-sharing lot).
#
# Only these specific params are inspected — never a blind argv scan — so a
# free-text value (e.g. a message body that happens to be "self") is untouched.
HOST_BOUND_PARAMS: frozenset[str] = frozenset(
    {
        # filiation filter options
        "spawned_by",
        "spawn_tree",
        "descendants",
        "siblings",
        # session-id scalar options
        "session",
        # session-id positionals (scalar)
        "session_id",
        # session-id positionals (variadic / mixed)
        "session_ids",
        "items",
    }
)

_HOST_BOUND_KEYWORDS = frozenset({"self", "parent"})

_SESSION_GROUP_FLAG_MISUSE = "--slim / --full apply to `session <id>` alone, not to `{sub}`."


def _make_context(cmd: click.Command, args: list[str], parent: click.Context | None) -> click.Context:
    """Parse ``args`` for ``cmd`` in resilient mode (missing args don't raise)."""
    return cmd.make_context(cmd.name or "twicc", list(args), parent=parent, resilient_parsing=True)


def _remaining_tokens(ctx: click.Context) -> list[str]:
    """Tokens left for a group after parsing its own options/arguments.

    ``protected_args`` holds the (deprecated, Click < 9) reserved subcommand
    token; ``args`` holds the rest. Concatenating them gives the tokens that
    the group must resolve into ``<subcommand> <rest…>``.
    """
    return list(ctx.protected_args) + list(ctx.args)


def resolve_command(argv: list[str]) -> Resolved:
    """Resolve a CLI ``argv`` to a remote route (path + spec + bound params).

    Navigates the Click tree from :func:`get_command` so that group arguments
    interleaved between subcommand tokens (e.g. ``session <id> content`` →
    ``session/content``) are consumed correctly and the resulting path equals a
    :func:`build_registry` key. The merged Click params (across every level)
    are returned so :func:`reject_host_bound` can inspect the bound values.

    Raises :class:`RemoteUsageError` for an empty argv, a local-only root, an
    unknown command, or a genuinely malformed command (no raw Click traceback
    leaks out).
    """
    if not argv:
        raise RemoteUsageError("no command given; nothing to forward to --remote")

    root_token = argv[0]
    if root_token in LOCAL_ONLY_COMMANDS:
        # Local-only roots are absent from the registry; the explicit check
        # produces a helpful message instead of a generic "unknown command".
        raise RemoteUsageError(
            f"'{root_token}' is a local-only command; not available over --remote"
        )

    try:
        path, params, explicit = _navigate(argv)
    except RemoteUsageError:
        raise
    except Exception as exc:  # surface any Click parse failure as a usage error
        raise RemoteUsageError(
            f"could not parse command: {' '.join(argv)} ({type(exc).__name__}: {exc})"
        ) from exc

    registry = build_registry()
    spec = registry.get(path)
    if spec is None:
        raise RemoteUsageError(f"unknown command: {' '.join(argv)}")

    return Resolved(path=path, spec=spec, params=params, explicit=explicit)


def _navigate(argv: list[str]) -> tuple[str, dict, frozenset[str]]:
    """Walk the Click tree, returning the registry path, merged params and the leaf's explicit params.

    Each level is parsed with ``make_context`` so a group consumes its own
    options/arguments before its subcommand token is resolved — this is what
    keeps interleaved group arguments (the ``<id>`` in ``session <id> content``)
    out of the path.

    A group that carries a *required positional* (``session`` / ``process`` /
    ``update-session``) takes the id *before* its subcommand — the canonical
    ``<group> <id> <subcommand>`` order, which navigates cleanly (the id binds to
    the group's positional, then the subcommand resolves). The wrong order
    (``<group> <subcommand> <id>``) is rejected as a usage error here, exactly as
    the local CLI and the remote server reject it — the forwarder mirrors CLI
    semantics rather than silently "fixing" the order.
    """
    root = get_command()
    cmd: click.Command = root
    args = list(argv)
    parent: click.Context | None = None
    path_tokens: list[str] = []
    merged: dict = {}
    explicit: frozenset[str] = frozenset()

    while True:
        ctx = _make_context(cmd, args, parent)
        if not isinstance(cmd, click.Group):
            merged.update(ctx.params)
            explicit = frozenset(
                name for name in ctx.params if ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE
            )
            break

        remaining = _remaining_tokens(ctx)
        if not remaining:
            # Group invoked without a subcommand: it is itself a route
            # (invoke_without_command). Its own params are the bound values.
            merged.update(ctx.params)
            explicit = frozenset(
                name for name in ctx.params if ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE
            )
            break

        if isinstance(cmd, SessionGroup):
            # The refusal only when a registered subcommand follows, as the
            # local SessionGroup does (step 5); anything else falls through to
            # the generic "unknown command" below, as Click reports it locally
            # (step 6).
            run_end = 0
            while run_end < len(remaining) and remaining[run_end] in ("--slim", "--full"):
                run_end += 1
            if run_end and run_end < len(remaining) and remaining[run_end] in cmd.commands:
                # `session X --full agents`: resilient mode left the run in place.
                raise RemoteUsageError(_SESSION_GROUP_FLAG_MISUSE.format(sub=remaining[run_end]))
            if (ctx.params.get("slim") or ctx.params.get("full")) and remaining[0] in cmd.commands:
                # `session --full X agents`: the group took the flag, a subcommand follows.
                raise RemoteUsageError(_SESSION_GROUP_FLAG_MISUSE.format(sub=remaining[0]))

        name, sub, rest = cmd.resolve_command(ctx, remaining)
        if sub is None:
            # In resilient mode resolve_command returns sub=None for an unknown
            # token instead of raising. This also covers a subcommand placed
            # before a group's required positional (wrong order, e.g.
            # ``session wait-reply <id>``): the positional swallows the subcommand and
            # the next token is not a command — rejected here exactly as the
            # local CLI and the remote server would reject it.
            raise RemoteUsageError(f"unknown command: {' '.join(argv)}")

        merged.update(ctx.params)
        path_tokens.append(name)
        cmd = sub
        args = rest
        parent = ctx

    return "/".join(path_tokens), merged, explicit


def reject_host_bound(resolved: Resolved) -> None:
    """Reject ``self`` / ``parent`` where they reference the local session.

    Inspects only the audited :data:`HOST_BOUND_PARAMS` of the resolved command
    (handling scalars and tuples/lists). A free-text value that merely equals
    ``self`` / ``parent`` (e.g. a message body) is never affected.

    Raises :class:`RemoteUsageError` if any host-bound param is set to
    ``self`` / ``parent``.
    """
    for name in HOST_BOUND_PARAMS:
        if name not in resolved.params:
            continue
        value = resolved.params[name]
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if item in _HOST_BOUND_KEYWORDS:
                raise RemoteUsageError(
                    "self/parent reference the local session and have no "
                    "meaning over --remote; pass an explicit session id"
                )


# Click param name of the repeatable ``--attach`` option, present on both
# ``create-session`` and ``send-message`` (audited from their command
# signatures: option string ``--attach``, no short alias). The forwarder never
# hardcodes the option string — it reads it from the resolved spec's ParamSpec,
# so a rename of the option stays correct.
_ATTACH_PARAM_NAME = "attach"


def _attach_option_strings(resolved: Resolved) -> list[str] | None:
    """Return the option string(s) of the attach option, or None if absent.

    Looks up the audited :data:`_ATTACH_PARAM_NAME` among the resolved command's
    options. ``ParamSpec`` only exposes the primary (``opt``) and the off-form
    secondary (``secondary_opt``) — there is no separate "short alias" field, so
    a short alias would only be handled if it happened to be the primary opt.
    For ``--attach`` the primary is the long form and there is no short alias.
    """
    for opt in resolved.spec.options:
        if opt.name == _ATTACH_PARAM_NAME:
            return [o for o in (opt.opt, opt.secondary_opt) if o]
    return None


def _resolve_remote_path(value: str) -> str | None:
    """Return the bare absolute path of a ``remote:`` value, or None if not one.

    A ``remote:`` value is forwarded as its bare path so the server reads it from
    its own filesystem (instead of the client inlining the local file). The path
    must be absolute — the server has no caller working directory to resolve a
    relative one against.

    Raises :class:`RemoteUsageError` if the path is not absolute (a client-side
    error — no HTTP is attempted).
    """
    if not has_remote_scheme(value):
        return None
    path = remote_scheme_path(value)
    if not path.startswith("/"):
        raise RemoteUsageError(
            f"remote: requires an absolute path (e.g. remote:/abs/path), got {value!r}"
        )
    return path


def _inline_one(value: str) -> str:
    """Rewrite a single attach value for forwarding.

    A value already in ``data:`` form is returned unchanged, and a ``remote:`` value is
    reduced to its bare absolute path so the *server* reads it (see
    :func:`_resolve_remote_path`). Otherwise the value is a local file path (relative paths
    are read against the client's cwd): its bytes become
    ``data:<mime>;name=<percent-encoded base name>;base64,<payload>``, so the server stages
    the file under its own name. The MIME is guessed from the name (``application/octet-stream``
    otherwise) and is only a label: the server detects the kind from the bytes.

    Raises :class:`RemoteUsageError` if a ``remote:`` path is not absolute, or a local file is
    missing or unreadable (a client-side error — no HTTP attempted).
    """
    if value.startswith("data:"):
        return value
    remote_path = _resolve_remote_path(value)
    if remote_path is not None:
        return remote_path
    try:
        with open(value, "rb") as f:
            data = f.read()
    except OSError:
        raise RemoteUsageError(f"attachment not found: {value}")
    # A name read from the filesystem may hold undecodable bytes (surrogate escapes).
    name = os.fsencode(os.path.basename(value)).decode("utf-8", "replace")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    payload = base64.b64encode(data).decode("ascii")
    return f"data:{mime};name={quote(name, safe='')};base64,{payload}"


def _rewrite_option_values(
    argv: list[str], option_strings: list[str], transform
) -> list[str]:
    """Return a copy of ``argv`` with each ``<option> VALUE`` value transformed.

    Shared machinery for the option-borne inliners (``--attach`` files,
    ``--message`` prompts). ``transform`` maps an option's raw value to its
    replacement (it is responsible for leaving values it does not handle
    unchanged). Both token forms are handled: ``<option> VALUE`` (two tokens)
    and ``<option>=VALUE`` (one token). A dangling option with no value (last
    token) is left untouched — Click would reject it anyway; the forwarder never
    invents a value. The original ``argv`` is never mutated — a fresh list is
    returned, preserving every other token and the order. With no option
    strings, ``argv`` is returned unchanged.
    """
    if not option_strings:
        return list(argv)

    eq_prefixes = tuple(f"{opt}=" for opt in option_strings)

    out: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token in option_strings:
            # Two-token form: "<option>" "VALUE". Keep the option token, then
            # transform the next token (the value).
            out.append(token)
            if i + 1 < n:
                out.append(transform(argv[i + 1]))
                i += 2
            else:
                i += 1
            continue
        matched_prefix = next((p for p in eq_prefixes if token.startswith(p)), None)
        if matched_prefix is not None:
            # One-token form: "<option>=VALUE". Transform only the value part,
            # preserving the exact "<option>=" prefix that was used.
            value = token[len(matched_prefix):]
            out.append(f"{matched_prefix}{transform(value)}")
            i += 1
            continue
        out.append(token)
        i += 1
    return out


def inline_attachments(argv: list[str], resolved: Resolved) -> list[str]:
    """Return a copy of ``argv`` with each ``--attach <local path>`` inlined.

    Over ``--remote``, the server only sees the forwarded argv — it has no
    access to the client's filesystem. So every ``--attach`` value that names a
    *local* file is rewritten to a ``data:<mime>;name=<percent-encoded base name>;base64,<payload>``
    URI (see :func:`_inline_one`): the server stages the file under its own name, and the MIME,
    guessed from the name, is only a label (the server detects the kind from the bytes). A value
    already in ``data:`` form is left as is; a ``remote:`` value becomes its bare server path.
    Commands without an attach option return ``argv`` unchanged.

    The size pre-check runs before, in :func:`check_inline_size`.

    Raises :class:`RemoteUsageError` if an ``--attach`` file is missing or
    unreadable (a client-side error — no HTTP is attempted).
    """
    return _rewrite_option_values(argv, _attach_option_strings(resolved) or [], _inline_one)


# The registry path whose files all travel inline to a peer, ``remote:`` files included.
_PEER_SEND_PATH = "peer-send"


def _too_large_hint(resolved: Resolved) -> str:
    return inline.PEER_TOO_LARGE_HINT if resolved.path == _PEER_SEND_PATH else inline.INLINE_TOO_LARGE_HINT


def check_inline_size(resolved: Resolved) -> None:
    """Refuse more than 50 MB of inline data before reading any file (phase 2 design §4.4.3).

    Counts the size of every local ``--attach`` file and the decoded size of every data URI the
    user gave. A ``remote:`` value is a server path: not counted here (for ``peer-send`` the
    server-side command counts it). A missing local file is left to :func:`_inline_one`.
    Raises :class:`RemoteUsageError` (exit 2) with the limit and the hint of the command.
    """
    total = 0
    for value in resolved.params.get(_ATTACH_PARAM_NAME) or ():
        if value.startswith("data:"):
            total += inline.data_uri_size(value) or 0
        elif not has_remote_scheme(value):
            try:
                total += os.path.getsize(value)
            except OSError:
                continue
    if total > inline.INLINE_MAX_BYTES:
        raise RemoteUsageError(
            "--attach: " + inline.too_large_message(total, inline.INLINE_MAX_BYTES, _too_large_hint(resolved))
        )


def apply_peer_send_timeout(argv: list[str], resolved: Resolved) -> tuple[list[str], Resolved]:
    """Give a ``peer-send`` with files and no explicit ``--timeout`` the long wait (§4.4.3).

    The server-side command waits ``PEER_SEND_TIMEOUT_WITH_FILES`` for a message with files;
    passing it explicitly lets the read timeout of this call cover that wait, without
    measuring any file (``remote:`` files included).
    """
    if (
        resolved.path != _PEER_SEND_PATH
        or not resolved.params.get(_ATTACH_PARAM_NAME)
        or "timeout" in resolved.explicit
    ):
        return list(argv), resolved
    timeout = inline.PEER_SEND_TIMEOUT_WITH_FILES
    return (
        [argv[0], f"--timeout={timeout}", *argv[1:]],
        resolved._replace(params={**resolved.params, "timeout": timeout}),
    )


# Click param names that carry a file-resolvable prompt / message body, audited
# from the command signatures: ``create-session`` and ``send-message`` take it
# as the positional ``prompt`` argument; ``send-messages`` takes it as the
# ``--message`` option. No other CLI command defines a param with these names
# (the ``message`` field of the ``ValidationError`` NamedTuple in ``validation.py``
# is not a Click param, so it never enters a CommandSpec).
# Only these specific params are inlined — a free-text value on any other
# command is never read from disk.
_PROMPT_PARAM_NAMES: frozenset[str] = frozenset({"prompt", "message"})

# Registry paths whose prompt/message never goes through ``@@`` include
# expansion, mirroring the server-side ``resolve_prompt(expand=False)`` call
# sites: a peer message is authored verbatim by the sender. File inlining
# still applies — only the marker expansion is skipped.
_NO_EXPAND_PATHS: frozenset[str] = frozenset({"peer-send"})


def _inline_prompt_value(value: str, *, expand: bool) -> str | None:
    """Return the forwarded form of a prompt/message value, or None to leave it as-is.

    Over ``--remote`` the server only sees the forwarded argv and cannot read the
    client's filesystem, so a file-borne prompt/message is resolved here:

    - ``remote:<abs path>`` → its bare absolute path, so the *server* reads it
      (see :func:`_resolve_remote_path`).
    - an existing local file → its UTF-8 text, read client-side (absolute or
      cwd-relative paths resolve).
    - anything else (inline text, or an absolute path that exists only on the
      server) → ``None``, forwarded unchanged for the server to handle as
      before — unless ``expand`` is on and the text carries a ``@@`` sequence.

    With ``expand`` on, the resulting text then runs the client half of the
    ``@@`` include expansion (``expand_prompt_includes(forward=True)``): local
    markers resolve against the client filesystem, ``remote:`` markers are
    rewritten for the server, and literal ``@@`` are re-escaped so the server's
    own pass reproduces the text exactly.

    Raises :class:`RemoteUsageError` if a ``remote:`` path is not absolute, or a
    local prompt file / include is unreadable / non-UTF-8 / empty / oversized
    (a client-side error — no HTTP is attempted).
    """
    remote_path = _resolve_remote_path(value)
    if remote_path is not None:
        return remote_path
    base: str | None = None
    if os.path.isfile(value):
        try:
            text = resolve_prompt(value, expand=False)
        except PromptError as exc:
            raise RemoteUsageError(str(exc))
        # Same base as a local run: the prompt file's own directory anchors
        # its ``./``/``../`` markers, resolved here on the client.
        base = os.path.dirname(os.path.abspath(value))
    elif expand and "@@" in value:
        text = value
    else:
        return None
    if not expand:
        return text
    try:
        return expand_prompt_includes(text, forward=True, base=base)
    except PromptError as exc:
        raise RemoteUsageError(str(exc))


def _inline_message_value(value: str, *, expand: bool) -> str:
    """Transform for an option-borne message: file/remote/markers → resolved, else as-is."""
    new = _inline_prompt_value(value, expand=expand)
    return value if new is None else new


def _positional_token_indices(
    argv: list[str], value_option_strings: frozenset[str]
) -> list[int]:
    """Return the indices in ``argv`` of positional tokens, in order.

    Option tokens, and the values consumed by value-taking options, are skipped
    so that only genuine positionals (command tokens, group args, leaf args) are
    counted. ``--`` ends option processing — everything after it is positional.
    An unknown option (absent from ``value_option_strings``) is assumed to take
    no value; if that guess is wrong, the caller's defensive value check catches
    it and declines to rewrite, so a miscount never corrupts the argv.
    """
    indices: list[int] = []
    i = 0
    n = len(argv)
    end_of_options = False
    while i < n:
        token = argv[i]
        if not end_of_options and token == "--":
            end_of_options = True
            i += 1
            continue
        if not end_of_options and len(token) > 1 and token.startswith("-"):
            if "=" in token:
                i += 1
            elif token in value_option_strings:
                i += 2
            else:
                i += 1
            continue
        indices.append(i)
        i += 1
    return indices


def _inline_positional_prompt(
    argv: list[str], resolved: Resolved, prompt_param: ParamSpec, *, expand: bool
) -> list[str]:
    """Rewrite a positional ``prompt`` token (a local file or ``remote:`` path).

    The prompt's ordinal among *all* positionals (every level's token + every
    ancestor level's arguments precede the leaf arguments, in argv order) is
    derived from the resolved spec, then mapped to a concrete argv index by
    walking the tokens. The rewrite is applied only when the located token still
    equals the value Click bound to the prompt param (a defensive guard against
    any walk/parse disagreement) and :func:`_inline_prompt_value` resolves it
    (a local file → its text, or ``remote:`` → its bare server path). A
    variadic/multiple positional at or before the prompt makes the
    token→positional mapping ambiguous, so such a command is left untouched.
    """
    chain = resolved.spec.chain
    if not chain:
        return list(argv)
    leaf_args = chain[-1].arguments

    leaf_ordinal = next(
        (k for k, a in enumerate(leaf_args) if a.name == prompt_param.name), None
    )
    if leaf_ordinal is None:
        return list(argv)
    if any(a.variadic or a.multiple for a in leaf_args[: leaf_ordinal + 1]):
        return list(argv)
    if any(a.variadic or a.multiple for lvl in chain[:-1] for a in lvl.arguments):
        return list(argv)

    total_tokens = sum(1 for lvl in chain if lvl.token is not None)
    ancestor_args = sum(len(lvl.arguments) for lvl in chain[:-1])
    target = total_tokens + ancestor_args + leaf_ordinal

    value_option_strings = frozenset(
        o.opt for o in resolved.spec.options if o.opt and not o.is_flag
    )
    positions = _positional_token_indices(argv, value_option_strings)
    if target >= len(positions):
        return list(argv)
    idx = positions[target]

    # Defensive: only rewrite if the located token is exactly what Click bound
    # to the prompt param. A mismatch means our positional walk and the parser
    # disagree, so we must not touch the argv.
    if argv[idx] != resolved.params.get(prompt_param.name):
        return list(argv)

    text = _inline_prompt_value(argv[idx], expand=expand)
    if text is None:
        return list(argv)
    out = list(argv)
    out[idx] = text
    return out


def inline_prompt(argv: list[str], resolved: Resolved) -> list[str]:
    """Return a copy of ``argv`` with a file-path prompt / message inlined to text.

    The symmetric counterpart to :func:`inline_attachments` for the message
    body: over ``--remote`` the server cannot read the client's filesystem, so a
    prompt/message given as a *local* file path is read here and forwarded as
    text, while a ``remote:<abs path>`` value is reduced to its bare path so the
    server reads it. ``create-session`` / ``send-message`` carry it as the
    positional ``prompt`` argument; ``send-messages`` as the ``--message``
    option — both are handled. A value that is neither (inline text, or a path
    meant to resolve on the server) is forwarded unchanged, and a command with
    no prompt param returns ``argv`` unchanged.

    Raises :class:`RemoteUsageError` if a ``remote:`` path is not absolute, or a
    prompt file exists but is unreadable / non-UTF-8 / empty (a client-side error
    — no HTTP is attempted).
    """
    expand = (
        resolved.path not in _NO_EXPAND_PATHS
        and not resolved.params.get("no_expand")
    )
    leaf_args = resolved.spec.chain[-1].arguments if resolved.spec.chain else []
    pos_param = next((a for a in leaf_args if a.name in _PROMPT_PARAM_NAMES), None)
    if pos_param is not None:
        return _inline_positional_prompt(argv, resolved, pos_param, expand=expand)
    opt_param = next(
        (o for o in resolved.spec.options if o.name in _PROMPT_PARAM_NAMES), None
    )
    if opt_param is not None:
        option_strings = [s for s in (opt_param.opt, opt_param.secondary_opt) if s]
        return _rewrite_option_values(
            argv,
            option_strings,
            lambda value: _inline_message_value(value, expand=expand),
        )
    return list(argv)


# The answer-waits hold the response open up to their ``--wait-timeout`` (a
# 300 s default), not a ``--timeout``: without their own entry they would get
# :data:`_DEFAULT_TIMEOUT` and be cut by the client long before the server
# answers.
_WAIT_REPLY_PATHS: frozenset[str] = frozenset({"session/wait-reply", "sessions/wait-reply"})

# Read-timeout margin (seconds) added on top of a command's own wait (its
# ``--wait-timeout`` or its ``--timeout``) so the local read does not race the
# server's own deadline.
_WAIT_TIMEOUT_MARGIN = 15.0

# Default read timeout (seconds) for read commands (no ``--timeout``). A
# drop-and-poll command waits its own ``--timeout`` plus the margin instead.
_DEFAULT_TIMEOUT = 30.0

# Mirror of ``create_session.command.DEFAULT_WAIT_TIMEOUT_SECONDS``, used when
# ``--wait-reply`` is passed without an explicit ``--wait-timeout``. Duplicated
# rather than imported: this module is on the ``--help`` path for every command
# and must not pull a command module in.
_DEFAULT_WAIT_TIMEOUT = 300.0

# Connection-establishment timeout (seconds). Kept short and constant: it bounds
# only the TCP/TLS handshake, never the server's processing time. The per-request
# read timeout (above) is what accommodates a long-poll ``wait``.
_CONNECT_TIMEOUT = 10.0


def _endpoint_url(base: str, path: str) -> str:
    """Join the remote base URL and the registry ``path`` into a ``/rpc/`` URL.

    The base may or may not carry a trailing slash (``http://box:3501`` or
    ``http://box:3501/``); either way the result is ``<base>/rpc/<path>`` with a
    single separator and no doubled ``/rpc/``. ``path`` is a clean registry key
    (e.g. ``session/content``), never user free-text, so no escaping is needed.
    """
    return f"{base.rstrip('/')}/rpc/{path}"


def _request_timeout(resolved: Resolved) -> httpx.Timeout:
    """Pick the httpx timeout for this command.

    The two answer-waits (``session wait-reply``, ``sessions wait-reply``) hold
    the response up to their ``--wait-timeout``; the local **read** timeout is
    therefore set to that value plus :data:`_WAIT_TIMEOUT_MARGIN` so the client
    never gives up before the server answers.

    ``--wait-reply`` gets the same treatment on top of its own ``--timeout``:
    the server holds the connection for the drop request *and* the wait.
    Every other drop-and-poll command (one with a ``--timeout``) waits up to its
    effective ``--timeout``: the server now stages inline data and the send may
    wait in the session's lane before its final status, so the read timeout is
    that value plus the margin. Read commands use :data:`_DEFAULT_TIMEOUT`.
    The connect timeout is always the short, constant :data:`_CONNECT_TIMEOUT`.
    """
    read = _DEFAULT_TIMEOUT
    if resolved.path in _WAIT_REPLY_PATHS:
        wait_timeout = resolved.params.get("wait_timeout")
        if not isinstance(wait_timeout, (int, float)) or wait_timeout <= 0:
            wait_timeout = _DEFAULT_WAIT_TIMEOUT
        read = float(wait_timeout) + _WAIT_TIMEOUT_MARGIN
    elif resolved.params.get("wait_reply"):
        # ``--wait-reply`` turns an ordinary drop-and-poll command into a long
        # one: the server holds the connection for the drop request *and* the
        # wait that follows. Without this the client gives up at
        # ``_DEFAULT_TIMEOUT`` while the session it just created keeps running,
        # and the caller never learns its id.
        wait_timeout = resolved.params.get("wait_timeout")
        if not isinstance(wait_timeout, (int, float)) or wait_timeout <= 0:
            wait_timeout = _DEFAULT_WAIT_TIMEOUT
        command_timeout = resolved.params.get("timeout")
        base = float(command_timeout) if isinstance(command_timeout, (int, float)) else 0.0
        read = base + float(wait_timeout) + _WAIT_TIMEOUT_MARGIN
    elif "timeout" in resolved.params:
        command_timeout = resolved.params.get("timeout")
        base = float(command_timeout) if isinstance(command_timeout, (int, float)) else _DEFAULT_TIMEOUT
        read = base + _WAIT_TIMEOUT_MARGIN
    return httpx.Timeout(read, connect=_CONNECT_TIMEOUT)


def _error_detail(response: httpx.Response) -> str | None:
    """Best-effort extraction of a JSON ``{"error": …}`` detail from a response.

    The ``/rpc/`` error responses (401/404/400/405/…) all carry a JSON body of
    the shape ``{"error": "<message>"}``. Return that message when present;
    otherwise ``None`` so the caller can fall back to a generic phrasing rather
    than echo a raw HTML/empty body.
    """
    try:
        body = orjson.loads(response.content)
    except ValueError:  # orjson.JSONDecodeError is a ValueError subclass
        return None
    if isinstance(body, dict):
        detail = body.get("error")
        if isinstance(detail, str) and detail:
            return detail
    return None


def _map_status_error(url: str, path: str, response: httpx.Response) -> RemoteTransportError:
    """Turn a non-200 HTTP response into a :class:`RemoteTransportError`.

    Special-cases the two operator-facing failures — ``401`` (the remote
    requires a token we did not / wrongly supplied) and ``404`` (the remote does
    not expose this command, e.g. version skew) — and otherwise reports the raw
    status plus any JSON ``error`` detail.
    """
    status = response.status_code
    detail = _error_detail(response)
    if status == 401:
        return RemoteTransportError(
            "authentication rejected — check --remote-token / TWICC_REMOTE_TOKEN"
            + (f" ({detail})" if detail else "")
        )
    if status == 404:
        return RemoteTransportError(f"remote has no such command: {path}")
    suffix = f": {detail}" if detail else ""
    return RemoteTransportError(f"remote returned HTTP {status}{suffix}")


def forward(url: str, token: str | None, argv: list[str]) -> int:
    """Forward a CLI command to a remote TwiCC ``/rpc/`` and map the result.

    Resolves ``argv`` against the local registry, applies the same pre-flight
    rejections as a local run would, inlines local ``--attach`` files and reads a
    file-path prompt / ``--message`` into text, then POSTs ``{"argv": [...]}`` to
    ``<url>/rpc/<path>``. On a 200 envelope it reproduces
    the command's own output exactly (``result`` to stdout in the same indented
    JSON shape as :func:`twicc.cli._output.emit_json`, ``error`` to stderr) and
    returns the remote ``exit_code`` — so a script behaves identically whether
    run locally or via ``--remote``.

    Returns the remote command's ``exit_code`` on completion.

    Raises :class:`RemoteUsageError` for a local misuse caught pre-flight
    (unknown / local-only command, host-bound ``self`` / ``parent``, missing
    attachment, more than 50 MB of inline attachment data, a request body above
    the 72 MB cap), and :class:`RemoteTransportError` for any transport- or
    remote-layer failure (cannot connect, DNS, timeout, non-200, malformed
    response). The caller prints ``twicc: <message>`` to stderr and exits with
    the exception's ``exit_code``; :func:`forward` itself only ever writes the
    *command's* own ``result`` / ``error``.
    """
    resolved = resolve_command(argv)
    reject_host_bound(resolved)
    check_inline_size(resolved)
    argv2 = inline_attachments(argv, resolved)
    argv2 = inline_prompt(argv2, resolved)
    argv2, resolved = apply_peer_send_timeout(argv2, resolved)

    endpoint = _endpoint_url(url, resolved.path)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = orjson.dumps({"argv": argv2})
    if len(body) > inline.INLINE_MAX_REQUEST_BYTES:
        # An inlined prompt file can push the body above the cap the server enforces.
        raise RemoteUsageError(
            f"the request is {inline.format_mb(len(body))} once encoded; the limit is "
            f"{inline.format_mb(inline.INLINE_MAX_REQUEST_BYTES)}. {_too_large_hint(resolved)}"
        )

    try:
        with httpx.Client(timeout=_request_timeout(resolved)) as client:
            response = client.post(endpoint, content=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise RemoteTransportError(f"remote timed out: {exc}") from exc
    except httpx.ConnectError as exc:
        raise RemoteTransportError(f"cannot reach remote {url}: {exc}") from exc
    except httpx.RequestError as exc:
        # Any other transport-level failure (read/write error, protocol error,
        # invalid URL, …) — never a server reply, so it is a transport error.
        raise RemoteTransportError(f"cannot reach remote {url}: {exc}") from exc

    if response.status_code != 200:
        raise _map_status_error(url, resolved.path, response)

    try:
        envelope = orjson.loads(response.content)
    except ValueError as exc:  # orjson.JSONDecodeError is a ValueError subclass
        raise RemoteTransportError(f"malformed response from remote: {exc}") from exc
    if not isinstance(envelope, dict) or "exit_code" not in envelope:
        raise RemoteTransportError("malformed response from remote: missing exit_code")

    result = envelope.get("result")
    # Invariant: result is None only on the error path — no command emits
    # emit_json(None), so a successful emit_json([])/{}/0/"" is not None and is
    # printed. Byte-for-byte identical to emit_json() so remote output matches a
    # local run exactly (indented UTF-8 JSON + trailing newline, on stdout).
    if result is not None:
        sys.stdout.buffer.write(orjson.dumps(result, option=orjson.OPT_INDENT_2))
        sys.stdout.buffer.write(b"\n")
    error = envelope.get("error")
    if error is not None:
        print(error, file=sys.stderr)

    return envelope["exit_code"]


# Environment fallbacks for the URL and token (see :func:`parse_remote_invocation`).
# An *empty* value is treated as unset for both, so an exported-but-blank var never
# masks the real precedence (inline value, then env, then unset).
_ENV_URL = "TWICC_REMOTE_URL"
_ENV_TOKEN = "TWICC_REMOTE_TOKEN"


def parse_remote_invocation(argv: list[str]) -> tuple[str, str | None, list[str]] | None:
    """Front-parse the leading global flags of ``argv`` for ``--remote`` mode.

    ``argv`` is ``sys.argv[1:]``. Only the *leading* global flags are parsed —
    parsing stops at the first token that is neither a recognized global flag nor
    a value consumed by one. This deliberately avoids colliding with a command's
    own free-text arguments (a ``--remote`` buried inside a message body is left
    alone, so the local app runs normally).

    Recognized leading flags, in any order:

    - ``--remote=<url>`` sets the URL inline; ``--remote <url>`` sets it only when
      the next token looks like a URL (contains ``://``) and is not itself a flag
      — command names never contain ``://``, so the space form never swallows a
      subcommand. A **bare** ``--remote`` (no URL token after it) leaves the URL
      unset here and falls back to :data:`_ENV_URL` below.
    - ``--remote-token=<tok>`` / ``--remote-token <tok>`` set the token (the space
      form always consumes the next token).

    Returns ``None`` when ``--remote`` is absent from the leading flags (the
    caller then runs the normal local app). When ``--remote`` is present, returns
    ``(url, token, command_argv)`` where ``command_argv`` is everything after the
    consumed leading flags.

    URL precedence: inline value, else :data:`_ENV_URL` (empty treated as unset),
    else :class:`RemoteUsageError`. Token precedence: inline value, else
    :data:`_ENV_TOKEN` (empty treated as ``None``), else ``None``.
    """
    remote_seen = False
    url: str | None = None
    token: str | None = None

    i = 0
    n = len(argv)
    while i < n:
        token_str = argv[i]

        if token_str.startswith("--remote-token="):
            token = token_str[len("--remote-token="):]
            i += 1
            continue
        if token_str == "--remote-token":
            # Space form: always consume the next token as the value (if any).
            if i + 1 < n:
                token = argv[i + 1]
                i += 2
            else:
                i += 1
            continue

        if token_str.startswith("--remote="):
            remote_seen = True
            url = token_str[len("--remote="):]
            i += 1
            continue
        if token_str == "--remote":
            remote_seen = True
            # Space form: only consume the next token as the URL when it actually
            # looks like one (contains "://") and is not another flag. Otherwise
            # this is a bare --remote and the URL comes from the environment.
            nxt = argv[i + 1] if i + 1 < n else None
            if nxt is not None and "://" in nxt and not nxt.startswith("-"):
                url = nxt
                i += 2
            else:
                i += 1
            continue

        # First token that is not a leading global flag (nor a consumed value):
        # stop front-parsing — everything from here is the command.
        break

    if not remote_seen:
        return None

    if not url:
        env_url = os.environ.get(_ENV_URL)
        url = env_url if env_url else None
    if not url:
        raise RemoteUsageError(
            "--remote given without a URL (pass --remote <url> or set TWICC_REMOTE_URL)"
        )
    if "://" not in url:
        # A scheme-less value (e.g. "box:3501") would otherwise reach httpx as a
        # relative/unsupported URL and surface as an opaque transport error.
        raise RemoteUsageError(
            f"remote URL must include a scheme (e.g. http://): {url}"
        )

    if not token:
        env_token = os.environ.get(_ENV_TOKEN)
        token = env_token if env_token else None

    command_argv = argv[i:]
    return url, token, command_argv


def maybe_forward(argv: list[str]) -> int | None:
    """Forward ``argv`` to a remote ``/rpc/`` when ``--remote`` is present.

    ``argv`` is ``sys.argv[1:]``. Parses the leading global flags via
    :func:`parse_remote_invocation`: when ``--remote`` is absent it returns
    ``None`` (the signal for the caller to run the local Typer app). Otherwise it
    calls :func:`forward` and returns the remote command's exit code.

    Both the parse and the forward run under a single ``try`` so the no-URL
    :class:`RemoteUsageError` is handled here too: on any
    :class:`RemoteUsageError` / :class:`RemoteTransportError` the message is
    printed as ``twicc: <message>`` to stderr and the exception's reserved
    ``exit_code`` is returned.
    """
    try:
        invocation = parse_remote_invocation(argv)
        if invocation is None:
            return None
        url, token, command_argv = invocation
        return forward(url, token, command_argv)
    except RemoteTransportError as exc:
        # Transport / remote-layer failures are labelled "remote error:" to set
        # them apart from a usage error (caught pre-flight, before any HTTP) and
        # from the forwarded command's own error (printed verbatim by forward()).
        print(f"twicc: remote error: {exc}", file=sys.stderr)
        return exc.exit_code
    except RemoteUsageError as exc:
        print(f"twicc: {exc}", file=sys.stderr)
        return exc.exit_code
