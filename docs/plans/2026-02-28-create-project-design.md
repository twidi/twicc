# Create Project Feature — Design

**Date:** 2026-02-28

## Summary

Allow users to create a new project from the home page by providing a filesystem directory path. The project is created in the database with default values (no sessions, no cost). After creation, the user is navigated to the new project's page.

## Backend — POST `/api/projects/`

Added to the existing `project_list` view.

**Request body:**
```json
{ "directory": "/home/user/dev/my-project", "name": "My Project", "color": "#ff5733" }
```
- `directory` is required
- `name` and `color` are optional

**Processing logic:**
1. Extract `directory` from body (required, else 400)
2. `resolved = os.path.realpath(directory)` — normalize the path
3. Verify `resolved` is absolute and `os.path.isdir(resolved)` — else 400
4. Generate project ID: `re.sub(r'[^a-zA-Z0-9]', '-', resolved)` — all non-alphanumeric chars become dashes
5. Check `Project.objects.filter(id=project_id).exists()` is false — else 409
6. Validate `name` if provided: trim, max 25 chars, uniqueness — else 409
7. Validate `color` if provided
8. `Project.objects.create(id=project_id, directory=resolved, name=name, color=color)` — other fields keep defaults (sessions_count=0, mtime=0, total_cost=0, stale=False)
9. Broadcast `project_added` via WebSocket channel layer
10. Return `serialize_project(project)` with status 201

**Error codes:**
- 400: directory missing, not absolute, does not exist, not a directory, name too long
- 409: project already exists for this directory, or name already taken

**Not included:**
- No folder creation on disk in `~/.claude/projects/`
- No git root detection at creation time

## Frontend — Extended `ProjectEditDialog.vue`

The existing dialog gains a creation mode based on the `project` prop:
- `project` provided → **edit mode** (unchanged behavior)
- `project` absent/null → **create mode**

**Create mode differences:**
- Title: "New Project" (instead of "Edit Project")
- Submit button: "Create" (instead of "Save")
- Shows a `wa-input` text field for `directory` (replaces the read-only ID/Directory fields)
- `name` and `color` fields remain identical
- API call: `POST /api/projects/` with `{ directory, name, color }`
- After success: `store.addProject(createdProject)`, emit `'saved'`, close dialog, navigate to `/projects/:id`

**"New Project" button on the home page:**
- Placed in `.home-header` of `HomeView.vue`, alongside the "All N sessions" button (both pushed right)
- Small button with a `+` icon, default or text variant

## Data Flow

1. User clicks "New Project" on home → dialog opens in create mode
2. User fills directory (+ optionally name/color) → clicks "Create"
3. Frontend POSTs to `/api/projects/`
4. Backend: `realpath()` → `isdir()` → generate ID → check uniqueness → create in DB → broadcast WS `project_added` → return 201
5. Frontend: receives response, `store.addProject()`, closes dialog, navigates to `/projects/:id`
6. Other connected clients receive `project_added` via WS and update their list

## Edge Cases

| Case | Response |
|------|----------|
| Relative path | 400 "Directory must be an absolute path" |
| Path does not exist / not a directory | 400 "Directory does not exist or is not a directory" |
| Project already exists for this directory | 409 "A project already exists for this directory" |
| Name already taken | 409 "This name is already used by another project" |
| Empty directory field | Frontend disables Create button |

## Out of Scope (YAGNI)

- No path auto-completion in the input
- No preview of generated project ID in the dialog
- No folder creation on disk in `~/.claude/projects/`
- No git root detection at creation time
