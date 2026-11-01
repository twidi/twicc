# Full-Text Search Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Tantivy-powered full-text search across session messages, with background indexing at startup, real-time indexing in the watcher, and a search API endpoint.

**Architecture:** A dedicated `search.py` module encapsulates all Tantivy state (index, writer, schema). It exposes simple functions called by the background indexing task, the watcher, and the API view. Tantivy calls are synchronous; async callers use `asyncio.to_thread()`.

**Tech Stack:** tantivy-py (Rust bindings), Django, orjson, asyncio

**Design doc:** `docs/specs/2026-03-09-full-text-search.md`

---

### Task 1: Add tantivy dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list)

**Steps:**

1. Add `"tantivy>=0.25.1"` to the `dependencies` list in `pyproject.toml`, alphabetically (between `python-dotenv` and `uvicorn`).

2. Run `uv lock` to update `uv.lock`.

3. Commit: `feat(search): add tantivy dependency`

---

### Task 2: Add `get_search_dir()` to paths.py

**Files:**
- Modify: `src/twicc/paths.py`

**Steps:**

1. Add the function after `get_synced_settings_path()` (line 84):

```python
def get_search_dir() -> Path:
    """Return the search index directory (<data_dir>/search/)."""
    return get_data_dir() / "search"
```

2. Add to `ensure_data_dirs()`:

```python
get_search_dir().mkdir(parents=True, exist_ok=True)
```

3. Update the docstring at the top of the file to include `search/` in the structure:

```
    ├── search/
    │   └── (tantivy index files)
```

4. Commit: `feat(search): add search directory to paths`

---

### Task 3: Add `search_version` to Session model + migration

**Files:**
- Modify: `src/twicc/core/models.py` (Session class)
- Create: `src/twicc/core/migrations/0055_session_search_version.py`

**Steps:**

1. Add field to Session model, next to `compute_version`:

```python
search_version = models.PositiveIntegerField(null=True, blank=True)
```

2. Add `CURRENT_SEARCH_VERSION` to `src/twicc/settings.py`, after `CURRENT_COMPUTE_VERSION` (line 134):

```python
# Search index version
CURRENT_SEARCH_VERSION = 1  # Bump when search indexing logic changes to trigger reindexing
```

3. Create migration:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feat-search && uv run python -m django makemigrations core --name session_search_version
```

4. Commit: `feat(search): add search_version field to Session model`

**Note:** Remind user to run `migrate` after this task.

---

### Task 4: Create the search module (`src/twicc/search.py`)

This is the core module. All Tantivy interaction is encapsulated here.

**Files:**
- Create: `src/twicc/search.py`

**Steps:**

1. Create `src/twicc/search.py` with the following structure:

**Module-level state:**
```python
_index: tantivy.Index | None = None
_writer: tantivy.IndexWriter | None = None
_schema: tantivy.Schema | None = None
```

**Schema definition function** — `_build_schema()`:
- `body`: text field, stored, tokenizer `"twicc"`
- `line_num`: unsigned field, stored, indexed
- `session_id`: text field, stored, indexed, tokenizer `"raw"`
- `project_id`: text field, stored, indexed, tokenizer `"raw"`
- `from_role`: text field, stored, indexed, tokenizer `"raw"` (values: `"user"`, `"assistant"`)
- `timestamp`: date field, stored, indexed
- `archived`: boolean field, stored, indexed

Note: the field is named `from_role` in the schema (not `from`) because `from` is a Python reserved keyword. The API query parameter remains `from`.

**Tokenizer registration** — `_register_tokenizer(index)`:
```python
from tantivy import TextAnalyzerBuilder, Tokenizer, Filter

analyzer = (
    TextAnalyzerBuilder(Tokenizer.simple())
    .filter(Filter.remove_long(100))
    .filter(Filter.lowercase())
    .filter(Filter.ascii_folding())
    .build()
)
index.register_tokenizer("twicc", analyzer)
```

**Lifecycle functions:**

`init_search_index()`:
- Import `get_search_dir()` from paths
- Build schema, create or open index at `get_search_dir()`
- Register tokenizer
- Create writer with `heap_size=50_000_000, num_threads=1`
- Store in module-level variables

`shutdown_search_index()`:
- Call `_writer.wait_merging_threads()` if writer exists
- Set module variables to None

**Text extraction function** — `extract_indexable_text(content: str | list | None) -> str | None`:
- Unlike `compute.py`'s `extract_text_from_content()` which returns only the first text block, this function extracts and concatenates ALL text blocks from the content.
- If content is a string: return it stripped
- If content is a list: iterate all items, collect all `type=="text"` entries, join with `\n`
- Return None if no text found

**Indexing functions:**

`index_document(session_id, project_id, line_num, body, from_role, timestamp, archived)`:
- Create a `tantivy.Document` with all fields
- `timestamp` must be converted to a datetime with timezone for Tantivy's date field
- Call `_writer.add_document(doc)`

`delete_session_documents(session_id)`:
- Use `_writer.delete_documents("session_id", session_id)` to remove all docs for that session

`commit()`:
- Call `_writer.commit()`

**Search function** — `search(query_str, *, project_id=None, session_id=None, from_role=None, after=None, before=None, include_archived=False, limit=20, offset=0)`:
- Build the text query: `_index.parse_query(query_str, ["body"])`
- Build filter clauses as `Query.term_query(...)` for each non-None filter
- If not `include_archived`: add `Query.term_query(schema, "archived", False, "bool")` as Must clause
- For `after`/`before`: use `Query.range_query(...)` on the timestamp field
- Combine all with `Query.boolean_query([(Occur.Must, text_query), (Occur.Must, filter1), ...])`
- `_index.reload()` then `_index.searcher()`
- Execute search with a generous limit (e.g., `limit * 20`) to get enough raw hits for grouping
- Generate snippets with `SnippetGenerator`
- Group results by `session_id`, sum scores per session, sort sessions by aggregate score
- Apply `offset` and `limit` on the grouped session list
- Return a `SearchResults` NamedTuple with the structured data

**Return types** (NamedTuples):
```python
class SearchMatch(NamedTuple):
    line_num: int
    from_role: str
    snippet: str
    score: float
    timestamp: str | None

class SessionResult(NamedTuple):
    session_id: str
    score: float
    matches: list[SearchMatch]

class SearchResults(NamedTuple):
    query: str
    total_sessions: int
    results: list[SessionResult]
```

2. Commit: `feat(search): create search module with Tantivy integration`

---

### Task 5: Background search indexing task

**Files:**
- Create: `src/twicc/search_task.py`
- Modify: `src/twicc/cli.py`

**Steps:**

1. Create `src/twicc/search_task.py`:

**`start_search_index_task()` async function:**
- Query sessions needing indexing: `Session.objects.filter(type=SessionType.SESSION).exclude(search_version=settings.CURRENT_SEARCH_VERSION)`
- Count them, log the count. If 0, log and return early.
- Broadcast startup progress (`"search_index"` phase)
- For each session (in a loop):
  - Read its `SessionItem` objects with `kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE]`, ordered by `line_num`
  - Delete existing documents for this session in the index (for re-indexing case)
  - For each item: parse JSON, extract text via `search.extract_indexable_text(get_message_content(parsed))`, call `search.index_document(...)` if text is non-empty
  - Commit after each session (batch per session)
  - Update `session.search_version = settings.CURRENT_SEARCH_VERSION` and save
  - Broadcast progress
- All Tantivy calls via `asyncio.to_thread()` (the function is async)
- DB reads via `sync_to_async`
- Handle `asyncio.CancelledError` for clean shutdown

**`stop_search_index_task` and stop event:**
- Simple `asyncio.Event` pattern, same as other tasks
- Check `stop_event.is_set()` between sessions

2. Modify `src/twicc/cli.py`:

**Imports** (add near line 54):
```python
from twicc.search import init_search_index, shutdown_search_index
from twicc.search_task import start_search_index_task, stop_search_index_task
```

**In `orchestrator_task()`** (after the watcher starts, ~line 193):
```python
# Initialize search index and start background indexing
await asyncio.to_thread(init_search_index)
deferred["search_task"] = asyncio.create_task(start_search_index_task())
logger.info("Search index initialized, background indexing started")
```

**In `deferred` dict** (line 127): add `"search_task": None`

**In `finally` block** (after compute shutdown):
```python
# Clean shutdown of search index
if deferred["search_task"] is not None:
    logger.info("Stopping search index task...")
    stop_search_index_task()
    await _cancel_task(deferred["search_task"], "Search index task")
await asyncio.to_thread(shutdown_search_index)
```

3. Commit: `feat(search): add background search indexing task at startup`

---

### Task 6: Watcher integration

**Files:**
- Modify: `src/twicc/sessions_watcher.py`

**Steps:**

1. In `sync_and_broadcast()`, after the `new_line_nums` block processes broadcasts (around line 399), add search indexing for new items:

```python
# Index new messages for full-text search
if new_line_nums and not is_subagent:
    await _index_new_items_for_search(session, new_line_nums)
```

2. Add the helper function `_index_new_items_for_search(session, line_nums)`:
- Import from `search` module and `compute` module
- Query new `SessionItem` objects with `kind__in=[USER_MESSAGE, ASSISTANT_MESSAGE]` and `line_num__in=line_nums`
- For each: parse content, extract text, call `search.index_document(...)` via `to_thread`
- Call `search.commit()` via `to_thread` once for the batch
- Wrap in try/except to never crash the watcher — log errors and continue

3. Commit: `feat(search): add real-time search indexing in watcher`

---

### Task 7: Search API endpoint

**Files:**
- Modify: `src/twicc/views.py`
- Modify: `src/twicc/urls.py`

**Steps:**

1. Add the search view to `src/twicc/views.py`:

```python
def search_sessions(request):
    """GET /api/search/ - Full-text search across session messages."""
```

- Extract query parameters: `q`, `project_id`, `session_id`, `from` (→ `from_role`), `after`, `before`, `include_archived`, `limit`, `offset`
- Validate `q` is present and non-empty (400 if missing)
- Parse `after`/`before` as ISO datetime strings if provided
- Parse `limit`/`offset` as ints with defaults (20 and 0)
- Parse `include_archived` as boolean (truthy string check)
- Call `search.search(...)` — this is a sync view so direct call is fine
- Enrich results with session titles and project names from DB
- Return `JsonResponse` with the structure from the design doc

2. Add URL pattern to `src/twicc/urls.py` (in the API endpoints section, after `api/sessions/`):

```python
path("api/search/", views.search_sessions),
```

3. Commit: `feat(search): add search API endpoint`

---

### Task 8: Test manually and commit

**Steps:**

1. Verify the full flow works:
   - Start dev servers in the worktree
   - Check logs for search index initialization and background indexing progress
   - Test search API: `curl "http://localhost:<port>/api/search/?q=django"`
   - Verify results contain sessions with matching messages, snippets, and scores
   - Test filters: `?q=test&from=user`, `?q=test&project_id=...`
   - Test with a non-matching query to verify empty results

2. Final commit if any adjustments needed.

---

## File summary

| File | Action |
|------|--------|
| `pyproject.toml` | Add `tantivy` dependency |
| `src/twicc/paths.py` | Add `get_search_dir()` + update `ensure_data_dirs()` |
| `src/twicc/settings.py` | Add `CURRENT_SEARCH_VERSION` |
| `src/twicc/core/models.py` | Add `search_version` field to Session |
| `src/twicc/core/migrations/0055_*.py` | New migration |
| `src/twicc/search.py` | **New** — Core search module (schema, index, writer, search) |
| `src/twicc/search_task.py` | **New** — Background indexing task |
| `src/twicc/cli.py` | Add search init + task to orchestrator + shutdown |
| `src/twicc/sessions_watcher.py` | Hook search indexing after new items |
| `src/twicc/views.py` | Add `search_sessions` view |
| `src/twicc/urls.py` | Add `api/search/` route |

## Dependency order

```
Task 1 (dependency)
  → Task 2 (paths)
  → Task 3 (model + settings)
  → Task 4 (search module) — depends on 1, 2, 3
    → Task 5 (background task) — depends on 4
    → Task 6 (watcher) — depends on 4
    → Task 7 (API) — depends on 4
      → Task 8 (test)
```

Tasks 2 and 3 are independent and can be done in parallel. Tasks 5, 6, and 7 are independent of each other (all depend on 4).
