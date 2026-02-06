import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import ProjectView from './views/ProjectView.vue'
import SessionView from './views/SessionView.vue'
import LoginView from './views/LoginView.vue'
import { useAuthStore } from './stores/auth'

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
    {
        path: '/project/:projectId',
        component: ProjectView,
        children: [
            { path: '', name: 'project', component: null },
            {
                path: 'session/:sessionId',
                name: 'session',
                component: SessionView,
                children: [
                    {
                        // Subagent route - opens subagent tab within the session
                        path: 'subagent/:subagentId',
                        name: 'session-subagent'
                    }
                ]
            }
        ]
    },
    // "All Projects" mode routes (note: plural "projects")
    {
        path: '/projects',
        component: ProjectView,
        children: [
            { path: '', name: 'projects-all', component: null },
            {
                path: ':projectId/session/:sessionId',
                name: 'projects-session',
                component: SessionView,
                children: [
                    {
                        path: 'subagent/:subagentId',
                        name: 'projects-session-subagent'
                    }
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
