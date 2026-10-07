"""Convert stored peer messages to the attachment entries shape (phase 2 design D17, §4.8.5).

A row of the old shape (its payload has ``images`` / ``documents``, or an ``attachments_meta``
row has ``kind``) gets ``payload.attachments`` (built from the SDK blocks, set only when not
empty) and ``attachments_meta`` rows ``{name, media_type, bytes}``; ``images`` and ``documents``
are removed and ``text`` is never touched. The rules of ``inline.entries_from_legacy_blocks``
and of the receive-time sanitation are copied here: a migration never imports app code. The
name bound is a fixed 255 bytes; the composer normalizes again at delivery. The rows are read
one at a time (SQLite has no server-side cursor). The old shape is not rebuilt on reverse.
"""

import base64
import binascii
import mimetypes
import re

from django.db import migrations

NAME_MAX_BYTES = 255
MEDIA_TYPE_MAX_CHARS = 255
OCTET_STREAM = "application/octet-stream"
TEXT_PLAIN = "text/plain"
FALLBACK_NAME = "attachment"
TEMP_FILE_PREFIX = ".twicc-upload-"
LEGACY_KEYS = ("images", "documents")
FORBIDDEN_CHARS = re.compile("[\x00-\x1f\x7f\x85  /\\\\]")
MEDIA_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*")


def _truncate(name, max_bytes):
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    ext_bytes = len(ext.encode("utf-8"))
    if ext_bytes >= max_bytes:
        stem, ext = name, ""
        budget = max_bytes
    else:
        budget = max_bytes - ext_bytes
    return stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + ext


def _normalize_name(name):
    result = FORBIDDEN_CHARS.sub("_", name.strip())
    if result.startswith(TEMP_FILE_PREFIX):
        result = "_" + result
    result = _truncate(result, NAME_MAX_BYTES).strip()
    return FALLBACK_NAME if result in ("", ".", "..") else result


def _media_type(value):
    if isinstance(value, str) and len(value) <= MEDIA_TYPE_MAX_CHARS and MEDIA_TYPE.fullmatch(value):
        return value
    return OCTET_STREAM


def _default_name(media_type, n):
    base = media_type.split(";", 1)[0].strip().lower() if isinstance(media_type, str) else ""
    return f"attachment-{n}{(mimetypes.guess_extension(base) if base else None) or '.bin'}"


def _decoded_size(data):
    padding = len(data) - len(data.rstrip("="))
    if len(data) % 4 == 0 and padding <= 2:
        return len(data) // 4 * 3 - padding
    try:
        return len(base64.b64decode(data))
    except (binascii.Error, ValueError):
        return 0


def _blocks(payload):
    blocks = []
    for key in LEGACY_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            blocks.extend(value)
    return blocks


def _entries(payload):
    """The sanitized wire entries of the legacy blocks, images first, in order."""
    entries = []
    for n, block in enumerate(_blocks(payload), start=1):
        block = block if isinstance(block, dict) else {}
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        is_text = source.get("type") == "text"
        media_type = source.get("media_type") or (TEXT_PLAIN if is_text else OCTET_STREAM)
        if not isinstance(media_type, str):
            media_type = OCTET_STREAM
        data = source.get("data") or ""
        if is_text:
            data = base64.b64encode(str(data).encode("utf-8")).decode("ascii")
        elif not isinstance(data, str):
            data = ""
        title = block.get("title") or block.get("name")
        name = title if isinstance(title, str) and title else _default_name(media_type, n)
        entries.append({"name": _normalize_name(name), "media_type": _media_type(media_type), "data": data})
    return entries


def _meta_from_old_rows(rows):
    meta = []
    for n, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict):
            continue
        media_type = row.get("media_type") or OCTET_STREAM
        name = row.get("name")
        if not (isinstance(name, str) and name):
            name = _default_name(media_type, n)
        size = row.get("bytes")
        meta.append({
            "name": _normalize_name(name),
            "media_type": _media_type(media_type),
            "bytes": size if isinstance(size, int) and not isinstance(size, bool) else 0,
        })
    return meta


def _is_old_shape(payload, meta):
    if isinstance(payload, dict) and any(key in payload for key in LEGACY_KEYS):
        return True
    return isinstance(meta, list) and any(isinstance(row, dict) and "kind" in row for row in meta)


def convert_peer_messages(apps, schema_editor):
    PeerMessage = apps.get_model("core", "PeerMessage")
    for pk in list(PeerMessage.objects.values_list("pk", flat=True)):
        row = PeerMessage.objects.filter(pk=pk).only("pk", "payload", "attachments_meta").first()
        if row is None or not _is_old_shape(row.payload, row.attachments_meta):
            continue
        payload = dict(row.payload) if isinstance(row.payload, dict) else {}
        entries = _entries(payload)
        if entries:
            meta = [
                {"name": entry["name"], "media_type": entry["media_type"], "bytes": _decoded_size(entry["data"])}
                for entry in entries
            ]
        else:
            meta = _meta_from_old_rows(row.attachments_meta)
        for key in LEGACY_KEYS:
            payload.pop(key, None)
        if entries:
            payload["attachments"] = entries
        row.payload = payload
        row.attachments_meta = meta
        row.save(update_fields=["payload", "attachments_meta"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0152_async_question_state"),
    ]

    operations = [
        migrations.RunPython(convert_peer_messages, migrations.RunPython.noop),
    ]
