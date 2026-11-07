import { i18n } from '../i18n/index.js'

// frontend/src/utils/projectTabs.js
//
// The tabs of a scope page (global, workspace, project, worktree) and how they map to routes and
// keys. Pure, so the node:test suite pins the numbering (display design sections 3.1 and 10).
//
//   Alt+Shift+1 Timeline, 2 Stats, 3 Files, 4 Git (only with a git root), 5 Terminal.
//
// Timeline is the BARE route of a scope (`project`, `projects-all`): an old link to a bare project URL
// opens it. Stats has its own `/stats` route.

import { buildTabRouteName } from './granularRoutes.js'

export const PROJECT_TABS = Object.freeze([
    { id: 'timeline', get label() { return i18n.global.t('project.tabs.timeline') }, icon: 'clock-rotate-left' },
    { id: 'stats', get label() { return i18n.global.t('project.tabs.stats') }, icon: 'chart-simple' },
    { id: 'files', get label() { return i18n.global.t('project.tabs.files') }, icon: 'folder' },
    { id: 'git', get label() { return i18n.global.t('project.tabs.git') }, icon: 'code-branch' },
    { id: 'terminal', get label() { return i18n.global.t('project.tabs.terminal') }, icon: 'terminal' },
])

/** Alt+Shift+digit → tab id. Fixed: a missing tab (Git without a git root) leaves its key unused. */
export const DIRECT_TAB_MAP = Object.freeze({ 1: 'timeline', 2: 'stats', 3: 'files', 4: 'git', 5: 'terminal' })

/** The tab strip: Git only with a git root. */
export function visibleProjectTabs({ hasGitRepo }) {
    return PROJECT_TABS.filter((tab) => tab.id !== 'git' || hasGitRepo)
}

/** The tab a direct shortcut opens, or null when the key is unused or its tab is missing. */
export function directTab(index, { hasGitRepo }) {
    const id = DIRECT_TAB_MAP[index] ?? null
    if (id === 'git' && !hasGitRepo) return null
    return id
}

const TAB_BY_ROUTE_NAME = {
    'project-stats': 'stats', 'projects-stats': 'stats',
    'project-files': 'files', 'projects-files': 'files',
    'project-git': 'git', 'projects-git': 'git',
    'project-terminal': 'terminal', 'projects-terminal': 'terminal',
}

/** The active tab, derived from the route name; the bare routes are the Timeline. */
export function tabForRouteName(name) {
    return Object.hasOwn(TAB_BY_ROUTE_NAME, name) ? TAB_BY_ROUTE_NAME[name] : 'timeline'
}

/** Route name of a tab: the bare route for the Timeline, `<prefix>-<tab>` for the others. */
export function routeNameForTab(tab, { isAllProjectsMode }) {
    if (tab === 'timeline') return isAllProjectsMode ? 'projects-all' : 'project'
    return buildTabRouteName({ isAllProjectsMode, isSessionRoute: false, tab })
}
