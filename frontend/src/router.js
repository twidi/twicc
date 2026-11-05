import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import ProjectView from './views/ProjectView.vue'
import SessionView from './views/SessionView.vue'
import LoginView from './views/LoginView.vue'
import JsonTestView from './views/JsonTestView.vue'
import GitLogTestView from './views/GitLogTestView.vue'
import { useAuthStore } from './stores/auth'
import { registerScopeMemory } from './utils/scopeMemory'

const routes = [
    {
        path: '/login',
        name: 'login',
        component: LoginView,
        meta: { public: true },
    },
    {
        path: '/logout',
        name: 'logout',
        meta: { public: true },
        beforeEnter: async () => {
            const authStore = useAuthStore()
            await authStore.logout()
            return { name: 'login' }
        },
        component: LoginView, // never rendered, redirect happens in beforeEnter
    },
    { path: '/', name: 'home', component: HomeView },
    { path: '/json-test', name: 'json-test', component: JsonTestView, meta: { public: true } },
    { path: '/git-log-test', name: 'git-log-test', component: GitLogTestView, meta: { public: true } },
    {
        path: '/project/:projectId',
        component: ProjectView,
        children: [
            // The bare route is the Timeline tab; Stats has its own route.
            { path: '', name: 'project', component: { render: () => null } },
            { path: 'stats', name: 'project-stats', component: { render: () => null } },
            { path: 'files/:rootKey?/:filePath?', name: 'project-files', component: { render: () => null } },
            { path: 'artifacts/:bookmarkId?', name: 'project-artifacts', component: { render: () => null } },
            { path: 'git/:rootKey?/:commitRef?/:filePath?', name: 'project-git', component: { render: () => null } },
            { path: 'terminal/:termIndex?', name: 'project-terminal', component: { render: () => null } },
            {
                path: 'session/:sessionId',
                name: 'session',
                component: SessionView,
                children: [
                    {
                        // Subagent route - opens subagent tab within the session
                        path: 'subagent/:subagentId',
                        name: 'session-subagent',
                        component: { render: () => null }
                    },
                    // Tool tabs - open as tabs within the session
                    { path: 'files/:rootKey?/:filePath?', name: 'session-files', component: { render: () => null } },
                    { path: 'artifacts/:rootKey?/:filePath?', name: 'session-artifacts', component: { render: () => null } },
                    { path: 'git/:rootKey?/:commitRef?/:filePath?', name: 'session-git', component: { render: () => null } },
                    { path: 'terminal/:termIndex?', name: 'session-terminal', component: { render: () => null } },
                    { path: 'orchestration', name: 'session-orchestration', component: { render: () => null } },
                    { path: 'plan/:docPath?', name: 'session-plan', component: { render: () => null } },
                    { path: 'tasks', name: 'session-tasks', component: { render: () => null } },
                    { path: 'workflows/:runId?', name: 'session-workflows', component: { render: () => null } },
                    { path: 'browser', name: 'session-browser', component: { render: () => null } },
                ]
            }
        ]
    },
    // "All Projects" mode routes (note: plural "projects")
    {
        path: '/projects',
        component: ProjectView,
        children: [
            { path: '', name: 'projects-all', component: { render: () => null } },
            { path: 'stats', name: 'projects-stats', component: { render: () => null } },
            { path: 'files/:rootKey?/:filePath?', name: 'projects-files', component: { render: () => null } },
            { path: 'artifacts/:bookmarkId?', name: 'projects-artifacts', component: { render: () => null } },
            { path: 'git/:rootKey?/:commitRef?/:filePath?', name: 'projects-git', component: { render: () => null } },
            { path: 'terminal/:termIndex?', name: 'projects-terminal', component: { render: () => null } },
            {
                path: ':projectId/session/:sessionId',
                name: 'projects-session',
                component: SessionView,
                children: [
                    {
                        path: 'subagent/:subagentId',
                        name: 'projects-session-subagent',
                        component: { render: () => null }
                    },
                    { path: 'files/:rootKey?/:filePath?', name: 'projects-session-files', component: { render: () => null } },
                    { path: 'artifacts/:rootKey?/:filePath?', name: 'projects-session-artifacts', component: { render: () => null } },
                    { path: 'git/:rootKey?/:commitRef?/:filePath?', name: 'projects-session-git', component: { render: () => null } },
                    { path: 'terminal/:termIndex?', name: 'projects-session-terminal', component: { render: () => null } },
                    { path: 'orchestration', name: 'projects-session-orchestration', component: { render: () => null } },
                    { path: 'plan/:docPath?', name: 'projects-session-plan', component: { render: () => null } },
                    { path: 'tasks', name: 'projects-session-tasks', component: { render: () => null } },
                    { path: 'workflows/:runId?', name: 'projects-session-workflows', component: { render: () => null } },
                    { path: 'browser', name: 'projects-session-browser', component: { render: () => null } },
                ]
            }
        ]
    }
]

export const router = createRouter({
    history: createWebHistory(),
    routes
})

// Navigation guard: redirect to login if not authenticated,
// and redirect away from login if no password is needed.
router.beforeEach(async (to) => {
    const authStore = useAuthStore()

    // Wait for initial auth check if not done yet
    if (!authStore.isReady) {
        await authStore.checkAuth()
    }

    // On login page: redirect to home if no login is needed
    // (no password configured, or already authenticated)
    if (to.name === 'login' && !authStore.needsLogin) {
        return { name: 'home' }
    }

    // Public routes (login, logout) are always accessible
    if (to.meta.public) return true

    // Redirect to login if authentication is needed
    if (authStore.needsLogin) {
        return {
            name: 'login',
            query: { redirect: to.fullPath },
        }
    }

    return true
})

// Propagate workspace query param across navigations.
// The workspace is preserved unless:
// - The destination explicitly sets/clears ?workspace= (e.g., switching workspaces)
// - The destination project is not in the workspace (e.g., following a search result to another project)
// - Navigating to the home page or a route without a project context
router.beforeEach(async (to, from) => {
    const currentWs = from.query?.workspace
    if (!currentWs) return                          // No workspace active
    if (to.query.workspace !== undefined) return     // Destination explicitly sets/clears

    // Check if the destination has a projectId (works for both /project/:id and /projects/:id/session/:id routes)
    const targetProjectId = to.params?.projectId
    if (targetProjectId) {
        // Any route with a projectId: keep workspace only if the project belongs
        // to it. A git worktree of a member counts as part of the workspace too
        // (see workspaceContainsProject), so navigating into a worktree of a
        // member keeps the workspace sticky.
        const { useWorkspacesStore } = await import('./stores/workspaces')
        const wsStore = useWorkspacesStore()
        if (wsStore.workspaceContainsProject(currentWs, targetProjectId)) {
            return { ...to, query: { ...to.query, workspace: currentWs } }
        }
        // Project not in workspace → workspace dropped
        return
    }

    // Routes without a projectId (home, projects-all, login, etc.) → workspace dropped.
    // Navigations that need to keep workspace must include it explicitly in query.
})

// Clean up explicit workspace-clear signal from URL.
// When navigating with { workspace: '' } to bypass guard propagation,
// the URL ends up with ?workspace= — strip it via replaceState (no guard re-trigger).
router.afterEach((to) => {
    if (to.query.workspace === '') {
        const params = new URLSearchParams(to.query)
        params.delete('workspace')
        const search = params.toString()
        window.history.replaceState(history.state, '', to.path + (search ? '?' + search : '') + (to.hash || ''))
    }
})

// Web playback stops on the login route. Native Stop all belongs to actual logout or authorization loss.
router.afterEach(async (to) => {
    if (to.name !== 'login') return
    const { useTextToSpeechStore } = await import('./stores/textToSpeech')
    if (useTextToSpeechStore().nativeMode) return
    const { useTimelineSpeechStore } = await import('./stores/timelineSpeech')
    useTimelineSpeechStore().disable()
    useTextToSpeechStore().stop()
})

// Per-scope last-location memory — restore a scope's last screen when re-entering it.
// Registered last so it sees the workspace-finalized `to` and records settled routes.
registerScopeMemory(router)
