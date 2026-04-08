"""
Core search module — encapsulates all Tantivy interaction.

No other module in the codebase should ever import tantivy directly.
All indexing and searching goes through the functions exposed here.

Schema fields:
    body       — full-text content (user/assistant messages/titles), tokenized with custom "twicc" analyzer
    line_num   — SessionItem.line_num within the session (unsigned integer); 0 for title documents
    session_id — session identifier (exact match via raw tokenizer)
    project_id — project identifier (exact match via raw tokenizer)
    from_role  — message source: "user", "assistant", or "title" (exact match via raw tokenizer)
    timestamp  — message timestamp (date, for range filtering)
    archived   — whether the session is archived (boolean filter)
"""

from __future__ import annotations

import logging
import math
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import NamedTuple

import tantivy
from tantivy import Filter, Occur, Query, TextAnalyzerBuilder, Tokenizer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (initialized once at startup via init_search_index)
# ---------------------------------------------------------------------------

_index: tantivy.Index | None = None
_writer = None  # tantivy.IndexWriter — no public type hint in tantivy-py
_schema: tantivy.Schema | None = None
_writer_lock = threading.Lock()  # Serialize all writer operations (add, delete, commit)

# Score multiplier for title matches — titles are more important than message content
TITLE_SCORE_BOOST = 3.0

# Session scoring: max(hit scores) + log1p(match_count) * MATCH_COUNT_FACTOR
# The best hit determines the primary ranking; the log bonus is a small tiebreaker
# that favors sessions where the search term appears frequently (likely a central topic).
# With BM25 scores typically in the 1–15 range, a factor of 0.5 yields a bonus of ~1.15
# for 10 matches — enough to differentiate without dominating.
MATCH_COUNT_FACTOR = 0.5

# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Schema & tokenizer
# ---------------------------------------------------------------------------


def _build_schema() -> tantivy.Schema:
    """Build and return the Tantivy schema for the search index."""
    builder = tantivy.SchemaBuilder()
    builder.add_text_field("body", stored=True, tokenizer_name="twicc")
    builder.add_unsigned_field("line_num", stored=True, indexed=True)
    builder.add_text_field("session_id", stored=True, tokenizer_name="raw")
    builder.add_text_field("project_id", stored=True, tokenizer_name="raw")
    builder.add_text_field("from_role", stored=True, tokenizer_name="raw")
    builder.add_date_field("timestamp", stored=True, indexed=True)
    builder.add_boolean_field("archived", stored=True, indexed=True)
    return builder.build()


def _register_tokenizer(index: tantivy.Index) -> None:
    """Register the custom "twicc" tokenizer on the given index.

    Language-agnostic tokenizer for mixed-language content:
    - SimpleTokenizer splits on whitespace/punctuation
    - remove_long(100) drops very long tokens (e.g. base64 blobs)
    - lowercase() for case-insensitive matching
    - ascii_fold() normalizes diacritics: e.g. resume matches resume
    """
    analyzer = (
        TextAnalyzerBuilder(Tokenizer.simple())
        .filter(Filter.remove_long(100))
        .filter(Filter.lowercase())
        .filter(Filter.ascii_fold())
        .build()
    )
    index.register_tokenizer("twicc", analyzer)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def init_search_index() -> None:
    """Initialize the search index, register the tokenizer, and create the writer.

    Must be called once at startup before any indexing or searching.
    """
    global _index, _writer, _schema

    from twicc.paths import get_search_dir

    _schema = _build_schema()
    search_dir = get_search_dir()
    search_dir.mkdir(parents=True, exist_ok=True)

    _index = tantivy.Index(_schema, path=str(search_dir))
    _register_tokenizer(_index)
    _writer = _index.writer(heap_size=50_000_000, num_threads=1)

    logger.info("Search index initialized at %s", search_dir)


def shutdown_search_index() -> None:
    """Cleanly shut down the search index.

    Waits for any ongoing merge operations, then releases all resources.
    """
    global _index, _writer, _schema

    if _writer is not None:
        try:
            _writer.wait_merging_threads()
        except Exception:
            logger.exception("Error waiting for merging threads during search shutdown")
        _writer = None

    _index = None
    _schema = None
    logger.info("Search index shut down")


def is_initialized() -> bool:
    """Return whether the search index has been initialized."""
    return _index is not None


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def extract_indexable_text(content: str | list | None) -> str | None:
    """Extract ALL text content from a message content field for indexing.

    Unlike compute.py's ``extract_text_from_content()`` which returns only the
    first text block, this function concatenates ALL text blocks for
    comprehensive search indexing.

    Args:
        content: Either a plain string or a list of content items
                 (dicts with ``type`` and ``text`` keys).

    Returns:
        The extracted text joined by newlines, or None if no text found.
    """
    if content is None:
        return None

    if isinstance(content, str):
        stripped = content.strip()
        return stripped or None

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        if texts:
            result = "\n".join(texts)
            return result or None

    return None


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def _check_writer() -> None:
    """Raise RuntimeError if the writer is not initialized."""
    if _writer is None:
        raise RuntimeError("Search index writer is not initialized. Call init_search_index() first.")


def _check_index() -> None:
    """Raise RuntimeError if the index is not initialized."""
    if _index is None or _schema is None:
        raise RuntimeError("Search index is not initialized. Call init_search_index() first.")


def index_document(
    session_id: str,
    project_id: str,
    line_num: int,
    body: str,
    from_role: str,
    timestamp: datetime | None,
    archived: bool,
) -> None:
    """Add a single document to the search index.

    Args:
        session_id: The session this message belongs to.
        project_id: The project this session belongs to.
        line_num: The SessionItem.line_num within the session.
        body: The text content of the message.
        from_role: "user" or "assistant".
        timestamp: Message timestamp. If None, defaults to now (UTC).
        archived: Whether the parent session is archived.
    """
    _check_writer()

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        # Tantivy requires timezone-aware datetimes
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    doc = tantivy.Document()
    doc.add_text("body", body)
    doc.add_unsigned("line_num", line_num)
    doc.add_text("session_id", session_id)
    doc.add_text("project_id", project_id)
    doc.add_text("from_role", from_role)
    doc.add_date("timestamp", timestamp)
    doc.add_boolean("archived", archived)

    with _writer_lock:
        _writer.add_document(doc)


def delete_session_documents(session_id: str) -> None:
    """Delete all documents belonging to a session from the index.

    Used before re-indexing a session (e.g. when search version changes
    or when a session is archived/unarchived).
    """
    _check_writer()
    with _writer_lock:
        _writer.delete_documents("session_id", session_id)


def commit() -> None:
    """Commit pending changes to the index, making them searchable."""
    _check_writer()
    with _writer_lock:
        _writer.commit()


def reindex_session(session_id: str) -> None:
    """Full re-index of a session: delete all docs, re-add title + all messages, commit.

    Used when a session title changes (rename, initial title set) to keep
    the title document in sync. Also re-indexes all messages since
    Tantivy only supports deleting by a single field (session_id), not
    compound deletes (session_id + from_role).

    Lazy-imports Django models to avoid circular imports at module level.
    """
    import orjson

    from twicc.compute import get_message_content
    from twicc.core.enums import ItemKind
    from twicc.core.models import Session, SessionItem

    _check_writer()
    _check_index()

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.warning("reindex_session: session %s not found, skipping", session_id)
        return

    # Delete all existing documents for this session
    delete_session_documents(session_id)

    # Index title (if any)
    if session.title:
        index_document(
            session_id, session.project_id, 0, session.title,
            "title", session.created_at, session.archived,
        )

    # Index all user/assistant messages
    items = (
        SessionItem.objects.filter(
            session_id=session_id,
            kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE],
        )
        .order_by("line_num")
        .values_list("content", "kind", "line_num", "timestamp", named=True)
    )

    for item in items:
        try:
            parsed = orjson.loads(item.content)
        except (orjson.JSONDecodeError, TypeError):
            continue
        content = get_message_content(parsed)
        text = extract_indexable_text(content)
        if text:
            from_role = "user" if item.kind == ItemKind.USER_MESSAGE else "assistant"
            index_document(
                session_id, session.project_id, item.line_num, text,
                from_role, item.timestamp, session.archived,
            )

    commit()
    logger.debug("reindex_session: session %s re-indexed", session_id)


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def _format_datetime_for_query(dt: datetime) -> str:
    """Format a datetime as an ISO 8601 string suitable for Tantivy query parsing."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def search(
    query_str: str,
    *,
    project_id: str | None = None,
    project_ids: list[str] | None = None,
    session_id: str | None = None,
    from_role: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    include_archived: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> SearchResults:
    """Execute a full-text search across indexed messages.

    Results are grouped by session, sorted by descending aggregate score.
    Each session group contains individual matches with snippets.

    Args:
        query_str: The user's search query (Tantivy query language).
        project_id: Filter to a specific project (mutually exclusive with project_ids).
        project_ids: Filter to any of these projects (mutually exclusive with project_id).
        session_id: Filter to a specific session.
        from_role: Filter by message author ("user" or "assistant").
        after: Only messages after this timestamp.
        before: Only messages before this timestamp.
        include_archived: Whether to include archived sessions (default False).
        limit: Max number of session groups to return (default 20).
        offset: Pagination offset for session groups (default 0).

    Returns:
        SearchResults with grouped, scored, and snippet-annotated results.
    """
    _check_index()

    try:
        text_query = _index.parse_query(query_str, ["body"], conjunction_by_default=True)
    except ValueError:
        logger.warning("Failed to parse search query: %r", query_str)
        return SearchResults(query=query_str, total_sessions=0, results=[])

    # Build filter clauses
    clauses: list[tuple[Occur, Query]] = [(Occur.Must, text_query)]

    if project_id is not None:
        clauses.append((Occur.Must, Query.term_query(_schema, "project_id", project_id)))
    elif project_ids:
        # OR across multiple project IDs (e.g. all projects in a workspace)
        project_clauses = [(Occur.Should, Query.term_query(_schema, "project_id", pid)) for pid in project_ids]
        clauses.append((Occur.Must, Query.boolean_query(project_clauses)))

    if session_id is not None:
        clauses.append((Occur.Must, Query.term_query(_schema, "session_id", session_id)))
        # In-session search: exclude title documents (title matches are useful for global
        # search to find sessions, but irrelevant when searching within a specific session)
        if from_role is None:
            clauses.append((Occur.MustNot, Query.term_query(_schema, "from_role", "title")))

    if from_role is not None:
        clauses.append((Occur.Must, Query.term_query(_schema, "from_role", from_role)))

    if not include_archived:
        clauses.append((Occur.Must, Query.term_query(_schema, "archived", False)))

    # Date range filters via parsed query syntax (tantivy-py's range_query doesn't
    # accept datetime objects for date fields, but the query parser handles ISO dates)
    if after is not None and before is not None:
        date_query_str = f"timestamp:[{_format_datetime_for_query(after)} TO {_format_datetime_for_query(before)}]"
        clauses.append((Occur.Must, _index.parse_query(date_query_str, [])))
    elif after is not None:
        date_query_str = f"timestamp:[{_format_datetime_for_query(after)} TO *]"
        clauses.append((Occur.Must, _index.parse_query(date_query_str, [])))
    elif before is not None:
        date_query_str = f"timestamp:[* TO {_format_datetime_for_query(before)}]"
        clauses.append((Occur.Must, _index.parse_query(date_query_str, [])))

    # Combine all clauses
    if len(clauses) == 1:
        combined_query = clauses[0][1]
    else:
        combined_query = Query.boolean_query(clauses)

    # Execute search
    _index.reload()
    searcher = _index.searcher()

    # Use a generous raw limit to get enough hits for session grouping.
    # In-session search needs all matches (no session grouping), so use a much higher limit.
    if session_id is not None:
        raw_limit = 10000
    else:
        raw_limit = max(limit * 20, 200)
    result = searcher.search(combined_query, limit=raw_limit)

    if not result.hits:
        return SearchResults(query=query_str, total_sessions=0, results=[])

    # Generate snippets
    snippet_generator = tantivy.SnippetGenerator.create(searcher, text_query, _schema, "body")
    snippet_generator.set_max_num_chars(200)

    # Group hits by session_id, tracking the best score per session
    session_matches: dict[str, list[SearchMatch]] = defaultdict(list)
    session_max_score: dict[str, float] = defaultdict(float)

    for score, doc_addr in result.hits:
        doc = searcher.doc(doc_addr)
        sid = doc.get_first("session_id")
        line = doc.get_first("line_num")
        role = doc.get_first("from_role")
        ts = doc.get_first("timestamp")

        snippet = snippet_generator.snippet_from_doc(doc)
        snippet_html = snippet.to_html()

        # Format timestamp as ISO string if available
        ts_str = None
        if ts is not None:
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)

        # Boost title matches so sessions matching by title rank higher
        effective_score = score * TITLE_SCORE_BOOST if role == "title" else score

        match = SearchMatch(
            line_num=line,
            from_role=role,
            snippet=snippet_html,
            score=effective_score,
            timestamp=ts_str,
        )
        session_matches[sid].append(match)
        if effective_score > session_max_score[sid]:
            session_max_score[sid] = effective_score

    # Session score = best hit score + small log bonus for match count.
    # The best hit determines the primary ranking; the log bonus is a tiebreaker
    # that favors sessions where the term appears frequently.
    session_scores = {
        sid: session_max_score[sid] + math.log1p(len(matches)) * MATCH_COUNT_FACTOR
        for sid, matches in session_matches.items()
    }

    # Sort session groups by descending aggregate score
    sorted_sessions = sorted(session_scores.keys(), key=lambda sid: session_scores[sid], reverse=True)

    total_sessions = len(sorted_sessions)

    # Apply offset and limit on session groups
    paginated_sessions = sorted_sessions[offset : offset + limit]

    results = []
    for sid in paginated_sessions:
        matches = session_matches[sid]
        # Sort matches within a session by descending score
        matches.sort(key=lambda m: m.score, reverse=True)
        results.append(
            SessionResult(
                session_id=sid,
                score=session_scores[sid],
                matches=matches,
            )
        )

    return SearchResults(
        query=query_str,
        total_sessions=total_sessions,
        results=results,
    )


# ---------------------------------------------------------------------------
# Raw search (read-only, CLI-friendly)
# ---------------------------------------------------------------------------


def _init_read_only() -> tuple[tantivy.Index, tantivy.Schema]:
    """Open the search index in read-only mode (no writer lock required).

    This allows querying from a separate process while the main server holds
    the writer lock. If the module is already fully initialized (server context),
    the existing index and schema are reused.

    Returns:
        A (index, schema) tuple ready for searching.
    """
    if _index is not None and _schema is not None:
        return _index, _schema

    from twicc.paths import get_search_dir

    schema = _build_schema()
    search_dir = get_search_dir()
    if not search_dir.exists():
        raise RuntimeError(f"Search index directory does not exist: {search_dir}")

    index = tantivy.Index(schema, path=str(search_dir))
    _register_tokenizer(index)
    return index, schema


def raw_search(
    query_str: str,
    *,
    limit: int = 20,
    offset: int = 0,
    to_json: bool = True,
) -> dict | str:
    """Execute a raw Tantivy query and return JSON-serializable results.

    Unlike ``search()``, this function:
    - Accepts any valid Tantivy query syntax (no implicit conjunction, no field default)
    - Does not group results by session — returns a flat list of hits
    - Works in read-only mode (no writer lock needed), safe to call from CLI
    - Returns all stored fields except the full ``body`` (a snippet is provided instead)

    Args:
        query_str: A raw Tantivy query string (e.g. ``body:websocket AND from_role:user``).
        limit: Maximum number of hits to return (default 20).
        offset: Number of hits to skip for pagination (default 0).
        to_json: If True (default), return a JSON string; if False, return a dict.

    Returns:
        If ``to_json`` is True: a JSON string (pretty-printed, sorted keys).
        If ``to_json`` is False: a dict with ``query``, ``total_hits``, ``limit``, ``offset``,
        and ``hits`` keys. Each hit contains ``score``, ``session_id``, ``project_id``,
        ``line_num``, ``from_role``, ``timestamp``, ``archived``, and ``snippet``.
    """
    index, schema = _init_read_only()

    try:
        parsed_query = index.parse_query(query_str, ["body"])
    except ValueError as exc:
        result_dict = {
            "query": query_str,
            "total_hits": 0,
            "limit": limit,
            "offset": offset,
            "hits": [],
            "error": f"Query parse error: {exc}",
        }
        if to_json:
            import orjson

            return orjson.dumps(result_dict, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode()
        return result_dict

    index.reload()
    searcher = index.searcher()

    # Fetch enough hits to satisfy offset + limit
    raw_limit = offset + limit
    result = searcher.search(parsed_query, limit=raw_limit)

    # Generate snippets from the text query
    snippet_generator = tantivy.SnippetGenerator.create(searcher, parsed_query, schema, "body")
    snippet_generator.set_max_num_chars(200)

    hits = []
    for score, doc_addr in result.hits[offset:]:
        doc = searcher.doc(doc_addr)

        ts = doc.get_first("timestamp")
        ts_str = None
        if ts is not None:
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)

        snippet = snippet_generator.snippet_from_doc(doc)

        hits.append({
            "score": round(score, 4),
            "session_id": doc.get_first("session_id"),
            "project_id": doc.get_first("project_id"),
            "line_num": doc.get_first("line_num"),
            "from_role": doc.get_first("from_role"),
            "timestamp": ts_str,
            "archived": doc.get_first("archived"),
            "snippet": snippet.to_html(),
        })

    result_dict = {
        "query": query_str,
        "total_hits": result.count,
        "limit": limit,
        "offset": offset,
        "hits": hits,
    }
    if to_json:
        import orjson

        return orjson.dumps(result_dict, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode()
    return result_dict
