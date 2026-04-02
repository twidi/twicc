"""
Batch-only computation for session metadata.

Contains functions used exclusively by the background compute task:
- ContentAnalysis / analyze_content: single-pass content extraction (optimized for batch)
- compute_session_metadata: full metadata computation for a session (runs in worker process)
- apply_session_complete: applies computed results to the database (runs in main process)

All shared extraction functions (compute_item_kind, get_tool_use_entries, etc.)
remain in compute.py and are used by both batch and live (watcher) code paths.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import NamedTuple

import orjson
from django.conf import settings

from twicc.compute import (
    AGENT_TOOL_NAMES,
    GroupState,
    ItemDisplayLevel,
    ItemKind,
    VISIBLE_CONTENT_TYPES,
    _SYSTEM_XML_PREFIXES,
    _TOOL_PATH_FIELDS,
    compute_file_change_stats,
    compute_item_cost_and_usage,
    compute_item_metadata,
    ensure_project_directory,
    ensure_project_git_root,
    extract_item_timestamp,
    extract_title_from_user_message,
    get_project_git_root,
    resolve_git_for_item,
    resolve_git_from_path,
    transform_local_command_output,
    transform_task_notification,
    update_project_metadata,
)
from twicc.core.models import AgentLink, Session, SessionItem, SessionType, ToolResultLink

logger = logging.getLogger(__name__)


# =============================================================================
# Single-Pass Content Analysis
# =============================================================================


class ContentAnalysis(NamedTuple):
    """
    Result of single-pass content analysis for batch computation.

    Extracts all information from a parsed JSONL item's message.content
    in a single traversal, replacing multiple individual function calls
    that each scan the content array separately.

    All individual extraction functions (get_tool_use_entries, get_tool_result_id, etc.)
    are preserved in compute.py for use by the live watcher code path.
    """
    # Content visibility (replaces _has_visible_content)
    has_visible_content: bool
    # First text block's text value (replaces extract_text_from_content)
    text_content: str | None
    # Content is a string starting with system XML prefix (replaces _is_system_xml_content)
    is_system_xml: bool
    # User message has a tool_result in content (replaces is_tool_result_item)
    has_tool_result: bool
    # First tool_result's tool_use_id (replaces get_tool_result_id)
    tool_result_id: str | None
    # Error from first tool_result (replaces get_tool_result_error)
    tool_result_error: str | None
    # tool_use_id -> tool_name mapping (replaces get_tool_use_entries)
    tool_use_entries: dict[str, str]
    # [(tool_use_id, is_background)] for Task/Agent tools (replaces get_task_tool_uses)
    task_tool_uses: list[tuple[str, bool]]
    # Absolute file paths from tool_use inputs (replaces extract_paths_from_tool_uses)
    file_paths: list[str]
    # Raw prefix/suffix detection (replaces _has_collapsible_prefix/_suffix)
    # Caller must filter by kind (only meaningful for USER_MESSAGE / ASSISTANT_MESSAGE)
    has_prefix: bool
    has_suffix: bool
    # (tool_use_id, agent_id) from tool_result with agentId (replaces get_tool_result_agent_info)
    tool_result_agent_info: tuple[str, str] | None


# Shared empty constants to avoid allocating new empty collections for every item
# that doesn't have the relevant content. MUST NOT be mutated.
_EMPTY_TOOL_USE_ENTRIES: dict[str, str] = {}
_EMPTY_TASK_TOOL_USES: list[tuple[str, bool]] = []
_EMPTY_FILE_PATHS: list[str] = []

_EMPTY_ANALYSIS = ContentAnalysis(
    has_visible_content=False,
    text_content=None,
    is_system_xml=False,
    has_tool_result=False,
    tool_result_id=None,
    tool_result_error=None,
    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
    task_tool_uses=_EMPTY_TASK_TOOL_USES,
    file_paths=_EMPTY_FILE_PATHS,
    has_prefix=False,
    has_suffix=False,
    tool_result_agent_info=None,
)


def analyze_content(parsed_json: dict) -> ContentAnalysis:
    """
    Single-pass content analysis for batch computation.

    Extracts all information from parsed_json's message.content in one traversal,
    replacing multiple function calls that each traverse the content array separately.

    Consolidates the work of: _has_visible_content, extract_text_from_content,
    _is_system_xml_content, is_tool_result_item, get_tool_result_id,
    get_tool_result_error, get_tool_use_entries, get_task_tool_uses,
    extract_paths_from_tool_uses, _has_collapsible_prefix/_suffix,
    and get_tool_result_agent_info.

    Args:
        parsed_json: Parsed JSONL line dict

    Returns:
        ContentAnalysis with all extracted fields
    """
    # Get message.content
    message = parsed_json.get('message')
    if not isinstance(message, dict):
        return _EMPTY_ANALYSIS

    content = message.get('content')
    entry_type = parsed_json.get('type')

    # --- String content (user messages can have string content) ---
    if isinstance(content, str):
        if not content:
            # Empty string: not visible, no text, not XML
            return _EMPTY_ANALYSIS
        # Non-empty string
        stripped_for_xml = content.lstrip()
        is_system_xml = any(stripped_for_xml.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)
        return ContentAnalysis(
            has_visible_content=True,
            text_content=content.strip(),
            is_system_xml=is_system_xml,
            has_tool_result=False,
            tool_result_id=None,
            tool_result_error=None,
            tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
            task_tool_uses=_EMPTY_TASK_TOOL_USES,
            file_paths=_EMPTY_FILE_PATHS,
            has_prefix=False,
            has_suffix=False,
            tool_result_agent_info=None,
        )

    # --- Not a list or empty list -> nothing to traverse ---
    if not isinstance(content, list) or not content:
        return _EMPTY_ANALYSIS

    # --- List content: single traversal ---

    # Prefix/suffix: check first and last items
    first_item = content[0]
    last_item = content[-1]
    has_prefix = isinstance(first_item, dict) and first_item.get('type') not in VISIBLE_CONTENT_TYPES
    has_suffix = isinstance(last_item, dict) and last_item.get('type') not in VISIBLE_CONTENT_TYPES

    # Common accumulators
    has_visible = False
    text_content: str | None = None

    if entry_type == 'assistant':
        # --- Assistant message: tool_use info + visibility ---
        tool_use_entries: dict[str, str] = {}
        task_tool_uses: list[tuple[str, bool]] = []
        file_paths: list[str] = []

        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')

            if item_type in VISIBLE_CONTENT_TYPES:
                has_visible = True
                # Extract text from first text block
                if item_type == 'text' and text_content is None:
                    text_val = item.get('text')
                    if isinstance(text_val, str):
                        text_content = text_val.strip()

            elif item_type == 'tool_use':
                tu_id = item.get('id')
                tu_name = item.get('name', '')
                if tu_id:
                    tool_use_entries[tu_id] = tu_name

                    # Task/Agent tool_uses
                    if tu_name in AGENT_TOOL_NAMES:
                        is_bg = bool(isinstance(item.get('input'), dict) and item['input'].get('run_in_background'))
                        task_tool_uses.append((tu_id, is_bg))

                    # File path extraction for git resolution
                    if tu_name in _TOOL_PATH_FIELDS:
                        field_name = _TOOL_PATH_FIELDS[tu_name]
                        inputs = item.get('input')
                        if isinstance(inputs, dict):
                            path = inputs.get(field_name)
                            if isinstance(path, str) and path.startswith('/'):
                                file_paths.append(path)

        return ContentAnalysis(
            has_visible_content=has_visible,
            text_content=text_content,
            is_system_xml=False,
            has_tool_result=False,
            tool_result_id=None,
            tool_result_error=None,
            tool_use_entries=tool_use_entries or _EMPTY_TOOL_USE_ENTRIES,
            task_tool_uses=task_tool_uses or _EMPTY_TASK_TOOL_USES,
            file_paths=file_paths or _EMPTY_FILE_PATHS,
            has_prefix=has_prefix,
            has_suffix=has_suffix,
            tool_result_agent_info=None,
        )

    if entry_type == 'user':
        # --- User message: tool_result info + visibility + text ---
        # Check for system XML in list content (single text entry starting with a system prefix)
        is_system_xml = False
        if len(content) == 1:
            only_item = content[0]
            if isinstance(only_item, dict) and only_item.get('type') == 'text':
                text_val = only_item.get('text')
                if isinstance(text_val, str):
                    stripped_xml = text_val.lstrip()
                    is_system_xml = any(stripped_xml.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)

        has_tool_result = False
        first_tool_result_id: str | None = None
        # Sentinel: ... means "first tool_result not found yet"
        first_tool_result_error: str | None | type(...) = ...

        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')

            if item_type in VISIBLE_CONTENT_TYPES:
                has_visible = True
                # Extract text from first text block
                if item_type == 'text' and text_content is None:
                    text_val = item.get('text')
                    if isinstance(text_val, str):
                        text_content = text_val.strip()

            elif item_type == 'tool_result':
                if not has_tool_result:
                    # First tool_result: extract id and error
                    has_tool_result = True
                    first_tool_result_id = item.get('tool_use_id')

                    if not item.get('is_error'):
                        first_tool_result_error = None
                    else:
                        error_content = item.get('content', '')
                        if isinstance(error_content, str):
                            stripped = error_content.strip()
                            if stripped.startswith('<tool_use_error>') and stripped.endswith('</tool_use_error>'):
                                first_tool_result_error = stripped[len('<tool_use_error>'):-len('</tool_use_error>')].strip() or 'Unknown error'
                            elif stripped.startswith('Exit code '):
                                first_tool_result_error = stripped.split('\n', 1)[0]
                            else:
                                first_tool_result_error = stripped or 'Unknown error'
                        else:
                            first_tool_result_error = 'Unknown error'

        # Resolve error sentinel
        tool_result_error = None if first_tool_result_error is ... else first_tool_result_error

        # Agent info: requires both tool_result_id and root-level toolUseResult.agentId
        agent_info = None
        if first_tool_result_id:
            tool_use_result = parsed_json.get('toolUseResult')
            if isinstance(tool_use_result, dict):
                agent_id = tool_use_result.get('agentId')
                if agent_id:
                    agent_info = (first_tool_result_id, agent_id)

        return ContentAnalysis(
            has_visible_content=has_visible,
            text_content=text_content,
            is_system_xml=is_system_xml,
            has_tool_result=has_tool_result,
            tool_result_id=first_tool_result_id,
            tool_result_error=tool_result_error,
            tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
            task_tool_uses=_EMPTY_TASK_TOOL_USES,
            file_paths=_EMPTY_FILE_PATHS,
            has_prefix=has_prefix,
            has_suffix=has_suffix,
            tool_result_agent_info=agent_info,
        )

    # --- Other message types: just visibility + text + prefix/suffix ---
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')
        if item_type in VISIBLE_CONTENT_TYPES:
            has_visible = True
            if item_type == 'text' and text_content is None:
                text_val = item.get('text')
                if isinstance(text_val, str):
                    text_content = text_val.strip()

    return ContentAnalysis(
        has_visible_content=has_visible,
        text_content=text_content,
        is_system_xml=False,
        has_tool_result=False,
        tool_result_id=None,
        tool_result_error=None,
        tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
        task_tool_uses=_EMPTY_TASK_TOOL_USES,
        file_paths=_EMPTY_FILE_PATHS,
        has_prefix=has_prefix,
        has_suffix=has_suffix,
        tool_result_agent_info=None,
    )


def compute_session_metadata(session_id: str, result_queue) -> None:
    """
    Compute metadata for all items in a session.

    Sends all results via result_queue as a single message per session.
    Does NOT write to the database directly.
    The caller is responsible for consuming the queue and applying changes.

    Uses single-pass content analysis (analyze_content) to avoid redundant
    content array traversals. The following calls are replaced by analysis fields:
    - get_tool_use_entries    -> analysis.tool_use_entries
    - get_task_tool_uses      -> analysis.task_tool_uses
    - get_tool_result_id      -> analysis.tool_result_id
    - get_tool_result_error   -> analysis.tool_result_error
    - get_tool_result_agent_info -> analysis.tool_result_agent_info
    - _detect_prefix_suffix   -> analysis.has_prefix/has_suffix

    Args:
        session_id: The session ID
        result_queue: Queue to send results (multiprocessing.Queue or queue.Queue)
    """
    from django.db import connection

    # Ensure this process/thread has its own database connection
    connection.close()

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found for metadata computation")
        result_queue.put(orjson.dumps({
            'type': 'error',
            'session_id': session_id,
            'error': 'Session not found',
        }))
        return

    queryset = SessionItem.objects.filter(session=session).order_by('line_num')

    state = GroupState()
    items_to_update: list[SessionItem] = []
    all_item_updates: list[dict] = []
    all_tool_result_links: dict[tuple[str, int], dict] = {}  # (tool_use_id, tool_result_line_num) -> serialized
    all_agent_links: dict[tuple[str, str], dict] = {}  # (agent_id, tool_use_id) -> serialized
    content_overrides: list[dict] = []
    batch_size = 500

    def serialize_item(item: SessionItem) -> dict:
        return {
            'id': item.id,
            'display_level': item.display_level,
            'group_head': item.group_head,
            'group_tail': item.group_tail,
            'kind': item.kind,
            'message_id': item.message_id,
            'cost': str(item.cost) if item.cost is not None else None,
            'context_usage': item.context_usage,
            'timestamp': item.timestamp.isoformat() if item.timestamp else None,
            'git_directory': item.git_directory,
            'git_branch': item.git_branch,
        }

    def flush_items(items: list[SessionItem]) -> None:
        for item in items:
            serialized = serialize_item(item)
            if serialized != original_serialized.get(item.id):
                all_item_updates.append(serialized)

    def serialize_tool_result_link(link: ToolResultLink) -> dict:
        return {
            'session_id': link.session_id,
            'tool_use_line_num': link.tool_use_line_num,
            'tool_result_line_num': link.tool_result_line_num,
            'tool_use_id': link.tool_use_id,
            'tool_name': link.tool_name,
            'tool_result_at': link.tool_result_at.isoformat() if link.tool_result_at else None,
            'extra': link.extra,
            'error': link.error,
        }

    def serialize_agent_link(link: AgentLink) -> dict:
        return {
            'session_id': link.session_id,
            'tool_use_line_num': link.tool_use_line_num,
            'tool_use_id': link.tool_use_id,
            'agent_id': link.agent_id,
            'is_background': link.is_background,
            'started_at': link.started_at.isoformat() if link.started_at else None,
        }

    tool_use_map: dict[str, tuple[int, str]] = {}
    task_tool_use_map: dict[str, tuple[int, bool, datetime]] = {}
    initial_title_set = False
    session_titles: dict[str, str] = {}
    user_message_count = 0
    affected_days: set[str] = set()
    seen_message_ids: set[str] = set()
    last_context_usage: int | None = None
    first_timestamp: datetime | None = None
    last_started_at: datetime | None = None
    last_updated_at: datetime | None = None
    first_cwd: str | None = None
    last_cwd: str | None = None
    last_cwd_git_branch: str | None = None
    last_model: str | None = None
    last_slug: str | None = None
    last_resolved_git_directory: str | None = None
    last_resolved_git_branch: str | None = None
    agent_tool_result_counts: dict[str, tuple[int, datetime | None]] = {}
    agent_stopped_list: list[dict] = []
    original_serialized: dict[int, dict] = {}

    # Load existing links for change detection
    original_tool_result_links: dict[tuple[str, int], dict] = {}
    original_tool_result_links_ids: dict[tuple[str, int], int] = {}
    for link in ToolResultLink.objects.filter(session_id=session_id):
        key = (link.tool_use_id, link.tool_result_line_num)
        original_tool_result_links[key] = serialize_tool_result_link(link)
        original_tool_result_links_ids[key] = link.id

    original_agent_links: dict[tuple[str, str], dict] = {}
    original_agent_links_ids: dict[tuple[str, str], int] = {}
    for link in AgentLink.objects.filter(session_id=session_id):
        key = (link.agent_id, link.tool_use_id)
        original_agent_links[key] = serialize_agent_link(link)
        original_agent_links_ids[key] = link.id

    for item in queryset.iterator(chunk_size=batch_size):
        # Snapshot original state before any computation, for change detection
        original_serialized[item.id] = serialize_item(item)

        try:
            parsed = orjson.loads(item.content)
        except orjson.JSONDecodeError:
            logger.warning(f"Invalid JSON in item {item.session_id}:{item.line_num}")
            parsed = {}

        # Transform task-notification XML into standard tool_result format
        new_content = transform_task_notification(parsed)
        if new_content is None:
            new_content = transform_local_command_output(parsed)
        if new_content is not None and new_content != item.content:
            item.content = new_content
            content_overrides.append({'id': item.id, 'content': new_content})

        # Single-pass content analysis (replaces multiple individual content traversals)
        analysis = analyze_content(parsed)

        # Compute display_level and kind
        metadata = compute_item_metadata(parsed)
        item.display_level = metadata['display_level']
        item.kind = metadata['kind']

        # Extract timestamp
        item.timestamp = extract_item_timestamp(parsed)
        if first_timestamp is None and item.timestamp is not None:
            first_timestamp = item.timestamp
            last_started_at = first_timestamp
            affected_days.add(first_timestamp.date().isoformat())
        if item.timestamp is not None:
            last_updated_at = item.timestamp
        if (
            item.timestamp is not None
            and parsed.get('type') == 'progress'
            and isinstance(parsed.get('data'), dict)
            and parsed['data'].get('hookEvent') == 'SessionStart'
        ):
            last_started_at = item.timestamp

        # Compute cost and context usage
        compute_item_cost_and_usage(item, parsed, seen_message_ids)
        if item.context_usage is not None:
            last_context_usage = item.context_usage

        # Extract runtime environment fields
        if cwd := parsed.get('cwd'):
            if first_cwd is None:
                first_cwd = cwd
            last_cwd = cwd
        if cwd_git_branch := parsed.get('gitBranch'):
            last_cwd_git_branch = cwd_git_branch
        if (message := parsed.get('message')) and isinstance(message, dict):
            if model := message.get('model'):
                last_model = model
        if item_slug := parsed.get('slug'):
            last_slug = item_slug

        # Resolve git directory/branch from tool_use paths
        if item.git_directory is not None:
            last_resolved_git_directory = item.git_directory
            last_resolved_git_branch = item.git_branch
        else:
            git_resolution = resolve_git_for_item(parsed)
            if git_resolution is not None:
                item.git_directory, item.git_branch = git_resolution
                last_resolved_git_directory = item.git_directory
                last_resolved_git_branch = item.git_branch

        # Handle title extraction
        if item.kind == ItemKind.USER_MESSAGE and not initial_title_set:
            title = extract_title_from_user_message(parsed)
            if title:
                session_titles[session_id] = title
                initial_title_set = True
        if item.kind == ItemKind.CUSTOM_TITLE:
            custom_title = parsed.get('customTitle')
            target_session_id = parsed.get('sessionId', session_id)
            if custom_title and isinstance(custom_title, str):
                session_titles[target_session_id] = custom_title
        if item.kind == ItemKind.USER_MESSAGE:
            user_message_count += 1
        if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost):
            affected_days.add(item.timestamp.date().isoformat())

        # Use analysis fields instead of individual function calls
        for tu_id, tu_name in analysis.tool_use_entries.items():
            tool_use_map[tu_id] = (item.line_num, tu_name)
        for tu_id, is_background in analysis.task_tool_uses:
            task_tool_use_map[tu_id] = (item.line_num, is_background, item.timestamp)
        tool_result_ref = analysis.tool_result_id
        if tool_result_ref and tool_result_ref in tool_use_map:
            tu_line_num, tu_name = tool_use_map[tool_result_ref]
            extra = compute_file_change_stats(parsed) if tu_name in ('Edit', 'Write') else None
            error = analysis.tool_result_error
            all_tool_result_links[(tool_result_ref, item.line_num)] = serialize_tool_result_link(ToolResultLink(
                session_id=session_id,
                tool_use_line_num=tu_line_num,
                tool_result_line_num=item.line_num,
                tool_use_id=tool_result_ref,
                tool_name=tu_name,
                tool_result_at=item.timestamp,
                extra=extra,
                error=error,
            ))
            if tu_name in AGENT_TOOL_NAMES:
                prev_count, _ = agent_tool_result_counts.get(tool_result_ref, (0, None))
                agent_tool_result_counts[tool_result_ref] = (prev_count + 1, item.timestamp)
        if analysis.tool_result_agent_info:
            tu_id, agent_id = analysis.tool_result_agent_info
            if tu_id in task_tool_use_map:
                line_num, is_background, started_at = task_tool_use_map[tu_id]
                all_agent_links[(agent_id, tu_id)] = serialize_agent_link(AgentLink(
                    session_id=session_id,
                    tool_use_line_num=line_num,
                    tool_use_id=tu_id,
                    agent_id=agent_id,
                    is_background=is_background,
                    started_at=started_at,
                ))
                del task_tool_use_map[tu_id]

        # Prefix/suffix for group state machine
        has_prefix, has_suffix = False, False
        if item.display_level == ItemDisplayLevel.ALWAYS and item.kind in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE):
            has_prefix, has_suffix = analysis.has_prefix, analysis.has_suffix
        info = state.process_item(
            line_num=item.line_num,
            display_level=item.display_level,
            has_prefix=has_prefix,
            has_suffix=has_suffix,
            item_ref=item,
        )
        item.group_head = info.group_head
        items_to_update.extend(info.closed_items)
        if item.display_level == ItemDisplayLevel.DEBUG_ONLY:
            items_to_update.append(item)
        elif item.display_level == ItemDisplayLevel.ALWAYS and not has_suffix:
            items_to_update.append(item)

        # Flush batches
        if len(items_to_update) >= batch_size:
            flush_items(items_to_update)
            items_to_update = []

    # Finalize pending groups
    finalized = state.finalize()
    items_to_update.extend(finalized)

    flush_items(items_to_update)

    # Diff tool result links: create / update / delete
    trl_to_create: list[dict] = []
    trl_to_update: list[dict] = []
    for key, serialized in all_tool_result_links.items():
        original = original_tool_result_links.get(key)
        if original is None:
            trl_to_create.append(serialized)
        elif serialized != original:
            serialized['id'] = original_tool_result_links_ids[key]
            trl_to_update.append(serialized)
    trl_to_delete: list[int] = [
        pk for key, pk in original_tool_result_links_ids.items() if key not in all_tool_result_links
    ]

    # Diff agent links: create / update / delete
    agent_links_to_create: list[dict] = []
    agent_links_to_update: list[dict] = []
    for key, serialized in all_agent_links.items():
        original = original_agent_links.get(key)
        if original is None:
            agent_links_to_create.append(serialized)
        elif serialized != original:
            # Carry the existing PK so apply_session_complete can bulk_update
            serialized['id'] = original_agent_links_ids[key]
            agent_links_to_update.append(serialized)
    agent_links_to_delete: list[int] = [
        pk for key, pk in original_agent_links_ids.items() if key not in all_agent_links
    ]

    # Determine which agents have stopped (using all_agent_links dict)
    for tu_id, (result_count, last_ts) in agent_tool_result_counts.items():
        if last_ts is None:
            continue
        for link in all_agent_links.values():
            if link['tool_use_id'] == tu_id:
                required = 2 if link.get('is_background') else 1
                if result_count >= required:
                    agent_stopped_list.append({
                        'agent_session_id': link['agent_id'],
                        'stopped_at': last_ts.isoformat(),
                    })
                break

    project_directory = first_cwd if first_cwd and session.type == SessionType.SESSION else None

    if not last_resolved_git_directory and last_cwd:
        cwd_git = resolve_git_from_path(last_cwd)
        if cwd_git:
            last_resolved_git_directory, last_resolved_git_branch = cwd_git

    result_queue.put(orjson.dumps({
        'type': 'session_complete',
        'session_id': session_id,
        'project_id': session.project_id,
        'item_updates': all_item_updates,
        'item_fields': ['display_level', 'group_head', 'group_tail', 'kind', 'message_id', 'cost', 'context_usage', 'timestamp', 'git_directory', 'git_branch'],
        'content_overrides': content_overrides,
        'tool_result_links_to_create': trl_to_create,
        'tool_result_links_to_update': trl_to_update,
        'tool_result_links_to_delete': trl_to_delete,
        'agent_links_to_create': agent_links_to_create,
        'agent_links_to_update': agent_links_to_update,
        'agent_links_to_delete': agent_links_to_delete,
        'session_fields': {
            'compute_version': settings.CURRENT_COMPUTE_VERSION,
            'user_message_count': user_message_count,
            'context_usage': last_context_usage,
            'cwd': last_cwd,
            'cwd_git_branch': last_cwd_git_branch,
            'git_directory': last_resolved_git_directory,
            'git_branch': last_resolved_git_branch,
            'model': last_model,
            'slug': last_slug,
            'created_at': first_timestamp.isoformat() if first_timestamp else None,
            'last_started_at': last_started_at.isoformat() if last_started_at else None,
            'last_updated_at': datetime.fromtimestamp(session.mtime, tz=timezone.utc).isoformat() if session.mtime else (last_updated_at.isoformat() if last_updated_at else None),
            'last_stopped_at': datetime.fromtimestamp(session.mtime, tz=timezone.utc).isoformat() if session.mtime else None,
        },
        'titles': session_titles,
        'project_directory': project_directory,
        'affected_days': sorted(affected_days) if affected_days else None,
        'agent_stopped': agent_stopped_list or None,
    }))

    connection.close()


# =============================================================================
# Apply Batch Results: apply_session_complete
# =============================================================================


def apply_session_complete(msg: dict) -> None:
    """
    Apply all results for a session in one go.

    This handles the 'session_complete' message type that contains
    all updates for a session in a single message.

    Runs in the main process (called via sync_to_async from consume_compute_results).
    """
    session_id = msg['session_id']

    # 1. Apply item updates (only items that changed)
    item_updates = msg.get('item_updates', [])
    item_fields = msg.get('item_fields', [])
    if item_updates and item_fields:
        items = [
            SessionItem(id=upd['id'], **{
                field: Decimal(value) if (value := upd.get(field)) is not None and field == 'cost' else value
                for field in item_fields
            })
            for upd in item_updates
        ]
        SessionItem.objects.bulk_update(items, item_fields, 50)

    # 2. Apply content overrides (rare: only transformed task-notification items)
    content_overrides = msg.get('content_overrides', [])
    if content_overrides:
        items = [
            SessionItem(id=ovr['id'], content=ovr['content'])
            for ovr in content_overrides
        ]
        SessionItem.objects.bulk_update(items, ['content'], 50)

    # 3. Sync tool result links (diff-based: create/update/delete)
    trl_to_create = msg.get('tool_result_links_to_create', [])
    if trl_to_create:
        links = [
            ToolResultLink(
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_result_line_num=d['tool_result_line_num'],
                tool_use_id=d['tool_use_id'],
                tool_name=d['tool_name'],
                tool_result_at=datetime.fromisoformat(d['tool_result_at']) if d.get('tool_result_at') else None,
                extra=d.get('extra'),
                error=d.get('error'),
            )
            for d in trl_to_create
        ]
        ToolResultLink.objects.bulk_create(links, ignore_conflicts=True)

    trl_to_update = msg.get('tool_result_links_to_update', [])
    if trl_to_update:
        trl_update_fields = ['tool_use_line_num', 'tool_name', 'tool_result_at', 'extra', 'error']
        links = [
            ToolResultLink(
                id=d['id'],
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_result_line_num=d['tool_result_line_num'],
                tool_use_id=d['tool_use_id'],
                tool_name=d['tool_name'],
                tool_result_at=datetime.fromisoformat(d['tool_result_at']) if d.get('tool_result_at') else None,
                extra=d.get('extra'),
                error=d.get('error'),
            )
            for d in trl_to_update
        ]
        ToolResultLink.objects.bulk_update(links, trl_update_fields, 50)

    trl_to_delete = msg.get('tool_result_links_to_delete', [])
    if trl_to_delete:
        ToolResultLink.objects.filter(id__in=trl_to_delete).delete()

    # 4. Sync agent links (diff-based: create/update/delete)
    agent_links_to_create = msg.get('agent_links_to_create', [])
    if agent_links_to_create:
        links = [
            AgentLink(
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_use_id=d['tool_use_id'],
                agent_id=d['agent_id'],
                is_background=d['is_background'],
                started_at=datetime.fromisoformat(d['started_at']) if d.get('started_at') else None,
            )
            for d in agent_links_to_create
        ]
        AgentLink.objects.bulk_create(links, ignore_conflicts=True)

    agent_links_to_update = msg.get('agent_links_to_update', [])
    if agent_links_to_update:
        agent_link_fields = ['tool_use_line_num', 'is_background', 'started_at']
        links = [
            AgentLink(
                id=d['id'],
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_use_id=d['tool_use_id'],
                agent_id=d['agent_id'],
                is_background=d['is_background'],
                started_at=datetime.fromisoformat(d['started_at']) if d.get('started_at') else None,
            )
            for d in agent_links_to_update
        ]
        AgentLink.objects.bulk_update(links, agent_link_fields, 50)

    agent_links_to_delete = msg.get('agent_links_to_delete', [])
    if agent_links_to_delete:
        AgentLink.objects.filter(id__in=agent_links_to_delete).delete()

    # 5. Update session fields (always includes compute_version)
    session_fields = msg.get('session_fields', {})
    if session_fields:
        # Handle datetime fields
        for dt_field in ('created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at'):
            if dt_field in session_fields and session_fields[dt_field] is not None:
                session_fields[dt_field] = datetime.fromisoformat(session_fields[dt_field])
        Session.objects.filter(id=session_id).update(**session_fields)

    # 6. Recalculate session costs from SessionItem data (idempotent, order-independent)
    session = Session.objects.get(id=session_id)
    session.recalculate_costs()
    session.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

    # 7. Recalculate parent session costs if subagent
    if session.parent_session_id:
        parent = Session.objects.get(id=session.parent_session_id)
        parent.recalculate_costs()
        parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

    # 8. Update session titles
    titles = msg.get('titles', {})
    for target_id, title in titles.items():
        Session.objects.filter(id=target_id).update(title=title)

    # 9. Update project directory
    project_id = msg.get('project_id')
    project_directory = msg.get('project_directory')
    if project_id and project_directory:
        ensure_project_directory(project_id, project_directory)

    # 10. Resolve project git_root if session has git info but project doesn't
    session_git_dir = session_fields.get('git_directory') if session_fields else None
    if session_git_dir and project_id and get_project_git_root(project_id) is None:
        ensure_project_git_root(project_id)

    # 11. Update last_stopped_at for subagents that finished naturally
    agent_stopped = msg.get('agent_stopped')
    if agent_stopped:
        for entry in agent_stopped:
            stopped_at = datetime.fromisoformat(entry['stopped_at'])
            Session.objects.filter(id=entry['agent_session_id']).update(
                last_stopped_at=stopped_at, last_updated_at=stopped_at
            )

    # 12. Update project metadata (sessions_count, mtime, total_cost)
    if project_id:
        update_project_metadata(project_id)
