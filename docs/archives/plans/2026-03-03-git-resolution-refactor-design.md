# Git Resolution Refactor — Design

## Goal

Refactor the git directory/branch resolution system to eliminate cache staleness issues and properly separate concerns between background compute and live watcher paths.

## Context

The current system uses a single module-level cache (`_git_resolution_cache`) for all git resolution. This cache never expires, causing:
- Branch changes (`git checkout`) not detected until server restart
- Newly created repos (`git init`) not detected
- Bash-only sessions never getting a `git_directory` (no tool_use paths to resolve from)

The cache exists for performance during background compute (thousands of items at startup). For the live watcher (one item at a time), the filesystem cost is negligible (~50-100µs per resolution) and the cache causes correctness issues.

## Design

### `_resolve_git_from_path` — Two modes

Add a `use_cache` parameter:

```python
def _resolve_git_from_path(dir_path: str, *, use_cache: bool = True) -> tuple[str, str] | None:
```

- `use_cache=True` (default): current behavior, reads/writes cache. Used by background compute.
- `use_cache=False`: always hits filesystem, does not read or write cache. Used by watcher live and `ensure_project_git_root`.

### `resolve_git_for_item` — Pass-through

```python
def resolve_git_for_item(parsed_json: dict, *, use_cache: bool = True) -> tuple[str, str] | None:
```

Passes `use_cache` down to `_resolve_git_from_path`. Per-item majority vote logic unchanged.

### `compute_item_metadata_live` — Item-level only

- Calls `resolve_git_for_item(parsed, use_cache=False)`
- Sets `item.git_directory` and `item.git_branch`
- **Removes** `Session.objects.filter().update(git_directory=..., git_branch=...)` — session-level propagation is not this function's responsibility

### `sync_session_items` — Session-level propagation + CWD fallback

After the per-item loop:

1. **Propagate from items**: iterate `reversed(items_to_create)`, take the last item with `git_directory`, set on `session`
2. **CWD fallback**: if `session.git_directory` is still `None` and `session.cwd` exists, call `_resolve_git_from_path(session.cwd, use_cache=False)`
3. **Persist**: `session.save(update_fields=[..., "git_directory", "git_branch"])` — these two fields are added to the existing `update_fields` list

### `ensure_project_git_root` — No cache

Calls `_resolve_git_from_path(directory, use_cache=False)` directly. No `pop` trick needed. This function is called rarely (startup, directory changes, project without git_root).

### Deletions

- `clear_git_resolution_cache()`: dead code (never called anywhere), remove it
- `resolve_git_from_cwd()` from PR #2: not needed, call `_resolve_git_from_path` directly

### Unchanged

- `compute_session_metadata` (background compute): keeps `use_cache=True`, cache is useful for bulk processing
- Per-item majority vote in `resolve_git_for_item`
- Serializer fallback (`git_branch or cwd_git_branch`)
- All frontend/backend consumers of `git_directory` (already handle `None` with their own fallback chains)
