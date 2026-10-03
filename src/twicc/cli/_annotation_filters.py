"""Annotation filter parser and Django ORM helper.

Used by `twicc sessions`, `twicc search`, `twicc topology`
to filter on the JSONField `Session.annotations`. See
`docs/superpowers/specs/2026-06-01-annotation-filtering-design.md`.
"""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, NamedTuple

from django.db.models import Q, QuerySet

# Reused from create-session's annotation parsing for typed scalar inference.
from twicc.cli._drop_request.annotations import _FLOAT_RE, _INT_RE


class AnnotationOp(str, Enum):
    EQ = "eq"
    NE = "ne"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    IN = "in"


class AnnotationFilter(NamedTuple):
    path: tuple[str, ...]
    op: AnnotationOp
    value: Any  # ignored for EXISTS / NOT_EXISTS


_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


def _parse_scalar(raw: str) -> Any:
    """Same typing rules as create-session --annotation (true/false/null/int/float/string)."""
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "null":
        return None
    if _INT_RE.match(raw):
        return int(raw)
    if _FLOAT_RE.match(raw):
        value = float(raw)
        if math.isfinite(value):
            return value
        # inf / nan fall through to string — matches create-session strictness
    return raw


def _split_in_list(raw: str) -> list[str]:
    """Split a :in: value list on unescaped commas. Handles \\, and \\\\."""
    out: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw) and raw[i + 1] in ",\\":
            buf.append(raw[i + 1])
            i += 2
            continue
        if ch == ",":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


def parse_annotation_filter(spec: str) -> AnnotationFilter:
    """Parse one --annotation value into a typed AnnotationFilter.

    Raises ValueError on:
    - empty input
    - key empty or containing characters other than [A-Za-z0-9_-] and dots
    - unknown operator suffix after `:`
    - `:in:` with an empty value list
    - no operator detected (no `=`, `!=`, `:exists`, `:not-exists`, `:in:`)
    """
    if not spec:
        raise ValueError("empty --annotation value")

    # Order matters: longer suffixes before shorter ones.
    if ":in:" in spec:
        key, _, raw_value = spec.partition(":in:")
        _validate_key(key)
        items = _split_in_list(raw_value)
        if items == [""]:
            raise ValueError(f"--annotation: empty :in: list in {spec!r}")
        return AnnotationFilter(_split_key(key), AnnotationOp.IN, [_parse_scalar(x) for x in items])

    if spec.endswith(":not-exists"):
        key = spec[: -len(":not-exists")]
        _validate_key(key)
        return AnnotationFilter(_split_key(key), AnnotationOp.NOT_EXISTS, None)

    if spec.endswith(":exists"):
        key = spec[: -len(":exists")]
        _validate_key(key)
        return AnnotationFilter(_split_key(key), AnnotationOp.EXISTS, None)

    # ':' followed by an unknown suffix is a parser error — surface it instead
    # of falling through to '=' and producing a confusing query.
    if ":" in spec and "=" not in spec.split(":", 1)[0]:
        # e.g. 'key:foobar' or 'key:' — neither = nor a known suffix
        raise ValueError(
            f"--annotation: unknown operator in {spec!r}; expected one of "
            "=, !=, :exists, :not-exists, :in:"
        )

    if "!=" in spec:
        key, _, raw_value = spec.partition("!=")
        _validate_key(key)
        return AnnotationFilter(_split_key(key), AnnotationOp.NE, _parse_scalar(raw_value))

    if "=" in spec:
        key, _, raw_value = spec.partition("=")
        _validate_key(key)
        return AnnotationFilter(_split_key(key), AnnotationOp.EQ, _parse_scalar(raw_value))

    raise ValueError(
        f"--annotation: no operator found in {spec!r}; expected one of "
        "=, !=, :exists, :not-exists, :in:"
    )


def _validate_key(key: str) -> None:
    if not key:
        raise ValueError("--annotation: empty key")
    if not _KEY_RE.match(key):
        raise ValueError(
            f"--annotation: invalid key {key!r}; allowed characters are A-Z, a-z, 0-9, _, - "
            "and . as path separator"
        )


def _split_key(key: str) -> tuple[str, ...]:
    return tuple(key.split("."))


def apply_annotation_filters(
    queryset: QuerySet,
    filters: list[AnnotationFilter],
    *,
    field: str = "annotations",
) -> QuerySet:
    """Return a new QuerySet with all filters AND-applied.

    `field` is the model attribute (lookup prefix) holding the JSONField.
    Default 'annotations' targets Session directly. To filter through a
    related model that actually has an FK to Session, pass a dotted lookup
    like 'session__annotations'.
    """
    for f in filters:
        path_chain = "__".join(f.path)
        prefix = f"{field}__{path_chain}"
        if f.op is AnnotationOp.EQ:
            queryset = queryset.filter(**{prefix: f.value})
        elif f.op is AnnotationOp.NE:
            # exclude() alone drops rows where the key is absent (SQLite JSON
            # quirk: absent path → NULL, which ORM silently strips out). To
            # honour the documented semantics — "absent key counts as NE" —
            # we keep rows that are either absent OR have a different value.
            queryset = queryset.filter(
                Q(**{f"{prefix}__isnull": True}) | ~Q(**{prefix: f.value})
            )
        elif f.op is AnnotationOp.EXISTS:
            queryset = queryset.filter(**{f"{prefix}__isnull": False})
        elif f.op is AnnotationOp.NOT_EXISTS:
            queryset = queryset.filter(**{f"{prefix}__isnull": True})
        elif f.op is AnnotationOp.IN:
            queryset = queryset.filter(**{f"{prefix}__in": f.value})
        else:  # defensive — enum exhausted above
            raise AssertionError(f"unhandled AnnotationOp: {f.op}")
    return queryset
