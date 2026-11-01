# Split SessionItemLink into ToolResultLink and AgentLink

**Date:** 2026-02-08
**Status:** DRAFT

## Overview

Replace the generic `SessionItemLink` table with two specialized tables: `ToolResultLink` (tool_use → tool_result) and `AgentLink` (Task tool_use → subagent). This improves clarity, eliminates the nullable `target_line_num` hack for agent links, renames fields to be self-documenting, and adds a `tool_use_id` field to both tables for precise matching — notably enabling correct identification of parallel agents sharing the same assistant message line.

## Problem

The current `SessionItemLink` model is a generic linking table with a `link_type` discriminator:

```python
class SessionItemLink(models.Model):
    session = models.ForeignKey(Session, ...)
    source_line_num = models.PositiveIntegerField()
    target_line_num = models.PositiveIntegerField(null=True, blank=True)
    link_type = models.CharField(max_length=50)       # "tool_result" or "agent"
    reference = models.CharField(max_length=255)       # tool_use_id or agent_id
```

This causes several issues:

1. **Semantic mismatch**: `target_line_num` is always NULL for agent links. It was made nullable solely to accommodate agent links — the schema doesn't match the data.

2. **Missing `tool_use_id` on agent links**: When an assistant message contains multiple Task tool_uses (parallel agents), there's no way to know which specific tool_use an agent corresponds to. The current code only stores `source_line_num` (the line of the assistant message) and `reference` (the agent_id). The view `tool_agent_id()` receives a `tool_id` from the frontend but cannot use it to query — it can only filter by `source_line_num`, which is ambiguous when multiple Task tool_uses share the same line.

3. **Confusing `reference` field**: Means "tool_use_id" for tool_result links and "agent_id" for agent links. Different semantics behind the same column name. Both link types actually need a `tool_use_id` — tool_result links to identify which tool_use the result belongs to, and agent links to identify which Task tool_use spawned the agent.

4. **Generic field names**: `source_line_num` and `target_line_num` don't convey what they actually represent. For tool_result links they're the tool_use line and tool_result line. For agent links, only the source (tool_use line) is used.

5. **All queries already filter by `link_type`**: Every single query in the codebase includes `link_type='tool_result'` or `link_type='agent'`. The generic table adds no benefit — the two types are never queried together.

## New Models

### ToolResultLink

Links a tool_use to its tool_result. Replaces `SessionItemLink` where `link_type='tool_result'`.

```python
class ToolResultLink(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="tool_result_links")
    tool_use_line_num = models.PositiveIntegerField()       # Line containing the tool_use
    tool_result_line_num = models.PositiveIntegerField()    # Line containing the tool_result
    tool_use_id = models.CharField(max_length=255)          # The tool_use ID

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_line_num", "tool_use_id"],
                name="idx_tool_result_link_lookup",
            ),
        ]
```

### AgentLink

Links a Task tool_use to its spawned subagent. Replaces `SessionItemLink` where `link_type='agent'`.

```python
class AgentLink(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="agent_links")
    tool_use_line_num = models.PositiveIntegerField()       # Line containing the assistant message with Task tool_use
    tool_use_id = models.CharField(max_length=255)          # The specific Task tool_use ID
    agent_id = models.CharField(max_length=255)             # The subagent ID

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_id"],
                name="idx_agent_link_lookup",
            ),
        ]
```

Key changes from `SessionItemLink`:
- **No `target_line_num`** on AgentLink (it was always NULL)
- **No `link_type`** on either model (the type is implicit in the table)
- **`tool_use_id` on both tables**: identifies the specific tool_use. On `AgentLink`, this enables precise matching when multiple Task tool_uses are on the same line
- **Renamed fields**: `source_line_num` → `tool_use_line_num`, `target_line_num` → `tool_result_line_num`, `reference` → `tool_use_id` or `agent_id`
- **All fields non-nullable**: no more nullable hacks

## What `tool_use_id` Enables for AgentLink

Currently, `create_agent_link_from_subagent()` matches by prompt text comparison. When it finds a matching Task tool_use, it knows which `tool_use.id` it matched against but doesn't store it — only `source_line_num` is stored. This means:

- The view `tool_agent_id(request, ..., tool_id)` receives the `tool_id` but can only filter by `source_line_num`
- With multiple parallel agents on the same line, it returns the first one found (wrong for the 2nd and 3rd agents)

With the new `tool_use_id` field:
- All three `create_agent_link_from_*` functions store the `tool_use_id`
- The view can filter by `tool_use_id=tool_id` for exact matching
- Parallel agents are correctly distinguished

## Migration Strategy

A single migration (0025) with no data migration. The new tables start empty and are populated by the background recompute triggered by a `CURRENT_COMPUTE_VERSION` bump.

```python
operations = [
    # 1. Create new tables
    migrations.CreateModel(name='ToolResultLink', ...),
    migrations.CreateModel(name='AgentLink', ...),
    # 2. Drop old table
    migrations.DeleteModel(name='SessionItemLink'),
]
```

No data migration is needed because:
- We are in development phase — no other users are affected
- The `CURRENT_COMPUTE_VERSION` bump triggers a full recompute of all sessions, which recreates all links from the JSONL data using the new models
- All fields in the new models are non-nullable — no need for empty/placeholder values

## Files to Modify

### 1. `src/twicc/core/models.py`

- **Remove** `SessionItemLink` class
- **Add** `ToolResultLink` class
- **Add** `AgentLink` class

### 2. `src/twicc/core/migrations/0025_split_session_item_links.py`

- Create both tables, drop old table (see Migration Strategy above)

### 3. `src/twicc/compute.py` — Batch Compute

**In `compute_session_metadata()`** (the background recompute function):

The function builds a `links_to_create` list of dicts that gets bulk_created. Currently:

```python
# tool_result link
links_to_create.append({
    'session_id': session_id,
    'source_line_num': tool_use_map[tool_result_ref],
    'target_line_num': item.line_num,
    'link_type': 'tool_result',
    'reference': tool_result_ref,
})

# agent link
links_to_create.append({
    'session_id': session_id,
    'source_line_num': task_tool_use_map[tu_id],
    'target_line_num': None,
    'link_type': 'agent',
    'reference': agent_id,
})
```

**Change to**: Use two separate lists, one for each model. The `link_type` field is removed. Field names change to match the new models.

```python
# tool_result link
tool_result_links_to_create.append({
    'session_id': session_id,
    'tool_use_line_num': tool_use_map[tool_result_ref],
    'tool_result_line_num': item.line_num,
    'tool_use_id': tool_result_ref,
})

# agent link — tu_id is available here since it comes from get_tool_result_agent_info()
agent_links_to_create.append({
    'session_id': session_id,
    'tool_use_line_num': task_tool_use_map[tu_id],
    'tool_use_id': tu_id,
    'agent_id': agent_id,
})
```

**In `apply_compute_results()`**: The function currently creates `SessionItemLink` objects from the combined `links_data` list. Split into two: create `ToolResultLink` objects from `tool_result_links` data and `AgentLink` objects from `agent_links` data. The message format from the compute worker changes accordingly.

**Delete operation**: `SessionItemLink.objects.filter(session_id=session_id).delete()` becomes two deletes:
```python
ToolResultLink.objects.filter(session_id=session_id).delete()
AgentLink.objects.filter(session_id=session_id).delete()
```

### 4. `src/twicc/compute.py` — Live Sync Functions

#### `create_tool_result_link_live()`

```python
# Before
SessionItemLink.objects.get_or_create(
    session_id=session_id,
    source_line_num=candidate.line_num,
    target_line_num=item.line_num,
    link_type='tool_result',
    reference=tool_use_id,
)

# After
ToolResultLink.objects.get_or_create(
    session_id=session_id,
    tool_use_line_num=candidate.line_num,
    tool_result_line_num=item.line_num,
    tool_use_id=tool_use_id,
)
```

#### `create_agent_link_from_tool_result()`

This function receives `(tool_use_id, agent_id)` from `get_tool_result_agent_info()`. The `tool_use_id` is already available:

```python
# Before
SessionItemLink.objects.get_or_create(
    session_id=session_id,
    link_type='agent',
    source_line_num=candidate.line_num,
    defaults={"reference": agent_id, "target_line_num": None},
)

# After
AgentLink.objects.get_or_create(
    session_id=session_id,
    tool_use_line_num=candidate.line_num,
    tool_use_id=tool_use_id,    # Already available from get_tool_result_agent_info()
    defaults={"agent_id": agent_id},
)
```

Also update the `exists()` check:
```python
# Before
SessionItemLink.objects.filter(session_id=..., link_type='agent', reference=agent_id).exists()

# After
AgentLink.objects.filter(session_id=..., agent_id=agent_id).exists()
```

#### `create_agent_link_from_subagent()`

This function matches by prompt and finds a matching Task tool_use. It needs to also extract the `tool_use.id` to store it. Currently `_extract_task_tool_use_prompt()` returns only the prompt string. We need the tool_use ID too.

**Change `_extract_task_tool_use_prompt()`** to return a list of `(tool_use_id, prompt)` tuples instead of a single prompt. Rename to `_extract_task_tool_use_prompts()`:

```python
def _extract_task_tool_use_prompts(content: list) -> list[tuple[str, str]]:
    """Extract (tool_use_id, prompt) pairs from Task tool_use items in content."""
    results = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'tool_use' or item.get('name') != 'Task':
            continue
        tu_id = item.get('id')
        inputs = item.get('input', {})
        if isinstance(inputs, dict) and tu_id:
            prompt = inputs.get('prompt')
            if isinstance(prompt, str):
                results.append((tu_id, prompt))
    return results
```

Then in `create_agent_link_from_subagent()`:
```python
# Before
prompt = _extract_task_tool_use_prompt(content)
if prompt and prompt.strip() == agent_prompt:
    SessionItemLink.objects.get_or_create(
        session_id=parent_session_id,
        link_type='agent',
        source_line_num=candidate.line_num,
        defaults={"reference": agent_id, "target_line_num": None},
    )

# After
for tu_id, prompt in _extract_task_tool_use_prompts(content):
    if prompt.strip() == agent_prompt:
        AgentLink.objects.get_or_create(
            session_id=parent_session_id,
            tool_use_line_num=candidate.line_num,
            tool_use_id=tu_id,
            defaults={"agent_id": agent_id},
        )
        # ... mark done and return
```

This also fixes the existing bug where `_extract_task_tool_use_prompt()` only returned the first prompt, making it impossible to match the 2nd or 3rd parallel agent.

#### `create_agent_link_from_tool_use()`

This function already iterates all Task tool_use prompts in the message. It needs to also collect the `tool_use.id` alongside each prompt:

```python
# Before: collects prompts only
task_prompts: list[str] = []
# ...
task_prompts.append(prompt.strip())

# After: collects (tool_use_id, prompt) pairs
task_prompts: list[tuple[str, str]] = []
# ...
tu_id = content_item.get('id')
if tu_id:
    task_prompts.append((tu_id, prompt.strip()))
```

Then when creating the link:
```python
# Before
if subagent_prompt in task_prompts:
    SessionItemLink.objects.get_or_create(
        session_id=session_id,
        link_type='agent',
        source_line_num=item.line_num,
        reference=subagent.id,
        defaults={"target_line_num": None},
    )

# After
for tu_id, prompt in task_prompts:
    if prompt == subagent_prompt:
        AgentLink.objects.get_or_create(
            session_id=session_id,
            tool_use_line_num=item.line_num,
            tool_use_id=tu_id,
            defaults={"agent_id": subagent.id},
        )
        break
```

#### In-memory caches

No change needed. `AGENTS_LINKS_DONE_CACHE` and `AGENTS_PROMPT_CACHE` are keyed by `(session_id, agent_id)` and are independent of the link table structure.

### 5. `src/twicc/background.py`

Three functions create/delete links:

#### `_apply_links_create()`

Currently receives a mixed list of link dicts. Change to receive two separate lists:

```python
# Before
def _apply_links_create(msg: dict) -> None:
    links_data = msg['links']
    links = [SessionItemLink(**link_data) for link_data in links_data]
    SessionItemLink.objects.bulk_create(links, ignore_conflicts=True)

# After
def _apply_links_create(msg: dict) -> None:
    tool_result_links_data = msg.get('tool_result_links', [])
    if tool_result_links_data:
        links = [ToolResultLink(**d) for d in tool_result_links_data]
        ToolResultLink.objects.bulk_create(links, ignore_conflicts=True)

    agent_links_data = msg.get('agent_links', [])
    if agent_links_data:
        links = [AgentLink(**d) for d in agent_links_data]
        AgentLink.objects.bulk_create(links, ignore_conflicts=True)
```

#### `_delete_session_links()`

```python
# Before
SessionItemLink.objects.filter(session_id=session_id).delete()

# After
ToolResultLink.objects.filter(session_id=session_id).delete()
AgentLink.objects.filter(session_id=session_id).delete()
```

#### `_apply_session_complete()`

Same pattern: split the delete into two, and split the bulk_create into two lists.

### 6. `src/twicc/views.py`

#### `tool_results()` view

```python
# Before
links = SessionItemLink.objects.filter(
    session=session,
    source_line_num=line_num,
    link_type='tool_result',
    reference=tool_id,
).values_list('target_line_num', flat=True)

# After
links = ToolResultLink.objects.filter(
    session=session,
    tool_use_line_num=line_num,
    tool_use_id=tool_id,
).values_list('tool_result_line_num', flat=True)
```

#### `tool_agent_id()` view

Now simplified — `tool_use_id` allows direct precise matching:

```python
# Before
link = SessionItemLink.objects.filter(
    session=session,
    source_line_num=line_num,
    link_type='agent',
).first()
return JsonResponse({"agent_id": link.reference if link else None})

# After
link = AgentLink.objects.filter(
    session=session,
    tool_use_id=tool_id,
).first()
return JsonResponse({"agent_id": link.agent_id if link else None})
```

No fallback needed — the tables start empty and are populated by the recompute with correct `tool_use_id` values from the start.

### 7. `src/twicc/settings.py`

Bump `CURRENT_COMPUTE_VERSION` from 44 to 45. This triggers recomputation of all sessions, which recreates all links with the new models and populates all fields correctly.

### 8. Imports

Update all files that import `SessionItemLink` to import `ToolResultLink` and/or `AgentLink` instead:

| File | Old import | New import |
|------|-----------|------------|
| `compute.py` | `SessionItemLink` | `ToolResultLink`, `AgentLink` (lazy imports inside functions, already the pattern) |
| `background.py` | `SessionItemLink` | `ToolResultLink`, `AgentLink` |
| `views.py` | `SessionItemLink` | `ToolResultLink`, `AgentLink` |

## Frontend

**No frontend changes required.** The API contract is unchanged:
- `GET .../tool-results/<tool_id>/` → `{"results": [...]}`
- `GET .../tool-agent-id/<tool_id>/` → `{"agent_id": "..." | null}`

## Edge Cases

### Multiple Task tool_uses with identical prompts on the same line

If two Task tool_uses in the same assistant message have the exact same prompt (unlikely but theoretically possible), prompt-based matching could assign both to the same subagent. The `tool_use_id` field doesn't help here because at subagent-matching time we match by prompt. However:
- `get_or_create` with `tool_use_id` prevents duplicate links for the same tool_use
- The second subagent with the same prompt will match a different tool_use_id entry
- This is an improvement over the current code which returns only the first prompt via `_extract_task_tool_use_prompt()`

### Concurrent live sync and background recompute

The background recompute deletes all links then recreates them. During the brief window between delete and recreate, live queries may return no results. This is the same behavior as today — no change.

## Summary of Benefits

1. **Clearer schema**: Each table has exactly the fields it needs, no nullable hacks, all fields required
2. **Self-documenting field names**: `tool_use_line_num`, `tool_result_line_num`, `tool_use_id`, `agent_id` instead of generic `source_line_num`, `target_line_num`, `reference`
3. **Precise agent matching**: `tool_use_id` on `AgentLink` distinguishes parallel agents on the same line
4. **Simpler view**: `tool_agent_id()` can filter directly by `tool_use_id` — no ambiguity
5. **Better queries**: No more `link_type=` filter on every query — the table itself is the type
6. **Fixes parallel agent bug**: `_extract_task_tool_use_prompts()` (plural) iterates all Task tool_uses, fixing the bug where only the first was matched
7. **Clean migration**: No data migration needed — tables start fresh, recompute rebuilds everything
