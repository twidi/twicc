"""Inline attachment data: the one place that knows the inline limit.

"Inline" means bytes that travel inside a request: a ``data:`` URI in ``--attach`` (RPC,
MCP, local CLI, or built by the ``--remote`` forwarder) and every entry of the peer wire.
A file read from a disk (a local path, an absolute server path, ``remote:``) is not inline
and has no limit (D11).

No application import at module level: the ``--remote`` forwarder and the CLI help strings
import this module before Django is set up. Staging helpers are imported inside functions.
Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.2.
"""

import base64
import binascii
import mimetypes
import re
from typing import NamedTuple
from urllib.parse import unquote

MIB = 1024 * 1024
# Total decoded bytes of the inline files of one request (D5).
INLINE_MAX_BYTES = 50 * MIB
# Body cap of an HTTP request that may carry inline data (D12): 4 * ceil(50 MB / 3) =
# 69 905 068 bytes of base64, plus about 5 MB for the JSON, the names and the text.
INLINE_MAX_REQUEST_BYTES = 72 * MIB
# The slowest upload a peer send is sized for (2 Mbit/s).
INLINE_MIN_THROUGHPUT = 256 * 1024
# Today's peer connect / read timeout (``peer.outbound.OUTBOUND_TIMEOUT_SECONDS``).
TRANSFER_BASE_SECONDS = 30.0

INLINE_TOO_LARGE_HINT = (
    "For a larger file, put it on a file storage service and pass its URL in the message text, "
    "or pass a path the server reads: remote:<absolute path> over --remote, an absolute server path "
    "over the RPC or the MCP."
)
PEER_TOO_LARGE_HINT = (
    "For a larger file, put it on a file storage service and pass its URL in the message text."
)

ERROR_INVALID_DATA_URI = "invalid_data_uri"
ERROR_TOO_LARGE = "attachments_too_large"

OCTET_STREAM = "application/octet-stream"
TEXT_PLAIN = "text/plain"
DATA_URI_PREFIX = "data:"
MEDIA_TYPE_MAX_CHARS = 255
# How much of a malformed data URI (no comma) its error label shows.
_LABEL_RAW_END = 40
# ``type/subtype``, RFC 6838 restricted-name characters, no parameters.
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*")


def transfer_timeout(body_bytes: int) -> float:
    """The time budget of an HTTP write of *body_bytes* (§4.2)."""
    return TRANSFER_BASE_SECONDS + body_bytes / INLINE_MIN_THROUGHPUT


# The wait of a caller of the peer send service for a message with files, from the worst case:
# the write of a full body, the local steps (file reads, encoding, the DB write lock), the
# connect, the receiver's answer, and a margin. Below the 600 s MCP tool timeout.
_PEER_SEND_LOCAL_STEPS_SECONDS = 60
_PEER_CONNECT_SECONDS = 30
_PEER_ANSWER_SECONDS = 30
_PEER_SEND_MARGIN_SECONDS = 30
PEER_SEND_TIMEOUT_WITH_FILES = (
    int(transfer_timeout(INLINE_MAX_REQUEST_BYTES))
    + _PEER_SEND_LOCAL_STEPS_SECONDS + _PEER_CONNECT_SECONDS + _PEER_ANSWER_SECONDS + _PEER_SEND_MARGIN_SECONDS
)


def _error(code: str, message: str) -> Exception:
    # Imported here: the staging module is not loaded on the CLI help path.
    from twicc.core.services.attachments.staging import AttachmentError

    return AttachmentError(code, message)


def format_mb(size: int) -> str:
    """``50 MB`` for a whole number of MB, else one decimal (``50.0 MB``)."""
    return f"{size // MIB} MB" if size % MIB == 0 else f"{size / MIB:.1f} MB"


def too_large_message(total: int, limit: int, hint: str) -> str:
    return f"The attached files total {format_mb(total)} of inline data; the limit is {format_mb(limit)}. {hint}"


class InlineBudget:
    """Adds up the decoded sizes of the inline files of one request (D5)."""

    def __init__(self, hint: str, *, limit: int | None = None) -> None:
        self.hint = hint
        self.limit = INLINE_MAX_BYTES if limit is None else limit
        self.total = 0

    def add(self, size: int) -> None:
        """Count *size* bytes; raise ``attachments_too_large`` above the limit."""
        self.total += size
        if self.total > self.limit:
            raise _error(ERROR_TOO_LARGE, too_large_message(self.total, self.limit, self.hint))


class DataUri(NamedTuple):
    name: str | None  # percent-decoded, not normalized; None without a (non-empty) name=
    media_type: str  # declared, only a label
    size: int  # decoded bytes
    data: bytes


def is_data_uri(value: str) -> bool:
    return value.startswith(DATA_URI_PREFIX)


def _decoded_size_or_none(data: str) -> int | None:
    """Decoded size of standard base64 *data* from its length and padding, or None when malformed."""
    if len(data) % 4:
        return None
    padding = len(data) - len(data.rstrip("="))
    if padding > 2:
        return None
    return len(data) // 4 * 3 - padding


def decoded_size(data: str) -> int:
    """Decoded size of *data* without decoding it; ``invalid_data_uri`` when malformed."""
    size = _decoded_size_or_none(data)
    if size is None:
        raise _error(ERROR_INVALID_DATA_URI, "invalid base64 payload in data URI")
    return size


def _split(spec: str) -> tuple[list[str], str] | None:
    """``(parameters, payload)`` of a data URI, or None without a comma."""
    header, sep, payload = spec[len(DATA_URI_PREFIX):].partition(",")
    if not sep:
        return None
    return header.split(";"), payload


def _media_type(params: list[str]) -> str:
    return params[0].strip() or OCTET_STREAM


def _name(params: list[str]) -> str | None:
    """The ``name=`` parameter, percent-decoded; None when absent or empty."""
    for raw in params[1:]:
        key, sep, value = raw.strip().partition("=")
        if sep and key.strip().lower() == "name":
            return unquote(value.strip(), errors="strict") or None
    return None


def parse_data_uri(spec: str, *, budget: InlineBudget | None = None) -> DataUri:
    """Parse and decode ``data:<media>[;<param>]*,<data>`` (§4.2).

    Parameters in any order; ``base64`` required in any case; ``name=`` optional; other
    parameters ignored. The whitespace inside the base64 is removed, then the decoded size
    is computed and counted by *budget* BEFORE any decoding. An empty payload is a 0-byte
    file. Raises ``AttachmentError`` (``invalid_data_uri`` or ``attachments_too_large``).
    """
    parts = _split(spec)
    if parts is None:
        raise _error(ERROR_INVALID_DATA_URI, "malformed data URI (missing comma)")
    params, payload = parts
    if not any(param.strip().lower() == "base64" for param in params[1:]):
        raise _error(ERROR_INVALID_DATA_URI, "only base64 data URIs are supported (data:<mime>;base64,...)")
    try:
        name = _name(params)
    except UnicodeDecodeError:
        raise _error(
            ERROR_INVALID_DATA_URI, "invalid name= encoding in data URI (percent-encoded UTF-8 expected)",
        ) from None
    data = "".join(payload.split())
    size = decoded_size(data)
    if budget is not None:
        budget.add(size)
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise _error(ERROR_INVALID_DATA_URI, "invalid base64 payload in data URI") from None
    return DataUri(name, _media_type(params), size, raw)


def data_uri_size(spec: str) -> int | None:
    """Decoded size of a data URI without decoding it; None when malformed (the server reports it)."""
    parts = _split(spec)
    if parts is None:
        return None
    return _decoded_size_or_none("".join(parts[1].split()))


def data_uri_label(spec: str) -> str:
    """The error label of a data URI: never the URI itself, which can be tens of MB (§4.2)."""
    parts = _split(spec)
    if parts is None:
        return f"{DATA_URI_PREFIX}{spec[len(DATA_URI_PREFIX):_LABEL_RAW_END]}…"
    params = parts[0]
    media = _media_type(params)[:MEDIA_TYPE_MAX_CHARS]
    try:
        name = _name(params)
    except UnicodeDecodeError:
        name = None
    if not name:
        return f"{DATA_URI_PREFIX}{media}"
    from twicc.core.services.attachments.staging import name_max_bytes, normalize_filename

    return f"{DATA_URI_PREFIX}{media} {normalize_filename(name, name_max_bytes())}"


def default_name(media_type: str, n: int) -> str:
    """``attachment-<n>`` plus the extension of *media_type* (``.bin`` when unknown) (D10)."""
    base = media_type.split(";", 1)[0].strip().lower()
    extension = (mimetypes.guess_extension(base) if base else None) or ".bin"
    return f"attachment-{n}{extension}"


def sanitize_media_type(value: object) -> str:
    """*value* when it is a ``type/subtype`` token of at most 255 characters, else octet-stream (§4.8.2)."""
    if isinstance(value, str) and len(value) <= MEDIA_TYPE_MAX_CHARS and _MEDIA_TYPE.fullmatch(value):
        return value
    return OCTET_STREAM


def entries_from_legacy_blocks(images: list | None, documents: list | None) -> list[dict]:
    """Wire entries ``{name, media_type, data}`` from the SDK blocks of an older peer (D16).

    Images first, then documents, in order. Any media type is kept (sanitized later). A
    ``base64`` source keeps its data; a ``text`` source is encoded as UTF-8 then base64. The
    name is the block ``title`` or ``name``, else ``attachment-<n>.<ext>`` (``n`` from 1).
    The caller has checked the block shape.
    """
    entries: list[dict] = []
    for n, block in enumerate([*(images or []), *(documents or [])], start=1):
        source = block.get("source") or {}
        is_text = source.get("type") == "text"
        media_type = source.get("media_type") or (TEXT_PLAIN if is_text else OCTET_STREAM)
        if not isinstance(media_type, str):
            media_type = OCTET_STREAM
        data = source.get("data") or ""
        if is_text:
            data = base64.b64encode(str(data).encode("utf-8")).decode("ascii")
        title = block.get("title") or block.get("name")
        name = title if isinstance(title, str) and title else default_name(media_type, n)
        entries.append({"name": name, "media_type": media_type, "data": data})
    return entries
