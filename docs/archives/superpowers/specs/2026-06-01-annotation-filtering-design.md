# Annotation Filtering on TwiCC CLI

**Status:** Draft
**Date:** 2026-06-01
**Author:** Twidi (with Claude)
**Plugin version target:** 0.28.0 (minor bump — new options on existing skills)

## 1. Goal

Allow filtering by session `annotations` (an arbitrary `JSONField`) on the four read-only CLI surfaces that already expose query semantics:

- `twicc sessions` (list)
- `twicc processes` (list)
- `twicc search` (full-text)
- `twicc topology` (tree view)

Annotations are user-defined structured metadata set at `create-session` time (`--annotation key=value`) or mutated via `update-session annotations`. Today they are stored, broadcast and injected into the agent system prompt, but **no command can filter on them**. This change closes that gap and unlocks the canonical orchestration pattern:

```
twicc sessions --descendants self --annotation role=implementer --annotation status:in:done,blocked
```

## 2. Out of scope

These are explicitly **not** addressed by this design and are deferred to follow-up specs:

- **Wait on annotation change** (e.g. `twicc sessions wait --annotation role=implementer --target status=done`). This is the orchestration primitive that motivated the broader exploration, but it introduces an event/transition layer that deserves its own design. Filtering ships first and stabilises the syntax; wait reuses it.
- **REST / WebSocket exposure** of the same filter (frontend Pinia store filtering). YAGNI for v1.
- **Filtering on `sessions get` (batch lookup by id)**: caller already supplies the ids, no read-only filter is meaningful there.
- **Filtering on Tantivy-indexed annotations**: we use a hybrid Tantivy → Django post-filter instead (see §6.3). No change to Tantivy schema. No reindexing on annotation updates.

## 3. Context

### 3.1 Annotations today

- `Session.annotations = models.JSONField(default=dict, blank=True)` (`src/twicc/core/models.py:386`). Never NULL — empty dict is the default.
- CLI write paths:
  - `create-session --annotation KEY=VALUE` repeatable, dotted-path keys, scalar values (`true`/`false`/`null`/int/float/string inferred via `parse_annotations`). Plus `--annotations-file PATH`.
  - `update-session annotations` accepts ordered ops: `clear`, `replace-file:PATH`, `merge-file:PATH`, `set:KEY=VALUE`, `unset:KEY`.
- Annotations are serialized into every session payload (`src/twicc/core/serializers.py:107`), broadcast on `session_updated`, and injected into the agent system prompt (`src/twicc/agent/.../system_prompt.py:269`). No code path filters on them.

### 3.2 Existing filiation filters (relevant for composition)

The three filiation flags shipped recently — `--spawned-by` (direct children only), `--descendants` (transitive subtree, target excluded), `--spawn-root` (full tree, root included) — are mutually exclusive on each CLI and all three lift the implicit `hidden=False` default. Each resolves to either an integer id (`spawn_root_id`, `spawned_by_id`) or an id list (`descendants_ids`) and is applied as a plain `qs.filter(...)` clause in the existing filiation filter block (cf. `src/twicc/cli/sessions.py:74-83`; the equivalent block is duplicated in `processes.py` and `search.py`). The annotation filter composes with these as another `.filter(...)` clause; no special handling is required.

### 3.3 Empirical findings (SQLite JSON1 + Django ORM)

Empirical test (see §10) confirms that **Django 6 + SQLite 3.46 JSONField lookups handle typed comparisons correctly without explicit type prefixes**. Django generates:

```sql
CASE WHEN JSON_TYPE(ann, '$."k"') IN ('false','true','null')
     THEN JSON_TYPE(ann, '$."k"')
     ELSE JSON_EXTRACT(ann, '$."k"')
END = JSON_EXTRACT(?, '$')
```

Behaviour:

| Python value passed to ORM | Matches in JSON | Does NOT match |
|---|---|---|
| `int 5` | int `5` | string `"5"` |
| `str "5"` | string `"5"` | int `5` |
| `float 1.5` | float `1.5` | string `"1.5"` |
| `None` | `null` explicit | absent key |
| `int 1` | int `1` | bool `true` |
| `int 0` | int `0` | bool `false` |
| `True` | bool `true` **AND** string `"true"` ⚠️ | int `1`, others |
| `False` | bool `false` **AND** string `"false"` ⚠️ | int `0`, others |

The `True ↔ "true"` collision is an acknowledged Django ORM quirk on SQLite JSONField. It is accepted as-is: storing both bool and string-of-bool in the same annotation key is not a realistic pattern. Documented in §8.1.

Additional verified lookups:

- `annotations__has_key="team"` → present (any value, including `null`)
- `annotations__team__isnull=True` → **strictly absent** (distinct from `value is null`)
- `annotations__team__isnull=False` → present (synonym of `__has_key` at top level, but also works for nested paths)
- `annotations__team__lead="alice"` → nested via `__`
- `annotations__role__in=["impl","reviewer"]` → IN typed
- `exclude(annotations__role="reviewer")` → negation, but **also matches sessions where the key is absent** (documented edge case)
- `__contains` is `NotSupportedError` on SQLite — not used; AND of `__exact` covers every case.

## 4. Filter syntax

### 4.1 CLI shape

```
--annotation KEY[OP]VALUE
```

`--annotation` is a repeatable Typer option. **AND** is implicit between all `--annotation` flags within the same invocation.

### 4.2 Operators

| Operator | Example | Semantics |
|---|---|---|
| `=` | `role=implementer` | Equality with typed value (see §4.4) |
| `!=` | `status!=done` | Inequality; sessions with absent key match (documented in §8.2) |
| `:exists` | `team:exists` | Key present at the dotted path (value can be `null`) |
| `:not-exists` | `team:not-exists` | Key absent at the dotted path (strict — value `null` does **not** count as absent) |
| `:in:` | `status:in:done,blocked,cancelled` | Equality against a comma-separated list (typed per element) |

The parser does the minimum needed to translate a spec into a Django expression. It checks that the operator is one of the five listed above (otherwise `ValueError`) and that the key part is non-empty (otherwise `ValueError`). Beyond that, it does **not**:

- validate the key against any schema (no introspection of what keys real sessions have stored),
- validate the value (e.g. that `weight=foo` is sensible for a numeric annotation),
- check consistency between repeated `--annotation` flags — if the caller passes `team:exists` and `team:not-exists` together, both clauses are AND-applied and the query returns nothing, which is the caller's problem,
- warn on "weird" inputs.

If the resulting ORM query matches nothing because the user composed contradicting clauses or filtered on a key no session ever stored, the caller gets an empty result — that is the signal, no error is raised.

### 4.3 Key — dotted path

Same convention as `create-session --annotation` and `update-session set:`:

```
team.lead.name=alice    →    {"team": {"lead": {"name": "alice"}}}
```

A segment containing `.` is not supported (no escaping in v1). This mirrors the existing write-side convention.

The path is translated to Django ORM lookup syntax by splitting on `.`:

```
team.lead.name=alice   →   filter(annotations__team__lead__name="alice")
```

### 4.4 Value — typed inference

Reuses the existing `parse_annotations` inference (cf. `src/twicc/cli/_drop_request/annotations.py:20-46`):

1. `true` / `false` → Python `bool`
2. `null` → Python `None`
3. Decimal integer → Python `int`
4. Float literal → Python `float`
5. Otherwise → string (raw text)

No type prefix syntax (`s:`, `i:`, etc.). Symmetry with `create-session` is preserved.

For `:in:`, each comma-separated element goes through the same inference independently. Example: `status:in:5,done,true` → `[5, "done", True]`.

### 4.5 Escaping in `:in:`

Separator: comma. Escape a literal comma with `\,`. Escape a literal backslash with `\\`. The escaping rules **apply only inside `:in:` value lists**; `=` and `!=` values are taken verbatim (no comma is special there). Documented but not foregrounded — expected to be a rare need.

### 4.6 Interaction with `hidden`

`--annotation` does **not** lift the implicit `hidden=False` default. Different from `--spawned-by` / `--spawn-root` / `--descendants`, which all lift it (filiation = "show me the whole tree").

Rationale: annotation filters are an orthogonal refinement on the visible view. To include hidden sessions, the caller combines `--annotation` with `--include-hidden`.

### 4.7 Composition with filiation

All filters compose in AND. The canonical orchestration pattern:

```
twicc sessions --descendants self \
               --annotation role=implementer \
               --annotation status:in:done,blocked
```

Equivalent ORM:

```python
Session.objects.filter(id__in=descendants_ids) \
               .filter(annotations__role="implementer") \
               .filter(annotations__status__in=["done", "blocked"])
```

## 5. Where the filter applies

### 5.1 `twicc sessions` (list)

Add `--annotation` repeatable option. Parse, apply via `apply_annotation_filters(qs, filters)` after the existing filiation block in `sessions.py` (around `sessions.py:74-83`). No other change.

### 5.2 `twicc processes` (list)

Same as sessions. The process queryset joins through `Session`, so the filter targets `session__annotations__...` (lookup chain stays consistent). Apply in the same position as filiation filters.

### 5.3 `twicc search`

Hybrid Tantivy → Django filter, with paginated oversample (see §6.3). Tantivy schema is untouched; no reindexing on annotation mutations.

### 5.4 `twicc topology`

The arbre is loaded by the existing query (`Session.objects.filter(spawn_root_id=...)`); the structure is preserved (tree is not pruned). When `--annotation` is passed, a **second query** runs the same spawn-root filter plus the annotation filter, returning the set of matching ids:

```python
all_nodes = Session.objects.filter(spawn_root_id=root_id)            # query 1, unchanged
if annotation_filters:
    matching_ids = set(
        apply_annotation_filters(
            Session.objects.filter(spawn_root_id=root_id),
            annotation_filters,
        ).values_list("id", flat=True)
    )                                                                  # query 2
    for node in serialized_nodes:
        node["matches_annotations"] = node["id"] in matching_ids
```

**Why two SQL queries instead of an in-memory Python matcher**: the ORM is the only source of truth for filter semantics. Reimplementing the same matching rules in Python would create two implementations to keep in sync, and would diverge on subtle edge cases (notably Django's `bool ↔ "true"/"false"` quirk on JSON fields, §8.1). The cost is one extra round-trip on an already-indexed `spawn_root_id` query — negligible compared to the tree fetch itself.

**Enrichment lives in the topology CLI layer**, not in `serialize_session()` (`src/twicc/core/serializers.py`). The `matches_annotations` field is appended to each node's dict **after** serialization, so other consumers of `serialize_session` (REST, WS broadcasts, other CLIs) are unaffected.

If no `--annotation` is passed, the field is omitted (no behaviour change to existing payloads). If `--annotation` is passed, both the slim 12-field payload and the full payload (`--full-sessions`) carry `matches_annotations: bool`.

The tree is **not** filtered — every node is returned, so the caller (agent or human) can locate the matches in context. To get a flat filtered list instead, use `twicc sessions --spawn-root <id> --annotation ...`.

## 6. Implementation

### 6.1 Module

`src/twicc/cli/_annotation_filters.py` (private CLI helper, follows the `_xxx.py` convention of `_process_state.py`, `_drop_request/`).

### 6.2 Public API

```python
from enum import Enum
from typing import Any, NamedTuple

from django.db.models import QuerySet


class AnnotationOp(str, Enum):
    EQ = "eq"
    NE = "ne"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    IN = "in"


class AnnotationFilter(NamedTuple):
    path: tuple[str, ...]   # ("team", "lead")
    op: AnnotationOp
    value: Any              # None | int | float | bool | str | list[Any]; ignored for EXISTS/NOT_EXISTS


def parse_annotation_filter(spec: str) -> AnnotationFilter:
    """Parse one CLI '--annotation' value into a typed AnnotationFilter.

    Raises ValueError on malformed input (empty path, unknown operator,
    empty :in: list, etc.). The message must name the offending token.
    """


def apply_annotation_filters(
    queryset: QuerySet,
    filters: list[AnnotationFilter],
    *,
    field: str = "annotations",
) -> QuerySet:
    """Return a new QuerySet with all filters AND-applied.

    `field` is the model attribute that holds the JSONField. Default
    'annotations' covers Session; passing 'session__annotations' covers
    Process and any other related model.
    """
```

### 6.3 Search pagination algorithm

Goal: respect the requested page size N while filtering out non-matching annotations after Tantivy.

```python
def paginated_search_with_annotations(
    query: TantivyQuery,
    annotation_filters: list[AnnotationFilter],
    requested: int,                # N from the caller
    *,
    batch_size: int = None,        # defaults to max(requested, 20)
    max_iterations: int = 50,
) -> SearchResult:
    """
    Returns:
        SearchResult(
            sessions: list[Session],   # at most `requested`, score-ordered
            exhausted: bool,           # Tantivy returned no more rows
            partial: bool,             # max_iterations hit before requested filled
        )
    """
    batch = batch_size or max(requested, 20)
    results = []
    offset = 0
    exhausted = False

    for iteration in range(max_iterations):
        if len(results) >= requested:
            break
        hits = tantivy.search(query, offset=offset, limit=batch)
        if not hits:
            exhausted = True
            break
        ids_in_score_order = [h.session_id for h in hits]
        qs = Session.objects.filter(id__in=ids_in_score_order)
        qs = apply_annotation_filters(qs, annotation_filters)
        by_id = {s.id: s for s in qs}
        for sid in ids_in_score_order:
            if sid in by_id:
                results.append(by_id[sid])
                if len(results) >= requested:
                    break
        offset += batch

    partial = not exhausted and len(results) < requested
    return SearchResult(
        sessions=results[:requested],
        exhausted=exhausted,
        partial=partial,
    )
```

Notes:
- Tantivy hits are score-ordered. Django's `filter(id__in=[...])` returns rows in primary-key order, not in the order of the input list. The algorithm above sidesteps this by iterating over the Tantivy-ordered id list and appending each id whose Session is in the post-filter set (`by_id`). The score order is preserved by construction; no sort step is needed.
- `exhausted` and `partial` flags are surfaced in the CLI JSON payload so the caller can detect a truncated result.
- `max_iterations=50` × `batch_size=20` = 1000 sessions scanned before giving up. Adjustable per command if needed.

**Flag semantics (precise)**:
- `exhausted=true` ↔ Tantivy returned zero hits on the last batch (`hits == []`). It means the corpus has no more candidates for this query at all. If `len(results) < requested` with `exhausted=true`, the returned list is **complete**: there is nothing more to find.
- `partial=true` ↔ `exhausted=false` AND `len(results) < requested`. It means Tantivy had more candidates available, but we stopped after `max_iterations` batches without filling the page. The caller may have missed valid matches that lie further in the score-ordered tail.
- Both flags `false` together with `len(results) == requested` means the page is full and Tantivy may or may not have more candidates — re-paginate with an offset to continue.

### 6.4 Translation table — AnnotationFilter to Django

| AnnotationFilter | Django expression |
|---|---|
| `AnnotationFilter(("role",), EQ, "implementer")` | `qs.filter(annotations__role="implementer")` |
| `AnnotationFilter(("team","lead"), EQ, "alice")` | `qs.filter(annotations__team__lead="alice")` |
| `AnnotationFilter(("status",), NE, "done")` | `qs.exclude(annotations__status="done")` |
| `AnnotationFilter(("team",), EXISTS, None)` | `qs.filter(annotations__team__isnull=False)` |
| `AnnotationFilter(("team",), NOT_EXISTS, None)` | `qs.filter(annotations__team__isnull=True)` |
| `AnnotationFilter(("status",), IN, ["done","blocked"])` | `qs.filter(annotations__status__in=["done","blocked"])` |

For nested paths the same suffix lookup is used (`__isnull=False/True`, `__in=[...]`, `__exact=...`). Confirmed working on SQLite JSON1 (see test step 3 and 7).

### 6.5 No in-memory matcher

Earlier drafts of this spec exposed a `match_annotation_filters_in_memory(annotations, filters)` helper alongside `apply_annotation_filters`. It is **removed**: the ORM is the only source of truth for filter semantics. Topology computes `matches_annotations` via the two-query approach in §5.4. The single helper `apply_annotation_filters` is everything any caller needs, and there is no second implementation to keep in sync with the ORM's edge cases (§8.1).

### 6.6 Per-command integration

| File | Change |
|---|---|
| `src/twicc/cli/_annotation_filters.py` | New module (see §6.2) |
| `src/twicc/cli/sessions.py` | Add `--annotation` Typer option. **Typer idiom** for repeatable options in this codebase: `annotations: list[str] = typer.Option(None, "--annotation", help=...)` — Typer infers multiplicity from the `list[str]` annotation, no `multiple=True` flag needed. Parse all values; call `apply_annotation_filters(qs, filters)` after filiation. |
| `src/twicc/cli/processes.py` | Same as sessions, but `field="session__annotations"` |
| `src/twicc/cli/search.py` | Replace single-shot Tantivy call with `paginated_search_with_annotations`. Add `exhausted` and `partial` to the JSON output. |
| `src/twicc/cli/topology.py` | Parse `--annotation`. After tree load, run the second ORM query (§5.4) to collect the matching id set, then enrich each serialized node with `matches_annotations: bool` by set membership. |
| `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md` | Document `--annotation` semantics, operators, examples |
| `src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md` | Same |
| `src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md` | Same + note on `exhausted`/`partial` semantics |
| `src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md` | Document `--annotation` + `matches_annotations` field |
| `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` | Version bump 0.27.0 → 0.28.0 (minor — new options on existing skills) |
| `CLAUDE.md` and `AGENTS.md` | Mention the new filter capability if a section already lists CLI behaviour (verify; otherwise no change) |

### 6.7 Error handling

The parser is opinionated about syntax but agnostic about semantics. It reports the bare minimum needed to construct a Django expression:

- Cannot split into key + operator + value at all (e.g. `--annotation foo` with no `=`, `!=`, `:exists`, etc.) → `ValueError`.
- Unknown operator suffix after `:` (e.g. `key:foobar`) → `ValueError` listing the five supported ones.
- Empty key (e.g. `=value` or `:exists`) → `ValueError`.
- `:in:` with empty list (`--annotation status:in:`) → `ValueError`.

Everything else flows straight through to Django:

- Conflicting filters across `--annotation` flags (e.g. `team:exists` + `team:not-exists`) → not detected; both clauses are AND-applied, Django returns zero rows.
- Filter on a key no session has → not detected; Django returns zero rows.
- Value type that does not match any stored value → not detected; Django returns zero rows.

`ValueError` is caught at the Typer entry point and converted to a clear CLI error message with non-zero exit code (mirror what `create-session --annotation` does today). All four CLIs use the same error formatter.

## 7. Skills documentation

Each updated SKILL.md must:

- Add a `--annotation KEY[OP]VALUE` bullet with operator table and one example showing composition with the relevant filiation flag.
- For `twicc-search`: document that the result includes `exhausted` and `partial` flags. Recommend the caller checks `partial` to detect truncation.
- For `twicc-topology`: document the new `matches_annotations` field per node and that the tree is **not** pruned by the filter.
- Reuse the existing wording style of the bundle (terse, scriptable, no narrative).

Plugin version bump: `0.27.0 → 0.28.0`. Justified by the plugin bump rule in `CLAUDE.md` ("new flag on existing skill = minor bump").

## 8. Edge cases and limitations

### 8.1 Bool ↔ string-of-bool collision (Django ORM on SQLite)

```
filter(annotations__active=True)   # matches {"active": true} AND {"active": "true"}
filter(annotations__active=False)  # matches {"active": false} AND {"active": "false"}
```

Documented in skill docs as a caveat for orchestrators that might want to discriminate. Workaround: rename one of the two semantic keys.

Since topology uses the same ORM query (§5.4), this quirk applies uniformly across all four commands — there is no second matcher to diverge from.

### 8.2 `!=` includes absent

```
filter() with `--annotation status!=done`
```

→ `Session.objects.exclude(annotations__status="done")` → matches sessions where `status` is absent, `null`, or any value other than `"done"`.

If the caller wants "present and not equal to done", combine: `--annotation status:exists --annotation status!=done`.

### 8.3 `--annotation key=null`

Matches sessions where the key is **present with JSON `null` value only**. Does not match sessions where the key is absent. To get "absent or null", use `--annotation key:not-exists` plus a second invocation or accept that the OR is not expressible without re-running the command.

### 8.4 Dotted segment containing `.`

Not supported in v1. No escaping syntax. The same limitation already exists on `create-session --annotation` and `update-session set:`, so no new divergence.

### 8.5 Filter on array element

Not supported in v1. Annotation values that are arrays are treated as opaque scalars for equality (i.e. `key=[1,2]` would have to JSON-serialize the array — not the intended semantics). Out of scope.

### 8.6 Search pagination — partial vs exhausted nuance

If the caller asks for 20 results and we return 7 with `exhausted=true`, there are no more matches anywhere in the corpus. If we return 7 with `partial=true`, there might be more matches past the guardrail. The caller can re-issue the search with a higher `--max-iterations` if exposed, or accept the truncation.

This nuance is communicated via the two flags. Consider exposing `--max-iterations` on the CLI only if needed in practice.

## 9. Non-changes

- **DB schema**: no migration. The `annotations` JSONField already exists and is populated.
- **Tantivy schema**: no change. No reindexing of annotations. The hybrid Tantivy → Django approach keeps Tantivy focused on full-text and filiation, with annotations layered on top.
- **WS / REST API**: no change. Filter is CLI-only in v1.
- **`create-session` / `update-session`**: no change to write semantics or `parse_annotations`.
- **Hidden flag semantics**: unchanged. `--annotation` is orthogonal to `--include-hidden`.

## 10. Verification

(Reminder: project policy is "no tests and no linting" as a shortcut. Verification here is manual, not blocking.)

A standalone script (`/tmp/test_json_typing.py` and `/tmp/test_json_typing_django.py`, retained during the design phase) exercised SQLite JSON1 and Django ORM JSONField lookups against a representative set of inputs:

- 15 rows covering int, str, bool, float, null, absent, and string-of-bool/number
- All `__exact` lookups with typed Python values
- `__has_key`, `__isnull`, `__in`, `exclude`, nested `__team__lead`

Findings confirmed:
- Django ORM correctly distinguishes typed JSON values via its `CASE WHEN JSON_TYPE IN ('true','false','null')` SQL.
- The bool↔string edge case is the only material divergence; it is documented in §8.1.
- `__isnull=True` strictly means absent; `=null` means present-with-null.
- Nested paths work natively via the `__` lookup chain.

For the implementation, a checked behaviour matrix should be added covering:

- All 5 operators on top-level and nested paths
- Composition with each of `--spawned-by`, `--spawn-root`, `--descendants`
- `hidden=False` is preserved when `--annotation` is the only filter
- Topology `matches_annotations` flag is set on exactly the same nodes that `sessions --spawn-root <id> --annotation ...` would return
- Search pagination: exhausted, partial, full-page cases

## 11. Open questions / future work

1. **Annotation wait** (`twicc sessions wait --annotation role=implementer --target status=done`): designed in a follow-up. Will reuse `parse_annotation_filter` / `AnnotationFilter` / `apply_annotation_filters` from this spec. Polling sketch: `ProcessRun`-style 250 ms loop running a single ORM query against the matching subset, with `--first` / `--all` semantics.
2. **Frontend filter** (Pinia store): can adopt the same syntax once stable. Not blocking.
3. **Annotation index** (SQLite generated column or Postgres partial index on a specific path): only if perf becomes an issue. Not anticipated for the v1 scale.
4. **OR across `--annotation` flags**: currently AND-only. If needed, a `--annotation-or` flag could be added later, or a query DSL. Not in scope for v1.
5. **`--max-iterations` exposure** on `twicc search`: hold until a real use case justifies it.

## 12. Decision log

| Decision | Outcome | Rationale |
|---|---|---|
| Filtering before wait | Filtering only in v1 | Scope clarity; wait reuses the parser later |
| Operator richness | `=`, `!=`, `:exists`, `:not-exists`, `:in:` | Covers 95% of orchestration needs; parser stays trivial |
| Type prefixes (`s:`, `i:`) | None | Django ORM handles typed comparisons cleanly; reuse `parse_annotations` inference |
| `:exists` semantics | Present (any value, including `null`) | Matches `JSON_TYPE IS NOT NULL` and `__isnull=False` |
| `:not-exists` semantics | Strictly absent | Matches `__isnull=True` |
| `--annotation` and `hidden` | Orthogonal (does not lift `hidden=False`) | Annotation = refinement, filiation = whole-tree intent |
| Topology behaviour | Tree preserved, `matches_annotations` flag per node | Avoid breaking tree semantics; flat list available via `sessions --spawn-root` |
| Topology implementation | Two SQL queries (full tree + filtered tree), set membership in Python | ORM is single source of truth for filter semantics; no Python re-implementation to keep in sync |
| Parser depth | Minimum to split key/op/value, no semantic validation | Same philosophy as Django ORM: build the query, run it; an empty result is the caller's signal, not a parser error |
| Search strategy | Tantivy → Django post-filter with oversample loop | Avoids extending Tantivy schema; mirrors known oversample pattern |
| Search order preservation | Iterate Tantivy-ordered ids, keep those in post-filter set | No sort step; Django's `id__in` row order is irrelevant |
| Module location | `src/twicc/cli/_annotation_filters.py` | Follows `_xxx.py` private CLI convention |
| Plugin version | 0.27.0 → 0.28.0 (minor) | New flags on existing skills |
