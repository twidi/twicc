---
name: twicc-artifacts
description: List bookmarked artifacts (viewable files saved from a session's Artifacts tab), or bookmark/unbookmark one. Use when you or the user want to browse bookmarks, filter them by project/workspace/scope, or add, rename, re-scope, or remove one from the CLI.
---

# TwiCC Artifacts

List bookmarked artifacts, or `bookmark` / `unbookmark` one. A bookmark is a saved pointer to a *viewable* artifact file (HTML, image, PDF, Markdown, …) under a session's artifacts directory — a file TwiCC can display directly — with a name and a visibility scope (`project` / `workspace` / `all`). The only artifacts TwiCC tracks are bookmarked ones, so listing them = listing the bookmarks.

## When to use

- You or the user want to list or browse bookmarked artifacts, optionally scoped to a project / workspace, or filtered to those bookmarked everywhere (`--scope all`).
- You or the user want to bookmark an artifact a session produced, or rename / re-scope an existing bookmark.
- You or the user want to remove a bookmark.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### List

```bash
$TWICC artifacts [OPTIONS]
```

Results are ordered by most recently updated. Read-only: works without the server running.

- `--project <PROJECT>` — filter by project (path or id; **drop the leading dash** on ids). A normal project also returns its git worktrees' bookmarks; a worktree project returns only its own. Mutually exclusive with `--workspace`.
- `--workspace ID` — filter to bookmarks of projects in the given workspace, each member's git worktrees included. Mutually exclusive with `--project`.
- `--scope <project|workspace|all>` — filter by each bookmark's own visibility scope (independent of `--project` / `--workspace`). `--scope all` lists only the ones bookmarked everywhere.
- `--limit N` — max results (default: 20; 50 with `--paginated`).
- `--offset N` — skip first N for pagination (default: 0).
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`, so you know whether another page follows instead of guessing from the page size. Without an explicit `--limit` the page size becomes **50**. Without the flag, shape and default page size are unchanged.

### Bookmark

```bash
$TWICC artifacts bookmark <SESSION_ID|self|parent> <PATH> --name NAME [--scope SCOPE]
```

Creates the bookmark, or renames / re-scopes it if one already exists for that `(session, path)`. Requires the live server.

- `SESSION_ID` — the session that owns the artifact: a session id, `self`, or `parent`.
- `PATH` — artifact path relative to the session's artifacts directory (the `relative_path` the listing prints, e.g. `demo/index.html`), or an absolute path confined to that directory.
- `--name NAME` — bookmark name (**required**).
- `--scope <project|workspace|all>` — visibility scope. On create, defaults to `project`. Omit when updating to keep the current scope.
- `--timeout N` — seconds to wait for the server's status (default: 30).

### Unbookmark

```bash
$TWICC artifacts unbookmark <SESSION_ID|self|parent> <PATH>
```

Removes the bookmark for that `(session, path)`. Symmetric with `bookmark` — the artifact file need not still exist. Requires the live server.

- `--timeout N` — seconds to wait for the server's status (default: 30).

## Errors

### Local (exit 1)

- `session_context_not_found` — `self` or `parent` has no current TwiCC session context.
- `parent_not_found` — `parent` was used from a root session.
- `invalid_scope`

### Server (exit 3)

- `missing` — a required field (`session_id`, `path`, or `name`) was empty.
- `invalid_scope`
- `session_not_found`
- `path_escapes` — the path resolves outside the session's artifacts directory.
- `file_not_found` — `bookmark` only; the artifact file does not exist.
- `not_bookmarked` — `unbookmark` only; no bookmark exists for that `(session, path)`.

## Output format

### Listing

```json
[
  {
    "id": 3,
    "name": "Layout playground",
    "scope": "all",
    "session_id": "54b42b89-290a-4324-bf86-f636a048d23d",
    "project_id": "-home-twidi-dev-myproject",
    "relative_path": "layout-playground/index.html",
    "root": "/home/twidi/.twicc/artifacts/54b42b89-290a-4324-bf86-f636a048d23d",
    "file_ext": "html",
    "created_at": "2026-06-16T22:22:04.856926+00:00",
    "updated_at": "2026-06-16T22:22:04.856938+00:00"
  }
]
```

`scope` is the bookmark's own visibility scope; `root` is the session's artifacts directory; `relative_path` is the key you pass to `bookmark` / `unbookmark`.

### Bookmark / unbookmark

```json
{"status": "updated", "bookmark_id": 3, "session_id": "abc…", "project_id": "-home-…", "request_uuid": "…"}
{"status": "deleted", "bookmark_id": 3, "session_id": "abc…", "project_id": "-home-…", "request_uuid": "…"}
```

On rejection: `{"status": "rejected", "errors": [{"field": "…", "code": "…", "message": "…"}], "request_uuid": "…"}`.

### Exit codes

- `0` — Success
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC artifacts
$TWICC artifacts --project .
$TWICC artifacts --workspace backend
$TWICC artifacts --scope all
$TWICC artifacts --limit 50 --offset 20
$TWICC artifacts bookmark 54b42b89-290a-4324-bf86-f636a048d23d demo/index.html --name "Demo"
$TWICC artifacts bookmark 54b42b89-290a-4324-bf86-f636a048d23d report.md --name "Report" --scope all
$TWICC artifacts unbookmark 54b42b89-290a-4324-bf86-f636a048d23d demo/index.html
$TWICC artifacts bookmark self report.md --name "Report"
$TWICC artifacts unbookmark self report.md
```

## Related commands

- `$TWICC sessions` — list sessions (find the SESSION_ID that owns an artifact). Skill: `twicc-sessions`.
- `$TWICC session <session_id>` — full metadata for one session. Skill: `twicc-session`.

## How to present results

1. Show each bookmark's name, scope, and `relative_path`; group by session or project when listing many.
2. If `--paginated` reports `has_more: true`, offer to fetch the next page with `--offset`.
3. You are in TwiCC — link to a bookmarked artifact: `[link text](/project/{project_id}/artifacts/{id})`.
