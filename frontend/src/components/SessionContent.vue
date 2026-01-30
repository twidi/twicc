<script setup>
import SessionHeader from './SessionHeader.vue'
import SessionItemsList from './SessionItemsList.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    projectId: {
        type: String,
        required: true
    }
})

// Determine mode based on whether we have a parent session
// If parentSessionId is provided, this is a subagent
const mode = props.parentSessionId ? 'subagent' : 'session'
</script>

<template>
    <div class="session-content">
        <SessionHeader
            :session-id="sessionId"
            :mode="mode"
        />
        <wa-divider></wa-divider>
        <SessionItemsList
            :session-id="sessionId"
            :parent-session-id="parentSessionId"
            :project-id="projectId"
        />
    </div>
</template>

<style scoped>
.session-content {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

.session-content > wa-divider {
    flex-shrink: 0;
}
</style>
