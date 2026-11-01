# Workspaces

**Date:** 2026-04-06
**Status:** DRAFT

## Overview

A workspace is a named group of projects. It lets users focus on a subset of their projects without seeing the rest. A user working on both the "Frontend" and "Backend" projects this week can create a "Sprint 14" workspace containing those two projects, and navigate between them effortlessly.

A project can belong to multiple workspaces. Workspaces are synced across devices.

## Concepts

### Workspace context

The workspace context is a persistent state that follows the user as they navigate. When a workspace is active, it influences how projects are displayed everywhere in the app, but it does not restrict what the user can do.

When the user enters a workspace, the workspace context stays active:
- when switching to the workspace view (merged sessions from the workspace's projects)
- when navigating to a specific project that belongs to the workspace
- when viewing a session within one of those projects
- when navigating via search results, toasts, or the command palette (as long as the target project is in the workspace)

The workspace context is cleared:
- when the user explicitly selects a different workspace or "All Projects" (without workspace)
- when the user navigates to a project that does not belong to the workspace (e.g. by clicking a project outside the workspace in the selector, or following a link to a session in a non-workspace project)
- when the user goes back to the Home page

### Workspace view vs. single-project view

When a workspace is active, the user can be in one of two views:

- **Workspace view:** the session list shows all sessions from all workspace projects, merged and sorted together. This is the equivalent of "All Projects" but scoped to the workspace.
- **Single-project view:** the user has drilled into one specific project within the workspace. The session list shows only that project's sessions. The workspace context is still active (it affects project ordering in selectors and stays in the URL).

The user switches between these two views using the project selector.

### Visible projects

A project is **visible** if it is not archived, or if the "Show archived projects" setting is active. This is the existing rule, unchanged.

Within a workspace, only visible projects are considered. An archived project that belongs to a workspace is ignored everywhere (session list, project selectors, aggregate counts) unless "Show archived projects" is active.

### Activable workspaces

A workspace is **activable** if it contains at least one visible project. A workspace whose projects are all archived and "Show archived projects" is off cannot be entered — it would show zero sessions.

## User interface

### Home page

A new "Workspaces" section appears at the top of the Home page, before the project list.

Each workspace is shown as a card displaying:
- The workspace name
- The project badges (colored dots + names) of the visible projects in the workspace
- An aggregate session count (sum of sessions across the workspace's visible projects)

Clicking a workspace card navigates to the workspace view (all sessions from the workspace's visible projects).

**Non-activable workspace cards** (workspaces with zero visible projects) are displayed but visually disabled (greyed out, not clickable). This lets the user see that the workspace exists but cannot be entered in the current configuration.

**Workspace card context menu:** Each workspace card has a context menu (similar to the existing project card menu) with:
- **Manage**: opens the workspace management dialog, focused directly on this workspace's edit view.
- **Archive** / **Unarchive**: toggles the workspace's archived state.

Below the workspace cards, a **"Manage workspaces"** link/button opens the workspace management dialog. This link is always visible, even when no workspaces exist yet (to allow creating the first one).

**Archived workspaces on the Home page:**
- By default, only non-archived workspaces are displayed.
- If archived workspaces exist, a **"Show archived workspaces"** toggle switch appears (same pattern as the existing "Show archived projects" toggle).
- When the toggle is on, archived workspaces are displayed with a visual "Archived" badge (same as archived projects today). An archived workspace is clickable only if it is activable (has at least one visible project).

**Empty state:** If no workspaces exist at all (neither active nor archived), the "Workspaces" section header is not displayed, but the "Manage workspaces" link still appears (for example as a subtle link near the page header or the "New project" button area).

The existing project list (named projects, then unnamed projects tree) remains unchanged below.

### Project selector (sidebar)

The project selector dropdown in the sidebar header is restructured into distinct sections separated by dividers. The content of the sections depends on whether a workspace is active.

Note: the current implementation uses a `<wa-select>` component. The new structure (with multiple highlighted/selected items and non-selectable action items) will likely require replacing it with a different component (e.g., a `<wa-dropdown>` with custom content). This is a technical detail left to implementation.

#### Structure without an active workspace

1. **All Projects** — selectable, highlighted when active
2. Divider
3. **Workspaces section** (only if activable workspaces exist):
   - Each activable workspace as a selectable option (non-activable workspaces are hidden)
   - "Manage workspaces..." action item (opens workspace management dialog)
4. Divider
5. **All projects**, in the normal order (by most recent activity). Named projects first, then unnamed in directory tree, same as today.

Archived workspaces are only shown in this section if "Show archived workspaces" is active, and even then only if they are activable.

#### Structure with an active workspace

1. **All Projects** — selectable (clears workspace context)
2. Divider
3. **Workspaces section** (only if activable workspaces exist):
   - Each activable workspace as a selectable option; the active workspace is visually highlighted
   - "Manage workspaces..." action item
4. Divider
5. **Workspace projects sub-section:**
   - **"All projects"** (or equivalent label) — selectable, navigates to the workspace view (all sessions from the workspace's visible projects merged). Highlighted when the user is in workspace view (not drilling into a specific project).
   - Each visible project belonging to the active workspace — selectable. Highlighted when the user is viewing that specific project. Ordered according to the workspace's custom project order (see "Project ordering" below).
6. Divider
7. **Other projects** — all projects that do not belong to the active workspace, in the normal order.

This structure gives the user two levels of visual feedback: which workspace is active (in the workspaces section) and which project or "all" view is selected within that workspace (in the workspace projects sub-section).

#### Selection behavior

- Clicking **"All Projects"** (top): navigates to all-projects view, clears workspace context.
- Clicking **a workspace**: navigates to the workspace view (merged sessions from that workspace's visible projects).
- Clicking **"All projects"** within the workspace sub-section: navigates to the workspace view (same as clicking the workspace name, but useful when the user has drilled into a single project and wants to go back to the merged view).
- Clicking **a project within the workspace sub-section**: navigates to that project in single-project view, workspace context is preserved.
- Clicking **a project in the "other projects" section**: navigates to that project in single-project view, workspace context is cleared.
- Clicking **"Manage workspaces..."**: opens the workspace management dialog without navigating.

### Project ordering

#### Within a workspace

Projects within a workspace have a **custom order** defined by the user. This order is set when creating or editing the workspace (via drag or arrow buttons in the management dialog) and stored as part of the workspace definition (the order of the project list is the display order).

This custom order is used everywhere workspace projects are displayed: the sidebar project selector, the "New session" dropdown, and all other project selectors.

When a new project is added to a workspace, it is appended at the end of the list. The user can then reorder it.

#### In all selectors when a workspace is active

When a workspace is active, **every** location in the app that presents a list of projects for selection must display the workspace's visible projects first (in their custom order), separated by a divider from the remaining projects. This includes (but is not limited to):

- Sidebar project selector (described above — the workspace projects sub-section)
- "New session" project picker dropdown
- Search overlay project filter dropdown
- Terminal snippets dialog scope selector
- Message snippets dialog scope selector
- Any future project selector

Below the workspace projects divider, the remaining projects appear in the normal order (by most recent activity, named before unnamed, etc.).

When no workspace is active, the order is unchanged from today.

Note: some of these selectors share a common component for rendering project options. The workspace-first ordering should be handled at that shared level so all current and future selectors benefit automatically.

### Workspace management dialog

The dialog follows the same design pattern as the existing terminal snippets and message snippets management dialogs, providing a consistent experience for list management across the app.

Opened from:
- The "Manage workspaces..." item in the sidebar project selector
- The "Manage workspaces" link/button on the Home page
- The "Manage" action in a workspace card's context menu on the Home page (opens directly to that workspace's edit view)

#### Workspace list view

The dialog shows the list of workspaces. Each workspace entry displays:
- The workspace name
- An "Archived" indicator if applicable

A **"Show archived workspaces"** toggle switch controls whether archived workspaces appear in the list. By default, only non-archived workspaces are shown.

Actions available on each entry:
- **Reorder arrows** (up/down) on the left side to change workspace display order
- **Edit button** to open the workspace edit view
- **Delete button** to delete the workspace (with confirmation)

A **"New workspace"** button allows creating a new workspace (opens the edit view with empty fields).

#### Workspace edit view

When editing (or creating) a workspace, the dialog shows:

- **Name field**: text input for the workspace name. Validation: non-empty, unique among existing workspaces.
- **Archived toggle**: a switch to archive/unarchive the workspace.
- **Project list**: the projects currently in the workspace, displayed in their custom order. Each project entry shows:
  - The project badge (colored dot + name or path)
  - **Reorder arrows** (up/down) to change the project's position within the workspace
  - **Remove button** (e.g., a delete/cross icon) to remove the project from the workspace
- **Add project button** ("+" or "Add project"): opens a project picker showing all available projects **except** those already in the workspace. The picker displays projects with their badges. Selecting a project adds it to the end of the workspace's project list.

**Save behavior:** Changes are local to the dialog until the user explicitly clicks "Save". The user can modify the name, add/remove/reorder projects, toggle archived state — nothing is persisted until save. A "Cancel" button discards all changes.

If the workspace being edited is currently active, saved changes take effect immediately (the session list and project ordering update live).

### Session list behavior

**In workspace view** (workspace active, no specific project selected):
- The session list shows sessions from all visible projects that belong to the workspace.
- Sessions are sorted and paginated in the same way as "All Projects" (by most recent activity, with active processes and pinned sessions at the top).
- Each session item displays the project name/badge next to it (same as in "All Projects" mode today).

**In single-project view with workspace active:**
- The session list shows only sessions from that specific project (same as today).
- The project name is not shown on each session item (same as single-project mode today).

### New session creation

**In workspace view:**
The "New session" button is a dropdown (same as in "All Projects" mode today). The project list in the dropdown shows workspace projects first (in their custom order), then other projects.

**In single-project view with workspace active:**
The "New session" button works exactly like today's single-project mode. The main button creates a session in the current project. The dropdown lets the user pick another project; workspace projects appear first.

### Workspace persistence and sync

Workspace definitions (name, archived state, ordered list of projects) are stored on the server and synchronized across all connected devices/browsers.

When workspaces are modified on one device, the changes are broadcast to all other connected instances.

The "active workspace" state (which workspace the user is currently working in) is **not** synced — it is specific to each browser tab/window. Different tabs can have different active workspaces.

### Archiving a workspace

An archived workspace:
- Is hidden on the Home page unless "Show archived workspaces" is toggled on.
- Is hidden in the sidebar project selector and all other selectors unless "Show archived workspaces" is toggled on.
- Even when visible (toggle is on), it can only be entered if it is activable (has at least one visible project).
- Remains fully manageable in the workspace management dialog (which always shows all workspaces regardless of archive state).

**Archiving the currently active workspace** (when "Show archived workspaces" is off):
The workspace context is cleared. The user is navigated to "All Projects" view. If a session was being viewed, it remains open in all-projects mode (same behavior as when a workspace is deleted while active).

### Edge cases

**A session from a non-workspace project is navigated to (e.g., from global search):**
The session opens normally. Since the session's project is not in the workspace, the workspace context is cleared. The user is now in single-project mode without a workspace.

**A project is removed from a workspace while the user is viewing it:**
If the user is in workspace view, the session list updates live (sessions from the removed project disappear). If the user is viewing the removed project in single-project view, the workspace context is cleared (the project is no longer part of the workspace).

**A project in a workspace is archived (and "Show archived projects" is off):**
The archived project is ignored within the workspace. Its sessions disappear from the workspace view. If the user was viewing that project in single-project view, the workspace context is cleared. If this was the last visible project in the workspace, the workspace becomes non-activable: the user is navigated to "All Projects" view, and the workspace appears disabled in selectors.

**A project in a workspace becomes stale (directory no longer exists):**
The stale project remains in the workspace definition and is still visible. In the "New session" dropdown, it is disabled (same as today). Its existing sessions still appear in the workspace view.

**The workspace is empty (all projects removed from it):**
The workspace still exists but is not activable. It shows as disabled on the Home page. It does not appear in the sidebar selector. It can be managed (add projects back) via the workspace management dialog.

**A workspace is deleted while another device has it active:**
On the other device, the workspace context is cleared and the user returns to "All Projects" view. A toast notification could indicate that the workspace was deleted.

**A workspace becomes non-activable while the user is in it** (e.g., the last visible project is archived):
The workspace context is cleared. The user is navigated to "All Projects" view.

## Future: virtual workspaces (for reference only)

> This section describes a possible future extension. It is **not** part of the initial implementation. It is documented here for traceability and to ensure the architecture does not preclude it.

### Concept

A virtual workspace is an ad-hoc, unsaved workspace. Instead of being defined in the workspace storage, it is encoded directly in the URL. It behaves identically to a saved workspace from the user's perspective, except:

- It is not synced across devices.
- It has no name (displayed as "Custom selection" or similar).
- It is not listed in the workspace management dialog.
- It is visually distinguished in the project selector (e.g., labeled "Virtual workspace" or showing the project names directly instead of a workspace name).

### How a user would create one

From single-project view, the user could "add" another project to the current view, creating a virtual workspace on the fly. The exact UX gesture is to be determined (e.g., a "Pin to view" button next to projects in the selector, or a Ctrl+click behavior).

### Saving a virtual workspace

The user could save a virtual workspace as a real workspace through the management dialog ("Save as workspace..."). This would create a persisted workspace with the same project list and switch the URL from the virtual encoding to the saved workspace ID.
