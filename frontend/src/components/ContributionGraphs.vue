<script setup>
// ContributionGraphs.vue - Fetches daily activity data once and renders
// both the user messages and cost contribution graphs.

import { ref, watch, onMounted } from 'vue'
import { apiFetch } from '../utils/api'
import { ALL_PROJECTS_ID } from '../stores/data'
import ContributionGraph from './ContributionGraph.vue'

const props = defineProps({
    /** Project ID or ALL_PROJECTS_ID for global view */
    projectId: {
        type: String,
        required: true,
    },
})

const dailyActivity = ref([])

async function fetchDailyActivity() {
    try {
        const url = props.projectId === ALL_PROJECTS_ID
            ? '/api/daily-activity/'
            : `/api/projects/${encodeURIComponent(props.projectId)}/daily-activity/`

        const res = await apiFetch(url)
        if (res.ok) {
            const data = await res.json()
            dailyActivity.value = data.daily_activity || []
        }
    } catch (error) {
        console.error('Failed to load daily activity:', error)
    }
}

onMounted(fetchDailyActivity)
watch(() => props.projectId, fetchDailyActivity)
</script>

<template>
    <ContributionGraph :daily-activity="dailyActivity" mode="messages" />
    <ContributionGraph :daily-activity="dailyActivity" mode="cost" />
</template>
