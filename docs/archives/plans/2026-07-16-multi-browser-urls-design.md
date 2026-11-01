# Multiple saved browser URLs per project/workspace — design

2026-07-16. Status: validated, not implemented.

## Goal

The Browser pane's saved URL is currently a single value per level
(`Project.default_browser_url`, workspace `browserUrl`). Allow several saved
URLs per level: saving adds to a list, old entries can be removed, one entry
per level is the explicit default (Home target).

## Data model

- `Project.default_browser_url` is replaced by `Project.browser_urls`
  (JSONField, default `list`): `[{url, label?, default?}]`.
  - `url`: normalized/validated exactly as today (`normalize_browser_url` /
    `validate_browser_url`), unique within the list.
  - `label`: optional human-readable name shown in menus.
  - `default`: at most one entry per list carries `default: true`. An empty
    list means "nothing saved" (same as `null` today).
  - Data migration: existing `default_browser_url` value →
    `[{url, default: true}]`.
- Workspace `browserUrl` (workspaces.json) becomes `browserUrls`, same entry
  shape, migrated on read like previous schema evolutions of that file.
- `Session.browser_url` is untouched — it is the session's last URL, not a
  saved list.
- Validation (shape, URL rules, uniqueness, single default) lives with the
  existing URL validation, shared by project mutation, workspace mutation and
  CLI paths.

## Resolution (blank pane default)

`browserDefaults.js` walks the same ancestor chain (worktree → main repo →
path ancestors): the first level with a non-empty list wins, yielding its
`default` entry (or the first entry when none is flagged). Workspace fallback
in `BrowserPane.vue` follows the same rule on `browserUrls`. Resolved live as
today, never materialized.

## Browser pane

- **Save**: the bookmark action opens a mini dialog (target level, optional
  label; the URL itself is the current page, not editable) and appends to the
  chosen level's list. The first URL saved at a level automatically becomes
  its default. Already-saved URLs stay disabled as targets.
- **Home**: the plain button navigates to the resolved default. With more
  than one distinct saved URL across levels, the existing dropdown lists every
  entry (level badge + label or raw URL, deduped by URL) and gains per-entry
  actions: remove, and "set as default" for its level.

## Edit dialogs

`ProjectEditDialog` and `WorkspaceManageDialog` replace the single URL input
with a small list editor: add, remove, edit label, reorder, pick the default
(radio).

## CLI / MCP

`update-project`, `update-workspace`, `create-workspace`:

- `--add-browser-url URL [--browser-url-label LABEL] [--set-default]`
- `--remove-browser-url URL`
- `--set-default-browser-url URL`
- The previous flags (`--default-browser-url`, `--browser-url`) remain as
  aliases for "add + set default" (add if missing, then flag as default).

Skills for the touched commands, `SKILLS-AND-CLI.md`, and the plugin version
are updated accordingly.

## Tests

- Mutation services: entry validation, per-list URL uniqueness, single
  default, add/remove/set-default operations.
- Data migration (project column) and workspaces.json read migration.
- Frontend resolution (`browserUrl.test.js` and neighbors): default picking,
  ancestor chain, workspace fallback.
