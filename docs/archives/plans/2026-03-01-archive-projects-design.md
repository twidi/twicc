# Archive Projects — Design

## Goal

Allow users to archive projects, hiding them from the UI by default. Mirrors the existing session archiving pattern.

## Backend

### Model

Add `archived = models.BooleanField(default=False)` to `Project` in `core/models.py`. Create a Django migration.

### Serializer

Add `"archived": project.archived` to `serialize_project()` in `core/serializers.py`.

### API

Add PATCH support to `project_detail` view in `views.py`:
- Accept `{ "archived": bool }`
- Save and broadcast `project_updated` via WebSocket
- Pattern follows `session_detail` PATCH handler

## Frontend

### Settings store (`settings.js`)

New setting `showArchivedProjects: false`:
- Schema entry with default `false`
- Validator: boolean check
- Getter: `isShowArchivedProjects`
- Action: `setShowArchivedProjects(enabled)`
- Persisted in localStorage (not synced, no settings panel entry)

### Home page — `ProjectList.vue`

- **Toggle switch**: "Show archived projects" control on the home page (like "Show archived sessions" on ProjectView)
- **Archive button**: `wa-button` with `archive` icon next to the edit (pencil) button on each project card
- **Filtering**: `namedProjects` and `treeRoots` computeds exclude archived projects unless `showArchivedProjects` is enabled
- **Visual indicator**: "Arch." tag on archived projects (same pattern as sessions)
- **Tree rebuilding**: when hiding archived projects, only non-archived unnamed projects are passed to `buildProjectTree()`, so the tree compresses correctly without phantom folders

### Project tree — `ProjectTreeNode.vue`

- Archive button added next to the edit button on project nodes
- Emit `archive` event up to `ProjectList.vue`

### Project selectors — `ProjectView.vue`

- `allProjects`, `namedProjects`, `flatTree`: filter out archived projects unless `showArchivedProjects` is enabled
- `nonStaleNamedProjects`, `nonStaleFlatTree` (new session dropdowns): **always** exclude archived projects (never create sessions in archived projects)

### Sessions in "All projects" mode

When `showArchivedProjects` is false, sessions belonging to archived projects are hidden from the "all projects" session list. Filtering happens at the component level (in `ProjectView.vue` before passing to `SessionList`), consistent with the existing `showArchivedSessions` pattern.

### Data store — `data.js`

New action `setProjectArchived(projectId, archived)`:
- Optimistic update of `project.archived`
- PATCH `/api/projects/<id>/` with `{ archived: bool }`
- Rollback on error
- Pattern follows `setSessionArchived`
