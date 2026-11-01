# Full-Text Search — Design Document

**Date:** 2026-03-09
**Branch:** `feat/search`
**Status:** Design approved, implementation pending

---

## Problem

TwiCC currently only supports fuzzy matching on **session titles** (frontend-side, `matchSubsequence()` in `SessionList.vue`). There is no way to search inside session content — the actual messages exchanged with Claude.

With hundreds to thousands of sessions and 76K+ searchable messages (~153 MB of text), a simple `LIKE '%term%'` approach is too slow and offers no relevance ranking.

## Goal

Provide a backend-powered full-text search across session message content with:
- Fast keyword search with relevance scoring (BM25)
- Incremental indexing (new messages are searchable immediately, no full rebuild)
- Results grouped by session with per-session aggregate scores
- Snippet/highlighting support (show the matching passage in context)
- Filtering by project and archived status
- Minimal resource consumption (no CPU spikes on indexation)

## Decision: Tantivy via tantivy-py

After evaluating multiple options (SQLite FTS5, Tantivy, Whoosh, Xapian, Typesense/Meilisearch, APSW, bm25s), we chose **Tantivy** through its Python bindings `tantivy-py`.

### Why Tantivy

| Criterion | Assessment |
|-----------|------------|
| **Installation** | `pip install tantivy` — pre-built wheels for Python 3.10-3.14 on Linux (x86_64, aarch64) and macOS (x86_64, ARM64). No Rust toolchain needed. |
| **Performance** | Rust-native. Sub-millisecond search latency. ~45K docs/sec indexing throughput. |
| **Incremental indexing** | `add_document()` + `commit()`. No full rebuild needed for new content. |
| **Scoring** | BM25 built-in, per-field boost, query explain for debugging. |
| **Query syntax** | AND/OR/NOT, phrases, prefix, fuzzy (Levenshtein), field-specific, boost. |
| **Snippets** | Built-in `SnippetGenerator` with configurable max chars and `<b>` highlighting. |
| **Crash safety** | Atomic commits. On crash, auto-rollback to last successful commit. Never corrupts. |
| **Concurrency** | Single writer (fine for our single-process arch), unlimited concurrent readers. GIL released during indexing since v0.25.0. |
| **Maintenance** | Actively maintained by Quickwit. Latest release: v0.25.1 (Dec 2025). |

### Why not SQLite FTS5

FTS5 was a strong contender (same DB, transactional triggers, zero deps). Main concern: **FTS5 availability is not guaranteed on all Linux distributions**. While most recent Ubuntu/Debian ship it, we cannot guarantee it for all PyPI users. The `pysqlite3-binary` fallback works but adds complexity (patching `sys.modules` for Django). Tantivy's pre-built wheels guarantee availability everywhere.

### Eliminated options

| Option | Reason |
|--------|--------|
| **Whoosh** | Abandoned (last release 2019), pure Python = slow |
| **Xapian** | Requires system-level C++ library installation |
| **Typesense / Meilisearch** | Separate server processes — violates "single process" constraint |
| **bm25s** | No incremental indexing (full rebuild required on any change) |
| **APSW + FTS5** | Separate SQLite binding, incompatible with Django's ORM layer |

## What we understand about BM25

BM25 is a **keyword-based** ranking algorithm (not semantic/vector search). It works with an **inverted index** (like a book's index: for each word, which documents contain it).

Scoring factors:
- **TF (Term Frequency):** More occurrences of the search term in a document → higher score
- **IDF (Inverse Document Frequency):** Rare words score higher than common ones
- **Document length:** A match in a short message scores higher than in a very long one

This is well-suited for our use case: searching for specific technical terms (file names, tool names, error messages, commands) in session conversations. We don't need semantic understanding ("car" ≈ "automobile") — when users search "websocket", they want the word "websocket".

## Architecture

### Storage

```
<data_dir>/
├── db/
│   └── data.sqlite
├── logs/
│   └── ...
└── search/                 ← NEW
    └── (tantivy index files)
```

A `get_search_dir()` function in `paths.py`, included in `ensure_data_dirs()`. Automatically isolated per worktree via the existing `$TWICC_DATA_DIR` mechanism.

Estimated index size: **50-100 MB** for ~153 MB of source text (FST-compressed inverted index + LZ4-compressed doc store).

### Index schema

```python
schema = tantivy.SchemaBuilder()
schema.add_text_field("body", stored=True, tokenizer_name="twicc")
schema.add_unsigned_field("line_num", stored=True, indexed=True)  # SessionItem.line_num within the session
schema.add_text_field("session_id", stored=True, indexed=True, tokenizer_name="raw")
schema.add_text_field("project_id", stored=True, indexed=True, tokenizer_name="raw")
schema.add_text_field("from", stored=True, indexed=True, tokenizer_name="raw")  # "user" or "assistant"
schema.add_date_field("timestamp", stored=True, indexed=True)
schema.add_boolean_field("archived", stored=True, indexed=True)
```

Each **Tantivy document = one message** (user or assistant) from a session.

### Tokenizer

Session content can be in any language (English, French, Spanish, German, etc.) plus code, file names, and commands. We use a **language-agnostic** custom tokenizer:

```python
analyzer = (
    TextAnalyzerBuilder(Tokenizer.simple())     # split on whitespace/punctuation
    .filter(Filter.remove_long(100))            # drop very long tokens
    .filter(Filter.lowercase())                 # case-insensitive
    .filter(Filter.ascii_folding())             # é→e, ñ→n, ü→u, ø→o (handles all Latin diacritics)
    .build()
)
index.register_tokenizer("twicc", analyzer)
```

No stemming — it's language-specific and would mangle other languages. ASCII folding handles all Latin-script diacritics universally: searching "resume" matches "résumé", "espanol" matches "español", etc. Works well for all Latin-alphabet languages. CJK languages (Chinese, Japanese, Korean) would need a specialized tokenizer — out of scope for now, as Claude Code users predominantly write in Latin-script languages and code.

### What gets indexed

| Item kind | Indexed? | Text source |
|-----------|----------|-------------|
| `user_message` | ✅ Yes | `message.content` (string) or `message.content[].text` entries |
| `assistant_message` | ✅ Yes | `message.content[].text` entries |
| `content_items` | ❌ No | Too noisy (tool results = raw file contents, command output) |
| `tool_use` / `tool_result` | ❌ No | Technical noise, not conversational |
| `system` | ❌ No | System prompts, not user content |

**Session type filter:** Only sessions with `type="session"` are indexed. Subagent sessions (`type="subagent"`) are excluded — their content is technical work spawned by the Task tool, and users navigate to the parent session, not to subagents directly.

### Search version pattern

Modeled after the existing `compute_version` mechanism:

**Settings:**
```python
CURRENT_SEARCH_VERSION = 1  # Bump when indexing logic changes
```

**Session model — new field:**
```python
search_version = models.PositiveIntegerField(null=True, blank=True)
# NULL = never indexed, integer = indexed at this version
```

**Flow:**
```
Startup
  ├── Initial sync (existing) → new sessions get search_version = NULL
  └── Background search indexing task (NEW)
        Query: Session.objects.filter(search_version != CURRENT_SEARCH_VERSION or NULL)
        For each session:
          → Read SessionItems (user_message, assistant_message)
          → Extract text from JSON content
          → Delete old documents for this session in Tantivy (if re-indexing)
          → Add new documents
          → Commit
          → Set search_version = CURRENT_SEARCH_VERSION

Runtime (watcher)
  └── New SessionItem of indexable kind
        → Extract text → writer.add_document() → commit (batched)
        → Update session.search_version = CURRENT_SEARCH_VERSION
```

**Full rebuild:** Bump `CURRENT_SEARCH_VERSION` → all sessions re-indexed at next startup.

### Search module (`src/twicc/search.py`)

A dedicated module encapsulates all Tantivy state and exposes simple functions. The rest of the codebase (compute, watcher, views) never touches Tantivy directly — it only calls these functions.

```python
# Module-level state (initialized once at startup)
_index = None
_writer = None

# Lifecycle
def init_search_index():     # Called at startup: open/create index, create writer
def shutdown_search_index():  # Called at shutdown: wait_merging_threads()

# Indexing (called by compute and watcher, agnostic of caller)
def index_document(session_id, project_id, line_num, body, from_role, timestamp, archived):
def delete_session_documents(session_id):
def commit():

# Searching (called by API views)
def search(query, filters, limit, offset):  # Returns structured results with snippets
```

This way:
- The background compute task calls `index_document()` + `commit()` in a loop
- The watcher calls `index_document()` + `commit()` when a new indexable item arrives
- The API view calls `search()`
- Nobody knows about the Tantivy writer, index, or schema — it's all encapsulated

### Writer/searcher lifecycle

Tantivy calls are synchronous. In async contexts (watcher, API views), they are wrapped with `asyncio.to_thread()` to avoid blocking the event loop. The GIL is released during Rust operations, so other Python threads run freely.

```
Startup:
  init_search_index()
    → index = tantivy.Index(schema, path=search_dir)
    → writer = index.writer(heap_size=50_000_000, num_threads=1)
    → Writer stays open for process lifetime

Background compute (sync, separate process at startup):
  index_document(...)         # Direct calls, no async needed
  commit()                    # One commit per session batch

Watcher (async):
  await asyncio.to_thread(index_document, ...)
  await asyncio.to_thread(commit)

Search request (async):
  await asyncio.to_thread(search, query, filters)

Shutdown:
  shutdown_search_index()
    → writer.wait_merging_threads()
```

### API endpoint

```
GET /api/search/?q=<query>&project_id=<id>&session_id=<id>&from=<user|assistant>&after=<datetime>&before=<datetime>&include_archived=true&limit=50&offset=0
```

All index fields are exposed as optional query parameters for filtering:

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | **Required.** Full-text search query (BM25 scored) |
| `project_id` | string | Filter to a specific project |
| `session_id` | string | Filter to a specific session (useful for "search within current session") |
| `from` | string | `user` or `assistant` — filter by message author |
| `after` | datetime | Only messages after this timestamp |
| `before` | datetime | Only messages before this timestamp |
| `include_archived` | bool | Include archived sessions (default: `false`) |
| `limit` | int | Max number of session groups returned (default: 20) |
| `offset` | int | Pagination offset |

These filters are applied as Tantivy boolean query clauses (Occur.Must), combined with the text query. The user doesn't need to know Tantivy query syntax — the frontend can build the API call with the right parameters.

Response structure (results grouped by session, sorted by aggregate score):

```json
{
  "query": "django migration",
  "total_sessions": 12,
  "results": [
    {
      "session_id": "abc-123",
      "session_title": "Refactoring des modèles Django",
      "project_id": "proj-456",
      "project_name": "twicc",
      "score": 12.5,
      "matches": [
        {
          "line_num": 42,
          "from": "user",
          "snippet": "...il faut lancer la <b>migration</b> <b>Django</b> après...",
          "score": 8.2,
          "timestamp": "2025-03-01T14:30:00Z"
        },
        {
          "line_num": 57,
          "from": "assistant",
          "snippet": "...j'ai créé la <b>migration</b> pour le modèle...",
          "score": 4.3,
          "timestamp": "2025-03-01T14:31:00Z"
        }
      ]
    }
  ]
}
```

### Archived sessions handling

The `archived` boolean is stored in the index. Default search behavior excludes archived sessions (`+archived:false` filter). The `include_archived=true` query parameter removes this filter.

When a session is archived/unarchived, all its documents in the index must be updated (delete + re-add with new flag). This is a rare operation.

## Scope

### In scope (this feature)
- Tantivy index setup, schema, custom tokenizer
- `search_version` field on Session model + migration
- Background indexing task at startup (same pattern as compute)
- Inline indexing in the watcher for real-time updates
- Text extraction from message content JSON
- Search API endpoint with filtering (project, session, from, timestamp range, archived)
- Snippet generation with highlighting
- Results grouped by session with aggregate scoring
- `search/` directory in data dir, managed by `paths.py`

### Out of scope (future)
- Frontend search UI (separate feature)
- Fuzzy query support (can be added later via `fuzzy_fields` parameter)
- Indexing tool_use inputs or thinking blocks
- Per-project separate indexes (single index with project_id filter is sufficient)
- Search suggestions / autocomplete
