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

// Navigation guard: redirect to login if not authenticated
router.beforeEach(async (to) => {
    // Always allow access to public routes (login page)
    if (to.meta.public) return true

    const authStore = useAuthStore()

    // Wait for initial auth check if not done yet
    if (!authStore.isReady) {
        await authStore.checkAuth()
    }

    // Redirect to login if authentication is needed
    if (authStore.needsLogin) {
        return {
            name: 'login',
            query: { redirect: to.fullPath },
        }
    }

    return true
})
