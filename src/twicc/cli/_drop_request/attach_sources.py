"""Resolve and stage the ``--attach`` values of a CLI command.

A value is a file path (read by this process), ``remote:<path>`` (meaningful only over
``--remote``, where the forwarder turns it into a bare server path before the call), or a base64
data URI with an optional ``name=``. Every resolved file is copied into the composer staging
store as a one-shot entry; the drop payload carries the refs as ``attachments``.
Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.4.
"""

from __future__ import annotations

import os
from typing import NamedTuple

from twicc.cli._drop_request import transport
from twicc.cli._drop_request.remote_scheme import has_remote_scheme
from twicc.cli._drop_request.validation import ValidationError
from twicc.cli._output import in_api_mode
from twicc.core.services.attachments import inline, staging
from twicc.core.services.attachments.staging import AttachmentError
from twicc.core.services.attachments.types import AttachmentRef


class AttachSource(NamedTuple):
    """One resolved ``--attach`` value: a file to copy (*path*) or decoded bytes (*data*)."""

    label: str  # the error label: the bounded value, or the short label of a data URI
    name: str  # the staged file name, before normalization
    path: str | None
    data: bytes | None


def _field(label: str) -> str:
    return f"--attach {label}"


def staging_origin() -> str:
    """``api`` for a command running inside the backend (RPC, MCP), else ``cli``."""
    return staging.ORIGIN_API if transport._in_backend() else staging.ORIGIN_CLI


def new_request_bucket() -> str:
    """The staging bucket of one command invocation."""
    return staging.new_bucket(staging_origin())


def as_payload(refs: tuple[AttachmentRef, ...]) -> list[dict]:
    """The ``attachments`` value of a drop payload."""
    return [ref._asdict() for ref in refs]


def resolve(
    attach: list[str], *, hint: str, count_paths: bool = False,
) -> tuple[list[AttachSource], list[ValidationError]]:
    """Turn the ``--attach`` strings into sources; every problem is an error of its value.

    Data URIs are decoded and their decoded sizes counted by one :class:`inline.InlineBudget`
    (a path is counted too with *count_paths*: ``peer-send``, whose files all travel inline).
    *hint* ends the ``attachments_too_large`` message. Reads no file content.
    """
    budget = inline.InlineBudget(hint)
    over_budget = False
    sources: list[AttachSource] = []
    errors: list[ValidationError] = []
    for n, spec in enumerate(attach, start=1):
        # Never the raw value: a data URI can be tens of MB, and a mistyped one is read as a path.
        label = inline.attach_label(spec)
        if inline.is_data_uri(spec):
            try:
                uri = inline.parse_data_uri(spec, budget=None if over_budget else budget)
            except AttachmentError as exc:
                errors.append(ValidationError(_field(label), exc.code, str(exc)))
                over_budget = over_budget or exc.code == inline.ERROR_TOO_LARGE
                continue
            sources.append(AttachSource(label, uri.name or inline.default_name(uri.media_type, n), None, uri.data))
            continue
        if has_remote_scheme(spec):
            # Only meaningful over --remote, where the forwarder strips it before the call.
            errors.append(ValidationError(
                _field(label), "remote_requires_remote", "remote: paths are only valid with --remote",
            ))
            continue
        if in_api_mode() and not os.path.isabs(spec):
            errors.append(ValidationError(
                _field(label), "relative_path",
                "relative path not allowed over the API (no caller working directory); "
                "pass an absolute path or a data: URI",
            ))
            continue
        if not os.path.isfile(spec):
            errors.append(ValidationError(
                _field(label), "not_a_file", f"file {label!r} does not exist or is not a regular file",
            ))
            continue
        if count_paths and not over_budget:
            try:
                budget.add(os.path.getsize(spec))
            except AttachmentError as exc:
                errors.append(ValidationError(_field(label), exc.code, str(exc)))
                over_budget = True
                continue
        sources.append(AttachSource(label, os.path.basename(spec), spec, None))
    return sources, errors


def stage(sources: list[AttachSource], *, bucket: str) -> tuple[tuple[AttachmentRef, ...], list[ValidationError]]:
    """Copy *sources* into one-shot entries of *bucket*, in order.

    On a failure, the entries staged by this call are discarded and the error of the failing
    source is returned. An interruption (Ctrl-C) discards them too, then propagates. Once the
    refs are submitted, the backend owns them: the CLI never deletes them afterwards.
    """
    origin = staging_origin()
    refs: list[AttachmentRef] = []
    try:
        for source in sources:
            try:
                if source.path is not None:
                    ref = staging.stage_path(source.path, bucket=bucket, origin=origin, name=source.name)
                else:
                    ref = staging.stage_bytes(source.data, source.name, bucket=bucket, origin=origin)
            except AttachmentError as exc:
                staging.discard_staged(refs)
                return (), [ValidationError(_field(source.label), exc.code, str(exc))]
            refs.append(ref)
    except BaseException:
        staging.discard_staged(refs)
        raise
    return tuple(refs), []
