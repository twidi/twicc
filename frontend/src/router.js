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
            { path: 'session/:sessionId', name: 'session', component: SessionView }
        ]
    }
]

export const router = createRouter({
    history: createWebHistory(),
    routes
})
