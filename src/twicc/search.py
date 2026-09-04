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
    hidden     — whether the session is hidden from the user (boolean filter)
    spawned_by — session_id of the session that spawned this one (exact match via raw tokenizer)
    spawn_root — session_id of the root of this session's spawn tree (exact match via raw tokenizer)
"""

from __future__ import annotations

import logging
import math
import shutil
import threading
from collections import defaultdict
from datetime import datetime, UTC
from typing import TYPE_CHECKING, NamedTuple

import tantivy
from tantivy import Filter, Occur, Query, TextAnalyzerBuilder, Tokenizer

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (initialized once at startup via init_search_index)
# ---------------------------------------------------------------------------

_index: tantivy.Index | None = None
_writer = None  # tantivy.IndexWriter — no public type hint in tantivy-py
_schema: tantivy.Schema | None = None
_writer_lock = threading.Lock()  # Serialize all writer operations (add, delete, commit)


class SearchIndexLockedError(RuntimeError):
    """Raised when the Tantivy writer lock is already held by another process.

    Distinguishes the recoverable "another process owns the index" case
    (handled by the CLI with a user-facing error message) from generic
    Tantivy failures, which still propagate as ``ValueError``.
    """

    def __init__(self, search_dir):
        self.search_dir = search_dir
        super().__init__(
            f"Search index writer lock at {search_dir} is held by another process."
        )


class SearchIndexWipeError(RuntimeError):
    """Raised when the search index dir cannot be wiped to migrate schema.

    Surfaces the case where the on-disk version does not match the in-memory
    schema (a CURRENT_SEARCH_VERSION bump), but the rmtree of the dir fails
    — typically because another process still holds open file handles
    inside it, or for a more mundane reason (permission denied, no space,
    etc.). Distinct from SearchIndexLockedError, which is specifically
    about Tantivy writer-lock contention.
    """

    def __init__(self, search_dir):
        self.search_dir = search_dir
        super().__init__(
            f"Could not wipe the search index dir at {search_dir} to migrate "
            f"its schema. Another process may be holding open files in it, "
            f"or the directory may have stricter permissions than expected."
        )


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
    builder.add_boolean_field("hidden", stored=True, indexed=True)
    builder.add_text_field("spawned_by", stored=True, tokenizer_name="raw")
    builder.add_text_field("spawn_root", stored=True, tokenizer_name="raw")
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


def _ensure_search_dir(search_dir: Path, current_version: str) -> None:
    """Wipe and recreate the search directory if the on-disk schema version doesn't match.

    Tantivy refuses to open an index whose on-disk schema disagrees with the in-memory
    schema. We guard against this by comparing a ``.schema-version`` file in the search
    directory against ``current_version`` (the caller passes ``str(settings.CURRENT_SEARCH_VERSION)``).
    On mismatch (or when the file is absent), the whole directory is removed and re-created so
    Tantivy initializes a fresh empty index.

    The ``.schema-version`` file is written AFTER the writer is successfully acquired
    (in ``init_search_index``), so it truthfully means "we fully booted with this schema
    version". This function only handles the wipe-on-mismatch decision and ensures the
    directory exists; it does NOT write the version file.

    The ``CURRENT_SEARCH_VERSION`` bump in settings.py drives both the Tantivy wipe
    (schema change) and the per-session re-index sweep (search content regeneration);
    they share the same version counter so bumping once is sufficient for both.
    """
    schema_version_file = search_dir / ".schema-version"

    if search_dir.exists():
        try:
            on_disk_version = schema_version_file.read_text().strip()
        except FileNotFoundError:
            on_disk_version = None

        if on_disk_version != current_version:
            logger.info(
                "Search index schema version mismatch (on-disk: %s, current: %s) — wiping index directory",
                on_disk_version,
                current_version,
            )
            try:
                shutil.rmtree(search_dir)
            except OSError as e:
                raise SearchIndexWipeError(search_dir) from e

    search_dir.mkdir(parents=True, exist_ok=True)


def init_search_index() -> None:
    """Initialize the search index, register the tokenizer, and create the writer.

    Must be called once at startup before any indexing or searching.
    """
    global _index, _writer, _schema

    from django.conf import settings
    from twicc.paths import get_search_dir

    current_version = str(settings.CURRENT_SEARCH_VERSION)

    _schema = _build_schema()
    search_dir = get_search_dir()
    _ensure_search_dir(search_dir, current_version)

    _index = tantivy.Index(_schema, path=str(search_dir))
    _register_tokenizer(_index)
    try:
        _writer = _index.writer(heap_size=50_000_000, num_threads=1)
    except ValueError as exc:
        # Tantivy raises a plain ValueError with "Failed to acquire Lockfile: LockBusy"
        # when the writer lock is already held — typically by an orphan TwiCC subprocess
        # (background compute worker that survived a hard kill of the main server) or
        # by another live TwiCC instance running on the same data directory. Surface a
        # specific exception so the CLI can render a user-friendly message instead of
        # a raw stack trace.
        message = str(exc)
        if "LockBusy" in message or "Lockfile" in message:
            _index = None
            _schema = None
            raise SearchIndexLockedError(search_dir) from exc
        raise

    # Write the version marker only after the writer has been successfully acquired.
    # This means the marker truthfully reflects "we fully booted with this schema version";
    # a half-baked index that crashed during init won't falsely claim to be up-to-date.
    schema_version_file = search_dir / ".schema-version"
    schema_version_file.write_text(current_version)

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
    hidden: bool = False,
    spawned_by_id: str | None = None,
    spawn_root_id: str | None = None,
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
        hidden: Whether the parent session is hidden from the user (default False).
        spawned_by_id: session_id of the session that spawned this one, or None.
        spawn_root_id: session_id of the root of this session's spawn tree, or None.
    """
    _check_writer()

    if timestamp is None:
        timestamp = datetime.now(UTC)
    elif timestamp.tzinfo is None:
        # Tantivy requires timezone-aware datetimes
        timestamp = timestamp.replace(tzinfo=UTC)

    doc = tantivy.Document()
    doc.add_text("body", body)
    doc.add_unsigned("line_num", line_num)
    doc.add_text("session_id", session_id)
    doc.add_text("project_id", project_id)
    doc.add_text("from_role", from_role)
    doc.add_date("timestamp", timestamp)
    doc.add_boolean("archived", archived)
    doc.add_boolean("hidden", hidden)
    # Empty string keeps Query.term_query symmetric: documents with no
    # spawner / no spawn root never accidentally match a spawned_by /
    # spawn_root filter.
    doc.add_text("spawned_by", spawned_by_id or "")
    doc.add_text("spawn_root", spawn_root_id or "")

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
    from twicc.core.enums import ItemKind
    from twicc.core.models import Session, SessionItem
    from twicc.core.serializers import session_compute_ready
    from twicc.providers.helpers import get_provider_helpers

    _check_writer()
    _check_index()

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.warning("reindex_session: session %s not found, skipping", session_id)
        return

    # Delete all existing documents for this session
    delete_session_documents(session_id)
    if not session_compute_ready(session):
        commit()
        logger.debug("reindex_session: obsolete session %s removed and skipped", session_id)
        return

    # Index title (if any)
    if session.title:
        index_document(
            session_id, session.project_id, 0, session.title,
            "title", session.created_at, session.archived,
            hidden=session.hidden,
            spawned_by_id=session.spawned_by_id,
            spawn_root_id=session.spawn_root_id,
        )

    # Index all user/assistant messages
    items = (
        SessionItem.objects
        .filter(session=session, kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE])
        .order_by("line_num")
    )
    for msg in get_provider_helpers(session.provider).get_indexable_messages(items):
        index_document(
            session_id, session.project_id, msg.line_num, msg.text,
            msg.from_role, msg.timestamp, session.archived,
            hidden=session.hidden,
            spawned_by_id=session.spawned_by_id,
            spawn_root_id=session.spawn_root_id,
        )

    # The compute version can change while extraction runs. Fail closed by
    # deleting everything again before the commit when that happened.
    fresh = Session.objects.filter(id=session_id).only("provider", "compute_version").first()
    if fresh is None or not session_compute_ready(fresh):
        delete_session_documents(session_id)
        commit()
        logger.debug("reindex_session: session %s became obsolete and was removed", session_id)
        return

    commit()
    logger.debug("reindex_session: session %s re-indexed", session_id)


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def _format_datetime_for_query(dt: datetime) -> str:
    """Format a datetime as an ISO 8601 string suitable for Tantivy query parsing."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
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
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: set[str] | None = None,
    siblings: set[str] | None = None,
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
        include_hidden: Whether to include hidden sessions alongside visible ones (default False).
        only_hidden: Whether to return only hidden sessions (default False).
        spawned_by: Filter to sessions directly spawned by this session_id (default None = no filter).
        spawn_tree: Filter to every session in the spawn tree containing this session_id —
            the caller is expected to pass the root id (resolved upstream via
            ``resolve_spawn_tree_filter``). Mutually exclusive with ``spawned_by`` and
            ``descendants``.
        descendants: Filter to this explicit set of session_ids (proper descendants of some target,
            resolved by the caller). An empty set means "no descendants" — returns no results.
            Mutually exclusive with ``spawned_by`` and ``spawn_tree``.
        siblings: Filter to this explicit set of session_ids (the siblings of some target — the
            other sessions spawned by the same parent, target excluded — resolved by the caller).
            An empty set means "no siblings" — returns no results. Mutually exclusive with
            ``spawned_by``, ``spawn_tree`` and ``descendants``.
        limit: Max number of session groups to return (default 20).
        offset: Pagination offset for session groups (default 0).

    Returns:
        SearchResults with grouped, scored, and snippet-annotated results.
    """
    if sum(x is not None for x in (spawned_by, spawn_tree, descendants, siblings)) > 1:
        raise ValueError(
            "search(): spawned_by, spawn_tree, descendants and siblings are mutually exclusive"
        )

    if (descendants is not None and not descendants) or (siblings is not None and not siblings):
        return SearchResults(query=query_str, total_sessions=0, results=[])

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

    # Hidden-session filters:
    # - only_hidden: restrict to hidden=True
    # - include_hidden (else branch): no filter added, both hidden and visible are returned
    # - spawned_by / spawn_tree / descendants is set (without include_hidden /
    #   only_hidden): no implicit hidden filter — when the caller asks about
    #   filiation, they want every matching session in the tree whatever its
    #   visibility (the spawn targets are almost always hidden sessions in
    #   practice)
    # - default: restrict to hidden=False (UI and CLI without --include-hidden see only
    #   visible sessions; hidden documents stay in the index for agent-owned searches)
    if only_hidden:
        clauses.append((Occur.Must, Query.term_query(_schema, "hidden", True)))
    elif (
        not include_hidden
        and spawned_by is None
        and spawn_tree is None
        and descendants is None
        and siblings is None
    ):
        clauses.append((Occur.Must, Query.term_query(_schema, "hidden", False)))

    if spawned_by is not None:
        clauses.append((Occur.Must, Query.term_query(_schema, "spawned_by", spawned_by)))

    if spawn_tree is not None:
        # OR with the session_id field so a standalone session (spawn_root=""
        # in the index because its DB spawn_root_id column is still NULL) is
        # also returned when queried by its own id. Same single-node-tree
        # fallback as ``twicc topology`` and the ORM callers.
        spawn_tree_clauses = [
            (Occur.Should, Query.term_query(_schema, "spawn_root", spawn_tree)),
            (Occur.Should, Query.term_query(_schema, "session_id", spawn_tree)),
        ]
        clauses.append((Occur.Must, Query.boolean_query(spawn_tree_clauses)))

    if descendants is not None:
        # Empty set is short-circuited above; here we always have at least one id.
        descendant_clauses = [
            (Occur.Should, Query.term_query(_schema, "session_id", sid))
            for sid in descendants
        ]
        clauses.append((Occur.Must, Query.boolean_query(descendant_clauses)))

    if siblings is not None:
        # Same shape as descendants: an explicit id set matched on session_id.
        # Empty set is short-circuited above; here we always have at least one id.
        sibling_clauses = [
            (Occur.Should, Query.term_query(_schema, "session_id", sid))
            for sid in siblings
        ]
        clauses.append((Occur.Must, Query.boolean_query(sibling_clauses)))

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
                    ts = ts.replace(tzinfo=UTC)
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
        session_max_score[sid] = max(session_max_score[sid], effective_score)

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
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: set[str] | None = None,
    siblings: set[str] | None = None,
    project_ids: list[str] | None = None,
    annotation_filters: list | None = None,
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
        include_hidden: If True, include hits from hidden sessions (default False).
        only_hidden: If True, restrict results to hidden sessions only. Mutually exclusive
            with ``include_hidden`` (caller's responsibility to enforce).
        spawned_by: If set, restrict results to sessions directly spawned by this session ID.
        spawn_tree: If set, restrict results to every session in the spawn tree containing
            this session ID — the caller is expected to pass the root id (resolved upstream
            via ``resolve_spawn_tree_filter``). Mutually exclusive with ``spawned_by`` and
            ``descendants``.
        descendants: If set, restrict results to this explicit set of session_ids
            (proper descendants of some target, resolved by the caller). An empty set
            returns no results. Mutually exclusive with ``spawned_by`` and ``spawn_tree``.
        siblings: If set, restrict results to this explicit set of session_ids (the siblings
            of some target — the other sessions spawned by the same parent, target excluded,
            resolved by the caller). An empty set returns no results. Mutually exclusive with
            ``spawned_by``, ``spawn_tree`` and ``descendants``.
        project_ids: If set, restrict results to hits in any of these projects — typically a
            project (plus its git worktrees) or a whole workspace's projects, resolved by the
            caller. An empty list returns no results (e.g. an empty workspace). Combines (AND)
            with every other filter, including the filiation ones.
        annotation_filters: If set (non-empty list), run an oversample-and-post-filter loop:
            Tantivy ranks the corpus, then Django ORM filters on ``Session.annotations``.
            The result dict gains ``annotation_filtered``, ``exhausted``, and ``partial`` keys.
            When ``None`` (default), the existing single-shot path runs unchanged.

    Returns:
        If ``to_json`` is True: a JSON string (pretty-printed, sorted keys).
        If ``to_json`` is False: a dict with ``query``, ``total_hits``, ``limit``, ``offset``,
        and ``hits`` keys. Each hit contains ``score``, ``session_id``, ``project_id``,
        ``line_num``, ``from_role``, ``timestamp``, ``archived``, and ``snippet``.
    """
    if sum(x is not None for x in (spawned_by, spawn_tree, descendants, siblings)) > 1:
        raise ValueError(
            "raw_search(): spawned_by, spawn_tree, descendants and siblings are mutually exclusive"
        )

    if (
        (descendants is not None and not descendants)
        or (siblings is not None and not siblings)
        or (project_ids is not None and not project_ids)
    ):
        # An empty explicit id/project set means "no candidates" — return
        # nothing without touching the index. For ``project_ids`` this covers an
        # empty workspace scope; a non-empty list falls through to the filter
        # clause below.
        result_dict = {
            "query": query_str,
            "total_hits": 0,
            "limit": limit,
            "offset": offset,
            "hits": [],
        }
        if to_json:
            import orjson

            return orjson.dumps(result_dict, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode()
        return result_dict

    index, schema = _init_read_only()

    try:
        text_query = index.parse_query(query_str, ["body"])
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

    # Build filter clauses for hidden / spawned_by / spawn_tree / descendants.
    # When any of the filiation flags is set without include_hidden /
    # only_hidden the implicit hidden=False filter is lifted: a filiation
    # query is an explicit ask about every session in the tree whatever its
    # visibility.
    clauses: list[tuple[Occur, Query]] = [(Occur.Must, text_query)]

    if only_hidden:
        clauses.append((Occur.Must, Query.term_query(schema, "hidden", True)))
    elif (
        not include_hidden
        and spawned_by is None
        and spawn_tree is None
        and descendants is None
        and siblings is None
    ):
        clauses.append((Occur.Must, Query.term_query(schema, "hidden", False)))

    if spawned_by is not None:
        clauses.append((Occur.Must, Query.term_query(schema, "spawned_by", spawned_by)))

    if spawn_tree is not None:
        # OR with the session_id field so a standalone session (spawn_root=""
        # in the index because its DB spawn_root_id column is still NULL) is
        # also returned when queried by its own id. Same single-node-tree
        # fallback as ``twicc topology`` and the ORM callers — mirrors the
        # equivalent block in ``search()``.
        spawn_tree_clauses = [
            (Occur.Should, Query.term_query(schema, "spawn_root", spawn_tree)),
            (Occur.Should, Query.term_query(schema, "session_id", spawn_tree)),
        ]
        clauses.append((Occur.Must, Query.boolean_query(spawn_tree_clauses)))

    if descendants is not None:
        # Empty set is short-circuited above; here we always have at least one id.
        descendant_clauses = [
            (Occur.Should, Query.term_query(schema, "session_id", sid))
            for sid in descendants
        ]
        clauses.append((Occur.Must, Query.boolean_query(descendant_clauses)))

    if siblings is not None:
        # Same shape as descendants: an explicit id set matched on session_id.
        # Empty set is short-circuited above; here we always have at least one id.
        sibling_clauses = [
            (Occur.Should, Query.term_query(schema, "session_id", sid))
            for sid in siblings
        ]
        clauses.append((Occur.Must, Query.boolean_query(sibling_clauses)))

    if project_ids:
        # Scope to a set of projects (a project plus its git worktrees, resolved
        # by the caller). AND-ed with every other clause; within the set the
        # term queries are OR-ed (Should). Mirrors the project-folding the UI
        # applies when listing a project's sessions.
        project_clauses = [
            (Occur.Should, Query.term_query(schema, "project_id", pid))
            for pid in project_ids
        ]
        clauses.append((Occur.Must, Query.boolean_query(project_clauses)))

    if len(clauses) == 1:
        parsed_query = clauses[0][1]
    else:
        parsed_query = Query.boolean_query(clauses)

    index.reload()
    searcher = index.searcher()

    # Generate snippets from the text query (use text_query, not the composite boolean query,
    # so highlights reflect the user's search terms rather than filter clauses)
    snippet_generator = tantivy.SnippetGenerator.create(searcher, text_query, schema, "body")
    snippet_generator.set_max_num_chars(200)

    def _hit_to_dict(score: float, doc_addr: object) -> dict:
        """Build the per-hit dict from a Tantivy (score, doc_addr) pair."""
        doc = searcher.doc(doc_addr)
        ts = doc.get_first("timestamp")
        ts_str = None
        if ts is not None:
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)
        snippet = snippet_generator.snippet_from_doc(doc)
        return {
            "score": round(score, 4),
            "session_id": doc.get_first("session_id"),
            "project_id": doc.get_first("project_id"),
            "line_num": doc.get_first("line_num"),
            "from_role": doc.get_first("from_role"),
            "timestamp": ts_str,
            "archived": doc.get_first("archived"),
            "snippet": snippet.to_html(),
        }

    if annotation_filters:
        # Oversample-and-post-filter loop.
        # Tantivy's Python binding does not expose a native offset parameter: the existing
        # code works by pre-fetching `offset + limit` hits and slicing in Python.  We keep
        # one `tantivy_consumed` counter (how many Tantivy hits we have already examined)
        # and fetch progressively larger batches until we have enough post-filter matches
        # or Tantivy is exhausted.
        from twicc.cli._annotation_filters import apply_annotation_filters
        from twicc.core.models import Session

        # Score-ordered (session_id, (score, doc_addr)) tuples that passed the ORM filter.
        matched_pairs: list[tuple[str, tuple[float, object]]] = []
        tantivy_consumed = offset
        batch_size = max(limit, 20)
        max_iterations = 50
        iteration = 0
        exhausted = False
        result = None  # set on first loop iteration; may stay None if limit == 0

        while len(matched_pairs) < limit and iteration < max_iterations:
            # Fetch enough hits to cover what we have already consumed plus the next batch.
            fetch_limit = tantivy_consumed + batch_size
            result = searcher.search(parsed_query, limit=fetch_limit)
            all_hits = result.hits
            # The slice we haven't examined yet.
            batch_hits = all_hits[tantivy_consumed:]
            if not batch_hits:
                exhausted = True
                break
            # Build (session_id, hit_pair) tuples preserving Tantivy score order.
            ordered_pairs = [
                (searcher.doc(doc_addr).get_first("session_id"), (score, doc_addr))
                for (score, doc_addr) in batch_hits
            ]
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
            tantivy_consumed += len(batch_hits)
            # If Tantivy returned fewer hits than we asked for it is exhausted.
            if len(all_hits) < fetch_limit:
                exhausted = True
                break
            iteration += 1

        partial = (not exhausted) and len(matched_pairs) < limit

        # Re-use `result` from the last Tantivy query to report total_hits.
        # `result` stays None only when limit == 0 (loop never entered).
        total_hits = result.count if result is not None else 0

        hits = [_hit_to_dict(score, doc_addr) for _, (score, doc_addr) in matched_pairs]

        result_dict = {
            "query": query_str,
            "total_hits": total_hits,
            "limit": limit,
            "offset": offset,
            "hits": hits,
            "annotation_filtered": True,
            "exhausted": exhausted,
            "partial": partial,
        }
    else:
        # Existing single-shot path — behaviour is byte-identical to before this change.
        raw_limit = offset + limit
        result = searcher.search(parsed_query, limit=raw_limit)

        hits = [_hit_to_dict(score, doc_addr) for score, doc_addr in result.hits[offset:]]

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
