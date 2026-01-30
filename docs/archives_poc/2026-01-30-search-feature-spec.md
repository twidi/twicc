# Search Feature Specification

## Overview

This document specifies a hybrid search feature for the Claude Code Web UI that combines full-text search (FTS5) with semantic vector search (sqlite-vec + FastEmbed) to provide powerful, intuitive search across conversation sessions.

## Problem Statement

Users need to find past conversations but often:
- Don't remember exact words used in the session
- Only have a vague idea of what they were working on ("that session where we discussed authentication")
- Want to find sessions by title OR by message content
- Need both exact phrase matching (quoted searches) and fuzzy/semantic matching

## Solution Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Interface                            │
├─────────────────────────────────────────────────────────────────┤
│  Search Icon (sidebar) → Dialog                                  │
│  Live results as user types (debounced)                         │
│  Two result sections: Session Titles / Message Content          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Search Backend API                          │
├─────────────────────────────────────────────────────────────────┤
│  WebSocket endpoint for live search                             │
│  Query parsing: detect quoted phrases vs free text              │
│  Route to appropriate search strategy                           │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐       ┌─────────────────────────┐
│   FTS5 Full-Text Search │       │  Vector Semantic Search │
├─────────────────────────┤       ├─────────────────────────┤
│  Exact phrase matching  │       │  Conceptual similarity  │
│  Keyword search         │       │  "Vibes" based search   │
│  BM25 ranking           │       │  Cosine similarity      │
│  Porter stemming        │       │  sqlite-vec + FastEmbed │
└─────────────────────────┘       └─────────────────────────┘
              │                               │
              └───────────────┬───────────────┘
                              ▼
                 ┌─────────────────────────┐
                 │  Reciprocal Rank Fusion │
                 │  (RRF) Score Merging    │
                 └─────────────────────────┘
                              │
                              ▼
                    Combined Results
```

### Search Behavior

| Query Type | Detection | Search Strategy |
|------------|-----------|-----------------|
| `"exact phrase"` | Quoted text | FTS5 phrase search only |
| `free text query` | No quotes | Hybrid FTS5 + Vector search with RRF fusion |

## Technical Stack

### Dependencies

| Package | Version | Purpose | Size |
|---------|---------|---------|------|
| sqlite-vec | ≥0.1.6 | Vector storage and KNN search | ~100 Ko |
| fastembed | ≥0.7.4 | Local embedding generation (ONNX, no GPU) | ~100 Ko + deps |

### Embedding Model

**Selected model:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`

| Property | Value |
|----------|-------|
| Size | 220 MB (downloaded once) |
| Dimensions | 384 |
| Languages | Multilingual (French, English, etc.) |
| Speed | ~14k sentences/sec on CPU |
| RAM usage | ~150-200 MB |

**Rationale:** Multilingual support is essential as conversations mix French and English. This model provides good quality across languages while remaining lightweight.

### Database Schema

#### FTS5 Virtual Tables (Full-Text Search)

```sql
-- Full-text search index for session titles
CREATE VIRTUAL TABLE search_fts_session_titles USING fts5(
    project_id UNINDEXED,  -- Reference to projects table (for filtering)
    session_id UNINDEXED,  -- Reference to sessions table
    title,                  -- Searchable session title
    tokenize='porter unicode61'
);

-- Full-text search index for session aggregated content (all messages concatenated)
-- Matches here point to session, no specific line_id
CREATE VIRTUAL TABLE search_fts_session_content USING fts5(
    project_id UNINDEXED,  -- Reference to projects table (for filtering)
    session_id UNINDEXED,  -- Reference to sessions table
    content,               -- All session messages concatenated
    tokenize='porter unicode61'
);

-- Full-text search index for individual messages
-- Matches here point to specific line_id
CREATE VIRTUAL TABLE search_fts_messages USING fts5(
    project_id UNINDEXED,  -- Reference to projects table (for filtering)
    session_id UNINDEXED,  -- Reference to sessions table
    line_id UNINDEXED,     -- Reference to lines table
    content,               -- Single message text
    tokenize='porter unicode61'
);
```

#### Vector Tables (Semantic Search)

```sql
-- Vector embeddings for session titles
CREATE VIRTUAL TABLE search_vec_session_titles USING vec0(
    session_id INTEGER PRIMARY KEY,
    project_id INTEGER,    -- Reference to projects table (for filtering)
    embedding float[384]
);

-- Vector embeddings for session aggregated content
-- Matches here point to session, no specific line_id
CREATE VIRTUAL TABLE search_vec_session_content USING vec0(
    session_id INTEGER PRIMARY KEY,
    project_id INTEGER,    -- Reference to projects table (for filtering)
    embedding float[384]
);

-- Vector embeddings for individual messages
-- Matches here point to specific line_id
CREATE VIRTUAL TABLE search_vec_messages USING vec0(
    line_id INTEGER PRIMARY KEY,
    project_id INTEGER,    -- Reference to projects table (for filtering)
    session_id INTEGER,    -- Reference to sessions table
    embedding float[384]
);
```

#### Indexing Levels

| Level | Tables | Points to | Use case |
|-------|--------|-----------|----------|
| Session title | `search_fts_session_titles`, `search_vec_session_titles` | session | Find by session name |
| Session content | `search_fts_session_content`, `search_vec_session_content` | session | Find "foo bar" even if spread across messages |
| Individual message | `search_fts_messages`, `search_vec_messages` | session + line_id | Find and link to specific message |

### Indexing Strategy

#### What Gets Indexed (Three Levels)

1. **Session Titles**
   - Source: `sessions.title` field
   - Indexed in: `search_fts_session_titles`, `search_vec_session_titles`
   - Points to: session (no line_id)
   - Re-indexed when title changes

2. **Session Aggregated Content**
   - Source: All UserMessage + AssistantMessage texts concatenated
   - Indexed in: `search_fts_session_content`, `search_vec_session_content`
   - Points to: session (no line_id)
   - Re-indexed when any message in session changes
   - Purpose: Find sessions where search terms appear across multiple messages

3. **Individual Messages** (kind = "UserMessage" or "AssistantMessage")
   - Source: Single message text content from JSONL
   - Indexed in: `search_fts_messages`, `search_vec_messages`
   - Points to: session + line_id
   - Purpose: Find and navigate to specific message

#### When Indexing Happens

- **New content:** Indexed in background compute (not via Django signals)
  - After initial sync when new sessions/lines are saved
  - When watcher detects new lines
- **Bootstrap:** Model downloaded on first backend startup if not cached
- **Rebuild:** Triggered automatically via version comparison (see below)

#### Index Versioning

Two-level versioning system to manage index updates:

```python
# settings.py
SEARCH_INDEX_VERSION = 1  # Bump when index format/logic changes

# models.py - Session model
search_index_version = models.IntegerField(default=0)
```

**Background compute logic (per session):**

```
for each session:
    needs_compute = session.computed_version < COMPUTE_VERSION
    needs_index = session.search_index_version < SEARCH_INDEX_VERSION

    if needs_compute:
        compute_metadata(session)
        session.computed_version = COMPUTE_VERSION

    if needs_index:
        index_for_search(session)
        session.search_index_version = SEARCH_INDEX_VERSION

    if needs_compute or needs_index:
        session.save()
```

**Key points:**
- Processing is per-session, not batched by operation type
- Compute and indexing are independent: changing one doesn't force the other
- Automatic rebuild on version bump: just increment `SEARCH_INDEX_VERSION` in settings
- Manual rebuild command (`manage.py rebuild_search_index`) resets all `search_index_version` to 0

### Search Algorithm

#### 1. Query Parsing

```
Input: user query string
Output: { type: "exact" | "hybrid", terms: string[] }

If query contains quoted phrases ("..."):
  → Extract phrases, search type = "exact"
Else:
  → Tokenize query, search type = "hybrid"
```

#### 2. FTS5 Search

```sql
-- Session titles
SELECT session_id, bm25(search_fts_sessions) as score
FROM search_fts_sessions
WHERE title MATCH ?
ORDER BY score
LIMIT 20;

-- Message content
SELECT session_id, line_id, bm25(search_fts_messages) as score
FROM search_fts_messages
WHERE content MATCH ?
ORDER BY score
LIMIT 50;
```

#### 3. Vector Search

```sql
-- Session titles (KNN)
SELECT session_id, distance
FROM search_vec_sessions
WHERE embedding MATCH ?
ORDER BY distance
LIMIT 20;

-- Message content (KNN)
SELECT session_id, line_id, distance
FROM search_vec_messages
WHERE embedding MATCH ?
ORDER BY distance
LIMIT 50;
```

#### 4. Reciprocal Rank Fusion (RRF)

Combines FTS5 and vector results using RRF scoring:

```
RRF_score(doc) = Σ 1 / (k + rank_in_list)

Where:
- k = 60 (standard constant)
- rank_in_list = position in each result list (1-indexed)
```

Documents appearing in both FTS5 and vector results get boosted scores.

#### 5. Result Grouping

Results are grouped into two categories for display:
1. **Session Title Matches** - Sessions where the title matches
2. **Message Content Matches** - Sessions containing matching messages

Within each category, results are sorted by RRF score (descending).

## User Interface

### Trigger

- **Location:** Session list header in left sidebar
- **Element:** Search icon button, positioned right of "Sessions" title
- **Action:** Opens search dialog

### Search Dialog

Using Web Awesome `wa-dialog` component.

**Layout:**
```
┌────────────────────────────────────────────┐
│ 🔍 [Search input field...            ] [X] │
├────────────────────────────────────────────┤
│                                            │
│ SESSION TITLES                             │
│ ┌────────────────────────────────────────┐ │
│ │ Session title with **match** highlight │ │
│ │ 12 Jan 2026                            │ │
│ └────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────┐ │
│ │ Another **matching** session           │ │
│ │ 10 Jan 2026                            │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ MESSAGE CONTENT                            │
│ ┌────────────────────────────────────────┐ │
│ │ Session: Working on auth feature       │ │
│ │ "...discussing **authentication**..."  │ │
│ └────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────┐ │
│ │ Session: API refactoring               │ │
│ │ "...the **login** endpoint needs..."   │ │
│ └────────────────────────────────────────┘ │
│                                            │
└────────────────────────────────────────────┘
```

**Behavior:**
- Live search as user types (debounced ~300ms)
- Show loading indicator during search
- Highlight matching terms in results (for FTS5 matches)
- Click result → navigate to session
- Close dialog on selection or Escape key

### Result Display

Each result item shows:

**For session title matches:**
- Session title (with highlights)
- Session date

**For message content matches:**
- Session title (parent session)
- Snippet of matching content (with highlights)
- Optionally: message timestamp

## API Design

### WebSocket Message Protocol

**Search Request:**
```json
{
  "type": "search",
  "query": "user search text",
  "request_id": "unique-id"
}
```

**Search Response:**
```json
{
  "type": "search_results",
  "request_id": "unique-id",
  "results": {
    "session_titles": [
      {
        "session_id": "uuid",
        "title": "Session title",
        "snippet": "Title with <mark>highlight</mark>",
        "score": 0.95,
        "date": "2026-01-15"
      }
    ],
    "message_content": [
      {
        "session_id": "uuid",
        "session_title": "Parent session",
        "line_id": 123,
        "snippet": "...text with <mark>match</mark>...",
        "score": 0.87,
        "date": "2026-01-15"
      }
    ]
  }
}
```

## Bootstrap & Initialization

### First Run

1. Check if embedding model is cached
2. If not cached, download model (~220 MB)
   - Show progress in logs
   - Block search feature until complete (graceful degradation)
3. Initialize FastEmbed with model
4. Create FTS5 and vector tables if not exist

### Subsequent Runs

1. Load cached model (~2-3 seconds)
2. Search feature available immediately

### Index Rebuild Command

Provide management command to rebuild search indexes:
```bash
uv run python manage.py rebuild_search_index
```

## Performance Considerations

### Indexing Performance

| Operation | Estimated Time |
|-----------|---------------|
| Embed single message | 5-20 ms |
| FTS5 insert | < 1 ms |
| Vector insert | < 1 ms |

### Search Performance

| Operation | Estimated Time (10K messages) |
|-----------|------------------------------|
| FTS5 query | 1-5 ms |
| Vector KNN query | 1-10 ms |
| RRF fusion | < 1 ms |
| Total search | 10-30 ms |

### Memory Usage

| Component | RAM |
|-----------|-----|
| Embedding model | ~150-200 MB |
| Vector index (10K msgs) | ~15 MB |
| FTS5 index | Managed by SQLite |

## Implementation Phases

**Important:** During all development phases, no migrations are run, no indexing is performed, no data is processed. The developer will run migrations and trigger indexing manually after all phases are complete.

### Phase 1: Backend Infrastructure
1. Add dependencies (sqlite-vec, fastembed)
2. Create database migrations for FTS5 and vector tables
3. Add `search_index_version` field to Session model + `SEARCH_INDEX_VERSION` setting
4. Implement embedding service (model loading, text → vector)
5. Bootstrap model download on first run

### Phase 2: Indexing Integration
6. Implement indexing functions (FTS5 + vector for all 3 levels)
7. Integrate indexing into background compute and watcher (with version comparison)

### Phase 3: Search Backend
8. Implement FTS5 search queries (titles, session content, messages)
9. Implement vector search queries (titles, session content, messages)
10. Implement RRF fusion algorithm
11. Add WebSocket handler for search requests
12. Add search API response formatting (snippets, highlighting, line_id when applicable)

### Phase 4: Frontend UI
13. Add search icon to session list header (right of "Sessions" title)
14. Implement search dialog component (`wa-dialog`)
15. Implement live search with debouncing (300ms, min 3 characters)
16. Implement result display with two sections (titles / message content)
17. Implement highlighting for FTS5 results
18. Handle navigation to selected session (scroll to message deferred to future)
19. Add "Show more" button for message results

### Phase 5: Polish & Edge Cases
20. Handle UI states (empty, loading, errors)
21. Add `manage.py rebuild_search_index` command (resets all `search_index_version` to 0)

### Future (not in scope)
- Scroll to specific message when clicking a result with line_id (requires virtual scroller integration)
- Optimize for large datasets if needed

## Task Tracking

- [ ] 1. Add dependencies (sqlite-vec, fastembed)
- [ ] 2. Create database migrations for FTS5 and vector tables
- [ ] 3. Add `search_index_version` field to Session model + `SEARCH_INDEX_VERSION` setting
- [ ] 4. Implement embedding service (model loading, text → vector)
- [ ] 5. Bootstrap model download on first run
- [ ] 6. Implement indexing functions (FTS5 + vector for all 3 levels)
- [ ] 7. Integrate indexing into background compute and watcher (with version comparison)
- [ ] 8. Implement FTS5 search queries (titles, session content, messages)
- [ ] 9. Implement vector search queries (titles, session content, messages)
- [ ] 10. Implement RRF fusion algorithm
- [ ] 11. Add WebSocket handler for search requests
- [ ] 12. Add search API response formatting (snippets, highlighting, line_id when applicable)
- [ ] 13. Add search icon to session list header (right of "Sessions" title)
- [ ] 14. Implement search dialog component (`wa-dialog`)
- [ ] 15. Implement live search with debouncing (300ms, min 3 characters)
- [ ] 16. Implement result display with two sections (titles / message content)
- [ ] 17. Handle navigation to selected session
- [ ] 18. Add "Show more" button for message results
- [ ] 19. Handle UI states (empty, loading, errors)
- [ ] 20. Add `manage.py rebuild_search_index` command

## Tasks Detail

### Task 1: Add dependencies (sqlite-vec, fastembed)

**Files:** `pyproject.toml`

**Work:**
- Add `sqlite-vec>=0.1.6` to dependencies
- Add `fastembed>=0.7.4` to dependencies
- Run `uv sync` to install
- Verify sqlite-vec loads correctly in Python (`import sqlite_vec`)
- Verify fastembed imports work

---

### Task 2: Create database migrations for FTS5 and vector tables

**Files:** `src/twicc_poc/search/migrations/0001_search_tables.py`

**Work:**
- Create `search` Django app structure
- Write raw SQL migration for 6 virtual tables:
  - `search_fts_session_titles` (FTS5)
  - `search_fts_session_content` (FTS5)
  - `search_fts_messages` (FTS5)
  - `search_vec_session_titles` (vec0)
  - `search_vec_session_content` (vec0)
  - `search_vec_messages` (vec0)
- Use `migrations.RunSQL` with proper `CREATE VIRTUAL TABLE` statements
- Add reverse migration (DROP statements)
- Load sqlite-vec extension before creating vec0 tables (connection setup)

---

### Task 3: Add `search_index_version` field to Session model + `SEARCH_INDEX_VERSION` setting

**Files:** `src/twicc_poc/core/models.py`, `src/twicc_poc/settings.py`, new migration

**Work:**
- Add `search_index_version = models.IntegerField(default=0)` to Session model
- Create migration file (do not run it)
- Add `SEARCH_INDEX_VERSION = 1` constant in settings
- Document that bumping this triggers full re-index

---

### Task 4: Implement embedding service (model loading, text → vector)

**Files:** `src/twicc_poc/search/embeddings.py`

**Work:**
- Create `EmbeddingService` class (singleton pattern recommended)
- Initialize FastEmbed with model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Method `embed_text(text: str) -> list[float]` - single text to 384-dim vector
- Method `embed_batch(texts: list[str]) -> list[list[float]]` - batch embedding for efficiency
- Handle model not yet downloaded gracefully (return None or raise specific exception)
- Add `is_ready()` method to check if model is loaded

---

### Task 5: Bootstrap model download on first run

**Files:** `src/twicc_poc/search/embeddings.py`, `src/twicc_poc/bootstrap.py`

**Work:**
- In `EmbeddingService.__init__`, check if model is cached locally
- If not cached, trigger download (FastEmbed does this automatically on first use)
- Log progress/status during download
- Integrate into `bootstrap.py` - call embedding service initialization early
- Consider async download or background thread to not block startup entirely
- Add status flag to indicate "model downloading" vs "ready"

---

### Task 6: Implement indexing functions (FTS5 + vector for all 3 levels)

**Files:** `src/twicc_poc/search/indexing.py`

**Work:**
- Function `index_session_title(session)`:
  - Delete existing entry for this session_id in FTS5 and vec tables
  - Insert into `search_fts_session_titles` (project_id, session_id, title)
  - Generate embedding, insert into `search_vec_session_titles`

- Function `index_session_content(session)`:
  - Concatenate all UserMessage + AssistantMessage texts from session
  - Delete existing entry, insert into `search_fts_session_content`
  - Generate embedding of concatenated text, insert into `search_vec_session_content`

- Function `index_message(session, line)`:
  - Extract text content from line (UserMessage or AssistantMessage)
  - Delete existing entry for this line_id
  - Insert into `search_fts_messages` (project_id, session_id, line_id, content)
  - Generate embedding, insert into `search_vec_messages`

- Function `index_session(session)`:
  - Orchestrator: calls all three above functions
  - Handles transaction/error recovery

- Function `delete_session_index(session_id)`:
  - Remove all entries for a session from all 6 tables

---

### Task 7: Integrate indexing into background compute and watcher (with version comparison)

**Files:** `src/twicc_poc/compute.py`, `src/twicc_poc/watcher.py`

**Work:**

**In background compute:**
- In the per-session processing loop, add indexing logic:
  ```python
  needs_index = session.search_index_version < settings.SEARCH_INDEX_VERSION
  if needs_index:
      index_session(session)
      session.search_index_version = settings.SEARCH_INDEX_VERSION
  ```
- Ensure this runs AFTER metadata compute (session title must be computed first)
- Handle embedding service not ready (skip indexing, will retry next run)

**In watcher:**
- When watcher detects new lines and saves them:
  - For each new UserMessage/AssistantMessage line: call `index_message(session, line)`
  - After all new lines processed: call `index_session_content(session)` to update aggregated content
- If session title changed (from custom-title item): call `index_session_title(session)`
- Handle embedding service not ready gracefully

**Version comparison logic:**
- All indexing entry points check `session.search_index_version < SEARCH_INDEX_VERSION`
- Skip if already at current version
- Update version after successful indexing

---

### Task 8: Implement FTS5 search queries

**Files:** `src/twicc_poc/search/fts.py`

**Work:**
- Function `search_fts_titles(query: str, project_id: int, limit: int = 10)`:
  - Build FTS5 MATCH query
  - Handle quoted phrases vs free text
  - Return list of `{session_id, score, snippet}` using `snippet()` function

- Function `search_fts_session_content(query: str, project_id: int, limit: int = 20)`:
  - Search aggregated session content
  - Return `{session_id, score, snippet}`

- Function `search_fts_messages(query: str, project_id: int, limit: int = 50)`:
  - Search individual messages
  - Return `{session_id, line_id, score, snippet}`

- Handle FTS5 query syntax (escape special characters, handle quotes)

---

### Task 9: Implement vector search queries

**Files:** `src/twicc_poc/search/vector.py`

**Work:**
- Function `search_vec_titles(query_embedding: list[float], project_id: int, limit: int = 10)`:
  - KNN query on `search_vec_session_titles`
  - Filter by project_id
  - Return `{session_id, distance}`

- Function `search_vec_session_content(query_embedding, project_id, limit)`:
  - KNN on aggregated content vectors
  - Return `{session_id, distance}`

- Function `search_vec_messages(query_embedding, project_id, limit)`:
  - KNN on message vectors
  - Return `{session_id, line_id, distance}`

- Ensure sqlite-vec extension is loaded before queries

---

### Task 10: Implement RRF fusion algorithm

**Files:** `src/twicc_poc/search/hybrid.py`

**Work:**
- Function `reciprocal_rank_fusion(result_lists: list[list], k: int = 60)`:
  - Input: multiple ranked result lists (each item has an ID)
  - Output: single merged list with RRF scores
  - Formula: `score(doc) = Σ 1/(k + rank)` across all lists containing doc

- Function `merge_search_results(fts_results, vec_results)`:
  - Apply RRF to combine FTS5 and vector results
  - Return unified sorted list

---

### Task 11: Add WebSocket handler for search requests

**Files:** `src/twicc_poc/asgi.py` (or dedicated consumer file)

**Work:**
- Add handler for message type `"search"`:
  ```python
  async def handle_search(self, data):
      query = data["query"]
      request_id = data["request_id"]
      # ... perform search ...
      await self.send({"type": "search_results", ...})
  ```
- Parse query to detect exact vs hybrid mode
- Call appropriate search functions
- Format and return results
- Handle errors gracefully (return error response, don't crash)

---

### Task 12: Add search API response formatting

**Files:** `src/twicc_poc/search/hybrid.py` or new `formatting.py`

**Work:**
- Function `format_search_results(title_results, content_results, message_results)`:
  - Group results into `session_titles` and `message_content` sections
  - For each result, fetch session title and date
  - For FTS5 results: include snippet with `<mark>` highlighting
  - For vector results: generate custom snippet (first ~150 chars)
  - Include `line_id` for message results (for navigation)
  - Return structure matching API spec

---

### Task 13: Add search icon to session list header

**Files:** `frontend/src/components/SessionList.vue` (or similar)

**Work:**
- Add search icon button next to "Sessions" title
- Use Web Awesome icon (`wa-icon` with appropriate icon name)
- Style: subtle, same visual weight as other header elements
- On click: open search dialog (emit event or call composable)

---

### Task 14: Implement search dialog component (`wa-dialog`)

**Files:** `frontend/src/components/SearchDialog.vue`, `frontend/src/main.js`

**Work:**
- Use `wa-dialog` component for modal search interface
- Create component with:
  - Search input field (`wa-input`, auto-focus on open)
  - Results area (scrollable)
  - Close button
- Import `wa-dialog` and `wa-input` in `main.js`
- Handle Escape key to close (built-in with wa-dialog)
- Handle click outside to close (built-in with wa-dialog)

---

### Task 15: Implement live search with debouncing

**Files:** `frontend/src/composables/useSearch.js`, `SearchDialog.vue`

**Work:**
- Create `useSearch` composable:
  - Reactive `query` ref
  - Reactive `results` ref
  - Reactive `loading` ref
  - `search(query)` function with debounce (300ms)
  - Skip search if query < 3 characters
- Use VueUse `useDebounceFn` or implement manually
- Send WebSocket message with unique `request_id`
- Match responses by `request_id` (handle out-of-order responses)

---

### Task 16: Implement result display with two sections

**Files:** `frontend/src/components/SearchDialog.vue`

**Work:**
- "SESSION TITLES" section header
  - List of title match results (session title, date)
  - Results pointing to session (no specific message)
- "MESSAGE CONTENT" section header
  - List of message match results (session title, snippet, date)
  - Results from session aggregated content: point to session (no line_id)
  - Results from individual messages: point to specific message (with line_id)
  - Visually differentiate results with line_id (e.g., small icon or indicator) to show they point to a specific message
- Each result item clickable
- Show counts: "3 sessions" / "12 messages"
- Handle empty sections (hide header if no results)
- Implement highlighting for FTS5 results (backend sends snippets with `<mark>` tags)
- Use `v-html` to render highlighted snippets
- Style `<mark>` elements appropriately
- For vector results (no highlighting): show plain snippet

---

### Task 17: Handle navigation to selected session

**Files:** `frontend/src/components/SearchDialog.vue`, integration with router/store

**Work:**
- On result click:
  - Close search dialog
  - Navigate to selected session (`router.push` or store action)
- Emit event or use store to trigger navigation

**Note:** Scrolling to specific message (when line_id is present) is NOT implemented in v1. The current virtual scroller architecture makes this complex. For now, all results simply open the session. The line_id is preserved in the data for future implementation. Visual indication in Task 16 shows users which results would point to a specific message (once implemented).

---

### Task 18: Add "Show more" button for message results

**Files:** `frontend/src/components/SearchDialog.vue`, `useSearch.js`

**Work:**
- Initially show first 20 message results
- If more results available, show "Show more" button
- On click: request next batch (offset parameter in search request)
- Append new results to existing list
- Hide button when no more results

---

### Task 19: Handle UI states (empty, loading, errors)

**Files:** `frontend/src/components/SearchDialog.vue`, `useSearch.js`

**Work:**

**Empty states:**
- "No results found" message when search returns empty
- "Type to search..." placeholder when query is empty
- "Index building in progress" state if embedding model not ready
- Check backend status via WebSocket or API

**Loading states:**
- Show loading spinner/indicator while search is in progress
- Use Web Awesome spinner component (`wa-spinner`)
- Disable "Show more" button while loading
- Consider skeleton loaders for result items

**Error handling:**
- Catch WebSocket errors
- Display user-friendly error message ("Search temporarily unavailable" or similar)
- Allow retry
- Log errors for debugging

---

### Task 20: Add `manage.py rebuild_search_index` command

**Files:** `src/twicc_poc/search/management/commands/rebuild_search_index.py`

**Work:**
- Create management command
- Reset all `Session.search_index_version` to 0
- Optionally: clear all search tables first
- Print progress/status
- This triggers full re-index on next background compute run

---

## File Structure (Planned)

```
src/twicc_poc/
├── search/
│   ├── __init__.py
│   ├── embeddings.py      # FastEmbed wrapper, model management
│   ├── indexing.py        # Index management
│   ├── fts.py             # FTS5 queries
│   ├── vector.py          # sqlite-vec queries
│   ├── hybrid.py          # RRF fusion, combined search
│   ├── management/
│   │   └── commands/
│   │       └── rebuild_search_index.py
│   └── migrations/
│       └── 0001_search_tables.py

frontend/src/
├── components/
│   └── SearchDialog.vue   # Search UI component
└── composables/
    └── useSearch.js       # Search state & WebSocket handling
```

## Resolved Questions

1. **Snippet generation:** Hybrid approach
   - FTS5 results: Use `snippet()` function for automatic highlighting
   - Vector results: Custom extraction (first ~150 characters + "...")

2. **Result limits:** 10 session titles, 20 message contents by default
   - Add "Show more" button for message results to load additional results

3. **Minimum query length:** 3 characters required before search triggers

4. **Search scope:** Project-based with future flexibility
   - Indexing always includes `project_id` and `session_id` for each indexed item
   - Default search (v1): All sessions within current project
   - Future options: Single session scope, or global cross-project search

## Design Decisions

### Why Custom Implementation

We evaluated several existing Django packages before deciding on a custom implementation:

| Package | Verdict | Reason |
|---------|---------|--------|
| **django-vectordb** | ❌ Rejected | Uses sentence-transformers (PyTorch ~2GB), stores vectors in separate HNSWLib file, no FTS5 support |
| **modelsearch** (Wagtail) | ❌ Rejected | FTS5 only, no vector/semantic search |
| **django-semantic-search** | ❌ Rejected | Requires external Qdrant service, not offline-capable |
| **pocketsearch** | ❌ Rejected | Standalone library, no Django ORM integration |

**Decision:** Custom implementation using sqlite-vec + FastEmbed + FTS5

**Rationale:**
1. **Lightweight:** FastEmbed uses ONNX (~70 MB) vs PyTorch (~2 GB)
2. **Unified storage:** Everything in SQLite (no separate index files)
3. **Full control:** Custom hybrid search with RRF exactly as needed
4. **FTS5 + Vector:** Both exact phrase search and semantic search
5. **Offline:** 100% local, no external services

### Why FastEmbed over Sentence-Transformers

| Aspect | FastEmbed | Sentence-Transformers |
|--------|-----------|----------------------|
| Runtime | ONNX | PyTorch |
| Size | ~70 MB | ~2 GB |
| GPU required | No | No (but benefits from it) |
| Speed | Comparable | Comparable |
| Model support | Good | Excellent |

FastEmbed provides sufficient model support (including multilingual) with significantly smaller footprint.

## References

- [sqlite-vec Documentation](https://alexgarcia.xyz/sqlite-vec/)
- [FastEmbed GitHub](https://github.com/qdrant/fastembed)
- [SQLite FTS5 Documentation](https://sqlite.org/fts5.html)
- [Hybrid Search with SQLite](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html)
- [Sentence Transformers Models](https://sbert.net/docs/sentence_transformer/pretrained_models.html)
- [django-vectordb GitHub](https://github.com/pkavumba/django-vectordb) (evaluated, not used)
