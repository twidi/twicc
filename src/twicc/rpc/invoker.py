"""Run a CLI command in-process and capture its structured result.

The HTTP RPC layer (and, later, the ``--remote`` forwarder) execute commands
through :func:`invoke`. It runs the *exact* Typer command the terminal CLI
runs, with ``standalone_mode=False`` so Click neither calls ``sys.exit`` nor
touches the global ``stdout``/``stderr``. Result and error are captured via
the ``_capture`` ContextVar in :mod:`twicc.cli._output`, making :func:`invoke`
safe to call concurrently from worker threads.
"""

from __future__ import annotations

from typing import NamedTuple

import click
import typer

from twicc.cli import app
from twicc.cli._output import _notices, _Sink, _capture


class InvocationResult(NamedTuple):
    exit_code: int
    result: object | None
    error: str | None
    #: Deprecation notices the command recorded. Last and defaulted so the
    #: positional construction sites keep working; a tuple because NamedTuple
    #: defaults are shared across instances.
    warnings: tuple[str, ...] = ()


# Build the Click command once: this registers the whole Typer tree.
_command: click.Command = typer.main.get_command(app)


def get_command() -> click.Command:
    """Return the root Click command (the converted Typer app)."""
    return _command


def invoke(argv: list[str]) -> InvocationResult:
    """Execute ``twicc <argv>`` in-process; capture result, error, exit code."""
    sink = _Sink()
    notices: list[str] = []
    tok = _capture.set(sink)
    tok_notices = _notices.set(notices)
    try:
        # With ``standalone_mode=False`` Click never re-raises a ``typer.Exit``
        # / ``click.exceptions.Exit`` out of ``.main()``: it converts it to the
        # function's return value (``e.exit_code``). A normal completion returns
        # the command's own return value (typically ``None``). So the exit code
        # is whatever ``.main()`` hands back, not a swallowed exception.
        raw = _command.main(args=argv, prog_name="twicc", standalone_mode=False)
        code = raw if isinstance(raw, int) else 0
    except (click.exceptions.Exit, SystemExit) as exc:
        raw = getattr(exc, "exit_code", getattr(exc, "code", 0))
        code = raw if isinstance(raw, int) else (0 if raw is None else 1)
    except click.exceptions.Abort:
        code = 1
        if sink.error is None:
            sink.error = "Aborted."
    except click.ClickException as exc:
        code = exc.exit_code
        if sink.error is None:
            sink.error = exc.format_message()
    finally:
        _notices.reset(tok_notices)
        _capture.reset(tok)
    # Read through the local ``notices`` name, not ``_notices.get()``: the
    # ``finally`` above has already run by the time this line does. Same reason
    # ``sink`` is a local.
    return InvocationResult(
        exit_code=code, result=sink.result, error=sink.error, warnings=tuple(notices),
    )
