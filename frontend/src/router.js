import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import ProjectView from './views/ProjectView.vue'
import SessionView from './views/SessionView.vue'

const routes = [
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
