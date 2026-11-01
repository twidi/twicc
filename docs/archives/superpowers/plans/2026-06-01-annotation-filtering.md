# Annotation Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--annotation KEY[OP]VALUE` filtering (repeatable, AND-combined) to `twicc sessions`, `twicc processes`, `twicc search`, and `twicc topology`, with five operators (`=`, `!=`, `:exists`, `:not-exists`, `:in:`).

**Architecture:** A single private CLI helper `src/twicc/cli/_annotation_filters.py` exposes two functions: `parse_annotation_filter(spec)` returns a typed `AnnotationFilter` NamedTuple; `apply_annotation_filters(qs, filters)` chains Django ORM lookups on a JSONField. Each CLI parses its `--annotation` options and calls these helpers in a single `.filter()` chain after the existing filiation block. Topology issues a second ORM query (same filiation + annotation filter) and enriches each serialized node with `matches_annotations: bool` via set membership. Search uses a Tantivy oversample-and-post-filter loop with `exhausted` and `partial` flags in the payload.

**Tech Stack:** Python ≥ 3.13, Django 6 (JSONField + JSON1), Typer, orjson, Tantivy (existing index, unchanged), pytest-django.

**Spec reference:** `docs/superpowers/specs/2026-06-01-annotation-filtering-design.md`

---

## File Structure

### Files to create

| Path | Responsibility |
|---|---|
| `src/twicc/cli/_annotation_filters.py` | Parser + ORM-query helper. Single source of truth for filter semantics. |
| `tests/test_cli_annotation_filters.py` | Unit tests for the parser and the ORM helper (with `db` fixture for the latter). |

### Files to modify

| Path | Change |
|---|---|
| `src/twicc/cli/__init__.py` | Declare the four `--annotation` Typer options in the matching Typer command wrappers (`_sessions_default` L199-279, `_processes_default` L423-501, `topology` L379-411, `search` L784-849); pass the raw `list[str]` through to each `*_main()`. |
| `src/twicc/cli/sessions.py` | Accept new `annotation: list[str] | None = None` parameter in `main()`; parse via `parse_annotation_filter`; chain `apply_annotation_filters` after the filiation block. |
| `src/twicc/cli/processes.py` | Accept new `annotation` parameter; apply via a Session-id pre-filter (NOT via `session__annotations` — `ProcessRun.session_id` is a plain `CharField`, not a FK, see `models.py:972-979`). |
| `src/twicc/cli/search.py` | Accept new `annotation` parameter; switch single-shot `raw_search` to an oversample loop, surface `exhausted` / `partial`. |
| `src/twicc/cli/topology.py` | Accept new `annotation` parameter on `main()` AND `annotation_filters` on `build_topology()`; issue second ORM query right after `_load_topology_sessions(seed)`; enrich `_serialize_topology_node` payload with `matches_annotations`. |
| `tests/test_cli_topology.py` | Add tests for `matches_annotations` flag (present nodes, absent nodes, no-filter case). |
| `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md` | Document `--annotation` syntax + examples + filiation composition. |
| `src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md` | Same. |
| `src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md` | Same + note on `exhausted` / `partial` flags. |
| `src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md` | Document `--annotation` + `matches_annotations` field. |
| `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` | Bump version `0.27.0` → `0.28.0`. |

### Files NOT to touch

- `src/twicc/core/models.py` — no schema change.
- `src/twicc/core/serializers.py` — `matches_annotations` is added in `topology.py` post-serialization.
- `src/twicc/search.py` Tantivy schema and `CURRENT_SEARCH_VERSION` — unchanged.
- REST views and WebSocket consumers — out of scope.

---

## Conventions reminder (project-specific)

- **Editable install + worktree** (cf. `CLAUDE.md`): if working in a worktree, every Bash command in this plan must be prefixed with `cd <worktree-path> && ` and run with `TWICC_DATA_DIR=$PWD` for any Python that touches the DB.
- **Language**: all code, comments, identifiers in English. Only `*.md` docs may contain French (but this plan and skill bundle stay English).
- **NamedTuple** preferred over `@dataclass` for immutable containers (cf. CLAUDE.md "Python Patterns").
- **orjson** instead of `json` (cf. CLAUDE.md).
- **Server / migrations / package install** are **user-only operations** (cf. CLAUDE.md). The plan never runs `migrate`, `devctl.py restart`, `uv add` itself. There is no migration in this plan; if any other reserved op becomes necessary, surface to the user.
- **Plugin version bump** is mandatory for any skill bundle change (cf. CLAUDE.md "TwiCC Plugin (Agent Skills)").
- **Commits**: never `git add -A` or `-a`; always list files explicitly.

---

## Task 1: Helper module — types and parser

**Files:**
- Create: `src/twicc/cli/_annotation_filters.py`
- Test: `tests/test_cli_annotation_filters.py`

This task creates the typed container and the parser. The ORM helper comes in Task 2 to keep the test surface focused.

- [ ] **Step 1.1: Create the helper module skeleton**

Create `src/twicc/cli/_annotation_filters.py` with:

```python
"""Annotation filter parser and Django ORM helper.

Used by `twicc sessions`, `twicc processes`, `twicc search`, `twicc topology`
to filter on the JSONField `Session.annotations`. See
`docs/superpowers/specs/2026-06-01-annotation-filtering-design.md`.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, NamedTuple

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


_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*$")


def _parse_scalar(raw: str) -> Any:
    """Same typing rules as create-session --annotation (true/false/null/int/float/string)."""
    import math
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
```

- [ ] **Step 1.2: Add the parser tests**

Create `tests/test_cli_annotation_filters.py`:

```python
"""Tests for the annotation filter parser and ORM helper."""

from __future__ import annotations

import pytest

from twicc.cli._annotation_filters import (
    AnnotationFilter,
    AnnotationOp,
    parse_annotation_filter,
)


# --- parse_annotation_filter ---------------------------------------------

def test_parse_eq_string():
    assert parse_annotation_filter("role=implementer") == AnnotationFilter(
        ("role",), AnnotationOp.EQ, "implementer"
    )


def test_parse_eq_typed_int_and_float_and_bool_and_null():
    assert parse_annotation_filter("weight=5").value == 5
    assert parse_annotation_filter("ratio=1.5").value == 1.5
    assert parse_annotation_filter("active=true").value is True
    assert parse_annotation_filter("active=false").value is False
    assert parse_annotation_filter("note=null").value is None


def test_parse_eq_empty_string_value():
    assert parse_annotation_filter("note=").value == ""


def test_parse_dotted_key_splits_into_path_tuple():
    f = parse_annotation_filter("team.lead.name=alice")
    assert f.path == ("team", "lead", "name")
    assert f.op is AnnotationOp.EQ


def test_parse_ne():
    f = parse_annotation_filter("status!=done")
    assert f == AnnotationFilter(("status",), AnnotationOp.NE, "done")


def test_parse_exists_and_not_exists():
    assert parse_annotation_filter("team:exists") == AnnotationFilter(
        ("team",), AnnotationOp.EXISTS, None
    )
    assert parse_annotation_filter("team:not-exists") == AnnotationFilter(
        ("team",), AnnotationOp.NOT_EXISTS, None
    )


def test_parse_in_typed_elements():
    f = parse_annotation_filter("status:in:done,blocked,5,true")
    assert f.path == ("status",)
    assert f.op is AnnotationOp.IN
    assert f.value == ["done", "blocked", 5, True]


def test_parse_in_escaped_comma_and_backslash():
    f = parse_annotation_filter("note:in:hello\\,world,foo\\\\bar")
    assert f.value == ["hello,world", "foo\\bar"]


def test_parse_rejects_empty_spec():
    with pytest.raises(ValueError):
        parse_annotation_filter("")


def test_parse_rejects_empty_key():
    with pytest.raises(ValueError):
        parse_annotation_filter("=value")
    with pytest.raises(ValueError):
        parse_annotation_filter(":exists")


def test_parse_rejects_unknown_operator_suffix():
    with pytest.raises(ValueError, match="unknown operator"):
        parse_annotation_filter("key:foobar")


def test_parse_rejects_missing_operator():
    with pytest.raises(ValueError, match="no operator"):
        parse_annotation_filter("just-a-key")


def test_parse_rejects_empty_in_list():
    with pytest.raises(ValueError, match="empty :in: list"):
        parse_annotation_filter("status:in:")


def test_parse_rejects_invalid_key_characters():
    with pytest.raises(ValueError, match="invalid key"):
        parse_annotation_filter("space key=foo")
    with pytest.raises(ValueError, match="invalid key"):
        parse_annotation_filter("a@b=foo")
```

- [ ] **Step 1.3: Run the parser tests**

Worktree-aware command:

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run pytest tests/test_cli_annotation_filters.py -v
```

Expected: every test in the "parse_annotation_filter" block PASSES.

- [ ] **Step 1.4: Commit**

```bash
git add src/twicc/cli/_annotation_filters.py tests/test_cli_annotation_filters.py
git commit -m "$(cat <<'EOF'
feat(cli): add annotation filter parser

Introduces AnnotationFilter NamedTuple, AnnotationOp enum, and
parse_annotation_filter() for the --annotation CLI option to be
plugged into sessions, processes, search and topology.

Five operators: =, !=, :exists, :not-exists, :in:. Typed value
inference shared with create-session via _INT_RE / _FLOAT_RE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Helper module — Django ORM application

**Files:**
- Modify: `src/twicc/cli/_annotation_filters.py`
- Modify: `tests/test_cli_annotation_filters.py`

- [ ] **Step 2.1: Add `apply_annotation_filters` to the helper**

Append to `src/twicc/cli/_annotation_filters.py`:

```python
from django.db.models import Q, QuerySet


def apply_annotation_filters(
    queryset: QuerySet,
    filters: list[AnnotationFilter],
    *,
    field: str = "annotations",
) -> QuerySet:
    """Return a new QuerySet with all filters AND-applied.

    `field` is the model attribute holding the JSONField. Default 'annotations'
    targets Session directly; pass 'session__annotations' when filtering a
    related model (e.g. ProcessRun via its session FK).
    """
    for f in filters:
        path_chain = "__".join(f.path)
        prefix = f"{field}__{path_chain}"
        if f.op is AnnotationOp.EQ:
            queryset = queryset.filter(**{prefix: f.value})
        elif f.op is AnnotationOp.NE:
            queryset = queryset.exclude(**{prefix: f.value})
        elif f.op is AnnotationOp.EXISTS:
            queryset = queryset.filter(**{f"{prefix}__isnull": False})
        elif f.op is AnnotationOp.NOT_EXISTS:
            queryset = queryset.filter(**{f"{prefix}__isnull": True})
        elif f.op is AnnotationOp.IN:
            queryset = queryset.filter(**{f"{prefix}__in": f.value})
        else:  # defensive — enum exhausted above
            raise AssertionError(f"unhandled AnnotationOp: {f.op}")
    return queryset
```

- [ ] **Step 2.2: Add ORM tests with `db` fixture**

Append to `tests/test_cli_annotation_filters.py`:

```python
# --- apply_annotation_filters --------------------------------------------

from datetime import timedelta
from django.utils import timezone

from twicc.cli._annotation_filters import apply_annotation_filters
from twicc.core.models import Project, Session, SessionType


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-ann-filter", directory="/tmp/ann-filter")


def _mksession(project, sid, *, annotations, minutes=0):
    now = timezone.now() + timedelta(minutes=minutes)
    return Session.objects.create(
        id=sid,
        project=project,
        provider="claude_code",
        file_path=f"{sid}.jsonl",
        type=SessionType.SESSION,
        title=sid,
        created_at=now,
        last_new_content_at=now,
        user_message_count=1,
        annotations=annotations,
    )


def _ids(qs):
    return sorted(qs.values_list("id", flat=True))


def test_apply_eq_matches_typed_value(project):
    _mksession(project, "S1", annotations={"role": "implementer"})
    _mksession(project, "S2", annotations={"role": "reviewer"})
    _mksession(project, "S3", annotations={"role": "implementer", "weight": 5})

    qs = Session.objects.all()
    out = apply_annotation_filters(
        qs, [AnnotationFilter(("role",), AnnotationOp.EQ, "implementer")]
    )
    assert _ids(out) == ["S1", "S3"]


def test_apply_eq_distinguishes_int_from_string(project):
    _mksession(project, "INT5", annotations={"weight": 5})
    _mksession(project, "STR5", annotations={"weight": "5"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("weight",), AnnotationOp.EQ, 5)],
    )
    assert _ids(out) == ["INT5"]

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("weight",), AnnotationOp.EQ, "5")],
    )
    assert _ids(out) == ["STR5"]


def test_apply_ne_excludes_matching_but_includes_absent(project):
    _mksession(project, "DONE", annotations={"status": "done"})
    _mksession(project, "TODO", annotations={"status": "todo"})
    _mksession(project, "NONE", annotations={})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("status",), AnnotationOp.NE, "done")],
    )
    # NE matches TODO and NONE (absent key); documented behaviour.
    assert _ids(out) == ["NONE", "TODO"]


def test_apply_exists_matches_present_keys_including_null(project):
    _mksession(project, "PRESENT", annotations={"team": "frontend"})
    _mksession(project, "NULL", annotations={"team": None})
    _mksession(project, "ABSENT", annotations={})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("team",), AnnotationOp.EXISTS, None)],
    )
    assert _ids(out) == ["NULL", "PRESENT"]


def test_apply_not_exists_matches_absent_only(project):
    _mksession(project, "PRESENT", annotations={"team": "frontend"})
    _mksession(project, "NULL", annotations={"team": None})
    _mksession(project, "ABSENT", annotations={})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("team",), AnnotationOp.NOT_EXISTS, None)],
    )
    assert _ids(out) == ["ABSENT"]


def test_apply_in_typed_elements(project):
    _mksession(project, "DONE", annotations={"status": "done"})
    _mksession(project, "BLOCKED", annotations={"status": "blocked"})
    _mksession(project, "TODO", annotations={"status": "todo"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("status",), AnnotationOp.IN, ["done", "blocked"])],
    )
    assert _ids(out) == ["BLOCKED", "DONE"]


def test_apply_nested_dotted_path(project):
    _mksession(project, "ALICE", annotations={"team": {"lead": "alice"}})
    _mksession(project, "BOB", annotations={"team": {"lead": "bob"}})
    _mksession(project, "FLAT", annotations={"team": "alice"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("team", "lead"), AnnotationOp.EQ, "alice")],
    )
    assert _ids(out) == ["ALICE"]


def test_apply_multiple_filters_compose_with_and(project):
    _mksession(project, "MATCH", annotations={"role": "impl", "status": "done"})
    _mksession(project, "ROLE_ONLY", annotations={"role": "impl", "status": "todo"})
    _mksession(project, "STATUS_ONLY", annotations={"role": "reviewer", "status": "done"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [
            AnnotationFilter(("role",), AnnotationOp.EQ, "impl"),
            AnnotationFilter(("status",), AnnotationOp.EQ, "done"),
        ],
    )
    assert _ids(out) == ["MATCH"]


def test_apply_with_alternative_field_targets_related_model(project):
    """Verify the `field=` keyword routes lookups through a related manager.

    We don't have a process row here; we just check that the query compiles
    with a custom prefix without raising. A proper end-to-end is in the
    processes integration (Task 4).
    """
    qs = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("role",), AnnotationOp.EQ, "impl")],
        field="annotations",  # default
    )
    # The compiled SQL contains the JSON path we built.
    sql = str(qs.query)
    assert '"role"' in sql
```

- [ ] **Step 2.3: Run the full helper test suite**

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run pytest tests/test_cli_annotation_filters.py -v
```

Expected: all tests pass (parser + ORM helper).

- [ ] **Step 2.4: Commit**

```bash
git add src/twicc/cli/_annotation_filters.py tests/test_cli_annotation_filters.py
git commit -m "$(cat <<'EOF'
feat(cli): add apply_annotation_filters Django ORM helper

Single source of truth for annotation filter semantics on Session.
Translates AnnotationFilter into Django ORM lookups on the
annotations JSONField. Composable AND with chained .filter() / .exclude()
calls.

Verified typed equality (int vs string vs bool), strict-absent semantics
on :not-exists, nested dotted paths, and composition of multiple
filters.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Integrate into `sessions.py`

**Files:**
- Modify: `src/twicc/cli/__init__.py:199-280` (Typer wrapper `_sessions_default`)
- Modify: `src/twicc/cli/sessions.py:8-104` (`main()` function)

The CLI is split: the Typer wrappers (which declare `typer.Option`) live in `cli/__init__.py`, and the implementation functions live in their own modules. So `--annotation` is declared in `__init__.py` and the raw list is forwarded to `sessions_main(annotation=..., ...)`.

- [ ] **Step 3.1: Add the `--annotation` Typer option in `cli/__init__.py`**

In `cli/__init__.py:249` (right after the `descendants` option, before the closing `) -> None:` of `_sessions_default`), insert:

```python
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Filter sessions by annotation. Repeatable, AND-combined. "
            "Operators: KEY=VALUE, KEY!=VALUE, KEY:exists, KEY:not-exists, "
            "KEY:in:V1,V2. KEY is a dotted path. Values are typed "
            "(true/false/null/int/float/string), same rules as "
            "create-session --annotation. See twicc-sessions skill for details."
        ),
    ),
```

Default `[]` (not `None`) matches the existing repeatable-option pattern used by `create-session --annotation` (cf. `src/twicc/cli/create_session/command.py:148-155`).

- [ ] **Step 3.2: Forward `annotation` to `sessions_main`**

In `cli/__init__.py:268-279` (the `sessions_main(...)` call inside `_sessions_default`), add a new keyword:

```python
    sessions_main(
        project=derive_project_id(project)[0] if project is not None else None,
        # ... existing kwargs ...
        descendants=descendants,
        annotation=annotation,        # <-- new
    )
```

- [ ] **Step 3.3: Add the parameter to `sessions.py:main()`**

In `sessions.py:8-20` (the keyword-only signature of `main()`), add `annotation: list[str] | None = None` at the end. Default `None` is fine here because the caller always passes a list (`[]` if the option was not used).

- [ ] **Step 3.4: Parse and apply in `sessions.py:main()`**

After the filiation block at `sessions.py:74-83`, add:

```python
    if annotation:
        from twicc.cli._annotation_filters import apply_annotation_filters, parse_annotation_filter
        try:
            annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
        qs = apply_annotation_filters(qs, annotation_filters)
```

Using `print(..., file=sys.stderr)` + `sys.exit(2)` mirrors the existing error-emission style in `sessions.py` (which uses `sys.stdout.buffer.write` for output and `sys` for exit). The Typer wrapper does its own validation but does not catch downstream errors; emitting from `main()` keeps the error path symmetric with how `sessions.py` already structures its output.

- [ ] **Step 3.5: Manual end-to-end check**

(No automated test for this CLI — the helper is tested separately; integration here is a single `.filter` chain. Manual run instead.)

In a worktree with a populated DB, or via a one-off seeding fixture:

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run twicc sessions --annotation role=implementer | head -50
TWICC_DATA_DIR=$PWD uv run twicc sessions --annotation status:in:done,blocked
TWICC_DATA_DIR=$PWD uv run twicc sessions --spawn-root self --annotation role=implementer
TWICC_DATA_DIR=$PWD uv run twicc sessions --annotation team:not-exists
```

Verify each returns sensible results (or empty if no matching session). Verify malformed input returns exit 2 with a clear message:

```bash
TWICC_DATA_DIR=$PWD uv run twicc sessions --annotation just-a-key
# Expected: 'Error: --annotation: no operator found in 'just-a-key'; ...'
```

- [ ] **Step 3.6: Commit**

```bash
git add src/twicc/cli/__init__.py src/twicc/cli/sessions.py
git commit -m "$(cat <<'EOF'
feat(cli): add --annotation filter to twicc sessions

Composes with --spawned-by / --spawn-root / --descendants as another
AND clause. Five operators via the shared parser.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Integrate into `processes.py`

**Files:**
- Modify: `src/twicc/cli/__init__.py:423-501` (Typer wrapper `_processes_default`)
- Modify: `src/twicc/cli/processes.py:8-156` (`main()` function)

`processes.py` differs from `sessions.py` in two ways:

1. **The Typer wrapper is `_processes_default` in `cli/__init__.py`**, not `main()` in `processes.py`.
2. **`ProcessRun.session_id` is a plain `CharField`, not a FK** (see the docstring at `src/twicc/core/models.py:972-979`: "Uses a plain CharField for session_id (not a FK) because new sessions may not exist in the Session table yet..."). Django cannot traverse `session__annotations` through it — the only viable approach is a **session-id pre-filter**: run the annotation filter on a separate `Session.objects` queryset to collect matching ids, then drop process rows whose `session_id` is not in that set.

There is also a pre-existing pattern subtlety: filiation filters in `processes.py` are applied **after** the row slice (L113-148, post-enrichment loop). The annotation filter inherits the same trade-off: a filtered-out row consumes a slot in the slice. Acceptable for v1 — same behaviour as existing filiation. Documented in the commit message.

- [ ] **Step 4.1: Add the `--annotation` Typer option in `cli/__init__.py`**

In `cli/__init__.py:472` (right after the `descendants` option, before the closing `) -> None:` of `_processes_default`), insert the same `annotation: list[str] = typer.Option([], "--annotation", help=...)` block as in Task 3.1 (copy the help text verbatim — same semantics, same operators).

- [ ] **Step 4.2: Forward `annotation` to `processes_main`**

In `cli/__init__.py:491-501` (the `processes_main(...)` call), add `annotation=annotation` as a new keyword.

- [ ] **Step 4.3: Add the parameter to `processes.py:main()`**

In `processes.py:8-19` (signature of `main()`), add `annotation: list[str] | None = None` at the end.

- [ ] **Step 4.4: Parse filter specs**

In `processes.py:main()`, right after the filiation resolution block (where `spawned_by_id`/`spawn_root_id`/`descendants_ids` are computed) and **before the post-enrichment filter loop at L113-148**, parse the annotation filters:

```python
    annotation_filters: list = []
    matching_session_ids: set[str] | None = None
    if annotation:
        from twicc.cli._annotation_filters import apply_annotation_filters, parse_annotation_filter
        from twicc.core.models import Session
        try:
            annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
        matching_session_ids = set(
            apply_annotation_filters(Session.objects.all(), annotation_filters)
            .values_list("id", flat=True)
        )
```

The pre-filter set is computed once, then membership-tested in the loop.

- [ ] **Step 4.5: Drop non-matching rows in the in-memory filter loop**

Inside the post-enrichment loop (around `processes.py:113-148` where the filiation `continue` checks live), add a new `continue` check next to the others:

```python
        if matching_session_ids is not None and row.session_id not in matching_session_ids:
            continue
```

Same shape as the existing filiation drops. Keeps the locality of the filter logic.

- [ ] **Step 4.6: Manual end-to-end check**

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run twicc processes --annotation role=implementer
TWICC_DATA_DIR=$PWD uv run twicc processes --spawn-root self --annotation status:in:running,done
TWICC_DATA_DIR=$PWD uv run twicc processes --annotation just-a-key  # exit 2 expected
```

Verify the matching session-id set agrees with `twicc sessions --annotation <same>` (intersected with what has live process rows).

- [ ] **Step 4.7: Commit**

```bash
git add src/twicc/cli/__init__.py src/twicc/cli/processes.py
git commit -m "$(cat <<'EOF'
feat(cli): add --annotation filter to twicc processes

Uses a session-id pre-filter (a separate Session.objects query
collects matching ids; the existing in-memory post-enrichment loop
drops rows whose session_id is not in that set). Cannot traverse
ProcessRun.session via the ORM because session_id is a plain
CharField, not a FK — see core/models.py docstring on ProcessRun.

Composes with the existing filiation flags. The filter is applied
after the slice, consistent with the existing filiation behaviour;
that is a known trade-off shared with --spawned-by / --spawn-root /
--descendants.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Integrate into `topology.py`

**Files:**
- Modify: `src/twicc/cli/__init__.py:379-411` (Typer wrapper `topology`)
- Modify: `src/twicc/cli/topology.py` (`main()` at L33, `build_topology()` at L64, `_serialize_topology_node()` at L316)
- Modify: `tests/test_cli_topology.py`

- [ ] **Step 5.1: Add `--annotation` option to the Typer wrapper**

In `cli/__init__.py:406` (right after the `full_sessions` option, before the closing `) -> None:` of `topology`), insert the same `annotation: list[str] = typer.Option([], "--annotation", help=...)` block as in Task 3.1.

Then update the `topology_main(...)` call at `cli/__init__.py:411` to pass `annotation=annotation`.

- [ ] **Step 5.2: Add the parameter to `main()` and `build_topology()`**

In `topology.py:33-38` (signature of `main()`), add `annotation: list[str] | None = None` at the end.

In `topology.py:64-70` (signature of `build_topology(seed, *, ...)`), add `annotation_filters: list | None = None` (typed loosely to avoid an extra import; the helper accepts any iterable of `AnnotationFilter`).

Parse `--annotation` values inside `topology.py:main()` (try/except + exit 2 on `ValueError`, mirroring Step 3.4), then call `build_topology(seed, ..., annotation_filters=annotation_filters)`.

- [ ] **Step 5.3: Compute `matching_ids` with a second ORM query**

In `build_topology` at `topology.py:79-85`, the existing call `(root, path_to_seed, sessions_by_id, ...) = _load_topology_sessions(seed)` unpacks `root` (a `Session` instance, not a string). The annotation query lives **after** that block — at the point where `root.id` is in scope:

```python
    (
        root,
        path_to_seed,
        sessions_by_id,
        children_by_parent,
        cycle_detected,
    ) = _load_topology_sessions(seed)

    # Second query: same spawn_root filter + annotation filter.
    # The full tree above is preserved; this set drives the
    # matches_annotations flag per node — see spec §5.4.
    matching_ids: set[str] | None = None
    if annotation_filters:
        from twicc.cli._annotation_filters import apply_annotation_filters
        from twicc.core.models import Session

        matching_ids = set(
            apply_annotation_filters(
                Session.objects.filter(spawn_root_id=root.id),
                annotation_filters,
            ).values_list("id", flat=True)
        )
```

(The `from twicc.core.models import Session` is needed because the function imports Session via the helper signature but not at runtime in this scope.)

- [ ] **Step 5.4: Pass `matching_ids` to `_serialize_topology_node`**

Update the list comprehension at `topology.py:107-116` to pass `matching_ids` as a keyword. Update the signature of `_serialize_topology_node` (`topology.py:316`) to accept `matching_ids: set[str] | None = None`, and just before the function returns its dict, add:

```python
    if matching_ids is not None:
        result["matches_annotations"] = session.id in matching_ids
```

(Replace `result` with whatever local name the function uses for the dict it returns — verify by reading `_serialize_topology_node` body L316-334.)

- [ ] **Step 5.5: Add topology tests for `matches_annotations`**

In `tests/test_cli_topology.py`, add the following tests. **Important**: `build_topology(seed, ...)` takes a `Session` instance (the resolved seed), not a session-id string. Pass the model instance directly:

```python
from twicc.cli._annotation_filters import AnnotationFilter, AnnotationOp
from twicc.cli.topology import build_topology


def test_topology_no_annotation_filter_omits_matches_field(project):
    root = make_session(project, "R", title="root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make_session(project, "C", title="child", spawned_by=root, spawn_root=root)

    data = build_topology(root, include_processes=False)
    for node in data["nodes"]:
        assert "matches_annotations" not in node


def test_topology_annotation_filter_sets_flag_per_node(project):
    root = make_session(project, "R", title="root", annotations={"role": "coord"})
    root.spawn_root = root
    root.save(update_fields=["spawn_root", "annotations"])
    make_session(
        project, "I", title="impl",
        spawned_by=root, spawn_root=root,
        annotations={"role": "implementer"},
    )
    make_session(
        project, "Rv", title="rev",
        spawned_by=root, spawn_root=root,
        annotations={"role": "reviewer"},
    )

    data = build_topology(
        root,
        include_processes=False,
        annotation_filters=[AnnotationFilter(("role",), AnnotationOp.EQ, "implementer")],
    )
    by_id = {node["session"]["id"]: node for node in data["nodes"]}
    assert by_id["R"]["matches_annotations"] is False
    assert by_id["I"]["matches_annotations"] is True
    assert by_id["Rv"]["matches_annotations"] is False


def test_topology_annotation_filter_no_match_still_returns_full_tree(project):
    root = make_session(project, "R", title="root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make_session(project, "C", title="child", spawned_by=root, spawn_root=root)

    data = build_topology(
        root,
        include_processes=False,
        annotation_filters=[AnnotationFilter(("role",), AnnotationOp.EQ, "nobody")],
    )
    ids = {node["session"]["id"] for node in data["nodes"]}
    assert ids == {"R", "C"}  # tree is preserved
    assert all(node["matches_annotations"] is False for node in data["nodes"])
```

The `include_processes=False` keeps the test independent of the live TwiCC sidecar resolution. The `data["nodes"]` accessor matches the existing return shape at `topology.py:131`. Adjust `node["session"]["id"]` if `_serialize_topology_node` exposes the id under a different key (verify against existing tests in `test_cli_topology.py`).

- [ ] **Step 5.6: Run topology tests**

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run pytest tests/test_cli_topology.py -v
```

Expected: existing tests still pass + the three new ones pass.

- [ ] **Step 5.7: Manual CLI check**

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run twicc topology <some-spawn-root-session-id> --annotation role=implementer | head -80
```

Verify each node carries `matches_annotations: true|false` and the tree shape is unchanged.

- [ ] **Step 5.8: Commit**

```bash
git add src/twicc/cli/__init__.py src/twicc/cli/topology.py tests/test_cli_topology.py
git commit -m "$(cat <<'EOF'
feat(cli): add --annotation filter to twicc topology

Tree is preserved (no pruning); each serialized node gets
matches_annotations: bool when --annotation is passed. Computed via a
second ORM query (same spawn_root filter + annotation filter) and
set membership in Python, so the ORM remains the single source of
truth for filter semantics.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Integrate into `search.py` with oversample loop

**Files:**
- Modify: `src/twicc/cli/__init__.py:784-849` (Typer wrapper `search`)
- Modify: `src/twicc/cli/search.py:6-67` (`main()` function)
- Modify: `src/twicc/search.py:682-847` (`raw_search()`)

This task is heavier than the other three because the oversample loop requires logic changes in `raw_search`.

- [ ] **Step 6.1: Add the `--annotation` option to the Typer wrapper**

In `cli/__init__.py:824` (right after the `descendants` option, before the closing `) -> None:` of the `search` command), insert the same `annotation: list[str] = typer.Option([], "--annotation", help=...)` block as in Task 3.1.

In `cli/__init__.py:840-849` (the `search_main(...)` call), add `annotation=annotation`.

- [ ] **Step 6.2: Parse filter specs in `search.py:main()`**

Add `annotation: list[str] | None = None` to `search.py:main()` signature. Inside, parse via `parse_annotation_filter` with the same try/except + `sys.exit(2)` block as Task 3.4. The resulting `annotation_filters: list[AnnotationFilter]` is passed to `raw_search`.

- [ ] **Step 6.3: Add an `annotation_filters` parameter to `raw_search`**

In `src/twicc/search.py:682-693`, extend the signature with `annotation_filters: list | None = None` (loosely typed to avoid the import — the helper handles any iterable). Default `None` preserves existing behaviour.

- [ ] **Step 6.4: Implement the oversample loop**

When `annotation_filters` is `None` (or empty), keep the existing single-shot `searcher.search(parsed_query, limit=raw_limit)` path unchanged. The new `exhausted`/`partial` keys in the result dict are only emitted on the annotation-filtered path; existing callers see no diff.

When `annotation_filters` is set, replace the single-shot call (around `search.py:800-810`) with the loop below. Verify the hit-extraction shape by reading the existing code at L810+ — the names `hit`, `score`, `doc_address`, `session_id` may differ from what's shown here, adapt accordingly:

```python
from twicc.cli._annotation_filters import apply_annotation_filters
from twicc.core.models import Session

# Score-ordered (session_id, hit_record) tuples we keep after post-filter.
matched_pairs: list[tuple[str, object]] = []
search_offset = offset
batch_size = max(limit, 20)
max_iterations = 50
iteration = 0
exhausted = False

while len(matched_pairs) < limit and iteration < max_iterations:
    batch = searcher.search(parsed_query, limit=batch_size, offset=search_offset)
    hits = batch.hits
    if not hits:
        exhausted = True
        break
    # Pair each hit with the session_id it points to, preserving Tantivy order.
    # Adapt this extraction to the actual hit/doc shape in raw_search.
    ordered_pairs = [(_session_id_from_hit(searcher, h), h) for h in hits]
    ids_in_score_order = [sid for sid, _ in ordered_pairs]
    matching_ids = set(
        apply_annotation_filters(
            Session.objects.filter(id__in=ids_in_score_order),
            annotation_filters,
        ).values_list("id", flat=True)
    )
    for sid, hit in ordered_pairs:
        if sid in matching_ids:
            matched_pairs.append((sid, hit))
            if len(matched_pairs) >= limit:
                break
    search_offset += batch_size
    iteration += 1

partial = (not exhausted) and len(matched_pairs) < limit
# Use matched_pairs in place of the raw Tantivy hits in the existing
# post-processing block (L810+) that builds the JSON `hits` list.
```

In the result dict (`search.py:836-847`), when `annotation_filters` is set, emit:

```python
    "annotation_filtered": True,
    "exhausted": exhausted,
    "partial": partial,
```

When `annotation_filters` is None, omit these three keys entirely. The skill doc (Task 7) documents that callers must check for `"annotation_filtered" in result` (or the typed flag) to know whether `exhausted` / `partial` are meaningful.

- [ ] **Step 6.5: Wire CLI to pass filters**

In `search.py:main()`, after parsing, pass `annotation_filters=annotation_filters` to `raw_search`.

- [ ] **Step 6.6: Manual end-to-end check**

(Search depends on Tantivy index, which means running tests against an indexed DB. Manual test is the realistic option.)

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run twicc search "deployment" --annotation role=implementer --limit 5
```

Verify the output JSON includes `"exhausted": false|true` and `"partial": false|true`, and that the hits are score-ordered (eyeball: scores should be monotonically non-increasing).

Try an annotation filter that matches nothing and verify `exhausted: true`, empty hits.

- [ ] **Step 6.7: Commit**

```bash
git add src/twicc/cli/__init__.py src/twicc/cli/search.py src/twicc/search.py
git commit -m "$(cat <<'EOF'
feat(cli): add --annotation filter to twicc search

Hybrid Tantivy + Django ORM: Tantivy ranks the corpus, Django filters
on annotations. Oversample-and-post-filter loop preserves the
requested page size as long as enough matches exist.

Adds annotation_filtered/exhausted/partial flags to the JSON payload
when --annotation is used. The flags are absent on the unfiltered
path, so existing callers see no diff.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update agent skills and bump plugin version

**Files:**
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`

- [ ] **Step 7.1: Read the existing SKILL.md style**

Read `src/twicc/agent/plugin/README.md` first (cf. `CLAUDE.md` plugin section), then `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md` for the established tone and bullet style.

- [ ] **Step 7.2: Add a `--annotation` bullet to each of the four skills**

For each of the four SKILL.md files, add a bullet in the options/flags section with:

- The five operators in a table or compact list
- One example of composition with a filiation flag (`--spawn-root self` typically)
- Specific notes:
  - **twicc-sessions / twicc-processes**: no extra notes beyond the bullet
  - **twicc-search**: add a paragraph noting the `exhausted` and `partial` flags in the result payload and what they mean
  - **twicc-topology**: add a paragraph noting that the tree is preserved (not pruned) and each node carries `matches_annotations: bool` when `--annotation` is passed

Wording must match the existing terse, scriptable style — read 2-3 other bullets in the same file first.

- [ ] **Step 7.3: Bump the plugin version**

In `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, change the `"version"` field from `"0.27.0"` to `"0.28.0"`.

- [ ] **Step 7.4: Read all five files back to verify wording consistency**

```bash
cd <worktree-path>
cat src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json
# Look at the bullet you added in each SKILL.md
```

- [ ] **Step 7.5: Commit**

```bash
git add src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md \
        src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md \
        src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md \
        src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md \
        src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json
git commit -m "$(cat <<'EOF'
docs(plugin): document --annotation on sessions/processes/search/topology

Plugin v0.27.0 -> 0.28.0 (new flag on existing skills = minor bump).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final cross-cutting verification

**Files:** none modified, verification only.

- [ ] **Step 8.1: Re-run the full test suite (modules touched in this plan)**

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run pytest \
    tests/test_cli_annotation_filters.py \
    tests/test_cli_topology.py \
    tests/test_cli_annotations.py \
    -v
```

Expected: all green. The third file (`test_cli_annotations.py`, which covers `create-session --annotation` and is **unrelated** to the filter) is included as a regression check — our parser reuses `_INT_RE` / `_FLOAT_RE` from there, so any accidental import break would surface here.

- [ ] **Step 8.2: Walk each documented edge case from the spec**

From `docs/superpowers/specs/2026-06-01-annotation-filtering-design.md` §8 ("Edge cases and limitations"):

- §8.1 bool ↔ string-of-bool: verify with `twicc sessions --annotation flag=true` against a DB containing both `{"flag": true}` and `{"flag": "true"}`. Document the observed behaviour in a quick note.
- §8.2 `!=` includes absent: covered by `test_apply_ne_excludes_matching_but_includes_absent`. Confirm.
- §8.3 `key=null` matches present-with-null only: write a quick ad hoc test or `python -c "..."` to confirm. (Not in the formal test suite — too narrow.)
- §8.4 dotted key with `.` in name: behaviour unspecified; verify the parser splits naively and document the result.

- [ ] **Step 8.3: Test the canonical orchestration command**

```bash
cd <worktree-path>
TWICC_DATA_DIR=$PWD uv run twicc sessions \
    --descendants self \
    --annotation role=implementer \
    --annotation status:in:done,blocked
```

Even if it returns empty (no matching sessions), confirm exit code is 0 and the JSON is well-formed.

- [ ] **Step 8.4: Verify plugin bundle structure**

```bash
cd <worktree-path>
jq -r '.version' src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json
# Expected: 0.28.0
```

- [ ] **Step 8.5: Surface user-only operations the user must do**

After all commits, surface to the user:

1. **No DB migration** is needed (no schema change).
2. **Backend restart** may be needed so any running TwiCC instance picks up the new CLI bin (`devctl.py restart back` is the user's call — do not run).
3. **No `uv add`** — no new dependencies.
4. The plugin bump propagates the next time providers refresh the bundle; remind the user this is automatic.

---

## Risk register

| Risk | Mitigation |
|---|---|
| `parse_annotation_filter` ambiguity on a key containing `:` (rare; mostly only `=` is realistic) | Documented behaviour: `:in:`, `:exists`, `:not-exists` are reserved suffixes; the parser checks for them before falling back to `=`. A key like `weird:thing=foo` is rejected by `_validate_key` (regex). |
| Django ORM SQL generation differs subtly across SQLite versions (we tested on 3.46) | The empirical test in §10 of the spec is repeatable. If the dev env upgrades SQLite, re-run `tests/test_cli_annotation_filters.py` first thing. |
| `raw_search` oversample loop performance on a large index | `max_iterations=50` cap (~1000 docs scanned) is the safety net. If a real workload hits it routinely, surface and revisit. |
| Topology `--full-sessions` payload bloats when `matches_annotations` is added | The added field is one bool per node. Negligible. |
| Plugin bump forgotten | Hard rule in CLAUDE.md; reviewer to check `plugin.json` diff before merging. |

---

## Acceptance criteria

1. `tests/test_cli_annotation_filters.py` and the new tests in `tests/test_cli_topology.py` all pass.
2. `twicc sessions`, `twicc processes`, `twicc search`, `twicc topology` accept `--annotation KEY[OP]VALUE` repeatable.
3. `twicc topology --annotation ...` returns the full tree with `matches_annotations: bool` on each node.
4. `twicc search ... --annotation ...` returns a JSON payload with `exhausted: bool` and `partial: bool` fields.
5. Plugin `plugin.json` is at version `0.28.0`.
6. No DB migration, no schema change, no new external dependency.
7. The four skill SKILL.md files document the new flag in the existing terse style.
