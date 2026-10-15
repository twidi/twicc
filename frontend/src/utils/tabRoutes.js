// Route names where per-tab navigation applies, shared by App.vue (the
// Alt+Ctrl+Shift keydown dispatch) and the command registry (route-gated
// "Go to … tab" commands), so the gate stays defined in one place.

// Every route of a scope page (global, workspace, project, worktree): the bare route (Timeline) and the
// tab routes (Stats, Files, Git, Terminal). Shared by App.vue (the Alt+Shift+digit dispatch) and the
// command registry (the "Switch to ... Tab" commands).
export const PROJECT_DETAIL_ROUTES = new Set([
    'project', 'project-stats', 'project-files', 'project-git', 'project-terminal',
    'projects-all', 'projects-stats', 'projects-files', 'projects-git', 'projects-terminal',
])

// Terminal panel routes — session + project/worktree scopes.
export const TERMINAL_ROUTES = new Set([
    'session-terminal', 'projects-session-terminal',
    'project-terminal', 'projects-terminal',
])

// Workflows pane routes — session scope only.
export const WORKFLOW_ROUTES = new Set([
    'session-workflows', 'projects-session-workflows',
])
