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
import { useRoute, useRouter } from 'vue-router'
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
    /** Notivue item reference — passed by CustomNotification to allow dismissing the toast */
    item: {
        type: Object,
        default: null,
    },
})

const route = useRoute()
const router = useRouter()
const store = useDataStore()

const session = computed(() => store.getSession(props.sessionId))
const projectId = computed(() => session.value?.project_id)

const sessionTitle = computed(() => {
    const title = session.value?.title
    if (!title) return 'Unknown'
    return title.length > 50 ? title.slice(0, 50) + '…' : title
})

/** Whether we're already viewing this session. */
const isCurrentSession = computed(() => route.params.sessionId === props.sessionId)

/** Navigate to the session, switching project if needed, then dismiss the toast. */
function goToSession() {
    if (!session.value || !projectId.value) return

    const isAllProjectsMode = route.name?.startsWith('projects-')

    if (isAllProjectsMode) {
        router.push({
            name: 'projects-session',
            params: { projectId: projectId.value, sessionId: props.sessionId },
        })
    } else {
        // Single-project mode: router.push will switch project automatically
        // if the session belongs to a different project
        router.push({
            name: 'session',
            params: { projectId: projectId.value, sessionId: props.sessionId },
        })
    }

    // Dismiss the toast
    props.item?.clear?.()
}
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
        <wa-button v-if="!isCurrentSession" size="small" variant="brand" appearance="outlined" @click="goToSession">Go to session</wa-button>
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

.session-toast-content > wa-button {
    align-self: flex-end;
}
</style>
