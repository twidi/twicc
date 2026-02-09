<script setup>
/**
 * SessionToastContent - Rich content for session-related toast notifications.
 *
 * Displays the project badge and session title inside a CustomNotification,
 * replacing the plain text "Session: <title>" that was used before.
 *
 * Usage (via useToast):
 *   toast.session(sessionId, { type: 'success', title: 'Claude Code started' })
 */
import { computed } from 'vue'
import { useDataStore } from '../stores/data'
import ProjectBadge from './ProjectBadge.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true,
    },
    errorMessage: {
        type: String,
        default: null,
    },
})

const store = useDataStore()

const session = computed(() => store.getSession(props.sessionId))
const projectId = computed(() => session.value?.project_id)

const sessionTitle = computed(() => {
    const title = session.value?.title
    if (!title) return 'Unknown'
    return title.length > 50 ? title.slice(0, 50) + '…' : title
})
</script>

<template>
    <div class="session-toast-content">
        <span v-if="projectId" class="session-toast-row">
            <span class="session-toast-label">Project:</span>
            <ProjectBadge :project-id="projectId" class="session-toast-project" />
        </span>
        <span class="session-toast-session">
            <span class="session-toast-label">Session:</span>
            <span class="session-toast-title">{{ sessionTitle }}</span>
        </span>
        <span v-if="errorMessage" class="session-toast-error">{{ errorMessage }}</span>
    </div>
</template>

<style scoped>
.session-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.session-toast-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
}

.session-toast-label {
    opacity: 0.8;
    flex-shrink: 0;
    width: 3rem;
}

.session-toast-project {
    font-weight: 700;
    min-width: 0;
}

.session-toast-session {
    display: flex;
    gap: var(--wa-space-s);
    margin-bottom: var(--wa-space-xs);
}

.session-toast-session .session-toast-label {
    align-self: flex-start;
}

.session-toast-title {
    font-weight: 700;
}

.session-toast-error {
    color: var(--wa-color-danger);
    font-weight: bold;
}
</style>
